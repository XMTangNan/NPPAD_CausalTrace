"""V2.3 §5.9 D2: 每类事故 sev-1 独立基准（稳健 σ + 分位数 + Z 病态标记）。

本模块只做纯计算（`compute_tolerance` 不写盘）；产物由执行器
`run_v23_tolerances.py` 经 `rca.v21.artifact` 写入 `tolerances/tolerance_{K}.json`，
供 K2 异常判定（physics_constraints）与 D9 偏离度（deviation）使用。

口径修正（V23-CODE-016）：排除集只含**本类自己的 marker** + 安全动作变量，
不得把其他类的 marker（RHRD/PPM/TBLD/WLD 等过程量）从本类正常模型变量中剔除。

口径修正（V23-DATA-004，2026-11-10 决策）：
- 基线窗口固定为注入前 [0, 100] s 共 11 点（t_inject=110 由注册表读取），
  不再用 t=0–990 的全轨迹滑窗；原口径把注入后段算进“准稳态”，
  系统性放大 σ，削弱偏离度敏感度。
- 准稳态段搜索仍保留，但加 `t < t_inject` 硬约束，只作可追溯记录，不参与 μ/σ。
- μ 取本类注入前基线均值；σ = max(1.4826·MAD, σ_floor(v))。
- Normal/1.csv 只作“正常波动量级”对照（`normal_reference`），不并入 μ/σ。
- 常量通道不再跳过（V23-DESIGN-006“病态变量全部走稳健 σ + ±10 截断”），
  逐变量标 `constant_pre_inject`，σ 取 σ_floor；**σ 仍不可比时不参与排序**（V23-CODE-029，
  见 `external_knowledge.is_deviation_comparable`）。

σ 兜底（V23-DESIGN-008 / §4.4.1）：σ_floor 优先逐变量/逐量纲物理下限
（external_knowledge.sigma_floor_for），无配置时回退 1%·|μ|，并在 thresholds 里
逐变量记录 `sigma_floor`/`sigma_floor_source`。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from causalrca.data import loader as D
from causalrca.evidence import external_knowledge as EK
from causalrca.config.accident_config import get_accident
from causalrca.evidence.knowledge import ENTITY_TABLE
from causalrca.config.constants_v21 import MARKERS, SAFETY_STEP_VARS
from causalrca.io.logging import logger

CV_STEADY_THRESHOLD = 0.05      # 准稳态 CV 阈值 5%
WINDOW_SIZE = 100               # 滑动窗口 100 点
Z_PATHOLOGY_RATIO = 0.01        # σ/|μ| < 1% 标记 Z 病态
Z_PATHOLOGY_MAX_PCT = 0.30      # 病态变量比例上限 30%（诊断口径，不阻断产物）
MIN_VARIABLES_PASS = 10         # 最少可用变量数

# ==================== σ 五桶（方案 §4.3.12，V23-CODE-046）====================
# 尺子只服务候选池门（gate），不进入任何排序分数。优先级 b4 > b5 > b3 > b2 > b1。
SIGMA_MAD_MULTIPLIER = 1.4826
SIGMA_INSUFFICIENT_RATIO = 10.0   # fallback 且 MAD < floor/10 → 样本不足


def classify_sigma_bucket(sigma_mad: float, sigma_floor: float,
                          floor_source: str, saturated: bool) -> Dict:
    """按 §4.3.12 判 σ 桶；该判不可评估就判，不编下限。"""
    if saturated:
        return {"bucket": "b4", "sigma_source": None, "sigma_value": None,
                "sigma_rule_id": "sigma_b4_saturated",
                "evaluability": "not_evaluable",
                "not_evaluable_reason": "saturated"}
    if (floor_source != "physical"
            and float(sigma_mad) < float(sigma_floor) / SIGMA_INSUFFICIENT_RATIO):
        return {"bucket": "b3", "sigma_source": None, "sigma_value": None,
                "sigma_rule_id": "sigma_b3_insufficient_baseline",
                "evaluability": "not_evaluable",
                "not_evaluable_reason": "insufficient_baseline"}
    if float(sigma_floor) >= float(sigma_mad):
        if float(sigma_floor) <= 0.0:
            return {"bucket": "b5", "sigma_source": None, "sigma_value": None,
                    "sigma_rule_id": "sigma_b5_not_calibrated",
                    "evaluability": "not_evaluable",
                    "not_evaluable_reason": "not_calibrated"}
        return {"bucket": "b2", "sigma_source": "physical_floor",
                "sigma_value": float(sigma_floor),
                "sigma_rule_id": "sigma_b2_physical_floor",
                "evaluability": "ok", "not_evaluable_reason": None}
    return {"bucket": "b1", "sigma_source": "measured_mad",
            "sigma_value": float(sigma_mad),
            "sigma_rule_id": "sigma_b1_measured_mad",
            "evaluability": "ok", "not_evaluable_reason": None}

# V23-DATA-004：注入前固定基线（TIME_STEP=10s → 0,10,…,100 共 11 点）
PRE_INJECT_BASELINE_START = 0.0
PRE_INJECT_BASELINE_END = 100.0
PRE_INJECT_BASELINE_POINTS = 11

QUANTILES = [0.10, 0.20, 0.50, 0.80, 0.90]
SATURATED_VARS = {"WFWA", "WSTA", "WFLB", "WTRA", "WTRB", "WLR", "WHPI", "WECS"}


def _find_steady_segment(df: pd.DataFrame, cols: List[str],
                         t_inject: float) -> Tuple[int, int]:
    """在注入前（t < t_inject）滑窗找 CV<5% 的最长连续区间，返回 pre 内索引。

    V23-DATA-004：原实现全轨迹搜索，sev-1 注入响应小（P 仅变 0.4 bar）时
    注入后 110–990 s 也被判“准稳态”。本函数只用于可追溯记录，不参与 μ/σ。
    """
    pre = df.loc[df["TIME"] < float(t_inject)].reset_index(drop=True)
    n = len(pre)
    if n < 2:
        return 0, n
    win = min(WINDOW_SIZE, n)
    best_len, best_start = 0, 0
    for start in range(0, n - win + 1):
        seg = pre.iloc[start:start + win]
        cvs = []
        for c in cols:
            vals = seg[c].to_numpy(dtype=float)
            mu = vals.mean()
            sigma = vals.std()
            if abs(mu) > 1e-9:
                cvs.append(sigma / abs(mu))
        if not cvs:
            continue
        if max(cvs) < CV_STEADY_THRESHOLD:
            end = start + win
            while end < n:
                seg2 = pre.iloc[start:end + 1]
                cvs2 = []
                for c in cols:
                    vals = seg2[c].to_numpy(dtype=float)
                    mu = vals.mean()
                    sigma = vals.std()
                    if abs(mu) > 1e-9:
                        cvs2.append(sigma / abs(mu))
                if cvs2 and max(cvs2) < CV_STEADY_THRESHOLD:
                    end += 1
                else:
                    break
            if end - start > best_len:
                best_len = end - start
                best_start = start
    if best_len == 0:
        return 0, win
    return best_start, best_start + best_len


def excluded_vars_for(accident_type: str) -> set:
    """V23-CODE-016：只排除本类 marker + 安全动作变量（不排除他类 marker/过程量）。"""
    return {MARKERS[accident_type]} | set(SAFETY_STEP_VARS)


def _normal_reference(model_cols: List[str]) -> Dict:
    """Normal/1.csv 在 [0,100]s 的逐变量 σ，仅作“正常波动量级”对照，不并入 μ/σ。"""
    path = D.REPO_DATA / "Normal" / "1.csv"
    if not path.is_file():
        return {"available": False, "reason": f"缺 Normal 轨迹: {path}"}
    df = pd.read_csv(path)
    if "TIME" not in df.columns:
        return {"available": False, "reason": "Normal/1.csv 缺 TIME 列"}
    mask = ((df["TIME"] >= PRE_INJECT_BASELINE_START - 1e-9)
            & (df["TIME"] <= PRE_INJECT_BASELINE_END + 1e-9))
    seg = df.loc[mask]
    sigma = {}
    for c in model_cols:
        if c not in seg.columns or len(seg) < 2:
            continue
        vals = seg[c].to_numpy(dtype=float)
        sigma[c] = float(np.std(vals, ddof=1))
    return {
        "available": True,
        "source": "nppad_repo/Operation_csv_data/Normal/1.csv",
        "window": [PRE_INJECT_BASELINE_START, PRE_INJECT_BASELINE_END],
        "n_points": int(len(seg)),
        "sigma_normal": sigma,
        "note": "仅作正常波动量级对照，不并入本类 μ/σ 标定（V23-DATA-004）",
    }


def compute_tolerance(accident_type: str) -> Dict:
    """计算单类事故的 sev-1 独立基准（纯函数，不落盘）。"""
    sev1 = D.load_trajectory(accident_type, 1)
    all_cols = [c for c in sev1.columns if c != "TIME"]

    cfg = get_accident(accident_type)
    t_inject = float(cfg.get("t_inject", 110.0))

    excluded = excluded_vars_for(accident_type)
    model_cols = [c for c in all_cols if c not in excluded]

    # 仅记录：注入前准稳态段（V23-DATA-004 加 t<t_inject 硬约束）
    steady_start, steady_end = _find_steady_segment(sev1, model_cols, t_inject)
    pre = sev1.loc[sev1["TIME"] < t_inject].reset_index(drop=True)
    steady = pre.iloc[steady_start:steady_end]
    steady_time = ((float(steady["TIME"].iloc[0]), float(steady["TIME"].iloc[-1]))
                   if len(steady) else (None, None))

    # 主基线：注入前固定 [0,100]s（11 点）
    base_mask = ((sev1["TIME"] >= PRE_INJECT_BASELINE_START - 1e-9)
                 & (sev1["TIME"] <= PRE_INJECT_BASELINE_END + 1e-9))
    baseline = sev1.loc[base_mask]
    baseline_time = (float(baseline["TIME"].min()), float(baseline["TIME"].max()))

    thresholds: Dict[str, Dict] = {}
    z_pathology_vars: List[str] = []
    constant_vars: List[str] = []

    for c in model_cols:
        vals = baseline[c].to_numpy(dtype=float)
        if len(vals) < 2:
            continue
        mu = float(np.mean(vals))
        is_constant = bool(np.std(vals) < 1e-12)
        mad = float(np.median(np.abs(vals - mu)))
        sigma_mad = 1.4826 * mad
        unit = (ENTITY_TABLE.get(c) or {}).get("unit")
        sigma_floor, floor_source = EK.sigma_floor_for(c, mu, unit)
        sigma_robust = float(max(sigma_mad, float(sigma_floor)))
        quantiles = {f"p{int(q * 100)}": float(np.quantile(vals, q)) for q in QUANTILES}

        is_saturated = c in SATURATED_VARS
        is_z_pathology = (sigma_robust / abs(mu)) < Z_PATHOLOGY_RATIO if abs(mu) > 1e-9 else False
        if is_z_pathology:
            z_pathology_vars.append(c)
        if is_constant:
            constant_vars.append(c)

        thresholds[c] = {
            "mu": mu,
            "sigma_robust": sigma_robust,
            "sigma_mad": float(sigma_mad),
            "sigma_floor": float(sigma_floor),
            "sigma_floor_source": floor_source,
            "sigma_v2": classify_sigma_bucket(sigma_mad, sigma_floor,
                                              floor_source, is_saturated),
            "quantiles": quantiles,
            "z_pathology": is_z_pathology,
            "saturated": is_saturated,
            "constant_pre_inject": is_constant,
        }

    n_pass = len(thresholds)
    z_ratio = len(z_pathology_vars) / max(n_pass, 1)
    baseline_points = int(len(baseline))
    quality = {
        "baseline_window": [PRE_INJECT_BASELINE_START, PRE_INJECT_BASELINE_END],
        "baseline_points": baseline_points,
        "baseline_time": baseline_time,
        "steady_segment_pre_inject": {
            "points": int(steady_end - steady_start),
            "time": steady_time,
            "note": "仅记录；μ/σ 用 [0,100]s 基线（V23-DATA-004）",
        },
        "n_variables_pass": n_pass,
        "n_constant_pre_inject": len(constant_vars),
        "z_pathology_count": len(z_pathology_vars),
        "z_pathology_ratio": round(z_ratio, 4),
        # V23-DESIGN-006 定稿：病态比例只作诊断，不再作为 quality 门槛
        "quality_pass": (
            baseline_points >= PRE_INJECT_BASELINE_POINTS
            and n_pass >= MIN_VARIABLES_PASS
        ),
    }

    bucket_counts: Dict[str, int] = {}
    for th in thresholds.values():
        b = th["sigma_v2"]["bucket"]
        bucket_counts[b] = bucket_counts.get(b, 0) + 1
    n_not_evaluable = sum(1 for th in thresholds.values()
                          if th["sigma_v2"]["evaluability"] == "not_evaluable")

    return {
        "accident_type": accident_type,
        "t_inject": t_inject,
        "tolerance_source": "data_proxy",
        "sigma_buckets": {
            "counts": dict(sorted(bucket_counts.items())),
            "n_variables": len(thresholds),
            "n_not_evaluable": n_not_evaluable,
            "mad_multiplier": SIGMA_MAD_MULTIPLIER,
            "insufficient_ratio": SIGMA_INSUFFICIENT_RATIO,
            "bucket_priority": ["b4", "b5", "b3", "b2", "b1"],
            "note": "σ 只是门的尺子，不进入排序分数；不可评估不造数（§4.3.12）",
        },
        "baseline_source": f"{accident_type}_1.csv 注入前 [0,100]s 固定基线",
        "baseline_rule": ("V23-DATA-004：基线=注入前 [0,100]s 共 11 点；"
                          "μ=基线均值，σ=max(1.4826*MAD, σ_floor(v))；"
                          "Normal 只作对照不并入"),
        "excluded_vars": sorted(excluded & set(all_cols)),
        "excluded_rule": "仅本类 marker + SAFETY_STEP_VARS（V23-CODE-016）",
        "sigma_rule": "sigma_robust = max(1.4826*MAD, sigma_floor(v))；"
                      "sigma_floor 优先 by_variable/by_unit，否则 1%*|mu|",
        "normal_reference": _normal_reference(model_cols),
        "thresholds": thresholds,
        "z_pathology_vars": z_pathology_vars,
        "constant_vars": constant_vars,
        "quality": quality,
        "z_truncation": [-10, 10],
    }


if __name__ == "__main__":
    from causalrca.config.accident_config import READY_CODES
    for k in READY_CODES:
        tol = compute_tolerance(k)
        q = tol["quality"]
        logger.info(
            f"[D2-tolerance] {k}: 基线{q['baseline_points']}点"
            f"{q['baseline_window']}, {q['n_variables_pass']}变量, "
            f"常量{q['n_constant_pre_inject']}个, "
            f"Z病态{q['z_pathology_count']}个({q['z_pathology_ratio']:.1%}), "
            f"quality={'PASS' if q['quality_pass'] else 'FAIL'}")

"""V2.3 §5.9 D2: 每类事故 sev-1 独立基准（稳健 σ + 分位数 + Z 病态标记）。

本模块只做纯计算（`compute_tolerance` 不写盘）；产物由执行器
`run_v23_tolerances.py` 经 `rca.v21.artifact` 写入 `tolerances/tolerance_{K}.json`，
供 K2 异常判定（physics_constraints）与 D9 偏离度（deviation）使用。

口径修正（V23-CODE-016）：排除集只含**本类自己的 marker** + 安全动作变量，
不得把其他类的 marker（RHRD/PPM/TBLD/WLD 等过程量）从本类正常模型变量中剔除。
σ 兜底（V23-DESIGN-008 / §4.4.1）：σ = max(1.4826·MAD, σ_floor(v))，
σ_floor 优先逐变量/逐量纲物理下限（external_knowledge.sigma_floor_for），
无配置时回退 1%·|μ|，并在 thresholds 里逐变量记录 `sigma_floor`/`sigma_floor_source`。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from rca.v21 import data as D
from rca.v21 import external_knowledge as EK
from rca.v21.knowledge import ENTITY_TABLE
from rca.rca_config_v21 import MARKERS, SAFETY_STEP_VARS
from core.utils import logger

CV_STEADY_THRESHOLD = 0.05      # 准稳态 CV 阈值 5%
WINDOW_SIZE = 100               # 滑动窗口 100 点
Z_PATHOLOGY_RATIO = 0.01        # σ/|μ| < 1% 标记 Z 病态
Z_PATHOLOGY_MAX_PCT = 0.30      # 病态变量比例上限 30%
MIN_STEADY_POINTS = 50          # 准稳态段最少点数
MIN_VARIABLES_PASS = 10         # 最少通过筛选的变量数

QUANTILES = [0.10, 0.20, 0.50, 0.80, 0.90]
SATURATED_VARS = {"WFWA", "WSTA", "WFLB", "WTRA", "WTRB", "WLR", "WHPI", "WECS"}


def _find_steady_segment(df: pd.DataFrame, cols: List[str]) -> Tuple[int, int]:
    """滑动窗口找 CV<5% 的最长连续区间，返回 (start_idx, end_idx)。"""
    n = len(df)
    best_len, best_start = 0, 0
    for start in range(0, n - WINDOW_SIZE + 1):
        seg = df.iloc[start:start + WINDOW_SIZE]
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
            # 扩展
            end = start + WINDOW_SIZE
            while end < n:
                seg2 = df.iloc[start:end + 1]
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
        return 0, min(WINDOW_SIZE, n)
    return best_start, best_start + best_len


def excluded_vars_for(accident_type: str) -> set:
    """V23-CODE-016：只排除本类 marker + 安全动作变量（不排除他类 marker/过程量）。"""
    return {MARKERS[accident_type]} | set(SAFETY_STEP_VARS)


def compute_tolerance(accident_type: str) -> Dict:
    """计算单类事故的 sev-1 独立基准（纯函数，不落盘）。"""
    sev1 = D.load_trajectory(accident_type, 1)
    all_cols = [c for c in sev1.columns if c != "TIME"]

    excluded = excluded_vars_for(accident_type)
    model_cols = [c for c in all_cols if c not in excluded]

    start_idx, end_idx = _find_steady_segment(sev1, model_cols)
    steady = sev1.iloc[start_idx:end_idx]
    steady_time = (float(steady["TIME"].iloc[0]), float(steady["TIME"].iloc[-1]))

    thresholds: Dict[str, Dict] = {}
    z_pathology_vars: List[str] = []

    for c in model_cols:
        vals = steady[c].to_numpy(dtype=float)
        if np.std(vals) < 1e-6:
            continue  # 常量列跳过（禁止数值兜底）
        mu = float(np.mean(vals))
        mad = float(np.median(np.abs(vals - mu)))
        sigma_mad = 1.4826 * mad if abs(mu) > 1e-9 else float(np.std(vals))
        unit = (ENTITY_TABLE.get(c) or {}).get("unit")
        sigma_floor, floor_source = EK.sigma_floor_for(c, mu, unit)
        sigma_robust = float(max(sigma_mad, float(sigma_floor)))
        quantiles = {f"p{int(q * 100)}": float(np.quantile(vals, q)) for q in QUANTILES}

        is_saturated = c in SATURATED_VARS
        is_z_pathology = (sigma_robust / abs(mu)) < Z_PATHOLOGY_RATIO if abs(mu) > 1e-9 else False
        if is_z_pathology:
            z_pathology_vars.append(c)

        thresholds[c] = {
            "mu": mu,
            "sigma_robust": sigma_robust,
            "sigma_mad": float(sigma_mad),
            "sigma_floor": float(sigma_floor),
            "sigma_floor_source": floor_source,
            "quantiles": quantiles,
            "z_pathology": is_z_pathology,
            "saturated": is_saturated,
        }

    n_pass = len(thresholds)
    z_ratio = len(z_pathology_vars) / max(n_pass, 1)
    quality = {
        "steady_segment_points": end_idx - start_idx,
        "steady_segment_time": steady_time,
        "n_variables_pass": n_pass,
        "z_pathology_count": len(z_pathology_vars),
        "z_pathology_ratio": round(z_ratio, 4),
        "quality_pass": (
            (end_idx - start_idx) >= MIN_STEADY_POINTS
            and n_pass >= MIN_VARIABLES_PASS
            and z_ratio < Z_PATHOLOGY_MAX_PCT
        ),
    }

    return {
        "accident_type": accident_type,
        "baseline_source": f"{accident_type}_1.csv sev-1 准稳态段",
        "excluded_vars": sorted(excluded & set(all_cols)),
        "excluded_rule": "仅本类 marker + SAFETY_STEP_VARS（V23-CODE-016）",
        "sigma_rule": "sigma_robust = max(1.4826*MAD, sigma_floor(v))；"
                      "sigma_floor 优先 by_variable/by_unit，否则 1%*|mu|",
        "thresholds": thresholds,
        "z_pathology_vars": z_pathology_vars,
        "quality": quality,
        "z_truncation": [-10, 10],
    }


if __name__ == "__main__":
    from rca.v21.accident_config import READY_CODES
    for k in READY_CODES:
        tol = compute_tolerance(k)
        q = tol["quality"]
        logger.info(
            f"[D2-tolerance] {k}: 稳态{q['steady_segment_points']}点, "
            f"{q['n_variables_pass']}变量通过, "
            f"Z病态{q['z_pathology_count']}个({q['z_pathology_ratio']:.1%}), "
            f"quality={'PASS' if q['quality_pass'] else 'FAIL'}")

"""v2.3 Phase 0 数据审计：B4 数据审计 + B5 Z 病态 + B6 严重度变点。

方案 §4.4.1 / §4.4.2。纯函数 + 执行器分离：
- 纯函数（sigma_floor_for / variable_z_profile / cusum_changepoints /
  pelt_dp_changepoints / marker_profile）可单测，不读盘；
- audit_class_data / audit_z_pathology / audit_changepoints 读数据但不写盘；
- run_audit(code, run_id) 落盘三份 JSON 到本 run 的 audit/ 子目录；
- run_all 逐类 try/except ClassDataMissing，跳过清单返回，绝不生成占位产物。

产物文件名（STRUCTURE.md §2）：
  data_audit_{K}.json / z_pathology_audit_{K}.json / changepoint_report_{K}.json
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import spearmanr

from causalrca.config.constants_v21 import (
    REPO_DATA, MAIN_WINDOW_START, MAIN_WINDOW_END,
    BASELINE_WINDOW_START, BASELINE_WINDOW_END,
    SEVERITY_BANDS, GROUP_ORDER, CONSTANT_SIGMA,
)
from causalrca.config.accident_config import ACCIDENT_ORDER, get_accident, marker_of
from causalrca.data.loader import (
    ClassDataMissing, ensure_class_available, list_class_severities,
    load_trajectory, window_slice, save_json,
)
from causalrca.evidence.knowledge import ENTITY_TABLE
from causalrca.evidence import external_knowledge as EK
from causalrca.io.logging import logger

# ==================== 审计常量（方案 §4.4.1 / §4.4.2）====================
POTENTIAL_PATHOLOGY_CV = 0.01      # σ/|μ| < 1% → 潜在病态
Z_PATHOLOGY_THRESHOLD = 100.0      # max|z| > 100 → Z病态
Z_TRUNCATION = (-10.0, 10.0)       # z 截断
# 逐变量物理 σ_floor 的数值配置**不再在本模块定义**（V23-DESIGN-029）：
# 唯一来源是 rca/v21/external_knowledge.py + external_knowledge/tolerance_overrides.yaml
# （by_variable → by_unit → fallback·|μ|）。此处仅保留对它的引用，避免两套口径漂移。
MIN_BASELINE_POINTS = 30           # 基线段点数不足则回退全段前 150 点
FALLBACK_HEAD_POINTS = 150

CUSUM_THRESHOLD = 3.0              # 标准化 CUSUM 统计量阈值（σ 单位）
CUSUM_MIN_SEGMENT = 4              # 二分递归最小段长
DP_PENALTY = 1.0                   # beta = penalty * log(n) * var(x)
LINEARITY_REL_TOL = 1e-3           # 线性剂量阶梯判定：max|残差| / 曲线极差
CLUSTER_GAP = 2                    # 相邻变点合并间隔（severity 索引）
MIN_SEVERITY_POINTS = 10           # 变点检测所需最少严重度点（LOCAC 仅 6 点 → insufficient）


# ==================== 通用小工具 ====================
def _common(code: str, run_id: Optional[str], artifact: str) -> Dict:
    """统一顶层字段：schema 用全链唯一值，artifact 记本文件类型（V23-CODE-024）。"""
    from causalrca.io.artifact import SCHEMA

    cfg = get_accident(code) or {}
    return {
        "schema": SCHEMA,
        "artifact": artifact,
        "accident_type": code,
        "group": cfg.get("group"),
        "run_id": run_id,
        "source": f"{REPO_DATA.parent.name}/{REPO_DATA.name}/{code}",
    }


def _window_pair(value, default: Tuple[float, float]) -> Tuple[float, float]:
    """注册表窗口既接受 [start, end] 也接受 {"start":..,"end":..}。"""
    if value is None:
        return default
    if isinstance(value, dict):
        return float(value["start"]), float(value["end"])
    seq = list(value)
    if len(seq) != 2:
        return default
    return float(seq[0]), float(seq[1])


def _class_windows(cfg: Dict) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    main = _window_pair(cfg.get("main_window"),
                        (MAIN_WINDOW_START, MAIN_WINDOW_END))
    base = _window_pair(cfg.get("baseline_window"),
                        (BASELINE_WINDOW_START, BASELINE_WINDOW_END))
    return main, base


def _class_marker(code: str, cfg: Optional[Dict] = None) -> Optional[str]:
    try:
        return marker_of(code)
    except Exception:                                   # 注册表未提供函数时回退
        return (cfg or get_accident(code) or {}).get("marker")


# ==================== B5 纯函数：σ_floor / 变量 z 画像 ====================
def sigma_floor_for(var: str, mu: float,
                    entity_table: Optional[Dict] = None,
                    unit: Optional[str] = None) -> Dict:
    """逐变量物理 σ_floor 的**兼容包装**（数值决策唯一来源：external_knowledge）。

    V23-DESIGN-029：原先 data_audit 自带一套 PHYSICAL_SIGMA_FLOOR + 1%·|μ| 的实现，
    与 external_knowledge.sigma_floor_for（tolerance_overrides.yaml）构成两套口径；
    现统一由 EK 决定数值（by_variable → by_unit → fallback），本函数只保留
    data_audit 既有的 dict 契约（floor/source/unit/rule），以免断引用。

    Args:
        var: 变量名
        mu: 基线均值（fallback 时用 |μ| 计下限）
        entity_table: 量纲表覆盖（默认 knowledge.ENTITY_TABLE）
        unit: 显式量纲（优先于 entity_table 反查）
    """
    if unit is None:
        et = ENTITY_TABLE if entity_table is None else entity_table
        unit = et.get(var, {}).get("unit")
    floor, source = EK.sigma_floor_for(var, mu, unit)
    rule = (f"physical: {var}/unit={unit} -> {floor}" if source == "physical"
            else f"fallback: {floor} = ratio*|mu| (unit={unit})")
    return {"floor": float(floor), "source": source, "unit": unit, "rule": rule}


def variable_z_profile(var: str, baseline_values, main_values=None,
                       unit: Optional[str] = None,
                       entity_table: Optional[Dict] = None) -> Dict:
    """单变量基线统计 + 主窗 max|z|（纯函数）。

    max_abs_z_raw 用稳健 σ（max(1.4826·MAD, σ_floor)）且**不截断**，用于
    §4.4.1 第 5 步的 ">100 → Z病态" 判定；max_abs_z_clipped 是 clip(±10)
    后的报告值。若两者都截断，>100 的判据将恒不成立（恒真/恒假陷阱）。
    """
    x = np.asarray(baseline_values, dtype=float)
    x = x[np.isfinite(x)]
    n = int(x.size)
    mu = float(x.mean()) if n else float("nan")
    sigma = float(x.std()) if n else float("nan")
    mad = float(np.median(np.abs(x - mu))) if n else float("nan")
    cv = (sigma / abs(mu)) if (n and abs(mu) > 1e-12) else None

    # σ_floor 一律走 EK 唯一来源；显式 unit 直接传入，不再本地二次覆盖
    sf = sigma_floor_for(var, mu, entity_table, unit)

    sigma_robust = max(1.4826 * mad, sf["floor"]) if n else float("nan")
    potential = bool(cv is not None and cv < POTENTIAL_PATHOLOGY_CV)

    max_raw = max_clip = None
    if potential and sigma_robust > 1e-12 and main_values is not None:
        y = np.asarray(main_values, dtype=float)
        y = y[np.isfinite(y)]
        if y.size:
            z = (y - mu) / sigma_robust
            max_raw = float(np.max(np.abs(z)))
            max_clip = float(np.max(np.abs(np.clip(z, *Z_TRUNCATION))))

    return {
        "variable": var,
        "unit": sf.get("unit"),
        "n_baseline_points": n,
        "mu": mu,
        "sigma": sigma,
        "cv_sigma_over_abs_mu": cv,
        "mad": mad,
        "sigma_robust": sigma_robust,
        "sigma_floor": sf["floor"],
        "sigma_floor_source": sf["source"],
        "sigma_floor_rule": sf["rule"],
        "potential_pathology": potential,
        "max_abs_z_raw": max_raw,
        "max_abs_z_clipped": max_clip,
        "z_pathology": bool(potential and max_raw is not None
                            and max_raw > Z_PATHOLOGY_THRESHOLD),
    }


# ==================== B6 纯函数：CUSUM / DP 变点 ====================
def cusum_changepoints(x, threshold: float = CUSUM_THRESHOLD,
                       min_segment: int = CUSUM_MIN_SEGMENT) -> List[int]:
    """标准化 CUSUM 双侧检测（二分递归定位），返回变点索引（新段起点）。

    统计量：段内 CUSUM 路径 S_k = Σ_{i<k}(x_i − mean_seg)，标准化
    T_k = |mean(x[:k]) − mean(x[k:])| / (sd_seg·sqrt(1/k + 1/(n−k)))，
    取 argmax T_k；T_k > threshold 则在该点切分，两侧递归（双侧）。
    常数段（sd≈0）不切分；n < min_segment 不切分。
    """
    arr = np.asarray(x, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = int(arr.size)
    if n < max(4, min_segment):
        return []
    sd = float(arr.std())
    if not np.isfinite(sd) or sd < 1e-12:
        return []

    cps: List[int] = []

    def _split(a: int, b: int):
        m = b - a
        if m < max(4, min_segment):
            return
        seg = arr[a:b]
        sd_seg = float(seg.std())
        if not np.isfinite(sd_seg) or sd_seg < 1e-12:
            return
        best_k, best_t = -1, 0.0
        for k in range(max(1, min_segment // 2), m - max(1, min_segment // 2) + 1):
            left, right = seg[:k], seg[k:]
            se = sd_seg * np.sqrt(1.0 / k + 1.0 / (m - k))
            if se <= 0:
                continue
            t = abs(left.mean() - right.mean()) / se
            if t > best_t:
                best_k, best_t = k, float(t)
        if best_k > 0 and best_t > threshold:
            cps.append(a + best_k)
            _split(a, a + best_k)
            _split(a + best_k, b)

    _split(0, n)
    return sorted(cps)


def pelt_dp_changepoints(x, penalty: float = DP_PENALTY) -> List[int]:
    """分段常数均值的精确动态规划（L2 代价），返回变点索引（新段起点）。

    这是 PELT 的动态规划等价实现（同一 L2 代价 + 同一惩罚形式）：
        F[t] = min_{s<t} F[s] + C(s,t) + beta,  C(s,t)=Σ(x−mean)^2
        beta = penalty * log(n) * var(x)（BIC/MDL 惩罚）
    依赖 numpy，O(n²)；severity 序列 n ≤ 110，可接受。未调用 ruptures。
    常数序列（var≈0）返回空。
    """
    arr = np.asarray(x, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = int(arr.size)
    if n < 4:
        return []
    var = float(arr.var())
    if not np.isfinite(var) or var < 1e-12:
        return []
    beta = float(penalty) * float(np.log(n)) * var

    cs = np.concatenate([[0.0], np.cumsum(arr)])
    cs2 = np.concatenate([[0.0], np.cumsum(arr * arr)])

    def cost(a: int, b: int) -> float:
        m = b - a
        s = cs[b] - cs[a]
        s2 = cs2[b] - cs2[a]
        return float(s2 - s * s / m)

    F = np.full(n + 1, np.inf)
    F[0] = 0.0
    prev = np.zeros(n + 1, dtype=int)
    for b in range(1, n + 1):
        best, bi = np.inf, 0
        for a in range(b):
            c = F[a] + cost(a, b) + (beta if a > 0 else 0.0)
            if c < best:
                best, bi = c, a
        F[b], prev[b] = best, bi

    cps: List[int] = []
    b = n
    while b > 0:
        a = int(prev[b])
        if a > 0:
            cps.append(a)
        b = a
    return sorted(cps)


def linear_detrend(x) -> Tuple[np.ndarray, Dict]:
    """剔除线性剂量阶梯趋势，返回 (残差, 描述)。

    §4.4.2 的 null 模型：干净剂量阶梯本身是单调爬升，不是"分段常数"，
    直接对水平曲线做分段常数变点检测会把整条梯度切成伪变点（FLB WFLB
    严格线性时尤甚）。因此以"线性阶梯"为零假设，对残差做变点检测，
    报告"偏离单调梯度"的位置；原始水平检测结果同时落盘备查。
    """
    y = np.asarray(x, dtype=float)
    n = int(y.size)
    sev = np.arange(n, dtype=float)
    info = {"relative_residual": None, "linear_slope": None,
            "linear_intercept": None, "is_clean_ladder": False}
    if n < 4:
        return y.copy(), info
    slope, intercept = np.polyfit(sev, y, 1)
    resid = y - (intercept + slope * sev)
    rng = float(np.max(y) - np.min(y))
    rel = float(np.max(np.abs(resid)) / rng) if rng > 0 else 0.0
    info.update({"relative_residual": rel, "linear_slope": float(slope),
                 "linear_intercept": float(intercept),
                 "is_clean_ladder": bool(rel < LINEARITY_REL_TOL)})
    return resid, info


def cluster_indices(indices: Sequence[int], gap: int = CLUSTER_GAP) -> List[int]:
    """把相邻（间隔 <= gap）的变点合并为一处，取中位索引。"""
    out: List[int] = []
    group: List[int] = []
    for i in sorted(int(v) for v in indices):
        if group and i - group[-1] > gap:
            out.append(int(np.median(group)))
            group = []
        group.append(i)
    if group:
        out.append(int(np.median(group)))
    return out


def marker_profile(severities: Sequence[int], values: Sequence[float]) -> Dict:
    """marker–severity 曲线画像（纯函数）：Spearman、相邻差符号翻转、单调性。"""
    sev = np.asarray(list(severities), dtype=float)
    y = np.asarray(list(values), dtype=float)
    prof = {"n_points": int(y.size), "spearman_rho": None, "spearman_p": None,
            "monotonic": False, "direction": None, "n_sign_flips": None}
    if y.size < 3:
        return prof
    rho, p = spearmanr(sev, y)
    prof["spearman_rho"] = float(rho) if np.isfinite(rho) else None
    prof["spearman_p"] = float(p) if np.isfinite(p) else None

    d = np.diff(y)
    signs = np.sign(d)
    nz = signs[signs != 0]
    flips = int(np.sum(nz[1:] != nz[:-1])) if nz.size > 1 else 0
    prof["n_sign_flips"] = flips
    if np.isfinite(rho):
        prof["monotonic"] = bool(p < 0.05 and abs(rho) >= 0.9)
        if rho > 0:
            prof["direction"] = "increasing"
        elif rho < 0:
            prof["direction"] = "decreasing"
        else:
            prof["direction"] = "flat"
    return prof


# ==================== 读数据辅助 ====================
def _require(code: str) -> Dict:
    return ensure_class_available(code)


def _iter_main_windows(code: str, severities: Sequence[int],
                       window: Tuple[float, float]):
    for sev in severities:
        df = load_trajectory(code, sev)
        yield sev, window_slice(df, window[0], window[1])


def _column_stats(code: str, severities: Sequence[int], columns: Sequence[str]
                  ) -> Dict[str, Dict]:
    """跨全部轨迹的逐列 σ 与唯一值个数<3 判定（流式，不堆大矩阵）。"""
    n = 0
    mean = np.zeros(len(columns))
    m2 = np.zeros(len(columns))
    uniq: List[set] = [set() for _ in columns]

    for sev in severities:
        df = load_trajectory(code, sev)
        X = df[list(columns)].to_numpy(dtype=float)
        nb = X.shape[0]
        if nb == 0:
            continue
        mb = X.mean(axis=0)
        m2b = ((X - mb) ** 2).sum(axis=0)
        if n == 0:
            mean, m2 = mb, m2b
        else:
            tot = n + nb
            delta = mb - mean
            m2 = m2 + m2b + delta ** 2 * n * nb / tot
            mean = mean + delta * nb / tot
        n += nb
        for j in range(len(columns)):
            if len(uniq[j]) >= 3:
                continue
            for v in np.unique(X[:, j]):
                uniq[j].add(float(v))
                if len(uniq[j]) >= 3:
                    break

    sigma = np.sqrt(m2 / max(n, 1))
    out = {}
    for j, c in enumerate(columns):
        out[c] = {"mu": float(mean[j]), "sigma": float(sigma[j]),
                  "n_unique_capped": int(min(len(uniq[j]), 3)),
                  "n_points": int(n)}
    return out


# ==================== B4 数据审计 ====================
def audit_class_data(code: str, severities: Sequence[int],
                     columns: Optional[Sequence[str]] = None,
                     run_id: Optional[str] = None) -> Dict:
    """B4：轨迹数 / 严重度覆盖 / 列校验 / 常量列 / 窗口覆盖。"""
    cfg = _require(code)
    main_win, base_win = _class_windows(cfg)
    columns = list(columns) if columns is not None else list(ENTITY_TABLE.keys())
    sevs = sorted(int(s) for s in severities)

    audit: Dict = _common(code, run_id, "data_audit")
    audit.update({"columns_expected": len(columns),
                  "main_window": list(main_win),
                  "baseline_window": list(base_win)})

    # —— 轨迹数 / 严重度覆盖 ——
    present = set(sevs)
    missing_sev = [s for s in range(2, 101) if s not in present]
    extra_sev = sorted(s for s in present if s < 1 or s > 100)
    audit.update({
        "n_trajectories": len(sevs),
        "severity_range": [min(sevs), max(sevs)] if sevs else None,
        "severity_present": sevs,
        "severity_missing_vs_2_100": missing_sev,
        "severity_extra": extra_sev,
    })

    # —— 列校验（96 变量）——
    csv_cols = set(load_trajectory(code, sevs[0]).columns) if sevs else set()
    csv_cols.discard("TIME")
    missing_cols = [c for c in columns if c not in csv_cols]
    extra_cols = sorted(csv_cols - set(columns))
    col_ok = not missing_cols

    # —— 常量列（全轨迹 σ<1e-6 或唯一值<3）——
    avail_cols = [c for c in columns if c in csv_cols]
    stats = _column_stats(code, sevs, avail_cols) if (sevs and avail_cols) else {}
    constant_cols = [
        {"variable": c, "sigma": s["sigma"], "n_unique_capped": s["n_unique_capped"],
         "reason": ("sigma<1e-6" if s["sigma"] < CONSTANT_SIGMA else "n_unique<3")}
        for c, s in stats.items()
        if s["sigma"] < CONSTANT_SIGMA or s["n_unique_capped"] < 3
    ]

    # —— 窗口覆盖 ——
    main_short, base_short, n_rows, t_last_all = [], [], [], []
    for sev in sevs:
        df = load_trajectory(code, sev)
        t_last = float(df["TIME"].iloc[-1])
        t_last_all.append(t_last)
        n_rows.append(int(len(df)))
        if t_last < main_win[1] - 1e-9:
            main_short.append({"severity": sev, "t_last": t_last})
        if t_last < base_win[1] - 1e-9:
            base_short.append({"severity": sev, "t_last": t_last})
    window_ok = not main_short and not base_short

    audit.update({
        "column_check": {"n_expected": len(columns), "missing": missing_cols,
                         "extra": extra_cols, "pass": bool(col_ok)},
        "constant_columns": constant_cols,
        "constant_columns_count": len(constant_cols),
        "window_coverage": {
            "main_window": list(main_win), "baseline_window": list(base_win),
            "main_short": main_short, "baseline_short": base_short,
            "t_last_min": min(t_last_all) if t_last_all else None,
            "t_last_max": max(t_last_all) if t_last_all else None,
            "n_rows_min": min(n_rows) if n_rows else None,
            "n_rows_max": max(n_rows) if n_rows else None,
            "pass": bool(window_ok),
        },
    })

    checks = [
        {"name": "B4_columns_complete", "pass": bool(col_ok),
         "detail": {"missing": missing_cols, "extra": extra_cols}},
        {"name": "B4_severity_coverage_2_100", "pass": not missing_sev,
         "detail": {"n_missing": len(missing_sev),
                    "n_expected": 99, "n_present": len(present & set(range(2, 101)))}}
        if not missing_sev else
        {"name": "B4_severity_coverage_2_100", "pass": False,
         "detail": {"n_missing": len(missing_sev),
                    "first_missing": missing_sev[:10]}},
        {"name": "B4_window_coverage", "pass": bool(window_ok),
         "detail": {"n_main_short": len(main_short),
                    "n_baseline_short": len(base_short)}},
        {"name": "B4_constant_columns", "pass": True,
         "detail": {"n_constant": len(constant_cols),
                    "note": "常量列只登记不剔除（剔除由变量选取阶段 1 处理）"}},
    ]
    audit["checks"] = checks
    audit["pass"] = all(c["pass"] for c in checks)
    return audit


# ==================== B5 Z 病态审计 ====================
def _baseline_segment(code: str, sev: int, base_win: Tuple[float, float]
                      ) -> Tuple[np.ndarray, np.ndarray, Dict]:
    """取 sev 轨迹恢复段（注册表 baseline_window）；点数不足回退全段前 150 点。"""
    df = load_trajectory(code, sev)
    seg = window_slice(df, base_win[0], base_win[1])
    info = {"severity": sev, "requested_window": list(base_win),
            "start": float(seg["TIME"].iloc[0]) if len(seg) else None,
            "end": float(seg["TIME"].iloc[-1]) if len(seg) else None,
            "n_points": int(len(seg)), "source": "baseline_window", "note": ""}
    if len(seg) < MIN_BASELINE_POINTS:
        n_head = min(FALLBACK_HEAD_POINTS, len(df))
        seg = df.iloc[:n_head].reset_index(drop=True)
        info.update({"start": float(seg["TIME"].iloc[0]) if len(seg) else None,
                     "end": float(seg["TIME"].iloc[-1]) if len(seg) else None,
                     "n_points": int(len(seg)), "source": "fallback_head150",
                     "note": f"baseline_window 点数 < {MIN_BASELINE_POINTS}，回退前 "
                             f"{n_head} 点"})
    return seg, df, info


def audit_z_pathology(code: str, baseline_severities: Sequence[int] = (1,),
                      run_id: Optional[str] = None) -> Dict:
    """B5：方案 §4.4.1 六步 Z 病态审计（逐变量物理 σ_floor）。"""
    cfg = _require(code)
    main_win, base_win = _class_windows(cfg)
    sevs = list_class_severities(code)
    baseline_sevs = [int(s) for s in baseline_severities if int(s) in sevs]
    if not baseline_sevs:
        baseline_sevs = [sevs[0]] if sevs else []

    out: Dict = _common(code, run_id, "z_pathology_audit")
    _tol = EK.load_tolerance_overrides()
    out.update({"main_window": list(main_win), "baseline_window": list(base_win),
                "thresholds": {"potential_pathology_cv": POTENTIAL_PATHOLOGY_CV,
                               "z_pathology": Z_PATHOLOGY_THRESHOLD},
                "sigma_floor_config": {
                    # 唯一来源：external_knowledge + tolerance_overrides.yaml（V23-DESIGN-029）
                    "source": ("rca/v21/external_knowledge.sigma_floor_for + "
                               "external_knowledge/tolerance_overrides.yaml"),
                    "by_variable": _tol["by_variable"],
                    "by_unit": _tol["by_unit"],
                    "fallback": _tol["fallback"]},
                "z_truncation": list(Z_TRUNCATION)})

    if not sevs:
        out.update({"baseline_segment": None, "variables": {}, "pathology_variables": [],
                    "n_pathology": 0, "pathology_ratio": 0.0,
                    "sigma_over_mu_quantiles": {"p10": None, "p50": None, "p90": None},
                    "pass": True})
        return out

    seg, _, seg_info = _baseline_segment(code, baseline_sevs[0], base_win)
    cols = [c for c in seg.columns if c != "TIME"]

    profiles: Dict[str, Dict] = {}
    for c in cols:
        profiles[c] = variable_z_profile(c, seg[c].to_numpy(), None)

    # 所有轨迹主窗的逐变量 max|z|（仅潜在病态变量需要，但一并缓存便于复算）
    potential = [c for c, p in profiles.items() if p["potential_pathology"]]
    max_raw = {c: 0.0 for c in potential}
    max_clip = {c: 0.0 for c in potential}
    if potential:
        for _, win in _iter_main_windows(code, sevs, main_win):
            for c in potential:
                y = win[c].to_numpy(dtype=float)
                y = y[np.isfinite(y)]
                if not y.size:
                    continue
                mu = profiles[c]["mu"]
                sr = profiles[c]["sigma_robust"]
                if not np.isfinite(sr) or sr <= 1e-12:
                    continue
                z = np.abs((y - mu) / sr)
                max_raw[c] = max(max_raw[c], float(z.max()))
                max_clip[c] = max(max_clip[c], float(np.clip(z, *Z_TRUNCATION).max()))

    for c in potential:
        profiles[c]["max_abs_z_raw"] = max_raw[c]
        profiles[c]["max_abs_z_clipped"] = max_clip[c]
        profiles[c]["z_pathology"] = bool(max_raw[c] > Z_PATHOLOGY_THRESHOLD)

    pathology = [c for c in potential if profiles[c]["z_pathology"]]
    cvs = [p["cv_sigma_over_abs_mu"] for p in profiles.values()
           if p["cv_sigma_over_abs_mu"] is not None]
    quant = ({"p10": float(np.quantile(cvs, 0.10)),
              "p50": float(np.quantile(cvs, 0.50)),
              "p90": float(np.quantile(cvs, 0.90))} if cvs else
             {"p10": None, "p50": None, "p90": None})

    out.update({
        "baseline_segment": seg_info,
        "n_baseline_points": int(len(seg)),
        "n_trajectories_scanned": len(sevs),
        "n_variables": len(cols),
        "variables": profiles,
        "pathology_variables": pathology,
        "n_pathology": len(pathology),
        "pathology_ratio": (len(pathology) / len(cols)) if cols else 0.0,
        "potential_pathology_variables": potential,
        "n_potential_pathology": len(potential),
        "sigma_over_mu_quantiles": quant,
    })
    out["checks"] = [{
        "name": "B5_z_pathology",
        "pass": len(pathology) == 0,
        "detail": {"n_pathology": len(pathology), "variables": pathology,
                   "note": "Z病态只登记；标准化在算法层用稳健 σ + clip 处理"},
    }]
    out["pass"] = len(pathology) == 0
    return out


# ==================== B6 严重度变点检测 ====================
def audit_changepoints(code: str, run_id: Optional[str] = None,
                       penalty: float = DP_PENALTY,
                       cusum_threshold: float = CUSUM_THRESHOLD) -> Dict:
    """B6：marker 主窗中位数随 severity 的变点 / 单调性 / 稳定性评级。"""
    cfg = _require(code)
    main_win, _ = _class_windows(cfg)
    sevs = list_class_severities(code)
    marker = _class_marker(code, cfg)

    out: Dict = _common(code, run_id, "changepoint_report")
    out.update({"marker": marker, "main_window": list(main_win),
                "n_severities": len(sevs),
                "methods": {"cusum": {"threshold": cusum_threshold,
                                      "statistic": "standardized_two_sided_cusum_binary_seg"},
                            "dp": {"method": "dp_l2_bic",
                                   "penalty": penalty,
                                   "beta": "penalty*log(n)*var(x)",
                                   "complexity": "O(n^2)"}}})

    if not sevs or not marker:
        out.update({"marker_curve": [], "changepoints_index": [],
                    "changepoints_severity": [], "changepoints_cusum_severity": [],
                    "changepoints_dp_severity": [],
                    "group_boundaries_recommended": {},
                    "marker_monotonic": marker_profile([], []),
                    "marker_stability": "insufficient", "pass": True})
        return out

    curve = []
    for sev in sevs:
        df = load_trajectory(code, sev)
        win = window_slice(df, main_win[0], main_win[1])
        curve.append({"severity": sev,
                      "marker_median": float(win[marker].median()) if len(win) else None})
    ys = np.array([c["marker_median"] if c["marker_median"] is not None else np.nan
                   for c in curve], dtype=float)
    valid = np.isfinite(ys)
    sev_arr = np.array([c["severity"] for c in curve], dtype=float)[valid]
    y_arr = ys[valid]

    idx_cusum = cusum_changepoints(y_arr, threshold=cusum_threshold)
    idx_dp = pelt_dp_changepoints(y_arr, penalty=penalty)
    raw_primary = idx_dp if idx_dp else idx_cusum

    # 线性剂量阶梯 null：只在偏离单调梯度的残差上定位机制切换点
    resid, detrend_info = linear_detrend(y_arr)
    clean_ladder = detrend_info["is_clean_ladder"]
    if y_arr.size < MIN_SEVERITY_POINTS:
        clean_ladder = False
        detrend_info = dict(detrend_info, insufficient=True,
                            note=f"严重度点 {y_arr.size} < {MIN_SEVERITY_POINTS}，"
                                 "不做变点判定")
    idx_det_cusum: List[int] = []
    idx_det_dp: List[int] = []
    if clean_ladder or detrend_info.get("insufficient"):
        primary: List[int] = []
    else:
        idx_det_cusum = cusum_changepoints(resid, threshold=cusum_threshold)
        idx_det_dp = pelt_dp_changepoints(resid, penalty=penalty)
        primary = cluster_indices(idx_det_dp if idx_det_dp else idx_det_cusum)

    def _to_sev(idx: Iterable[int]) -> List[Dict]:
        return [{"index": int(i), "severity": int(sev_arr[i]),
                 "marker_median": float(y_arr[i])} for i in idx]

    cps_sev = _to_sev(primary)
    prof = marker_profile(sev_arr, y_arr)

    # 建议子段：仅在原三分组内部切分，不改全局 SEVERITY_BANDS
    rec: Dict[str, Dict] = {}
    for g in GROUP_ORDER:
        lo, hi = SEVERITY_BANDS[g]
        interior = sorted(int(s["severity"]) for s in cps_sev if lo < s["severity"] < hi)
        subsegments = []
        start = lo
        for cp in interior:
            subsegments.append([start, cp])
            start = cp + 1
        subsegments.append([start, hi])
        rec[g] = {"severity_band": [lo, hi],
                  "interior_changepoints": interior,
                  "subsegments": subsegments,
                  "note": ("建议子段（不改全局 SEVERITY_BANDS）；执行器按需采用"
                           if interior else "组内未检出变点，沿用原组区间")}

    if prof["n_points"] < MIN_SEVERITY_POINTS or detrend_info.get("insufficient"):
        stability = "insufficient"
    elif not prof["monotonic"] and cps_sev:
        stability = "unstable"
    elif prof["monotonic"] is False and prof["spearman_rho"] is None:
        stability = "insufficient"
    else:
        stability = "stable"

    out.update({
        "marker_curve": curve,
        "changepoints_index": [int(i) for i in primary],
        "changepoints_severity": cps_sev,
        "changepoints_method": "linear_detrended_dp_l2_bic",
        "detrend": dict(detrend_info,
                        linearity_rel_tol=LINEARITY_REL_TOL,
                        cluster_gap=CLUSTER_GAP,
                        note="线性剂量阶梯为零假设，检测残差偏离；干净线性阶梯返回空变点"),
        "changepoints_detrended_cusum_severity": _to_sev(idx_det_cusum),
        "changepoints_detrended_dp_severity": _to_sev(idx_det_dp),
        "changepoints_level_raw_severity": _to_sev(raw_primary),
        "changepoints_level_raw_cusum_severity": _to_sev(idx_cusum),
        "changepoints_level_raw_dp_severity": _to_sev(idx_dp),
        "group_boundaries_recommended": rec,
        "marker_monotonic": prof,
        "marker_stability": stability,
        "pass": True,
    })
    return out


# ==================== 执行器 ====================
def run_audit(code: str, run_id: Optional[str] = None) -> Dict:
    """B4+B5+B6 三份产物落盘；data_missing 抛 ClassDataMissing（上层捕获）。"""
    ensure_class_available(code)          # 先门控，缺数据不落任何占位产物
    sevs = list_class_severities(code)
    from causalrca.io import artifact
    audit_dir = artifact.subdir_path("audit", run_id)

    b4 = audit_class_data(code, sevs, run_id=run_id)
    b5 = audit_z_pathology(code, run_id=run_id)
    b6 = audit_changepoints(code, run_id=run_id)

    paths = {
        "data_audit": str(save_json(b4, f"data_audit_{code}.json", audit_dir)),
        "z_pathology_audit": str(
            save_json(b5, f"z_pathology_audit_{code}.json", audit_dir)),
        "changepoint_report": str(
            save_json(b6, f"changepoint_report_{code}.json", audit_dir)),
    }
    summary = {
        "code": code, "run_id": run_id,
        "n_trajectories": b4["n_trajectories"],
        "n_columns_missing": len(b4["column_check"]["missing"]),
        "n_constant_columns": b4["constant_columns_count"],
        "n_pathology": b5["n_pathology"],
        "pathology_variables": b5["pathology_variables"],
        "changepoints_severity": [c["severity"] for c in b6["changepoints_severity"]],
        "marker_stability": b6["marker_stability"],
        "paths": paths,
    }
    logger.info(f"[B audit] {code}: n_traj={summary['n_trajectories']} "
                f"pathology={summary['n_pathology']} "
                f"cp={summary['changepoints_severity']} "
                f"stability={summary['marker_stability']}")
    return summary


def run_all(codes: Optional[Sequence[str]] = None,
            run_id: Optional[str] = None) -> Dict:
    """逐类审计；ClassDataMissing 只登记跳过，不生成占位产物。"""
    codes = list(codes) if codes is not None else list(ACCIDENT_ORDER)
    audited: Dict[str, Dict] = {}
    skipped: List[Dict] = []
    for code in codes:
        try:
            audited[code] = run_audit(code, run_id=run_id)
        except ClassDataMissing as exc:
            logger.warning(f"[B audit] skip {code}: {exc}")
            skipped.append({"code": code, "reason": exc.reason,
                            "status": exc.status,
                            "expected_dir": str(exc.expected_dir)})
    summary = {"run_id": run_id, "n_audited": len(audited),
               "n_skipped": len(skipped), "audited": audited, "skipped": skipped}
    logger.info(f"[B audit] done: audited={summary['n_audited']} "
                f"skipped={[s['code'] for s in skipped]}")
    return summary


if __name__ == "__main__":
    run_all()

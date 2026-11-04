"""V2.3 §5.9 D2: 每类事故 sev-1 独立基准（稳健 σ + 分位数 + Z 病态标记）。

输出 tolerance_{k}.json，供 K2 异常判定和 D9 偏离度使用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from rca.v21 import data as D
from rca.rca_config_v21 import ACCIDENT_TYPES, MARKERS, SAFETY_STEP_VARS
from core.utils import logger, save_json

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


def compute_tolerance(accident_type: str) -> Dict:
    """计算单类事故的 sev-1 独立基准。"""
    sev1 = D.load_trajectory(accident_type, 1)
    all_cols = [c for c in sev1.columns if c != "TIME"]

    # 排除 marker 和安全系统变量（零正常，用非零即异常规则）
    excluded = set(MARKERS.values()) | set(SAFETY_STEP_VARS)
    model_cols = [c for c in all_cols if c not in excluded]

    start_idx, end_idx = _find_steady_segment(sev1, model_cols)
    steady = sev1.iloc[start_idx:end_idx]
    steady_time = (float(steady["TIME"].iloc[0]), float(steady["TIME"].iloc[-1]))

    thresholds: Dict[str, Dict] = {}
    z_pathology_vars: List[str] = []

    for c in model_cols:
        vals = steady[c].to_numpy(dtype=float)
        if np.std(vals) < 1e-6:
            continue  # 常量列跳过
        mu = float(np.mean(vals))
        mad = float(np.median(np.abs(vals - mu)))
        sigma_robust = float(max(1.4826 * mad, 0.01 * abs(mu))) if abs(mu) > 1e-9 else float(np.std(vals))
        quantiles = {f"p{int(q*100)}": float(np.quantile(vals, q)) for q in QUANTILES}

        is_saturated = c in SATURATED_VARS
        is_z_pathology = (sigma_robust / abs(mu)) < Z_PATHOLOGY_RATIO if abs(mu) > 1e-9 else False
        if is_z_pathology:
            z_pathology_vars.append(c)

        thresholds[c] = {
            "mu": mu,
            "sigma_robust": sigma_robust,
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

    result = {
        "accident_type": accident_type,
        "baseline_source": f"{accident_type}_1.csv sev-1 准稳态段",
        "excluded_vars": sorted(excluded & set(all_cols)),
        "thresholds": thresholds,
        "z_pathology_vars": z_pathology_vars,
        "quality": quality,
        "z_truncation": [-10, 10],
    }
    return result


def run_all_tolerances() -> Dict[str, Dict]:
    """为所有事故类型计算基准并落盘。"""
    out_dir = Path(D.V21_OUTPUT) / "tolerances"
    out_dir.mkdir(parents=True, exist_ok=True)
    all_results = {}
    for k in ACCIDENT_TYPES:
        tol = compute_tolerance(k)
        save_json(tol, f"tolerance_{k}", result_dir=out_dir)
        all_results[k] = tol
        q = tol["quality"]
        logger.info(f"[D2-tolerance] {k}: 稳态{q['steady_segment_points']}点, "
                    f"{q['n_variables_pass']}变量通过, "
                    f"Z病态{q['z_pathology_count']}个({q['z_pathology_ratio']:.1%}), "
                    f"quality={'PASS' if q['quality_pass'] else 'FAIL'}")
    return all_results


if __name__ == "__main__":
    run_all_tolerances()

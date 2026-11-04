"""V2.3 §5.10 D9: 数据偏离度计算。

d = min(|x - μ_k| / σ_k^robust, 10)
4 级着色：<3 正常, 3-5 中等, 5-7 较大, >7 严重
Z 病态变量和饱和变量不显示偏离度。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import numpy as np

from rca.v21 import data as D
from rca.v21.tolerance import compute_tolerance
from core.utils import logger, save_json

TOL_DIR = Path(D.V21_OUTPUT) / "tolerances"
OUT_DIR = Path(D.V21_OUTPUT) / "deviations"
OUT_DIR.mkdir(parents=True, exist_ok=True)

LEVELS = [
    (3.0, "normal",    "#2ecc71"),  # 正常-绿
    (5.0, "moderate",  "#f1c40f"),  # 中等-黄
    (7.0, "large",     "#e67e22"),  # 较大-橙
    (1e9, "severe",    "#e74c3c"),  # 严重-红
]


def level_of(d: float) -> Dict:
    for thresh, name, color in LEVELS:
        if d < thresh:
            return {"name": name, "color": color}
    return {"name": "severe", "color": "#e74c3c"}


def compute_deviation(accident_type: str, severity: int,
                      tolerance: Dict = None) -> Dict:
    """计算某事故某严重度的各变量偏离度。"""
    if tolerance is None:
        tol_path = TOL_DIR / f"tolerance_{accident_type}.json"
        if tol_path.exists():
            tolerance = D.load_json(str(tol_path))
        else:
            tolerance = compute_tolerance(accident_type)

    df = D.load_trajectory(accident_type, severity)
    thresholds = tolerance["thresholds"]

    deviations = {}
    for var, th in thresholds.items():
        if th.get("z_pathology") or th.get("saturated"):
            continue
        if var not in df.columns:
            continue
        vals = df[var].to_numpy(dtype=float)
        mu = th["mu"]
        sigma = th["sigma_robust"]
        # 取全轨迹最大偏离度
        abs_dev = np.abs(vals - mu) / (sigma + 1e-12)
        d = float(min(abs_dev.max(), 10.0))
        lvl = level_of(d)
        # 取最大偏离时刻的值和分位
        max_idx = int(np.argmax(abs_dev))
        q = th["quantiles"]
        val_at_max = float(vals[max_idx])
        # 分位定位
        sorted_q = sorted(q.items(), key=lambda x: x[1])
        quantile_label = ">p90"
        for qname, qval in sorted_q:
            if val_at_max <= qval:
                quantile_label = qname
                break

        deviations[var] = {
            "d": round(d, 3),
            "level": lvl["name"],
            "color": lvl["color"],
            "value_at_max": round(val_at_max, 4),
            "mu": round(mu, 4),
            "sigma_robust": round(sigma, 4),
            "quantile_at_max": quantile_label,
            "time_idx_max": max_idx,
        }

    # 排序：偏离度降序
    sorted_dev = dict(sorted(deviations.items(), key=lambda x: -x[1]["d"]))

    level_counts = {"normal": 0, "moderate": 0, "large": 0, "severe": 0}
    for v in sorted_dev.values():
        level_counts[v["level"]] += 1

    return {
        "accident_type": accident_type,
        "severity": severity,
        "n_variables": len(sorted_dev),
        "level_counts": level_counts,
        "deviations": sorted_dev,
        "note": "偏离度 ≠ 根因；4 条注意事项见方案 §5.10",
    }


def run_all_deviations():
    from rca.rca_config_v21 import ACCIDENT_TYPES
    for k in ACCIDENT_TYPES:
        tolerance = compute_tolerance(k)
        for sev in [10, 50, 100]:  # 三个严重度代表
            dev = compute_deviation(k, sev, tolerance)
            save_json(dev, f"deviation_{k}_sev{sev}", result_dir=OUT_DIR)
            lc = dev["level_counts"]
            logger.info(f"[D9-deviation] {k}_sev{sev}: "
                        f"正常={lc['normal']}, 中等={lc['moderate']}, "
                        f"较大={lc['large']}, 严重={lc['severe']}")


if __name__ == "__main__":
    run_all_deviations()

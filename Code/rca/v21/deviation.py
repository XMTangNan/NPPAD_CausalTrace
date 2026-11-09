"""V2.3 §5.10 D9: 数据偏离度计算（辅助证据层，不改变排名）。

d = min(|x - μ_k| / σ_k^robust, 10)
4 级着色：<3 正常, 3-5 中等, 5-7 较大, >7 严重
Z 病态变量和饱和变量不显示偏离度（沿用 V23-DESIGN-006 口径）。

收敛（V23-DESIGN-002/003）：公差由调用方传入（内存 dict）或从
`V23_SUBDIRS["tolerances"]` 读取，本模块不再拼接 v2.1 旧目录；
产物经 `artifact` 写入 `reports/deviation_{K}_{g}.json`。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Sequence

import numpy as np

from rca.rca_config_v21 import GROUP_ORDER, SEVERITY_BANDS
from rca.v21 import artifact
from rca.v21 import data as D
from rca.v21.tolerance import compute_tolerance
from core.utils import logger

TOP_N_DEVIATIONS = 10           # 落盘/挂载只保留 Top-N，完整级别表见 levels

LEVELS = [
    (3.0, "normal",    "#2ecc71"),  # 正常-绿
    (5.0, "moderate",  "#f1c40f"),  # 中等-黄
    (7.0, "large",     "#e67e22"),  # 较大-橙
    (1e9, "severe",    "#e74c3c"),  # 严重-红
]
LEVEL_NAMES = [name for _, name, _ in LEVELS]


def level_of(d: float) -> Dict:
    for thresh, name, color in LEVELS:
        if d < thresh:
            return {"name": name, "color": color}
    return {"name": "severe", "color": "#e74c3c"}


def _tolerance_path(accident_type: str) -> Path:
    return Path(artifact.V23_SUBDIRS["tolerances"]) / f"tolerance_{accident_type}.json"


def load_tolerance(accident_type: str) -> Dict:
    """优先读 tolerances/tolerance_{K}.json；缺失时按 sev-1 现算（不写盘）。"""
    path = _tolerance_path(accident_type)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    logger.warning(f"[D9] 缺 {path.name}，按 sev-1 现算公差（不落盘）")
    return compute_tolerance(accident_type)


def max_severity_in_group(accident_type: str, group: str) -> int:
    """该组实际存在轨迹中的最大 severity（low=33/mid=66/high=100 中实存者）。"""
    lo, hi = SEVERITY_BANDS[group]
    sevs = [s for s in D.list_class_severities(accident_type) if lo <= s <= hi]
    if not sevs:
        raise ValueError(f"{accident_type}/{group}: 该严重度区间无可用轨迹")
    return int(max(sevs))


def compute_deviation(accident_type: str, severity: int,
                      tolerance: Optional[Dict] = None) -> Dict:
    """计算某事故某严重度的各变量偏离度。"""
    if tolerance is None:
        tolerance = load_tolerance(accident_type)

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

    level_counts = {name: 0 for name in LEVEL_NAMES}
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


def deviation_for_group(accident_type: str, group: str, tolerance: Dict,
                        severity: Optional[int] = None) -> Dict:
    """组级偏离度摘要（挂载到签名 auxiliary.deviation / 图内着色）。

    severity 默认取该组实际存在的最大 severity；返回 {severity, deviations(TopN),
    level_counts, levels(全变量级别)}。
    """
    sev = int(severity) if severity is not None else max_severity_in_group(accident_type,
                                                                          group)
    dev = compute_deviation(accident_type, sev, tolerance)
    top = dict(list(dev["deviations"].items())[:TOP_N_DEVIATIONS])
    return {
        "accident_type": accident_type,
        "group": group,
        "severity": sev,
        "n_variables": dev["n_variables"],
        "level_counts": dev["level_counts"],
        "deviations": top,
        "levels": {v: d["level"] for v, d in dev["deviations"].items()},
        "note": "偏离度 ≠ 根因；仅辅助展示，不参与 T=(I−B)⁻¹ 排序",
    }


def run_all_deviations(run_id: str,
                       classes: Optional[Sequence[str]] = None,
                       groups: Optional[Sequence[str]] = None,
                       tolerances: Optional[Dict[str, Dict]] = None) -> Dict[str, Dict]:
    """遍历 READY_CODES × 三组，写 reports/deviation_{K}_{g}.json（同一 run_id）。"""
    from rca.v21.accident_config import READY_CODES
    classes = list(classes) if classes is not None else list(READY_CODES)
    groups = list(groups) if groups is not None else list(GROUP_ORDER)
    tolerances = tolerances or {}

    results: Dict[str, Dict] = {}
    for k in classes:
        tol = tolerances.get(k) or load_tolerance(k)
        for g in groups:
            summary = deviation_for_group(k, g, tol)
            artifact.write_artifact(
                f"deviation_{k}_{g}", summary, "reports", run_id=run_id,
                accident_type=k, group=g,
                source=f"tolerances/tolerance_{k}.json")
            results[f"{k}_{g}"] = summary
            lc = summary["level_counts"]
            logger.info(f"[D9-deviation] {k}_{g} (sev{summary['severity']}): "
                        f"正常={lc['normal']}, 中等={lc['moderate']}, "
                        f"较大={lc['large']}, 严重={lc['severe']}")
    return results


if __name__ == "__main__":
    run_all_deviations(artifact.new_run_id("v23d"))

"""V2.3 §5.10 D9: 数据偏离度计算（辅助证据层，不改变排名）。

V23-DESIGN-011（2026-11-10 决策）口径定稿为“模板内相对分位”：
- 原始强度 d_raw = max_t |x(t) − μ| / σ_robust（不截断，仅用于排序）；
- 展示值 d = min(d_raw, 10)（沿用 ±10 截断，避免极端数值撑爆版面）；
- 档位切点取本模板全部变量 d_raw 的 p50/p70/p85 分位：
  ≤p50 正常、p50–p70 中等、p70–p85 较大、>p85 严重；>p95 另标“极端”附注。
- 旧 3/5/7σ 绝对档保留为 `d_abs_level`（附注），不再用于着色：
  绝对 σ 倍数对 NPPAD 阶跃不适用（8/9 模板 severe 占比 72%–88%）。
- 病态变量（低方差通道）走稳健 σ + ±10 截断（V23-DESIGN-006）；**σ 不可比者不再给巨大数**
  （V23-CODE-029）：`σ_robust ≤ 0`，或无物理下限且本底零变异 → `d_status=not_comparable`、
  `d_raw=None`，不参与分位与排序。仅手工标记的饱和变量仍不展示。
- **第三态（V23-CODE-033，2026-11-10）**：σ 由 fallback/登记下限撑起、正常工况近似恒定
  （MAD=0 或 σ 与 |μ| 同量级且都在噪音量级）、正常工况参考带无展布（IQR=0 或远小于动态区间）
  且该通道在本轨迹幅值下实质为 0（`max(|μ|,σ) ≤ 1e-3·max|x|`）——四条同时满足时同样标
  `not_comparable`，理由 `degenerate_zero_channel`。此类通道的“N 倍 σ”只是拿任意下限当标尺，
  不是物理相对倍数。不新增截断、不改 `d` 定义、不改聚合方式。
- 图例统一为“通道偏移强度（模板内相对）”。

收敛（V23-DESIGN-002/003）：公差由调用方传入（内存 dict）或从
本 run 的 `tolerances/` 读取，本模块不再拼接 v2.1 旧目录；
产物经 `artifact` 写入 `reports/deviation_{K}_{g}.json`。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Sequence

import numpy as np

from causalrca.config.constants_v21 import GROUP_ORDER, SEVERITY_BANDS
from causalrca.io import artifact
from causalrca.data import loader as D
from causalrca.evidence import external_knowledge as EK
from causalrca.evidence.tolerance import compute_tolerance
from causalrca.io.logging import logger

TOP_N_DEVIATIONS = 10           # 落盘/挂载只保留 Top-N，完整级别表见 levels
D_TRUNCATION = 10.0             # d 展示截断（V23-DESIGN-011 保留）
D_STATUS_OK = "ok"
D_STATUS_NOT_COMPARABLE = "not_comparable"   # V23-CODE-029：不可比，d 不参与排序
PERCENTILE_CUTS = (50.0, 70.0, 85.0, 95.0)

# 相对分位档（V23-DESIGN-011）：切点来自本模板 d_raw 分布
RELATIVE_LEVELS = [
    (PERCENTILE_CUTS[0], "normal",    "#2ecc71"),  # 正常-绿
    (PERCENTILE_CUTS[1], "moderate",  "#f1c40f"),  # 中等-黄
    (PERCENTILE_CUTS[2], "large",     "#e67e22"),  # 较大-橙
    (1e9,                "severe",    "#e74c3c"),  # 严重-红
]
LEVEL_NAMES = [name for _, name, _ in RELATIVE_LEVELS]
# 旧绝对档（仅附注，不再着色）
ABS_LEVELS = [(3.0, "normal"), (5.0, "moderate"), (7.0, "large"), (1e9, "severe")]


def abs_level_of(d: float) -> str:
    """旧 3/5/7σ 绝对档（附注用，NPPAD 上已饱和，不参与着色）。"""
    for thresh, name in ABS_LEVELS:
        if d < thresh:
            return name
    return "severe"


def relative_level_of(percentile: float) -> Dict:
    """按模板内分位排序定位档位（p50/p70/p85 切点）。"""
    for thresh, name, color in RELATIVE_LEVELS:
        if percentile <= thresh + 1e-9:
            return {"name": name, "color": color}
    return {"name": "severe", "color": "#e74c3c"}


def _percentile_rank(value: float, values: np.ndarray) -> float:
    """名次百分位（最大值=100），并列取均值秩，返回 0–100。"""
    if values.size == 0:
        return 0.0
    less = float(np.sum(values < value))
    equal = float(np.sum(np.isclose(values, value, rtol=0.0, atol=1e-12)))
    rank = less + 0.5 * (equal + 1.0)
    return min(100.0, 100.0 * rank / float(values.size))


def _tolerance_path(accident_type: str, run_id: Optional[str] = None) -> Path:
    return artifact.subdir_path("tolerances", run_id) / f"tolerance_{accident_type}.json"


def load_tolerance(accident_type: str, run_id: Optional[str] = None) -> Dict:
    """优先读本 run 的 tolerances/tolerance_{K}.json；缺失时按 sev-1 现算（不写盘）。"""
    path = _tolerance_path(accident_type, run_id)
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
    """计算某事故某严重度的各变量偏离度（模板内相对分位档）。"""
    if tolerance is None:
        tolerance = load_tolerance(accident_type)

    df = D.load_trajectory(accident_type, severity)
    thresholds = tolerance["thresholds"]

    raw: Dict[str, Dict] = {}
    for var, th in thresholds.items():
        if th.get("saturated"):
            continue
        if var not in df.columns:
            continue
        vals = df[var].to_numpy(dtype=float)
        mu = th["mu"]
        sigma = float(th["sigma_robust"])
        sigma_mad = float(th.get("sigma_mad") or 0.0)
        floor_src = str(th.get("sigma_floor_source") or "unknown")
        _q = th.get("quantiles") or {}
        iqr = (float(_q["p90"]) - float(_q["p10"])) if ("p90" in _q and "p10" in _q) else None
        abs_max = float(np.abs(vals).max()) if vals.size else None
        ok, why = EK.is_deviation_comparable(
            sigma, floor_src, sigma_mad,
            mu=float(mu), sigma_floor=float(th.get("sigma_floor") or 0.0),
            iqr=iqr, abs_max=abs_max)
        if not ok:
            raw[var] = {
                "d_raw": None, "d": None,
                "d_status": D_STATUS_NOT_COMPARABLE, "d_status_reason": why,
                "value_at_max": None, "mu": round(mu, 4),
                "sigma_robust": round(sigma, 6), "sigma_floor_source": floor_src,
                "quantile_at_max": None, "time_idx_max": None,
                "z_pathology": bool(th.get("z_pathology")),
                "constant_pre_inject": bool(th.get("constant_pre_inject")),
            }
            continue
        abs_dev = np.abs(vals - mu) / sigma
        d_raw = float(abs_dev.max())
        max_idx = int(np.argmax(abs_dev))
        q = th["quantiles"]
        val_at_max = float(vals[max_idx])
        sorted_q = sorted(q.items(), key=lambda x: x[1])
        quantile_label = ">p90"
        for qname, qval in sorted_q:
            if val_at_max <= qval:
                quantile_label = qname
                break
        raw[var] = {
            "d_raw": d_raw,
            "d": round(min(d_raw, D_TRUNCATION), 3),
            "d_status": D_STATUS_OK,
            "value_at_max": round(val_at_max, 4),
            "mu": round(mu, 4),
            "sigma_robust": round(sigma, 4),
            "quantile_at_max": quantile_label,
            "time_idx_max": max_idx,
            "z_pathology": bool(th.get("z_pathology")),
            "constant_pre_inject": bool(th.get("constant_pre_inject")),
        }

    # 模板内分位切点（基于未截断 d_raw，避免截断并列导致整体 severe）
    d_values = np.array([r["d_raw"] for r in raw.values() if r["d_raw"] is not None],
                        dtype=float)
    if d_values.size >= 2:
        pcts = {f"p{int(q)}": float(np.percentile(d_values, q)) for q in PERCENTILE_CUTS}
    else:
        pcts = {f"p{int(q)}": (float(d_values[0]) if d_values.size else None)
                for q in PERCENTILE_CUTS}

    deviations = {}
    for var, r in raw.items():
        if r["d_raw"] is None:
            deviations[var] = {**r, "percentile_in_template": None,
                               "level": D_STATUS_NOT_COMPARABLE, "color": "#95a5a6",
                               "above_p95": False, "d_abs_level": None}
            continue
        pct = _percentile_rank(r["d_raw"], d_values)
        lvl = relative_level_of(pct)
        deviations[var] = {
            **r,
            "percentile_in_template": round(pct, 2),
            "level": lvl["name"],
            "color": lvl["color"],
            "above_p95": bool(pct > PERCENTILE_CUTS[3]),
            "d_abs_level": abs_level_of(r["d"]),
        }

    # 排序：原始强度降序
    sorted_dev = dict(sorted(
        deviations.items(),
        key=lambda x: -(x[1]["d_raw"] if x[1]["d_raw"] is not None else -1.0)))

    level_counts = {name: 0 for name in LEVEL_NAMES}
    abs_level_counts = {name: 0 for _, name in ABS_LEVELS}
    n_not_comparable = 0
    for v in sorted_dev.values():
        if v["d_raw"] is None:
            n_not_comparable += 1
            continue
        level_counts[v["level"]] += 1
        abs_level_counts[v["d_abs_level"]] += 1

    return {
        "accident_type": accident_type,
        "severity": severity,
        "n_variables": len(sorted_dev),
        "n_not_comparable": n_not_comparable,
        "not_comparable": [v for v, d in sorted_dev.items() if d["d_raw"] is None],
        "level_counts": level_counts,
        "abs_level_counts": abs_level_counts,
        "percentiles": pcts,
        "deviations": sorted_dev,
        "note": ("通道偏移强度（模板内相对）：档位按本模板 d_raw 的 "
                 "p50/p70/p85 切分，p95 以上另标“极端”；档位是模板内排序，"
                 "跨模板不可比绝对偏移量（不同模板档位计数相同是分位分档的必然结果，"
                 "不代表物理强度一致）；d=min(d_raw,10) 与 "
                 "d_abs_level（旧 3/5/7σ 绝对档）仅作附注；偏离度 ≠ 根因；"
                 "d_status=not_comparable 表示该变量无可用 σ 下限或属退化零通道（正常工况无展布、"
                 "相对倍数无物理含义），不参与分位与排序（V23-CODE-029/033）"),
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
        "n_not_comparable": dev.get("n_not_comparable", 0),
        "not_comparable": dev.get("not_comparable", []),
        "level_counts": dev["level_counts"],
        "abs_level_counts": dev["abs_level_counts"],
        "percentiles": dev["percentiles"],
        "deviations": top,
        "levels": {v: d["level"] for v, d in dev["deviations"].items()},
        "note": "通道偏移强度（模板内相对）；仅辅助展示，不参与 T=(I−B)⁻¹ 排序",
    }


def run_all_deviations(run_id: str,
                       classes: Optional[Sequence[str]] = None,
                       groups: Optional[Sequence[str]] = None,
                       tolerances: Optional[Dict[str, Dict]] = None) -> Dict[str, Dict]:
    """遍历 READY_CODES × 三组，写 reports/deviation_{K}_{g}.json（同一 run_id）。"""
    from causalrca.config.accident_config import READY_CODES
    classes = list(classes) if classes is not None else list(READY_CODES)
    groups = list(groups) if groups is not None else list(GROUP_ORDER)
    tolerances = tolerances or {}

    results: Dict[str, Dict] = {}
    for k in classes:
        tol = tolerances.get(k) or load_tolerance(k, run_id)
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

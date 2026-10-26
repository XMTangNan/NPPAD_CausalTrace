"""D3 正式流水线（方案 13.8.2）：轨迹级评分纯函数 + 指标聚合。

纪律（与 run_d0 同口径）：
- 纯函数、零学图、零标签泄漏：true 标签只允许出现在 run_r3 的指标聚合阶段；
- 测试侧 d/A 只用 shared_baseline 的 mu/sigma，marker/安注位不参与 E2/E3；
- E3 符号图 = effective_marker_map(cA,cL)；逐边 gCF 边门控 + 模板 template_gcf；
- E3_path 为 D0 冻结主证据；通道在该模板无可用证据（nan，如 reach=空）时，
  类聚合 max_g 按 0 分地板处理（"无路径证据"= 最差），原始模板分仍保留 nan 落盘；
- FULL/MASKED 融合权重取自 rca_config_v21（W_FULL/W_MASKED），本文件禁止散落魔数。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKERS, MARKER_COLS, W_FULL, W_MASKED,
    ANOMALY_K, ACTIVATION_FRAC,
)
from rca.v21.marker_effects import effective_marker_map
from rca.v21.inversion import (
    assert_no_marker_columns, signed_median_z, overlimit_rates,
    activation_set, e2_score, e3_base_channel, e3_path_channel,
    should_abstain,
)

PROTOCOL_D3 = "NPPAD-CausalTrace-v2.1-D3-13.7.6-13.7.7-13.8.2"
TEMPLATE_KEYS = [f"{k}_{g}" for k in ACCIDENT_TYPES for g in GROUP_ORDER]


# ==================== R1.5 门控读取（cf_validation/cf_<k>_<g>.json）====================
def load_cf_gate(cf_obj: Dict) -> Dict[str, object]:
    """cf JSON -> {"edge": {变量: gCF 0.5/1.0}, "template": template_gcf 0.5/1.0}。

    逐边键：edges[<variable>]["gCF"]；模板键：顶层 template_gcf。
    缺失边默认 1.0（通道函数同口径），禁止为标定/测试另造门控。
    """
    edge = {v: float(e["gCF"]) for v, e in cf_obj.get("edges", {}).items()}
    return {"edge": edge, "template": float(cf_obj["template_gcf"])}


# ==================== 测试观测（与 run_d0._d_aligned 同口径，这里在 V_thr 序上）====================
def observe_main_window(block: np.ndarray, cols: Sequence[str],
                        v_thr: Sequence[str], baseline: Dict
                        ) -> Dict[str, object]:
    """主窗块 -> d（V_thr 序有符号中位 z）、越限率、激活集 A(x)、Z。"""
    pos = {v: p for p, v in enumerate(cols)}
    mu, sigma = baseline["mu"], baseline["sigma"]
    T = block.shape[0]
    P = len(v_thr)
    Z = np.zeros((T, P))
    for j, v in enumerate(v_thr):
        Z[:, j] = (block[:, pos[v]] - mu[v]) / sigma[v]
    d = signed_median_z(Z)
    rates = overlimit_rates(Z, ANOMALY_K)
    A = activation_set(rates, v_thr, v_thr, ACTIVATION_FRAC)
    return {"d": d, "rates": rates, "A": A, "Z": Z}


def marker_main_medians(block: np.ndarray, cols: Sequence[str],
                        marker_cols: Sequence[str] = MARKER_COLS) -> Dict[str, float]:
    """FULL 条件：三 marker 主窗原始量纲中位数（MASKED 块无 marker 列，禁止调用）。"""
    pos = {v: p for p, v in enumerate(cols)}
    return {m: float(np.median(block[:, pos[m]])) for m in marker_cols}


def e1_vector(medians: Dict[str, float], epsilon: Dict[str, float]) -> Dict[str, float]:
    """E1（13.7.2，仅 FULL）：本类 marker 越阈 且 其余 marker 全不越阈 的互斥指示。"""
    out = {}
    for k in ACCIDENT_TYPES:
        own = MARKERS[k]
        own_on = abs(medians[own]) > epsilon[k]
        others_off = all(abs(medians[m]) <= epsilon[k_other]
                         for k_other, m in MARKERS.items() if m != own)
        out[k] = 1.0 if (own_on and others_off) else 0.0
    return out


# ==================== 单模板评分 ====================
def score_template_raw(d: np.ndarray, A: Sequence[str], v_thr: Sequence[str],
                       sig: Dict, gate: Dict[str, object]) -> Dict[str, float]:
    """对一个 (k,g) 模板算 E2 / E3_base / E3_path（含 gCF 逐边门控与 template_gcf）。

    E3 符号图统一用 effective_marker_map(cA,cL)；返回值为模板聚合分（已乘 template_gcf）。
    通道无可用证据时对应分量为 nan（调用方做类聚合地板处理）。
    """
    gp = sig["group_pattern"]
    assert gp["variables"] == list(v_thr), "group_pattern 变量序与 V_thr 不一致"
    template_d = np.asarray(gp["template_d"], dtype=float)
    template_over = gp["template_over"]
    e2 = e2_score(d, A, v_thr, template_d, template_over)

    eff = effective_marker_map(sig.get("cA", {}), sig["cL"])
    c_map = {v: info["c"] for v, info in eff.items()}
    pi_map = {v: info["pi"] for v, info in eff.items()}
    edge_gate = gate["edge"]
    e_base = e3_base_channel(d, v_thr, A, c_map, pi_map, edge_gate)
    e_path = e3_path_channel(d, v_thr, A, c_map, pi_map,
                             sig["frontier"], edge_gate)["score"]

    tg = float(gate["template"])
    return {
        "e2": float(e2) * tg,
        "e3_base": (float("nan") if math.isnan(e_base) else float(e_base) * tg),
        "e3_path": (float("nan") if math.isnan(e_path) else float(e_path) * tg),
    }


def _nanmax_or_zero(vals: Sequence[float]) -> float:
    finite = [float(v) for v in vals if not math.isnan(float(v))]
    return max(finite) if finite else 0.0


# ==================== min-max 归一（13.7.7）====================
def fit_minmax(values: Sequence[float]) -> Dict[str, object]:
    """按候选类跨全部标定轨迹的类级分拟合 min/max；零极差恒映射 0.5。"""
    arr = np.asarray([float(v) for v in values], dtype=float)
    lo, hi = float(arr.min()), float(arr.max())
    zero_range = bool(hi - lo < 1e-12)
    return {"min": 0.0 if zero_range else lo,
            "max": 1.0 if zero_range else hi,
            "zero_range": zero_range}


def apply_minmax(value: float, p: Dict[str, object]) -> float:
    if p["zero_range"]:
        return 0.5
    if math.isnan(float(value)):
        value = 0.0
    x = (float(value) - float(p["min"])) / (float(p["max"]) - float(p["min"]))
    return float(min(1.0, max(0.0, x)))


# ==================== 融合 / 决策 ====================
def fuse_scores(condition: str, e2_norm: Dict[str, float],
                e3_path_norm: Dict[str, float],
                e1: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """FULL: .3 E1 + .35 E2n + .35 E3path_n；MASKED: .5 E2n + .5 E3path_n。"""
    if condition == "FULL":
        assert e1 is not None, "FULL 必须有 E1"
        return {k: (W_FULL["E1"] * e1[k]
                    + W_FULL["E2"] * e2_norm[k]
                    + W_FULL["E3"] * e3_path_norm[k])
                for k in ACCIDENT_TYPES}
    if condition == "MASKED":
        return {k: (W_MASKED["E2"] * e2_norm[k]
                    + W_MASKED["E3"] * e3_path_norm[k])
                for k in ACCIDENT_TYPES}
    raise ValueError(f"unknown condition: {condition}")


def score_trajectory(block: np.ndarray, cols: Sequence[str], condition: str,
                     v_thr: Sequence[str], sigs: Dict[Tuple[str, str], Dict],
                     gates: Dict[Tuple[str, str], Dict[str, object]],
                     baseline: Dict, calibration: Dict,
                     epsilon: Optional[Dict[str, float]] = None) -> Dict:
    """对一条主窗轨迹在指定条件下完整评分（纯函数，不读任何标签/target）。

    sigs/gates 键为 (k, g)；calibration 为 calibration_params.json。
    """
    if condition not in ("FULL", "MASKED"):
        raise ValueError(f"condition 必须是 FULL/MASKED，收到 {condition}")
    if condition == "MASKED":
        assert_no_marker_columns(cols, MARKER_COLS)  # G4 运行时零访问断言
    obs = observe_main_window(block, cols, v_thr, baseline)
    d, A = obs["d"], obs["A"]

    per_template: Dict[str, Dict[str, float]] = {}
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            per_template[f"{k}_{g}"] = score_template_raw(
                d, A, v_thr, sigs[(k, g)], gates[(k, g)])

    class_raw: Dict[str, Dict[str, float]] = {}
    for k in ACCIDENT_TYPES:
        vals = {m: [per_template[f"{k}_{g}"][m] for g in GROUP_ORDER]
                for m in ("e2", "e3_base", "e3_path")}
        class_raw[k] = {m: _nanmax_or_zero(v) for m, v in vals.items()}

    mm = calibration["minmax"]
    e2_norm = {k: apply_minmax(class_raw[k]["e2"], mm["E2"][k])
               for k in ACCIDENT_TYPES}
    e3p_norm = {k: apply_minmax(class_raw[k]["e3_path"], mm["E3_path"][k])
                for k in ACCIDENT_TYPES}

    e1 = None
    if condition == "FULL":
        if epsilon is None:
            raise ValueError("FULL 条件必须提供 epsilon（marker_scale.json）")
        e1 = e1_vector(marker_main_medians(block, cols), epsilon)
    fusion = fuse_scores(condition, e2_norm, e3p_norm, e1)

    ranking = sorted(fusion.items(), key=lambda kv: kv[1], reverse=True)
    pred, s1 = ranking[0]
    s2 = ranking[1][1] if len(ranking) > 1 else 0.0
    margin = float(s1 - s2)
    reason = should_abstain(A, np.asarray([fusion[k] for k in ACCIDENT_TYPES]),
                            float(calibration["tau"]), float(calibration["delta"]))
    return {
        "condition": condition,
        "A": list(A), "n_activation": len(A),
        "per_template": per_template,
        "class_raw": class_raw,
        "class_norm": {"e2": e2_norm, "e3_path": e3p_norm},
        "e1": e1,
        "fusion": {k: float(v) for k, v in fusion.items()},
        "ranking": [[k, float(s)] for k, s in ranking],
        "pred": pred if reason is None else None,
        "argmax_class": pred,
        "s1": float(s1), "s2": float(s2), "margin": margin,
        "accepted": reason is None,
        "abstain_reason": reason,
    }


# ==================== 指标聚合（13.8.2；唯一允许使用 true 标签处）====================
def wilson_ci(x: int, n: int, z: float = 1.96) -> Optional[Tuple[float, float]]:
    """Wilson score 95% 区间；n=0 不可估 -> None。"""
    if n <= 0:
        return None
    p = x / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def compute_metrics(rows: Sequence[Dict], classes: Sequence[str] = ACCIDENT_TYPES
                    ) -> Dict[str, object]:
    """rows: [{true, pred, accepted, s1, s2, margin, n_activation}]。

    Overall Accuracy：弃权=错；Selective Accuracy：仅接受样本；
    macro-F1：接受样本上逐类（某类接受预测数=0 -> null 且报该类弃权率，不填充）。
    """
    n = len(rows)
    accepted = [bool(r["accepted"]) for r in rows]
    correct = [bool(r["accepted"]) and r["pred"] == r["true"] for r in rows]
    n_acc = sum(accepted)
    n_correct = sum(correct)
    overall = n_correct / n if n else float("nan")
    selective = n_correct / n_acc if n_acc else None

    per_class: Dict[str, Dict[str, object]] = {}
    f1_available = []
    null_classes = []
    for k in classes:
        true_k = [r for r in rows if r["true"] == k]
        tp = sum(1 for r in true_k if r["accepted"] and r["pred"] == k)
        fp = sum(1 for r in rows if r["accepted"] and r["pred"] == k and r["true"] != k)
        fn = sum(1 for r in true_k if not (r["accepted"] and r["pred"] == k))
        support = len(true_k)
        n_accept_pred_k = tp + fp
        n_accept_true_k = sum(1 for r in true_k if r["accepted"])
        abstain_rate_k = (1 - n_accept_true_k / support) if support else None
        if n_accept_pred_k == 0:
            f1 = None
            null_classes.append(k)
        else:
            f1 = (2 * tp / (2 * tp + fp + fn)) if (2 * tp + fp + fn) > 0 else 0.0
            f1_available.append(f1)
        per_class[k] = {
            "support": support, "tp": tp, "fp": fp, "fn": fn,
            "accepted_pred_n": n_accept_pred_k,
            "accepted_true_n": n_accept_true_k,
            "abstention_rate": abstain_rate_k,
            "f1": f1,
        }
    macro_f1 = float(np.mean(f1_available)) if f1_available and not null_classes else None
    return {
        "n": n, "n_accepted": n_acc, "n_correct": n_correct,
        "overall_accuracy": overall,
        "selective_accuracy": selective,
        "coverage": n_acc / n if n else float("nan"),
        "abstention_rate": 1 - n_acc / n if n else float("nan"),
        "wilson95_overall": wilson_ci(n_correct, n),
        "wilson95_selective": wilson_ci(n_correct, n_acc) if n_acc else None,
        "per_class": per_class,
        "macro_f1": macro_f1,
        "macro_f1_mean_available": (float(np.mean(f1_available))
                                    if f1_available else None),
        "macro_f1_null_classes": null_classes,
    }

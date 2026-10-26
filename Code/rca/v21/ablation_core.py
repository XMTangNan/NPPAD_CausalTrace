"""§13.11 核心消融：轨迹级评分纯函数（零重学图，复用 pipeline 纯函数）。

三类消融（全部只改评分环节，不动标定 calibration_params.json）：
- 组件消融 −E1 / −E2 / −E3：对应证据分量权重置 0，**不重归一**（与方案
  「对应证据分量权重置 0」字面一致；E2/E3 仍保持 0.35/0.35、FULL 下 E1 停用后
  E2/E3 也不放大）。注意：不重归一会使融合分整体缩水，从而更易触发 below_tau
  弃权——这是规格明确选择的语义，非 bug。
- 引擎消融 −ANM（仅 LiNGAM）/ −LiNGAM（仅 ANM）：effective_marker_map 强制
  source=L / source=A，E3_base 与 E3_path 两通道都用同一强制符号引擎；拓扑
  frontier 仍用 LiNGAM 冻结稳定边（13.7.5 规定拓扑恒由 LiNGAM 提供）。
- 拓扑消融 −E3-base（E3 只用 path 通道）/ −E3-path（E3 只用 base 通道）。
  因 D0 已把主证据冻结为 E3_path，−E3-base 与 full 等价（如实报告）；
  −E3-path 的 E3_base 用同一 E3_path 标定 minmax 归一（calibration 中不存在
  E3_base 的 minmax 表，且禁止测试时重拟合以杜绝泄漏）。

纪律：本文件禁止改 pipeline.py / inversion.py / marker_effects.py；仅 import
其底层纯函数并传不同参数。full 变体与 pipeline.score_trajectory 逐字段一致，
由 tests/test_ablation_core.py 对拍。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKER_COLS, W_FULL, W_MASKED,
)
from rca.v21.inversion import (
    assert_no_marker_columns, e2_score, e3_base_channel, e3_path_channel,
    should_abstain,
)
from rca.v21.pipeline import (
    apply_minmax, e1_vector, marker_main_medians, observe_main_window,
    score_template_raw, _nanmax_or_zero,
)

ABLATIONS = ["full", "E1", "E2", "E3", "ANM", "LiNGAM", "E3-base", "E3-path"]
CONDITIONS = ["FULL", "MASKED"]

# 与 marker_effects.effective_marker_map 默认 pi_thr 同口径（方案 13.7.4）。
_EFFECTIVE_PI_THR = 0.70


# ==================== 引擎强制（−ANM / −LiNGAM）====================
def _forced_effective_marker_map(cA: Dict, cL: Dict, engine: str
                                 ) -> Dict[str, Dict]:
    """effective_marker_map 的单引擎强制版：只用 cL（engine='L'）或 cA（engine='A'）。

    不复用 effective_marker_map 的 A 优先/回退逻辑——引擎消融要求彻底停用另一引擎。
    source 标签仅为落盘/诊断，不影响 e3_base_channel/e3_path_channel 的数值。
    """
    if engine == "L":
        return {v: {"c": float(info["cL"]), "pi": float(info["piL"]),
                    "source": "L" if float(info["piL"]) >= _EFFECTIVE_PI_THR
                    else "L_unstable"}
                for v, info in cL.items()}
    if engine == "A":
        out: Dict[str, Dict] = {}
        for v, info in cA.items():
            est = bool(info.get("estimable", False))
            pi = float(info.get("piA", 0.0))
            out[v] = {"c": float(info["cA"]), "pi": pi,
                      "source": "A" if (est and pi >= _EFFECTIVE_PI_THR)
                      else "A_unstable"}
        return out
    raise ValueError(f"未知引擎 engine={engine}（只接受 'L' / 'A'）")


def _score_template_ablation(d: np.ndarray, A: Sequence[str], v_thr: Sequence[str],
                             sig: Dict, gate: Dict[str, object], engine: Optional[str]
                             ) -> Dict[str, float]:
    """score_template_raw 的引擎可切换版；engine=None 直接复用 pipeline 原函数。"""
    if engine is None:
        return score_template_raw(d, A, v_thr, sig, gate)

    gp = sig["group_pattern"]
    assert gp["variables"] == list(v_thr), "group_pattern 变量序与 V_thr 不一致"
    template_d = np.asarray(gp["template_d"], dtype=float)
    template_over = gp["template_over"]
    e2 = e2_score(d, A, v_thr, template_d, template_over)

    eff = _forced_effective_marker_map(sig.get("cA", {}), sig["cL"], engine)
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


# ==================== 融合（支持分量权重置 0，不重归一）====================
def _ablation_weights(condition: str, ablation: str) -> Dict[str, float]:
    """返回该变体下 FULL/MASKED 的融合权重；组件消融置 0 后其余权重保持原值。"""
    if condition == "FULL":
        if ablation == "E1":
            return {"E1": 0.0, "E2": W_FULL["E2"], "E3": W_FULL["E3"]}
        if ablation == "E2":
            return {"E1": W_FULL["E1"], "E2": 0.0, "E3": W_FULL["E3"]}
        if ablation == "E3":
            return {"E1": W_FULL["E1"], "E2": W_FULL["E2"], "E3": 0.0}
        return dict(W_FULL)
    if condition == "MASKED":
        if ablation == "E2":
            return {"E2": 0.0, "E3": W_MASKED["E3"]}
        if ablation == "E3":
            return {"E2": W_MASKED["E2"], "E3": 0.0}
        # MASKED 无 E1：−E1 与 full 等价
        return dict(W_MASKED)
    raise ValueError(f"unknown condition: {condition}")


def _fuse(condition: str, e2_norm: Dict[str, float], e3_norm: Dict[str, float],
          e1: Optional[Dict[str, float]], weights: Dict[str, float]
          ) -> Dict[str, float]:
    """与 pipeline.fuse_scores 逐项对齐，但权重由 weights 决定（支持置 0 不重归一）。"""
    if condition == "FULL":
        assert e1 is not None, "FULL 必须有 E1"
        return {k: (weights["E1"] * e1[k]
                    + weights["E2"] * e2_norm[k]
                    + weights["E3"] * e3_norm[k])
                for k in ACCIDENT_TYPES}
    if condition == "MASKED":
        return {k: (weights["E2"] * e2_norm[k]
                    + weights["E3"] * e3_norm[k])
                for k in ACCIDENT_TYPES}
    raise ValueError(f"unknown condition: {condition}")


# ==================== 主入口 ====================
def score_ablation(block: np.ndarray, cols: Sequence[str], condition: str,
                   v_thr: Sequence[str], sigs: Dict[Tuple[str, str], Dict],
                   gates: Dict[Tuple[str, str], Dict[str, object]],
                   baseline: Dict, calibration: Dict,
                   epsilon: Optional[Dict[str, float]],
                   ablation: str) -> Dict:
    """对一条主窗轨迹在指定消融变体下完整评分（纯函数，不读任何标签/target）。

    返回结构与 pipeline.score_trajectory 完全一致（ablation='full' 时逐字段相等）。
    class_norm['e3_path'] 恒为融合所用 E3 通道的归一化分（full/−E3-base 为 path，
    −E3-path 为 base）；弃权语义与 τ/δ 沿用 calibration_params.json。
    """
    if ablation not in ABLATIONS:
        raise ValueError(f"未知消融变体 {ablation}，可选 {ABLATIONS}")
    if condition not in ("FULL", "MASKED"):
        raise ValueError(f"condition 必须是 FULL/MASKED，收到 {condition}")
    if condition == "MASKED":
        assert_no_marker_columns(cols, MARKER_COLS)

    engine: Optional[str] = None
    if ablation == "ANM":
        engine = "L"          # −ANM：仅 LiNGAM
    elif ablation == "LiNGAM":
        engine = "A"          # −LiNGAM：仅 ANM

    obs = observe_main_window(block, cols, v_thr, baseline)
    d, A = obs["d"], obs["A"]

    per_template: Dict[str, Dict[str, float]] = {}
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            per_template[f"{k}_{g}"] = _score_template_ablation(
                d, A, v_thr, sigs[(k, g)], gates[(k, g)], engine)

    class_raw: Dict[str, Dict[str, float]] = {}
    for k in ACCIDENT_TYPES:
        vals = {m: [per_template[f"{k}_{g}"][m] for g in GROUP_ORDER]
                for m in ("e2", "e3_base", "e3_path")}
        class_raw[k] = {m: _nanmax_or_zero(v) for m, v in vals.items()}

    mm = calibration["minmax"]
    e2_norm = {k: apply_minmax(class_raw[k]["e2"], mm["E2"][k])
               for k in ACCIDENT_TYPES}

    # 拓扑消融的 E3 通道选择；其余变体沿用冻结主证据 E3_path。
    # −E3-path 无独立 E3_base minmax 表，用 E3_path minmax 归一（见模块 docstring）。
    e3_channel = "e3_base" if ablation == "E3-path" else "e3_path"
    e3_norm = {k: apply_minmax(class_raw[k][e3_channel], mm["E3_path"][k])
               for k in ACCIDENT_TYPES}

    e1 = None
    if condition == "FULL":
        if epsilon is None:
            raise ValueError("FULL 条件必须提供 epsilon（marker_scale.json）")
        e1 = e1_vector(marker_main_medians(block, cols), epsilon)

    weights = _ablation_weights(condition, ablation)
    fusion = _fuse(condition, e2_norm, e3_norm, e1, weights)

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
        "class_norm": {"e2": e2_norm, "e3_path": e3_norm},
        "e1": e1,
        "fusion": {k: float(v) for k, v in fusion.items()},
        "ranking": [[k, float(s)] for k, s in ranking],
        "pred": pred if reason is None else None,
        "argmax_class": pred,
        "s1": float(s1), "s2": float(s2), "margin": margin,
        "accepted": reason is None,
        "abstain_reason": reason,
    }

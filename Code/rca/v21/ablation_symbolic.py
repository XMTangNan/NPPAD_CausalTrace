"""v2.1 §13.11 三类神经符号/结构消融库：−本体约束 / −过程本体 / MASKED-strict。

纪律（只新增本文件，禁止改动现有模块）：
- −本体约束：走 signatures.bootstrap_lingam_template 的 use_ontology=False 开关，
  学图窗 [60,1500]、100 次轨迹 Bootstrap，C^A 照常（marker_ca_column 50 次）；
- −过程本体：A(x) 口径从 μ±3σ 换成 μ±3·MAD（方案字面，不乘 1.4826；1.4826 是
  MAD→σ 的标准一致性系数，仅供对照，见 fit_mad_baseline docstring）。d 仍用 σ 尺度
  （E2/E3 签名 cL/cA/group_pattern 不变，只替换 A(x)），弃权三条件里的 A=∅ 用 A_MAD；
- MASKED-strict：无 marker 重学 G_k′（V_graph−marker 上的 LiNGAM），不学 C^A；
  MASKED 评分仅 E2 分类 + E3′ 诊断（e3_base/e3_path 照算但不进入融合，融合 E3=0）。
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKERS, MARKER_COLS, W_MASKED,
    ANOMALY_K, ACTIVATION_FRAC, LINGAM_N_BOOTSTRAP, GLOBAL_SEED,
    GAM_MARKER_N_BOOTSTRAP,
    LEARN_WINDOW_START, LEARN_WINDOW_END, MAIN_WINDOW_START, MAIN_WINDOW_END,
    LEARN_WINDOW_POINTS, MAIN_WINDOW_POINTS,
    V21_OUTPUT, PHYSICAL_CANDIDATES, BASELINE_SEVERITIES,
    BASELINE_WINDOW_START, BASELINE_WINDOW_END, SAFETY_STEP_VARS,
)
from rca.v21 import data as D
from rca.v21.knowledge import build_ontology, apply_ontology_constraint
from rca.v21.signatures import (
    bootstrap_lingam_template, load_signature, save_signature,
    compute_graph_only_scale, transform_graph_block, select_stable_edges,
)
from rca.v21.marker_effects import trajectory_steady_pairs, marker_ca_column
from rca.v21.inversion import (
    signed_median_z, overlimit_rates, activation_set, should_abstain,
    assert_no_marker_columns, build_group_pattern,
)
from rca.v21.pipeline import (
    observe_main_window, score_template_raw, _nanmax_or_zero,
    apply_minmax, fuse_scores, e1_vector, marker_main_medians,
)
from core.utils import logger

NO_ONTOLOGY_SIGNATURE_DIR = V21_OUTPUT / "signatures_no_ontology"
STRICT_SIGNATURE_DIR = V21_OUTPUT / "signatures_strict"

# 方案字面：阈值=μ±3·MAD，不乘 1.4826。1.4826 是 MAD→σ 的标准一致性系数，
# 若乘上会放大尺度使激活更稀疏；本消融按方案字面只做稳健尺度替换，写死为常量便于审查。
MAD_TO_SIGMA_CONSISTENCY = 1.4826


# ==================== 复用 run_r1 口径（不 import 其私有函数） ====================
def _load_blocks(manifest: Dict, k: str, g: str, gvars: Sequence[str]
                 ) -> Tuple[List[np.ndarray], List[np.ndarray], List[int]]:
    """返回 (学图块[60,1500], 主窗块[300,1500], train sev 列表)。"""
    sevs = manifest["classes"][k]["groups"][g]["train_template_severities"]
    learn_blocks, main_blocks = [], []
    for sev in sevs:
        Xl, cols, tl = D.load_window(k, sev, LEARN_WINDOW_START, LEARN_WINDOW_END, gvars)
        assert cols == list(gvars) and len(tl) == LEARN_WINDOW_POINTS, (k, g, sev, len(tl))
        Xm, cols2, tm = D.load_window(k, sev, MAIN_WINDOW_START, MAIN_WINDOW_END, gvars)
        assert cols2 == list(gvars) and len(tm) == MAIN_WINDOW_POINTS, (k, g, sev, len(tm))
        learn_blocks.append(Xl)
        main_blocks.append(Xm)
    return learn_blocks, main_blocks, sevs


def _group_pattern(blocks: List[np.ndarray], gvars: Sequence[str],
                   v_thr: Sequence[str], baseline: Dict) -> Dict:
    """训练侧组模式（V_thr 子集；marker/安注位不进 E2）。"""
    pos = {v: i for i, v in enumerate(gvars)}
    mu, sigma = baseline["mu"], baseline["sigma"]
    d_list, over_sets = [], []
    for X in blocks:
        Z = np.zeros((X.shape[0], len(gvars)))
        for p, v in enumerate(gvars):
            if v in mu:
                Z[:, p] = (X[:, p] - mu[v]) / sigma[v]
        d = signed_median_z(Z)
        rates = overlimit_rates(Z, ANOMALY_K)
        d_vthr = np.array([d[pos[v]] for v in v_thr])
        A_t = activation_set(rates, gvars, v_thr, ACTIVATION_FRAC)
        d_list.append(d_vthr)
        over_sets.append(A_t)
    pat = build_group_pattern(d_list, over_sets, v_thr)
    return {"variables": list(v_thr),
            "template_d": pat["template_d"].tolist(),
            "template_over": pat["template_over"]}


# ==================== A. −本体约束（use_ontology=False） ====================
def learn_no_ontology_template(manifest: Dict, vsets: Dict, baseline: Dict,
                               scale: Dict, k: str, g: str,
                               n_bootstrap: int = LINGAM_N_BOOTSTRAP,
                               seed: int = GLOBAL_SEED) -> Dict:
    """−本体约束签名：use_ontology=False 重学图 + 照常 C^A + 组模式。"""
    gvars = vsets["V_graph"][k]
    v_thr = vsets["V_thr"]
    learn_blocks, main_blocks, sevs = _load_blocks(manifest, k, g, gvars)
    ontology = build_ontology(k, gvars)
    sig = bootstrap_lingam_template(
        learn_blocks, gvars, MARKERS[k], ontology,
        baseline["mu"], baseline["sigma"], scale["median_platform"][k],
        n_bootstrap=n_bootstrap, seed=seed, use_ontology=False)
    pat = _group_pattern(main_blocks, gvars, v_thr, baseline)
    n_ca = min(GAM_MARKER_N_BOOTSTRAP, n_bootstrap)
    x, Y, symptoms = trajectory_steady_pairs(
        main_blocks, gvars, MARKERS[k], v_thr,
        baseline["mu"], baseline["sigma"], scale["median_platform"][k])
    sig["group_pattern"] = pat
    sig["cA"] = marker_ca_column(x, Y, symptoms, n_bootstrap=n_ca, seed=seed)
    sig["learn_window"] = [LEARN_WINDOW_START, LEARN_WINDOW_END]
    sig["train_severities"] = sevs
    sig["ablation"] = "no_ontology"
    return sig


# ==================== C. MASKED-strict（无 marker 重学） ====================
def aggregate_edge_stats_no_marker(B_list: List[np.ndarray],
                                   variables: List[str]) -> Dict:
    """无 marker 图边统计（无 cL/frontier，只出 f_e/dir_e/权重）。"""
    B_arr = np.stack(B_list)
    n = len(variables)
    R = len(B_list)
    present = np.abs(B_arr) > 1e-10
    f_e = present.mean(axis=0)
    count = present.sum(axis=0)
    rev_count = count.T
    pair_total = count + rev_count
    dir_e = np.where(pair_total > 0, count / np.maximum(pair_total, 1), 0.0)
    strength_med = np.median(B_arr, axis=0)
    strength_q1 = np.quantile(B_arr, 0.25, axis=0)
    strength_q3 = np.quantile(B_arr, 0.75, axis=0)
    edges = []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            edges.append({
                "from": variables[j], "to": variables[i],
                "f_e": float(f_e[i, j]), "dir_e": float(dir_e[i, j]),
                "weight_median": float(strength_med[i, j]),
                "weight_iqr": float(strength_q3[i, j] - strength_q1[i, j]),
                "count": int(count[i, j]),
            })
    return {"edges": edges}


def bootstrap_lingam_no_marker(blocks: List[np.ndarray], variables: List[str],
                               ontology: Dict, mu: Dict[str, float],
                               sigma: Dict[str, float],
                               n_bootstrap: int = LINGAM_N_BOOTSTRAP,
                               seed: int = GLOBAL_SEED,
                               use_ontology: bool = True) -> Dict:
    """V_graph−marker 上的轨迹级 Bootstrap LiNGAM（无 marker 节点，无 cL/frontier）。"""
    from lingam import DirectLiNGAM
    rng = np.random.default_rng(seed)
    graph_only = compute_graph_only_scale(blocks, variables, None, mu)
    n_traj = len(blocks)
    B_list: List[np.ndarray] = []
    for _ in range(n_bootstrap):
        sampled = rng.integers(0, n_traj, size=n_traj)
        raw = np.vstack([blocks[i] for i in sampled])
        Z = transform_graph_block(raw, variables, None, mu, sigma, None, graph_only)
        model = DirectLiNGAM()
        model.fit(Z)
        B_c, _rep = apply_ontology_constraint(model.adjacency_matrix_, variables,
                                              ontology, use_ontology)
        B_list.append(B_c)
    stats = aggregate_edge_stats_no_marker(B_list, variables)
    stable = select_stable_edges(stats)
    return {
        "variables": variables, "marker": None,
        "n_train_trajectories": n_traj, "n_bootstrap": n_bootstrap,
        "stable_edges": sorted(stable, key=lambda e: (-e["f_e"], e["from"], e["to"])),
        "all_edges": stats["edges"],
        # 无 marker：marker→j 总效应 cL、C^A、marker BFS frontier 均不可计算。
        "cL": {}, "cA": {},
        "frontier": {"layers": {1: [], 2: [], 3: []}, "reach": [], "depth_of": {}},
        "ontology_constraint": {"use_ontology": use_ontology},
    }


def learn_strict_template(manifest: Dict, vsets: Dict, baseline: Dict,
                          k: str, g: str,
                          n_bootstrap: int = LINGAM_N_BOOTSTRAP,
                          seed: int = GLOBAL_SEED) -> Dict:
    """MASKED-strict 签名：V_graph−marker 重学 LiNGAM，不学 C^A，只留组模式。"""
    v_thr = vsets["V_thr"]
    gvars = [v for v in vsets["V_graph"][k] if v != MARKERS[k]]
    learn_blocks, main_blocks, sevs = _load_blocks(manifest, k, g, gvars)
    ontology = build_ontology(k, gvars)
    sig = bootstrap_lingam_no_marker(
        learn_blocks, gvars, ontology,
        baseline["mu"], baseline["sigma"], n_bootstrap=n_bootstrap, seed=seed)
    sig["group_pattern"] = _group_pattern(main_blocks, gvars, v_thr, baseline)
    sig["learn_window"] = [LEARN_WINDOW_START, LEARN_WINDOW_END]
    sig["train_severities"] = sevs
    sig["ablation"] = "strict"
    return sig


def edge_retention(main_sig: Dict, strict_sig: Dict) -> Dict:
    """G_k′ 边保留率：strict 稳定边 vs 主签名稳定边（限制到非 marker 节点对）。"""
    marker = main_sig.get("marker")
    main_edges = {(e["from"], e["to"]) for e in main_sig.get("stable_edges", [])}
    main_nm = {e for e in main_edges if marker not in e}
    strict_edges = {(e["from"], e["to"]) for e in strict_sig.get("stable_edges", [])}
    directed_overlap = main_nm & strict_edges
    undirected_main = {frozenset(e) for e in main_nm}
    undirected_strict = {frozenset(e) for e in strict_edges}
    undirected_overlap = undirected_main & undirected_strict
    dir_agree = 0
    for pair in undirected_overlap:
        a, b = tuple(pair)
        if (a, b) in main_nm and (a, b) in strict_edges:
            dir_agree += 1
        elif (b, a) in main_nm and (b, a) in strict_edges:
            dir_agree += 1
    n_main_nm = len(main_nm)
    n_undir = len(undirected_overlap)
    return {
        "main_stable_edges_total": len(main_edges),
        "main_stable_edges_nonmarker": n_main_nm,
        "strict_stable_edges": len(strict_edges),
        "directed_overlap": len(directed_overlap),
        "undirected_overlap": n_undir,
        "direction_agree": dir_agree,
        "retention_rate_directed": (len(directed_overlap) / n_main_nm) if n_main_nm else None,
        "retention_rate_undirected": (n_undir / n_main_nm) if n_main_nm else None,
        "direction_consistency": (dir_agree / n_undir) if n_undir else None,
    }


# ==================== B. −过程本体（MAD 经验阈值） ====================
def fit_mad_baseline(baseline: Dict, v_thr: Sequence[str]) -> Dict[str, float]:
    """从 sev-1 三类恢复段池化算 MAD_j = median(|x_j − mu_j|)，mu 用 shared_baseline。

    取舍（写死并注明）：方案字面是"阈值=μ±3·MAD"，故本函数返回 raw MAD（不乘
    1.4826）；1.4826 是 MAD→σ 的标准一致性系数，仅在注释中保留作为对照。若乘
    1.4826 会放大尺度、使激活更稀疏，与"过程本体过紧"的消融方向相反，故不乘。
    """
    blocks = []
    for k in ACCIDENT_TYPES:
        for sev in BASELINE_SEVERITIES:
            X, cols, t = D.load_window(k, sev, BASELINE_WINDOW_START,
                                       BASELINE_WINDOW_END, PHYSICAL_CANDIDATES)
            assert len(t) == 152, (k, sev, len(t))
            blocks.append(X)
    pooled = np.vstack(blocks)
    pos = {v: i for i, v in enumerate(PHYSICAL_CANDIDATES)}
    mad: Dict[str, float] = {}
    for v in v_thr:
        mu = baseline["mu"][v]
        mad[v] = float(np.median(np.abs(pooled[:, pos[v]] - mu)))
    return mad


def observe_main_window_mad(block: np.ndarray, cols: Sequence[str],
                            v_thr: Sequence[str], baseline: Dict,
                            mad: Dict[str, float]) -> Dict[str, object]:
    """主窗块 -> d（σ 尺度，与主流程一致）+ A_MAD（μ±3·MAD 阈值）。

    d 不换口径：E2 余弦 / E3 符号一致仍基于 σ 标准化 z；只有激活集 A 用 MAD。
    """
    pos = {v: p for p, v in enumerate(cols)}
    mu, sigma = baseline["mu"], baseline["sigma"]
    T = block.shape[0]
    P = len(v_thr)
    Z = np.zeros((T, P))
    Z_mad = np.zeros((T, P))
    for j, v in enumerate(v_thr):
        x = block[:, pos[v]]
        Z[:, j] = (x - mu[v]) / sigma[v]
        Z_mad[:, j] = (x - mu[v]) / mad[v]
    d = signed_median_z(Z)
    rates_mad = overlimit_rates(Z_mad, ANOMALY_K)
    A = activation_set(rates_mad, v_thr, v_thr, ACTIVATION_FRAC)
    return {"d": d, "rates": rates_mad, "A": A, "Z": Z, "Z_mad": Z_mad}


def score_with_obs(obs: Dict, block: np.ndarray, cols: Sequence[str],
                   condition: str, v_thr: Sequence[str],
                   sigs: Dict[Tuple[str, str], Dict],
                   gates: Dict[Tuple[str, str], Dict[str, object]],
                   calibration: Dict,
                   epsilon: Optional[Dict[str, float]] = None) -> Dict:
    """与 pipeline.score_trajectory 同构，但观测（d/A）由调用方提供（消融换口径）。"""
    if condition not in ("FULL", "MASKED"):
        raise ValueError(f"condition 必须是 FULL/MASKED，收到 {condition}")
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


def score_trajectory_mad(block: np.ndarray, cols: Sequence[str], condition: str,
                         v_thr: Sequence[str],
                         sigs: Dict[Tuple[str, str], Dict],
                         gates: Dict[Tuple[str, str], Dict[str, object]],
                         baseline: Dict, calibration: Dict, mad: Dict[str, float],
                         epsilon: Optional[Dict[str, float]] = None) -> Dict:
    """−过程本体评分：A(x)=A_MAD，d 仍 σ 尺度，其余与主流程一致。"""
    if condition == "MASKED":
        assert_no_marker_columns(cols, MARKER_COLS)
    obs = observe_main_window_mad(block, cols, v_thr, baseline, mad)
    return score_with_obs(obs, block, cols, condition, v_thr, sigs, gates,
                          calibration, epsilon)


def score_trajectory_strict(block: np.ndarray, cols: Sequence[str], condition: str,
                            v_thr: Sequence[str],
                            sigs: Dict[Tuple[str, str], Dict],
                            gates: Dict[Tuple[str, str], Dict[str, object]],
                            baseline: Dict, calibration: Dict,
                            epsilon: Optional[Dict[str, float]] = None) -> Dict:
    """MASKED-strict 评分：E2 照常，E3′ 只诊断不融合，融合 E3=0（实际仅 E2）。"""
    if condition != "MASKED":
        raise ValueError("MASKED-strict 只支持 MASKED 条件")
    assert_no_marker_columns(cols, MARKER_COLS)
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
    # E3′ 诊断量：raw e3_base/e3_path（不归一、不融合）
    e3p_diag = {k: class_raw[k]["e3_path"] for k in ACCIDENT_TYPES}
    e3b_diag = {k: class_raw[k]["e3_base"] for k in ACCIDENT_TYPES}
    # 融合：MASKED 权重但 E3=0 → 实际仅 E2
    fusion = {k: W_MASKED["E2"] * e2_norm[k] + W_MASKED["E3"] * 0.0
              for k in ACCIDENT_TYPES}

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
        "class_norm": {"e2": e2_norm},
        "e3_component_zero": {k: 0.0 for k in ACCIDENT_TYPES},
        "e3p_diagnostic_class_raw": e3p_diag,
        "e3b_diagnostic_class_raw": e3b_diag,
        "fusion": {k: float(v) for k, v in fusion.items()},
        "ranking": [[k, float(s)] for k, s in ranking],
        "pred": pred if reason is None else None,
        "argmax_class": pred,
        "s1": float(s1), "s2": float(s2), "margin": margin,
        "accepted": reason is None,
        "abstain_reason": reason,
    }


# ==================== 指标差异 ====================
DELTA_KEYS = ["overall_accuracy", "selective_accuracy", "coverage",
              "abstention_rate", "macro_f1_mean_available",
              "n_accepted", "n_correct"]


def delta_metrics(abl: Dict, main: Dict) -> Dict:
    """消融 vs 主流程指标差值（标量口径，逐项 delta = 消融 − 主流程）。"""
    out = {}
    for key in DELTA_KEYS:
        a, b = abl.get(key), main.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            out[key] = {"ablation": float(a), "main": float(b),
                        "delta": (float(a) - float(b)) if (a is not None and b is not None) else None}
    return out

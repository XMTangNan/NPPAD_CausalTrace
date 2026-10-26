"""D3 轨迹级留一标定（方案 13.7.6/13.7.7）。

协议：
- 标定轨迹 = 9 模板全部 train_template_severities（sev-1 与 Normal 不参加）；
- 每条被留轨迹 h∈(k_h,g_h) 只重学该模板：剔除 h 后学图窗块确定性单次 DirectLiNGAM
  （fit_single_template：全块 vstack 无有放回抽样，单图边即标定签名拓扑）
  + marker_ca_column(full_sample=True) + 剔除 h 的组模式；其余 8 模板用最终签名原样；
- gCF 门控（逐边 edges[v]["gCF"] + 顶层 template_gcf）9 模板一律用最终 cf，
  禁止为标定另造门控；被重学模板同样用最终门控；
- MASKED 口径（特征仅 V_thr），E1 不参与标定；
- 归一：E2 / E3_path 的类级分 s(k)=max_g 各自按候选类跨全部标定轨迹 min-max；
- τ/δ 网格在归一化 MASKED 融合分 .5Ê2+.5Ê3path 上最大化 U（13.7.6）；
- calib_topology 诊断：LOO 单图 vs 最终签名的 (轨迹×9模板) E3_path Spearman。
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Sequence, Tuple

import numpy as np

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKERS, GLOBAL_SEED,
    MAIN_WINDOW_START, MAIN_WINDOW_END, LEARN_WINDOW_START, LEARN_WINDOW_END,
    LEARN_WINDOW_POINTS, MAIN_WINDOW_POINTS, ANOMALY_K, ACTIVATION_FRAC,
    LAMBDA1_HIGH, LAMBDA2_LOW, LAMBDA2_GRID, W_MASKED,
)
from rca.v21 import data as D
from rca.v21.knowledge import build_ontology
from rca.v21.signatures import fit_single_template
from rca.v21.marker_effects import trajectory_steady_pairs, marker_ca_column
from rca.v21.inversion import (
    signed_median_z, overlimit_rates, activation_set, build_group_pattern,
)
from rca.v21.pipeline import (
    PROTOCOL_D3, TEMPLATE_KEYS, load_cf_gate, observe_main_window,
    score_template_raw, _nanmax_or_zero, fit_minmax, apply_minmax, fuse_scores,
)
from core.utils import logger

# 预注册弃权网格（13.7.6）：τ 101 点，δ 101 点
TAU_GRID = np.round(np.arange(0.0, 1.0 + 1e-9, 0.01), 2)
DELTA_GRID = np.round(np.arange(0.0, 0.5 + 1e-9, 0.005), 3)


# ==================== 标定任务清单 ====================
def calibration_jobs(manifest: Dict) -> List[Tuple[str, str, int]]:
    """9 模板全部 train_template_severities，按类/组/sev 排序。"""
    jobs = []
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            for sev in manifest["classes"][k]["groups"][g]["train_template_severities"]:
                jobs.append((k, g, int(sev)))
    return jobs


# ==================== LOO 单模板重学 ====================
def _group_pattern_from_blocks(main_blocks: List[np.ndarray], gvars: Sequence[str],
                               v_thr: Sequence[str], baseline: Dict) -> Dict:
    """剔除 h 后组模式（复刻 run_r1 口径；不 import 其私有函数）。"""
    pos = {v: i for i, v in enumerate(gvars)}
    mu, sigma = baseline["mu"], baseline["sigma"]
    d_list, over_sets = [], []
    for X in main_blocks:
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
    return build_group_pattern(d_list, over_sets, v_thr)


def refit_template_loo(k: str, g: str, exclude_sev: int,
                       manifest: Dict, vsets: Dict, baseline: Dict,
                       scale: Dict) -> Dict:
    """学图窗块剔除 h 后单次 DirectLiNGAM 标定签名 + 单次 C^A + 剔除组模式。"""
    sevs = [s for s in manifest["classes"][k]["groups"][g]["train_template_severities"]
            if s != exclude_sev]
    gvars = vsets["V_graph"][k]
    learn_blocks, main_blocks = [], []
    for sev in sevs:
        Xl, cols_l, tl = D.load_window(k, sev, LEARN_WINDOW_START, LEARN_WINDOW_END, gvars)
        assert cols_l == gvars and len(tl) == LEARN_WINDOW_POINTS, (k, g, sev, len(tl))
        Xm, cols_m, tm = D.load_window(k, sev, MAIN_WINDOW_START, MAIN_WINDOW_END, gvars)
        assert cols_m == gvars and len(tm) == MAIN_WINDOW_POINTS, (k, g, sev, len(tm))
        learn_blocks.append(Xl)
        main_blocks.append(Xm)
    v_thr = vsets["V_thr"]
    ontology = build_ontology(k, gvars)
    sig = fit_single_template(
        learn_blocks, gvars, MARKERS[k], ontology,
        baseline["mu"], baseline["sigma"], scale["median_platform"][k],
        use_ontology=True)
    x, Y, symptoms = trajectory_steady_pairs(
        main_blocks, gvars, MARKERS[k], v_thr,
        baseline["mu"], baseline["sigma"], scale["median_platform"][k])
    sig["cA"] = marker_ca_column(x, Y, symptoms, full_sample=True)
    pat = _group_pattern_from_blocks(main_blocks, gvars, v_thr, baseline)
    sig["group_pattern"] = {
        "variables": list(v_thr),
        "template_d": pat["template_d"].tolist(),
        "template_over": pat["template_over"],
    }
    sig["train_severities_loo"] = sevs
    sig["loo_note"] = "D3 标定 LOO 单图签名（n_bootstrap=1），门控仍用最终 cf"
    return sig


# ==================== 单条留一评估 ====================
def evaluate_loo(job: Tuple[str, str, int], v_thr: Sequence[str],
                 final_sigs: Dict[Tuple[str, str], Dict],
                 gates: Dict[Tuple[str, str], Dict],
                 baseline: Dict, manifest: Dict, vsets: Dict, scale: Dict
                 ) -> Dict:
    """被留轨迹主窗 MASKED 观测 -> 9 模板 E2/E3_path/E3_base（LOO 集 + 最终集 path）。"""
    k_h, g_h, sev_h = job
    X, cols, _ = D.load_window(k_h, sev_h, MAIN_WINDOW_START, MAIN_WINDOW_END, list(v_thr))
    assert cols == list(v_thr)
    obs = observe_main_window(X, cols, v_thr, baseline)
    d, A = obs["d"], obs["A"]

    t0 = time.time()
    loo_sig = refit_template_loo(k_h, g_h, sev_h, manifest, vsets, baseline, scale)
    elapsed = time.time() - t0

    templates_loo: Dict[str, Dict[str, float]] = {}
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            sig = loo_sig if (k, g) == (k_h, g_h) else final_sigs[(k, g)]
            templates_loo[f"{k}_{g}"] = score_template_raw(d, A, v_thr, sig, gates[(k, g)])

    # 拓扑稳定性诊断：最终签名下同一观测的 E3_path（仅本模板需重算，其余 8 个相同）
    own_final = score_template_raw(d, A, v_thr, final_sigs[(k_h, g_h)],
                                   gates[(k_h, g_h)])
    path_final = {tk: templates_loo[tk]["e3_path"] for tk in TEMPLATE_KEYS}
    path_final[f"{k_h}_{g_h}"] = own_final["e3_path"]

    class_scores: Dict[str, Dict[str, float]] = {}
    for k in ACCIDENT_TYPES:
        class_scores[k] = {
            m: _nanmax_or_zero([templates_loo[f"{k}_{g}"][m] for g in GROUP_ORDER])
            for m in ("e2", "e3_path", "e3_base")
        }
    return {
        "id": f"{k_h}/{sev_h}", "class": k_h, "group": g_h, "severity": sev_h,
        "A": list(A), "n_activation": len(A),
        "empty_activation": len(A) == 0,
        "templates_loo": templates_loo,
        "path_final": path_final,
        "class_scores": class_scores,
        "elapsed_s": elapsed,
    }


# ==================== 归一化拟合 ====================
def fit_minmax_tables(records: Sequence[Dict]) -> Dict[str, Dict[str, Dict]]:
    """E2、E3_path 各按候选类 k 跨全部标定轨迹的 s(k) 拟合 min/max。"""
    tables = {}
    for metric in ("e2", "e3_path"):
        tables[metric] = {}
        for k in ACCIDENT_TYPES:
            vals = [r["class_scores"][k][metric] for r in records]
            tables[metric][k] = fit_minmax(vals)
    return tables


# ==================== U 网格（13.7.6）====================
def decision_features(records: Sequence[Dict], minmax: Dict
                      ) -> Dict[str, np.ndarray]:
    """由标定记录构造网格输入：MASKED 归一融合分 s1/s2、argmax 正确性、严重度组。"""
    s1, s2, correct, empty = [], [], [], []
    is_high, is_low, pred_arr, true_arr = [], [], [], []
    for r in records:
        e2n = {k: apply_minmax(r["class_scores"][k]["e2"], minmax["e2"][k])
               for k in ACCIDENT_TYPES}
        e3n = {k: apply_minmax(r["class_scores"][k]["e3_path"], minmax["e3_path"][k])
               for k in ACCIDENT_TYPES}
        fusion = fuse_scores("MASKED", e2n, e3n)
        ranking = sorted(fusion.items(), key=lambda kv: kv[1], reverse=True)
        pred, top1 = ranking[0]
        top2 = ranking[1][1]
        s1.append(top1)
        s2.append(top2)
        pred_arr.append(pred)
        true_arr.append(r["class"])
        correct.append(pred == r["class"])
        empty.append(bool(r["empty_activation"]))
        is_high.append(r["group"] == "high")
        is_low.append(r["group"] == "low")
    return {
        "s1": np.asarray(s1), "s2": np.asarray(s2),
        "correct": np.asarray(correct), "empty": np.asarray(empty),
        "is_high": np.asarray(is_high), "is_low": np.asarray(is_low),
        "pred": np.asarray(pred_arr), "true": np.asarray(true_arr),
    }


def utility_grid(feat: Dict[str, np.ndarray], lambda1: int, lambda2: int,
                 tau_grid: np.ndarray = TAU_GRID,
                 delta_grid: np.ndarray = DELTA_GRID) -> np.ndarray:
    """U(τ,δ)=正确弃权−错误弃权−λ1(接受且错且high)−λ2(接受且错且low)；mid 错分权重 0。"""
    n = len(feat["s1"])
    s1 = feat["s1"][:, None, None]
    s2 = feat["s2"][:, None, None]
    empty = feat["empty"][:, None, None]
    tau = tau_grid[None, :, None]
    delta = delta_grid[None, None, :]
    abstain = empty | (s1 < tau) | ((s1 - s2) < delta)
    wrong = (~feat["correct"])[:, None, None]
    high = feat["is_high"][:, None, None]
    low = feat["is_low"][:, None, None]
    correct_abstain = (abstain & wrong).sum(axis=0)
    wrong_abstain = (abstain & ~wrong).sum(axis=0)
    accepted_wrong = (~abstain) & wrong
    U = (correct_abstain.astype(float) - wrong_abstain.astype(float)
         - lambda1 * (accepted_wrong & high).sum(axis=0)
         - lambda2 * (accepted_wrong & low).sum(axis=0))
    return U


def select_thresholds(U: np.ndarray, tau_grid: np.ndarray = TAU_GRID,
                      delta_grid: np.ndarray = DELTA_GRID
                      ) -> Tuple[float, float, float]:
    """同 U 平局：先小 τ 再小 δ（升序遍历，严格更优才更新）。"""
    best = (-np.inf, 0, 0)
    for ti, t in enumerate(tau_grid):
        for di, d in enumerate(delta_grid):
            if U[ti, di] > best[0]:
                best = (float(U[ti, di]), float(t), float(d))
    return best[1], best[2], best[0]


# ==================== 拓扑稳定性诊断 ====================
def topology_spearman(records: Sequence[Dict]) -> float:
    """LOO 单图 vs 最终签名下 (轨迹×9 模板) E3_path 展平的 Spearman；nan 按 0 地板。"""
    from scipy import stats
    loo = np.array([[records[i]["templates_loo"][tk]["e3_path"] for tk in TEMPLATE_KEYS]
                    for i in range(len(records))], dtype=float)
    fin = np.array([[records[i]["path_final"][tk] for tk in TEMPLATE_KEYS]
                    for i in range(len(records))], dtype=float)
    loo = np.nan_to_num(loo, nan=0.0)
    fin = np.nan_to_num(fin, nan=0.0)
    if np.std(loo) < 1e-12 or np.std(fin) < 1e-12:
        return float("nan")
    return float(stats.spearmanr(loo.ravel(), fin.ravel()).statistic)


# ==================== 汇总落盘 ====================
def assemble_calibration(records: Sequence[Dict]) -> Dict:
    minmax = fit_minmax_tables(records)
    feat = decision_features(records, minmax)
    sensitivity = {}
    for lam2 in LAMBDA2_GRID:
        U = utility_grid(feat, LAMBDA1_HIGH, lam2)
        tau, delta, ubest = select_thresholds(U)
        sensitivity[str(lam2)] = {"lambda2": lam2, "tau": tau, "delta": delta,
                                  "U": ubest}
    primary = sensitivity[str(LAMBDA2_LOW)]
    rho = topology_spearman(records)

    flags = []
    zero_range = {"e2": [k for k in ACCIDENT_TYPES if minmax["e2"][k]["zero_range"]],
                  "e3_path": [k for k in ACCIDENT_TYPES
                              if minmax["e3_path"][k]["zero_range"]]}
    if zero_range["e2"] or zero_range["e3_path"]:
        flags.append("minmax_zero_range")
    empty_ids = [r["id"] for r in records if r["empty_activation"]]
    if empty_ids:
        flags.append("empty_activation_calibration_trajectories")
    if math.isnan(rho) or rho < 0.5:
        flags.append("calib_topology_unstable")

    return {
        "protocol": PROTOCOL_D3,
        "stage": "D3-calibration-13.7.6-13.7.7",
        "condition": "MASKED",
        "n_trajectories": len(records),
        "lambda1_high": LAMBDA1_HIGH,
        "lambda2_primary": LAMBDA2_LOW,
        "grids": {"tau": [float(x) for x in TAU_GRID],
                  "delta": [float(x) for x in DELTA_GRID]},
        "minmax": {
            "E2": {k: {"min": float(p["min"]), "max": float(p["max"]),
                       "zero_range": bool(p["zero_range"])}
                   for k, p in minmax["e2"].items()},
            "E3_path": {k: {"min": float(p["min"]), "max": float(p["max"]),
                            "zero_range": bool(p["zero_range"])}
                        for k, p in minmax["e3_path"].items()},
        },
        "tau": primary["tau"],
        "delta": primary["delta"],
        "U": primary["U"],
        "sensitivity": sensitivity,
        "calib_topology_spearman": (None if math.isnan(rho) else rho),
        "zero_range_classes": zero_range,
        "empty_activation_trajectories": empty_ids,
        "flags": flags,
        "fusion_weights": {"MASKED": W_MASKED},
    }


def json_ready(obj):
    """递归把 nan 转 None（JSON 落盘用）。"""
    if isinstance(obj, dict):
        return {k: json_ready(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_ready(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj

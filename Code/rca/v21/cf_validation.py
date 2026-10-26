"""Phase R1.5 反事实验证环（Alg3，方案 13.6，v2.1 冻结口径）。

做什么：
- 对一个 (k,g) 模板的训练轨迹稳态点（x=主窗 z_marker 中位，Y=主窗症状 z 中位，
  口径与 marker_effects.trajectory_steady_pairs 完全一致）做 K 折留出反事实重演；
- 线性引擎 L：Δpred_L = (a2−a1)·cL（冻结签名，不重学）；
- 非线性引擎 A：在全部训练稳态点上对每个症状重拟 marker 单变量 GAM
  （LinearGAM(s(0,n_splines=GAM_N_SPLINES,lam=GAM_LAM))，失败回退 OLS），
  Δpred_A = ĝ(a2)−ĝ(a1)；
- 观测：留出折按 x≤median(x)（基线≈a1）/ >median（干预≈a2）两组，
  Δobs_fj = median(Y 干预,j)−median(Y 基线,j)，跨折中位汇总；
- 门控：适用性门（dr=Q3−Q1 / 轨迹数 / 跨折组大小）、边级双侧近零 cf_inconclusive、
  边级 gCF∈{1,0.5}（cf_fail）、模板级 template_gcf∈{1,0.5}（template_cf_fail）。

失败条件（方案 13.6-6 冻结的 OR）：
  该边两引擎池化一致率 < CF_FAIL_AGREEMENT(.5)，**或**任一引擎跨可判边的
  量级 Spearman 为负（模板级信号，适用于该模板全部可判边），gCF 降到 0.5；
  两分量均在 flags 中留痕。
本环只产出签名质量门控，不改分类连续分，不做参数反演。
"""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from rca.rca_config_v21 import (
    CF_N_FOLDS, CF_MIN_GROUP_SIZE, CF_DYNAMIC_RANGE_MIN,
    CF_INCONCLUSIVE_DELTA, CF_FAIL_AGREEMENT, GCF_PASS, GCF_FAIL,
    GAM_N_SPLINES, GAM_LAM, GLOBAL_SEED,
)
from core.utils import logger

STATUS_OK = "cf_ok"
STATUS_NA = "cf_not_applicable"


# ==================== 纯函数 ====================
def gam_two_point_delta(x: np.ndarray, y: np.ndarray,
                        a1: float, a2: float) -> Tuple[float, str]:
    """全部训练稳态点上重拟 marker 单变量 GAM，直接 predict 两点差；失败回退 OLS。

    设置与 marker_effects._local_slope 同款；返回 (delta, engine)，
    engine ∈ {"GAM","OLS","flat"} 仅供审计。
    """
    from pygam import LinearGAM, s
    try:
        gam = LinearGAM(s(0, n_splines=GAM_N_SPLINES, lam=GAM_LAM)).fit(
            x.reshape(-1, 1), y)
        p1 = float(gam.predict(np.array([[a1]], dtype=float))[0])
        p2 = float(gam.predict(np.array([[a2]], dtype=float))[0])
        return p2 - p1, "GAM"
    except Exception:
        if float(np.std(x)) < 1e-12:
            return 0.0, "flat"
        coef = np.polyfit(x, y, 1)
        return float(coef[0] * (a2 - a1)), "OLS"


def _na_result(reason: str, n: int, dr: float, symptoms: Sequence[str],
               group_sizes: Optional[Dict[str, int]] = None) -> Dict:
    """cf_not_applicable：不落逐边判据，gCF 全 1，仅记录 dr/n（及组大小审计）。"""
    return {
        "status": STATUS_NA,
        "reason": reason,
        "n": int(n),
        "dr": float(dr),
        "group_sizes": group_sizes,
        "edges": {v: {"gCF": GCF_PASS, "flags": ["cf_not_applicable"]}
                  for v in symptoms},
        "template_gcf": GCF_PASS,
        "flags": ["cf_not_applicable"],
    }


def _sign_agree(pred: float, obs: float) -> bool:
    return (pred > 0.0 and obs > 0.0) or (pred < 0.0 and obs < 0.0)


def counterfactual_validation(x: Sequence[float], Y: np.ndarray,
                              symptoms: Sequence[str],
                              cL: Mapping[str, float],
                              n_folds: int = CF_N_FOLDS,
                              seed: int = GLOBAL_SEED) -> Dict:
    """单模板 Alg3 反事实环（纯数据接口，便于合成数据单测）。

    参数
    ----
    x : (n,) 训练轨迹主窗 z_marker 中位（trajectory_steady_pairs 的 x）。
    Y : (n, J) 训练轨迹主窗症状 z 中位（列对齐 symptoms）。
    symptoms : V_thr 症状变量序。
    cL : 冻结签名线性效应 {var: cL[var]['cL']}，不重学。

    返回 dict（落盘结构）；status ∈ {cf_ok, cf_not_applicable}。
    """
    xa = np.asarray(x, dtype=float)
    Ya = np.asarray(Y, dtype=float)
    n = len(xa)
    symptoms = list(symptoms)
    J = len(symptoms)
    assert Ya.shape == (n, J), f"Y 形状 {Ya.shape} 与 x/symptoms 不匹配"

    # ---- 适用性门 1：动态范围 dr=Q3−Q1（与 a1/a2 同口径）----
    q1, q3 = float(np.quantile(xa, 0.25)), float(np.quantile(xa, 0.75))
    dr = q3 - q1
    if dr < CF_DYNAMIC_RANGE_MIN:
        logger.info(f"[Alg3] cf_not_applicable(dynamic_range): n={n} dr={dr:.4f}")
        return _na_result("dynamic_range", n, dr, symptoms)

    # ---- 适用性门 2：轨迹数 >= 2*CF_MIN_GROUP_SIZE ----
    if n < 2 * CF_MIN_GROUP_SIZE:
        logger.info(f"[Alg3] cf_not_applicable(n_trajectories): n={n} dr={dr:.4f}")
        return _na_result("n_trajectories", n, dr, symptoms)

    a1, a2 = q1, q3
    med_x = float(np.median(xa))

    # ---- 5 折确定性划分：rng.permutation 后均分 ----
    rng = np.random.default_rng(seed)
    perm = rng.permutation(n)
    folds = [np.array(f, dtype=int) for f in np.array_split(perm, n_folds)]

    # ---- 适用性门 3：跨折汇总两组各 >= CF_MIN_GROUP_SIZE ----
    base_mask_all = xa <= med_x
    n_base_all = int(base_mask_all.sum())
    n_int_all = int((~base_mask_all).sum())
    if min(n_base_all, n_int_all) < CF_MIN_GROUP_SIZE:
        group_sizes = {"baseline": n_base_all, "intervention": n_int_all}
        logger.info(f"[Alg3] cf_not_applicable(group_size): {group_sizes}")
        return _na_result("group_size", n, dr, symptoms, group_sizes)

    # ---- 逐折观测 Δobs_fj（某折某组为空则该折该 j 跳过）----
    fold_deltas: List[List[Optional[float]]] = [[] for _ in range(J)]
    fold_info = []
    for f_idx, fold in enumerate(folds):
        bm = xa[fold] <= med_x
        base_idx, int_idx = fold[bm], fold[~bm]
        fold_info.append({"fold": f_idx, "n_holdout": int(len(fold)),
                          "n_baseline": int(len(base_idx)),
                          "n_intervention": int(len(int_idx)),
                          "skipped": bool(len(base_idx) == 0 or len(int_idx) == 0)})
        for j in range(J):
            if len(base_idx) == 0 or len(int_idx) == 0:
                fold_deltas[j].append(None)
            else:
                d = float(np.median(Ya[int_idx, j]) - np.median(Ya[base_idx, j]))
                fold_deltas[j].append(d)

    # ---- 逐 j：预测 + 汇总 + 引擎级一致率 ----
    edges: Dict[str, Dict] = {}
    for j, v in enumerate(symptoms):
        deltas = np.array([np.nan if d is None else d for d in fold_deltas[j]],
                          dtype=float)
        ok_mask = ~np.isnan(deltas)
        delta_obs = float(np.median(deltas[ok_mask])) if ok_mask.any() else np.nan

        delta_pred_L = float((a2 - a1) * float(cL[v]))
        delta_pred_A, fit_A = gam_two_point_delta(xa, Ya[:, j], a1, a2)

        inconc_L = (abs(delta_obs) < CF_INCONCLUSIVE_DELTA
                    and abs(delta_pred_L) < CF_INCONCLUSIVE_DELTA)
        inconc_A = (abs(delta_obs) < CF_INCONCLUSIVE_DELTA
                    and abs(delta_pred_A) < CF_INCONCLUSIVE_DELTA)

        # Δobs_f=0 的折跳过；inconclusive 引擎不进分母
        comp = ok_mask & (deltas != 0.0)

        def _engine_rate(pred: float, inconc: bool) -> Tuple[Optional[float], int, int]:
            if inconc:
                return None, 0, 0
            idx = np.where(comp)[0]
            n_agree = int(sum(_sign_agree(pred, float(deltas[i])) for i in idx))
            n_comp = int(len(idx))
            if n_comp == 0:
                return None, 0, 0
            return n_agree / n_comp, n_agree, n_comp

        agree_L, n_ag_L, n_cp_L = _engine_rate(delta_pred_L, inconc_L)
        agree_A, n_ag_A, n_cp_A = _engine_rate(delta_pred_A, inconc_A)
        n_ag_all, n_cp_all = n_ag_L + n_ag_A, n_cp_L + n_cp_A
        agree_all = (n_ag_all / n_cp_all) if n_cp_all > 0 else None

        flags: List[str] = []
        if inconc_L:
            flags.append("cf_inconclusive_L")
        if inconc_A:
            flags.append("cf_inconclusive_A")
        if inconc_L and inconc_A:
            flags.append("cf_inconclusive")
        if agree_all is None and "cf_inconclusive" not in flags:
            flags.append("cf_no_comparable_fold")

        edges[v] = {
            "delta_obs": (None if np.isnan(delta_obs) else float(delta_obs)),
            "delta_pred_L": float(delta_pred_L),
            "delta_pred_A": float(delta_pred_A),
            "agree_L": agree_L,
            "agree_A": agree_A,
            "agree": agree_all,
            "n_agree_L": n_ag_L, "n_comparable_L": n_cp_L,
            "n_agree_A": n_ag_A, "n_comparable_A": n_cp_A,
            "inconclusive_L": bool(inconc_L),
            "inconclusive_A": bool(inconc_A),
            "fit_A": fit_A,
            "fold_delta_obs": [None if d is None else float(d) for d in fold_deltas[j]],
            "gCF": GCF_PASS,
            "flags": flags,
        }

    # ---- 量级 Spearman：逐引擎，Δpred 向量 vs Δobs 向量，仅可判边，n>=3 ----
    spearman: Dict[str, Optional[Dict]] = {"L": None, "A": None}
    rho: Dict[str, Optional[float]] = {"L": None, "A": None}
    for eng, dkey, akey in (("L", "delta_pred_L", "agree_L"),
                            ("A", "delta_pred_A", "agree_A")):
        pv, ov = [], []
        for v in symptoms:
            e = edges[v]
            if e[akey] is not None and e["delta_obs"] is not None:
                pv.append(e[dkey])
                ov.append(e["delta_obs"])
        if len(pv) >= 3:
            from scipy.stats import spearmanr
            rho_v, p_v = spearmanr(np.array(pv), np.array(ov))
            if rho_v is not None and not np.isnan(rho_v):
                spearman[eng] = {"rho": float(rho_v), "p": float(p_v),
                                 "n": int(len(pv))}
                rho[eng] = float(rho_v)

    rho_neg_any = (rho["L"] is not None and rho["L"] < 0.0) or \
                  (rho["A"] is not None and rho["A"] < 0.0)

    # ---- 边级 gCF（方案 13.6-6 冻结的 OR：合并一致率<.5 或 任一引擎量级 Spearman 为负）----
    n_fail = n_inconc = 0
    tot_agree, tot_comp = 0, 0
    n_judgeable = 0
    for v in symptoms:
        e = edges[v]
        if e["agree"] is not None:
            n_judgeable += 1
            tot_agree += e["n_agree_L"] + e["n_agree_A"]
            tot_comp += e["n_comparable_L"] + e["n_comparable_A"]
            low = e["agree"] < CF_FAIL_AGREEMENT
            if low:
                e["flags"].append("cf_low_agreement")
            if rho_neg_any:
                e["flags"].append("cf_template_rho_negative")
            if low or rho_neg_any:
                e["gCF"] = GCF_FAIL
                e["flags"].append("cf_fail")
                n_fail += 1
        else:
            if "cf_inconclusive" in e["flags"]:
                n_inconc += 1

    template_agree = (tot_agree / tot_comp) if tot_comp > 0 else None
    template_fail = template_agree is not None and template_agree < CF_FAIL_AGREEMENT
    template_gcf = GCF_FAIL if template_fail else GCF_PASS
    template_flags = ["template_cf_fail"] if template_fail else []

    result = {
        "status": STATUS_OK,
        "reason": None,
        "n": int(n),
        "dr": float(dr),
        "a1": float(a1),
        "a2": float(a2),
        "median_x": med_x,
        "n_folds": int(n_folds),
        "fold_info": fold_info,
        "group_sizes": {"baseline": n_base_all, "intervention": n_int_all},
        "seed": int(seed),
        "thresholds": {
            "dynamic_range_min": CF_DYNAMIC_RANGE_MIN,
            "min_group_size": CF_MIN_GROUP_SIZE,
            "inconclusive_delta": CF_INCONCLUSIVE_DELTA,
            "fail_agreement": CF_FAIL_AGREEMENT,
        },
        "edge_order": symptoms,
        "edges": edges,
        "spearman": spearman,
        "template": {
            "n_judgeable_edges": int(n_judgeable),
            "n_comparable": int(tot_comp),
            "n_agree": int(tot_agree),
            "agree": template_agree,
            "gcf": float(template_gcf),
            "flags": template_flags,
        },
        "template_gcf": float(template_gcf),
        "flags": template_flags,
        "summary": {
            "n_edges": J,
            "n_judgeable_edges": int(n_judgeable),
            "n_fail_edges": int(n_fail),
            "n_inconclusive_edges": int(n_inconc),
            "rho_negative_L": rho["L"] is not None and rho["L"] < 0.0,
            "rho_negative_A": rho["A"] is not None and rho["A"] < 0.0,
        },
    }
    return result

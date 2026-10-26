"""D0 决策门控执行器（13.8.1，G6 门）：MASKED-高严重度 18 条最小正确实验。

- E3_base 双引擎通道（LiNGAM cL + 轨迹级 GAM C^A 等权；RCA-031 修正后）；
  无 R1.5 门控（gCF 全 1）；
- 判据统计量 = 纯 E3_base 通道 argmax 正确性（不用融合分；融合分仅附属）；
- 共同子集：A(x)!=empty；逐轨迹 delta_i，B=2000 轨迹 Bootstrap 95% CI；
- E3_path 同次学图顺带计算落盘但不进判据；
- 三级分支写死主张句 -> d0_decision.json，D0 后不得再改 E3 主证据。
用法：cd Code && PYTHONPATH=. python -m rca.v21.run_d0
"""
from __future__ import annotations

import json
import numpy as np

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, MARKER_COLS, MARKERS, GROUP_ORDER, D0_GROUP,
    D0_BOOTSTRAP_B, D0_SEED, D0_CI_LOW, D0_CI_HIGH, V21_OUTPUT,
    MAIN_WINDOW_START, MAIN_WINDOW_END, ANOMALY_K, ACTIVATION_FRAC,
)
from rca.v21 import data as D
from rca.v21.signatures import load_signature
from rca.v21.marker_effects import effective_marker_map
from rca.v21.inversion import (
    assert_no_marker_columns, signed_median_z, overlimit_rates,
    activation_set, e3_base_channel, e3_path_channel, e2_score, pearson_corr,
)
from core.utils import logger


def _dual_channel(sig, d, gvars, A):
    """E3-base = 0.5(LiNGAM cL 通道 + GAM cA 通道)，nan 通道自动忽略；path 用有效效应图。"""
    cLm = {j: info["cL"] for j, info in sig["cL"].items()}
    piLm = {j: info["piL"] for j, info in sig["cL"].items()}
    eL = e3_base_channel(d, gvars, A, cLm, piLm)
    if "cA" in sig:
        cAm = {j: info["cA"] for j, info in sig["cA"].items()}
        piAm = {j: info["piA"] for j, info in sig["cA"].items()}
        eA = e3_base_channel(d, gvars, A, cAm, piAm)
    else:
        eA, cAm, piAm = float("nan"), {}, {}
    chans = [x for x in (eL, eA) if not np.isnan(x)]
    e_base = float(np.mean(chans)) if chans else float("nan")
    eff = effective_marker_map(sig.get("cA", {}), sig["cL"])
    ce = {j: info["c"] for j, info in eff.items()}
    pie = {j: info["pi"] for j, info in eff.items()}
    ep = e3_path_channel(d, gvars, A, ce, pie, sig["frontier"])
    return e_base, ep, eL, eA


def _d_aligned(block, cols, gvars, baseline):
    """返回对齐 gvars 的 d、rates、越限 z 矩阵；marker 位（MASKED 不存在）置 0。"""
    pos = {v: p for p, v in enumerate(cols)}
    mu, sigma = baseline["mu"], baseline["sigma"]
    T = block.shape[0]
    Z = np.zeros((T, len(gvars)))
    for j, v in enumerate(gvars):
        if v in pos and v in mu:
            Z[:, j] = (block[:, pos[v]] - mu[v]) / sigma[v]
        # marker / 无基线变量保持 0
    return signed_median_z(Z), overlimit_rates(Z, ANOMALY_K), Z


def main():
    manifest = D.load_json("split_manifest.json")
    baseline = D.load_json("shared_baseline.json")
    vsets = D.load_json("variable_sets.json")
    v_thr = vsets["V_thr"]
    sigs = {(k, g): load_signature(k, g) for k in ACCIDENT_TYPES for g in GROUP_ORDER}
    missing = [f"{k}_{g}" for (k, g), s in sigs.items() if s is None]
    if missing:
        raise RuntimeError(f"R1 签名缺失: {missing}，先完成 run_r1")

    rows = []
    for k_true in ACCIDENT_TYPES:
        gi = manifest["classes"][k_true]["groups"][D0_GROUP]
        for sev in gi["test_severities"]:
            # MASKED 特征列：V_thr + 安注（显式不含 marker），并跑零访问断言
            feat_cols = list(v_thr)
            X, cols, _ = D.load_window(k_true, sev, MAIN_WINDOW_START, MAIN_WINDOW_END, feat_cols)
            assert cols == feat_cols
            assert_no_marker_columns(cols, MARKER_COLS)  # G4 运行时断言

            per_template = {}
            best = {"base": (-np.inf, None), "path": (-np.inf, None),
                    "b1": (-np.inf, None), "fusion": (-np.inf, None)}
            for (k, g), sig in sigs.items():
                gvars = sig["variables"]
                d, rates, Z = _d_aligned(X, cols, gvars, baseline)
                A = activation_set(rates, gvars, v_thr, ACTIVATION_FRAC)
                eb, ep, eL, eA = _dual_channel(sig, d, gvars, A)
                vthr_pos = {v: i for i, v in enumerate(gvars) if v in set(v_thr)}
                d_vthr = np.array([d[vthr_pos[v]] for v in v_thr])
                td = np.array(sig["group_pattern"]["template_d"])
                b1 = pearson_corr(d_vthr, td)
                e2 = e2_score(d_vthr, A, v_thr, td, sig["group_pattern"]["template_over"])
                # MASKED 融合（附属）：0.5 E2 + 0.5 E3_base（E1 不可用）
                fusion = 0.5 * e2 + 0.5 * (eb if not np.isnan(eb) else -1.0)
                per_template[f"{k}_{g}"] = {
                    "A": A, "e3_base": None if np.isnan(eb) else eb,
                    "e3_L": None if np.isnan(eL) else eL,
                    "e3_A": None if np.isnan(eA) else eA,
                    "e3_path": None if np.isnan(ep["score"]) else ep["score"],
                    "b1": b1, "e2": e2, "fusion": fusion}
                for key, val in (("base", eb), ("path", ep["score"]),
                                 ("b1", b1), ("fusion", fusion)):
                    if not np.isnan(val) and val > best[key][0]:
                        best[key] = (val, k)
            rows.append({"true": k_true, "sev": sev,
                         "A_empty": len(per_template[f'{k_true}_{D0_GROUP}']["A"]) == 0,
                         "pred_base": best["base"][1], "pred_path": best["path"][1],
                         "pred_b1": best["b1"][1], "pred_fusion": best["fusion"][1],
                         "per_template": per_template})

    paired = [r for r in rows if not r["A_empty"]]
    n_all, n_pair = len(rows), len(paired)
    for r in rows:
        r["correct_base"] = int(r["pred_base"] == r["true"])
        r["correct_path"] = int(r["pred_path"] == r["true"])
        r["correct_b1"] = int(r["pred_b1"] == r["true"])
        r["correct_fusion"] = int(r["pred_fusion"] == r["true"])
        r["delta"] = r["correct_base"] - r["correct_b1"]
    delta = np.array([r["delta"] for r in paired], dtype=float)
    mean_delta = float(delta.mean()) if n_pair else float("nan")
    rng = np.random.default_rng(D0_SEED)
    if n_pair:
        means = np.array([delta[rng.integers(0, n_pair, n_pair)].mean()
                          for _ in range(D0_BOOTSTRAP_B)])
        ci = [float(np.quantile(means, D0_CI_LOW)),
              float(np.quantile(means, D0_CI_HIGH))]
    else:
        ci = [float("nan"), float("nan")]

    acc_base = float(np.mean([r["correct_base"] for r in paired])) if n_pair else float("nan")
    acc_path = float(np.mean([r["correct_path"] for r in paired])) if n_pair else float("nan")
    acc_b1 = float(np.mean([r["correct_b1"] for r in paired])) if n_pair else float("nan")
    acc_fusion = float(np.mean([r["correct_fusion"] for r in paired])) if n_pair else float("nan")

    # 三级预注册分支
    if mean_delta > 0 and ci[0] > 0:
        branch, claim = "strong", (
            "D0=强证据：MASKED-高严重度共同子集上纯 E3_base 通道相对 Pearson B1 的配对正确性差值"
            f" Δ̄={mean_delta:.3f}，95%CI=[{ci[0]:.3f},{ci[1]:.3f}] 下界>0；E3_base 为融合与主张句主证据，"
            "E3_path 列 P2 机制增强。")
    elif mean_delta > 0:
        branch, claim = "directional", (
            "D0=方向性证据（CI 跨 0）：按预注册保守规则 E3_path 升为主证据，E3_base 降为消融对照；"
            f" Δ̄={mean_delta:.3f}，95%CI=[{ci[0]:.3f},{ci[1]:.3f}]，结论带‘方向支持但证据不足’限定。")
    else:
        branch, claim = "none", (
            "D0=无增量证据：纯 E3_base 通道不优于 Pearson B1"
            f"（Δ̄={mean_delta:.3f}，CI=[{ci[0]:.3f},{ci[1]:.3f}]）；E3_path 为主证据，"
            "不得声称因果识别增量，按 13.1 结论句报告。")

    out = {
        "gate": "D0", "group": D0_GROUP, "condition": "MASKED",
        "engine": "E3_base=0.5(E3_L+E3_A)：LiNGAM 100 次 cL + 轨迹级 GAM 50 次 cA（RCA-031）；gCF=1（R1.5 在 D2）",
        "n_total": n_all, "n_paired_subset": n_pair,
        "empty_activation_sev": [f"{r['true']}/{r['sev']}" for r in rows if r["A_empty"]],
        "accuracy_paired_subset": {"e3_base": acc_base, "e3_path": acc_path,
                                   "b1_pearson": acc_b1, "fusion_aux": acc_fusion},
        "delta_mean": mean_delta, "delta_ci95": ci,
        "bootstrap_B": D0_BOOTSTRAP_B, "seed": D0_SEED,
        "branch": branch,
        "primary_e3": "E3_base" if branch == "strong" else "E3_path",
        "frozen_claim": claim,
        "per_trajectory": rows,
    }
    path = D.save_json(out, "d0_decision.json")
    logger.info(f"n_pair={n_pair}/{n_all} acc(base)={acc_base:.3f} acc(path)={acc_path:.3f} "
                f"acc(B1)={acc_b1:.3f} acc(fusion_aux)={acc_fusion:.3f}")
    logger.info(f"Δ̄={mean_delta:.3f} CI={ci} -> branch={branch} primary={out['primary_e3']}")
    logger.info(f"frozen claim: {claim}")
    logger.info(f"saved -> {path}")


if __name__ == "__main__":
    main()

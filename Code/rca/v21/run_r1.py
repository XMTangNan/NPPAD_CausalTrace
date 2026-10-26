"""D1 / R1 执行器：9 模板（3 类 x 3 严重度组）轨迹级 Bootstrap LiNGAM 正演签名。

断点续跑：已存在的 signature_<k>_<g>.json 默认跳过（--force 重算）。
冒烟：python -m rca.v21.run_r1 --smoke（每模板 3 次，仅 LOCA-low）
用法：cd Code && PYTHONPATH=. python -m rca.v21.run_r1
"""
from __future__ import annotations

import argparse
import time
import numpy as np

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKERS, LINGAM_N_BOOTSTRAP, GLOBAL_SEED,
    MAIN_WINDOW_START, MAIN_WINDOW_END, LEARN_WINDOW_START, LEARN_WINDOW_END,
    LEARN_WINDOW_POINTS, MAIN_WINDOW_POINTS, ANOMALY_K, ACTIVATION_FRAC,
    GAM_MARKER_N_BOOTSTRAP,
)
from rca.v21 import data as D
from rca.v21.knowledge import build_ontology
from rca.v21.signatures import (
    bootstrap_lingam_template, save_signature, load_signature,
    annotate_severity_flip,
)
from rca.v21.marker_effects import trajectory_steady_pairs, marker_ca_column
from rca.v21.inversion import signed_median_z, overlimit_rates, activation_set, build_group_pattern
from core.utils import logger


def _load_template_inputs(manifest, vsets, k, g):
    """返回 (graph 变量序, 学图块[60,1500], 主窗块[300,1500], sev 列表)。"""
    sevs = manifest["classes"][k]["groups"][g]["train_template_severities"]
    gvars = vsets["V_graph"][k]
    learn_blocks, main_blocks = [], []
    for sev in sevs:
        Xl, cols, tl = D.load_window(k, sev, LEARN_WINDOW_START, LEARN_WINDOW_END, gvars)
        assert cols == gvars and len(tl) == LEARN_WINDOW_POINTS, (k, g, sev, len(tl))
        Xm, cols2, tm = D.load_window(k, sev, MAIN_WINDOW_START, MAIN_WINDOW_END, gvars)
        assert cols2 == gvars and len(tm) == MAIN_WINDOW_POINTS, (k, g, sev, len(tm))
        learn_blocks.append(Xl)
        main_blocks.append(Xm)
    return gvars, learn_blocks, main_blocks, sevs


def _group_pattern(blocks, gvars, v_thr, baseline):
    """训练侧组模式：模板变量序上的中位 z（V_thr 子集）+ 组越限集。"""
    pos = {v: i for i, v in enumerate(gvars)}
    mu, sigma = baseline["mu"], baseline["sigma"]
    d_list, over_sets = [], []
    d_full = []  # 对齐 gvars 的 d（marker/安注位不用，置 0 方便对齐存盘）
    for X in blocks:
        Z = np.zeros((X.shape[0], len(gvars)))
        for p, v in enumerate(gvars):
            if v in mu:
                Z[:, p] = (X[:, p] - mu[v]) / sigma[v]
        d = signed_median_z(Z)
        rates = overlimit_rates(Z, ANOMALY_K)
        # E2 只用 V_thr 对齐
        d_vthr = np.array([d[pos[v]] for v in v_thr])
        A_t = activation_set(rates, gvars, v_thr, ACTIVATION_FRAC)
        d_list.append(d_vthr)
        over_sets.append(A_t)
        d_full.append(d)
    pat = build_group_pattern(d_list, over_sets, v_thr)
    return pat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-ontology", action="store_true")
    args = ap.parse_args()

    manifest = D.load_json("split_manifest.json")
    baseline = D.load_json("shared_baseline.json")
    vsets = D.load_json("variable_sets.json")
    scale = D.load_json("marker_scale.json")
    v_thr = vsets["V_thr"]

    jobs = [(k, g) for k in ACCIDENT_TYPES for g in GROUP_ORDER]
    n_boot = 3 if args.smoke else LINGAM_N_BOOTSTRAP
    if args.smoke:
        jobs = [("LOCA", "low")]
    timings = []

    for k, g in jobs:
        if not args.force and load_signature(k, g) is not None:
            logger.info(f"[skip] signature_{k}_{g} 已存在（--force 重算）")
            continue
        t0 = time.time()
        gvars, learn_blocks, main_blocks, sevs = _load_template_inputs(manifest, vsets, k, g)
        ontology = build_ontology(k, gvars)
        sig = bootstrap_lingam_template(
            learn_blocks, gvars, MARKERS[k], ontology,
            baseline["mu"], baseline["sigma"], scale["median_platform"][k],
            n_bootstrap=n_boot, seed=GLOBAL_SEED, use_ontology=not args.no_ontology)
        pat = _group_pattern(main_blocks, gvars, v_thr, baseline)
        # C^A：轨迹级稳态 marker→症状（主窗），50 次轨迹 Bootstrap
        n_ca = min(GAM_MARKER_N_BOOTSTRAP, n_boot)
        x, Y, symptoms = trajectory_steady_pairs(
            main_blocks, gvars, MARKERS[k], v_thr,
            baseline["mu"], baseline["sigma"], scale["median_platform"][k])
        cA = marker_ca_column(x, Y, symptoms, n_bootstrap=n_ca, seed=GLOBAL_SEED)
        n_est = sum(1 for v in cA.values() if v["estimable"] and v["piA"] >= 0.7)
        sig["group_pattern"] = {"variables": v_thr,
                                "template_d": pat["template_d"].tolist(),
                                "template_over": pat["template_over"]}
        sig["cA"] = cA
        sig["learn_window"] = [LEARN_WINDOW_START, LEARN_WINDOW_END]
        sig["train_severities"] = sevs
        save_signature(sig, k, g)
        dt = time.time() - t0
        timings.append((f"{k}_{g}", len(learn_blocks), len(sig["stable_edges"]), dt))
        logger.info(f"[done] {k}/{g}: n_traj={len(learn_blocks)} stable_edges="
                    f"{len(sig['stable_edges'])} cA_pi>=.7={n_est}/{len(v_thr)} removed/fit="
                    f"{sig['ontology_constraint']['mean_removed_per_fit']:.2f} {dt:.1f}s")

    if not args.smoke:
        # 跨组翻转标注（辨识性证据）
        for k in ACCIDENT_TYPES:
            sigs = {g: load_signature(k, g) for g in GROUP_ORDER}
            if all(s is not None for s in sigs.values()):
                flips = annotate_severity_flip(sigs)
                for g in GROUP_ORDER:
                    save_signature(sigs[g], k, g)
                logger.info(f"{k} severity-flipped marker edges: {flips}")
    if timings:
        logger.info("timing (template,n_traj,stable,s): %s", timings)
        if not args.smoke:
            total = sum(t[3] for t in timings)
            logger.info(f"R1 实跑合计 {total/60:.1f} min（9 模板含已跳过则更少）")


if __name__ == "__main__":
    main()

"""v2.1 §13.11 三类神经符号/结构消融执行器。

统一输出 output/rca_v21/ablation_symbolic_metrics.json：
  {"-ontology": ..., "-process_ontology": ..., "-strict": ...}

- −本体约束 / −过程本体：60 条测试轨迹 FULL/MASKED 两条件评分，报告指标与相对
  主流程 metrics_summary.json ALL 行的差值（不单独做传播链）；
- MASKED-strict：仅 MASKED，E2 分类 + E3′ 诊断（不融合），报告 E2 单独结果、
  G_k′ 边保留率、E3′ 分布；明确"结构消融，不与 full 比准确率"；
- 标定复用主流程 calibration_params.json，cf 门控复用主签名上的 cf_validation
  （cf 门控是主签名上算的，消融只换图/换 A(x) 口径）。

用法（cd Code）：
  PYTHONPATH=. python -m rca.v21.run_ablation_symbolic            # 全量（断点续跑）
  PYTHONPATH=. python -m rca.v21.run_ablation_symbolic --force    # 忽略已学签名重学
  PYTHONPATH=. python -m rca.v21.run_ablation_symbolic --smoke    # 仅 LOCA_low 学图计时
"""
from __future__ import annotations

import argparse
import functools
import math
import time
from typing import Dict, List, Sequence, Tuple

import numpy as np

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKERS, MARKER_COLS, V21_OUTPUT, CF_DIR,
    MAIN_WINDOW_START, MAIN_WINDOW_END, LINGAM_N_BOOTSTRAP, GLOBAL_SEED,
)
from rca.v21 import data as D
from rca.v21.signatures import load_signature, save_signature
from rca.v21.inversion import assert_no_marker_columns
from rca.v21.pipeline import (
    PROTOCOL_D3, TEMPLATE_KEYS, load_cf_gate, score_trajectory, compute_metrics,
)
from rca.v21.ablation_symbolic import (
    NO_ONTOLOGY_SIGNATURE_DIR, STRICT_SIGNATURE_DIR,
    learn_no_ontology_template, learn_strict_template,
    fit_mad_baseline, score_trajectory_mad, score_trajectory_strict,
    edge_retention, delta_metrics,
)
from core.utils import logger

CONDITIONS = ["FULL", "MASKED"]
METRICS_NAME = "ablation_symbolic_metrics.json"


def _load_inputs():
    manifest = D.load_json("split_manifest.json")
    baseline = D.load_json("shared_baseline.json")
    vsets = D.load_json("variable_sets.json")
    scale = D.load_json("marker_scale.json")
    calibration = D.load_json("calibration_params.json")
    main_sigs, gates = {}, {}
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            sig = load_signature(k, g)
            if sig is None:
                raise RuntimeError(f"主签名缺失 {k}_{g}，先完成 run_r1")
            main_sigs[(k, g)] = sig
            cf = D.load_json(f"cf_{k}_{g}.json", out_dir=CF_DIR)
            gates[(k, g)] = load_cf_gate(cf)
    return manifest, baseline, vsets, scale, calibration, main_sigs, gates


def _load_ablation_sigs(out_dir):
    sigs = {}
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            sig = load_signature(k, g, out_dir=out_dir)
            if sig is None:
                raise RuntimeError(f"消融签名缺失 {k}_{g}（{out_dir}），先学图")
            sigs[(k, g)] = sig
    return sigs


def _learn_templates(manifest, vsets, baseline, scale, kind, force, n_boot,
                     seed, jobs):
    """kind: 'no_ontology' | 'strict'；返回 (签名表, timings)。断点续跑。"""
    out_dir = NO_ONTOLOGY_SIGNATURE_DIR if kind == "no_ontology" else STRICT_SIGNATURE_DIR
    sigs, timings = {}, []
    for k, g in jobs:
        if not force and load_signature(k, g, out_dir=out_dir) is not None:
            logger.info(f"[{kind}] skip signature_{k}_{g} 已存在（--force 重算）")
            continue
        t0 = time.time()
        if kind == "no_ontology":
            sig = learn_no_ontology_template(manifest, vsets, baseline, scale,
                                             k, g, n_bootstrap=n_boot, seed=seed)
        else:
            sig = learn_strict_template(manifest, vsets, baseline, k, g,
                                        n_bootstrap=n_boot, seed=seed)
        save_signature(sig, k, g, out_dir=out_dir)
        dt = time.time() - t0
        timings.append((f"{k}_{g}", len(sig.get("stable_edges", [])), dt))
        logger.info(f"[{kind}] {k}/{g}: stable_edges={len(sig.get('stable_edges', []))} "
                    f"{dt:.1f}s")
    for k, g in jobs:
        sig = load_signature(k, g, out_dir=out_dir)
        if sig is None:
            raise RuntimeError(f"{kind} 签名缺失 {k}_{g}")
        sigs[(k, g)] = sig
    return sigs, timings


def _evaluate(manifest, vsets, scale, baseline, calibration, sigs, gates,
              score_fn, conditions):
    """对 60 条测试轨迹评分，返回 (group_rows, records)。"""
    v_thr = vsets["V_thr"]
    epsilon = scale["epsilon"]
    group_rows = {g: {c: [] for c in conditions} for g in GROUP_ORDER}
    records = {c: [] for c in conditions}
    for g in GROUP_ORDER:
        for k_true in ACCIDENT_TYPES:
            sevs = manifest["classes"][k_true]["groups"][g]["test_severities"]
            for sev in sevs:
                for cond in conditions:
                    feat_cols = (list(v_thr) if cond == "MASKED"
                                 else list(v_thr) + list(MARKER_COLS))
                    X, cols, _ = D.load_window(k_true, sev,
                                               MAIN_WINDOW_START, MAIN_WINDOW_END,
                                               feat_cols)
                    assert cols == feat_cols
                    if cond == "MASKED":
                        assert_no_marker_columns(cols, MARKER_COLS)
                    res = score_fn(X, cols, cond, v_thr, sigs, gates, baseline,
                                   calibration, epsilon=epsilon)
                    row = {"true": k_true, "sev": sev,
                           "pred": res["argmax_class"] if res["accepted"] else "ABSTAIN",
                           "accepted": bool(res["accepted"]),
                           "s1": res["s1"], "s2": res["s2"], "margin": res["margin"],
                           "n_activation": res["n_activation"]}
                    group_rows[g][cond].append(row)
                    records[cond].append(res)
    return group_rows, records


def _metrics_summary(group_rows, conditions):
    summary = {"groups": {}, "ALL": {}}
    all_rows = {c: [] for c in conditions}
    for g in GROUP_ORDER:
        summary["groups"][g] = {}
        for c in conditions:
            rows = group_rows[g][c]
            all_rows[c].extend(rows)
            summary["groups"][g][c] = compute_metrics(rows)
    for c in conditions:
        summary["ALL"][c] = compute_metrics(all_rows[c])
    return summary


def _json_ready(obj):
    if isinstance(obj, dict):
        return {k: _json_ready(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_ready(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


def _e3p_distribution(records):
    """strict E3′ 诊断分布：per-template raw e3_path 展平的统计（含 nan 占比）。"""
    vals = []
    for rec in records:
        for tk in TEMPLATE_KEYS:
            vals.append(rec["per_template"][tk]["e3_path"])
    arr = np.asarray([float(v) for v in vals], dtype=float)
    finite = arr[~np.isnan(arr)]
    return {
        "n_total": int(arr.size),
        "n_nan": int(np.isnan(arr).sum()),
        "n_finite": int(finite.size),
        "mean": float(finite.mean()) if finite.size else None,
        "median": float(np.median(finite)) if finite.size else None,
        "min": float(finite.min()) if finite.size else None,
        "max": float(finite.max()) if finite.size else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="仅 LOCA_low 学图计时（no_ontology + strict），不评分不落盘")
    ap.add_argument("--force", action="store_true", help="忽略已学签名重学")
    args = ap.parse_args()

    manifest, baseline, vsets, scale, calibration, main_sigs, gates = _load_inputs()
    v_thr = vsets["V_thr"]
    n_boot = 3 if args.smoke else LINGAM_N_BOOTSTRAP
    jobs = [("LOCA", "low")] if args.smoke else [
        (k, g) for k in ACCIDENT_TYPES for g in GROUP_ORDER]

    t_start = time.time()

    # ===== 冒烟：只计时学图 =====
    if args.smoke:
        for kind in ("no_ontology", "strict"):
            t0 = time.time()
            if kind == "no_ontology":
                learn_no_ontology_template(manifest, vsets, baseline, scale,
                                           "LOCA", "low", n_bootstrap=n_boot,
                                           seed=GLOBAL_SEED)
            else:
                learn_strict_template(manifest, vsets, baseline, "LOCA", "low",
                                      n_bootstrap=n_boot, seed=GLOBAL_SEED)
            logger.info(f"[smoke] {kind} LOCA_low n_boot={n_boot} "
                        f"{time.time() - t0:.1f}s")
        logger.info(f"[smoke] 总耗时 {time.time() - t_start:.1f}s，未落盘未评分")
        return

    # ===== A. −本体约束：重学 9 模板 + 评分 =====
    t0 = time.time()
    no_onto_sigs, timings_a = _learn_templates(
        manifest, vsets, baseline, scale, "no_ontology", args.force,
        LINGAM_N_BOOTSTRAP, GLOBAL_SEED, jobs)
    group_rows, records = _evaluate(
        manifest, vsets, scale, baseline, calibration, no_onto_sigs, gates,
        score_trajectory, CONDITIONS)
    summary_a = _metrics_summary(group_rows, CONDITIONS)
    main_summary = D.load_json("metrics_summary.json")
    delta_a = {c: delta_metrics(summary_a["ALL"][c], main_summary["ALL"][c])
               for c in CONDITIONS}
    elapsed_a = time.time() - t0

    # ===== B. −过程本体：MAD 激活口径 =====
    t0 = time.time()
    mad = fit_mad_baseline(baseline, v_thr)
    mad_fn = functools.partial(score_trajectory_mad, mad=mad)
    group_rows_b, records_b = _evaluate(
        manifest, vsets, scale, baseline, calibration, main_sigs, gates,
        mad_fn, CONDITIONS)
    summary_b = _metrics_summary(group_rows_b, CONDITIONS)
    delta_b = {c: delta_metrics(summary_b["ALL"][c], main_summary["ALL"][c])
               for c in CONDITIONS}
    elapsed_b = time.time() - t0

    # ===== C. MASKED-strict：无 marker 重学 + 仅 MASKED 评分 =====
    t0 = time.time()
    strict_sigs, timings_c = _learn_templates(
        manifest, vsets, baseline, scale, "strict", args.force,
        LINGAM_N_BOOTSTRAP, GLOBAL_SEED, jobs)
    group_rows_c, records_c = _evaluate(
        manifest, vsets, scale, baseline, calibration, strict_sigs, gates,
        score_trajectory_strict, ["MASKED"])
    summary_c = _metrics_summary(group_rows_c, ["MASKED"])
    retention = {}
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            retention[f"{k}_{g}"] = edge_retention(
                main_sigs[(k, g)], strict_sigs[(k, g)])
    e3p_dist = _e3p_distribution(records_c["MASKED"])
    elapsed_c = time.time() - t0

    metrics = {
        "protocol": PROTOCOL_D3,
        "stage": "D4-ablation-symbolic-13.11",
        "generated_by": "rca/v21/run_ablation_symbolic.py",
        "note": ("−ontology/−process_ontology 复用主流程 calibration_params.json 标定与 "
                 "cf_validation 门控（cf 门控在主签名上算，消融只换图/A(x) 口径）；"
                 "delta = 消融 − 主流程 metrics_summary.json ALL 行"),
        "-ontology": {
            "note": "use_ontology=False 重学 9 模板；只报指标差异，不单独做传播链",
            "groups": summary_a["groups"],
            "ALL": summary_a["ALL"],
            "delta_vs_main_ALL": delta_a,
            "learn_timing_s": {"templates": timings_a, "total_s": elapsed_a},
        },
        "-process_ontology": {
            "note": ("A(x) 口径 μ±3σ → μ±3·MAD（方案字面不乘 1.4826）；"
                     "d 仍 σ 尺度，E2/E3 签名不变"),
            "mad": {v: float(m) for v, m in mad.items()},
            "groups": summary_b["groups"],
            "ALL": summary_b["ALL"],
            "delta_vs_main_ALL": delta_b,
            "timing_s": elapsed_b,
        },
        "-strict": {
            "note": ("结构消融，不与 full 比准确率；无 marker 重学 G_k′（V_graph−marker），"
                     "不学 C^A；仅 E2 分类 + E3′ 诊断（不融合，融合 E3=0）"),
            "groups": summary_c["groups"],
            "ALL": summary_c["ALL"],
            "edge_retention": retention,
            "e3p_diagnostic_distribution": e3p_dist,
            "learn_timing_s": {"templates": timings_c, "total_s": elapsed_c},
        },
        "timing": {
            "-ontology_s": elapsed_a,
            "-process_ontology_s": elapsed_b,
            "-strict_s": elapsed_c,
            "total_s": time.time() - t_start,
        },
    }
    D.save_json(_json_ready(metrics), METRICS_NAME)
    logger.info(f"saved -> {V21_OUTPUT / METRICS_NAME}")


if __name__ == "__main__":
    main()

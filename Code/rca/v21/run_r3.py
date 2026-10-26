"""D3 正式流水线执行器（方案 13.8.2）：60 条测试轨迹 FULL/MASKED 两条件评分 + 指标。

- MASKED 特征列 = V_thr（每条轨迹跑 assert_no_marker_columns，G4）；
- FULL 特征列 = V_thr + 三 marker，E1 用 marker_scale.json 的 epsilon；
- τ/δ 来自 calibration_params.json（MASKED 融合分标定，两条件共用）；
- Normal/1.csv 两条件均打分但不进指标（normal_check.json，期望弃权）；
- true 标签仅用于本文件指标聚合，评分侧不可见。

用法（cd Code）：PYTHONPATH=. python -m rca.v21.run_r3
"""
from __future__ import annotations

import pandas as pd

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKER_COLS,
    MAIN_WINDOW_START, MAIN_WINDOW_END, NORMAL_CSV, V21_OUTPUT, CF_DIR,
    W_FULL, W_MASKED,
)
from rca.v21 import data as D
from rca.v21.signatures import load_signature
from rca.v21.inversion import assert_no_marker_columns
from rca.v21.pipeline import (
    PROTOCOL_D3, load_cf_gate, score_trajectory, compute_metrics,
)
from core.utils import logger

CONDITIONS = ["FULL", "MASKED"]
CSV_COLUMNS = ["true", "pred", "accepted", "s1", "s2", "margin", "n_activation"]


def _load_inputs():
    manifest = D.load_json("split_manifest.json")
    baseline = D.load_json("shared_baseline.json")
    vsets = D.load_json("variable_sets.json")
    scale = D.load_json("marker_scale.json")
    calibration = D.load_json("calibration_params.json")
    sigs, gates = {}, {}
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            sig = load_signature(k, g)
            if sig is None:
                raise RuntimeError(f"R1 签名缺失 {k}_{g}，先完成 run_r1")
            sigs[(k, g)] = sig
            cf = D.load_json(f"cf_{k}_{g}.json", out_dir=CF_DIR)
            gates[(k, g)] = load_cf_gate(cf)
    return manifest, baseline, vsets, scale, calibration, sigs, gates


def _row(k_true, sev, res):
    return {"true": k_true, "sev": sev,
            "pred": res["argmax_class"] if res["accepted"] else "ABSTAIN",
            "accepted": bool(res["accepted"]),
            "s1": res["s1"], "s2": res["s2"], "margin": res["margin"],
            "n_activation": res["n_activation"],
            "_res": res}


def main():
    manifest, baseline, vsets, scale, calibration, sigs, gates = _load_inputs()
    v_thr = vsets["V_thr"]
    epsilon = scale["epsilon"]

    summary = {
        "protocol": PROTOCOL_D3,
        "stage": "D3-formal-13.8.2",
        "main_window": [MAIN_WINDOW_START, MAIN_WINDOW_END],
        "calibration": {"tau": calibration["tau"], "delta": calibration["delta"],
                        "n_trajectories": calibration["n_trajectories"],
                        "protocol": calibration["protocol"]},
        "weights": {"FULL": W_FULL, "MASKED": W_MASKED},
        "groups": {},
        "note": "Overall Accuracy 弃权=错；ALL 为三组 60 条测试轨迹的池化聚合（仅统计聚合）",
    }
    all_rows = {c: [] for c in CONDITIONS}

    for g in GROUP_ORDER:
        summary["groups"][g] = {}
        group_rows = {c: [] for c in CONDITIONS}
        for k_true in ACCIDENT_TYPES:
            sevs = manifest["classes"][k_true]["groups"][g]["test_severities"]
            for sev in sevs:
                for cond in CONDITIONS:
                    feat_cols = list(v_thr) if cond == "MASKED" else list(v_thr) + list(MARKER_COLS)
                    X, cols, _ = D.load_window(k_true, sev,
                                               MAIN_WINDOW_START, MAIN_WINDOW_END, feat_cols)
                    assert cols == feat_cols
                    if cond == "MASKED":
                        assert_no_marker_columns(cols, MARKER_COLS)  # G4
                    res = score_trajectory(
                        X, cols, cond, v_thr, sigs, gates, baseline,
                        calibration, epsilon=epsilon)
                    row = _row(k_true, sev, res)
                    group_rows[cond].append(row)
        for cond in CONDITIONS:
            rows = group_rows[cond]
            all_rows[cond].extend(rows)
            metrics = compute_metrics(rows)
            summary["groups"][g][cond] = {kk: vv for kk, vv in metrics.items()}
            csv_df = pd.DataFrame([{c: r[c] for c in CSV_COLUMNS} for r in rows])
            csv_path = V21_OUTPUT / f"metrics_{cond}_{g}.csv"
            csv_df.to_csv(csv_path, index=False)
            logger.info(f"[{cond}/{g}] n={metrics['n']} acc={metrics['overall_accuracy']:.3f} "
                        f"sel_acc={metrics['selective_accuracy']} "
                        f"cov={metrics['coverage']:.3f} abstain={metrics['abstention_rate']:.3f} "
                        f"macroF1={metrics['macro_f1']} -> {csv_path.name}")

    # ALL：三组池化（仅统计聚合）
    summary["ALL"] = {}
    for cond in CONDITIONS:
        metrics = compute_metrics(all_rows[cond])
        summary["ALL"][cond] = metrics
        logger.info(f"[{cond}/ALL] n={metrics['n']} acc={metrics['overall_accuracy']:.3f} "
                    f"sel_acc={metrics['selective_accuracy']} "
                    f"cov={metrics['coverage']:.3f} macroF1={metrics['macro_f1']} "
                    f"CI_overall={metrics['wilson95_overall']} "
                    f"CI_selective={metrics['wilson95_selective']}")

    # ==================== Normal/1.csv 功能验证（不进指标）====================
    df_norm = D.window_slice(pd.read_csv(NORMAL_CSV),
                             MAIN_WINDOW_START, MAIN_WINDOW_END)
    marker_med = {m: float(df_norm[m].median()) for m in MARKER_COLS}
    assert all(abs(v) < 1e-12 for v in marker_med.values()), f"Normal 主窗 marker 非零: {marker_med}"
    normal_out = {"source": str(NORMAL_CSV),
                  "window": [MAIN_WINDOW_START, MAIN_WINDOW_END],
                  "marker_main_medians": marker_med,
                  "expected": "abstain", "conditions": {}}
    all_abstain = True
    for cond in CONDITIONS:
        feat_cols = list(v_thr) if cond == "MASKED" else list(v_thr) + list(MARKER_COLS)
        X = df_norm[feat_cols].to_numpy(dtype=float)
        res = score_trajectory(X, feat_cols, cond, v_thr, sigs, gates, baseline,
                               calibration, epsilon=epsilon)
        all_abstain &= not res["accepted"]
        normal_out["conditions"][cond] = {
            "accepted": res["accepted"], "abstain_reason": res["abstain_reason"],
            "argmax_class": res["argmax_class"],
            "s1": res["s1"], "s2": res["s2"], "margin": res["margin"],
            "n_activation": res["n_activation"],
            "fusion": res["fusion"], "class_raw": res["class_raw"],
            "e1": res["e1"],
        }
    normal_out["passes_expectation"] = bool(all_abstain)
    D.save_json(normal_out, "normal_check.json")
    logger.info(f"[Normal] marker medians={marker_med} 期望弃权 passes={all_abstain}")

    D.save_json(summary, "metrics_summary.json")
    logger.info("saved -> metrics_summary.json / metrics_<cond>_<group>.csv / normal_check.json")


if __name__ == "__main__":
    main()

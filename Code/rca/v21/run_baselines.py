"""方案 v2.1 §13.9 三基线评测执行器（60 条测试轨迹；--smoke 每类 2 条共 6 条）。

输出（output/rca_v21/）：
- baseline_metrics.json：FULL/MASKED 两条件下 B0/B1/B2 的正确数/总数 + 95% Wilson CI，
  B2 5 折 CV、后端信息、Normal/1.csv 单列预测、口径注记；
- b2_coverage.csv：B2 FULL/MASKED 各 9 点选择性预测退化曲线（ceil 取整）。

用法：cd Code && PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_baselines [--smoke]
说明：B1/B2 无弃权；B0 为 marker 规则，0 或 >1 激活时输出 None（弃权，单列计数，
指标中弃权计错）；MASKED 条件 B0=N/A（null，不是 0）。
"""
from __future__ import annotations

import argparse
import csv
import time
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKER_COLS, XGB_PARAMS, GLOBAL_SEED,
    MAIN_WINDOW_START, MAIN_WINDOW_END, V21_OUTPUT,
)
from rca.v21 import data as D
from rca.v21.pipeline import wilson_ci  # 只读复用，不改 pipeline
from rca.v21.inversion import assert_no_marker_columns
from rca.v21 import baselines as B
from core.utils import logger


# ==================== 数据组织 ====================
def select_test_severities(manifest: Dict, smoke: bool
                           ) -> List[Tuple[str, List[int]]]:
    """每类测试严重度（low→mid→high 顺序）；--smoke 取每类前 2 条。"""
    out = []
    for k in ACCIDENT_TYPES:
        sevs = [int(s) for g in GROUP_ORDER
                for s in manifest["classes"][k]["groups"][g]["test_severities"]]
        if smoke:
            sevs = sevs[:2]
        out.append((k, sevs))
    return out


def load_epsilon() -> Dict[str, float]:
    """marker_scale.json 的按类 ε（文件实际键名 epsilon；兼容 epsilon_by_class）。"""
    ms = D.load_json("marker_scale.json")
    eps = ms.get("epsilon_by_class", ms.get("epsilon"))
    if not isinstance(eps, dict):
        raise KeyError("marker_scale.json 缺 epsilon/epsilon_by_class")
    return {k: float(eps[k]) for k in ACCIDENT_TYPES}


def collect_observations(manifest: Dict, v_thr: Sequence[str], baseline: Dict,
                         smoke: bool) -> List[Dict]:
    """每条测试轨迹加载一次：V_thr 主窗块（B1/B2 共用，MASKED 零访问断言）+
    FULL 用三 marker 主窗块（B0）。"""
    obs = []
    for k_true, sevs in select_test_severities(manifest, smoke):
        for sev in sevs:
            block, cols, _ = D.load_window(k_true, sev, MAIN_WINDOW_START,
                                           MAIN_WINDOW_END, list(v_thr))
            assert cols == list(v_thr)
            assert_no_marker_columns(cols, MARKER_COLS)  # G4：V_thr 侧零 marker
            d_vthr = B.signed_median_vthr(block, cols, v_thr, baseline)
            feats, feat_names = B.compute_b2_features(block, cols, v_thr, baseline)
            marker_by_class, marker_by_col = B.load_marker_medians(k_true, sev)
            obs.append({"true": k_true, "sev": int(sev),
                        "d_vthr": d_vthr,
                        "b2_x": feats.reshape(1, -1),
                        "b2_feature_names": feat_names,
                        "marker_by_class": marker_by_class,
                        "marker_by_col": marker_by_col})
    return obs


# ==================== 指标聚合 ====================
def _block(preds: List[Optional[str]], trues: List[str]) -> Dict:
    n = len(preds)
    correct = sum(int(p == t) for p, t in zip(preds, trues))
    ci = wilson_ci(correct, n)
    by_class = {}
    for k in ACCIDENT_TYPES:
        idx = [i for i, t in enumerate(trues) if t == k]
        c = sum(int(preds[i] == k) for i in idx)
        by_class[k] = {"correct": c, "total": len(idx),
                       "accuracy": (c / len(idx)) if idx else None,
                       "wilson_ci95": list(wilson_ci(c, len(idx)))
                       if wilson_ci(c, len(idx)) else None}
    return {"correct": correct, "total": n,
            "accuracy": (correct / n) if n else None,
            "wilson_ci95": list(ci) if ci else None,
            "by_class": by_class}


def evaluate(obs: Sequence[Dict], key_pred: str) -> Tuple[Dict, List[Optional[str]], List[str]]:
    preds = [o[key_pred] for o in obs]
    trues = [o["true"] for o in obs]
    return _block(preds, trues), preds, trues


# ==================== 主流程 ====================
def run(smoke: bool) -> Dict:
    t0 = time.perf_counter()
    manifest = D.load_json("split_manifest.json")
    baseline = D.load_json("shared_baseline.json")
    v_thr = D.load_json("variable_sets.json")["V_thr"]
    epsilon = load_epsilon()
    templates = B.load_b1_templates(v_thr)
    logger.info(f"V_thr={len(v_thr)} 变量，9 模板已加载；ε={epsilon}")

    # ---- B2 训练矩阵：9 组合并 train_template_severities（237 条）----
    t_tr = time.perf_counter()
    X_train, y_train, meta_train = B.build_b2_train_matrix(manifest, v_thr, baseline)
    assert X_train.shape == (len(meta_train), len(v_thr) * 6)
    feat_names = B.b2_feature_names(v_thr)
    assert_no_marker_columns(feat_names, MARKER_COLS)
    logger.info(f"B2 训练矩阵 {X_train.shape}（期望 237×102），标签分布 "
                f"{ {k: y_train.count(k) for k in ACCIDENT_TYPES} }")

    # ---- B2 5 折 StratifiedKFold CV（在训练集上）----
    t_cv = time.perf_counter()
    cv_mean, cv_folds = B.b2_cv5_accuracy(X_train, y_train, ACCIDENT_TYPES,
                                          seed=GLOBAL_SEED)
    cv_sec = time.perf_counter() - t_cv
    logger.info(f"B2 5 折 CV 准确率={cv_mean:.4f} folds={[round(x,4) for x in cv_folds]} "
                f"({cv_sec:.1f}s)")

    # ---- FULL / MASKED 各训练一版（特征/数据/种子相同 -> 预测应一致）----
    model_full = B.fit_b2(X_train, y_train, feat_names)
    model_masked = B.fit_b2(X_train, y_train, feat_names)

    # ---- 测试观测 ----
    obs = collect_observations(manifest, v_thr, baseline, smoke)
    logger.info(f"评测轨迹 {len(obs)} 条（smoke={smoke}）")

    b2_proba_full = b2_proba_masked = None
    for o in obs:
        # B0（FULL only）
        pred_b0, activated = B.b0_decide(o["marker_by_class"], epsilon)
        o["b0_pred"], o["b0_activated"] = pred_b0, activated
        # B1（FULL/MASKED 同输入）
        pred_b1, by_class_b1, per_tpl_b1 = B.b1_scores(o["d_vthr"], templates)
        o["b1_pred"], o["b1_by_class"], o["b1_per_template"] = (
            pred_b1, by_class_b1, per_tpl_b1)
    X_test = np.vstack([o["b2_x"][0] for o in obs])
    b2_pred_full, b2_conf_full, b2_proba_full = B.predict_b2(model_full, X_test)
    b2_pred_masked, b2_conf_masked, b2_proba_masked = B.predict_b2(model_masked, X_test)
    for i, o in enumerate(obs):
        o["b2_pred_full"], o["b2_conf_full"] = b2_pred_full[i], float(b2_conf_full[i])
        o["b2_pred_masked"], o["b2_conf_masked"] = (
            b2_pred_masked[i], float(b2_conf_masked[i]))
    b2_pred_equal = (b2_pred_full == b2_pred_masked) and bool(
        np.allclose(b2_proba_full, b2_proba_masked, atol=1e-12))

    # ---- 指标 ----
    trues = [o["true"] for o in obs]
    metrics_b0, preds_b0, _ = evaluate(obs, "b0_pred")
    metrics_b1, _, _ = evaluate(obs, "b1_pred")
    metrics_b2_full, _, _ = _b2_block(b2_pred_full, trues)
    metrics_b2_masked, _, _ = _b2_block(b2_pred_masked, trues)
    abstain_by_class = {k: sum(1 for o in obs
                               if o["true"] == k and o["b0_pred"] is None)
                        for k in ACCIDENT_TYPES}
    metrics_b0["abstain_n"] = sum(p is None for p in preds_b0)
    metrics_b0["abstain_by_class"] = abstain_by_class
    metrics_b0["note"] = (
        "B0 marker 规则：唯一 |median(marker)|>ε_k 激活才出预测；0 或 >1 激活 -> None"
        "（弃权，单列；overall 分母含弃权，弃权计错）")

    metrics_b1["note"] = "B1=argmax_k max_g Pearson(d_x,template_d)，无弃权"
    b2_equiv_note = (
        f"FULL/MASKED 特征定义相同（V_thr 17×6=102 维，不含 marker），分别训练，"
        f"预测/proba 一致={b2_pred_equal}（同训练集/超参/random_state 决定必然一致）")
    for mm, model in ((metrics_b2_full, model_full), (metrics_b2_masked, model_masked)):
        mm["note"] = "B2=argmax predict_proba，无弃权"
        mm["condition_equivalence"] = b2_equiv_note
        mm["full_masked_identical"] = bool(b2_pred_equal)
        mm["backend"] = model["backend"]
        mm["backend_version"] = model["backend_version"]
        mm["fallback_note"] = model["fallback_note"]
        mm["n_train"] = model["n_train"]

    # ---- B2 退化曲线（两条件各落一份）----
    curve_full = B.b2_coverage_curve(b2_proba_full, trues)
    curve_masked = B.b2_coverage_curve(b2_proba_masked, trues)
    _write_coverage_csv(curve_full, curve_masked, smoke)

    # ---- Normal/1.csv：B1/B2 单列，不进指标 ----
    normal = _normal_predictions(v_thr, baseline, templates, model_full,
                                 model_masked)

    out = {
        "gate": "BASELINES_v2.1_13.9",
        "smoke": bool(smoke),
        "n_evaluated": len(obs),
        "n_test_per_class_full": 20,
        "test_selection": {
            k: sevs for k, sevs in select_test_severities(manifest, smoke)},
        "main_window": [MAIN_WINDOW_START, MAIN_WINDOW_END],
        "xgb_params_requested": XGB_PARAMS,
        "backend": {"name": B.BACKEND_NAME, "version": B.BACKEND_VERSION,
                    "has_xgboost": B.HAS_XGBOOST,
                    "fallback_note": (None if B.HAS_XGBOOST else B.FALLBACK_NOTE)},
        "b2_train": {"n_trajectories": int(X_train.shape[0]),
                     "n_features": int(X_train.shape[1]),
                     "feature_layout": "17 V_thr 变量 × 6 统计量（变量优先）",
                     "stats_per_variable": list(B.B2_STATS),
                     "label_counts": {k: y_train.count(k) for k in ACCIDENT_TYPES}},
        "b2_cv5_stratified": {"mean_accuracy": cv_mean, "fold_accuracy": cv_folds,
                              "n_splits": 5, "shuffle": True,
                              "random_state": GLOBAL_SEED, "elapsed_sec": cv_sec},
        "conditions": {
            "FULL": {"B0": metrics_b0, "B1": metrics_b1, "B2": metrics_b2_full},
            "MASKED": {"B0": None,  # §13.9：MASKED 条件 B0=N/A（null，不是 0）
                       "B0_NA_note": "MASKED 零 marker 访问，B0 不适用（null，非 0）",
                       "B1": _identical_b1(metrics_b1),
                       "B2": metrics_b2_masked},
        },
        "normal_validation": normal,
        "coverage": {"csv": "b2_coverage.csv",
                     "coverages": list(B.COVERAGES),
                     "n_keep_rule": "n_keep=max(1,ceil(coverage*N))",
                     "FULL": curve_full, "MASKED": curve_masked},
        "per_trajectory": [_compact_obs(o) for o in obs],
        "notes": [
            "B1/B2 无弃权；B0 的 None 为规则弃权，单独计数且在 overall 中计错。",
            "B1 FULL/MASKED 输入相同（V_thr 不含 marker），两条件结果一致。",
            "B2 FULL/MASKED 特征定义决定两版相同：同 237 训练轨迹、同 102 维特征、"
            "XGBClassifier(**XGB_PARAMS) 同 random_state=42，分别落盘预测。",
            "ε_k 取自 marker_scale.json（键 epsilon）=0.1×min 训练轨迹 marker 主窗中位数。",
            "Normal/1.csv 仅功能验证，B1/B2 预测单列、不进任何指标。",
        ],
        "elapsed_sec_total": time.perf_counter() - t0,
    }
    path = D.save_json(out, "baseline_metrics.json")
    logger.info(f"saved -> {path}")
    logger.info(f"saved -> {V21_OUTPUT / 'b2_coverage.csv'}")
    _log_summary(out)
    return out


def _b2_block(preds: Sequence[str], trues: Sequence[str]) -> Tuple[Dict, List[str], List[str]]:
    return _block(list(preds), list(trues)), list(preds), list(trues)


def _identical_b1(metrics_b1: Dict) -> Dict:
    m = {k: v for k, v in metrics_b1.items()}
    m["condition_equivalence"] = (
        "FULL/MASKED 输入相同（d_x 仅用 V_thr，不含 marker），结果与 FULL 完全一致")
    return m


def _write_coverage_csv(curve_full: List[Dict], curve_masked: List[Dict],
                        smoke: bool) -> None:
    path = V21_OUTPUT / "b2_coverage.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["condition", "coverage", "n_accepted", "n_correct",
                    "selective_accuracy", "smoke"])
        for cond, curve in (("FULL", curve_full), ("MASKED", curve_masked)):
            for r in curve:
                w.writerow([cond, r["coverage"], r["n_accepted"],
                            r["n_correct"], round(r["selective_accuracy"], 6),
                            int(smoke)])


def _normal_predictions(v_thr, baseline, templates, model_full, model_masked) -> Dict:
    """Normal/1.csv（D.load_window 以 accident_type='Normal', sev=1 读取）的 B1/B2。"""
    block, cols, _ = D.load_window("Normal", 1, MAIN_WINDOW_START, MAIN_WINDOW_END,
                                   list(v_thr))
    assert cols == list(v_thr)
    d_vthr = B.signed_median_vthr(block, cols, v_thr, baseline)
    pred_b1, by_class, per_template = B.b1_scores(d_vthr, templates)
    feats, _ = B.compute_b2_features(block, cols, v_thr, baseline)
    p_full, c_full, proba_full = B.predict_b2(model_full, feats.reshape(1, -1))
    p_mask, c_mask, proba_mask = B.predict_b2(model_masked, feats.reshape(1, -1))
    return {
        "source": "Normal/1.csv 主窗（不进训练/指标）",
        "B1": {"pred": pred_b1,
               "scores_by_class": {k: round(float(v), 6) for k, v in by_class.items()},
               "scores_per_template": {kk: round(float(vv), 6)
                                       for kk, vv in per_template.items()}},
        "B2_FULL": {"pred": p_full[0], "confidence": float(c_full[0]),
                    "proba": {k: round(float(v), 6)
                              for k, v in zip(model_full["classes"], proba_full[0])}},
        "B2_MASKED": {"pred": p_mask[0], "confidence": float(c_mask[0]),
                      "proba": {k: round(float(v), 6)
                                for k, v in zip(model_masked["classes"], proba_mask[0])}},
    }


def _compact_obs(o: Dict) -> Dict:
    return {"true": o["true"], "sev": o["sev"],
            "b0_pred": o["b0_pred"], "b0_activated": o["b0_activated"],
            "b1_pred": o["b1_pred"],
            "b1_scores_by_class": {k: round(float(v), 6)
                                   for k, v in o["b1_by_class"].items()},
            "b2_pred_FULL": o["b2_pred_full"],
            "b2_conf_FULL": round(o["b2_conf_full"], 6),
            "b2_pred_MASKED": o["b2_pred_masked"],
            "b2_conf_MASKED": round(o["b2_conf_masked"], 6)}


def _log_summary(out: Dict) -> None:
    for cond in ("FULL", "MASKED"):
        for name in ("B0", "B1", "B2"):
            m = out["conditions"][cond][name]
            if m is None:
                logger.info(f"[{cond}] {name}=N/A (MASKED 不适用)")
                continue
            ci = m["wilson_ci95"]
            logger.info(f"[{cond}] {name}: {m['correct']}/{m['total']} "
                        f"acc={m['accuracy']:.3f} Wilson95={[round(x,3) for x in ci]}")
    logger.info(f"B2 CV5={out['b2_cv5_stratified']['mean_accuracy']:.4f} "
                f"backend={out['backend']['name']} {out['backend']['version']} "
                f"total {out['elapsed_sec_total']:.1f}s")


def main() -> None:
    ap = argparse.ArgumentParser(description="v2.1 §13.9 三基线评测")
    ap.add_argument("--smoke", action="store_true", help="每类仅评 2 条（共 6 条）")
    args = ap.parse_args()
    run(smoke=args.smoke)


if __name__ == "__main__":
    main()

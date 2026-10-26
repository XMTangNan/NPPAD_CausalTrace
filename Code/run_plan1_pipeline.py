"""
方案一（LOCA 单事故因果追踪）流水线入口。
使用分层结构：core/（通用算法）+ accidents/loca/（LOCA物理约束）。
产出写入 output/plan1_loca/results/。
"""
import json, numpy as np, pandas as pd
from collections import Counter
from pathlib import Path

from config import (REPO_DATA, OUTPUT_DIR, PLAN1_RESULT_DIR, RESULT_DIR,
                    WINDOW_START, WINDOW_END, TIME_STEP, WINDOW_POINTS,
                    TRAIN_RATIO, RANDOM_SEED, BOOTSTRAP_N, F_E_THRESHOLD, S_E_THRESHOLD,
                    ANOMALY_SIGMA, ANOMALY_ALPHA, ALPHA_GRID, STATIONARY_CV_THRESHOLD,
                    SEVERITY_GROUPS, LLM_MODEL)

from accidents.loca.config import (TARGET, PRIOR_VARIABLES, FROZEN_RELATIONS,
                                   FORBIDDEN_EDGES, FAULT_SYSTEM_VARS, CORE_RELATIONS)
from accidents.loca.data_loader import (load_all_loca, load_loca1_steady_segment)
from accidents.loca.anomaly import (build_thresholds_from_loca1_steady,
                                    compute_anomaly_scores, enhanced_ranking)
from accidents.loca.ontology import (build_ontology, save_ontology,
                                     get_edge_category, serialize_causal_graph)
from accidents.loca.explanation import generate_explanation, check_faithfulness, generate_explanation_llm

from core.utils import logger, save_json, save_text
from core.data_utils import (build_matrix, fit_standardizer, standardize,
                             split_trajectories, run_audit, non_gaussian_diagnostics,
                             severity_heterogeneity)
from core.lingam_baseline import verify_matrix_index, fit_lingam, adjacency_to_edges
from core.bootstrap import run_bootstrap, select_stable_edges, build_stable_B
from core.total_effect import compute_total_effects, stable_graph_total_effect
from core.severity_groups import run_severity_groups, edge_direction_consistency
from core.nonlinear_baseline import (anm_pair_direction, build_anm_graph,
                                     anm_total_effect_ranking, run_anm_bootstrap,
                                     compare_lingam_anm)
from core.method_ensemble import classify_edges, reciprocal_rank_fusion, build_ensemble, marker_direction_check
from core.ontology_constraint import apply_ontology_constraint
from core.consistency import consistency_check, dual_split_consistency
from core.ablation import run_ablation, topk_overlap


def save_json_v2(obj, name):
    """写入方案一归档目录。"""
    path = Path(PLAN1_RESULT_DIR) / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"saved -> {path}")
    return path


def save_text_v2(text, name):
    path = Path(PLAN1_RESULT_DIR) / f"{name}.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info(f"saved -> {path}")
    return path


def main():
    logger.info("=" * 60)
    logger.info("NPPAD CausalTrace Pipeline (Plan1 LOCA)")
    logger.info("=" * 60)
    logger.info(f"window: t={WINDOW_START}~{WINDOW_END}s ({WINDOW_POINTS} pts), {TIME_STEP}s/step")
    logger.info(f"prior variables: {len(PRIOR_VARIABLES)}")

    # ---- Phase 1: 数据加载 + 审计 ----
    trajectories, valid_vars = load_all_loca()
    variables = valid_vars
    severities = list(range(1, 101))
    train_sev, val_sev = split_trajectories(severities, TRAIN_RATIO, RANDOM_SEED)

    train_mat = build_matrix(trajectories, train_sev, variables)
    mu, sigma = fit_standardizer(train_mat)
    Z = standardize(train_mat, mu, sigma)

    audit = run_audit(trajectories, variables, train_sev, WINDOW_POINTS, SEVERITY_GROUPS)
    save_json_v2(audit, "audit_report")

    ng = non_gaussian_diagnostics(Z, variables)
    sh = severity_heterogeneity(trajectories, variables, SEVERITY_GROUPS)
    save_json_v2({"non_gaussian": ng, "severity_heterogeneity": sh}, "diagnostics")

    # ---- Phase 2: LiNGAM + Bootstrap ----
    verify_matrix_index(Z, variables)
    B, K = fit_lingam(Z)
    edges = adjacency_to_edges(B, variables)
    save_json_v2({"n_edges": len(edges), "edges": edges, "B": B.tolist(), "K": K.tolist()}, "lingam_baseline")

    bs = run_bootstrap(trajectories, train_sev, variables, mu, sigma, BOOTSTRAP_N, RANDOM_SEED)
    stable = select_stable_edges(bs["edge_stats"], F_E_THRESHOLD, S_E_THRESHOLD)
    B_stable = build_stable_B(stable, variables)
    save_json_v2(bs, "bootstrap_results")

    # ---- Phase 3: 总因果效应 ----
    effects = compute_total_effects([B_stable], variables, TARGET)
    te = stable_graph_total_effect(B_stable, variables, TARGET)
    save_json_v2({"B_stable": B_stable.tolist(), "T": effects["T"].tolist(),
                  "total_effects_per_edge": effects["effects"],
                  "upstream_ranking": te["upstream_ranking"],
                  "total_effect_on_target": te["total_effect_on_target"]},
                 "total_effect_results")

    # ---- Phase 4: 异常检测 ----
    steady_df = load_loca1_steady_segment()
    thresholds = build_thresholds_from_loca1_steady(steady_df, variables)
    anomaly_scores = compute_anomaly_scores(trajectories, variables, thresholds)
    base_ranking = [(v, abs(te["total_effect_on_target"].get(v, 0.0))) for v in variables]
    anomaly_ranking = enhanced_ranking(base_ranking, anomaly_scores)
    save_json_v2(thresholds, "anomaly_thresholds")
    save_json_v2(anomaly_scores, "anomaly_scores")
    save_json_v2({"base_ranking": base_ranking, "anomaly_ranking": anomaly_ranking,
                  "alpha_grid": ALPHA_GRID}, "anomaly_ranking_comparison")

    # ---- Phase 4.5: 严重度分组 ----
    sg = run_severity_groups(trajectories, variables, mu, sigma, SEVERITY_GROUPS, TRAIN_RATIO, RANDOM_SEED)
    save_json_v2(sg, "severity_groups")
    consistency = edge_direction_consistency(sg, variables, CORE_RELATIONS)
    save_json_v2(consistency, "consistency_results")

    # ---- Phase 5: GAM-ANM 非线性基线 ----
    anm_ranking = anm_total_effect_ranking(Z, variables, TARGET)
    anm_edges = build_anm_graph(anm_ranking, variables, k=5)
    save_json_v2({"anm_ranking": anm_ranking, "anm_edges": anm_edges}, "anm_baseline_results")

    anm_bs = run_anm_bootstrap(trajectories, train_sev, variables, mu, sigma, 50, RANDOM_SEED)
    save_json_v2(anm_bs, "anm_bootstrap_results")

    cmp_result = compare_lingam_anm(edges, anm_edges, te["upstream_ranking"], anm_ranking, variables, "WLR", TARGET)
    save_json_v2(cmp_result, "anm_comparison")

    # ---- Phase 5.5: 方法结合 ----
    ensemble = build_ensemble(edges, anm_edges, te["upstream_ranking"], anm_ranking, "WLR", TARGET)
    save_json_v2(ensemble, "ensemble_results")

    # ---- Phase 6: 本体约束 ----
    ontology = build_ontology(variables)
    save_ontology(ontology, Path(PLAN1_RESULT_DIR) / "ontology.json")
    B_ontology = apply_ontology_constraint(B_stable, variables, ontology)
    save_json_v2({"B_before": B_stable.tolist(), "B_after": B_ontology.tolist()}, "ontology_constraint_report")

    # ---- Phase 6.5: 一致性 ----
    cons = consistency_check(trajectories, variables, mu, sigma, train_sev, val_sev, effects, TARGET, "WLR")
    save_json_v2(cons, "consistency_check")
    dual = dual_split_consistency(trajectories, variables, TARGET, "WLR", 20, 1, 66, 67, 100)
    save_json_v2(dual, "dual_split_consistency")

    # ---- Phase 7: 消融 ----
    abl = run_ablation(trajectories, variables, train_sev, mu, sigma, TARGET)
    save_json_v2(abl, "ablation_results")

    # ---- Phase 8: 解释生成 ----
    explanation = generate_explanation(te["upstream_ranking"], variables, ontology, thresholds, anomaly_scores, TARGET)
    faithfulness = check_faithfulness(te["upstream_ranking"], variables, ontology, TARGET)
    save_text_v2(explanation, "root_cause_explanation")
    save_json_v2(faithfulness, "faithfulness_check")

    # ---- Phase 8.5: LLM 增强解释 ----
    llm_explanation = generate_explanation_llm(
        te["upstream_ranking"], variables, ontology, thresholds, anomaly_scores, TARGET)
    if llm_explanation:
        save_text_v2(llm_explanation, "llm_explanation")

    # ---- 汇总报告 ----
    report = generate_summary_report(te, ensemble, consistency, cons, dual, faithfulness,
                                     anomaly_ranking, cmp_result, variables)
    save_text_v2(report, "root_cause_report")

    logger.info("=" * 60)
    logger.info("Pipeline complete. Results in output/plan1_loca/results/")
    logger.info("=" * 60)


def generate_summary_report(te, ensemble, consistency, cons, dual, faithfulness,
                            anomaly_ranking, cmp_result, variables):
    lines = []
    lines.append("=" * 60)
    lines.append("NPPAD CausalTrace - Root Cause Report (Plan1 LOCA)")
    lines.append("=" * 60)
    lines.append("")
    lines.append("【结构化三分类摘要】")
    lines.append("")
    lines.append("-- 上游变量排序（总因果效应绝对值）--")
    for rank, (v, score) in enumerate(te["upstream_ranking"][:10], 1):
        lines.append(f"  {rank}. {v}: {score:.4f}")
    lines.append("")
    lines.append("-- 方法结合（LiNGAM + GAM-ANM）--")
    lines.append(f"  共识边: {ensemble.get('consensus_count', 0)}")
    lines.append(f"  冲突边: {ensemble.get('conflict_count', 0)}")
    lines.append(f"  单边边: {ensemble.get('single_count', 0)}")
    lines.append(f"  RRF融合排序 Top-5: {[x[0] for x in ensemble.get('fused_ranking', [])[:5]]}")
    lines.append("")
    lines.append("-- 严重度分组一致性 --")
    for r in consistency.get("results", []):
        lines.append(f"  {r['label']}: 方向一致性={r['consistency']}, 冲突组={r['conflict_groups']}")
    lines.append("")
    lines.append("-- 一致性检验 --")
    lines.append(f"  WLR→P 效应预测: ΔP_pred={cons.get('delta_p_pred', 'N/A')}")
    lines.append(f"  实际观测: ΔP_obs={cons.get('delta_p_obs', 'N/A')}")
    lines.append(f"  符号一致: {cons.get('sign_consistent', 'N/A')}")
    lines.append("")
    lines.append("-- 忠实性检查 --")
    lines.append(f"  因果充分性: {faithfulness.get('causal_sufficiency', 'N/A')}")
    lines.append(f"  结构忠实性: {faithfulness.get('structural_faithfulness', 'N/A')}")
    lines.append("")
    lines.append("-- 异常增强排序 Top-5 --")
    for v, s in anomaly_ranking[:5]:
        lines.append(f"  {v}: {s:.4f}")
    lines.append("")
    lines.append("=" * 60)
    lines.append("LLM 自然语言根因解释见 llm_explanation.txt")
    lines.append("=" * 60)
    return "\n".join(lines)


if __name__ == "__main__":
    main()

"""
方案二（跨事故因果签名 RCA）流水线入口。
产出写入 output/rca/results/ 和 output/rca/signatures/。
"""
import json, numpy as np, pandas as pd
from pathlib import Path
from collections import Counter

from config import (WINDOW_START, WINDOW_END, TIME_STEP, WINDOW_POINTS,
                    TRAIN_RATIO, RANDOM_SEED, BOOTSTRAP_N)
from core.utils import logger, save_json, log_error
from core.data_utils import build_matrix, fit_standardizer, standardize
from core.lingam_baseline import fit_lingam, adjacency_to_edges
from core.total_effect import compute_total_effects
from core.nonlinear_baseline import build_anm_graph, anm_total_effect_ranking
from core.method_ensemble import classify_edges

from rca.rca_config import (ACCIDENT_TYPES, VARIABLE_UNION_22, CLASS_VARIABLES,
                            CLASS_TARGETS, CLASS_MARKERS, SIGNATURE_DIR, RESULT_DIR)
from rca.rca_data_loader import (load_all_accidents, build_shared_baseline,
                                 stratified_split)
from rca.signature import (compute_marker_activation, compute_anomaly_pattern,
                           build_causal_signature, build_class_prototype)
from rca.signature_matcher import match_signature
from rca.rca_baselines import baseline_b0_predict, baseline_b1_predict, baseline_b2_predict


def build_class_causal_model(accident_type, trajectories, train_sev, variables, baseline):
    """为某事故类构建因果模型：LiNGAM 图 + GAM-ANM 图 + 因果效应排序。
    仅使用训练集数据，防数据泄漏。
    """
    target = CLASS_TARGETS[accident_type]
    marker = CLASS_MARKERS[accident_type]

    train_mat = build_matrix(trajectories, train_sev, variables)
    mu, sigma = fit_standardizer(train_mat)
    Z = standardize(train_mat, mu, sigma)

    # G^L: LiNGAM
    B = fit_lingam(Z)
    g_l_edges = adjacency_to_edges(B, variables)
    effects = compute_total_effects([B], variables, target)
    c_l = {v: float(effects["per_variable"].get(v, {}).get("median_total_effect", 0.0))
           for v in variables if v != target}

    # G^A: GAM-ANM
    B_a = build_anm_graph(Z, variables)
    g_a_edges = adjacency_to_edges(B_a, variables)
    anm_ranking = anm_total_effect_ranking(Z, variables, target)
    c_a = {r["variable"]: float(r["abs_effect"]) for r in anm_ranking}

    # E^cons: 共识边
    cls = classify_edges(g_l_edges, g_a_edges)
    e_cons = cls.get("consensus", [])

    model = {
        "accident_type": accident_type,
        "target": target,
        "marker": marker,
        "G_L": g_l_edges,
        "G_A": g_a_edges,
        "E_cons": e_cons,
        "C_L": c_l,
        "C_A": c_a,
        "mu": mu.tolist(),
        "sigma": sigma.tolist(),
    }
    logger.info(f"[{accident_type}] causal model built: G_L={len(g_l_edges)} edges, G_A={len(g_a_edges)} edges")
    return model


def build_signature_for_trajectory(accident_type, severity, trajectory, variables,
                                   baseline, class_model=None, per_trajectory_causal=False):
    """为单条轨迹构建签名。
    
    Args:
        per_trajectory_causal: 若为 True，用轨迹自身数据构建 LiNGAM 模型（防泄漏）；
                               若为 False，使用 class_model 的类级因果组件（仅用于训练集原型构建）。
    """
    baseline_mu = dict(zip(baseline["variables"], baseline["mu"]))
    baseline_sigma = dict(zip(baseline["variables"], baseline["sigma"]))

    marker_rule = CLASS_MARKERS[accident_type]
    marker = marker_rule["positive"][0] if marker_rule["positive"] else None
    m_activated, m_score = compute_marker_activation(
        trajectory, marker, baseline_mu, baseline_sigma) if marker else (False, 0.0)
    anomaly_pattern = compute_anomaly_pattern(
        trajectory, variables, baseline_mu, baseline_sigma)

    g_l_edges = []
    g_a_edges = []
    e_cons = []
    c_l = {}
    c_a = {}

    if per_trajectory_causal:
        # 用轨迹自身数据构建 LiNGAM（防泄漏），不使用已知类别的因果模型
        from core.data_utils import build_matrix as _bm
        traj_mat = _bm({severity: trajectory}, [severity], variables)
        mu_t = traj_mat.mean(axis=0)
        sigma_t = traj_mat.std(axis=0)
        sigma_t = np.where(sigma_t < 1e-6, 1e-6, sigma_t)
        Z = (traj_mat - mu_t) / sigma_t
        try:
            B = fit_lingam(Z)
            g_l_edges = adjacency_to_edges(B, variables)
            target = CLASS_TARGETS[accident_type]
            if target in variables:
                effects = compute_total_effects([B], variables, target)
                c_l = {v: float(effects["per_variable"].get(v, {}).get("median_total_effect", 0.0))
                       for v in variables if v != target}
        except Exception as e:
            logger.warning(f"[{accident_type}_{severity}] per-trajectory LiNGAM failed: {e}")
        # C^A 留空（逐条 GAM-ANM 太慢，E3 仅用 C^L）
    elif class_model is not None:
        g_l_edges = class_model["G_L"]
        g_a_edges = class_model["G_A"]
        e_cons = class_model["E_cons"]
        c_l = class_model["C_L"]
        c_a = class_model["C_A"]

    signature = {
        "accident_type": accident_type,
        "severity": severity,
        "target": CLASS_TARGETS[accident_type],
        "marker": marker_rule,
        "M": {"activated": m_activated, "score": m_score},
        "A": anomaly_pattern,
        "G_L": g_l_edges,
        "G_A": g_a_edges,
        "E_cons": e_cons,
        "C_L": c_l,
        "C_A": c_a,
    }
    return signature


def compute_metrics(results, true_labels):
    """计算分类指标，明确区分两种口径：
    - Selective Accuracy: 仅在接受（未弃权）样本上的准确率
    - Overall Accuracy: 全样本准确率，弃权计为错误
    - Macro-F1: 在接受样本上计算（与 Selective Accuracy 同口径）
    """
    n = len(results)
    correct = 0
    abstained = 0
    predicted = []
    actual = []
    for r, true in zip(results, true_labels):
        actual.append(true)
        if r["abstained"]:
            abstained += 1
            predicted.append(None)
        else:
            predicted.append(r["predicted"])
            if r["predicted"] == true:
                correct += 1

    n_accepted = n - abstained
    coverage = n_accepted / n
    # Selective Accuracy: 接受样本上的准确率
    selective_accuracy = correct / n_accepted if n_accepted > 0 else 0.0
    # Overall Accuracy: 全样本准确率，弃权=错误
    overall_accuracy = correct / n
    abstention_rate = abstained / n

    # F1 (macro, 仅在接受样本上计算，与 Selective Accuracy 同口径)
    classes = ACCIDENT_TYPES
    f1_per_class = {}
    for c in classes:
        tp = sum(1 for p, a in zip(predicted, actual) if p == c and a == c)
        fp = sum(1 for p, a in zip(predicted, actual) if p == c and a != c)
        fn = sum(1 for p, a in zip(predicted, actual) if p != c and a == c)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        f1_per_class[c] = {"precision": precision, "recall": recall, "f1": f1}
    macro_f1 = np.mean([v["f1"] for v in f1_per_class.values()])

    return {
        "n_total": n,
        "n_correct": correct,
        "n_abstained": abstained,
        "selective_accuracy": selective_accuracy,   # 接受样本准确率
        "overall_accuracy": overall_accuracy,       # 全样本准确率（弃权=错误）
        "macro_f1": float(macro_f1),                # 接受样本上的宏F1
        "coverage": coverage,
        "abstention_rate": abstention_rate,
        "per_class": f1_per_class,
    }


def main():
    logger.info("=" * 60)
    logger.info("NPPAD CausalTrace RCA Pipeline (Plan2 Cross-Accident)")
    logger.info("=" * 60)

    # ---- 1. 加载全部事故数据 ----
    all_data = load_all_accidents()
    logger.info(f"Loaded {len(ACCIDENT_TYPES)} accident types")

    # ---- 2. 构建共享基线（三类事故 sev-1 池化）----
    accident_trajectories = {at: all_data[at]["trajectories"] for at in ACCIDENT_TYPES}
    baseline = build_shared_baseline(accident_trajectories, VARIABLE_UNION_22)
    save_json(baseline, "shared_baseline", RESULT_DIR)

    # ---- 3. 逐类构建因果模型 ----
    class_models = {}
    for at in ACCIDENT_TYPES:
        variables = all_data[at]["valid_vars"]
        train_sev = all_data[at]["train_sev"]
        class_models[at] = build_class_causal_model(
            at, all_data[at]["trajectories"], train_sev, variables, baseline)
    save_json(class_models, "class_causal_models", RESULT_DIR)

    # ---- 4. 构建类原型（聚合训练集签名）----
    prototypes = {}
    for at in ACCIDENT_TYPES:
        variables = all_data[at]["valid_vars"]
        train_sev = all_data[at]["train_sev"]
        train_sigs = []
        for sev in train_sev:
            traj = all_data[at]["trajectories"][sev]
            sig = build_signature_for_trajectory(
                at, sev, traj, variables, baseline, class_models[at])
            train_sigs.append(sig)
        prototypes[at] = build_class_prototype(at, train_sigs)
        logger.info(f"[{at}] prototype built from {len(train_sigs)} training signatures")
    save_json(prototypes, "class_prototypes", RESULT_DIR)

    # ---- 5. 测试集签名构建（防泄漏：用轨迹自身数据构建因果模型）----
    test_sigs = []
    true_labels = []
    for at in ACCIDENT_TYPES:
        variables = all_data[at]["valid_vars"]
        val_sev = all_data[at]["val_sev"]
        for sev in val_sev:
            traj = all_data[at]["trajectories"][sev]
            sig = build_signature_for_trajectory(
                at, sev, traj, variables, baseline, per_trajectory_causal=True)
            test_sigs.append(sig)
            true_labels.append(at)

    # ---- 6. 主方法 + 消融实验（复用同一批 test_sigs）----
    # 消融配置：不同 E1/E2/E3 权重组合
    ablation_configs = {
        "full (E1+E2+E3)":   (0.3, 0.5, 0.2),
        "E1_only":           (1.0, 0.0, 0.0),
        "E2_only":           (0.0, 1.0, 0.0),
        "E3_only":           (0.0, 0.0, 1.0),
        "no_E3 (E1+E2)":     (0.375, 0.625, 0.0),
    }

    def run_matching_with_weights(test_sigs, prototypes, w1, w2, w3):
        results = []
        for sig in test_sigs:
            match = match_signature(sig, prototypes, w1=w1, w2=w2, w3=w3)
            results.append({
                "true": sig["accident_type"],
                "predicted": match["predicted"],
                "abstained": match["abstained"],
                "reason": match["reason"],
            })
        return results

    ablation_metrics = {}
    for name, (w1, w2, w3) in ablation_configs.items():
        res = run_matching_with_weights(test_sigs, prototypes, w1, w2, w3)
        ablation_metrics[name] = compute_metrics(res, true_labels)

    rca_results = run_matching_with_weights(test_sigs, prototypes, 0.3, 0.5, 0.2)
    rca_metrics = ablation_metrics["full (E1+E2+E3)"]

    # ---- 7. 基线方法 B0/B1/B2 ----
    b0_results, b1_results, b2_results = [], [], []
    for sig in test_sigs:
        b0_pred, _ = baseline_b0_predict(sig, prototypes)
        b1_pred, _ = baseline_b1_predict(sig, prototypes)
        b2_pred, _ = baseline_b2_predict(sig, prototypes)
        b0_results.append({"true": sig["accident_type"], "predicted": b0_pred})
        b1_results.append({"true": sig["accident_type"], "predicted": b1_pred})
        b2_results.append({"true": sig["accident_type"], "predicted": b2_pred})

    def simple_metrics(results, labels):
        n = len(results)
        correct = sum(1 for r, l in zip(results, labels) if r["predicted"] == l)
        return {"accuracy": correct / n, "n": n}

    b0_metrics = simple_metrics(b0_results, true_labels)
    b1_metrics = simple_metrics(b1_results, true_labels)
    b2_metrics = simple_metrics(b2_results, true_labels)

    all_metrics = {
        "rca_proposed": rca_metrics,
        "b0_anomaly_only": b0_metrics,
        "b1_causal_no_abstention": b1_metrics,
        "b2_random_abstention": b2_metrics,
        "ablation": ablation_metrics,
    }
    save_json(all_metrics, "rca_metrics", RESULT_DIR)
    save_json(rca_results, "rca_predictions", RESULT_DIR)

    # ---- 8. 输出报告 ----
    report = generate_rca_report(all_metrics, prototypes, class_models, ablation=ablation_metrics)
    report_path = RESULT_DIR / "rca_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info(f"saved -> {report_path}")

    logger.info("=" * 60)
    logger.info(f"RCA Pipeline complete.")
    logger.info(f"  Proposed: sel_acc={rca_metrics['selective_accuracy']:.3f}, "
                f"ovr_acc={rca_metrics['overall_accuracy']:.3f}, f1={rca_metrics['macro_f1']:.3f}, "
                f"cov={rca_metrics['coverage']:.3f}, abst={rca_metrics['abstention_rate']:.3f}")
    logger.info(f"  B0 (anomaly): acc={b0_metrics['accuracy']:.3f}")
    logger.info(f"  B1 (causal):  acc={b1_metrics['accuracy']:.3f}")
    logger.info(f"  B2 (random):  acc={b2_metrics['accuracy']:.3f}")
    logger.info(f"  Ablation: {len(ablation_metrics)} configs done")
    logger.info("=" * 60)


def generate_rca_report(metrics, prototypes, class_models, ablation=None):
    lines = []
    lines.append("=" * 70)
    lines.append("NPPAD CausalTrace - 跨事故因果签名 RCA 报告 (方案二)")
    lines.append("=" * 70)
    lines.append("")
    lines.append("【方法对比】")
    rca = metrics["rca_proposed"]
    lines.append(f"  提出方法 (因果签名+弃权):")
    lines.append(f"    Selective Accuracy (接受样本): {rca['selective_accuracy']:.3f}")
    lines.append(f"    Overall Accuracy (弃权=错误):   {rca['overall_accuracy']:.3f}")
    lines.append(f"    Macro-F1 (接受样本): {rca['macro_f1']:.3f}")
    lines.append(f"    Coverage: {rca['coverage']:.3f}  Abstention Rate: {rca['abstention_rate']:.3f}")
    lines.append(f"    (n_total={rca['n_total']}, n_correct={rca['n_correct']}, n_abstained={rca['n_abstained']})")
    lines.append("")
    lines.append(f"  B0 (仅异常模式, 无弃权): acc={metrics['b0_anomaly_only']['accuracy']:.3f}")
    lines.append(f"  B1 (仅因果, 无弃权):     acc={metrics['b1_causal_no_abstention']['accuracy']:.3f}")
    lines.append(f"  B2 (随机+弃权):          acc={metrics['b2_random_abstention']['accuracy']:.3f}")
    lines.append("")

    # 消融实验
    if ablation is not None:
        lines.append("=" * 70)
        lines.append("【消融实验：E1/E2/E3 组件贡献度】")
        lines.append("=" * 70)
        lines.append(f"  {'配置':<20s} {'SelAcc':>8s} {'OvrAcc':>8s} {'F1':>8s} {'Cov':>8s} {'Abst':>8s}")
        lines.append("  " + "-" * 60)
        for name, m in ablation.items():
            lines.append(f"  {name:<20s} {m['selective_accuracy']:>8.3f} {m['overall_accuracy']:>8.3f} "
                         f"{m['macro_f1']:>8.3f} {m['coverage']:>8.3f} {m['abstention_rate']:>8.3f}")
        lines.append("")

    lines.append("=" * 70)
    lines.append("【因果溯源：方案一方法产出的上游变量排序】")
    lines.append("（借用方案一的 LiNGAM 总因果效应 T=(I-B)^-1 与 GAM-ANM 边际效应排序）")
    lines.append("=" * 70)
    for at in ACCIDENT_TYPES:
        model = class_models[at]
        target = model["target"]
        lines.append("")
        lines.append(f"--- {at} (目标变量 target={target}) ---")

        # LiNGAM 上游排序（总因果效应绝对值）
        c_l = model["C_L"]
        c_l_sorted = sorted(c_l.items(), key=lambda x: abs(x[1]), reverse=True)
        lines.append(f"  LiNGAM 上游排序 (|median total effect| 降序):")
        for rank, (v, eff) in enumerate(c_l_sorted[:10], 1):
            lines.append(f"    {rank:2d}. {v:8s}  effect={eff:+.4f}")

        # GAM-ANM 上游排序
        c_a = model["C_A"]
        c_a_sorted = sorted(c_a.items(), key=lambda x: abs(x[1]), reverse=True)
        lines.append(f"  GAM-ANM 上游排序 (|avg marginal effect| 降序):")
        for rank, (v, eff) in enumerate(c_a_sorted[:10], 1):
            lines.append(f"    {rank:2d}. {v:8s}  effect={eff:+.4f}")

        # 共识边（涉及目标变量）
        e_cons = model["E_cons"]
        cons_target = [e for e in e_cons if e.get("to") == target or e.get("from") == target]
        lines.append(f"  共识边 (LiNGAM∩GAM-ANM 方向一致, 共 {len(e_cons)} 条, 涉目标 {len(cons_target)} 条):")
        for e in cons_target[:8]:
            direction = f"{e['from']}→{e['to']}"
            lines.append(f"    {direction}")

    lines.append("")
    lines.append("=" * 70)
    lines.append("【因果签名七组件 & 匹配证据 E1/E2/E3】")
    lines.append("=" * 70)
    lines.append("  七组件: M(marker规则) A(异常模式) G^L(LiNGAM图) G^A(ANM图) E^cons(共识边) C^L(LiNGAM效应) C^A(ANM效应)")
    lines.append("  匹配证据:")
    lines.append("    E1 = 正marker激活率 × 负marker未激活率  (marker规则匹配)")
    lines.append("    E2 = 异常模式余弦相似度  (模式匹配)")
    lines.append("    E3 = C^L/C^A 因果效应排序 Spearman 相关  (因果模型一致性证据，非因果反演证明)")
    lines.append("    融合 score = w1·E1 + w2·E2 + w3·E3  (默认 0.3/0.5/0.2)")
    lines.append("    弃权: score<0.3 或 top-2 差距<0.05")
    lines.append("")
    lines.append("  ⚠ 概念边界：E3 衡量两种因果算法（LiNGAM 与 GAM-ANM）输出的一致性，")
    lines.append("    反映模型间的结构共识，不等同于\"真实因果链已被反演\"。两种算法共享")
    lines.append("    数据/变量/预处理偏差时可能产生虚高一致性。")
    lines.append("")
    lines.append("  ⚠ 数值尺度警示：LiNGAM 总因果效应 T=(I-B)^-1 在 B 存在接近 1 的特征值时")
    lines.append("    会数值爆炸（如 LOCA 中 PSGA→P 效应 +5855），这是静态 LiNGAM 无法处理")
    lines.append("    动态反馈环（如 WLR↔P）的已知局限，效应绝对值不宜跨变量直接比较。")
    lines.append("")

    lines.append("【逐类 marker 规则 & 原型异常模式 Top-5】")
    for at, proto in prototypes.items():
        marker = proto["marker"]
        a_sorted = sorted(proto["A_prototype"].items(), key=lambda x: x[1], reverse=True)[:5]
        lines.append(f"  {at}: marker={marker}")
        lines.append(f"    异常 Top-5: {[(v, round(s,1)) for v,s in a_sorted]}")
    lines.append("")

    lines.append("=" * 70)
    lines.append("【关键发现与局限】")
    lines.append("=" * 70)
    lines.append("  1. marker 变量直接编码事故类型：WLR(仅LOCA非零) / WTRA(仅SGATR非零) /")
    lines.append("     WFLB(仅FLB非零) 构成事故类型 one-hot 编码。消融实验显示 E1(marker规则)")
    lines.append("     单独即达 100% 准确率，E2(异常模式) 与 E3(因果一致性) 在 FULL 条件下")
    lines.append("     增量贡献为零。")
    lines.append("  2. 因此，当前 100% 准确率仅证明\"marker 规则有效\"，不能证明\"因果签名方法")
    lines.append("     有效\"。因果组件的价值需在 MASKED 条件（屏蔽 marker）下验证。")
    lines.append("  3. E2 单独实验中全部弃权（abstention=1.0），说明异常模式跨事故高度相似")
    lines.append("     (Top-5 均为 WRCA/WHPI/VOID/RH/DNBR)，单独无法区分事故类型。")
    lines.append("  4. E3 单独仅 16.7% overall accuracy，说明逐条 LiNGAM 因果结构噪声大，")
    lines.append("     因果一致性证据的判别力有限。")
    lines.append("  5. 变量集说明：方案二使用 23 变量并集（含 WTRA），逐类经常量列审计后")
    lines.append("     实际有效变量数不同；方案一因果溯源模块使用 LOCA 14 变量子集。")
    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


if __name__ == "__main__":
    main()

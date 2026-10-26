"""因果签名构建：七组件 (M, A, G^L, G^A, E^cons, C^L, C^A)。"""
import numpy as np
from core.utils import logger, save_json
from core.data_utils import build_matrix, fit_standardizer, standardize
from core.lingam_baseline import fit_lingam, adjacency_to_edges
from core.total_effect import compute_total_effects
from core.nonlinear_baseline import build_anm_graph, anm_total_effect_ranking
from core.method_ensemble import classify_edges
from rca.rca_config import CLASS_TARGETS, CLASS_MARKERS


def compute_marker_activation(trajectory, marker, baseline_mu, baseline_sigma, k=3.0):
    """E1 前置：marker 变量是否超阈值（相对共享基线）。返回 (activated, deviation_score)。"""
    vals = trajectory[marker].values
    mu, sigma = baseline_mu[marker], baseline_sigma[marker]
    if sigma < 1e-6:
        return False, 0.0
    max_dev = np.max(np.abs(vals - mu)) / sigma
    activated = max_dev > k
    return activated, float(max_dev)


def compute_anomaly_pattern(trajectory, variables, baseline_mu, baseline_sigma):
    """A 组件：异常模式向量（各变量最大标准化偏离的绝对值）。"""
    pattern = {}
    for v in variables:
        mu = baseline_mu[v]
        sigma = baseline_sigma[v]
        if sigma < 1e-6:
            pattern[v] = 0.0
            continue
        vals = trajectory[v].values
        pattern[v] = float(np.max(np.abs(vals - mu)) / sigma)
    return pattern


def build_causal_signature(accident_type, severity, trajectory, variables,
                            baseline, train_matrix=None, mu_std=None):
    """为单条轨迹构建七组件签名。

    Args:
        accident_type: 事故类型
        severity: 严重度
        trajectory: DataFrame（主窗口）
        variables: 变量列表
        baseline: 共享基线 {"mu": {...}, "sigma": {...}}（变量名->值）
        train_matrix: 训练集矩阵（可选，用于构建因果图）
        mu_std: (mu, sigma) 标准化参数（可选）

    Returns: dict 签名
    """
    target = CLASS_TARGETS[accident_type]
    marker_rule = CLASS_MARKERS[accident_type]

    # M: marker 激活（取第一个正 marker）
    baseline_mu = dict(zip(baseline["variables"], baseline["mu"]))
    baseline_sigma = dict(zip(baseline["variables"], baseline["sigma"]))
    first_marker = marker_rule["positive"][0] if marker_rule.get("positive") else None
    m_activated, m_score = (compute_marker_activation(trajectory, first_marker, baseline_mu, baseline_sigma)
                            if first_marker else (False, 0.0))

    # A: 异常模式向量
    anomaly_pattern = compute_anomaly_pattern(trajectory, variables, baseline_mu, baseline_sigma)

    # 如果提供了训练矩阵，构建因果图
    g_l_edges = []
    g_a_edges = []
    e_cons = []
    c_l = {}
    c_a = {}

    if train_matrix is not None and mu_std is not None:
        mu, sigma = mu_std
        Z = standardize(train_matrix, mu, sigma)
        # G^L: LiNGAM 图
        B = fit_lingam(Z)
        g_l_edges = adjacency_to_edges(B, variables)
        # C^L: LiNGAM 因果效应反演
        if target in variables:
            effects = compute_total_effects([B], variables, target)
            c_l = {v: float(effects["per_variable"].get(v, {}).get("median_total_effect", 0.0))
                   for v in variables if v != target}
        # G^A: GAM-ANM 图（build_anm_graph 返回邻接矩阵，需转边列表）
        B_a = build_anm_graph(Z, variables)
        g_a_edges = adjacency_to_edges(B_a, variables)
        # C^A: GAM-ANM 因果效应（anm_ranking 是 List[Dict]）
        anm_ranking = anm_total_effect_ranking(Z, variables, target)
        c_a = {r["variable"]: float(r["abs_effect"]) for r in anm_ranking}
        # E^cons: 边方向共识
        cls = classify_edges(g_l_edges, g_a_edges)
        e_cons = cls.get("consensus", [])

    signature = {
        "accident_type": accident_type,
        "severity": severity,
        "target": target,
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


def build_class_prototype(accident_type, signatures):
    """构建事故类原型签名：聚合该类所有训练签名。"""
    target = CLASS_TARGETS[accident_type]
    marker = CLASS_MARKERS[accident_type]

    # A: 平均异常模式
    all_patterns = [s["A"] for s in signatures]
    variables = list(all_patterns[0].keys())
    avg_pattern = {v: float(np.mean([p[v] for p in all_patterns])) for v in variables}

    # G^L, G^A: 高频边（出现率 > 50%）
    from collections import Counter
    l_edge_counter = Counter()
    a_edge_counter = Counter()
    for s in signatures:
        for e in s["G_L"]:
            l_edge_counter[(e["from"], e["to"])] += 1
        for e in s["G_A"]:
            a_edge_counter[(e["from"], e["to"])] += 1
    n = len(signatures)
    g_l_proto = [{"from": f, "to": t, "freq": c/n} for (f,t), c in l_edge_counter.items() if c/n > 0.5]
    g_a_proto = [{"from": f, "to": t, "freq": c/n} for (f,t), c in a_edge_counter.items() if c/n > 0.5]

    # C^L, C^A: 平均因果效应
    c_l_proto = {v: float(np.mean([s["C_L"].get(v, 0.0) for s in signatures])) for v in variables}
    c_a_proto = {v: float(np.mean([s["C_A"].get(v, 0.0) for s in signatures])) for v in variables}

    prototype = {
        "accident_type": accident_type,
        "target": target,
        "marker": marker,
        "n_signatures": n,
        "A_prototype": avg_pattern,
        "G_L_prototype": g_l_proto,
        "G_A_prototype": g_a_proto,
        "C_L_prototype": c_l_proto,
        "C_A_prototype": c_a_proto,
    }
    return prototype

"""签名匹配器：E1/E2/E3 证据计算、融合、弃权机制。"""
import numpy as np
from core.utils import logger
from rca.rca_config import CLASS_MARKERS


def cosine_similarity(vec_a, vec_b):
    """余弦相似度。自动按变量名取交集对齐（跨类变量集可能不同）。"""
    common_keys = sorted(set(vec_a.keys()) & set(vec_b.keys()))
    if len(common_keys) == 0:
        return 0.0
    a = np.array([vec_a[k] for k in common_keys])
    b = np.array([vec_b[k] for k in common_keys])
    if np.linalg.norm(a) < 1e-12 or np.linalg.norm(b) < 1e-12:
        return 0.0
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def spearman_rank_correlation(dict_a, dict_b):
    """Spearman 秩相关（基于变量值的排序）。"""
    keys = sorted(set(dict_a.keys()) & set(dict_b.keys()))
    if len(keys) < 2:
        return 0.0
    ranks_a = np.argsort(np.argsort([dict_a[k] for k in keys]))
    ranks_b = np.argsort(np.argsort([dict_b[k] for k in keys]))
    n = len(keys)
    d = ranks_a - ranks_b
    rho = 1 - 6 * np.sum(d**2) / (n * (n**2 - 1))
    return float(rho)


def compute_e1(test_sig, prototype, k=3.0):
    """E1: marker 规则匹配。
    正 marker 应激活（异常分数 > k），负 marker 不应激活（异常分数 < k/2）。
    E1 = (正marker激活率) * (负marker未激活率)。
    """
    marker_rule = prototype.get("marker", {})
    positive = marker_rule.get("positive", [])
    negative = marker_rule.get("negative", [])
    test_anomaly = test_sig.get("A", {})

    # 正 marker 激活率
    pos_score = 0.0
    if positive:
        activated = sum(1 for m in positive if test_anomaly.get(m, 0.0) > k)
        pos_score = activated / len(positive)

    # 负 marker 未激活率
    neg_score = 1.0
    if negative:
        not_activated = sum(1 for m in negative if test_anomaly.get(m, 0.0) < k * 0.5)
        neg_score = not_activated / len(negative)

    return float(pos_score * neg_score)


def compute_e2(test_sig, prototype):
    """E2: 异常模式相似度（余弦）。"""
    return cosine_similarity(test_sig["A"], prototype["A_prototype"])


def compute_e3(test_sig, prototype):
    """E3: 因果反演证据链。
    测试签名的因果效应排序（C^L, C^A）与原型的排序相关度。
    若测试签名无 C^A（逐条 GAM-ANM 太慢），仅用 C^L。
    """
    c_l = test_sig.get("C_L", {})
    c_a = test_sig.get("C_A", {})
    cl_sim = spearman_rank_correlation(c_l, prototype["C_L_prototype"]) if c_l else 0.0
    ca_sim = spearman_rank_correlation(c_a, prototype["C_A_prototype"]) if c_a else None

    if ca_sim is not None:
        e3 = (cl_sim + ca_sim) / 2
    else:
        e3 = cl_sim
    # 归一化到 [0,1]
    e3 = (e3 + 1) / 2
    return float(e3)


def match_signature(test_sig, prototypes, w1=0.3, w2=0.5, w3=0.2,
                    abstention_threshold=0.3, margin=0.05):
    """匹配测试签名到事故类原型。

    Args:
        test_sig: 测试签名
        prototypes: {accident_type: prototype}
        w1, w2, w3: E1/E2/E3 权重
        abstention_threshold: 弃权阈值（最高分低于此值则弃权）
        margin: top-2 差距阈值（低于此值则弃权）

    Returns: {"predicted": str|None, "scores": dict, "abstained": bool, "reason": str}
    """
    scores = {}
    for at, proto in prototypes.items():
        e1 = compute_e1(test_sig, proto)
        e2 = compute_e2(test_sig, proto)
        e3 = compute_e3(test_sig, proto)
        score = w1 * e1 + w2 * e2 + w3 * e3
        scores[at] = {"score": score, "E1": e1, "E2": e2, "E3": e3}

    # 排序
    sorted_scores = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
    top_at, top_score = sorted_scores[0]
    second_score = sorted_scores[1][1]["score"] if len(sorted_scores) > 1 else 0.0

    # 弃权判断
    abstained = False
    reason = ""
    if top_score["score"] < abstention_threshold:
        abstained = True
        reason = f"最高分 {top_score['score']:.3f} < 弃权阈值 {abstention_threshold}"
    elif (top_score["score"] - second_score) < margin:
        abstained = True
        reason = f"top-2 差距 {top_score['score']-second_score:.3f} < margin {margin}"

    predicted = None if abstained else top_at
    return {
        "predicted": predicted,
        "scores": scores,
        "abstained": abstained,
        "reason": reason,
    }

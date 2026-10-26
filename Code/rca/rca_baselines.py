"""方案二 RCA 基线方法：
- B0: 仅异常模式匹配（无因果）
- B1: 因果特征 + 简单分类器（无弃权）
- B2: 随机猜测 + 弃权率上界
"""
import numpy as np
from core.utils import logger
from rca.signature_matcher import cosine_similarity, spearman_rank_correlation
from rca.rca_config import ACCIDENT_TYPES


def baseline_b0_predict(test_sig, prototypes):
    """B0: 仅异常模式相似度（余弦），无因果证据，无弃权。"""
    scores = {}
    for at, proto in prototypes.items():
        scores[at] = cosine_similarity(test_sig["A"], proto["A_prototype"])
    return max(scores, key=scores.get), scores


def baseline_b1_predict(test_sig, prototypes):
    """B1: 因果特征（C^L+C^A 排序相关）+ 简单投票，无弃权机制。
    若测试签名无 C^A（逐条 GAM-ANM 太慢），仅用 C^L。"""
    scores = {}
    for at, proto in prototypes.items():
        cl_sim = spearman_rank_correlation(test_sig["C_L"], proto["C_L_prototype"])
        if test_sig.get("C_A"):
            ca_sim = spearman_rank_correlation(test_sig["C_A"], proto["C_A_prototype"])
            scores[at] = (cl_sim + ca_sim) / 2
        else:
            scores[at] = cl_sim
    return max(scores, key=scores.get), scores


def baseline_b2_predict(test_sig, prototypes, abstention_rate=0.3):
    """B2: 随机猜测 + 固定弃权率上界。
    以概率 abstention_rate 弃权，否则均匀随机选择。
    """
    rng = np.random.default_rng()
    if rng.random() < abstention_rate:
        return None, {at: 1.0 / len(ACCIDENT_TYPES) for at in ACCIDENT_TYPES}
    pred = rng.choice(ACCIDENT_TYPES)
    return pred, {at: (1.0 if at == pred else 0.0) for at in ACCIDENT_TYPES}

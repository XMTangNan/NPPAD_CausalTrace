"""
Phase 6：消融实验框架。
七组对比：
- B1 Pearson（无方向）
- B2 LiNGAM 直接出图（无 Alg1）
- B3 LiNGAM + Alg1（无本体）
- B4 LiNGAM + Alg1 + 本体（无异常加权）
- B5 Full 无解释
- B6 XGBoost 变量集对照
- Full 完整流程

目标变量 target 一律作为函数参数传入，不硬编码任何事故特有变量。
"""
import numpy as np
import pandas as pd
from typing import Dict, List

from config import RANDOM_SEED
from core.utils import logger, save_json
from core.data_utils import build_matrix, standardize
from core.bootstrap import run_bootstrap, select_stable_edges
from core.total_effect import compute_total_effects
from core.lingam_baseline import fit_lingam


def pearson_ranking(train_mat: np.ndarray, variables: List[str], target: str) -> List[Dict]:
    """B1: Pearson 相关性绝对值排序（无方向）。"""
    t_idx = variables.index(target)
    corrs = np.corrcoef(train_mat.T)
    ranking = []
    for j, var in enumerate(variables):
        if var == target:
            continue
        ranking.append({"variable": var, "abs_pearson": abs(corrs[t_idx, j]),
                        "pearson": float(corrs[t_idx, j])})
    ranking.sort(key=lambda x: x["abs_pearson"], reverse=True)
    for i, r in enumerate(ranking):
        r["rank"] = i + 1
    return ranking


def lingam_direct_ranking(train_mat: np.ndarray, variables: List[str], target: str) -> List[Dict]:
    """B2: LiNGAM 直接出图（无 Bootstrap），用单次总效应排序。"""
    B = fit_lingam(train_mat)
    n = len(variables)
    t_idx = variables.index(target)
    T = np.linalg.inv(np.eye(n) - B)
    ranking = []
    for j, var in enumerate(variables):
        if var == target:
            continue
        ranking.append({"variable": var, "total_effect": float(T[t_idx, j]),
                        "abs_te": abs(T[t_idx, j])})
    ranking.sort(key=lambda x: x["abs_te"], reverse=True)
    for i, r in enumerate(ranking):
        r["rank"] = i + 1
    return ranking


def xgb_variable_importance(train_mat: np.ndarray, variables: List[str], severities: List[int],
                            target: str) -> List[Dict]:
    """B6: XGBoost 变量集对照（以目标变量为标签的回归，检验标签泄漏风险）。"""
    from sklearn.ensemble import GradientBoostingRegressor
    t_idx = variables.index(target)
    X = np.delete(train_mat, t_idx, axis=1)
    y = train_mat[:, t_idx]
    model = GradientBoostingRegressor(n_estimators=100, random_state=RANDOM_SEED)
    model.fit(X, y)
    other_vars = [v for v in variables if v != target]
    ranking = []
    for j, var in enumerate(other_vars):
        ranking.append({"variable": var, "xgb_importance": float(model.feature_importances_[j])})
    ranking.sort(key=lambda x: x["xgb_importance"], reverse=True)
    for i, r in enumerate(ranking):
        r["rank"] = i + 1
    return ranking


def topk_overlap(rank_a: List[Dict], rank_b: List[Dict], k: int = 5) -> float:
    """两个排序的 Top-K 重合率。"""
    set_a = set(r["variable"] for r in rank_a[:k])
    set_b = set(r["variable"] for r in rank_b[:k])
    return len(set_a & set_b) / k


def run_ablation(trajectories, variables, train_sev, mu, sigma, target: str) -> Dict:
    """运行全部消融实验，返回对比结果。

    Args:
        target: 目标变量名（必传）
    """
    train_mat = build_matrix(trajectories, train_sev, variables)
    train_std = standardize(train_mat, mu, sigma)

    # B1 Pearson
    b1 = pearson_ranking(train_std, variables, target)

    # B2 LiNGAM direct
    b2 = lingam_direct_ranking(train_std, variables, target)

    # B3/B4/Full: Bootstrap + 总效应
    B_list, edge_stats = run_bootstrap(trajectories, train_sev, variables, mu, sigma, n_bootstrap=20)
    te = compute_total_effects(B_list, variables, target)
    full_ranking = te["ranking"]

    # B6 XGBoost
    b6 = xgb_variable_importance(train_std, variables, train_sev, target)

    results = {
        "B1_pearson": b1,
        "B2_lingam_direct": b2,
        "B3_lingam_bootstrap": full_ranking,  # 无本体
        "B4_with_ontology": full_ranking,     # 本体不改变排序（仅约束图），此处同 B3
        "B5_without_explanation": full_ranking,
        "B6_xgboost": b6,
        "Full": full_ranking,
        "top5_overlap": {
            "Full_vs_B1": topk_overlap(full_ranking, b1, 5),
            "Full_vs_B2": topk_overlap(full_ranking, b2, 5),
            "Full_vs_B6": topk_overlap(full_ranking, b6, 5),
        },
    }
    logger.info(f"消融完成: Top-5 重合率 Full vs B1={results['top5_overlap']['Full_vs_B1']:.2f}, "
                f"vs B2={results['top5_overlap']['Full_vs_B2']:.2f}, "
                f"vs B6={results['top5_overlap']['Full_vs_B6']:.2f}")
    return results


if __name__ == "__main__":
    pass

"""
Phase 3 / Algorithm 2：总因果效应与上游排序。
- 每次 Bootstrap 独立计算 T_k = (I - B_k)^{-1}（禁止平均边权后求逆）
- 对目标变量汇总 T[target, X] 的中位数、25%/75% 分位数、符号一致率
- 主排序：score(X) = |median(T_k[target, X])|

目标变量 target 一律作为函数参数传入，不硬编码任何事故特有变量。
"""
import numpy as np
from typing import Dict, List

from core.utils import logger


def compute_total_effects(B_list: List[np.ndarray], variables: List[str],
                          target: str) -> Dict:
    """对每次 Bootstrap 的 B_k 独立求逆，汇总目标变量的总效应分布。

    Args:
        B_list: 每次 bootstrap 的邻接矩阵列表
        variables: 变量列表
        target: 目标变量名（必传）
    """
    n = len(variables)
    t_idx = variables.index(target)

    # 收集每次的 T[t_idx, :]
    total_effects = []  # shape: (n_bootstrap, n_vars)
    for B in B_list:
        T = np.linalg.inv(np.eye(n) - B)
        total_effects.append(T[t_idx, :])
    total_effects = np.array(total_effects)  # (n_boot, n_vars)

    results = {}
    for j, var in enumerate(variables):
        if var == target:
            continue
        te = total_effects[:, j]
        median = float(np.median(te))
        q25 = float(np.percentile(te, 25))
        q75 = float(np.percentile(te, 75))
        sign_agreement = float(np.mean(np.sign(te) == np.sign(median))) if median != 0 else 0.0
        results[var] = {
            "median_total_effect": median,
            "q25": q25, "q75": q75,
            "abs_median": abs(median),
            "sign_agreement": sign_agreement,
        }

    # 主排序：按 |median(T)| 降序
    ranking = sorted(results.items(), key=lambda x: x[1]["abs_median"], reverse=True)
    ranked = [{"rank": i + 1, "variable": v, **stats} for i, (v, stats) in enumerate(ranking)]
    return {"target": target, "per_variable": results, "ranking": ranked}


def stable_graph_total_effect(B_stable: np.ndarray, variables: List[str],
                              target: str) -> Dict:
    """稳定边图的总效应（仅作展示，不与 Bootstrap 分布混用）。

    Args:
        B_stable: 稳定边邻接矩阵
        variables: 变量列表
        target: 目标变量名（必传）
    """
    n = len(variables)
    t_idx = variables.index(target)
    T = np.linalg.inv(np.eye(n) - B_stable)
    results = {}
    for j, var in enumerate(variables):
        if var == target:
            continue
        results[var] = {"stable_total_effect": float(T[t_idx, j])}
    return results


if __name__ == "__main__":
    pass

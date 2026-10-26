"""
Phase 2 / Algorithm 1：轨迹级 Bootstrap 四指标稳定性。
- 轨迹级有放回抽样（仅训练集），N 次
- 每次拟合 LiNGAM 得 B_k
- 每条有向边统计：f_e, s_e=1/(1+σ_e), 方向一致率, 符号一致率
- 稳定边筛选：f_e ≥ 0.5 且 s_e ≥ 0.6
"""
import numpy as np
from typing import Dict, List, Tuple

from config import BOOTSTRAP_N, F_E_THRESHOLD, S_E_THRESHOLD, RANDOM_SEED
from core.utils import logger
from core.data_utils import build_matrix, standardize
from core.lingam_baseline import fit_lingam


def run_bootstrap(trajectories: Dict, train_sev: List[int], variables: List[str],
                  mu: np.ndarray, sigma: np.ndarray,
                  n_bootstrap: int = None,
                  random_seed: int = RANDOM_SEED) -> Tuple[List[np.ndarray], Dict]:
    """轨迹级 Bootstrap。
    返回 (B_list, edge_stats)。
    B_list: 每次 bootstrap 的邻接矩阵列表。
    edge_stats: {edge_key: {f_e, s_e, dir_agreement, sign_agreement, weights}}

    Args:
        n_bootstrap: 重采样次数；为 None 时取 config.BOOTSTRAP_N
        random_seed: 随机种子
    """
    if n_bootstrap is None:
        n_bootstrap = BOOTSTRAP_N

    rng = np.random.default_rng(random_seed)
    n_vars = len(variables)
    B_list = []
    # 记录每条有向边 (i,j) 的出现次数和权重
    edge_count = np.zeros((n_vars, n_vars), dtype=int)
    edge_weights = {f"{j}->{i}": [] for i in range(n_vars) for j in range(n_vars) if i != j}

    for k in range(n_bootstrap):
        # 轨迹级有放回抽样
        sampled = rng.choice(train_sev, size=len(train_sev), replace=True)
        mat = build_matrix(trajectories, list(sampled), variables)
        mat_std = standardize(mat, mu, sigma)
        B = fit_lingam(mat_std)
        B_list.append(B)
        for i in range(n_vars):
            for j in range(n_vars):
                if i != j and abs(B[i, j]) > 1e-10:
                    edge_count[i, j] += 1
                    edge_weights[f"{j}->{i}"].append(B[i, j])

    # 计算四指标
    edge_stats = {}
    for i in range(n_vars):
        for j in range(n_vars):
            if i == j:
                continue
            key = f"{variables[j]}->{variables[i]}"
            count = edge_count[i, j]
            f_e = count / n_bootstrap
            weights = edge_weights[f"{j}->{i}"]
            if len(weights) > 0:
                sigma_e = np.std(weights, ddof=1) if len(weights) > 1 else 0.0
                s_e = 1.0 / (1.0 + sigma_e)
                signs = np.sign(weights)
                sign_agreement = float(np.mean(signs == np.sign(np.mean(weights))))
            else:
                s_e = 0.0
                sign_agreement = 0.0

            # 方向一致率：该变量对至少出现一条边的重复数中，X→Y 占比
            rev_count = edge_count[j, i]
            total_dir = count + rev_count
            dir_agreement = count / total_dir if total_dir > 0 else 0.0

            edge_stats[key] = {
                "from": variables[j], "to": variables[i],
                "f_e": float(f_e), "s_e": float(s_e),
                "dir_agreement": float(dir_agreement),
                "sign_agreement": float(sign_agreement),
                "count": int(count),
            }

    return B_list, edge_stats


def select_stable_edges(edge_stats: Dict, f_thresh: float = F_E_THRESHOLD,
                        s_thresh: float = S_E_THRESHOLD) -> List[Dict]:
    """筛选稳定边：f_e ≥ f_thresh 且 s_e ≥ s_thresh。"""
    stable = []
    for key, stat in edge_stats.items():
        if stat["f_e"] >= f_thresh and stat["s_e"] >= s_thresh:
            stable.append(stat)
    stable.sort(key=lambda x: x["f_e"], reverse=True)
    return stable


def build_stable_B(stable_edges: List[Dict], variables: List[str]) -> np.ndarray:
    """从稳定边列表构建稀疏邻接矩阵（边权取 f_e 作为展示权重；真实总效应来自各次独立求逆）。"""
    n = len(variables)
    B = np.zeros((n, n))
    var_idx = {v: i for i, v in enumerate(variables)}
    for e in stable_edges:
        i = var_idx[e["to"]]
        j = var_idx[e["from"]]
        B[i, j] = e["f_e"]
    return B


if __name__ == "__main__":
    pass

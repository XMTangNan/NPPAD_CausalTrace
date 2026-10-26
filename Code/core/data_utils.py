"""
通用数据工具：窗口截取、常量列识别、矩阵构建、标准化、轨迹划分、审计诊断。

所有函数均不硬编码任何事故类型特有的变量名、目标变量或冻结关系；
时间窗口、严重度分组等均通过参数传入（默认值取自全局 config）。
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple

from config import (
    WINDOW_START, WINDOW_END, TIME_STEP, WINDOW_POINTS,
    TRAIN_RATIO, RANDOM_SEED,
)
from core.utils import logger, log_error


def slice_window(df: pd.DataFrame,
                 window_start: float = WINDOW_START,
                 window_end: float = WINDOW_END,
                 time_step: float = TIME_STEP,
                 window_points: int = WINDOW_POINTS) -> pd.DataFrame:
    """截取主时间窗口，返回恰好 window_points 行。

    要求 df 含 "TIME" 列且严格递增、步长等于 time_step。
    """
    mask = (df["TIME"] >= window_start - 1e-6) & (df["TIME"] <= window_end + 1e-6)
    win = df.loc[mask].reset_index(drop=True)
    expected_times = np.arange(window_start, window_end + time_step / 2, time_step)
    assert len(win) == window_points, f"窗口点数 {len(win)} != {window_points}"
    assert np.allclose(win["TIME"].values, expected_times, atol=1e-6), "窗口时间戳不匹配"
    return win


def find_constant_columns(dfs: Dict, variables: List[str]) -> List[str]:
    """识别在所有轨迹中全程为常量的列。"""
    constant_cols = []
    for col in variables:
        vals = np.concatenate([dfs[sev][col].values for sev in dfs])
        if np.nanstd(vals) < 1e-10:
            constant_cols.append(col)
    return constant_cols


def build_matrix(trajectories: Dict, severities: List[int],
                 variables: List[str]) -> np.ndarray:
    """堆叠多条轨迹的窗口数据为 (N_timepoints, n_vars) 矩阵。"""
    blocks = [trajectories[s][variables].values for s in severities]
    return np.vstack(blocks)


def fit_standardizer(train_matrix: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """在训练集上估计 μ、σ（逐变量）。"""
    mu = train_matrix.mean(axis=0)
    sigma = train_matrix.std(axis=0)
    sigma[sigma < 1e-12] = 1.0  # 防除零
    return mu, sigma


def standardize(matrix: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """应用训练集标准化参数。"""
    return (matrix - mu) / sigma


def split_trajectories(severities: List[int],
                       train_ratio: float = TRAIN_RATIO,
                       random_seed: int = RANDOM_SEED) -> Tuple[List[int], List[int]]:
    """轨迹级划分，返回 (train_severities, val_severities)。

    Args:
        severities: 待划分的严重度/轨迹编号列表
        train_ratio: 训练集比例（默认 0.8）
        random_seed: 随机种子（默认 42）
    """
    rng = np.random.default_rng(random_seed)
    perm = rng.permutation(severities)
    n_train = int(len(severities) * train_ratio)
    train = sorted(perm[:n_train].tolist())
    val = sorted(perm[n_train:].tolist())
    logger.info(f"划分: train={len(train)} (e.g. {train[:5]}...), val={len(val)}")
    return train, val


# ==================== 审计诊断 ====================
def non_gaussian_diagnostics(matrix: np.ndarray, variables: List[str]) -> pd.DataFrame:
    """非高斯性诊断：偏度、超额峰度、Jarque-Bera。

    注意：堆叠时间点非独立，JB p 值不作正式显著性解释。
    """
    from scipy import stats
    rows = []
    for i, var in enumerate(variables):
        x = matrix[:, i]
        skew = stats.skew(x)
        kurt = stats.kurtosis(x)  # excess kurtosis
        jb_stat, jb_p = stats.jarque_bera(x)
        rows.append({"variable": var, "skewness": skew, "excess_kurtosis": kurt,
                     "jb_stat": jb_stat, "jb_pvalue": jb_p})
    return pd.DataFrame(rows)


def severity_heterogeneity(trajectories: Dict, variables: List[str],
                           severity_groups: Dict) -> Dict:
    """严重度异质性审计：按 severity_groups 分组统计各变量的均值范围。

    Args:
        severity_groups: {group_name: (lo, hi)} 严重度范围映射
    """
    report = {}
    for gname, (lo, hi) in severity_groups.items():
        sevs = [s for s in trajectories if lo <= s <= hi]
        report[gname] = {"n_trajectories": len(sevs), "variables": {}}
        for var in variables:
            vals = np.concatenate([trajectories[s][var].values for s in sevs])
            report[gname]["variables"][var] = {
                "mean": float(np.mean(vals)), "std": float(np.std(vals)),
                "min": float(np.min(vals)), "max": float(np.max(vals)),
            }
    return report


def run_audit(trajectories: Dict, variables: List[str],
              train_sev: List[int],
              window_points: int = WINDOW_POINTS,
              severity_groups: Dict = None) -> Dict:
    """执行完整数据审计，返回审计报告字典。

    Args:
        trajectories: {severity: window_df}
        variables: 入模变量列表
        train_sev: 训练集严重度列表
        window_points: 窗口点数（用于审计报告记录）
        severity_groups: 严重度分组映射；为 None 时不做异质性审计
    """
    train_mat = build_matrix(trajectories, train_sev, variables)
    mu, sigma = fit_standardizer(train_mat)
    train_std = standardize(train_mat, mu, sigma)

    audit = {
        "n_trajectories": len(trajectories),
        "n_variables": len(variables),
        "variables": variables,
        "window_points": window_points,
        "n_train_timepoints": train_mat.shape[0],
        "non_gaussian": non_gaussian_diagnostics(train_std, variables).to_dict("records"),
        "train_mu": mu.tolist(),
        "train_sigma": sigma.tolist(),
    }
    if severity_groups is not None:
        audit["severity_heterogeneity"] = severity_heterogeneity(
            trajectories, variables, severity_groups
        )
    return audit


if __name__ == "__main__":
    pass

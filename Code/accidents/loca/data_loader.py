"""
LOCA 事故数据加载与审计。
- 按列名读取 LOCA CSV（禁止用 Table 2 ID 做 iloc）
- TIME 严格递增、步长 10s 校验
- 主窗口 t=300~1500s（121 点）截取
- 常量列识别与剔除
- 轨迹级 80/20 划分
- 全局 Z-score 标准化（训练集参数复用）
- 非高斯性诊断 + 严重度异质性审计
"""
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Tuple

from config import (
    WINDOW_START, WINDOW_END, TIME_STEP, WINDOW_POINTS,
    TRAIN_RATIO, RANDOM_SEED, NORMAL_DIR,
)
from accidents.loca.config import LOCA_DIR, PRIOR_VARIABLES, LOCA1_STEADY_START, LOCA1_STEADY_END
from core.data_utils import slice_window, find_constant_columns
from core.utils import logger, log_error


def load_loca_trajectory(severity: int) -> pd.DataFrame:
    """加载单条 LOCA 轨迹（severity = 文件名编号）。

    Args:
        severity: 轨迹编号（1~100）。

    Returns:
        原始 CSV DataFrame，含 TIME 列。

    Raises:
        AssertionError: TIME 非严格递增或步长非 10s。
    """
    path = LOCA_DIR / f"{severity}.csv"
    df = pd.read_csv(path)
    # 校验 TIME 递增 & 步长
    time = df["TIME"].values
    diffs = np.diff(time)
    assert np.all(diffs > 0), f"LOCA_{severity}: TIME 非严格递增"
    assert np.allclose(diffs, TIME_STEP, atol=1e-6), f"LOCA_{severity}: 步长非 10s"
    return df


def load_all_loca() -> Tuple[Dict[int, pd.DataFrame], List[str]]:
    """加载全部 100 条 LOCA 轨迹，返回 {severity: window_df} 和有效变量列表。

    主窗口截取使用全局 WINDOW_START~WINDOW_END，常量列基于 PRIOR_VARIABLES 识别。

    Returns:
        trajectories: {severity: 窗口 DataFrame}
        valid_vars: 剔除常量列后的变量列表。
    """
    logger.info(f"加载 LOCA 轨迹 1~100 ...")
    trajectories = {}
    for sev in range(1, 101):
        df = load_loca_trajectory(sev)
        trajectories[sev] = slice_window(df, WINDOW_START, WINDOW_END, TIME_STEP, WINDOW_POINTS)

    # 常量列剔除
    constant_cols = find_constant_columns(trajectories, PRIOR_VARIABLES)
    if constant_cols:
        logger.info(f"剔除常量列: {constant_cols}")
        log_error("DATA", f"常量列剔除 {len(constant_cols)} 个",
                  "LOCA 下部分参数全程为常量", f"剔除: {constant_cols}", "resolved")
    valid_vars = [v for v in PRIOR_VARIABLES if v not in constant_cols]
    logger.info(f"入模变量 ({len(valid_vars)}): {valid_vars}")
    return trajectories, valid_vars


def load_normal() -> pd.DataFrame:
    """加载 Normal 轨迹（功率漂移，非稳态）。

    Returns:
        Normal/1.csv 的原始 DataFrame，含 TIME 列。
    """
    path = NORMAL_DIR / "1.csv"
    df = pd.read_csv(path)
    time = df["TIME"].values
    assert np.all(np.diff(time) > 0), "Normal: TIME 非递增"
    return df


def load_loca1_steady_segment() -> pd.DataFrame:
    """加载 LOCA_1 的 100% 功率准稳态段。

    Normal 轨迹是 100%→40% 降负荷过程，与 LOCA 事故的 100% 功率演化不匹配，
    无法用 Normal 建阈值。LOCA_1（1% 小破口）在 t=520~2030s 恢复到 100% 功率
    稳态，所有热工变量 CV<0.5%，作为准 Normal 基准。

    Returns:
        LOCA_1 稳态段 DataFrame（含 TIME 列）。
    """
    path = LOCA_DIR / "1.csv"
    df = pd.read_csv(path)
    mask = (df["TIME"] >= LOCA1_STEADY_START) & (df["TIME"] <= LOCA1_STEADY_END)
    seg = df.loc[mask].reset_index(drop=True)
    logger.info(f"LOCA_1 准稳态段: t={LOCA1_STEADY_START}~{LOCA1_STEADY_END}s, "
                f"{len(seg)} 个点, PWNT≈{seg['PWNT'].mean():.1f}%, P≈{seg['P'].mean():.1f} bar")
    return seg


def split_trajectories(severities: List[int]) -> Tuple[List[int], List[int]]:
    """轨迹级 80/20 划分，返回 (train_severities, val_severities)。"""
    rng = np.random.default_rng(RANDOM_SEED)
    perm = rng.permutation(severities)
    n_train = int(len(severities) * TRAIN_RATIO)
    train = sorted(perm[:n_train].tolist())
    val = sorted(perm[n_train:].tolist())
    logger.info(f"划分: train={len(train)} (e.g. {train[:5]}...), val={len(val)}")
    return train, val


def build_matrix(trajectories: Dict[int, pd.DataFrame], severities: List[int],
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


# ==================== 审计诊断 ====================
def non_gaussian_diagnostics(matrix: np.ndarray, variables: List[str]) -> pd.DataFrame:
    """非高斯性诊断：偏度、超额峰度、Jarque-Bera。
    注意：堆叠时间点非独立，JB p 值不作正式显著性解释。"""
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


def severity_heterogeneity(trajectories: Dict[int, pd.DataFrame], variables: List[str]) -> Dict:
    """严重度异质性审计：低/中/高三组各变量的均值范围。"""
    from config import SEVERITY_GROUPS
    report = {}
    for gname, (lo, hi) in SEVERITY_GROUPS.items():
        sevs = [s for s in trajectories if lo <= s <= hi]
        report[gname] = {"n_trajectories": len(sevs), "variables": {}}
        for var in variables:
            vals = np.concatenate([trajectories[s][var].values for s in sevs])
            report[gname]["variables"][var] = {
                "mean": float(np.mean(vals)), "std": float(np.std(vals)),
                "min": float(np.min(vals)), "max": float(np.max(vals)),
            }
    return report


def run_audit(trajectories: Dict[int, pd.DataFrame], variables: List[str],
              train_sev: List[int]) -> Dict:
    """执行完整数据审计，返回审计报告字典。"""
    train_mat = build_matrix(trajectories, train_sev, variables)
    mu, sigma = fit_standardizer(train_mat)
    train_std = standardize(train_mat, mu, sigma)

    audit = {
        "n_trajectories": len(trajectories),
        "n_variables": len(variables),
        "variables": variables,
        "window_points": WINDOW_POINTS,
        "n_train_timepoints": train_mat.shape[0],
        "non_gaussian": non_gaussian_diagnostics(train_std, variables).to_dict("records"),
        "severity_heterogeneity": severity_heterogeneity(trajectories, variables),
        "train_mu": mu.tolist(),
        "train_sigma": sigma.tolist(),
    }
    return audit


if __name__ == "__main__":
    trajs, vars_ = load_all_loca()
    train, val = split_trajectories(list(trajs.keys()))
    audit = run_audit(trajs, vars_, train)
    from core.utils import save_json
    save_json(audit, "audit_report")
    print(f"审计完成: {len(vars_)} 变量, {audit['n_train_timepoints']} 训练时间点")

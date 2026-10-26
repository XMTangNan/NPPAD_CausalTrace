"""
Phase 5 / Algorithm 3：一致性比较（探索性）。
- 目标关系 source_var↔target：用总因果效应 T[target, source_var] 预测验证集上 target 的条件均值差，
  并与观测到的条件均值差比较。
- 双划分：随机 80/20 划分 + 严重度范围划分（低中训练 / 高验证）。
- 注意：这是探索性检查，不是因果效应验证。若 LiNGAM 选 target→source_var 方向，
  T[target, source_var] 可能为 0，Δtarget_pred≈0，如实报告。

目标变量 target 与源变量 source_var 均由参数传入，不硬编码任何事故特有变量。
"""
import numpy as np
from typing import Dict, List

from config import RANDOM_SEED
from core.utils import logger, save_json
from core.data_utils import (
    build_matrix, standardize, fit_standardizer, split_trajectories,
)
from core.bootstrap import run_bootstrap
from core.total_effect import compute_total_effects

# 探索性检查使用的分位数邻域半宽（标准化尺度）
EPSILON = 0.1


def _conditional_mean(x: np.ndarray, y: np.ndarray, anchor: float,
                      eps: float = EPSILON) -> tuple:
    """计算 y 在 x 处于 anchor ± eps 范围内的条件均值。

    Returns: (mean_y, n_samples)。若样本数为 0，返回 (nan, 0)。
    """
    mask = np.abs(x - anchor) < eps
    n = int(mask.sum())
    if n == 0:
        return float("nan"), 0
    return float(np.mean(y[mask])), n


def consistency_check(trajectories, variables, mu, sigma,
                      train_sev, val_sev, total_effect_result,
                      target: str, source_var: str) -> Dict:
    """一致性检查：比较总因果效应预测的 Δtarget 与观测到的 Δtarget。

    Args:
        trajectories: {severity: window_df}
        variables: 变量列表
        mu, sigma: 训练集标准化参数
        train_sev, val_sev: 训练/验证严重度列表
        total_effect_result: compute_total_effects 的输出
        target: 目标变量名（被预测的变量）
        source_var: 源变量名（用于预测 target 的变量）

    Returns:
        包含 τ、Q1/Q3、Δtarget_pred、Δtarget_obs、Error 等的字典。
    """
    target_idx = variables.index(target)
    source_idx = variables.index(source_var)

    # 1. 从 total_effect_result 取 τ = T[target, source_var] 的中位数（标准化尺度）
    per_var = total_effect_result.get("per_variable", {})
    source_stats = per_var.get(source_var)
    if source_stats is None:
        logger.warning(f"total_effect_result 中无 {source_var}，τ 设为 0")
        tau = 0.0
    else:
        tau = float(source_stats["median_total_effect"])

    # 2. 验证集上构建标准化矩阵
    val_mat = build_matrix(trajectories, val_sev, variables)
    val_std = standardize(val_mat, mu, sigma)
    source_std = val_std[:, source_idx]
    target_std = val_std[:, target_idx]

    # 3. a1 = Q1(source_std), a2 = Q3(source_std)
    a1 = float(np.percentile(source_std, 25))
    a2 = float(np.percentile(source_std, 75))

    # 4. Δtarget_pred = (a2 - a1) * τ
    delta_target_pred = (a2 - a1) * tau

    # 5. Δtarget_obs = E[target_std | source_std near a2] - E[target_std | source_std near a1]
    mean_target_at_a2, n_a2 = _conditional_mean(source_std, target_std, a2, EPSILON)
    mean_target_at_a1, n_a1 = _conditional_mean(source_std, target_std, a1, EPSILON)

    if n_a1 == 0 or n_a2 == 0:
        logger.warning(
            f"验证集中分位数邻域样本不足: n(Q1)={n_a1}, n(Q3)={n_a2}, "
            f"ε={EPSILON}。Δtarget_obs 设为 nan。"
        )
        delta_target_obs = float("nan")
        error = float("nan")
    else:
        delta_target_obs = mean_target_at_a2 - mean_target_at_a1
        error = abs(delta_target_pred - delta_target_obs)

    # 预测方向与观测方向是否一致
    if np.isnan(delta_target_obs):
        direction_agreement = None
    else:
        direction_agreement = bool(np.sign(delta_target_pred) == np.sign(delta_target_obs))

    result = {
        "target_relation": f"{source_var}<->{target}",
        "tau": tau,
        f"Q1_{source_var}_std": a1,
        f"Q3_{source_var}_std": a2,
        f"delta_{source_var}": a2 - a1,
        f"delta_{target}_pred": delta_target_pred,
        f"mean_{target}_at_Q1": mean_target_at_a1,
        f"mean_{target}_at_Q3": mean_target_at_a2,
        "n_at_Q1": n_a1,
        "n_at_Q3": n_a2,
        f"delta_{target}_obs": delta_target_obs,
        "error": error,
        "direction_agreement": direction_agreement,
        "tau_is_zero": abs(tau) < 1e-12,
        "note": (
            f"探索性检查：若 τ≈0，说明 LiNGAM 倾向 {target}->{source_var} 方向，"
            f"{source_var} 对 {target} 无总因果效应。"
        ),
    }
    logger.info(
        f"[consistency] τ={tau:+.4f} | Δ{target}_pred={delta_target_pred:+.4f} | "
        f"Δ{target}_obs={delta_target_obs:+.4f} | Error={error:.4f} | "
        f"dir_agree={direction_agreement}"
    )
    return result


def _run_single_split(trajectories, variables, train_sev, val_sev, target, source_var,
                      n_bootstrap: int = 20) -> Dict:
    """对单个划分运行完整流程：标准化 -> bootstrap -> 总效应 -> 一致性检查。"""
    # 训练集标准化参数
    train_mat = build_matrix(trajectories, train_sev, variables)
    mu, sigma = fit_standardizer(train_mat)

    # Bootstrap + 总效应
    logger.info(f"Bootstrap (n={n_bootstrap}) on {len(train_sev)} train trajectories ...")
    B_list, _ = run_bootstrap(trajectories, train_sev, variables, mu, sigma,
                              n_bootstrap=n_bootstrap)
    te_result = compute_total_effects(B_list, variables, target)

    # 一致性检查
    result = consistency_check(trajectories, variables, mu, sigma,
                               train_sev, val_sev, te_result, target, source_var)
    result["train_sev"] = train_sev
    result["val_sev"] = val_sev
    return result


def dual_split_consistency(trajectories, variables, target: str, source_var: str,
                           n_bootstrap: int = 20,
                           severity_split_lo: int = 1, severity_split_hi: int = 66,
                           val_lo: int = 67, val_hi: int = 100) -> Dict:
    """双划分一致性比较。

    划分一：随机轨迹 80/20（使用 split_trajectories）。
    划分二：严重度范围划分，[severity_split_lo, severity_split_hi] 训练 / [val_lo, val_hi] 验证。

    Args:
        target: 目标变量名
        source_var: 源变量名
        n_bootstrap: bootstrap 次数
        severity_split_lo/hi: 严重度划分训练集范围
        val_lo/hi: 严重度划分验证集范围

    Returns:
        {"random_split": ..., "severity_split": ...}
    """
    # 划分一：随机 80/20
    train_rand, val_rand = split_trajectories(list(trajectories.keys()))
    logger.info("=== 划分一：随机轨迹 80/20 ===")
    res_random = _run_single_split(trajectories, variables, train_rand, val_rand,
                                   target, source_var, n_bootstrap=n_bootstrap)

    # 划分二：严重度范围
    train_sev = [s for s in trajectories if severity_split_lo <= s <= severity_split_hi]
    val_sev = [s for s in trajectories if val_lo <= s <= val_hi]
    logger.info(
        f"=== 划分二：严重度范围 (train {severity_split_lo}-{severity_split_hi}, "
        f"val {val_lo}-{val_hi}) ==="
    )
    res_severity = _run_single_split(trajectories, variables, train_sev, val_sev,
                                     target, source_var, n_bootstrap=n_bootstrap)

    return {
        "random_split": res_random,
        "severity_split": res_severity,
    }


if __name__ == "__main__":
    pass

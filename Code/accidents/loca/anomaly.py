"""
Phase 3.5：LOCA 事故异常变量标记。

【2026-10-25 修订】基准数据源从 Normal 轨迹改为 LOCA_1 准稳态段。
原因：Normal 轨迹是 100%→40% 降负荷过程，与 LOCA 事故的 100% 功率演化
不匹配，用 Normal 建的阈值去判 LOCA 异常会导致功率相关变量全部误报。
改用 LOCA_1（1% 小破口）的 100% 功率恢复段（t=520~2030s）作为准 Normal，
所有热工变量 CV<0.5%，功率水平与事故初始功率一致。

- 基于 LOCA_1 准稳态段建立 (μ, σ) 阈值（全局，不按时间分段）
- 排除故障/安全系统变量（WLR/WECS/WHPI，正常应为 0）
- 对 LOCA 事故窗口逐变量计算异常程度与异常持续比例
- 增强排序：enhanced_score = |median_T| * (1 + α * anomaly_degree)，α 敏感性分析

注意：主排序始终用纯总因果效应，异常加权仅作探索性消融。
      阈值基于 LOCA_1 恢复段，非真正正常运行数据，需在报告中声明。
"""
import numpy as np
from typing import Dict, List, Tuple

from config import (
    ANOMALY_SIGMA, ANOMALY_ALPHA, ALPHA_GRID, STATIONARY_CV_THRESHOLD,
)
from accidents.loca.config import FAULT_SYSTEM_VARS
from core.utils import logger, save_json


def build_thresholds_from_loca1_steady(steady_df, variables: List[str]) -> Tuple[Dict, bool]:
    """基于 LOCA_1 准稳态段建立全局 (μ, σ) 阈值。

    【2026-10-25 修订】不再使用 Normal 轨迹分段，改用 LOCA_1 稳态段。
    整段为 100% 功率稳态，不按时间分段，直接用全局均值和标准差。

    判据：
    1. 排除故障/安全系统变量（FAULT_SYSTEM_VARS），正常运行时恒为 0
    2. 变异系数 CV = std/|mean| < STATIONARY_CV_THRESHOLD 视为近似稳态
    3. 排除 σ<1e-6 的近似常量变量（无法估计正常波动范围）

    Args:
        steady_df: LOCA_1 准稳态段 DataFrame（含 TIME 列）
        variables: 待建阈值的变量列表

    Returns:
        thresholds: {var: {"mu": float, "sigma": float, "cv": float, "n_points": int}}
        module_exit: 可信变量数 < 3 时为 True
    """
    thresholds = {}
    excluded_fault = []
    excluded_nonstationary = []

    for var in variables:
        # 排除故障/安全系统变量
        if var in FAULT_SYSTEM_VARS:
            excluded_fault.append(var)
            continue

        series = steady_df[var].values
        mean = float(np.mean(series))
        std = float(np.std(series))

        # 变异系数判稳态
        if abs(mean) < 1e-12:
            # 均值接近 0，用绝对标准差判
            cv = std if std > 1e-12 else 0.0
            if cv > STATIONARY_CV_THRESHOLD:
                excluded_nonstationary.append(var)
                continue
        else:
            cv = std / abs(mean)
            if cv > STATIONARY_CV_THRESHOLD:
                excluded_nonstationary.append(var)
                continue

        sigma_safe = std if std > 1e-12 else 1e-12
        # 若标准差极小（变量在稳态段近似常量），无法估计正常波动范围，不建阈值
        if std < 1e-6:
            excluded_nonstationary.append(var)
            continue
        thresholds[var] = {
            "mu": mean,
            "sigma": sigma_safe,
            "cv": cv,
            "n_points": len(series),
        }

    n_credible = len(thresholds)
    module_exit = n_credible < 3

    logger.info(f"[anomaly] LOCA_1 准稳态段建阈值: {n_credible}/{len(variables)} 变量有可信阈值")
    logger.info(f"  排除故障变量 ({len(excluded_fault)}): {excluded_fault}")
    logger.info(f"  排除非稳态变量 ({len(excluded_nonstationary)}): {excluded_nonstationary}")
    for var, info in thresholds.items():
        logger.info(f"  {var}: μ={info['mu']:.4g}  σ={info['sigma']:.4g}  CV={info['cv']*100:.3f}%  n={info['n_points']}")

    if module_exit:
        logger.warning(f"[anomaly] 可信变量过少 ({n_credible} < 3)，阈值模块退出 (module_exit=True)")
    return thresholds, module_exit


def compute_anomaly_scores(trajectories: Dict, variables: List[str],
                           thresholds: Dict) -> Dict:
    """对 LOCA 事故窗口逐变量计算异常程度与异常持续时间比例。

    anomaly_degree(t) = max(0, |X(t) - μ| - k·σ) / σ
    anomaly_degree    = 时间平均（再对轨迹取平均）
    anomaly_duration_ratio = 异常时间点（degree>0）占比

    阈值为全局（不按时间分段），来自 LOCA_1 准稳态段。
    仅对有阈值的变量计算；无阈值变量 anomaly_degree=0。

    Args:
        trajectories: {severity: window_df}
        variables: 变量列表
        thresholds: build_thresholds_from_loca1_steady 的输出

    Returns:
        {var: {"anomaly_degree", "anomaly_duration_ratio", "has_threshold", "n_trajectories"}}
    """
    k = ANOMALY_SIGMA
    scores = {}

    for var in variables:
        if var not in thresholds:
            scores[var] = {
                "anomaly_degree": 0.0,
                "anomaly_duration_ratio": 0.0,
                "has_threshold": False,
                "n_trajectories": 0,
            }
            continue

        mu = thresholds[var]["mu"]
        sigma = thresholds[var]["sigma"]
        traj_degrees = []
        traj_durations = []

        for sev, df in trajectories.items():
            x = df[var].values
            dev = np.abs(x - mu) - k * sigma
            degrees = np.maximum(0.0, dev) / sigma
            traj_degrees.append(float(np.mean(degrees)))
            traj_durations.append(float(np.mean(degrees > 0)))

        scores[var] = {
            "anomaly_degree": float(np.mean(traj_degrees)),
            "anomaly_duration_ratio": float(np.mean(traj_durations)),
            "has_threshold": True,
            "n_trajectories": len(traj_degrees),
        }

    return scores


def enhanced_ranking(base_ranking: List[Dict], anomaly_scores: Dict) -> Dict:
    """增强排序：enhanced_score = abs_median * (1 + α * anomaly_degree)。

    对 α ∈ ALPHA_GRID 做敏感性分析，返回各 α 下排序及与主排序的 Kendall τ。
    主排序 = base_ranking（纯总效应，按 abs_median 降序）。

    Args:
        base_ranking: compute_total_effects 的 ranking 字段
        anomaly_scores: compute_anomaly_scores 的输出

    Returns:
        {alpha: {"ranking": [...], "kendall_tau": float}}
    """
    from scipy.stats import kendalltau

    base_order = [r["variable"] for r in base_ranking]
    base_rank_map = {v: i for i, v in enumerate(base_order)}
    n = len(base_order)

    results = {}
    for a in ALPHA_GRID:
        enhanced = []
        for r in base_ranking:
            var = r["variable"]
            ad = anomaly_scores.get(var, {}).get("anomaly_degree", 0.0)
            enh_score = r["abs_median"] * (1.0 + a * ad)
            enhanced.append({**r, "anomaly_degree": ad, "enhanced_score": enh_score})

        enhanced.sort(key=lambda x: x["enhanced_score"], reverse=True)
        for i, item in enumerate(enhanced):
            item["enhanced_rank"] = i + 1

        # Kendall τ：主排序名次 vs 增强排序名次（同一变量集上的两种名次）
        enh_rank_map = {item["variable"]: i for i, item in enumerate(enhanced)}
        base_ranks = np.array([base_rank_map[v] for v in base_order])
        enh_ranks = np.array([enh_rank_map[v] for v in base_order])
        tau, _ = kendalltau(base_ranks, enh_ranks)

        results[a] = {
            "ranking": enhanced,
            "kendall_tau": float(tau),
        }

    return results


if __name__ == "__main__":
    from accidents.loca.data_loader import (
        load_loca1_steady_segment, load_all_loca,
        split_trajectories, build_matrix, fit_standardizer,
    )
    from bootstrap import run_bootstrap
    from total_effect import compute_total_effects

    logger.info("=== Phase 3.5 异常变量标记（修订版：LOCA_1 准稳态段）===")

    # 1. 加载数据
    steady_df = load_loca1_steady_segment()  # LOCA_1 的 100% 功率稳态段
    trajs, variables = load_all_loca()
    logger.info(f"LOCA_1 准稳态段 {len(steady_df)} 点; LOCA {len(trajs)} 轨迹, {len(variables)} 变量")

    # 2. 建阈值（基于 LOCA_1 稳态段，排除故障变量）
    thresholds, module_exit = build_thresholds_from_loca1_steady(steady_df, variables)
    save_json({
        "thresholds": thresholds,
        "module_exit": module_exit,
        "n_credible": len(thresholds),
        "n_variables": len(variables),
        "baseline": "LOCA_1 准稳态段 (t=520~2030s, 100% 功率)",
        "note": "Normal 轨迹功率 100%→40% 与 LOCA 不匹配，改用 LOCA_1 恢复段",
    }, "anomaly_thresholds")

    # 3. 主排序（始终用纯总效应，与阈值无关）
    train, val = split_trajectories(list(trajs.keys()))
    train_mat = build_matrix(trajs, train, variables)
    mu, sigma = fit_standardizer(train_mat)
    logger.info("运行 Bootstrap（n=20）获取主排序 ...")
    B_list, _ = run_bootstrap(trajs, train, variables, mu, sigma, n_bootstrap=20)
    te = compute_total_effects(B_list, variables)
    base_ranking = te["ranking"]

    if module_exit:
        logger.warning("可信变量不足，跳过异常增强排序，仅输出主排序。")
        save_json({"base_ranking": base_ranking, "module_exit": True},
                  "anomaly_ranking_comparison")
    else:
        # 4. 计算异常度
        anomaly_scores = compute_anomaly_scores(trajs, variables, thresholds)
        save_json(anomaly_scores, "anomaly_scores")

        logger.info("异常度 Top-5:")
        sorted_ad = sorted(anomaly_scores.items(),
                           key=lambda x: x[1]["anomaly_degree"], reverse=True)
        for var, s in sorted_ad[:5]:
            logger.info(f"  {var}: degree={s['anomaly_degree']:.3f}  "
                        f"duration={s['anomaly_duration_ratio']:.3f}")

        # 5. 增强排序对比
        enh = enhanced_ranking(base_ranking, anomaly_scores)
        save_json({
            "base_ranking": base_ranking,
            "anomaly_scores": anomaly_scores,
            "enhanced": enh,
            "module_exit": False,
        }, "anomaly_ranking_comparison")

        logger.info("=== 主排序 vs 增强排序（α=0.3）Top-10 ===")
        main = enh[ANOMALY_ALPHA]
        for b, e in zip(base_ranking[:10], main["ranking"][:10]):
            logger.info(f"  主#{b['rank']:2d} {b['variable']:<6} |T|={b['abs_median']:.3f}  "
                        f"=> 增#{e['enhanced_rank']:2d} {e['variable']:<6} "
                        f"ad={e['anomaly_degree']:.3f} score={e['enhanced_score']:.3f}")

        logger.info(f"Kendall τ (α={ANOMALY_ALPHA}): {main['kendall_tau']:.3f}")

        logger.info("=== α 敏感性（Kendall τ）===")
        for a in ALPHA_GRID:
            logger.info(f"  α={a}: τ={enh[a]['kendall_tau']:.3f}")

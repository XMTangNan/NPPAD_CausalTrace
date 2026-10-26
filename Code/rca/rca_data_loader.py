"""跨事故 RCA 数据加载器。
- 逐事故加载 + 逐类常量列审计
- 分层划分（sev-1 固定训练集）
- 共享基线（三类事故 sev-1 池化）
"""
import numpy as np, pandas as pd
from pathlib import Path
from config import WINDOW_START, WINDOW_END, TIME_STEP, WINDOW_POINTS, RANDOM_SEED, TRAIN_RATIO
from core.utils import logger, log_error
from core.data_utils import slice_window, find_constant_columns, build_matrix, fit_standardizer, standardize, split_trajectories
from rca.rca_config import (ACCIDENT_TYPES, ACCIDENT_DIRS, VARIABLE_UNION_22,
                            CLASS_VARIABLES, CLASS_TARGETS, CLASS_MARKERS,
                            TRAIN_SEVERITY_RANGE, VAL_SEVERITY_RANGE, SHARED_BASELINE_SEVERITIES)


def load_accident_trajectory(accident_type, severity):
    """加载单条事故轨迹，截取主窗口。"""
    path = ACCIDENT_DIRS[accident_type] / f"{severity}.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found")
    df = pd.read_csv(path)
    if "TIME" not in df.columns:
        raise ValueError(f"{path}: TIME column missing")
    # 校验时间步长
    dt = df["TIME"].diff().dropna()
    if not np.allclose(dt.iloc[0], TIME_STEP, atol=1e-3):
        log_error("data_loader", f"{accident_type}_{severity}: time step != {TIME_STEP}s",
                  "数据时间步长异常", "检查原始数据")
    return df


def load_accident_all(accident_type, variables=None):
    """加载某事故全部100条轨迹，主窗口截取，逐类常量列剔除。
    返回: trajectories(dict), valid_vars(list)
    """
    if variables is None:
        variables = VARIABLE_UNION_22
    trajectories = {}
    for sev in range(1, 101):
        df = load_accident_trajectory(accident_type, sev)
        win = slice_window(df, WINDOW_START, WINDOW_END, TIME_STEP, WINDOW_POINTS)
        trajectories[sev] = win
    # 逐类常量列审计（find_constant_columns 要求传入 {severity: df} 字典）
    constant_cols = find_constant_columns(trajectories, variables)
    valid_vars = [v for v in variables if v not in constant_cols]
    logger.info(f"[{accident_type}] loaded 100 trajectories, valid_vars={len(valid_vars)}/{len(variables)}, dropped_const={constant_cols}")
    return trajectories, valid_vars


def stratified_split(severities):
    """分层划分：sev-1 固定训练集，其余按 train_ratio 划分。
    确保测试集不含任何训练过程监督信息。
    """
    sev1 = [s for s in severities if s == 1]
    others = [s for s in severities if s != 1]
    others_train, others_val = split_trajectories(others, TRAIN_RATIO, RANDOM_SEED)
    train_sev = sorted(sev1 + others_train)
    val_sev = sorted(others_val)
    return train_sev, val_sev


def build_shared_baseline(accident_trajectories, variables):
    """共享基线：三类事故 sev-1 池化后的 μ±σ 作为跨事故 Normal 基准。
    仅使用训练集轨迹，防数据泄漏。
    """
    pool = []
    for at in ACCIDENT_TYPES:
        trajs = accident_trajectories[at]
        for sev in SHARED_BASELINE_SEVERITIES:
            if sev in trajs:
                pool.append(trajs[sev][variables].values)
    if not pool:
        raise ValueError("共享基线池为空")
    stacked = np.vstack(pool)  # (n_pts, n_vars)
    mu = stacked.mean(axis=0)
    sigma = stacked.std(axis=0)
    sigma = np.where(sigma < 1e-6, 1e-6, sigma)  # 防止除零
    baseline = {"mu": mu.tolist(), "sigma": sigma.tolist(), "variables": variables,
                "n_trajectories": len(pool)}
    logger.info(f"[shared_baseline] pooled {len(pool)} sev-1 trajectories, {len(variables)} vars")
    return baseline


def load_all_accidents():
    """加载全部三类事故数据，返回统一结构。
    返回: {
        accident_type: {"trajectories": dict, "valid_vars": list, "train_sev": list, "val_sev": list}
    }
    """
    all_data = {}
    for at in ACCIDENT_TYPES:
        trajs, valid_vars = load_accident_all(at)
        train_sev, val_sev = stratified_split(list(range(1, 101)))
        all_data[at] = {
            "trajectories": trajs,
            "valid_vars": valid_vars,
            "train_sev": train_sev,
            "val_sev": val_sev,
            "target": CLASS_TARGETS[at],
            "marker": CLASS_MARKERS[at],
        }
    return all_data

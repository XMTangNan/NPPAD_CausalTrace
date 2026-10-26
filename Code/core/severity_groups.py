"""
Phase 2 稳健性检查：严重度分组学图。

将轨迹按严重度分组（默认 low/mid/high 三组，可由 severity_groups 参数覆盖），
每组在训练集内独立拟合 LiNGAM，得到分组 B 矩阵与边列表；
随后对核心关系按三条预注册规则判定方向一致性。

注意：每组样本量可能较小，LiNGAM 估计可能不稳定，结论仅供稳健性参考。
核心关系清单（core_relations）由调用方传入，不在此处硬编码任何事故特有关系。
"""
import numpy as np
from typing import Dict, List, Tuple

from config import SEVERITY_GROUPS
from core.utils import logger, save_json
from core.data_utils import build_matrix, standardize, split_trajectories
from core.lingam_baseline import fit_lingam, adjacency_to_edges


# "无法估计"阈值：双向边权绝对值均低于此值视为该组无法判定方向
UNESTIMABLE_THRESHOLD = 1e-6


# ==================== 分组学图 ====================
def run_severity_groups(trajectories: Dict, variables: List[str],
                        mu: np.ndarray, sigma: np.ndarray,
                        severity_groups: Dict = None,
                        train_ratio: float = 0.8,
                        random_seed: int = 42) -> Dict:
    """对各严重度组分别在训练集内学 LiNGAM 图。

    每组内部按 train_ratio 划分训练/验证，仅用训练集拟合 LiNGAM。
    标准化参数 mu/sigma 由调用方（通常为全局训练集）提供，保证组间可比。

    Args:
        trajectories: {severity: window_df}
        variables: 入模变量列表
        mu: 训练集均值向量 (n_vars,)
        sigma: 训练集标准差向量 (n_vars,)
        severity_groups: {group_name: (lo, hi)} 严重度范围映射；
                         为 None 时取 config.SEVERITY_GROUPS
        train_ratio: 组内训练集比例
        random_seed: 组内划分随机种子

    Returns:
        group_results: {
            group_name: {
                "B": np.ndarray,          # 邻接矩阵 B[i,j]=x_j→x_i
                "edges": List[Dict],      # 非零边列表（按|weight|降序）
                "severity_range": [lo, hi],
                "n_total": int,
                "n_train": int,
                "n_val": int,
                "train_severities": List[int],
                "n_timepoints": int,
                "status": "ok" | "failed",
                "error": str | None,
                "sample_size_note": str,
            }
        }
    """
    if severity_groups is None:
        severity_groups = SEVERITY_GROUPS

    group_results = {}
    for gname, (lo, hi) in severity_groups.items():
        sevs = sorted([s for s in trajectories if lo <= s <= hi])
        train_sev, val_sev = split_trajectories(sevs, train_ratio=train_ratio,
                                                random_seed=random_seed)
        mat = build_matrix(trajectories, train_sev, variables)
        mat_std = standardize(mat, mu, sigma)

        try:
            B = fit_lingam(mat_std)
            edges = adjacency_to_edges(B, variables)
            edges.sort(key=lambda e: abs(e["weight"]), reverse=True)
            status = "ok"
            error = None
        except Exception as e:  # noqa: BLE001
            B = np.zeros((len(variables), len(variables)))
            edges = []
            status = "failed"
            error = str(e)
            logger.warning(f"[{gname}] LiNGAM 拟合失败: {e}")

        n_timepoints = int(mat_std.shape[0])
        group_results[gname] = {
            "B": B,
            "edges": edges,
            "severity_range": [lo, hi],
            "n_total": len(sevs),
            "n_train": len(train_sev),
            "n_val": len(val_sev),
            "train_severities": train_sev,
            "n_timepoints": n_timepoints,
            "status": status,
            "error": error,
            "sample_size_note": (
                f"本组仅 {len(train_sev)} 条训练轨迹 / {n_timepoints} 时间点，"
                f"样本量小，LiNGAM 估计可能不稳定。"
            ),
        }
        logger.info(
            f"[{gname}] sev={lo}-{hi}, n_train={len(train_sev)}, "
            f"timepoints={n_timepoints}, edges={len(edges)}, status={status}"
        )
    return group_results


# ==================== 方向一致性判定 ====================
def _relation_direction(B: np.ndarray, var_idx: Dict[str, int],
                        from_var: str, to_var: str) -> Tuple[str, float, float]:
    """判定单组中关系 (from_var → to_var) 的方向。

    LiNGAM 邻接矩阵 B[i,j] = x_j → x_i 的直接效应，因此：
      - B[i_to, i_from] 为 from_var → to_var 的权重（正向）
      - B[i_from, i_to] 为 to_var → from_var 的权重（反向）

    Returns:
        (direction, fwd_weight, rev_weight)
        direction ∈ {"forward", "reverse", "none"}
    """
    i_from = var_idx[from_var]
    i_to = var_idx[to_var]
    fwd = float(B[i_to, i_from])   # from_var → to_var
    rev = float(B[i_from, i_to])   # to_var → from_var

    fwd_present = abs(fwd) > UNESTIMABLE_THRESHOLD
    rev_present = abs(rev) > UNESTIMABLE_THRESHOLD

    if not fwd_present and not rev_present:
        return "none", fwd, rev
    if fwd_present and rev_present:
        # DirectLiNGAM 理论上为 DAG，双向非零极少出现；兜底取主导方向
        return ("forward" if abs(fwd) >= abs(rev) else "reverse"), fwd, rev
    if fwd_present:
        return "forward", fwd, rev
    return "reverse", fwd, rev


def edge_direction_consistency(group_results: Dict, variables: List[str],
                               core_relations: List[Dict]) -> List[Dict]:
    """对核心关系按三条预注册规则判定方向一致性。

    预注册规则：
      1. 所有可估计组方向一致 → candidate
      2. 可估计组方向不一致（发生翻转） → heterogeneous（不强行选一个方向）
      3. 存在无法估计的组（拟合失败或双向边权均近零） → insufficient_evidence

    Args:
        group_results: run_severity_groups 的返回值
        variables: 入模变量列表
        core_relations: 核心关系清单，每条含 from/to/label/type/basis 字段
                        （对应事故类型的 CORE_RELATIONS，由调用方传入）

    Returns:
        report: 每条核心关系的判定报告列表
    """
    var_idx = {v: i for i, v in enumerate(variables)}
    group_names = list(group_results.keys())
    report = []

    for rel in core_relations:
        from_var = rel["from"]
        to_var = rel["to"]
        group_dirs: Dict[str, Dict] = {}

        # 变量若因常量列被剔除，直接标记为无法估计
        if from_var not in var_idx or to_var not in var_idx:
            for gname in group_names:
                group_dirs[gname] = {
                    "direction": "none",
                    "fwd_weight": 0.0,
                    "rev_weight": 0.0,
                    "note": "variable_dropped",
                }
        else:
            for gname in group_names:
                res = group_results[gname]
                if res["status"] != "ok":
                    group_dirs[gname] = {
                        "direction": "none",
                        "fwd_weight": 0.0,
                        "rev_weight": 0.0,
                        "note": f"fit_failed: {res['error']}",
                    }
                    continue
                direction, fwd, rev = _relation_direction(
                    res["B"], var_idx, from_var, to_var
                )
                group_dirs[gname] = {
                    "direction": direction,
                    "fwd_weight": fwd,
                    "rev_weight": rev,
                }

        # ===== 三条预注册规则判定 =====
        estimable = {g: d for g, d in group_dirs.items() if d["direction"] != "none"}
        n_estimable = len(estimable)
        n_groups = len(group_names)

        if n_estimable < n_groups:
            verdict = "insufficient_evidence"
        else:
            dirs = [d["direction"] for d in estimable.values()]
            if len(set(dirs)) == 1:
                verdict = "candidate"
            else:
                verdict = "heterogeneous"

        entry = {
            "relation": rel["label"],
            "from": from_var,
            "to": to_var,
            "type": rel["type"],
            "basis": rel["basis"],
            "groups": group_dirs,
            "n_estimable_groups": n_estimable,
            "verdict": verdict,
        }
        report.append(entry)
        logger.info(
            f"[{rel['label']}] verdict={verdict} "
            f"(estimable={n_estimable}/{n_groups}, "
            f"dirs={[d['direction'] for d in group_dirs.values()]})"
        )

    return report


# ==================== 序列化辅助 ====================
def _serialize_group_results(group_results: Dict) -> Dict:
    """将 group_results 转为可 JSON 序列化的结构（B 矩阵转 list）。"""
    out = {}
    for gname, res in group_results.items():
        out[gname] = {
            "B": res["B"].tolist() if isinstance(res["B"], np.ndarray) else res["B"],
            "edges": res["edges"],
            "severity_range": res["severity_range"],
            "n_total": res["n_total"],
            "n_train": res["n_train"],
            "n_val": res["n_val"],
            "train_severities": res["train_severities"],
            "n_timepoints": res["n_timepoints"],
            "status": res["status"],
            "error": res["error"],
            "sample_size_note": res["sample_size_note"],
        }
    return out


if __name__ == "__main__":
    pass

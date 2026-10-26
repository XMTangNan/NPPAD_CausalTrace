"""
方案 B：轻量非线性基线 — GAM-ANM（广义加性模型 + 加性噪声模型）。

机制：对每个变量对 (X, Y)，分别拟合 Y=f(X)+ε 与 X=g(Y)+η（f,g 为样条），
用互信息度量残差对因变量的依赖性；ANM 可辨识性指出因果方向的残差与因变量独立。
因此选择 MI(残差, 因变量) 更小的方向为因果方向。

公平性前提：与 LiNGAM 共用同一变量集、同一主窗口、同一轨迹级划分、种子对齐的 Bootstrap 索引。

目标变量 target 与方向检查标记 marker_a/marker_b 均由参数传入，不硬编码事故特有变量。
"""
import numpy as np
from typing import Dict, List, Tuple

from config import RANDOM_SEED
from core.utils import logger, log_error
from core.data_utils import build_matrix, standardize
from core.lingam_baseline import adjacency_to_edges


def _gam_fit_residual(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """拟合 y = f(x) + ε（样条 GAM），返回残差 ε。"""
    from pygam import LinearGAM, s
    try:
        gam = LinearGAM(s(0, n_splines=10, lam=0.6)).fit(x.reshape(-1, 1), y)
        pred = gam.predict(x.reshape(-1, 1))
        return y - pred
    except Exception:
        # GAM 拟合失败回退到线性
        from sklearn.linear_model import LinearRegression
        lr = LinearRegression().fit(x.reshape(-1, 1), y)
        return y - lr.predict(x.reshape(-1, 1))


def _mi(x: np.ndarray, y: np.ndarray) -> float:
    """互信息估计（sklearn），作为依赖性度量。"""
    from sklearn.feature_selection import mutual_info_regression
    if np.std(x) < 1e-12 or np.std(y) < 1e-12:
        return 0.0
    mi = mutual_info_regression(x.reshape(-1, 1), y, random_state=RANDOM_SEED)
    return float(mi[0])


def anm_pair_direction(x: np.ndarray, y: np.ndarray) -> Tuple[int, float]:
    """ANM 成对方向判定。
    返回 (direction, confidence)：
      direction = +1 表示 x→y, -1 表示 y→x, 0 表示无法判定。
      confidence = |MI_rev - MI_fwd|。
    """
    res_fwd = _gam_fit_residual(x, y)   # y = f(x) + ε
    res_rev = _gam_fit_residual(y, x)   # x = g(y) + η
    mi_fwd = _mi(res_fwd, x)            # ε 与 x 的依赖
    mi_rev = _mi(res_rev, y)            # η 与 y 的依赖
    diff = mi_rev - mi_fwd              # >0 表示正向残差更独立 → x→y
    if abs(diff) < 1e-6:
        return 0, 0.0
    direction = 1 if diff > 0 else -1
    confidence = abs(diff)
    return direction, confidence


def build_anm_graph(data: np.ndarray, variables: List[str]) -> np.ndarray:
    """构建 ANM 有向邻接矩阵 B（B[i,j] = 强度，x_j→x_i）。
    仅当该方向的 ANM 判据胜出时置边，边权为 confidence。
    """
    n = len(variables)
    B = np.zeros((n, n))
    for j in range(n):
        for i in range(n):
            if i == j:
                continue
            x = data[:, j]
            y = data[:, i]
            direction, conf = anm_pair_direction(x, y)
            if direction == 1:   # x_j → x_i
                B[i, j] = conf
            # direction == -1 时 x_i → x_j，在 (j,i) 处设置
    return B


def anm_total_effect_ranking(data: np.ndarray, variables: List[str],
                             target: str) -> List[Dict]:
    """GAM 总效应排序：对目标变量 target，用 GAM 拟合 target=f(X)，
    取平均边际效应 |d(target)/dX| 排序。边际效应用中心差分近似。

    Args:
        target: 目标变量名（必传）
    """
    from pygam import LinearGAM, s
    t_idx = variables.index(target)
    y = data[:, t_idx]
    ranking = []
    for j, var in enumerate(variables):
        if var == target:
            continue
        x = data[:, j]
        try:
            gam = LinearGAM(s(0, n_splines=10, lam=0.6)).fit(x.reshape(-1, 1), y)
            h = np.std(x) * 0.01 + 1e-9
            dydx = (gam.predict((x + h).reshape(-1, 1)) -
                    gam.predict((x - h).reshape(-1, 1))) / (2 * h)
            avg_effect = float(np.mean(dydx))
        except Exception:
            from sklearn.linear_model import LinearRegression
            lr = LinearRegression().fit(x.reshape(-1, 1), y)
            avg_effect = float(lr.coef_[0])
        ranking.append({"variable": var, "avg_marginal_effect": avg_effect,
                        "abs_effect": abs(avg_effect)})
    ranking.sort(key=lambda r: r["abs_effect"], reverse=True)
    for k, r in enumerate(ranking):
        r["rank"] = k + 1
    return ranking


def run_anm_bootstrap(trajectories: Dict, train_sev: List[int], variables: List[str],
                      mu: np.ndarray, sigma: np.ndarray,
                      n_bootstrap: int = 50,
                      random_seed: int = 42) -> Tuple[List[np.ndarray], Dict]:
    """轨迹级 Bootstrap ANM（与 LiNGAM 同抽样口径）。
    返回 (B_list, edge_stats)。
    """
    rng = np.random.default_rng(random_seed)
    n = len(variables)
    B_list = []
    edge_count = np.zeros((n, n), dtype=int)
    edge_weights = {f"{j}->{i}": [] for i in range(n) for j in range(n) if i != j}

    for k in range(n_bootstrap):
        sampled = rng.choice(train_sev, size=len(train_sev), replace=True)
        mat = build_matrix(trajectories, list(sampled), variables)
        mat_std = standardize(mat, mu, sigma)
        B = build_anm_graph(mat_std, variables)
        B_list.append(B)
        for i in range(n):
            for j in range(n):
                if i != j and B[i, j] > 0:
                    edge_count[i, j] += 1
                    edge_weights[f"{j}->{i}"].append(B[i, j])
        if (k + 1) % 5 == 0:
            logger.info(f"  ANM Bootstrap {k+1}/{n_bootstrap}")

    edge_stats = {}
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            key = f"{variables[j]}->{variables[i]}"
            count = edge_count[i, j]
            f_e = count / n_bootstrap
            weights = edge_weights[f"{j}->{i}"]
            if len(weights) > 0:
                sigma_e = np.std(weights, ddof=1) if len(weights) > 1 else 0.0
                s_e = 1.0 / (1.0 + sigma_e)
            else:
                s_e = 0.0
            rev_count = edge_count[j, i]
            total = count + rev_count
            dir_agree = count / total if total > 0 else 0.0
            edge_stats[key] = {
                "from": variables[j], "to": variables[i],
                "f_e": float(f_e), "s_e": float(s_e),
                "dir_agreement": float(dir_agree),
                "count": int(count),
            }
    return B_list, edge_stats


def compare_lingam_anm(lingam_edges: List[Dict], anm_edges: List[Dict],
                       lingam_ranking: List[Dict], anm_ranking: List[Dict],
                       variables: List[str],
                       marker_a: str = "WLR", marker_b: str = "P") -> Dict:
    """LiNGAM 与 GAM-ANM 对照指标（方案 13.5 预注册）。

    Args:
        marker_a, marker_b: 方向专项检查的两个变量名（如 WLR 与 P）。
    """
    def edge_set(edges):
        return set((e["from"], e["to"]) for e in edges)

    set_l = edge_set(lingam_edges)
    set_a = edge_set(anm_edges)

    # SHD（结构汉明距离）与 Jaccard
    shd = len(set_l.symmetric_difference(set_a))
    jaccard = len(set_l & set_a) / len(set_l | set_a) if (set_l | set_a) else 0.0

    # 方向一致率：在两算法都识别出的变量对上，方向是否一致
    pair_l = {(min(a, b), max(a, b)): (a, b) for a, b in set_l}
    pair_a = {(min(a, b), max(a, b)): (a, b) for a, b in set_a}
    common_pairs = set(pair_l.keys()) & set(pair_a.keys())
    if common_pairs:
        agree = sum(1 for p in common_pairs if pair_l[p] == pair_a[p])
        dir_agreement = agree / len(common_pairs)
    else:
        dir_agreement = 0.0

    # Top-K 重合率
    def topk(ranking, k):
        return set(r["variable"] for r in ranking[:k])
    top5_overlap = len(topk(lingam_ranking, 5) & topk(anm_ranking, 5)) / 5
    top10_overlap = len(topk(lingam_ranking, 10) & topk(anm_ranking, 10)) / 10

    # 标记变量对方向专项
    def find_dir(edges, a, b):
        for e in edges:
            if e["from"] == a and e["to"] == b:
                return f"{a}->{b}"
            if e["from"] == b and e["to"] == a:
                return f"{b}->{a}"
        return "none"

    marker_key = f"{marker_a}_{marker_b}"
    return {
        "lingam_n_edges": len(set_l),
        "anm_n_edges": len(set_a),
        "shd": shd,
        "jaccard": jaccard,
        "direction_agreement_on_common": dir_agreement,
        "n_common_pairs": len(common_pairs),
        "top5_overlap": top5_overlap,
        "top10_overlap": top10_overlap,
        marker_key: {
            "lingam": find_dir(lingam_edges, marker_a, marker_b),
            "anm": find_dir(anm_edges, marker_a, marker_b),
        },
    }


if __name__ == "__main__":
    pass

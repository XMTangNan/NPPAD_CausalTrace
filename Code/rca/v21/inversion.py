"""R2 反演匹配原语（测试侧零学图；13.7）。

d_x[j] = median_t z_xj(t)，z 相对过程本体共享 mu/sigma（禁止自标准化）。
A(x)  = {j in V_act : |z|>3 的时间点占比 > 10%}；A=空强制弃权。
E3-base: pi 加权符号一致率；E3-path: marker 稳定边 BFS 的 reach+depth 拓扑信息。
"""
from __future__ import annotations

import math
import numpy as np
from typing import Dict, List, Optional, Sequence

from rca.rca_config_v21 import (
    ANOMALY_K, ACTIVATION_FRAC, E3_FRONTIER_INVERSION_PENALTY,
)


class MarkerAccessError(AssertionError):
    """MASKED 条件运行时零访问断言失败（测试特征中出现 marker 列）。"""


def assert_no_marker_columns(feature_columns: Sequence[str],
                             marker_cols: Sequence[str]) -> None:
    """13.8.2 G4：MASKED 测试特征矩阵列名 ∩ marker 列必须为空。"""
    leak = sorted(set(feature_columns) & set(marker_cols))
    if leak:
        raise MarkerAccessError(f"MASKED 零访问断言失败，marker 泄漏: {leak}")


def signed_median_z(Z: np.ndarray) -> np.ndarray:
    """d_x = 主窗逐变量有符号中位 z。"""
    return np.median(Z, axis=0)


def overlimit_rates(Z: np.ndarray, k: float = ANOMALY_K) -> np.ndarray:
    """每变量 |z|>k 的时间点占比。"""
    return (np.abs(Z) > k).mean(axis=0)


def activation_set(rates: np.ndarray, variables: Sequence[str],
                   v_act: Sequence[str], frac: float = ACTIVATION_FRAC) -> List[str]:
    """A(x)：V_act 内越限时间点占比 > frac 的变量。"""
    idx = {v: i for i, v in enumerate(variables)}
    return [v for v in v_act if v in idx and rates[idx[v]] > frac]


def _sign_match(a: float, b: float) -> bool:
    return (a > 0 and b > 0) or (a < 0 and b < 0)


def e3_base_channel(d: np.ndarray, variables: Sequence[str],
                    activated: Sequence[str], cL_map: Dict[str, float],
                    pi_map: Dict[str, float],
                    gcf_map: Optional[Dict[str, float]] = None) -> float:
    """E3_L：wL_j = piL_j * gCF_j 的符号一致率；分母为 0 返回 nan。"""
    idx = {v: i for i, v in enumerate(variables)}
    num = den = 0.0
    for j in activated:
        if j not in cL_map or j not in idx:
            continue
        w = pi_map[j] * (gcf_map or {}).get(j, 1.0)
        if w <= 0:
            continue
        den += w
        if _sign_match(d[idx[j]], cL_map[j]):
            num += w
    return num / den if den > 0 else float("nan")


def _layer_anomaly_rates(activated: Sequence[str], layers: Dict[int, List[str]]
                         ) -> Dict[int, float]:
    A = set(activated)
    out = {}
    for depth, members in layers.items():
        out[depth] = (len(A & set(members)) / len(members)) if members else 0.0
    return out


def frontier_violations(rates: Dict[int, float]) -> int:
    """r1>=r2>=r3 的违例对数（取值 0..3，仅诊断不评分）。"""
    keys = sorted(rates)
    return sum(1 for ii, a in enumerate(keys) for b in keys[ii + 1:]
               if rates[a] < rates[b])


def e3_path_channel(d: np.ndarray, variables: Sequence[str],
                    activated: Sequence[str], cL_map: Dict[str, float],
                    pi_map: Dict[str, float], frontier: Dict,
                    gcf_map: Optional[Dict[str, float]] = None
                    ) -> Dict[str, float]:
    """E3_path：0.5 reach + 0.5 depth；r3>r1 时整体乘 0.8。

    返回 {score, reach, depth, violation_pairs, inverted}。
    """
    idx = {v: i for i, v in enumerate(variables)}
    A = set(activated)
    R = set(frontier["reach"])
    in_reach = A & R
    reach_ratio = len(in_reach) / len(A) if A else float("nan")

    num = den = 0.0
    depth_of = frontier["depth_of"]
    layers = {int(k): v for k, v in frontier["layers"].items()}
    for j in in_reach:
        if j not in cL_map:
            continue
        dep = depth_of[j]
        w = pi_map.get(j, 0.0) * (gcf_map or {}).get(j, 1.0) / dep
        if w <= 0:
            continue
        den += w
        if _sign_match(d[idx[j]], cL_map[j]):
            num += w
    depth_score = num / den if den > 0 else float("nan")

    rates = _layer_anomaly_rates(activated, layers)
    violations = frontier_violations(rates)
    inverted = rates.get(3, 0.0) > rates.get(1, 0.0)
    score = 0.5 * reach_ratio + 0.5 * depth_score
    if not math.isnan(score) and inverted:
        score *= E3_FRONTIER_INVERSION_PENALTY
    return {"score": score, "reach": reach_ratio, "depth": depth_score,
            "violation_pairs": violations, "inverted": inverted,
            "layer_rates": rates}


# ==================== E2 / B1 ====================
def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def cosine_alignment(u: np.ndarray, v: np.ndarray) -> float:
    nu, nv = np.linalg.norm(u), np.linalg.norm(v)
    if nu < 1e-12 or nv < 1e-12:
        return 0.0
    return float(np.dot(u, v) / (nu * nv))


def e2_score(d: np.ndarray, over_set: Sequence[str], variables: Sequence[str],
             template_d: np.ndarray, template_over: Sequence[str]) -> float:
    """0.5 越限指示 Jaccard + 0.5 有符号中位 z 余弦（变量已对齐）。"""
    j = jaccard(over_set, template_over)
    c = cosine_alignment(d, template_d)
    return 0.5 * j + 0.5 * c


def pearson_corr(u: np.ndarray, v: np.ndarray) -> float:
    if np.std(u) < 1e-12 or np.std(v) < 1e-12:
        return 0.0
    return float(np.corrcoef(u, v)[0, 1])


# ==================== 组模式（训练侧，签名 A_{k,g}）====================
def build_group_pattern(d_list: List[np.ndarray], over_sets: List[Sequence[str]],
                        variables: Sequence[str]) -> Dict:
    """组中位有符号 z + 组越限变量集（轨迹越限率中位 >10%）。"""
    D = np.stack(d_list)
    template_d = np.median(D, axis=0)
    # 组越限集：在组内 >10% 训练轨迹中被激活的变量
    freq = {v: float(np.mean([v in set(s) for s in over_sets])) for v in variables}
    template_over = [v for v, q in freq.items() if q > 0.10]
    return {"template_d": template_d, "template_over": template_over}


def should_abstain(activated: Sequence[str], scores: np.ndarray,
                   tau: float, delta: float) -> Optional[str]:
    """弃权三条件：A 空 / max<tau / top1-top2<delta；返回原因或 None。"""
    if not activated:
        return "empty_activation"
    s = np.sort(np.asarray(scores, dtype=float))[::-1]
    if s[0] < tau:
        return "below_tau"
    if len(s) > 1 and s[0] - s[1] < delta:
        return "small_margin"
    return None

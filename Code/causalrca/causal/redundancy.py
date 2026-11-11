"""冗余通道诊断（V23-CODE-035）：只记录，不参与排序/候选集/稳定边/Top-k。

背景：`SGATR_low` 的 `NSGA`/`LSGA` 是同一物理量（SG-A 水位）的窄/宽量程两路通道，
在线性回归里被劈成对同一组目标反号、近等幅的系数（+13~+17 / −13~−16）。
本模块只把这类"共线分裂"标记出来供解读，绝不改变任何计算结果。

判定规则（可复核，阈值与样本量都写进产物）：
1. 通道对范围：`ENTITY_TABLE[v]["meaning"]` 去掉"窄量程/宽量程/小量程/大量程"后
   属于同一物理量的变量对（同物理量多量程/冗余通道）；不做无差别全量打标。
2. 相关性：两路在学图训练块（全部训练轨迹、学图窗池化）上的 Pearson |r| ≥ 0.95。
3. 目标集：存在至少 2 个共同目标 t，使 u→t 与 v→t 都是稳定边，且
   |w_u|、|w_v| ≥ 5（`B_CLIP`），符号相反，且 |w_u+w_v| ≤ 0.10·min(|w_u|,|w_v|)。
4. 任一成员若为零方差列（学图中被置 0），跳过该对。

相关性与边权都只读；本模块不返回任何用于排序的量。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

from causalrca.evidence.knowledge import ENTITY_TABLE

# 同一物理量的不同量程写法（只认这几个词，避免把无关变量并组）
REDUNDANT_RANGE_TOKENS = ("窄量程", "宽量程", "小量程", "大量程")

MIN_ABS_R = 0.95        # Pearson 相关阈值
MIN_ABS_W = 5.0         # 与 B_CLIP 一致：只标记会被截断量级的大系数
CANCEL_RATIO = 0.10     # |w_u + w_v| 相对 min(|w_u|,|w_v|) 的近抵消比例
MIN_TARGETS = 2         # 至少在两个共同目标上反号近抵消


def _quantity(meaning: str) -> Optional[str]:
    """从实体含义里去掉量程词，得到同一物理量的组名；无量程词返回 None。"""
    if not meaning:
        return None
    base = meaning
    for tok in REDUNDANT_RANGE_TOKENS:
        base = base.replace(tok, "")
    return base if base != meaning and base else None


def redundant_channel_groups(variables: Sequence[str]) -> Dict[str, List[str]]:
    """按"同一物理量、多量程"分组；只返回成员数 ≥2 的组。"""
    groups: Dict[str, List[str]] = {}
    for v in variables:
        q = _quantity((ENTITY_TABLE.get(v) or {}).get("meaning") or "")
        if q:
            groups.setdefault(q, []).append(v)
    return {q: members for q, members in groups.items() if len(members) >= 2}


def zero_variance_variables(variables: Sequence[str], Z: np.ndarray,
                            tol: float = 1e-12) -> List[str]:
    """学图矩阵 Z 中被置零（极差≤tol）的列；这些变量保留不剔，只打标记。

    判据用实际送入 LiNGAM 的矩阵 Z，因此对任何归一化口径都成立。
    """
    out: List[str] = []
    for p, v in enumerate(variables):
        col = Z[:, p]
        if col.size and float(np.ptp(col)) <= tol:
            out.append(v)
    return out


def detect_collinear_pairs(
    variables: Sequence[str],
    pooled_raw: np.ndarray,
    stable_edges: Sequence[Dict],
    zero_vars: Sequence[str] = (),
) -> Dict:
    """识别共线分裂通道对；返回可复核的明细（不参与任何计算）。

    pooled_raw: 形状 (n_samples, n_variables)，学图训练块按列顺序池化。
    """
    n = len(variables)
    pos = {v: i for i, v in enumerate(variables)}
    weights = {(e["from"], e["to"]): float(e.get("weight_median", 0.0))
               for e in stable_edges}
    groups = redundant_channel_groups(variables)
    zero_set = set(zero_vars)

    # 对全部候选对计算 Pearson（可复核的"样本量"口径），但只在同量程组内套用规则
    n_samples = int(pooled_raw.shape[0])
    corr: Dict[tuple, float] = {}
    for a in range(n):
        for b in range(a + 1, n):
            x, y = pooled_raw[:, a], pooled_raw[:, b]
            if float(np.ptp(x)) <= 1e-12 or float(np.ptp(y)) <= 1e-12:
                continue
            r = float(np.corrcoef(x, y)[0, 1])
            if np.isfinite(r):
                corr[(variables[a], variables[b])] = r

    pairs: List[Dict] = []
    for quantity, members in groups.items():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                u, v = members[i], members[j]
                if u in zero_set or v in zero_set:
                    continue
                r = corr.get((u, v), corr.get((v, u)))
                if r is None or abs(r) < MIN_ABS_R:
                    continue
                targets: List[Dict] = []
                for t in variables:
                    if t in (u, v):
                        continue
                    wu, wv = weights.get((u, t)), weights.get((v, t))
                    if wu is None or wv is None:
                        continue
                    if abs(wu) < MIN_ABS_W or abs(wv) < MIN_ABS_W:
                        continue
                    if np.sign(wu) == np.sign(wv):
                        continue
                    if abs(wu + wv) > CANCEL_RATIO * min(abs(wu), abs(wv)):
                        continue
                    targets.append({"target": t,
                                    "w_u": round(wu, 6), "w_v": round(wv, 6),
                                    "sum": round(wu + wv, 6)})
                if len(targets) < MIN_TARGETS:
                    continue
                pairs.append({
                    "flag": "collinear_pair",
                    "pair": [u, v],
                    "quantity": quantity,
                    "pearson_r": round(r, 6),
                    "n_samples": n_samples,
                    "targets": targets,
                    "n_targets": len(targets),
                })

    return {
        "note": ("仅记录，不参与排序、候选集、稳定边判定或 Top-k；"
                 "用于提示同一物理量多量程通道被回归劈成反号近等幅系数"),
        "rule": {
            "scope": "ENTITY_TABLE.meaning 去掉量程词后同一物理量",
            "range_tokens": list(REDUNDANT_RANGE_TOKENS),
            "min_abs_r": MIN_ABS_R,
            "min_abs_w": MIN_ABS_W,
            "cancel_ratio": CANCEL_RATIO,
            "min_targets": MIN_TARGETS,
        },
        "n_samples": n_samples,
        "n_pairs_scanned": len(corr),
        "n_groups": len(groups),
        "collinear_pairs": pairs,
    }

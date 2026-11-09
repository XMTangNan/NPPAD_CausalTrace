"""V2.3 §5.3.2 D4: K2 数值约束 — 5 类近似物理关系残差三态判定。

对 consensus 边验证物理关系，三态：违反 / 未违反 / 不可判定。
满足约束≠方向证明、违反≠链错（可能遗漏项导致）。

接线（V23-DESIGN-003）：`evaluate_all_relations` 支持按组代表严重度取主窗切片；
`evaluate_groups` 一次给三组结果，供 `signatures` 挂载 auxiliary.k2 与图内面板。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from rca.rca_config_v21 import GROUP_ORDER, SEVERITY_BANDS
from rca.v21.accident_config import get_accident
from rca.v21 import data as D

# 5 类近似物理关系定义（方案 §5.3.2）
PHYSICS_RELATIONS = [
    {
        "id": "R1_inventory_leak",
        "name": "一回路装量-泄漏",
        "lhs": "WLR",               # 泄漏流量
        "rhs": "VOL",               # 一回路装量
        "rhs_operator": "d_dt",     # 对 rhs 求时间导数
        "expected_sign": "neg",     # WLR↑ → d(VOL)/dt↓，残差符号预期为负
        "applicable_accidents": ["LOCA", "LOCAC", "LLB"],
        "missing_terms": "安注/补水、密度变化、未测小泄漏",
    },
    {
        "id": "R2_sg_water_level",
        "name": "SG 水位动态",
        "lhs": "NET_FW",             # (WFWA+WFWB) − (WSTA+WSTB)
        "rhs": "LSG_AVG",            # (LSGA+LSGB)/2
        "rhs_operator": "d_dt",
        "expected_sign": "pos",      # 净给水↑ → d(LSG)/dt↑
        "applicable_accidents": ["FLB", "SGATR", "SGBTR"],
        "missing_terms": "压力/密度/相变、排污、未测流入",
        "compose_lhs": {"WFWA": 1, "WFWB": 1, "WSTA": -1, "WSTB": -1},
        "compose_rhs": {"LSGA": 0.5, "LSGB": 0.5},
    },
    {
        "id": "R3_eci_trigger",
        "name": "安注触发",
        "lhs": "P_LOW",              # P 低于阈值
        "rhs": "ECI_FLOW",           # WHPI+WECS
        "rhs_operator": "level",     # 阶跃判定
        "expected_sign": "trigger",  # P 低阈 → 安注阶跃
        "applicable_accidents": ["LOCA", "SGATR", "FLB"],
        "missing_terms": "手动/自动逻辑、阈值回差",
        "compose_rhs": {"WHPI": 1, "WECS": 1},
        "threshold_key": "P",
    },
    {
        "id": "R4_tube_leak",
        "name": "传热管泄漏",
        "lhs": "WTRA",               # 传热管破裂流量
        "rhs": "PSGA",               # SG 压力
        "rhs_operator": "level",
        "expected_sign": "neg",      # WTRA↑ → PSG↓（一回路侧向二回路侧泄漏）
        "applicable_accidents": ["SGATR", "SGBTR"],
        "missing_terms": "壳侧排放、控制动作、饱和",
    },
    {
        "id": "R5_reactivity_power",
        "name": "反应性-功率",
        "lhs": "RH",                 # 反应性
        "rhs": "PWNT",               # 核功率
        "rhs_operator": "d_dt",
        "expected_sign": "pos",      # RH↑ → d(PWNT)/dt↑
        "applicable_accidents": ["RW", "RI", "MD"],
        "missing_terms": "温度反馈、氙毒、控制棒动态",
    },
]

VIOLATION = "violation"
NOT_VIOLATED = "not_violated"
UNDECIDABLE = "undecidable"


def _compose_signal(df: pd.DataFrame, compose: Dict[str, float]) -> np.ndarray:
    """按 compose 字典加权合成信号。"""
    out = np.zeros(len(df))
    for col, w in compose.items():
        if col in df.columns:
            out += w * df[col].to_numpy(dtype=float)
    return out


def _d_dt(x: np.ndarray, dt: float = 1.0) -> np.ndarray:
    """一阶差分（前向）。"""
    return np.diff(x, prepend=x[0]) / dt


def check_relation(rel: Dict, df: pd.DataFrame, dt: float = 1.0) -> Dict:
    """对单条物理关系做残差三态判定。"""
    # 构建 lhs 信号
    if "compose_lhs" in rel:
        lhs = _compose_signal(df, rel["compose_lhs"])
    else:
        lhs = df[rel["lhs"]].to_numpy(dtype=float) if rel["lhs"] in df.columns else None

    # 构建 rhs 信号
    if "compose_rhs" in rel:
        rhs = _compose_signal(df, rel["compose_rhs"])
    else:
        rhs = df[rel["rhs"]].to_numpy(dtype=float) if rel["rhs"] in df.columns else None

    if lhs is None or rhs is None or len(lhs) < 2:
        # 触发类关系允许 lhs 为虚拟变量（由阈值判定）
        if rel["expected_sign"] != "trigger":
            return {"relation_id": rel["id"], "status": UNDECIDABLE,
                    "reason": "变量缺失或样本不足", "n_points": len(df)}

    # 对 rhs 做算子
    if rel["rhs_operator"] == "d_dt":
        rhs_op = _d_dt(rhs, dt)
    else:
        rhs_op = rhs  # level

    # 对触发类关系（R3）做特殊判定
    if rel["expected_sign"] == "trigger":
        p_col = rel.get("threshold_key", "P")
        if p_col not in df.columns:
            return {"relation_id": rel["id"], "status": UNDECIDABLE,
                    "reason": f"阈值变量 {p_col} 缺失", "n_points": len(df)}
        p_vals = df[p_col].to_numpy(dtype=float)
        p_mu = float(np.mean(p_vals[:min(50, len(p_vals))]))
        eci_triggered = np.any(rhs_op > 1e-3)
        p_below = np.any(p_vals < p_mu * 0.95)
        if eci_triggered and not p_below:
            status = VIOLATION
            reason = "安注触发但一回路压力未低于阈值"
        else:
            status = NOT_VIOLATED
            reason = "安注触发与压力降低一致（或均未发生）"
        return {"relation_id": rel["id"], "status": status, "reason": reason,
                "n_points": len(df)}

    # 相关系数判定方向一致性
    # 归一化避免量级问题
    lhs_n = (lhs - lhs.mean()) / (lhs.std() + 1e-9)
    rhs_n = (rhs_op - rhs_op.mean()) / (rhs_op.std() + 1e-9)
    corr = float(np.corrcoef(lhs_n, rhs_n)[0, 1])

    expected = {"pos": 1.0, "neg": -1.0}[rel["expected_sign"]]
    if np.isnan(corr) or abs(corr) < 0.1:
        status = UNDECIDABLE
        reason = f"相关系数 {corr:.3f} 接近 0，无法判定"
    elif (expected > 0 and corr > 0.1) or (expected < 0 and corr < -0.1):
        status = NOT_VIOLATED
        reason = f"相关系数 {corr:.3f} 与预期符号一致"
    else:
        status = VIOLATION
        reason = f"相关系数 {corr:.3f} 与预期符号相反"

    return {"relation_id": rel["id"], "status": status, "reason": reason,
            "correlation": round(corr, 4), "n_points": len(df)}


def main_window(accident_type: str) -> Tuple[float, float]:
    """该类主分析窗（元数据唯一来源 accident_config）。"""
    return tuple(get_accident(accident_type)["main_window"])


def representative_severity(accident_type: str, group: str) -> int:
    """组内代表严重度 = 该组实际存在轨迹中的最大 severity（不臆造缺失轨迹）。"""
    lo, hi = SEVERITY_BANDS[group]
    sevs = [s for s in D.list_class_severities(accident_type) if lo <= s <= hi]
    if not sevs:
        raise ValueError(f"{accident_type}/{group}: 严重度区间 [{lo},{hi}] 无可用轨迹")
    return int(max(sevs))


def evaluate_all_relations(accident_type: str, df: Optional[pd.DataFrame] = None, *,
                           severity: Optional[int] = None,
                           window: Optional[Sequence[float]] = None) -> Dict:
    """对指定事故评估所有适用的物理关系（默认取主窗切片）。"""
    win = tuple(window) if window is not None else main_window(accident_type)
    if df is None:
        sev = 1 if severity is None else int(severity)
        df = D.load_trajectory(accident_type, sev)
        severity = sev
    df = D.window_slice(df, win[0], win[1])

    results = []
    for rel in PHYSICS_RELATIONS:
        if accident_type not in rel["applicable_accidents"]:
            continue
        r = check_relation(rel, df)
        r["relation_name"] = rel["name"]
        r["missing_terms"] = rel["missing_terms"]
        results.append(r)

    n_total = len(results)
    n_violation = sum(1 for r in results if r["status"] == VIOLATION)
    n_not = sum(1 for r in results if r["status"] == NOT_VIOLATED)
    n_undec = sum(1 for r in results if r["status"] == UNDECIDABLE)

    return {
        "accident_type": accident_type,
        "severity": severity,
        "window": [float(win[0]), float(win[1])],
        "n_points": int(len(df)),
        "n_relations_applicable": n_total,
        "n_violation": n_violation,
        "n_not_violated": n_not,
        "n_undecidable": n_undec,
        "undecidable_ratio": round(n_undec / max(n_total, 1), 4),
        "relations": results,
        "note": "满足约束≠方向证明、违反≠链错（可能遗漏项导致）；"
                "K2 为辅助证据，不参与 T=(I−B)⁻¹ 排序",
    }


def evaluate_groups(accident_type: str,
                    window: Optional[Sequence[float]] = None) -> Dict:
    """三组代表严重度（组内最大 severity）的主窗残差三态结果。"""
    win = tuple(window) if window is not None else main_window(accident_type)
    groups: Dict[str, Dict] = {}
    for g in GROUP_ORDER:
        sev = representative_severity(accident_type, g)
        df = D.load_trajectory(accident_type, sev)
        res = evaluate_all_relations(accident_type, df, severity=sev, window=win)
        res["group"] = g
        groups[g] = res
    return {
        "accident_type": accident_type,
        "window": [float(win[0]), float(win[1])],
        "groups": groups,
        "note": "每组取组内最大 severity 轨迹的主窗切片；满足约束≠方向证明、违反≠链错",
    }


if __name__ == "__main__":
    for k in ["LOCA", "SGATR", "FLB"]:
        res = evaluate_groups(k)
        print(f"\n{k}: 主窗 {res['window']}")
        for g, r in res["groups"].items():
            print(f"  [{g}] sev={r['severity']}: {r['n_relations_applicable']} 关系适用, "
                  f"违反={r['n_violation']}, 未违反={r['n_not_violated']}, "
                  f"不可判定={r['n_undecidable']}")
            for rel in r["relations"]:
                print(f"    {rel['relation_id']} ({rel['relation_name']}): "
                      f"{rel['status']} — {rel['reason']}")

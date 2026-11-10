"""V2.3 Phase 1：四阶段变量选取机制（方案 §3）。

对应方案 NPPAD-CausalTrace-v2.3 §3：从 NPPAD 的 96 个原始参数中自动筛选
每类事故的最优变量集，替代 v2.1 硬编码变量集（LOCA/SGATR/FLB 各 20 变量）。

四阶段漏斗：
  阶段1 硬过滤：近常量（σ<1e-6 或唯一值<3）、缺失率>50%、全程零值 剔除
  阶段2 数据驱动评分：CV(0.25) + 非高斯(0.20) + 轨迹级MI(0.30) + 跨轨迹区分度(0.25)
  阶段3 物理知识校验：触发变量+关键响应强制保留、精确重复测点去重、
        子系统覆盖、冗余消除
  阶段4 因果发现适配：VIF<500 迭代剔除、变量数 15-25

复用：
- core.utils 的 logger / log_error / save_json / load_json（统一错误跟踪 + 持久化）
- rca.v21.knowledge 的 ENTITY_TABLE / ALLOWED_RELATIONS（子系统映射 + 物理关系）
- rca.rca_config_v21 的 MARKERS / SAFETY_STEP_VARS（触发变量 + 关键响应）
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.feature_selection import mutual_info_regression
from statsmodels.stats.outliers_influence import variance_inflation_factor

from causalrca.config.constants_v21 import (
    MARKERS, SAFETY_STEP_VARS, MAIN_WINDOW_START, MAIN_WINDOW_END,
)
from causalrca.config.accident_config import (
    ALL_CODES, key_responses, marker_of,
)
from causalrca.evidence.knowledge import ENTITY_TABLE, ALLOWED_RELATIONS
from causalrca.features.paper_anchor import build_anchor, stage1_drop_reason
from causalrca.io.logging import logger, log_error


# ==================== 常量（方案 §3.3，禁止散落魔数）====================
EXCLUDE_COLS = {"TIME", "time", "trajectory_id", "traj_id", "severity", "Severity"}

# 阶段1硬过滤阈值
SIGMA_MIN = 1e-6          # 原始标准差下限（CONSTANT_SIGMA 同口径）
NUNIQUE_MIN = 3           # 唯一值下限
MISSING_RATE_MAX = 0.5    # 缺失率上限

# 阶段2评分权重
W_CV = 0.25
W_NON_GAUSS = 0.20
W_MI = 0.30
W_CROSS_DISC = 0.25

# 阶段4约束
# VIF 阈值：方案 §3.3 原为 <10，但核电数据物理强耦合导致 v2.1 硬编码集自身
# max VIF 达 1e10~1e13、16~20/20 变量 VIF>10（实测见 output/rca_v21/variable_selection/）。
# LiNGAM 可耐受共线性，仅近完全共线性（R²>0.998，VIF>500）致数值奇异需剔除。
# 此处按方案 §8.1 风险预案"放宽 VIF 阈值"调整为 500，仅消除病态共线。
VIF_MAX = 500.0
N_MIN = 15
N_MAX = 25
TOP_K = 30
# 论文锚定评分加权（V23-DESIGN-023）：候选优先，但**不改变 VIF 剔除资格**。
# 取值 0.15 的依据：评分已归一到 [0,1]，0.15 足以让锚定变量进入 TOP_K 前列，
# 又不足以把高共线变量强留在最终集合（VIF≥500 仍会被剔除并记录）。
ANCHOR_SCORE_BONUS = 0.15

# 精确重复测点对判定阈值（V23-DESIGN-028）：同一物理量的双测点在数据里是
# 两条几乎同一的序列，实测 FLB PSGA/PSGB=0.9999999996、WFWA/WFWB=0.9999999947，
# SGATR PSGA/PSGB=0.999916、LSGA/LSGB=0.994073。取 |Pearson r| ≥ 0.99
# （R² ≥ 0.98，等效 VIF≈50）判为「精确重复」：
#   1) 覆盖上述全部实测配对（最小 0.994073）；
#   2) 与强制集内下一个最高相关（LOCA TAVG/VOL=0.9106）留有明显间隔，
#      不会退化成通用冗余过滤器（通用去冗余仍由 deduplicate 负责）；
#   3) 等效 VIF 比 VIF_MAX=500 低一个数量级，与 V3 判据解耦。
# 该阈值只决定「同一物理量双测点保留哪一路」，不参与 V3/V5 判定。
DUPLICATE_CORR_MIN = 0.99
DUPLICATE_STAGE = "stage3"

# 变量列序固定规则（V23-CODE-023）：字母序（ASCII，变量名全大写）。
# 为什么必须固定：DirectLiNGAM 的逐变量回归顺序对输入列序敏感，同一变量集
# 仅改变列序即可改变学到的邻接矩阵（实测 SGATR_high Top1 由 RM2 变 RHBR）。
# 选字母序而非本体序：本体序会隐含“物理可信度”偏好，且随本体表增删漂移；
# 字母序与选取得分、数据、本体表完全解耦，审稿人可按名字直接复算列序。
VARIABLE_ORDER_RULE = "alphabetical_ascii"


def canonical_variable_order(variables: Sequence[str]) -> List[str]:
    """按固定规则（字母序）返回变量列表，作为全链唯一列序口径。"""
    return sorted(variables)


# ==================== 纯函数工具 ====================
def norm_quantile_dict(value_dict: Dict[str, float], lower: float = 0.05,
                       upper: float = 0.95) -> Dict[str, float]:
    """分位裁剪(5%-95%)后 min-max 归一化到 [0,1]，防单列饱和（方案 §3.4）。"""
    vals = np.array(list(value_dict.values()), dtype=float)
    if len(vals) == 0:
        return {}
    lo, hi = np.quantile(vals, [lower, upper])
    if hi - lo < 1e-12:
        return {k: 0.5 for k in value_dict}
    return {k: float(max(0.0, min(1.0, (v - lo) / (hi - lo))))
            for k, v in value_dict.items()}


def compute_vif_safe(df: pd.DataFrame) -> Optional[Dict[str, float]]:
    """安全计算 VIF（方差膨胀因子）。

    用 statsmodels.variance_inflation_factor；奇异矩阵返回 None，
    调用方通过 log_error 上报后决定处置（不静默吞错）。
    """
    n = df.shape[1]
    if n < 2:
        return {c: 1.0 for c in df.columns}
    X = df.to_numpy(dtype=float)
    std = X.std(axis=0)
    constant_mask = std < 1e-12
    if constant_mask.all():
        return {c: 1.0 for c in df.columns}
    # 加截距列（statsmodels 约定第 0 列为截距）
    X_const = np.column_stack([np.ones(X.shape[0]), X])
    vifs: Dict[str, float] = {}
    for i, col in enumerate(df.columns):
        if constant_mask[i]:
            vifs[col] = 1.0
            continue
        vifs[col] = float(variance_inflation_factor(X_const, i + 1))
    return vifs


# ==================== 本体查询辅助（复用 knowledge.py 数据，不新增 knowledge.py 接口）====================
# 12 类触发变量与关键响应统一来自 causalrca/config/accident_config.py（方案 §4.3 / 附录 A）。
# v2.1 内联集合与方案附录 A 有差异：SGATR 内联额外含 PSGB/LSGB、FLB 额外含 PSGB，
# 为不改动已验收的 V1-V7 结果，pilot 三类保留 v2.1 实际内联集合作为覆盖值。
_V21_KEY_RESPONSE_OVERRIDES: Dict[str, List[str]] = {
    "LOCA":  ["P", "TAVG", "VOL", "WECS", "WHPI"],                   # §4.3 组1
    "SGATR": ["PSGA", "PSGB", "LSGA", "LSGB", "RM2"],                # §4.3 组3（v2.1 更全）
    "FLB":   ["WFWA", "WFWB", "TAVG", "PWNT", "PSGA", "PSGB"],       # §4.3 组5（v2.1 更全）
}
ACCIDENT_KEY_RESPONSES: Dict[str, List[str]] = {
    c: _V21_KEY_RESPONSE_OVERRIDES.get(c, key_responses(c)) for c in ALL_CODES
}


def get_trigger_var(accident_type: str) -> str:
    """触发变量 = 本类 marker（与 rca_config_v21.MARKERS 同源，见 accident_config）。"""
    return marker_of(accident_type)


def get_key_responses(accident_type: str) -> List[str]:
    """关键响应变量：按事故类型返回方案 §4.3 定义；未配置则回退全局安全系统变量。"""
    if accident_type in ACCIDENT_KEY_RESPONSES:
        return list(ACCIDENT_KEY_RESPONSES[accident_type])
    return list(SAFETY_STEP_VARS)


def get_subsystem(var: str) -> str:
    """变量子系统映射（来自 knowledge.ENTITY_TABLE）。"""
    return ENTITY_TABLE.get(var, {}).get("subsystem", "未登记")


def get_required_subsystems(accident_type: str) -> List[str]:
    """该类事故涉及的子系统 = ALLOWED_RELATIONS 中所有实体的子系统并集。"""
    rels = ALLOWED_RELATIONS.get(accident_type, [])
    subsystems = set()
    for rel in rels:
        for v in (rel["from"], rel["to"]):
            subsystems.add(get_subsystem(v))
    return sorted(s for s in subsystems if s != "未登记")


def deduplicate(vars_list: List[str], scores: Dict[str, float],
                forced: Optional[Sequence[str]] = None) -> List[str]:
    """冗余消除：同一子系统内若有多个变量，保留综合得分最高的（方案 §3.3 阶段3）。

    强制变量（触发变量 + 关键响应）不参与去重，始终保留。
    """
    forced_set = set(forced or [])
    by_sub: Dict[str, List[str]] = {}
    for v in vars_list:
        by_sub.setdefault(get_subsystem(v), []).append(v)
    result: List[str] = []
    for vs in by_sub.values():
        # 强制变量全部保留，非强制变量只保留得分最高的
        forced_in_sub = [v for v in vs if v in forced_set]
        non_forced_in_sub = [v for v in vs if v not in forced_set]
        result.extend(forced_in_sub)
        if non_forced_in_sub:
            result.append(max(non_forced_in_sub, key=lambda v: scores.get(v, 0.0)))
    return result


# ==================== 阶段3：精确重复测点去重（V23-DESIGN-028）====================
def _pair_corr(df: pd.DataFrame, a: str, b: str) -> Optional[float]:
    """两列在共同非缺失行上的 Pearson r；样本不足或常数列返回 None。"""
    if a not in df.columns or b not in df.columns:
        return None
    sub = df[[a, b]].dropna()
    if len(sub) < 3:
        return None
    xa = sub[a].to_numpy(dtype=float)
    xb = sub[b].to_numpy(dtype=float)
    if xa.std() < 1e-12 or xb.std() < 1e-12:
        return None
    return float(np.corrcoef(xa, xb)[0, 1])


def dedupe_duplicate_key_responses(
        key_responses: Sequence[str], raw_data: pd.DataFrame
) -> Tuple[List[str], List[Dict]]:
    """阶段3：同一物理量的双测点只保留一路（方案 §3.3 阶段3 冗余消除的特例）。

    为什么需要：PSGA/PSGB、WFWA/WFWB、LSGA/LSGB 是同一物理量的 A/B 两路测点，
    实测相关系数 0.994~1.000（见 DUPLICATE_CORR_MIN 注释）。两路同时进入强制集
    （forced → protected）会绕过去冗余与 VIF 迭代，导致入选集合 VIF≈∞、V3 恒 fail。

    口径：
      - 只处理 key_responses，触发变量永不参与剔除；
      - 按配置顺序贪心遍历，与「已保留的一路」|r| ≥ DUPLICATE_CORR_MIN 即剔除；
      - 保留先出现的一路（如 PSGA 先于 PSGB），剔除项完整记录配对/相关系数/保留项。

    Returns:
        (保留的 key_responses, 剔除记录列表)
    """
    kept: List[str] = []
    dropped: List[Dict] = []
    for v in key_responses:
        if v not in raw_data.columns:
            kept.append(v)
            continue
        twin: Optional[Tuple[str, float]] = None
        for k in kept:
            r = _pair_corr(raw_data, k, v)
            if r is not None and abs(r) >= DUPLICATE_CORR_MIN:
                twin = (k, r)
                break
        if twin is None:
            kept.append(v)
            continue
        dropped.append({
            "var": v,
            "retained": twin[0],
            "pair": sorted([twin[0], v]),
            "corr": float(twin[1]),
            "stage": DUPLICATE_STAGE,
            "reason": (f"同一物理量双测点：{twin[0]} / {v} 的 |Pearson r|="
                       f"{abs(twin[1]):.6f} ≥ {DUPLICATE_CORR_MIN}，"
                       f"保留 {twin[0]}，剔除 {v}"),
        })
    return kept, dropped


# ==================== 数据构造 ====================
def build_raw_dataframe(accident_type: str, severities: Sequence[int],
                        window_start: float = MAIN_WINDOW_START,
                        window_end: float = MAIN_WINDOW_END
                        ) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """加载某类事故的多条轨迹主窗，拼接为含 trajectory_id 列的 DataFrame。

    变量选取粒度：每类事故一次（非每组一次），故使用该类全部轨迹。

    Returns:
        (raw_data, severity_by_traj)
        raw_data 列 = [数值变量..., trajectory_id, TIME]
    """
    from causalrca.data.loader import load_window
    frames: List[pd.DataFrame] = []
    severity_by_traj: Dict[str, float] = {}
    for sev in severities:
        traj_id = f"{accident_type}_{sev}"
        X, cols, t = load_window(accident_type, sev, window_start, window_end)
        df = pd.DataFrame(X, columns=cols)
        df["trajectory_id"] = traj_id
        df["TIME"] = t
        frames.append(df)
        severity_by_traj[traj_id] = float(sev)
    raw_data = pd.concat(frames, ignore_index=True)
    return raw_data, severity_by_traj


# ==================== 主函数：四阶段变量选取 ====================
def classify_v3(vif_all: Dict[str, float], selected: Sequence[str],
                protected: Sequence[str], forced: Sequence[str],
                subsystem_reps: Sequence[str],
                vif_computed: bool = True) -> Tuple[str, str, List[Dict]]:
    """V3 判定（V23-DESIGN-031，用户裁决"豁免式 pass"）。

    判据 = **非保护变量** 的 max VIF < VIF_MAX。保护变量（forced 触发/关键响应
    + 子系统代表）由设计强制入选、无法被 VIF 迭代剔除，其 VIF 不计入判定，
    但必须逐项披露在返回的 v3_exempt 里，状态记为 pass_with_exemption——
    **豁免不等于通过**，只是把"设计强制"与"自动选择"的共线性分开记账。

    为什么不用"把阈值放宽到 5000"的 (b) 方案：阶段3 已按
    |r| ≥ DUPLICATE_CORR_MIN 剔除重复测点，此处再放宽阈值等于对同一份
    共线性做二次放松，且会掩盖真正的自动选择缺陷。

    Returns:
        (status, note, exempt)；status ∈ {pass, pass_with_exemption, fail, n/a}
    """
    exempt: List[Dict] = []
    if vif_computed and vif_all:
        for v in sorted(set(protected) & set(selected)):
            reasons = []
            if v in forced:
                reasons.append("forced（触发变量/关键响应，强制入选）")
            if v in subsystem_reps:
                reasons.append("subsystem_rep（子系统代表，强制保留）")
            exempt.append({
                "var": v,
                "vif": round(float(vif_all[v]), 1),
                "vif_ge_threshold": bool(vif_all[v] >= VIF_MAX),
                "reason": "；".join(reasons) or "protected（保护变量）",
            })
    if not vif_computed:
        return "n/a", "VIF 无法计算（协方差矩阵奇异），V3 不适用", exempt
    if len(selected) < 3:
        return "n/a", f"入选变量仅 {len(selected)} 个（<3），VIF 不适用", exempt
    non_prot = {v: vif_all[v] for v in selected if v not in set(protected)}
    max_np = max(non_prot.values()) if non_prot else None
    fmt_np = "n/a" if max_np is None else f"{max_np:.1f}"
    offenders = {v: round(float(x), 1) for v, x in non_prot.items() if x >= VIF_MAX}
    if offenders:
        return ("fail",
                f"非保护变量存在 VIF≥{VIF_MAX:.0f}：{offenders}"
                f"（max_nonprot={fmt_np}；{len(exempt)} 个保护变量豁免、不计入此判定）",
                exempt)
    if exempt:
        return ("pass_with_exemption",
                f"非保护变量 VIF 全部 < {VIF_MAX:.0f}（max_nonprot={fmt_np}）；"
                f"{len(exempt)} 个保护变量豁免计入，明细见 v3_exempt——"
                f"豁免不等于通过，必须逐项披露",
                exempt)
    return ("pass",
            f"入选集合 VIF 全部 < {VIF_MAX:.0f}（无保护变量豁免）", exempt)


def classify_v5(key_responses: Sequence[str],
                key_responses_configured: Sequence[str],
                selected: Sequence[str],
                dropped_key_response: Sequence[Dict]) -> Tuple[bool, str]:
    """V5 判定（V23-DESIGN-032，用户裁决：**有效集语义**）。

    同一物理量的重复测点对，只要留有另一路代表即算覆盖；配对/相关系数/
    保留哪一路/剔除阶段全部写进 note。**仅限**这种"重复测点等价代表"，
    其他关键响应缺失仍判 False（不放宽）。
    """
    missing = [v for v in key_responses if v not in selected]
    ok = not missing
    if dropped_key_response:
        pairs = "；".join(
            f"{d['pair'][0]}/{d['pair'][1]} |r|={abs(d['corr']):.6f} "
            f"保留 {d['retained']} 剔除 {d['var']} @{d['stage']}"
            for d in dropped_key_response)
        note = (f"有效集判定 V5={'pass' if ok else 'fail'}："
                f"关键响应 {len(key_responses)}/{len(key_responses_configured)} 路"
                f"{'全部入选' if ok else f'缺失 {missing}'}；"
                f"阶段3 重复测点等价代表剔除 {len(dropped_key_response)} 路：{pairs}")
    else:
        note = (f"有效集判定 V5={'pass' if ok else 'fail'}："
                f"关键响应 {len(key_responses)} 路"
                f"{'全部入选（无重复测点剔除）' if ok else f'缺失 {missing}'}")
    return ok, note


def select_variables(accident_type: str, raw_data: pd.DataFrame,
                     severity_by_traj: Dict[str, float],
                     random_state: int = 42
                     ) -> Tuple[List[str], Dict]:
    """四阶段变量选取（方案 §3.4）。

    Args:
        accident_type: 事故类（LOCA/SGATR/FLB）
        raw_data: DataFrame，含 trajectory_id 列 + 数值变量列
        severity_by_traj: {trajectory_id: severity_value}
        random_state: 随机种子（MI 计算可复现）

    Returns:
        (selected_vars, selection_report)
    """
    np.random.seed(random_state)

    # 仅数值列，排除标识列
    num_cols = [c for c in raw_data.select_dtypes(include=[np.number]).columns
                if c not in EXCLUDE_COLS]

    # ---------- 阶段1：硬过滤（方案 §3.3 阶段1）----------
    candidates: List[str] = []
    stage1_log: List[Dict] = []
    for v in num_cols:
        raw_series = raw_data[v]
        missing_rate = float(raw_series.isna().mean())
        series = raw_series.dropna()
        if len(series) == 0:
            stage1_log.append({"var": v, "reason": "all_nan"})
            continue
        sigma = float(series.std())
        nunique = int(series.nunique())
        if sigma < SIGMA_MIN or nunique < NUNIQUE_MIN:
            stage1_log.append({
                "var": v,
                "reason": f"near_constant(sigma={sigma:.2e},nunique={nunique})",
            })
            continue
        if missing_rate > MISSING_RATE_MAX:
            stage1_log.append({"var": v, "reason": f"high_missing({missing_rate:.2f})"})
            continue
        if (series == 0).all():
            stage1_log.append({"var": v, "reason": "all_zero"})
            continue
        candidates.append(v)

    if not candidates:
        log_error("CODE", f"变量选取阶段1无候选变量 ({accident_type})",
                  "所有变量被硬过滤剔除", "检查数据窗口与过滤阈值", "open")
        return [], {"error": "no candidates after stage 1", "stage1_log": stage1_log}

    # ---------- 阶段2：数据驱动评分（方案 §3.3 阶段2）----------
    traj_stats = raw_data.groupby("trajectory_id")[candidates].mean()
    sev_array = np.array([severity_by_traj.get(tid, 0.0) for tid in traj_stats.index])

    raw_metrics: Dict[str, Dict[str, float]] = {"cv": {}, "non_gauss": {}, "skew": {}, "mi": {}, "cross_disc": {}}
    for v in candidates:
        series = raw_data[v].dropna()
        mu = float(series.mean())
        sigma_raw = float(series.std())
        # σ_floor 修正：防 Z 病态（暂用 1%·|μ| 兜底，逐变量下限待 §5.9 tolerance 配置）
        mad = float(np.median(np.abs(series - mu)))
        sigma_robust = (max(1.4826 * mad, 0.01 * abs(mu))
                        if abs(mu) > 1e-9 else sigma_raw)
        sigma = max(sigma_raw, sigma_robust)
        raw_metrics["cv"][v] = sigma / abs(mu) if abs(mu) > 1e-9 else sigma
        raw_metrics["non_gauss"][v] = float(abs(series.skew()) + abs(series.kurt()))
        raw_metrics["skew"][v] = float(abs(series.skew()))

        v_traj_mean = traj_stats[v].values
        if len(np.unique(sev_array)) > 1:
            raw_metrics["mi"][v] = float(mutual_info_regression(
                v_traj_mean.reshape(-1, 1), sev_array, random_state=random_state)[0])
        else:
            raw_metrics["mi"][v] = 0.0

        within_var = float(raw_data.groupby("trajectory_id")[v].var().mean())
        total_var = float(series.var())
        cd = 1.0 - (within_var / total_var) if total_var > 1e-12 else 0.0
        raw_metrics["cross_disc"][v] = float(max(0.0, min(1.0, cd)))

    cv_norm = norm_quantile_dict(raw_metrics["cv"])
    ng_norm = norm_quantile_dict(raw_metrics["non_gauss"])
    mi_norm = norm_quantile_dict(raw_metrics["mi"])

    scores: Dict[str, float] = {}
    for v in candidates:
        scores[v] = (W_CV * cv_norm[v]
                     + W_NON_GAUSS * ng_norm[v]
                     + W_MI * mi_norm[v]
                     + W_CROSS_DISC * raw_metrics["cross_disc"][v])

    top_k = sorted(scores, key=scores.get, reverse=True)[:TOP_K]

    # ---------- 阶段3：物理知识校验（方案 §3.3 阶段3）----------
    trigger = get_trigger_var(accident_type)
    key_responses_configured = get_key_responses(accident_type)
    # 精确重复测点去重（V23-DESIGN-028）：同一物理量的 A/B 双测点只保留一路，
    # 另一路不进入 forced，故不会被 protected 永久钉住、也不会抬高 V3 的 VIF。
    key_responses, dropped_key_response = dedupe_duplicate_key_responses(
        key_responses_configured, raw_data)
    if dropped_key_response:
        logger.info(
            f"[变量选取] {accident_type}: 阶段3 剔除重复测点 "
            f"{[(d['var'], d['retained'], d['corr']) for d in dropped_key_response]}")
    forced = [v for v in [trigger] + key_responses if v is not None and v in num_cols]

    # 被剔除的重复测点此后不得以任何路径回流（V23-DESIGN-028）：
    #   阶段2 TOP_K 里可能本来就有它；它可能被选为子系统代表（→ protected）；
    #   可能是锚定变量；也可能被 N_MIN 回补。四条路径统一封死。
    dropped_key_vars = {d["var"] for d in dropped_key_response}
    retained_of = {d["var"]: d for d in dropped_key_response}
    if dropped_key_vars:
        top_k = [v for v in top_k if v not in dropped_key_vars]

    for v in forced:
        if v not in top_k and v in candidates:
            top_k.append(v)

    required_subsystems = get_required_subsystems(accident_type)
    # 每子系统选取得分最高的代表变量（V2 子系统覆盖，受 VIF 保护）。
    # 若最高分恰是被剔除的重复测点，则代表权交给同物理量已入选的另一路
    # （不得因此改用次高分变量——那会凭空引入一个未被选中的保护变量）。
    subsystem_reps: List[str] = []
    for sub in required_subsystems:
        sub_cands = [v for v in candidates if get_subsystem(v) == sub]
        if not sub_cands:
            continue
        best = max(sub_cands, key=lambda v: scores.get(v, 0.0))
        if best in retained_of:
            best = retained_of[best]["retained"]
        subsystem_reps.append(best)
        if best not in top_k:
            top_k.append(best)

    # ---------- 阶段3b：论文锚定（V23-DESIGN-018；返工 V23-DESIGN-023）----------
    # 论文原文：最终变量集 = XGBoost 特征选择 + 领域专家验证（p.4），但未给出名单；
    # 代理取项目「早期机理筛选集」（工程假设，非 v2.1 硬编码集）。
    # 语义（返工）：候选优先 + 评分加权，**不是保护集**；VIF 迭代/病态处理可剔除，
    # 剔除必须写理由（近常量/缺列/Z 病态/VIF 实测值）与影响（见 anchor_impact）。
    anchor_spec = build_anchor(accident_type, num_cols)
    anchor_all = list(anchor_spec["anchor_variables"])
    anchor_dropped: List[Dict] = []
    for v in anchor_all:
        if v in retained_of:
            d = retained_of[v]
            anchor_dropped.append({
                "var": v, "stage": "stage3_duplicate",
                "reason": (f"与保留测点 {d['retained']} 为同一物理量的重复测点"
                           f"（|Pearson r|={abs(d['corr']):.6f}），阶段3 去重剔除"),
            })
            continue
        if v in candidates:
            continue
        if v in anchor_spec["missing_in_data"]:
            reason = "missing_in_data"   # 数据集未采集该列，锚定自然失效（不报错）
        else:
            reason = stage1_drop_reason(v, stage1_log) or "not_numeric_column"
        anchor_dropped.append({"var": v, "stage": "stage1_or_missing", "reason": reason})

    anchor_vars = [v for v in anchor_all
                   if v in candidates and v not in dropped_key_vars]
    for v in anchor_vars:
        scores[v] = min(1.0, float(scores.get(v, 0.0)) + ANCHOR_SCORE_BONUS)
    for v in anchor_vars:
        if v not in top_k:
            top_k.append(v)

    top_k = deduplicate(top_k, scores, forced=list(forced))
    for v in anchor_vars:
        if v not in top_k:
            anchor_dropped.append({
                "var": v, "stage": "deduplicate",
                "reason": "与原集合高相关且评分低于保留变量（去冗余阶段剔除）",
            })

    # ---------- 阶段4：因果发现适配（方案 §3.3 阶段4）----------
    selected = list(top_k)
    n_vars_before_vif = len(selected)
    vif_log: List[Dict] = []
    # 保护集 = 强制变量（触发+关键响应）+ 每子系统代表（保证 V2 覆盖）；
    # 锚定变量不在保护集内：可被 VIF 迭代剔除（V23-DESIGN-023）。
    protected = set(forced) | set(subsystem_reps)

    max_vif_before_removal = None
    if len(selected) > 1:
        vifs0 = compute_vif_safe(raw_data[selected].dropna())
        max_vif_before_removal = max(vifs0.values()) if vifs0 else None

    # 4a. VIF 迭代剔除（锚定变量同样参与；剔除留理由与当时影响）
    # 上界用「仍在集合内的保护变量」计数：去冗余可能已剔除某子系统代表，
    # 若按 protected 原始集合计数，迭代会被提前终止（LOCA 曾因此卡在 11 变量）。
    while len(selected) > len(protected & set(selected)):
        vifs = compute_vif_safe(raw_data[selected].dropna())
        if vifs is None:
            log_error("ALGO", f"VIF 矩阵奇异 ({accident_type})",
                      "多重共线性导致 VIF 无法计算",
                      "停止 VIF 剔除，保留当前变量集", "open")
            break
        non_protected = [v for v in selected if v not in protected]
        if not non_protected:
            break
        max_vif_var = max(non_protected, key=lambda v: vifs.get(v, 0.0))
        if vifs.get(max_vif_var, 0.0) < VIF_MAX:
            break
        entry = {"var": max_vif_var, "vif": vifs[max_vif_var],
                 "max_vif_at_drop": max(vifs.values()),
                 "n_vars_before": len(selected)}
        vif_log.append(entry)
        if max_vif_var in anchor_vars:
            mu = float(raw_data[max_vif_var].mean())
            sd = float(raw_data[max_vif_var].std())
            z_path = bool(abs(mu) > 1e-9 and abs(sd / mu) < 0.01)
            anchor_dropped.append({
                "var": max_vif_var, "stage": "vif_iter",
                "reason": f"vif={vifs[max_vif_var]:.3e} >= VIF_MAX({VIF_MAX:.0f})",
                "vif": float(vifs[max_vif_var]),
                "z_pathological": z_path,
                "max_vif_at_drop": float(max(vifs.values())),
                "n_vars_before": len(selected),
            })
        selected.remove(max_vif_var)

    # 4b. 变量数约束 15-25
    if len(selected) > N_MAX:
        protected_kept = protected & set(selected)
        non_protected_sorted = sorted([v for v in selected if v not in protected],
                                       key=lambda v: scores.get(v, 0.0), reverse=True)
        trimmed = non_protected_sorted[:N_MAX - len(protected_kept)]
        for v in non_protected_sorted[N_MAX - len(protected_kept):]:
            if v in anchor_vars:
                anchor_dropped.append({
                    "var": v, "stage": "n_max_trim",
                    "reason": f"变量数>{N_MAX}，按评分裁剪（score={scores.get(v, 0.0):.3f}）",
                })
        selected = list(protected_kept) + trimmed
    elif len(selected) < N_MIN:
        remaining = sorted([v for v in candidates
                            if v not in selected and v not in dropped_key_vars],
                           key=lambda v: scores.get(v, 0.0), reverse=True)
        for v in remaining:
            if len(selected) >= N_MIN:
                break
            trial = selected + [v]
            vifs = compute_vif_safe(raw_data[trial].dropna())
            # 回补校验：trial 中所有非保护变量 VIF 须 < 阈值（防已有变量被推高）
            non_prot_vifs = {vv: vifs[vv] for vv in trial if vv not in protected} if vifs else {}
            if vifs is not None and all(val < VIF_MAX for val in non_prot_vifs.values()):
                selected.append(v)
            else:
                log_error("STAT", f"回补变量 {v} 致 VIF 超标 ({accident_type})",
                          "回补变量引入或加剧共线性", "跳过该候选，尝试下一个", "open")

    # 列序口径固定（V23-CODE-023）：进入因果发现前按字母序排一次，
    # 后续 VIF 报告/落盘/建模全部使用该顺序，杜绝选择器内部顺序外泄到 Top1。
    selected = canonical_variable_order(selected)

    # 去冗余阶段记录可能因 N_MIN 回补而失效：最终在集合内的锚定变量不算剔除，
    # 单列 readded_anchor 记录“剔了又补回”，避免同一变量同时出现在 retained/dropped。
    readded_anchor = sorted({d["var"] for d in anchor_dropped if d["var"] in selected})
    anchor_dropped = [d for d in anchor_dropped if d["var"] not in selected]

    # ---------- 校验报告（方案 §3.5 V0-V7）----------
    final_vifs = compute_vif_safe(raw_data[selected].dropna())
    # V7 方案口径：|skewness| > 0.5 的变量占比 ≥ 60%（评审 P1-8：原代码误将 |skew|+|kurt| 合并）
    skew_frac = (sum(1 for v in selected if raw_metrics["skew"].get(v, 0) > 0.5)
                 / max(len(selected), 1))
    # V3 判定（V23-DESIGN-031 豁免式 pass，判据/理由见 classify_v3 docstring）
    vif_all = {v: float(final_vifs[v]) for v in selected} if final_vifs else {}
    non_protected_vifs = {v: vif_all[v] for v in selected if v not in protected} if vif_all else {}
    max_vif_all = max(vif_all.values()) if vif_all else None
    max_nonprot = max(non_protected_vifs.values()) if non_protected_vifs else None
    v3_status, v3_note, v3_exempt = classify_v3(
        vif_all, selected, protected, forced, subsystem_reps,
        vif_computed=final_vifs is not None)

    # 锚定保留记录：Z 病态仅标记，不再作为保留理由
    anchor_retained: List[Dict] = []
    for v in anchor_vars:
        if v not in selected:
            continue
        mu = float(raw_data[v].mean())
        sd = float(raw_data[v].std())
        anchor_retained.append({
            "var": v,
            "z_pathological": bool(abs(mu) > 1e-9 and abs(sd / mu) < 0.01),
            "vif": None if not vif_all else round(float(vif_all.get(v, 0.0)), 1),
            "action": "retained",
        })

    # V5 判定（V23-DESIGN-032 有效集语义，口径见 classify_v5 docstring）
    v5_ok, v5_note = classify_v5(
        key_responses, key_responses_configured, selected, dropped_key_response)
    v5_missing = [v for v in key_responses if v not in selected]

    report = {
        "accident_type": accident_type,
        "n_candidates_stage1": len(candidates),
        "stage1_excluded": stage1_log,
        "scores": scores,
        "raw_metrics": raw_metrics,
        "forced": forced,
        "key_responses_configured": key_responses_configured,
        "key_responses_effective": key_responses,
        "dropped_key_response": dropped_key_response,
        "duplicate_corr_min": DUPLICATE_CORR_MIN,
        "v5_note": v5_note,
        "v5_missing": v5_missing,
        "subsystem_reps": subsystem_reps,
        "protected": sorted(protected),
        "protected_in_selected": sorted(protected & set(selected)),
        "required_subsystems": required_subsystems,
        "vif_log": vif_log,
        "n_selected": len(selected),
        "final_vif": final_vifs,
        "non_protected_vif": non_protected_vifs,
        "vif_all": vif_all,
        "v3_status": v3_status,
        "v3_note": v3_note,
        "v3_criterion": (f"非保护变量 max VIF < {VIF_MAX:.0f}；"
                         "保护变量（forced/子系统代表）豁免并逐项披露"),
        "v3_exempt": v3_exempt,
        "n_v3_exempt": len(v3_exempt),
        "max_vif": max_nonprot,
        "max_vif_all": max_vif_all,
        "vif_max_threshold": VIF_MAX,
        "selected": selected,
        "variable_order_rule": VARIABLE_ORDER_RULE,
        "paper_anchor": {
            "source": anchor_spec["source"],
            "citations": anchor_spec["citations"],
            "paper_n_variables": anchor_spec["paper_n_variables"],
            "paper_explicit_variables": anchor_spec["paper_explicit_variables"],
            "explicit_is_empty_reason": anchor_spec["explicit_is_empty_reason"],
            "anchor_mode": anchor_spec["anchor_mode"],
            "anchor_score_bonus": ANCHOR_SCORE_BONUS,
            "anchor_proxy_source": anchor_spec["anchor_proxy_source"],
            "v21_set_reference": anchor_spec["v21_set_reference"],
            "proxy_vs_v21_diff": anchor_spec["proxy_vs_v21_diff"],
            "anchor_variables": anchor_spec["anchor_variables"],
            "anchor_used": anchor_vars,
            "n_anchor_used": len(anchor_vars),
            "anchor_retained": anchor_retained,
            "n_anchor_retained": len(anchor_retained),
            "dropped_anchor": anchor_dropped,
            "readded_anchor": readded_anchor,
            "dropped_stage1": [d for d in anchor_dropped
                               if d["stage"] == "stage1_or_missing"],
            "anchor_impact": {
                "n_anchor_declared": len(anchor_all),
                "n_anchor_retained": len(anchor_retained),
                "n_anchor_dropped": len(anchor_dropped),
                "max_vif_all_before_removal": max_vif_before_removal,
                "max_vif_all_final": max_vif_all,
                "n_vars_before_vif": n_vars_before_vif,
                "n_vars_final": len(selected),
                "top1_change_note": ("Top-1 属下游产物；剔除代价见 error_tracking "
                                     "对应验收记录的 run 间 Top-1 对比"),
            },
            "conflict_policy": anchor_spec["conflict_policy"],
            "note": anchor_spec["note"],
        },
        "random_state": random_state,
        "non_gauss_frac": skew_frac,
        "checks": {
            "V0_anchor_accounted": all(
                (v in selected) or any(d["var"] == v for d in anchor_dropped)
                for v in anchor_all
            ),
            "V1_count_15_25": N_MIN <= len(selected) <= N_MAX,
            "V2_subsystem_coverage": all(
                any(get_subsystem(v) == sub for v in selected)
                for sub in required_subsystems
            ),
            # 三态：True=pass/pass_with_exemption / False=fail / None=n/a
            # 判据口径见 v3_note：非保护变量 max VIF < VIF_MAX；保护变量豁免且逐项披露
            "V3_vif_lt_500": (None if v3_status == "n/a"
                              else v3_status in ("pass", "pass_with_exemption")),
            "V4_trigger_included": trigger in selected,
            # 有效集语义：重复测点只要留有另一路代表即算覆盖，其余关键响应缺失仍 False
            "V5_key_responses_included": v5_ok,
            "V6_reproducible": None,   # 由调用方二次运行后回填
            "V7_skew_gt_0.5_60pct": skew_frac >= 0.6,
        },
    }

    all_pass = all(v is not False for v in report["checks"].values())
    _fmt = lambda x: "n/a" if x is None else f"{x:.1f}"  # noqa: E731
    logger.info(f"[变量选取] {accident_type}: {len(selected)} 变量入选 "
                f"(V0={report['checks']['V0_anchor_accounted']}, "
                f"V1={report['checks']['V1_count_15_25']}, "
                f"V2={report['checks']['V2_subsystem_coverage']}, "
                f"V3={v3_status} "
                f"(max_nonprot={_fmt(report['max_vif'])}, max_all={_fmt(max_vif_all)}), "
                f"V4={report['checks']['V4_trigger_included']}, "
                f"V5={report['checks']['V5_key_responses_included']}, "
                f"V7={report['checks']['V7_skew_gt_0.5_60pct']}) "
                f"anchor={len(anchor_retained)}/{len(anchor_all)} "
                f"all_pass={all_pass}")

    if not all_pass:
        log_error("DESIGN", f"变量选取 V1-V7 未全通过 ({accident_type})",
                  "自动选取变量集未满足全部校验门控",
                  "保留当前选择集并如实记录失败项（不自动回退，需人工裁决）", "open")

    return selected, report

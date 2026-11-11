"""V2.3 D11：留出集评测（第 10 步，reports 之后 / manifest 之前）。

**任务定义（V23-DESIGN-036）**
v2.3 不做事故大类匹配，只做「事故内、变量级的根因溯源」。评测因此也是变量级：
对每条留出测试轨迹，把该模板的根因候选**变量排序**与「该事故类型的始发（注入）变量
真值集」比对，算 Hit@1 / Hit@3 / MRR / P@2 / P@3 / Recall@3 / AP@3 / Jaccard@3，
按模板块（(类,组)，6–7 条）、按类（20 条）、全局（60 条）三层汇总。
**不沿用 v2.1 的匹配范式**：不做事故类型分类、不把 9 个模板当检索候选池、
不用关键响应变量/forced 保护集当判据。（模板级切片只是同一变量级指标按 (K,g) 分组，
不是“模板检索”任务，两者不混表。）

**真值（唯一来源）**
`causalrca/config/injection_truth.py::INJECTION_TRUTH`（对齐老师项目 GROUND_TRUTH，
见该文件 docstring）。真值集按「只剔除数据里不存在的列」处理；真值变量是否落在候选集
（模板 15 个节点）内单独披露 `gt_in_candidate_set`，用于区分“没找对”和“根本不在候选”。

**候选与四通道（同一批轨迹；取消遮蔽后四通道统一为 15 节点）**
- `causal`（生产口径）：候选=模板**全部变量（含注入变量 marker）**，排序键总因果效应
  DESC → in_degree ASC → weighted_reach DESC → reach_size DESC → consensus_rate DESC，
  再按“有传导能力(out_degree>0)优先、孤立节点殿后”分组。取消遮蔽（2026-11-11）后与落盘
  `tracing/root_candidates_*.json` 的候选集一致（15 节点）。
- `causal_unmasked_for_eval`（**历史别名**）：取消遮蔽前它是“把注入点放回候选”的评测专用列；
  取消遮蔽后与 `causal` 完全等价，仅为兼容旧产物/旧文档保留，产物里标 `legacy_alias=true`。
- `deviation_equal_weight` 与 `deviation_production_set`：纯偏差基线，同候选集等权，只按该
  轨迹偏移强度 d(v)=max|x−μ_pre|/max(1.4826·MAD_pre, σ_floor(v)) 降序，完全不用因果图。
  取消遮蔽后“生产集”=“全集”（同为 15 节点），两列数值因此一致；`deviation_production_set`
  同样标 `legacy_alias=true` 以兼容旧四通道表。

**指标分层（primary / diagnostic）**
本工作**不主张准确率**：主表只含 `stability` / `structural_health` / `mechanism_consistency`
三个 primary 块；`hit1/hit3/mrr/p2/p3/recall3/ap3/jaccard3` 一律为 diagnostic（附录诊断），
产物中逐块标 `metric_tier`，**不得进结论句、不得作论文数字**。

**结果层撤出（V23-DESIGN-063，2026-11-11 用户裁决）**
上述 diagnostic 指标，加上稳定性块的推断量（跨组 Jaccard / Spearman / 单调占比 / 名次标准差 /
bootstrap 翻转率）、VIF 门与条件数门的通过结论、截断翻转、偏差通道 `d` 排序，
**一律不再进入结果层**：字段与数值仍留在产物 json 里（复算与审计需要），但逐块标
`reported=false` / `reliability="unsupported"` / `metric_status="known_unusable"`
（语义是“已知不可用”，不是“待完善”），并在 `metrics_summary.result_layer_policy`
汇总三条系统性前提（P1 无独立真值 / P2 样本不独立 / P3 门槛不可失败）。
渲染层（`report_template`、类级汇总、`submit/` 页面与结果列表）不得展示这些数字。
处置待定 2026-11-12。

**B_CLIP 敏感性（V23-DESIGN-038，评测一等公民）**
总效应是 T=(I−B)⁻¹ 的 |T| 行和，`B_CLIP` 硬截断会改名次：`LOCA_high` 的 `WLR` 在 clip5 下第 1、
去截断（clip_off）下第 3。因此每个模板与汇总都必须并列 `clip3 / clip5 / clip10 / clip_off`
四列的 Hit@1 / Hit@3 / MRR（+ 注入点名次中位数），并自动列出“随 clip 翻转”的模板（`flips`）。
**只报 clip5 一列不得当结论。**

**与老师 accuracy 的关系**
同任务（事故内变量级 Top-k 命中）、但**不同协议**：我们无人工先验降权、切分严格互斥
（split_manifest.test_severities）、每 (类,组) 单独建图、候选含注入变量（取消遮蔽），
且另给纯偏差基线两列。
因此数字**不并排比较**，只作脚注引用。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

from causalrca.causal import source_tracing as ST
from causalrca.config import injection_truth as IT
from causalrca.config.constants_v21 import GROUP_ORDER, SEVERITY_BANDS
from causalrca.config.constants_v23 import (
    B_CLIP, N_BOOTSTRAP_SPEC, run_mode_for, stability_claims_allowed,
)
from causalrca.data import loader as D
from causalrca.evidence import external_knowledge as EK
from causalrca.io import artifact
from causalrca.io.logging import logger
from causalrca.reporting import truth_reachability as TR
from causalrca.config import role_prior as RP
ROLE_PRIOR_CHANNEL = "causal_role_prior"

# 评测对象硬规则（V23-DESIGN-055，2026-11-11 用户裁决）：
# 评测对象只能是「事故内变量级根因溯源」；类别识别/模板识别/模板检索类指标
# 一律为实现侧诊断，不得进主表、不得进结论句、不得作论文数字。
EVALUATION_TARGET = "variable_level_in_accident"
RETRIEVAL_METRIC_STATUS = "not_evaluation_target"


def _all_channels() -> tuple:
    """通道全集：基础四通道 + 角色先验通道（B1；成对出现才算合规）。"""
    return tuple(CHANNELS) + (ROLE_PRIOR_CHANNEL,)

CUTOFF = 3                       # 指标截断 @3（与老师 metrics_for_ranking 的 k=3 一致）
BASELINE_LEAD_SEC = 10.0         # 注入前窗口末端留 10s 余量（[0, t_inject−10]）
CHANNELS = ("causal", "causal_unmasked_for_eval",
            "deviation_equal_weight", "deviation_production_set")
# B_CLIP 敏感性（V23-DESIGN-038）：变量级命中对总效应截断阈值敏感，必须并列四列
CLIP_VARIANTS = (("clip3", 3.0), ("clip5", B_CLIP), ("clip10", 10.0), ("clip_off", None))
METRIC_KEYS = ("hit1", "hit3", "mrr", "p2", "p3", "recall3", "ap3", "jaccard3")

# ==================== 指标分层（主表 vs 附录诊断）====================
# 本工作不主张准确率：主表只允许 primary 块；一切命中/排序类指标只作附录诊断。
# 这两组常量同时是守卫测试的契约（tests/test_v23_evaluation.py）。
METRIC_TIER_PRIMARY = "primary"          # 主表：稳定性 / 结构健康度 / 机理一致性
METRIC_TIER_DIAGNOSTIC = "diagnostic"    # 附录：hit@k、MRR、P@k、Recall@k、AP@k、Jaccard@k
PRIMARY_BLOCKS = ("stability", "structural_health", "mechanism_consistency")
DIAGNOSTIC_METRICS = ("hit1", "hit3", "mrr", "p2", "p3", "recall3", "ap3", "jaccard3")


# ==================== 结果层撤出（V23-DESIGN-063，2026-11-11 用户裁决）====================
# 三条系统性前提（完整表述见方案 §7.10）：
#   P1 无独立真值：命中类真值是按机理人工指定的假设，且与我方候选集存在构造关联；
#   P2 样本不独立：同模板 20 条轨迹共享同一张图，因果通道全 run 只有 9 个不同排序；
#   P3 门槛不可失败：VIF 门 / 条件数门 / 跨组一致性 / 偏差通道 d。
# 处置：以下指标**不再进入结果层**。字段仍保留在产物 json 里（历史与复算需要），
# 但一律 reported=false、reliability=unsupported、metric_status=known_unusable——
# 语义是「已知不可用」，不是「待完善」。生成函数保留、仅作内部计算留档，
# 不得进正文、结论句、图注或论文数字；是否彻底删除等用户 2026-11-12 裁决。
METRIC_STATUS_KNOWN_UNUSABLE = "known_unusable"
METRIC_STATUS_PENDING_EXPERT = "pending_expert"
METRIC_STATUS_DESCRIPTIVE = "descriptive"
RELIABILITY_UNSUPPORTED = "unsupported"
RELIABILITY_DESCRIPTIVE = "descriptive"
RESULT_LAYER_WITHDRAWN_AT = "2026-11-11"
WITHDRAWAL_DISPOSITION = "处置待定 2026-11-12"

# 逐指标留档：前提（P1/P2/P3）、为何不可解读、若要用需补什么。文档 §7.10 与产物共用此表。
UNSUPPORTED_METRICS: Dict[str, Dict[str, str]] = {
    "hit1": {"premise": "P1+P2", "why": "真值为人工假设且与候选集构造关联；因果通道 n_eff=9 而非 60",
             "needs": "独立真值 + 按模板聚类的有效 n + 随机基线（15 选 1 = 0.0667）"},
    "hit3": {"premise": "P1+P2", "why": "同上；随机基线 0.20，实测低于随机",
             "needs": "同上；按 9 模板分层后重算"},
    "mrr": {"premise": "P1+P2", "why": "真值不可达时上限被结构性截断；随机期望 0.2212，实测低于随机",
            "needs": "独立真值 + 有效 n + 名次分布"},
    "p2": {"premise": "P1", "why": "真值集大小 1 vs 3 跨类不可比", "needs": "按真值集大小分层 + 随机基线"},
    "p3": {"premise": "P1", "why": "同上；随机基线随真值集大小变化", "needs": "同上"},
    "recall3": {"premise": "P1", "why": "LOCA 真值 3 个仅 1 个在候选内，上限即 1/3",
                "needs": "只在可达真值上算，或并列「可达真值召回」与「全集召回」"},
    "ap3": {"premise": "P1", "why": "同 Recall@3；真值不可达时被结构性压低", "needs": "同 Recall@3 + 有效 n"},
    "jaccard3": {"premise": "P1", "why": "真值集大小不可比，不可达真值使分母虚高", "needs": "按真值集大小分层"},
    "wilson95": {"premise": "P2", "why": "按 n=60/20 计算，区间被系统性压窄（n_eff=9/3）",
                 "needs": "按模板聚类的有效 n 或 cluster bootstrap，并打印 n_eff"},
    "rank_distribution": {"premise": "P1+P2", "why": "22% 真值不可达（coverage 0.7778）；27/60 落在同一名次",
                          "needs": "只在可达真值上统计 + 按模板分层"},
    "topk_jaccard_across_groups": {"premise": "P2+P3",
                                   "why": "实测 0.2632 vs 随机基线 0.2144，三对均值≥实测的概率 0.30",
                                   "needs": "置换零分布 + 按类 CI"},
    "cross_group_consistency": {"premise": "P3", "why": "无零分布；三组来自同一套选变量/建图流程，不独立",
                                "needs": "置换零分布 + 按类 CI"},
    "rank_std_across_groups": {"premise": "P3", "why": "实测中位 1.89–2.87 vs 随机期望 3.22–3.40，与随机不可区分",
                               "needs": "随机基线 + CI"},
    "monotonicity": {"premise": "P3", "why": "实测 1/15–5/15 vs 随机 0.27–0.33，不超过随机",
                     "needs": "置换基线 + 预设物理方向"},
    "vif_gate": {"premise": "P3", "why": "500 既是阈值又是迭代停止条件；非保护 max 恰为 489.6/490.0/276.3",
                 "needs": "报告含保护变量的 max VIF，或取消通过/失败语义"},
    "cond_gate": {"premise": "P3", "why": "阈值 10000 无来源，实测截断后最高 1054.8，9/9 全过",
                  "needs": "阈值来源 + 失败样例 + 与排序稳定性的联合判据"},
    "clip_flip": {"premise": "P3", "why": "无预设容差、无 5/6 名 margin、B_CLIP 无物理依据",
                  "needs": "容差 + 翻转分解 + |B| 在 5 附近的分布"},
    "deviation_channel": {"premise": "P3",
                          "why": "d 跨变量不可比（量纲与各自噪声/下限归一，d_raw 到 1.67e5）",
                          "needs": "共同物理/影响量纲；在此之前禁止跨变量排序"},
}


def _withdrawn_fields(metrics: Sequence[str] = ()) -> Dict:
    """结果层撤出的统一标记（字段保留、数值保留，但显式声明不进结果）。"""
    out: Dict[str, object] = {
        "reported": False,
        "reliability": RELIABILITY_UNSUPPORTED,
        "metric_status": METRIC_STATUS_KNOWN_UNUSABLE,
        "not_in_result_layer": True,
        "withdrawn_at": RESULT_LAYER_WITHDRAWN_AT,
        "disposition": WITHDRAWAL_DISPOSITION,
    }
    if metrics:
        out["withdrawn_metrics"] = list(metrics)
        out["withdrawn_reason"] = {m: UNSUPPORTED_METRICS.get(m, {}) for m in metrics
                                   if m in UNSUPPORTED_METRICS}
    return out


def _withdraw(block: Dict, metrics: Sequence[str]) -> Dict:
    """给任意子块补“结果层撤出”标记：数值不动，只加 reported=false 一类字段。

    用途：这些量仍留在产物里供内部复算/审计，但任何渲染层（报告、汇总页、
    结果列表）都不得把它们当结果展示（V23-DESIGN-063）。
    """
    out = dict(block)
    out.update(_withdrawn_fields(metrics))
    return out


def _tag_agg(agg: Dict, tier: str = METRIC_TIER_DIAGNOSTIC) -> Dict:
    """给单个指标聚合块打层级标记；诊断块另给 8 个键的 `tier` 映射与撤出标记。"""
    out = dict(agg)
    out["metric_tier"] = tier
    if tier == METRIC_TIER_DIAGNOSTIC:
        out["tier"] = {key: METRIC_TIER_DIAGNOSTIC for key in METRIC_KEYS}
        out.update(_withdrawn_fields(METRIC_KEYS))
    return out


# ==================== 工具 ====================
def wilson_ci95(k: int, n: int) -> List[Optional[float]]:
    """Wilson 95% 区间（标准式；p=1 时下界 = 1/(1+z²/n)）。"""
    if n <= 0:
        return [None, None]
    z = 1.959963984540054
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [max(0.0, centre - half), min(1.0, centre + half)]


def _read_json(subdir: str, name: str, run_id: Optional[str] = None) -> Dict:
    path = artifact.subdir_path(subdir, run_id) / name
    if not path.exists():
        raise FileNotFoundError(f"评测缺输入：{path}（先跑上游步骤，禁止现算替代）")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def group_of_severity(severity: int) -> str:
    for g in GROUP_ORDER:
        lo, hi = SEVERITY_BANDS[g]
        if lo <= int(severity) <= hi:
            return g
    raise ValueError(f"severity={severity} 不在任何严重度组内")


# ==================== 输入 ====================
def load_split_manifest(run_id: Optional[str] = None) -> Dict:
    doc = _read_json("registry", "split_manifest.json", run_id)
    if not doc.get("classes"):
        raise ValueError("split_manifest.json 缺 classes，无法评测")
    return doc


def load_registry(run_id: Optional[str] = None) -> Dict:
    return _read_json("registry", "accident_registry.json", run_id).get("registry") or {}


def test_traces(split: Dict, classes: Sequence[str],
                groups: Sequence[str]) -> List[Dict]:
    """留出测试轨迹：只取 split_manifest 的 test_severities（训练轨迹不进评测）。"""
    out: List[Dict] = []
    for k in classes:
        cls = split["classes"].get(k)
        if not cls:
            continue
        for g in groups:
            grp = (cls.get("groups") or {}).get(g) or {}
            for sev in grp.get("test_severities") or []:
                out.append({"accident_type": k, "group": g, "severity": int(sev),
                            "template": f"{k}_{g}"})
    return out


def load_template(k: str, g: str, run_id: Optional[str] = None) -> Dict:
    """模板输入：拓扑节点 + 签名稳定边（供去遮蔽重算总效应）。"""
    topo = _read_json("tracing", f"topology_{k}_{g}.json", run_id)
    rc = _read_json("tracing", f"root_candidates_{k}_{g}.json", run_id)
    sig = _read_json("signatures", f"signature_{k}_{g}.json", run_id)
    nodes = topo.get("nodes") or {}
    if not nodes:
        raise ValueError(f"topology_{k}_{g}.json 缺 nodes")
    return {"k": k, "g": g, "template": f"{k}_{g}", "nodes": nodes,
            "marker": sig.get("marker") or topo.get("marker"),
            "variables": list(sig.get("variables") or topo.get("variables") or []),
            "stable_edges": sig.get("stable_edges") or [],
            "marker_info": rc.get("marker_info") or {},
            "n_bootstrap": sig.get("n_bootstrap"),
            "method_note": rc.get("method_note")}


# ==================== 排序 ====================
def _production_order(tpl: Dict, total_effect: Dict[str, float],
                      exclude: Sequence[str] = (), role_prior: bool = False) -> List[str]:
    """生产口径排序：效应 DESC → 次级拓扑键；有传导能力者优先，孤立节点殿后。

    `role_prior=True`（B1）：命中外部响应变量名单者，**排序键**乘 0.30（仅乘子）。
    """
    def key(v):
        nd = tpl["nodes"][v]
        return (-(float(total_effect.get(v) or 0.0) * RP.multiplier(v, role_prior)),
                int(nd.get("in_degree") or 0),
                -float(nd.get("weighted_reach") or 0.0),
                -float(nd.get("reach_size") or 0),
                -float(nd.get("consensus_rate") or 0.0),
                v)
    cands = [v for v in tpl["nodes"] if v not in set(exclude)]
    cands.sort(key=key)
    conductive = [v for v in cands if int(tpl["nodes"][v].get("out_degree") or 0) > 0]
    isolated = [v for v in cands if int(tpl["nodes"][v].get("out_degree") or 0) == 0]
    return conductive + isolated


def causal_ranking(tpl: Dict, role_prior: bool = False) -> List[str]:
    """生产口径（取消遮蔽后）：候选=模板全部节点（含注入变量），
    效应取 topology 节点已落盘 total_effect（不重算 T、不改产物）。"""
    eff = {v: (nd.get("total_effect") or 0.0) for v, nd in tpl["nodes"].items()}
    return _production_order(tpl, eff, role_prior=role_prior)


def unmasked_total_effect(tpl: Dict, clip: Optional[float] = B_CLIP) -> Dict[str, float]:
    """评测模式：把注入点放回候选，用已落盘 stable_edges 重算总效应（纯矩阵，无 bootstrap）。

    `clip` 只影响 |B| 的硬截断上限；clip=None 表示不截断（敏感性分析用）。
    """
    variables = list(tpl["variables"])
    T, idx, _diag = ST.build_total_effect_matrix(
        tpl["stable_edges"], variables,
        clip=float("inf") if clip is None else clip, return_diag=True)
    effect_vars = {v for v, nd in tpl["nodes"].items() if int(nd.get("out_degree") or 0) == 0}
    if tpl["marker"]:
        effect_vars.add(tpl["marker"])
    effect_idx = [idx[v] for v in effect_vars if v in idx]
    return {v: float(np.sum(np.abs(T[effect_idx, idx[v]]))) if effect_idx else 0.0
            for v in variables}


def causal_ranking_unmasked(tpl: Dict, clip: Optional[float] = B_CLIP) -> List[str]:
    """历史别名（取消遮蔽后与 `causal_ranking` 等价）：按 |B| 截断上限重算 T 后排序。

    保留只为兼容旧产物/旧文档与 B_CLIP 敏感性（clip≠B_CLIP 时仍需重算）。
    """
    return _production_order(tpl, unmasked_total_effect(tpl, clip=clip))


def trace_deviation(df, cfg: Dict, variables: Sequence[str],
                    return_status: bool = False):
    """逐轨迹偏移强度 d(v)=max|x−μ_pre|/max(1.4826·MAD_pre, σ_floor(v))（与类别无关）。

    σ 不可比（≤0，或无物理下限且本底零变异）的变量不给 d，只落 `d_status=not_comparable`
    （V23-CODE-029）；`return_status=True` 时返回 `(d, status)`。
    """
    t_inject = float(cfg.get("t_inject", 0.0))
    win = cfg.get("main_window") or [300.0, 1500.0]
    pre = df[df["TIME"] <= t_inject - BASELINE_LEAD_SEC + 1e-9]
    if len(pre) < 3:
        pre = df[df["TIME"] <= t_inject]
    post = df[(df["TIME"] >= float(win[0]) - 1e-9) & (df["TIME"] <= float(win[1]) + 1e-9)]
    if len(pre) < 2 or len(post) < 1:
        raise ValueError("轨迹窗口点数不足（基线段或主窗口）")
    d: Dict[str, float] = {}
    status: Dict[str, Dict] = {}
    for v in variables:
        if v not in df.columns:
            continue
        base = pre[v].to_numpy(dtype=float)
        mu = float(np.mean(base))
        sigma_mad = 1.4826 * float(np.median(np.abs(base - mu)))
        floor, src = EK.sigma_floor_for(v, mu)
        sigma = max(sigma_mad, float(floor))
        ok, why = EK.is_deviation_comparable(sigma, src, sigma_mad)
        if not ok:
            status[v] = {"d_status": "not_comparable", "reason": why,
                         "sigma_robust": float(sigma), "sigma_floor_source": src}
            continue
        vals = post[v].to_numpy(dtype=float)
        if vals.size == 0:
            continue
        d[v] = float(np.max(np.abs(vals - mu) / sigma))
    if return_status:
        return d, status
    return d


def deviation_ranking(variables: Sequence[str], d: Dict[str, float]) -> List[str]:
    """纯偏差基线：同候选集等权，只按偏移强度降序（无因果图信息）。"""
    return sorted(variables, key=lambda v: (-d.get(v, -1.0), v))


# ==================== 指标（与老师 metrics_for_ranking 同式，snake_case） ====================
def ranking_metrics(ranking: Sequence[str], truth: Sequence[str]) -> Dict[str, float]:
    rel = set(truth)
    top = list(ranking[:CUTOFF])
    hits = [1 if v in rel else 0 for v in ranking]
    prec_sum = 0.0
    hit_count = 0
    for i, v in enumerate(top, start=1):
        if v in rel:
            hit_count += 1
            prec_sum += hit_count / i
    first = next((i for i, h in enumerate(hits, start=1) if h), None)
    top_set = set(top)
    return {
        "hit1": 1.0 if ranking and ranking[0] in rel else 0.0,
        "hit3": 1.0 if hit_count > 0 else 0.0,
        "mrr": (1.0 / first) if first else 0.0,
        "p2": sum(1 for v in ranking[:2] if v in rel) / 2.0,
        "p3": hit_count / float(CUTOFF),
        "recall3": hit_count / max(1, len(rel)),
        "ap3": (prec_sum / min(len(rel), CUTOFF)) if rel else 0.0,
        "jaccard3": (len(top_set & rel) / len(top_set | rel)) if (top_set | rel) else 0.0,
        "first_rank": first,
        "n_gt": len(rel),
        "n_gt_in_ranking": sum(1 for v in rel if v in ranking),
    }


def rank_distribution(rows: Sequence[Dict]) -> Dict:
    """B3（V23-DESIGN-045）：真值名次分布标准化，避免只看 Hit@1。

    - `rank_hist`：首个真值变量名次直方图（1..max），未命中计入 `n_miss`
    - `mean_normalized_rank`：`(rank-1)/(n_candidates-1)` 的均值，消除候选集大小差异
    - `gt_coverage_mean`：真值集落在候选集内的比例均值（真值可达性的评测侧镜像）
    """
    out: Dict[str, Dict] = {}
    for ch in CHANNELS:
        if not any(ch in r["metrics"] for r in rows):
            continue
        ranks: List[int] = []
        norm: List[float] = []
        cover: List[float] = []
        miss = 0
        for r in rows:
            m = r["metrics"][ch]
            n_cand = int((r.get("deviation_status") or {}).get("n_candidates") or 0)
            fr = m.get("first_rank")
            if fr:
                ranks.append(int(fr))
                norm.append((int(fr) - 1) / max(1, n_cand - 1))
            else:
                miss += 1
            n_gt = int(m.get("n_gt") or 0)
            cover.append((int(m.get("n_gt_in_ranking") or 0) / n_gt) if n_gt else 0.0)
        top = max(ranks) if ranks else 0
        out[ch] = {
            "n": len(rows),
            "n_ranked": len(ranks),
            "n_miss": miss,
            "rank_hist": {str(i): ranks.count(i) for i in range(1, top + 1)},
            "mean_first_rank": round(float(np.mean(ranks)), 4) if ranks else None,
            "median_first_rank": float(np.median(ranks)) if ranks else None,
            "mean_normalized_rank": round(float(np.mean(norm)), 4) if norm else None,
            "gt_coverage_mean": round(float(np.mean(cover)), 4) if cover else None,
        }
    return out


def _summarize(rows: Sequence[Dict]) -> Dict:
    out: Dict[str, Dict] = {}
    channels = [c for c in _all_channels() if all(c in r["metrics"] for r in rows)]
    for ch in channels:
        mets = [r["metrics"][ch] for r in rows]
        n = len(mets)
        agg: Dict[str, object] = {"n": n}
        for key in METRIC_KEYS:
            agg[key] = round(float(np.mean([m[key] for m in mets])), 4) if n else None
        for key in ("hit1", "hit3"):
            k = int(sum(round(float(m[key])) for m in mets))
            lo, hi = wilson_ci95(k, n)
            agg[f"{key}_n"] = k
            agg[f"{key}_wilson95"] = [None if lo is None else round(lo, 4),
                                      None if hi is None else round(hi, 4)]
        out[ch] = _tag_agg(agg)
    return out


def _summarize_clip(rows: Sequence[Dict], label: str) -> Dict:
    """B_CLIP 敏感性单列汇总：Hit@1/Hit@3/MRR/AP@3 + 注入点名次中位数 + Wilson。"""
    mets = [r["clip_sensitivity"][label]["metrics"] for r in rows]
    n = len(mets)
    agg: Dict[str, object] = {"n": n}
    for key in ("hit1", "hit3", "mrr", "ap3"):
        agg[key] = round(float(np.mean([m[key] for m in mets])), 4) if n else None
    for key in ("hit1", "hit3"):
        k = int(sum(round(float(m[key])) for m in mets))
        lo, hi = wilson_ci95(k, n)
        agg[f"{key}_n"] = k
        agg[f"{key}_wilson95"] = [None if lo is None else round(lo, 4),
                                  None if hi is None else round(hi, 4)]
    ranks = [r["clip_sensitivity"][label]["marker_rank"] for r in rows
             if r["clip_sensitivity"][label]["marker_rank"]]
    agg["marker_rank_median"] = round(float(np.median(ranks)), 2) if ranks else None
    return _tag_agg(agg)


def _clip_flip_note(per_tpl_clips: Dict[str, Dict[str, Dict]]) -> Dict[str, object]:
    """显式标出哪些模板的命中/注入点名次会随 clip 翻转（供读者判读，不靠肉眼比对）。"""
    flips: Dict[str, Dict] = {}
    for name, by_clip in per_tpl_clips.items():
        ranks = {lab: v.get("marker_rank_median") for lab, v in by_clip.items()}
        hits = {lab: (v.get("hit1"), v.get("hit3")) for lab, v in by_clip.items()}
        if len({r for r in ranks.values()}) > 1 or len(set(hits.values())) > 1:
            flips[name] = {"marker_rank_median": ranks,
                           "hit1_hit3": {lab: list(h) for lab, h in hits.items()},
                           "top3": {lab: v.get("top3") for lab, v in by_clip.items()}}
    return {
        "templates": flips,
        "rule": ("判定：任一 clip 下 hit1/hit3 或注入点名次不同即列入；"
                 "列入者不得以单列 clip5 数字作结论"),
        "known_case": "LOCA_high：clip5 下 WLR 第 1（hit1=1.0）、clip_off 下第 3（hit1=0）",
    }


# ==================== primary 块 1：稳定性 ====================
def _average_ranks(values: Sequence[float]) -> List[float]:
    """秩变换（并列取平均秩），供自实现的 Spearman 使用。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for t in range(i, j + 1):
            ranks[order[t]] = avg
        i = j + 1
    return ranks


def _spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Spearman 秩相关（自实现：平均秩 + Pearson；无方差返回 None，不引新依赖）。"""
    if len(x) != len(y) or len(x) < 2:
        return None
    rx = _average_ranks([float(a) for a in x])
    ry = _average_ranks([float(b) for b in y])
    mx, my = float(np.mean(rx)), float(np.mean(ry))
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx)
    dy = sum((b - my) ** 2 for b in ry)
    if dx <= 0 or dy <= 0:
        return None
    return float(num / math.sqrt(dx * dy))


def _template_rankings(run_id: str) -> Dict[str, Dict[str, Dict]]:
    """{类: {组: {ranking, persisted_top, n_nodes}}}：只读本 run 已落盘产物。

    `ranking` = 15 变量完整生产口径名次（读 topology 的 total_effect，不重算 T、不重学图）；
    `persisted_top` = `root_candidates_*.json` 里榜内变量的顺序（用于校验口径一致）。
    """
    out: Dict[str, Dict[str, Dict]] = {}
    for p in sorted((artifact.run_path(run_id) / "tracing").glob("root_candidates_*.json")):
        k, g = p.stem.replace("root_candidates_", "").rsplit("_", 1)
        tpl = load_template(k, g, run_id)
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        persisted = [c["variable"] for c in
                     sorted(doc.get("root_candidates") or [], key=lambda c: c.get("rank") or 10 ** 6)]
        out.setdefault(k, {})[g] = {"ranking": causal_ranking(tpl),
                                   "persisted_top": persisted,
                                   "n_nodes": len(tpl["nodes"])}
    return out


def stability_block(run_id: str) -> Dict:
    """稳定性（primary）：同一类内 low/mid/high 三组榜单的一致程度。

    名次全部取自本 run 落盘产物；不跑 bootstrap、不重学图、不改任何生产输出。
    """
    by_class = _template_rankings(run_id)
    per_class: Dict[str, Dict] = {}
    for k, groups_all in by_class.items():
        gs = [g for g in GROUP_ORDER if g in groups_all]
        if len(gs) < 2:
            per_class[k] = {"groups": gs, "status": "not_applicable",
                            "reason": "可用严重度组少于 2 个，无法比较组间一致性"}
            continue
        ranks = {g: {v: i + 1 for i, v in enumerate(groups_all[g]["ranking"])} for g in gs}
        variables = sorted({v for r in ranks.values() for v in r})
        n_nodes = max(groups_all[g]["n_nodes"] for g in gs)

        std_per_var = {v: round(float(np.std([ranks[g][v] for g in gs if v in ranks[g]])), 4)
                       for v in variables if sum(1 for g in gs if v in ranks[g]) >= 2}
        std_vals = list(std_per_var.values())
        rank_std = {
            "per_variable": std_per_var,
            "median": round(float(np.median(std_vals)), 4) if std_vals else None,
            "max": max(std_vals) if std_vals else None,
            "max_variable": (max(std_per_var, key=lambda v: std_per_var[v])
                             if std_per_var else None),
            "n_variables": len(std_vals),
        }

        k_top = min(5, n_nodes)
        top_sets = {g: set(groups_all[g]["ranking"][:k_top]) for g in gs}
        jac: Dict[str, Optional[float]] = {}
        rho: Dict[str, Optional[float]] = {}
        for i in range(len(gs)):
            for j in range(i + 1, len(gs)):
                a, b = gs[i], gs[j]
                pair = f"{a}-{b}"
                union = top_sets[a] | top_sets[b]
                jac[pair] = (round(len(top_sets[a] & top_sets[b]) / len(union), 4)
                             if union else None)
                common = [v for v in variables if v in ranks[a] and v in ranks[b]]
                r = _spearman([ranks[a][v] for v in common],
                              [ranks[b][v] for v in common])
                rho[pair] = None if r is None else round(r, 4)
        jac_vals = [v for v in jac.values() if v is not None]
        rho_vals = [v for v in rho.values() if v is not None]

        if len(gs) == 3:
            mono: Dict[str, Dict] = {}
            for v in variables:
                seq = [ranks[g][v] for g in gs]
                mono[v] = {"ranks": seq,
                           "monotone": bool(all(x < y for x, y in zip(seq, seq[1:]))
                                            or all(x > y for x, y in zip(seq, seq[1:])))}
            n_mono = sum(1 for m in mono.values() if m["monotone"])
            monotonicity = {
                "status": "computed",
                "group_severity_order": gs,
                "ratio": round(n_mono / len(variables), 4) if variables else None,
                "n_monotone": n_mono,
                "n_variables": len(variables),
                "per_variable": mono,
                "rule": "三组严重度 low<mid<high；某变量三组名次严格递增或严格递减即计为单调",
            }
        else:
            monotonicity = {"status": "not_applicable",
                            "reason": f"仅 {len(gs)} 组可用（{gs}），无法判定随严重度单调"}

        per_class[k] = {
            "groups": gs,
            "group_severity_order": gs,
            "n_variables": len(variables),
            "persisted_top_matches_ranking": all(
                groups_all[g]["ranking"][:len(groups_all[g]["persisted_top"])]
                == groups_all[g]["persisted_top"] for g in gs),
            # 以下四块已撤出结果层（V23-DESIGN-063）：数值仍在，但不得进正文/结论。
            # 撤出依据：实测与随机基线不可区分（Jaccard 0.2632 vs 0.2144、名次标准差
            # 1.89–2.87 vs 3.22–3.40、单调占比 1/15–5/15 vs 0.27–0.33）。
            "rank_std_across_groups": _withdraw(rank_std, ["rank_std_across_groups"]),
            "topk_jaccard_across_groups": _withdraw(
                {"k": k_top, "pairs": jac,
                 "mean": (round(float(np.mean(jac_vals)), 4) if jac_vals else None)},
                ["topk_jaccard_across_groups"]),
            "cross_group_consistency": _withdraw(
                {"measure": "spearman_rank_correlation", "pairs": rho,
                 "mean": (round(float(np.mean(rho_vals)), 4) if rho_vals else None)},
                ["cross_group_consistency"]),
            "monotonicity": _withdraw(monotonicity, ["monotonicity"]),
        }

    return {
        "metric_tier": METRIC_TIER_PRIMARY,
        "source": ("tracing/root_candidates_*.json + tracing/topology_*.json（只读落盘；"
                   "不重学图、不重算 T、不跑 bootstrap）"),
        "definition": {
            "rank": "变量在类内某严重度组的完整生产口径名次（1=榜首，越小越靠前）",
            "rank_std_across_groups": "同一变量在类内各组名次的标准差（总体标准差，ddof=0）",
            "topk_jaccard_across_groups": f"各组 Top-{5} 集合两两 Jaccard（k=5）",
            "cross_group_consistency": "各组完整名次向量的 Spearman 秩相关（自实现）",
            "monotonicity": "三组严重度有序时，名次随组别严格单调的变量占比",
        },
        "per_class": per_class,
        "bootstrap_flip_rate": {**_withdrawn_fields(["bootstrap_flip_rate"]),
                                "status": "not_computed",
                                "reason": "逐次 bootstrap 排名未落盘",
                                "required_bootstrap": ">=20 次并逐次落盘 top-k"},
        "result_layer": {
            "reported": False,
            "reliability": RELIABILITY_UNSUPPORTED,
            "metric_status": METRIC_STATUS_KNOWN_UNUSABLE,
            "withdrawn_metrics": ["rank_std_across_groups", "topk_jaccard_across_groups",
                                  "cross_group_consistency", "monotonicity",
                                  "bootstrap_flip_rate"],
            "reason": ("跨组一致性数值与随机不可区分：Jaccard 0.2632 vs 随机 0.2144"
                       "（三对均值≥实测的概率 0.30）、名次标准差 1.89–2.87 vs 3.22–3.40、"
                       "单调占比 1/15–5/15 vs 0.27–0.33；bootstrap 翻转率从未计算"),
            "disposition": WITHDRAWAL_DISPOSITION,
        },
        "meaning": ("稳定性衡量：同一事故类型在不同严重度组下，根因榜（名次/Top-k 集合）"
                    "是否一致，以及有多少变量名次随严重度系统性移动。"
                    "**该块的推断量已全部撤出结果层**（与随机不可区分），"
                    "只保留为内部留档，不得进正文、结论句或论文数字。"),
        "reading": ("本块不产出可解读数字。若将来要恢复：先给随机/置换零分布与按类 CI，"
                    "再按模板聚类的有效 n（n_eff=9/3）重算；在此之前任何"
                    "“稳定/不稳定”的判词都没有依据。bootstrap_flip_rate 需 ≥20 次"
                    "逐次落盘 top-k 才能计算。"),
    }


# ==================== primary 块 2：结构健康度 ====================
def _vif_for_class(k: str, run_id: str) -> Dict:
    """类级 VIF（变量选择阶段产物，模板按类共享同一入模变量集）。"""
    path = artifact.run_path(run_id) / "variable_sets" / f"selection_report_{k}.json"
    if not path.exists():
        return {"status": "not_available",
                "reason": f"缺 {path.name}（VIF 只在变量选择阶段计算，本步不重算）"}
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    vifs = doc.get("final_vif") or doc.get("vif_all") or {}
    vals = [(v, float(x)) for v, x in vifs.items() if x is not None and math.isfinite(float(x))]
    if not vals:
        return {"status": "not_available", "reason": "selection_report 无可用 VIF 数值"}
    top_var, top_val = max(vals, key=lambda t: t[1])
    return {
        "status": "computed",
        "source": f"variable_sets/selection_report_{k}.json::final_vif",
        "n_vars": len(vals),
        "max_vif": round(top_val, 4),
        "max_vif_variable": top_var,
        "median_vif": round(float(np.median([x for _v, x in vals])), 4),
        "max_vif_all_stage1": doc.get("max_vif_all"),
        "threshold": doc.get("vif_max_threshold"),
        "scope": "类级（该类的 low/mid/high 三组共用同一入模变量集）",
    }


def structural_health_block(run_id: str) -> Dict:
    """结构健康度（primary）：共线性/病态程度/图的稀疏度/结果对 B_CLIP 的敏感度。"""
    per_template: Dict[str, Dict] = {}
    vif_cache: Dict[str, Dict] = {}
    for p in sorted((artifact.run_path(run_id) / "tracing").glob("root_candidates_*.json")):
        k, g = p.stem.replace("root_candidates_", "").rsplit("_", 1)
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        tpl = load_template(k, g, run_id)
        n_nodes = int(doc.get("n_nodes") or len(tpl["nodes"]))
        n_edges = int(doc.get("n_stable_edges") or 0)
        n_possible = n_nodes * (n_nodes - 1)
        if k not in vif_cache:
            vif_cache[k] = _vif_for_class(k, run_id)
        tops = {label: causal_ranking_unmasked(tpl, clip=clip)[:min(5, n_nodes)]
                for label, clip in CLIP_VARIANTS}
        unique_sets = {frozenset(v) for v in tops.values()}
        per_template[f"{k}_{g}"] = {
            "n_nodes": n_nodes,
            "n_stable_edges": n_edges,
            "stable_edge_ratio": round(n_edges / n_possible, 4) if n_possible else None,
            "condition_number": {
                "b_clip": doc.get("b_clip"),
                "before_clip": doc.get("cond_IminusB"),
                "after_clip": doc.get("cond_IminusB_after_clip"),
            },
            "vif": vif_cache[k],
            "clip_top5": tops,
            "n_unique_top5_sets": len(unique_sets),
            "clip_flip": len(unique_sets) > 1,
            "stability_label": (doc.get("stability") or {}).get("label"),
        }

    templates = list(per_template.values())
    cond_before = [t["condition_number"]["before_clip"] for t in templates
                   if t["condition_number"]["before_clip"] is not None]
    cond_after = [t["condition_number"]["after_clip"] for t in templates
                  if t["condition_number"]["after_clip"] is not None]
    ratios = [t["stable_edge_ratio"] for t in templates if t["stable_edge_ratio"] is not None]
    vif_max = [v["max_vif"] for v in vif_cache.values() if v.get("status") == "computed"]
    vif_med = [v["median_vif"] for v in vif_cache.values() if v.get("status") == "computed"]

    labels = [lab for lab, _c in CLIP_VARIANTS]
    pair_flip: Dict[str, Optional[float]] = {}
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = labels[i], labels[j]
            n_diff = sum(1 for t in templates
                         if set(t["clip_top5"][a]) != set(t["clip_top5"][b]))
            pair_flip[f"{a}-{b}"] = round(n_diff / len(templates), 4) if templates else None
    n_flip = sum(1 for t in templates if t["clip_flip"])

    return {
        "metric_tier": METRIC_TIER_PRIMARY,
        "source": ("tracing/root_candidates_*.json（cond/n_stable_edges）+ "
                   "variable_sets/selection_report_*.json（VIF）+ 已落盘 stable_edges"
                   "（仅为 clip_off 重算 |T|，纯矩阵、无 bootstrap）"),
        "definition": {
            "vif": "变量选择阶段入模变量集的 VIF（类级；max 与中位数）",
            "condition_number": "cond(I−B)：B 为稳定边直接效应矩阵；before_clip 为不截断时、"
                                "after_clip 为 |B|≤B_CLIP 截断后的条件数",
            "stable_edge_ratio": "n_stable_edges / (n_nodes×(n_nodes−1))，即稳定有向边占"
                                 "全部可能有向边的比例（有向图密度，不含自环）",
            "clip_sensitivity_flip": "同一模板在 clip3/clip5/clip10/clip_off 下 Top-5 集合"
                                     "是否变化；flip_ratio = 发生变化的模板数 / 模板总数",
        },
        "per_class_vif": {k: v for k, v in vif_cache.items()},
        "per_template": per_template,
        "global": {
            "n_templates": len(templates),
            "condition_number": {
                "before_clip_max": round(max(cond_before), 4) if cond_before else None,
                "before_clip_median": round(float(np.median(cond_before)), 4) if cond_before else None,
                "after_clip_max": round(max(cond_after), 4) if cond_after else None,
                "after_clip_median": round(float(np.median(cond_after)), 4) if cond_after else None,
            },
            "vif": {
                "max_over_classes": max(vif_max) if vif_max else None,
                "median_over_classes": (round(float(np.median(vif_med)), 4) if vif_med else None),
                "n_classes": len(vif_max),
            },
            "stable_edge_ratio": {
                "mean": round(float(np.mean(ratios)), 4) if ratios else None,
                "median": round(float(np.median(ratios)), 4) if ratios else None,
                "min": min(ratios) if ratios else None,
                "max": max(ratios) if ratios else None,
            },
            "clip_sensitivity_flip": _withdraw({
                "k": 5,
                "clips": labels,
                "n_flipped": n_flip,
                "flip_ratio": round(n_flip / len(templates), 4) if templates else None,
                "pairwise": pair_flip,
                "flipped_templates": [n for n, t in per_template.items() if t["clip_flip"]],
            }, ["clip_flip"]),
        },
        "gates": {
            "vif": {**_withdrawn_fields(["vif_gate"]),
                    "gate_status": "record_only_no_pass",
                    "why": ("VIF_MAX=500 同时是迭代停止条件：循环在“非保护 max<500”时退出，"
                            "classify_v3 再把同一条件复述一遍；高 VIF 全落在保护集，"
                            "非保护 max 恰为 489.6/490.0/276.3，对残留高 VIF 无告警能力"),
                    "use": ("仅作记录，不构成通过判定（不是质量合格证）；"
                            "只可作描述性原始量（含保护变量的 max/median），不得写“通过/失败”")},
            "condition_number": {**_withdrawn_fields(["cond_gate"]),
                                 "gate_status": "record_only_no_pass",
                                 "why": ("阈值 10000 无来源；实测截断后最高 1054.8，9/9 全过；"
                                         "截断前最高 2.38e9，“变好”是截断产物"),
                                 "use": ("仅作记录，不构成通过判定（不是质量合格证）；"
                                         "只可陈述“多大”，不得据此声称“数值稳定”")},
        },
        "result_layer": {
            "reported": False,
            "reliability": RELIABILITY_UNSUPPORTED,
            "metric_status": METRIC_STATUS_KNOWN_UNUSABLE,
            "withdrawn_metrics": ["vif_gate", "cond_gate", "clip_flip"],
            "kept_as": "描述性原始量（n_nodes / n_stable_edges / stable_edge_ratio / "
                       "cond 原值 / VIF 原值）可陈述“多大/多密”，不得推出“通过/稳定”",
            "disposition": WITHDRAWAL_DISPOSITION,
        },
        "meaning": ("结构健康度衡量：学到的图是否良态（条件数）、是否近似共线（VIF）、"
                    "是否稠密到不可解释（边密度），以及结论是否依赖 B_CLIP 这一人为阈值。"),
        "reading": ("cond 与 VIF 只说明数值/共线状况：越大表示线性代数越接近病态，"
                    "结论对该图的依赖越不可靠（不是“越大越准/越不准”）；"
                    "stable_edge_ratio 越大表示图越稠密、可解释性越差；"
                    "flip_ratio 越大表示 Top-5 结论越依赖 clip 阈值，单列数字不得作结论。"),
    }


# ==================== primary 块 3：机理一致性（占位）====================
def mechanism_consistency_block() -> Dict:
    """机理一致性（primary，占位）：无专家输入时不产出任何数值，也不允许拿诊断指标顶替。"""
    return {
        "status": "not_available",
        "metric_tier": METRIC_TIER_PRIMARY,
        "reported": False,
        "reliability": RELIABILITY_UNSUPPORTED,
        # 与“已知不可用”区分：这一块是缺专家输入（pending_expert），不是数值不可解读。
        "metric_status": METRIC_STATUS_PENDING_EXPERT,
        "violation_rate": None,
        "coverage": None,
        "protocol": ("排除性判据：专家给出『某部件必然先响应』『某部件不可能先响应』两类清单，"
                     "据此算违反率与覆盖率"),
        "note": "当前无专家输入；不得用现有 hit@k/MRR 或任何数字替代",
    }


# ==================== 模板级检索（诊断工具，V23-DESIGN-040）====================
RETRIEVAL_WINDOW = (300.0, 1500.0)


def _wilson(k: int, n: int, z: float = 1.959964) -> List[float]:
    if n <= 0:
        return [0.0, 0.0]
    p = k / n
    d = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def _baseline_scales(df, cfg: Dict, variables: Sequence[str]) -> Dict[str, tuple]:
    """逐变量基线 μ/σ（σ 不可比则记 None）；只用注入前基线，与事故类型无关。"""
    t_inject = float(cfg.get("t_inject", 110.0))
    pre = df[df["TIME"] <= t_inject - BASELINE_LEAD_SEC + 1e-9]
    if len(pre) < 3:
        pre = df[df["TIME"] <= t_inject]
    out: Dict[str, tuple] = {}
    for v in variables:
        if v not in df.columns:
            continue
        base = pre[v].to_numpy(dtype=float)
        mu = float(np.mean(base))
        mad = 1.4826 * float(np.median(np.abs(base - mu)))
        floor, src = EK.sigma_floor_for(v, mu)
        sigma = max(mad, float(floor))
        ok, _why = EK.is_deviation_comparable(sigma, src, mad)
        out[v] = (mu, sigma if ok else None)
    return out


def template_fit_score(df, tpl: Dict, cfg: Dict,
                       window: Sequence[float] = RETRIEVAL_WINDOW) -> float:
    """候选模板的 SEM 残差：mean_{v 有父节点} |z_v − Σ_u w_uv·z_u|（越小越拟合）。

    只用轨迹数值 + 候选模板已落盘 `stable_edges`；不读文件名/严重度标签，无泄漏。
    """
    scales = _baseline_scales(df, cfg, tpl["variables"])
    seg = df[(df["TIME"] >= float(window[0]) - 1e-9)
             & (df["TIME"] <= float(window[1]) + 1e-9)]
    if seg.empty:
        return float("inf")
    z: Dict[str, "np.ndarray"] = {}
    for v, (mu, sigma) in scales.items():
        if sigma is None or sigma <= 0:
            continue
        z[v] = (seg[v].to_numpy(dtype=float) - mu) / sigma
    parents: Dict[str, List[tuple]] = {}
    for e in tpl["stable_edges"]:
        parents.setdefault(e["to"], []).append((e["from"], float(e["weight_median"])))
    res: List[float] = []
    for v, pars in parents.items():
        if v not in z:
            continue
        pred = np.zeros_like(z[v])
        used = 0
        for u, w in pars:
            if u in z and len(z[u]) == len(z[v]):
                pred = pred + w * z[u]
                used += 1
        if not used:
            continue
        # 相对残差：除以该变量自身的偏离幅度，消除"权重越平残差越小"的模型选择偏置
        denom = float(np.mean(np.abs(z[v]))) + 1e-9
        res.append(float(np.mean(np.abs(z[v] - pred))) / denom)
    return float(np.mean(res)) if res else float("inf")


def template_ranking(df, templates: Dict[str, Dict], cfg: Dict) -> List[str]:
    """按 SEM 残差升序给模板榜（并列按名次字典序，保证确定性）。"""
    scores = {name: template_fit_score(df, tpl, cfg) for name, tpl in templates.items()}
    return [n for n, _s in sorted(scores.items(), key=lambda x: (x[1], x[0]))]


def evaluate_template_retrieval(traces: Sequence[Dict], templates: Dict[str, Dict],
                                registry: Dict[str, Dict]) -> Dict:
    """【诊断工具，非评测对象（V23-DESIGN-055）】模板级（9 个）与事故类级（3 个）检索：
    Hit@1/Hit@3/MRR + 逐类 + 混淆 Top-1。

    本函数**不在** `evaluate()` 内自动调用，流水线不产出 `metrics_template_retrieval.json`；
    只在需要排查"模板能不能被认出来"时手动调用。结果不得进主表、结论句或论文数字——
    v2.3 的评测对象是事故内变量级根因溯源（V23-DESIGN-036），类别/模板识别不是任务。
    """
    rows = []
    for tr in traces:
        k, sev = tr["accident_type"], tr["severity"]
        df = D.load_trajectory(k, sev)
        cfg = registry.get(k) or {}
        order = template_ranking(df, templates, cfg)
        t_rank = order.index(tr["template"]) + 1
        cls_scores: Dict[str, float] = {}
        for name in order:
            kk = name.rsplit("_", 1)[0]
            cls_scores[kk] = min(cls_scores.get(kk, float("inf")),
                                 template_fit_score(df, templates[name], cfg))
        cls_order = [c for c, _s in sorted(cls_scores.items(), key=lambda x: (x[1], x[0]))]
        rows.append({"template": tr["template"], "accident_type": k, "severity": sev,
                     "template_rank": t_rank, "class_rank": cls_order.index(k) + 1,
                     "template_top1": order[0], "class_top1": cls_order[0],
                     "template_top3": order[:3], "class_top3": cls_order[:3]})

    def _agg(sub: Sequence[Dict], key: str) -> Dict:
        n = len(sub)
        if n == 0:
            return {"n": 0}
        ranks = [r[key] for r in sub]
        h1 = sum(1 for x in ranks if x == 1)
        h3 = sum(1 for x in ranks if x <= 3)
        return {"n": n, "hit1": round(h1 / n, 4), "hit3": round(h3 / n, 4),
                "mrr": round(sum(1.0 / x for x in ranks) / n, 4),
                "hit1_n": h1, "hit1_wilson95": _wilson(h1, n),
                "hit3_n": h3, "hit3_wilson95": _wilson(h3, n)}

    classes = []
    for r in rows:
        if r["accident_type"] not in classes:
            classes.append(r["accident_type"])
    top1 = {}
    for r in rows:
        top1[r["template_top1"]] = top1.get(r["template_top1"], 0) + 1
    return {
        "definition": ("给定一条无标签测试轨迹，对本次有产物的每个模板（K×严重度组）算相对 SEM 残差 "
                       "mean_v [mean|z_v−Σ_u w_uv z_u| / mean|z_v|]（v 取该模板有父节点的变量），"
                       "升序为模板榜；类别榜 = 该类的组内最小残差。窗口固定 [300,1500]，B_CLIP 不影响该分数；"
                       "类别级 Hit@3 在 3 类下恒为 1.0、无信息量，只读类别级 Hit@1 与 MRR"),
        "n_traces": len(rows),
        "n_templates": len(templates),
        "template_level": _agg(rows, "template_rank"),
        "class_level": _agg(rows, "class_rank"),
        "per_class": {c: {"template_level": _agg([r for r in rows if r["accident_type"] == c],
                                                 "template_rank"),
                          "class_level": _agg([r for r in rows if r["accident_type"] == c],
                                              "class_rank")} for c in classes},
        "template_top1_distribution": dict(sorted(top1.items(), key=lambda x: -x[1])),
        "rows": rows,
        "leakage_note": ("打分只读轨迹数值与已落盘 stable_edges；标签（类/严重度组）仅用于算名次；"
                         "未使用文件名、未重新学图、未读 root_candidates"),
    }


# ==================== 主流程 ====================
def evaluate(run_id: str, classes: Optional[Sequence[str]] = None,
             groups: Optional[Sequence[str]] = None,
             role_prior: Optional[bool] = None) -> Dict:
    """跑变量级留出评测并落 metrics/；返回 {files, n_traces, summary}。

    `role_prior`（B1，默认关闭）：开启时**同时**产出"有先验/无先验"两版排序与两套指标，
    报告并列；任一单独出现即 `role_prior.pair_complete=False`（视为不合规）。
    """
    prior_on = RP.resolve(role_prior)
    from causalrca.config.accident_config import READY_CODES
    classes = list(classes) if classes else [k for k in READY_CODES]
    groups = list(groups) if groups else list(GROUP_ORDER)
    split = load_split_manifest(run_id)
    registry = load_registry(run_id)
    traces = test_traces(split, classes, groups)
    if not traces:
        raise ValueError("split_manifest.json 未给出任何测试轨迹")

    templates: Dict[str, Dict] = {}
    for k in classes:
        for g in groups:
            try:
                templates[f"{k}_{g}"] = load_template(k, g, run_id)
            except FileNotFoundError:
                continue
    if not templates:
        raise FileNotFoundError("没有任何模板产物（tracing/ + signatures/），无法评测")

    gt_effective: Dict[str, List[str]] = {}
    for k in classes:
        gt_effective[k] = list(IT.truth_for(k))

    rows: List[Dict] = []
    for tr in traces:
        k, sev, g = tr["accident_type"], tr["severity"], tr["group"]
        tpl = templates.get(tr["template"])
        if tpl is None:
            continue
        cfg = registry.get(k) or {}
        df = D.load_trajectory(k, sev)
        gt = [v for v in gt_effective[k] if v in df.columns]      # 只剔除数据里没有的列
        if not gt:
            continue
        d, d_status = trace_deviation(df, cfg, list(tpl["nodes"]), return_status=True)
        if not d:
            raise ValueError(f"{k}/{sev}: 偏移强度为空，无法评测")
        rank_c = causal_ranking(tpl)
        # 取消遮蔽（2026-11-11）后 causal 已含注入变量 → 评测列与生产列完全等价，
        # rank_u 作为历史别名直接复用同一份名次，保证两列数值逐项相同。
        rank_u = rank_c
        rank_p = causal_ranking(tpl, role_prior=True) if prior_on else None
        rank_d = deviation_ranking(list(tpl["nodes"]), d)
        # 偏差基线第二列（历史别名）：取消遮蔽后生产集=全集（同 15 节点），故与 rank_d 相同
        rank_d_prod = rank_d
        # B_CLIP 敏感性：同一候选集，只改 |B| 的截断上限（clip5 = 生产默认）
        clip_rows: Dict[str, Dict] = {}
        for label, clip in CLIP_VARIANTS:
            rk = rank_u if label == "clip5" else causal_ranking_unmasked(tpl, clip=clip)
            clip_rows[label] = {
                "metrics": {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                            for kk, vv in ranking_metrics(rk, gt).items()},
                "marker_rank": (rk.index(tpl["marker"]) + 1)
                               if tpl["marker"] and tpl["marker"] in rk else None,
                "top3": rk[:3],
            }
        row = {
            "accident_type": k, "group": g, "severity": sev, "template": tr["template"],
            "gt": gt,
            "gt_in_candidate_set": {"causal": [v for v in gt if v in rank_c],
                                    "causal_unmasked_for_eval": [v for v in gt if v in rank_u],
                                    "deviation_equal_weight": [v for v in gt if v in rank_d],
                                    "deviation_production_set": [v for v in gt if v in rank_d_prod]},
            "d_top5": [[v, round(d[v], 3)] for v in sorted(d, key=lambda x: -d[x])[:5]],
            "deviation_status": {
                "n_candidates": len(tpl["nodes"]), "n_comparable": len(d),
                "not_comparable": {v: s["reason"] for v, s in d_status.items()},
            },
            "top5": {"causal": rank_c[:5], "causal_unmasked_for_eval": rank_u[:5],
                     "deviation_equal_weight": rank_d[:5],
                     "deviation_production_set": rank_d_prod[:5]},
            "metrics": {"causal": ranking_metrics(rank_c, gt),
                        "causal_unmasked_for_eval": ranking_metrics(rank_u, gt),
                        "deviation_equal_weight": ranking_metrics(rank_d, gt),
                        "deviation_production_set": ranking_metrics(rank_d_prod, gt)},
            "clip_sensitivity": clip_rows,
        }
        if prior_on:
            row["metrics"][ROLE_PRIOR_CHANNEL] = ranking_metrics(rank_p, gt)
            row["top5"][ROLE_PRIOR_CHANNEL] = rank_p[:5]
            row["gt_in_candidate_set"][ROLE_PRIOR_CHANNEL] = [v for v in gt if v in rank_p]
        row["metrics"] = {ch: {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                               for kk, vv in row["metrics"][ch].items()} for ch in row["metrics"]}
        rows.append(row)

    written: List[str] = []
    by_template: Dict[str, Dict] = {}
    by_template_clips: Dict[str, Dict] = {}
    for name, tpl in templates.items():
        sub = [r for r in rows if r["template"] == name]
        if not sub:
            continue
        gt = gt_effective[tpl["k"]]
        marker = tpl["marker"]
        masked = (tpl["nodes"].get(marker) or {}).get("total_effect") is None
        eff_unmasked = unmasked_total_effect(tpl)
        tpl_clips: Dict[str, Dict] = {}
        for label, _clip in CLIP_VARIANTS:
            agg = _summarize_clip(sub, label)
            agg["top3"] = sub[0]["clip_sensitivity"][label]["top3"]
            tpl_clips[label] = agg
        payload = {
            "template": {"k": tpl["k"], "g": tpl["g"], "n_nodes": len(tpl["nodes"])},
            "n_test": len(sub),
            "ground_truth": {
                "variables": gt, "all_registered": list(IT.truth_for(tpl["k"])),
                "source": "causalrca/config/injection_truth.py::INJECTION_TRUTH",
                "provenance": IT.SOURCE,
                "suspect": list(IT.SUSPECT.get(tpl["k"], ())),
                "filter_rule": "只剔除数据列中不存在的变量；不在候选集内的真值计入命中分母并在 gt_in_candidate_set 披露",
            },
            "candidate_set": {
                "source": f"tracing/topology_{tpl['k']}_{tpl['g']}.json（n_nodes={len(tpl['nodes'])}）",
                "causal_order": "生产口径（取消遮蔽）：候选=全部节点（含注入变量）；total_effect DESC → in_degree ASC → weighted_reach DESC → reach_size DESC → consensus_rate DESC；有传导者优先、孤立节点殿后",
                "deviation_order": "同候选集等权，按 d(v) DESC（无因果图）；取消遮蔽后两列偏差基线同为 15 节点",
                "unmasked_note": "causal_unmasked_for_eval 为历史别名：取消遮蔽后与 causal 等价（同一 15 节点、同一名次），仅为兼容旧产物/旧文档",
            },
            "injection_point_masking": {
                "variable": marker, "masked": masked,
                "total_effect_production": (tpl["nodes"].get(marker) or {}).get("total_effect"),
                "total_effect_unmasked_for_eval": round(eff_unmasked.get(marker, 0.0), 6),
                "root_candidates_rank_production": tpl["marker_info"].get("rank"),
                "note": ("取消遮蔽（2026-11-11）后的 run：masked 恒为 False（注入变量已作为普通候选"
                         "参与排名）；旧产物可能仍为 True。本字段只作历史标识"),
            },
            "method_note": tpl["method_note"],
            "clip_sensitivity": {
                "channel": "causal（含注入变量；只改 |B| 的截断上限，其余不变）",
                "note": ("变量级命中对 B_CLIP 敏感：clip5 是本仓库生产默认值，"
                         "引用命中率必须并列四列，单列 clip5 不得当结论"),
                "clips": tpl_clips,
            },
            "traces": sub,
            "summary": _summarize(sub),
            "leakage_note": ("留出轨迹取自 split_manifest.test_severities；排序读本 run 已落盘 "
                             "topology/signature；不重算 bootstrap、不重新学图；真值只用于打分"),
        }
        path = artifact.write_artifact(f"metrics_{tpl['k']}_{tpl['g']}", payload, "metrics",
                                       run_id=run_id, accident_type=tpl["k"], group=tpl["g"],
                                       source=f"tracing/root_candidates_{tpl['k']}_{tpl['g']}.json")
        written.append(Path(path).name)
        by_template[name] = payload["summary"]
        by_template_clips[name] = tpl_clips

    per_class = {k: _summarize([r for r in rows if r["accident_type"] == k]) for k in classes
                 if any(r["accident_type"] == k for r in rows)}
    global_summary = _summarize(rows)
    boots = sorted({int(t["n_bootstrap"]) for t in templates.values()
                    if t.get("n_bootstrap") is not None})
    run_mode = run_mode_for(boots[0] if boots else None)
    bootstrap = {
        "n_bootstrap_values": boots,
        "run_mode": run_mode,
        "stability_claims_allowed": stability_claims_allowed(boots[0] if boots else None),
        "note": (f"正式口径 N_BOOTSTRAP_SPEC={N_BOOTSTRAP_SPEC}，但执行默认 5（V23-DESIGN-044）；"
                 "本次实际次数见 n_bootstrap_values，报告中必须原样打印。"
                 "次数未达正式口径时不得声称边稳定性、不得引用 consistency_rate 作结论；"
                 "citable 不再由次数自动推导，需人工显式声明"),
    }
    # 检索指标已断开流水线自动调用（V23-DESIGN-055，2026-11-11）：类别/模板检索是实现侧
    # 诊断工具，不是评测对象；需要时手动调用 evaluate_template_retrieval()，其结果不得进
    # 主表/结论句/论文数字。流水线因此不再产出 metrics_template_retrieval.json。
    truth_reach = TR.audit_run(run_id, [k for k in classes
                                        if any(r["accident_type"] == k for r in rows)])
    stability = stability_block(run_id)
    structural = structural_health_block(run_id)
    mechanism = mechanism_consistency_block()
    summary = {
        "n_traces": len(rows),
        "n_templates": len(by_template),
        "bootstrap": bootstrap,
        "report_header": (f"n_bootstrap={boots}（正式口径 {N_BOOTSTRAP_SPEC}；run_mode={run_mode}）"
                          + ("" if bootstrap["stability_claims_allowed"] else
                             " —— 缩减次数执行：不得声称边稳定性、不得引用 consistency_rate 作结论")),
        "result_layer_policy": {
            "withdrawn_at": RESULT_LAYER_WITHDRAWN_AT,
            "disposition": WITHDRAWAL_DISPOSITION,
            "reported": False,
            "rule": ("下列指标不进结果层：字段保留在 json、数值照算，但不得出现在逐模板解读报告、"
                     "类级汇总、submit 入口页与结果列表类文档里（V23-DESIGN-063）。"),
            "withdrawn": {k: v for k, v in UNSUPPORTED_METRICS.items()},
            "premises": {
                "P1_无独立真值": ("命中类指标的真值是按机理人工指定的假设，且与我方候选集存在构造关联；"
                                  "因此只能称“与假设的一致度”，不能称准确率"),
                "P2_样本不独立": ("同一模板下 20 条轨迹共享同一张图、同一组稳定边；因果通道全 run 只有 "
                                  "9 个不同排序。按 n=60 算的区间被系统性压窄："
                                  "n=60 → [0.0466, 0.2015]；n_eff=9 → [0.0199, 0.4350]；"
                                  "每类 n_eff=3 → [0.0615, 0.7923]"),
                "P3_门槛不可失败": ("VIF 门（500 既是阈值又是迭代停止条件，非保护 max 恰为 "
                                    "489.6/490.0/276.3）；条件数门（阈值 10000、实测截断后最高 1054.8、"
                                    "9/9 全过）；跨组一致性（Jaccard 0.2632 vs 随机 0.2144、"
                                    "名次标准差 1.89–2.87 vs 3.22–3.40、单调占比 1/15–5/15 vs "
                                    "0.27–0.33）；偏差通道 d 跨变量不可比（d_raw 到 1.67e5）"),
            },
            "kept_in_result_layer": ["结构健康度描述性原始量（节点数/稳定边数/边密度/cond 原值/VIF 原值）",
                                     "Top-5 变量榜与其排序键（total_effect 等）",
                                     "K2 三态关系检查与 K3 模板覆盖率（机理检查，非统计成绩）"],
        },
        "evaluation_protocol": {
            "statement": ("本工作不主张准确率；自 2026-11-11 起把已判定不可信的评价指标整体撤出结果层"
                          "（V23-DESIGN-063）：hit@k / MRR / P@k / Recall@k / AP@k / Jaccard@k、"
                          "名次分布与 Wilson 区间、跨组一致性（Jaccard/Spearman/单调占比/名次标准差）、"
                          "VIF 门与条件数门的通过结论、截断翻转、偏差通道 d 排序一律 reported=false，"
                          "字段保留在 json 仅作内部留档。结果层只保留：结构健康度描述性原始量、"
                          "Top-5 变量榜与排序键、K2/K3 机理检查。处置待定 2026-11-12。"),
            "primary_blocks": list(PRIMARY_BLOCKS),
            "primary_metric_tier": METRIC_TIER_PRIMARY,
            "diagnostic_metrics": list(DIAGNOSTIC_METRICS),
            "diagnostic_metric_tier": METRIC_TIER_DIAGNOSTIC,
            "diagnostic_blocks": ["global", "per_class", "per_template",
                                  "clip_sensitivity.clips.*", "evidence_rank_distribution"],
            "rule": ("primary 块内不得出现任何 diagnostic 指标键；"
                     "diagnostic 块必须标 metric_tier='diagnostic' 并给出 tier 映射"),
        },
        "stability": stability,
        "structural_health": structural,
        "mechanism_consistency": mechanism,
        "evidence_rank_distribution": {
            "metric_tier": METRIC_TIER_DIAGNOSTIC,
            **_withdrawn_fields(["rank_distribution"]),
            "definition": "真值名次分布（B3/V23-DESIGN-045）：rank_hist + 中位/均值名次 + 归一化名次 + 真值覆盖率",
            "why": "Hit@1 只反映头部；名次分布可区分'差一点'与'完全不可达'，且归一化名次消除候选集大小差异",
            "global": rank_distribution(rows),
            "per_class": {k: rank_distribution([r for r in rows if r["accident_type"] == k])
                          for k in classes if any(r["accident_type"] == k for r in rows)},
        },
        "truth_reachability": {
            "definition": "真值可达性审计（B2/V23-DESIGN-043）：真值变量在变量选择各阶段的最终去向",
            "artifact": "audit/truth_reachability.json（只读审计，不改变选择结果）",
            "summary": truth_reach["summary"],
        },
        "role_prior": {
            "definition": "角色先验（B1/V23-DESIGN-046）：外部响应变量名单作排序乘子，不进主排序公式",
            "enabled": prior_on,
            "weight": RP.ROLE_PRIOR_WEIGHT,
            "n_vars_in_list": len(RP.ROLE_PRIOR_VARS),
            "source": RP.ROLE_PRIOR_SOURCE,
            "scope": "仅排序乘子（不改 B、不改 T）",
            "pair_complete": (ROLE_PRIOR_CHANNEL in global_summary) == prior_on
                             and "causal" in global_summary,
            "note": ("开启时必须同时报告'有先验/无先验'两版排序与两套指标；"
                     "只有一版出现即视为不合规"),
        },
        "task": ("评测对象=事故内变量级根因溯源（候选=该模板 15 节点，含注入变量；"
                 "真值=类级注入变量集）；主表只含 stability/structural_health/"
                 "mechanism_consistency，Hit@k/MRR 等一律为附录诊断；"
                 "类别/模板识别指标（template_retrieval）仅为实现侧诊断，"
                 "status=not_evaluation_target，流水线不再自动计算/落盘，需要时手动调用，"
                 "不得进主表或结论句（V23-DESIGN-055）"),
        "evaluation_target": EVALUATION_TARGET,
        "ground_truth": {
            "definition": "该事故类型的始发（注入）事件变量集",
            "source": "causalrca/config/injection_truth.py::INJECTION_TRUTH",
            "provenance": IT.SOURCE,
            "by_class": {k: gt_effective[k] for k in gt_effective if k in
                         {r["accident_type"] for r in rows}},
            "suspect": {k: list(v) for k, v in IT.SUSPECT.items() if k in gt_effective},
            "scope": "class_level（NPPAD 数据只给严重度=文件名，无逐样本注入字段）",
        },
        "channels": {
            "causal": "生产口径（取消遮蔽：候选=模板全部节点，含注入变量，共 15 节点）",
            "causal_unmasked_for_eval": {
                "description": "历史别名：取消遮蔽后与 causal 等价（同一 15 节点、同一名次）",
                "legacy_alias": True,
                "note": "仅为兼容旧产物/旧文档保留；数值与 causal 逐项相同，不得据此另作比较",
            },
            "deviation_equal_weight": {
                "description": "纯偏差基线（15 节点，与 causal 同变量集）",
                **_withdrawn_fields(["deviation_channel"]),
                "why": "d 跨变量不可比（量纲与各自噪声/下限归一，d_raw 到 1.67e5）",
            },
            "deviation_production_set": {
                "description": ("历史别名：取消遮蔽后“生产集”=“全集”，与 deviation_equal_weight "
                                "等价（同为 15 节点）"),
                "legacy_alias": True,
                **_withdrawn_fields(["deviation_channel"]),
                "note": "仅为兼容旧四通道表保留；不得据此另作比较（d 本身也不得跨变量排序）",
            },
        },
        "pairing": {
            "因果 vs 偏差": "causal ↔ deviation_equal_weight（同 15 节点，严格同变量集）",
            "历史别名": ("causal_unmasked_for_eval 与 causal 等价；deviation_production_set 与 "
                        "deviation_equal_weight 等价；旧四通道表按这两对映射回当前两列"),
            "note": ("取消遮蔽（2026-11-11）后 15/14 两套候选集合并为一套（15 节点），"
                     "四通道只余“因果 vs 偏差”两组真正不同的排序；不得跨配对比较"),
        },
        "test_selection": {k: {g: ((split["classes"].get(k) or {}).get("groups", {})
                                   .get(g, {}) or {}).get("test_severities")
                               for g in groups} for k in classes},
        "clip_sensitivity": {
            "channel": "causal（含注入变量；只改 |B| 的截断上限）",
            "why": ("总效应 = |T| 行和，T=(I−B)⁻¹；B_CLIP 硬截断会改变名次，"
                    "因此变量级命中必须并列 clip3/clip5/clip10/clip_off 四列"),
            "clips": {
                label: {
                    "global": _summarize_clip(rows, label),
                    "per_class": {k: _summarize_clip(
                        [r for r in rows if r["accident_type"] == k], label)
                        for k in classes if any(r["accident_type"] == k for r in rows)},
                    "per_template": {
                        name: {kk: by_template_clips[name][label][kk]
                               for kk in ("hit1", "hit3", "mrr", "marker_rank_median", "n",
                                          "metric_tier", "tier")}
                        for name in by_template_clips},
                } for label, _clip in CLIP_VARIANTS},
            "flips": _clip_flip_note(by_template_clips),
        },
        "per_class": per_class,
        "template_retrieval": {
            "status": RETRIEVAL_METRIC_STATUS,
            "evaluation_target": EVALUATION_TARGET,
            "note": ("诊断工具，非评测对象（V23-DESIGN-055）：流水线不再自动计算/落盘，"
                     "需要时手动调用 evaluate_template_retrieval；结果不得进主表、"
                     "结论句或论文数字"),
        },
        "per_template": {name: {ch: {kk: s[ch][kk] for kk in
                                     ("hit1", "hit3", "mrr", "p2", "p3", "recall3", "ap3", "n",
                                      "metric_tier", "tier")}
                                for ch in CHANNELS}
                         for name, s in by_template.items()},
        "global": global_summary,
        "metric_definition": {
            "metric_tier": METRIC_TIER_DIAGNOSTIC,
            "hit1": "始发变量排第 1", "hit3": "始发变量进前 3",
            "mrr": "1/首个始发变量名次", "p2/p3": "前 2/前 3 中始发变量占比",
            "recall3": "前 3 覆盖的始发变量数/真值集大小",
            "ap3": "AP@3（与老师 metrics_for_ranking 同式）",
            "jaccard3": "前 3 集合与真值集的 Jaccard",
            "wilson95": "仅 Hit@1/Hit@3 的二项比例区间",
        },
        "not_v21_paradigm": ("不做事故类型分类/匹配；不把 9 模板当检索池；"
                             "不用关键响应变量或 forced/protected 集合当判据"),
        "comparability_with_teacher": {
            "same_task": True,
            "comparable_numbers": False,
            "reason": ("同任务（事故内变量级 Top-k 命中）但不同协议：我们无人工先验降权、"
                       "切分严格互斥、每 (类,组) 单独建图、候选含注入变量（取消遮蔽）；"
                       "引用老师 0.4368/0.5800 只能作脚注，不得并排"),
        },
        "leakage_note": ("只用 test_severities 轨迹；候选与排序全部读本 run 已落盘产物；"
                         "不重算 bootstrap、不重新学图；真值不参与选变量/建图/排序"),
        "source": "signatures/signature_*.json + tracing/topology_*.json + config/injection_truth.py",
    }
    path = artifact.write_artifact("metrics_summary", summary, "metrics", run_id=run_id,
                                   source="causalrca/reporting/evaluation.py")
    written.append(Path(path).name)
    logger.info(f"[D11-evaluation] {len(rows)} 条留出轨迹 / {len(by_template)} 个模板；"
                f"hit1 causal={global_summary['causal']['hit1']} "
                f"unmasked={global_summary['causal_unmasked_for_eval']['hit1']} "
                f"基线={global_summary['deviation_equal_weight']['hit1']}")
    return {"files": sorted(written), "n_traces": len(rows), "summary": summary}

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

**候选与四通道（同一批轨迹；偏差基线按配对变量集拆两列）**
- `causal`（生产口径）：`trace_roots` 原样——候选=模板节点中排除 marker（故障指示器
  遮蔽），排序键总因果效应 DESC → in_degree ASC → weighted_reach DESC → reach_size DESC
  → consensus_rate DESC，再按“有传导能力(out_degree>0)优先、孤立节点殿后”分组。
- `deviation_equal_weight`（纯偏差基线，与去遮蔽通道同 15 节点）与 `deviation_production_set`
  （纯偏差基线，与生产通道同 14 节点）：同候选集等权，只按该轨迹偏移强度
  d(v)=max|x−μ_pre|/max(1.4826·MAD_pre, σ_floor(v)) 降序，完全不用因果图。
  两列基线只为保证「因果 vs 偏差」各自严格同变量集，**不得跨配对比较**。
- `causal_unmasked_for_eval`（评测模式）：把被遮蔽的注入点放回候选，用**已落盘**
  `signatures/*.stable_edges` 重算 T=(I−B)⁻¹ 的总效应（纯矩阵运算，不重算 bootstrap、
  不重新学图），其余排序键与生产口径一致。**只用于评测，不改变生产输出**；
  没有这一列，注入点结构性不可能被命中（命中率恒 0 只能说明“被遮蔽”，
  不能回答“根因找对没有”）。

**与老师 accuracy 的关系**
同任务（事故内变量级 Top-k 命中）、但**不同协议**：我们无人工先验降权、切分严格互斥
（split_manifest.test_severities）、每 (类,组) 单独建图、且额外给“遮蔽/去遮蔽”两列。
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
from causalrca.config.constants_v23 import B_CLIP
from causalrca.data import loader as D
from causalrca.evidence import external_knowledge as EK
from causalrca.io import artifact
from causalrca.io.logging import logger

CUTOFF = 3                       # 指标截断 @3（与老师 metrics_for_ranking 的 k=3 一致）
BASELINE_LEAD_SEC = 10.0         # 注入前窗口末端留 10s 余量（[0, t_inject−10]）
CHANNELS = ("causal", "causal_unmasked_for_eval",
            "deviation_equal_weight", "deviation_production_set")
METRIC_KEYS = ("hit1", "hit3", "mrr", "p2", "p3", "recall3", "ap3", "jaccard3")


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
            "method_note": rc.get("method_note")}


# ==================== 排序 ====================
def _production_order(tpl: Dict, total_effect: Dict[str, float],
                      exclude: Sequence[str] = ()) -> List[str]:
    """生产口径排序：效应 DESC → 次级拓扑键；有传导能力者优先，孤立节点殿后。"""
    def key(v):
        nd = tpl["nodes"][v]
        return (-float(total_effect.get(v) or 0.0),
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


def causal_ranking(tpl: Dict) -> List[str]:
    """生产口径：marker 遮蔽（不进候选），效应取 topology 节点已落盘 total_effect。"""
    eff = {v: (nd.get("total_effect") or 0.0) for v, nd in tpl["nodes"].items()}
    return _production_order(tpl, eff, exclude=[tpl["marker"]] if tpl["marker"] else [])


def unmasked_total_effect(tpl: Dict) -> Dict[str, float]:
    """评测模式：把注入点放回候选，用已落盘 stable_edges 重算总效应（纯矩阵，无 bootstrap）。"""
    variables = list(tpl["variables"])
    T, idx, _diag = ST.build_total_effect_matrix(tpl["stable_edges"], variables,
                                                 clip=B_CLIP, return_diag=True)
    effect_vars = {v for v, nd in tpl["nodes"].items() if int(nd.get("out_degree") or 0) == 0}
    if tpl["marker"]:
        effect_vars.add(tpl["marker"])
    effect_idx = [idx[v] for v in effect_vars if v in idx]
    return {v: float(np.sum(np.abs(T[effect_idx, idx[v]]))) if effect_idx else 0.0
            for v in variables}


def causal_ranking_unmasked(tpl: Dict) -> List[str]:
    """评测模式排序：候选含注入点，其余排序键与生产口径一致。"""
    return _production_order(tpl, unmasked_total_effect(tpl))


def trace_deviation(df, cfg: Dict, variables: Sequence[str]) -> Dict[str, float]:
    """逐轨迹偏移强度 d(v)=max|x−μ_pre|/max(1.4826·MAD_pre, σ_floor(v))（与类别无关）。"""
    t_inject = float(cfg.get("t_inject", 0.0))
    win = cfg.get("main_window") or [300.0, 1500.0]
    pre = df[df["TIME"] <= t_inject - BASELINE_LEAD_SEC + 1e-9]
    if len(pre) < 3:
        pre = df[df["TIME"] <= t_inject]
    post = df[(df["TIME"] >= float(win[0]) - 1e-9) & (df["TIME"] <= float(win[1]) + 1e-9)]
    if len(pre) < 2 or len(post) < 1:
        raise ValueError("轨迹窗口点数不足（基线段或主窗口）")
    d: Dict[str, float] = {}
    for v in variables:
        if v not in df.columns:
            continue
        base = pre[v].to_numpy(dtype=float)
        mu = float(np.mean(base))
        sigma_mad = 1.4826 * float(np.median(np.abs(base - mu)))
        floor, _src = EK.sigma_floor_for(v, mu)
        sigma = max(sigma_mad, float(floor))
        if not np.isfinite(sigma) or sigma <= 0:
            continue
        vals = post[v].to_numpy(dtype=float)
        if vals.size == 0:
            continue
        d[v] = float(np.max(np.abs(vals - mu) / sigma))
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


def _summarize(rows: Sequence[Dict]) -> Dict:
    out: Dict[str, Dict] = {}
    for ch in CHANNELS:
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
        out[ch] = agg
    return out


# ==================== 主流程 ====================
def evaluate(run_id: str, classes: Optional[Sequence[str]] = None,
             groups: Optional[Sequence[str]] = None) -> Dict:
    """跑变量级留出评测并落 metrics/；返回 {files, n_traces, summary}。"""
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
        d = trace_deviation(df, cfg, list(tpl["nodes"]))
        if not d:
            raise ValueError(f"{k}/{sev}: 偏移强度为空，无法评测")
        rank_c = causal_ranking(tpl)
        rank_u = causal_ranking_unmasked(tpl)
        rank_d = deviation_ranking(list(tpl["nodes"]), d)
        # 偏差基线第二列：与生产通道**同变量集**（不含被遮蔽的注入点）
        rank_d_prod = deviation_ranking(
            [v for v in tpl["nodes"] if v != tpl["marker"]], d)
        row = {
            "accident_type": k, "group": g, "severity": sev, "template": tr["template"],
            "gt": gt,
            "gt_in_candidate_set": {"causal": [v for v in gt if v in rank_c],
                                    "causal_unmasked_for_eval": [v for v in gt if v in rank_u],
                                    "deviation_equal_weight": [v for v in gt if v in rank_d],
                                    "deviation_production_set": [v for v in gt if v in rank_d_prod]},
            "d_top5": [[v, round(d[v], 3)] for v in sorted(d, key=lambda x: -d[x])[:5]],
            "top5": {"causal": rank_c[:5], "causal_unmasked_for_eval": rank_u[:5],
                     "deviation_equal_weight": rank_d[:5],
                     "deviation_production_set": rank_d_prod[:5]},
            "metrics": {"causal": ranking_metrics(rank_c, gt),
                        "causal_unmasked_for_eval": ranking_metrics(rank_u, gt),
                        "deviation_equal_weight": ranking_metrics(rank_d, gt),
                        "deviation_production_set": ranking_metrics(rank_d_prod, gt)},
        }
        row["metrics"] = {ch: {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                               for kk, vv in row["metrics"][ch].items()} for ch in CHANNELS}
        rows.append(row)

    written: List[str] = []
    by_template: Dict[str, Dict] = {}
    for name, tpl in templates.items():
        sub = [r for r in rows if r["template"] == name]
        if not sub:
            continue
        gt = gt_effective[tpl["k"]]
        marker = tpl["marker"]
        masked = (tpl["nodes"].get(marker) or {}).get("total_effect") is None
        eff_unmasked = unmasked_total_effect(tpl)
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
                "causal_order": "生产口径：排除 marker；total_effect DESC → in_degree ASC → weighted_reach DESC → reach_size DESC → consensus_rate DESC；有传导者优先、孤立节点殿后",
                "deviation_order": "同候选集等权，按 d(v) DESC（无因果图）；两列基线分别对应 15 节点（含注入点）与 14 节点（生产集）",
                "unmasked_note": "评测模式：注入点放回候选，用已落盘 stable_edges 重算 T=(I−B)⁻¹（不重算 bootstrap、不改生产输出）",
            },
            "injection_point_masking": {
                "variable": marker, "masked": masked,
                "total_effect_production": (tpl["nodes"].get(marker) or {}).get("total_effect"),
                "total_effect_unmasked_for_eval": round(eff_unmasked.get(marker, 0.0), 6),
                "root_candidates_rank_production": tpl["marker_info"].get("rank"),
                "note": tpl["marker_info"].get("note"),
            },
            "method_note": tpl["method_note"],
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

    per_class = {k: _summarize([r for r in rows if r["accident_type"] == k]) for k in classes
                 if any(r["accident_type"] == k for r in rows)}
    global_summary = _summarize(rows)
    summary = {
        "n_traces": len(rows),
        "n_templates": len(by_template),
        "task": ("事故内变量级根因溯源：候选=模板节点排序；指标=始发（注入）变量是否被排到 "
                 "Top-1/Top-3；不做事故类型匹配、不用关键响应当判据"),
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
            "causal": "生产口径（注入点被遮蔽、候选=14 节点）",
            "causal_unmasked_for_eval": "评测模式（注入点放回候选=15 节点；其余同生产口径）",
            "deviation_equal_weight": "纯偏差基线（15 节点，与 causal_unmasked_for_eval 同变量集）",
            "deviation_production_set": "纯偏差基线（14 节点，与 causal 同变量集）",
        },
        "pairing": {
            "因果 vs 偏差（评测模式）": "causal_unmasked_for_eval ↔ deviation_equal_weight（同 15 节点）",
            "因果 vs 偏差（生产口径）": "causal ↔ deviation_production_set（同 14 节点）",
            "note": "两列偏差基线的差别只在候选集是否含被遮蔽的注入点；不得跨配对比较",
        },
        "test_selection": {k: {g: ((split["classes"].get(k) or {}).get("groups", {})
                                   .get(g, {}) or {}).get("test_severities")
                               for g in groups} for k in classes},
        "per_class": per_class,
        "per_template": {name: {ch: {kk: s[ch][kk] for kk in
                                     ("hit1", "hit3", "mrr", "p2", "p3", "recall3", "ap3", "n")}
                                for ch in CHANNELS}
                         for name, s in by_template.items()},
        "global": global_summary,
        "metric_definition": {
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
                       "切分严格互斥、每 (类,组) 单独建图、另有遮蔽/去遮蔽两列；"
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

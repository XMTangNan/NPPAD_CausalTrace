"""V2.3 D11：留出集评测（第 10 步，reports 之后 / manifest 之前）。

口径（V23-DESIGN-035，写死在本文件，改动需同步方案 §5.12 与 error_tracking）

1) 留出集与无泄漏
   - 只读本 run 的 `registry/split_manifest.json`：每类 20 条 test_severities
     （按组 7/7/6 = 7 low + 7 mid + 6 high），三类共 60 条；
   - 训练用过的 `train_template_severities` 一律不进评测；
   - 打分只用本 run 已落盘产物 + 留出轨迹自身：
     `tracing/topology_{K}_{g}.json`（T=(I−B)⁻¹ 溯源权重）、`registry/accident_registry.json`
     （t_inject / main_window）；**不重算 bootstrap、不重新学图、不训练任何模型**。

2) 逐例打分（候选 = 池内全部模板 (K, g)，默认 9 个）
   每条留出轨迹先算一份**与类别无关**的偏移强度 d(v)（D2 同一套规则搬到逐轨迹）：
     d(v) = max_{t∈main_window} |x(t) − μ_pre(v)| / max(1.4826·MAD_pre, σ_floor(v))
     μ_pre / MAD_pre 取该轨迹注入前窗口（TIME ≤ t_inject − 10）；σ_floor 取
     external_knowledge.sigma_floor_for(v, μ_pre)（逐变量物理下限，与类别无关）。
   d 是全轨迹池内变量**横向不可比**的（不同量纲/σ 尺度差几个数量级，且步响应普遍
   远超 10σ），因此逐轨迹先做**名次归一** r(v)=d(v) 在本轨迹池内变量的百分位
   （0–1，并列取均值秩；D9 的 ±10 截断只用于展示，这里排序用未截断 d_raw，
   与 D9 “截断仅展示、不参与排序”的口径一致）。
   两个通道共用同一份 r，只在「模板内怎么加权」上不同：
     - causal：score_t = Σ_{v∈V_t} w_t(v)·r(v) / Σ_{v∈V_t} w_t(v)，
       w_t(v) = topology.nodes[v].total_effect（因果溯源权重），越大越能解释；
     - deviation_equal_weight（纯偏差基线）：同 V_t 上等权平均 r，
       不使用 T=(I−B)⁻¹ 的边方向、强度与溯源权重，只用偏离度排序。
   两通道的差异即「因果结构是否带来增益」的对照。

3) 相关性判定与指标
   - 一条轨迹的相关项 = 真类别的 3 个模板（同类不同组）：两通道都不估 severity，
     组内不做区分；精确模板命中单列 `exact_hit1`（只作参考，不计入主指标）；
   - 逐例算 Hit@1 / Hit@3 / MRR / P@2 / P@3 / Recall@3 / AP@3（cutoff=3，9 个候选）；
   - 9 个模板各落 `metrics/metrics_{K}_{g}.json`（含本模板 6–7 条轨迹明细与汇总）；
     `metrics/metrics_summary.json` 给每类（n=20）与全局（n=60）汇总 + Wilson 95%
     区间（仅对 Hit@1 / Hit@3 / exact_hit1 这类二项比例给区间）。

4) 与 v2.1 口径差异
   v2.1（`archive/rca_v21_frozen/baseline_metrics.json`）是 3 类**事故类型分类准确率**
   （B0 marker 规则 / B1 Pearson / B2 XGB，FULL 与 MASKED 两种条件，60 条留出），
   报的是 accuracy + Wilson CI；本步骤是**9 模板检索排序质量**（含同类组级模板），
   报 AP@3/P@2/P@3/Recall@3/MRR/Hit@1/Hit@3，并用「等权偏离度」作同表基线。
   两者不可直接比数值，只能比「同类结论是否一致」。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from causalrca.config.constants_v21 import GROUP_ORDER, SEVERITY_BANDS
from causalrca.data import loader as D
from causalrca.evidence import external_knowledge as EK
from causalrca.io import artifact
from causalrca.io.logging import logger

CUTOFF = 3                       # 指标截断 @3
BASELINE_LEAD_SEC = 10.0         # 注入前窗口末端留 10s 余量（[0, t_inject−10]）
CHANNELS = ("causal", "deviation_equal_weight")


# ==================== 工具 ====================
def wilson_ci95(k: int, n: int) -> List[Optional[float]]:
    """Wilson 95% 区间（与 v2.1 baseline_metrics.json 同式，p=1 时下界=1/(1+z²/n)）。"""
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
    """按 SEVERITY_BANDS 归组；落不进任何组立即报错（不猜）。"""
    for g in GROUP_ORDER:
        lo, hi = SEVERITY_BANDS[g]
        if lo <= int(severity) <= hi:
            return g
    raise ValueError(f"severity={severity} 不在任何严重度组内")


# ==================== 池与留出集 ====================
def load_split_manifest(run_id: Optional[str] = None) -> Dict:
    doc = _read_json("registry", "split_manifest.json", run_id)
    if not doc.get("classes"):
        raise ValueError("split_manifest.json 缺 classes，无法评测")
    return doc


def load_pool(classes: Sequence[str], groups: Sequence[str],
              run_id: Optional[str] = None) -> List[Dict]:
    """候选模板池：读 tracing/topology_{K}_{g}.json 的变量集与溯源权重。"""
    pool: List[Dict] = []
    for k in classes:
        for g in groups:
            doc = _read_json("tracing", f"topology_{k}_{g}.json", run_id)
            nodes = doc.get("nodes") or {}
            variables = list(doc.get("variables") or [])
            if not variables or not nodes:
                raise ValueError(f"topology_{k}_{g}.json 缺 variables/nodes")
            weights = {v: float(nodes[v].get("total_effect") or 0.0) for v in variables}
            if sum(weights.values()) <= 0:
                raise ValueError(f"topology_{k}_{g}.json 的 total_effect 全为 0，无法排序")
            pool.append({"name": f"{k}_{g}", "k": k, "g": g,
                         "variables": variables, "weights": weights})
    return pool


def load_registry(run_id: Optional[str] = None) -> Dict:
    return _read_json("registry", "accident_registry.json", run_id).get("registry") or {}


def test_traces(split: Dict, classes: Sequence[str],
                groups: Sequence[str]) -> List[Dict]:
    """留出测试轨迹清单：只取 split_manifest 的 test_severities（禁取训练轨迹）。"""
    out: List[Dict] = []
    for k in classes:
        cls = split["classes"].get(k)
        if not cls:
            continue
        for g in groups:
            grp = (cls.get("groups") or {}).get(g) or {}
            for sev in grp.get("test_severities") or []:
                out.append({"accident_type": k, "group": g, "severity": int(sev),
                            "true_template": f"{k}_{g}"})
    return out


# ==================== 逐轨迹偏移强度 ====================
def trace_deviation(df, cfg: Dict, variables: Sequence[str]) -> Dict[str, float]:
    """逐轨迹偏移强度 d_raw（与类别无关）：max|x−μ_pre| / max(1.4826·MAD, σ_floor)。"""
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


def percentile_ranks(d: Dict[str, float]) -> Dict[str, float]:
    """把偏移强度换成逐轨迹内的百分位名次（0–1，并列取均值秩），消除量纲差异。"""
    if not d:
        return {}
    names = list(d)
    vals = np.array([d[v] for v in names], dtype=float)
    out: Dict[str, float] = {}
    for v in names:
        less = float(np.sum(vals < d[v]))
        equal = float(np.sum(np.isclose(vals, d[v], rtol=0.0, atol=1e-12)))
        out[v] = (less + 0.5 * (equal + 1.0)) / float(len(names))
    return out


# ==================== 打分 ====================
def score_template(tpl: Dict, r: Dict[str, float], channel: str) -> float:
    """模板得分（越大越能解释）：causal 按溯源权重加权，基线等权。"""
    vals: List[float] = []
    weights: List[float] = []
    for v in tpl["variables"]:
        if v not in r:
            continue
        vals.append(r[v])
        weights.append(tpl["weights"][v] if channel == "causal" else 1.0)
    if not vals:
        return 0.0
    wsum = float(sum(weights))
    if wsum <= 0:
        return 0.0
    return float(sum(w * x for w, x in zip(weights, vals)) / wsum)


# ==================== 指标 ====================
def retrieval_metrics(ranking: Sequence[str], relevant: Sequence[str],
                      exact: str) -> Dict[str, Optional[float]]:
    """单条轨迹的检索指标（相关项=同类 3 模板；exact 仅记录不计分）。"""
    rel = set(relevant)
    ranks = [i for i, name in enumerate(ranking, start=1) if name in rel]
    first = ranks[0] if ranks else None
    top = list(ranking[:CUTOFF])
    hits = [1 if name in rel else 0 for name in top]
    precision_sum = 0.0
    for i, h in enumerate(hits, start=1):
        precision_sum += (sum(hits[:i]) / i) * h
    return {
        "hit1": 1.0 if first == 1 else 0.0,
        "hit3": 1.0 if first is not None and first <= CUTOFF else 0.0,
        "mrr": (1.0 / first) if first else 0.0,
        "p2": sum(hits[:2]) / 2.0,
        "p3": sum(hits[:CUTOFF]) / CUTOFF,
        "recall3": sum(hits) / len(rel) if rel else 0.0,
        "ap3": precision_sum / min(CUTOFF, len(rel)) if rel else 0.0,
        "first_correct_rank": first,
        "exact_hit1": 1.0 if ranking[0] == exact else 0.0,
    }


def _rank(pool: Sequence[Dict], scores: Dict[str, float]) -> List[str]:
    """按分数降序；同分按池顺序（类序 × 组序）稳定，保证可复现。"""
    order = {t["name"]: i for i, t in enumerate(pool)}
    return sorted(scores, key=lambda n: (-scores[n], order[n]))


# ==================== 汇总 ====================
_METRIC_KEYS = ("hit1", "hit3", "mrr", "p2", "p3", "recall3", "ap3", "exact_hit1")


def _summarize(rows: Sequence[Dict]) -> Dict:
    """对一组轨迹的逐例指标取均值；二项比例额外给 Wilson 95%。"""
    out: Dict[str, Dict] = {}
    for ch in CHANNELS:
        mets = [r["metrics"][ch] for r in rows]
        n = len(mets)
        agg: Dict[str, object] = {}
        for key in _METRIC_KEYS:
            vals = [m[key] for m in mets]
            agg[key] = round(float(np.mean(vals)), 4) if n else None
        for key in ("hit1", "hit3", "exact_hit1"):
            k = int(sum(round(float(m[key])) for m in mets))
            lo, hi = wilson_ci95(k, n)
            agg[f"{key}_n"] = k
            agg[f"{key}_wilson95"] = [None if lo is None else round(lo, 4),
                                      None if hi is None else round(hi, 4)]
        agg["n"] = n
        out[ch] = agg
    out["delta_causal_minus_baseline"] = {
        key: (None if out["causal"][key] is None else
              round(float(out["causal"][key]) - float(out["deviation_equal_weight"][key]), 4))
        for key in ("hit1", "hit3", "mrr", "ap3")
    }
    return out


# ==================== 主流程 ====================
def evaluate(run_id: str, classes: Optional[Sequence[str]] = None,
             groups: Optional[Sequence[str]] = None) -> Dict:
    """跑留出集评测并落 metrics/；返回 {files, n_traces, summary}。"""
    from causalrca.config.accident_config import READY_CODES
    classes = list(classes) if classes else [k for k in READY_CODES]
    groups = list(groups) if groups else list(GROUP_ORDER)
    split = load_split_manifest(run_id)
    registry = load_registry(run_id)
    pool = load_pool(classes, groups, run_id)
    traces = test_traces(split, classes, groups)
    if not traces:
        raise ValueError("split_manifest.json 未给出任何测试轨迹")
    pool_vars = sorted({v for t in pool for v in t["variables"]})

    rows: List[Dict] = []
    for tr in traces:
        k, sev = tr["accident_type"], tr["severity"]
        cfg = registry.get(k) or {}
        df = D.load_trajectory(k, sev)
        d = trace_deviation(df, cfg, pool_vars)
        if not d:
            raise ValueError(f"{k}/{sev}: 偏移强度为空，无法评测")
        r = percentile_ranks(d)
        scores = {ch: {t["name"]: score_template(t, r, ch) for t in pool} for ch in CHANNELS}
        relevant = [t["name"] for t in pool if t["k"] == k]
        row = {
            "accident_type": k, "group": tr["group"], "severity": sev,
            "true_template": tr["true_template"], "n_vars": len(d),
            "d_top5": [[v, round(d[v], 3)] for v in sorted(d, key=lambda x: -d[x])[:5]],
            "scores": {ch: {n: round(s, 4) for n, s in sc.items()} for ch, sc in scores.items()},
            "ranking": {ch: _rank(pool, scores[ch]) for ch in CHANNELS},
            "metrics": {},
        }
        for ch in CHANNELS:
            row["metrics"][ch] = retrieval_metrics(row["ranking"][ch], relevant,
                                                   tr["true_template"])
            # 逐例指标保留 4 位，避免长尾小数影响可读性
            row["metrics"][ch] = {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                                  for kk, vv in row["metrics"][ch].items()}
        rows.append(row)

    written: List[str] = []
    by_template: Dict[str, Dict] = {}
    for t in pool:
        sub = [r for r in rows if r["true_template"] == t["name"]]
        if not sub:
            continue
        cls = (split["classes"].get(t["k"]) or {}).get("groups", {}).get(t["g"], {})
        payload = {
            "template": {"k": t["k"], "g": t["g"], "n_vars": len(t["variables"]),
                         "test_severities": list(cls.get("test_severities") or []),
                         "n_train_templates": len(cls.get("train_template_severities") or [])},
            "n_test": len(sub),
            "pool": [x["name"] for x in pool],
            "relevance_rule": "相关项 = 真类别的全部模板（同类不同组）；组内不区分，精确模板命中见 exact_hit1",
            "channels": {ch: ("topology total_effect 加权（T=(I−B)⁻¹ 溯源权重）" if ch == "causal"
                              else "同变量集等权平均（纯偏差基线，不用因果权重）")
                         for ch in CHANNELS},
            "traces": sub,
            "summary": _summarize(sub),
            "source": f"registry/split_manifest.json + tracing/topology_{t['k']}_{t['g']}.json",
            "leakage_note": "只用 test_severities；训练轨迹不参与；不改图、不重算 bootstrap",
        }
        path = artifact.write_artifact(f"metrics_{t['k']}_{t['g']}", payload, "metrics",
                                       run_id=run_id, accident_type=t["k"], group=t["g"],
                                       source=f"tracing/topology_{t['k']}_{t['g']}.json")
        written.append(Path(path).name)
        by_template[t["name"]] = payload["summary"]

    per_class = {k: _summarize([r for r in rows if r["accident_type"] == k]) for k in classes}
    global_summary = _summarize(rows)
    best = max(by_template.items(), key=lambda kv: kv[1]["causal"]["hit1"]) if by_template else None
    worst = min(by_template.items(), key=lambda kv: kv[1]["causal"]["hit1"]) if by_template else None
    summary = {
        "n_traces": len(rows),
        "n_templates": len(by_template),
        "pool": [x["name"] for x in pool],
        "cutoff": CUTOFF,
        "test_selection": {k: {g: ((split["classes"].get(k) or {}).get("groups", {})
                                   .get(g, {}) or {}).get("test_severities")
                               for g in groups} for k in classes},
        "per_class": per_class,
        "per_template": {name: {ch: {"hit1": s[ch]["hit1"], "hit3": s[ch]["hit3"],
                                     "mrr": s[ch]["mrr"], "ap3": s[ch]["ap3"],
                                     "n": s[ch]["n"]} for ch in CHANNELS}
                         for name, s in by_template.items()},
        "global": global_summary,
        "conclusion": {
            "best_template_by_causal_hit1": best[0] if best else None,
            "worst_template_by_causal_hit1": worst[0] if worst else None,
            "note": ("两通道都不估 severity，故组内模板只能靠因果权重差异区分；"
                     "exact_hit1 仅作参考，主指标按同类判定"),
        },
        "metric_definition": {
            "hit1": "排名第 1 的模板属于真类别",
            "hit3": "前 3 名含真类别模板",
            "mrr": "1/首个同类模板的名次",
            "p2": "前 2 名中同类模板占比",
            "p3": "前 3 名中同类模板占比",
            "recall3": "前 3 名覆盖的同类模板数 / 同类模板总数(3)",
            "ap3": "AP@3（相关项=同类 3 模板）",
            "exact_hit1": "(K,g) 精确命中第 1 名（不计入主指标）",
        },
        "v21_diff": ("v2.1 baseline_metrics.json 是 3 类分类准确率（B0 marker/B1 Pearson/B2 XGB，"
                     "FULL 与 MASKED，60 条留出，accuracy+Wilson）；本步骤是 9 模板检索排序"
                     "（AP@3/P@2/P@3/Recall@3/MRR/Hit@1/Hit@3 + 等权偏离度基线），"
                     "两者只能比结论方向，不能比数值"),
        "leakage_note": ("留出轨迹取自 split_manifest.test_severities；签名/溯源/公差全部读本 run "
                         "已落盘产物，不重算 bootstrap；σ_floor 与 t_inject 取自 external_knowledge "
                         "与 persisted registry，不使用测试轨迹的标签信息"),
        "source": "registry/split_manifest.json + tracing/topology_*.json",
    }
    path = artifact.write_artifact("metrics_summary", summary, "metrics", run_id=run_id,
                                   source="causalrca/reporting/evaluation.py")
    written.append(Path(path).name)
    logger.info(f"[D11-evaluation] {len(rows)} 条留出轨迹 / {len(by_template)} 个模板；"
                f"causal hit1={global_summary['causal']['hit1']} "
                f"基线 hit1={global_summary['deviation_equal_weight']['hit1']}")
    return {"files": sorted(written), "n_traces": len(rows),
            "summary": summary}

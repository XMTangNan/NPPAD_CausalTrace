"""V2.3 判定式解释路径（V23-DESIGN-062）：把算法的 Top-5 交给 LLM 判断合理性。

**默认关闭**。开关：环境变量 ``CAUSALTRACE_LLM_VERDICT``（``1/true/on/yes`` 为开，其余一律关）。
关闭时主链行为完全不变：既有 9 份 ``llm_explanation_{K}_{g}.json`` 仍由规则模板生成，
本模块只被显式调用（CLI 或开启开关后的报表步骤）时才产生额外产物，不替换任何现有文件。

三重约束（与方案文档一一对应）：
  1. 图：解释只能引用 evidence_pack 里的稳定边与系数；
  2. 排序：rank 由算法给定，校验器拒绝任何重排；
  3. 物理：机理陈述必须引用 knowledge 条目 id（K2 relation_id / K3 模板 id / 补充条目 id）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from causalrca.evidence import external_knowledge as EK
from causalrca.io import artifact
from causalrca.reporting import llm_explanation as LE

logger = logging.getLogger(__name__)

VERDICT_ENV = "CAUSALTRACE_LLM_VERDICT"
VERDICT_VALUES = ("supported", "partially_supported", "unsupported", "undetermined")
EVIDENCE_SCHEMA = "causaltrace/explanation_evidence/v1"
VERDICT_SCHEMA = "causaltrace/explanation_verdict/v1"
MAX_REPAIR = 1

VERDICT_SYSTEM_PROMPT = """你是核电事故根因分析的审阅者，不是分析者。你的唯一任务：对算法已经给出的 Top-5 根因逐项判断“是否合理”，并给出受约束的解释。

提问口径（重要）：要回答的是**“这批前五在物理机理上是否可能位于传导起点”**，而不是
“有没有资格当唯一根因”。本项目的“根因”定义 = 同类事故内最先动起来、并把影响传下去的变量
（方案 §0.1）；报告可以写“根因”，但必须说明是“以传导起点为定义的根因”。

四态语义（缺条目 ≠ 无支撑）：
- supported（有支撑）：知识或机理明确指向它是起点、或起点性传导节点；
- partially_supported（部分支撑）：方向对，但存在未解释环节（如只有半条链、只有一个方向一致）；
- unsupported（无支撑）：存在**明确反向证据**才可判——如 response_not_root 条目、K3 反向边、
  或机理上明确是下游响应；仅仅“知识库没有条目”不得判 unsupported；
- undetermined（无法判断）：知识缺口或证据冲突无法裁决，必须写明缺哪一条证据。

硬约束：
1. 排序由算法给定。不得新增、删除、替换候选；不得改变名次；正文中不得暗示另一种排序。
2. 只能使用 evidence_pack 中出现的变量名、稳定边及其系数。禁止引入图中不存在的变量、边或数值。
3. 每条机理陈述必须引用 evidence_pack.knowledge 中的条目 id（K2 形如 R1_inventory_leak；K3 形如 K3:LOCA；补充条目形如 S1_LOCA_P_response / P1_LOCA_WLR_injection）。正向条目（P 系列）与否定性条目（S 系列）都由 knowledge[].polarity 标出，不得混用。
4. K2 的 not_violated 只表示“未被否定”，不能当作“支持”；K3 的 n_matched 只说明期望链命中几条边，不单独构成支持。
5. 数值只允许原样引用 evidence_pack 中出现的数并保留原精度；禁止做四则运算推导新数值，禁止给出概率、置信度、百分比估计。
6. 三类证据分开写：图证据（边/系数/入出度）、知识证据（条目 id）、缺失证据（无覆盖）。三者冲突必须写进 conflicts，不得用文字抹平。
7. 不确定就判 undetermined，并在 uncertainty_notes 写明“缺哪一条证据”；禁止用“可能/或许/通常”替代缺失依据。
8. 只输出一个 JSON 对象，严格符合给定 schema；不要任何解释性前后文。
9. `conclusion` 是报告"结论"章的正文：不超过 120 字，只能复述算法名次与上述判定，不得改变名次、不得引入新变量或新数值；引用机理必须带知识条目 id；若证据不足，直接写明"无法判断"。
10. **若五个候选全部为 unsupported 或 undetermined**，`conclusion` 与 `conflict_notes` 必须给出“图为何这样排”的诊断：只能从 evidence_pack.graph_diagnosis 里选事实（双向边对=反馈被单向化、与 K3 期望相反=方向可能学反、|系数| 量级异常、出度≥7 且入度≤2 的枢纽），不得编造其它原因。
11. 正文不复述 `total_effect` 的具体数值（写“总效应见第一章”）；稳定边系数可以原样引用。
12. **候选集状态只能引用 evidence_pack**：`nodes[].in_candidate_set` 恒为 true（入模即候选），
    是否进前五看 `has_rank`；`excluded_variables[].gate_outcome` 才是“不在候选集”的变量。
    **不得把“未进前五”写成“不在候选集”**；任何“某变量未进入/不在/未纳入候选集”的断言，
    只有在 `excluded_variables` 里能查到对应变量与 `gate_outcome` 时才允许。
"""

_PROMPT_SCHEMA = """{
  "schema": "causaltrace/explanation_verdict/v1",
  "run_id": "<与证据包一致>",
  "template_id": "<与证据包一致>",
  "ranking_source": "algorithm",
  "verdicts": [
    {
      "rank": <整数，必须等于算法名次>,
      "variable": "<必须是证据包 candidates 中的变量>",
      "verdict": "supported | partially_supported | unsupported | undetermined",
      "graph_basis": ["FROM->TO", "..."],
      "knowledge_basis": ["<K2 id | K3:CODE | 补充条目 id>"],
      "mechanism": "<一句话，不超过 60 字>",
      "conflicts": ["<冲突说明>"]
    }
  ],
  "conflict_notes": ["..."],
  "uncertainty_notes": ["..."],
  "conclusion": "<报告结论章正文：≤120 字，只复述名次与判定，不改名次、不加新变量/新数值>"
}"""

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def verdict_enabled() -> bool:
    """开关是否打开（默认关闭）。"""
    return os.environ.get(VERDICT_ENV, "").strip().lower() in ("1", "true", "on", "yes")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# ==================== 证据包 ====================
def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _signature(run_id: str, k: str, g: str) -> Dict[str, Any]:
    path = artifact.subdir_path("signatures", run_id) / f"signature_{k}_{g}.json"
    return _read_json(path)


def _stable_edges(sig: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for e in sig.get("stable_edges") or []:
        out.append({
            "from": e.get("from"), "to": e.get("to"),
            "weight_median": e.get("weight_median"),
            "weight_iqr": e.get("weight_iqr"),
            "dir_e": e.get("dir_e"), "f_e": e.get("f_e"),
            "count": e.get("count"),
        })
    return out


def _k2_entries(k: str, payload_k2: Dict[str, Any]) -> List[Dict[str, Any]]:
    status = {r.get("relation_id"): r.get("status")
              for r in (payload_k2.get("relations") or [])}
    out = []
    for rel in EK.load_physics_relations():
        if k not in (rel.get("applicable_accidents") or []):
            continue
        rid = rel.get("id")
        out.append({
            "id": rid, "name": rel.get("name"),
            "lhs": rel.get("lhs"), "rhs": rel.get("rhs"),
            "rhs_operator": rel.get("rhs_operator"),
            "expected_sign": rel.get("expected_sign"),
            "status": status.get(rid),
            "missing_terms": rel.get("missing_terms"),
            "applicable_accidents": rel.get("applicable_accidents"),
        })
    return out


def _k3_entry(k: str, payload_k3: Dict[str, Any]) -> Dict[str, Any]:
    tpl = EK.load_fault_templates(k)
    return {
        "template_id": f"K3:{k}",
        "verification": payload_k3.get("verification"),
        "coverage": payload_k3.get("coverage"),
        "n_chain_edges": payload_k3.get("n_chain_edges"),
        "n_matched": payload_k3.get("n_matched"),
        "expected_chain": tpl.get("expected_chain"),
        "expected_sign_direction": tpl.get("expected_sign_direction"),
        # 注意：v2.3 的 K3 产物只落 n_matched 与 missing/reverse，不落逐边 matched 列表；
        # 早先这里硬编码 []，会让模型误判“K3 无任何边支持”，已移除（V23-DESIGN-065）。
        "reverse_edges": payload_k3.get("reverse_edges") or [],
        "missing_edges": payload_k3.get("missing_edges") or [],
        "note": "expected_chain 只是机理骨架，不构成逐变量方向断言。",
    }


def _truth_rows(run_id: str, k: str) -> Dict[str, Dict[str, Any]]:
    """读 B2 真值可达性审计：给出每个真值变量的去向（selected / score_rejected / …）。

    证据包必须显式携带“某变量在不在候选集、为什么”，否则模型会自行推断并可能断言错
    （V23-DESIGN-075：LOCA_low 曾出现“WLR 未进候选”，实际 WLR 已入选、只是未进前五）。
    """
    try:
        path = artifact.subdir_path("audit", run_id) / f"truth_reachability_{k}.json"
        doc = _read_json(path)
    except FileNotFoundError:
        return {}
    return {r.get("var"): r for r in doc.get("rows") or [] if r.get("var")}


def _graph_diagnosis(candidates: List[Dict[str, Any]], edges: List[Dict[str, Any]],
                     docs: Dict[str, Any]) -> List[Dict[str, str]]:
    """程序预计算的“图为何这样排”诊断：只列事实，不给因果故事。

    起因：用户指出“全是否定”没有信息量；若前五确实缺物理支撑，报告必须说明图是怎么排出来的。
    这里只提供可复核的图事实（双向边对、与 K3 方向相反、出高入低的枢纽、量级最大的边），
    模型只能引用这些事实，不得自行发挥。
    """
    out: List[Dict[str, str]] = []
    directed = {(e["from"], e["to"]) for e in edges}
    pairs = sorted({(e["from"], e["to"]) for e in edges})
    for a, b in pairs:
        if (b, a) in directed and (a, b) < (b, a):
            out.append({"type": "two_way_pair", "subject": f"{a}<->{b}",
                        "detail": ("两向都有稳定边。若物理上这是反馈回路，单向化后的方向是算法选的，"
                                   "不是辨识出来的；这类边的方向需要单独复核。")})
    for edge in docs["k3"].get("reverse_edges") or []:
        out.append({"type": "k3_reverse", "subject": edge.get("stable"),
                    "detail": (f"K3 期望 {edge.get('template')}，稳定边为 {edge.get('stable')}；"
                               "与期望链方向相反，可能是方向学反。")})
    hubs = [c for c in candidates
            if (c.get("out_degree") or 0) >= 7 and (c.get("in_degree") or 0) <= 2]
    for c in hubs:
        out.append({"type": "hub_sink_pattern", "subject": c["variable"],
                    "detail": (f"出度 {c.get('out_degree')}、入度 {c.get('in_degree')}：在图上呈"
                               "“只出不进”的源状结构，总效应会因此被放大。")})
    biggest = sorted(edges, key=lambda e: abs(e.get("weight_median") or 0.0), reverse=True)[:3]
    for e in biggest:
        out.append({"type": "largest_edge", "subject": f"{e['from']}->{e['to']}",
                    "detail": (f"|系数| = {abs(e.get('weight_median') or 0.0):.6g}，"
                               "为图中量级最大的边；大系数边对总效应的贡献占主导，需核对是否物理合理。")})
    return out[:8]


def _supplementary_entries(k: str) -> List[Dict[str, Any]]:
    """该事故可用的补充条目：否定性（S 系列）与正向（P 系列）分开标注 polarity。"""
    out: List[Dict[str, Any]] = []
    for e in EK.load_supplementary_mechanisms().get("entries") or []:
        if k in (e.get("applicable_accidents") or []):
            out.append({**e, "polarity": "negative"})
    for e in EK.load_positive_mechanisms().get("entries") or []:
        if k in (e.get("applicable_accidents") or []):
            out.append({**e, "polarity": "positive"})
    return out


def _conflicts(candidates: List[Dict[str, Any]], docs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """程序预计算的冲突项：把矛盾摊在桌面上，避免模型自行“圆回来”。"""
    out: List[Dict[str, Any]] = []
    sup = {e.get("variable"): e for e in docs["supplementary"]}
    chain = set(docs["k3"].get("expected_chain") or [])
    k2_vars = set()
    for rel in docs["k2"]:
        k2_vars.update([rel.get("lhs"), rel.get("rhs")])
    for c in candidates:
        v = c["variable"]
        if v in sup and sup[v].get("can_be_root") is False:
            out.append({"type": "response_not_root", "subject": v,
                        "detail": sup[v].get("basis"),
                        "knowledge_id": sup[v].get("id")})
        elif v not in k2_vars and v not in chain:
            out.append({"type": "no_knowledge_coverage", "subject": v,
                        "detail": "K2/K3/补充条目均未覆盖该变量"})
        elif v in chain:
            out.append({"type": "mid_chain_node", "subject": v,
                        "detail": "该变量出现在 K3 expected_chain 中段，属链上节点而非链首驱动"})
    for edge in docs["k3"].get("reverse_edges") or []:
        out.append({"type": "reverse_edge", "subject": edge.get("stable"),
                    "detail": f"K3 期望 {edge.get('template')}，稳定边为 {edge.get('stable')}"})
    for edge in docs["k3"].get("missing_edges") or []:
        out.append({"type": "missing_edge", "subject": edge.get("template"),
                    "detail": f"期望边 {edge.get('template')} 无稳定边（{edge.get('reason')}）"})
    return out


def build_evidence_pack(run_id: str, k: str, g: str) -> Dict[str, Any]:
    """组装证据包（只读现有产物，不重算任何因果量）。"""
    payload = LE.build_payload(k, g, run_id=run_id)
    sig = _signature(run_id, k, g)
    edges = _stable_edges(sig)
    var_table = EK.load_variable_dictionary().get("variables") or {}

    candidates: List[Dict[str, Any]] = []
    for c in payload.get("root_candidates") or []:
        v = c.get("variable")
        candidates.append({
            "rank": c.get("rank"), "variable": v,
            "total_effect": c.get("total_effect"),
            "in_degree": c.get("in_degree"), "out_degree": c.get("out_degree"),
            "reach_size": c.get("reach_size"), "weighted_reach": c.get("weighted_reach"),
            "incoming": [{"from": e["from"], "weight_median": e["weight_median"], "dir_e": e["dir_e"]}
                         for e in edges if e["to"] == v],
            "outgoing": [{"to": e["to"], "weight_median": e["weight_median"], "dir_e": e["dir_e"]}
                         for e in edges if e["from"] == v],
        })

    docs = {"k2": _k2_entries(k, payload.get("k2") or {}),
            "k3": _k3_entry(k, payload.get("k3") or {}),
            "supplementary": _supplementary_entries(k)}

    nodes = []
    role_map = {c.get("variable"): c.get("role")
                for c in payload.get("root_candidates") or []}
    top_set = {c.get("variable") for c in payload.get("root_candidates") or []}
    truth = _truth_rows(run_id, k)
    for v in payload.get("variables") or []:
        info = var_table.get(v) or {}
        nodes.append({"variable": v,
                      "meaning_cn": info.get("meaning"),
                      "unit": info.get("unit"),
                      "subsystem": info.get("subsystem"),
                      "role": role_map.get(v, "non_candidate"),
                      "in_graph": True,
                      # 候选集状态显式下发（V23-DESIGN-075）：入模变量一律在候选集内，
                      # 是否进前五由 has_rank 表示；两者不可混为一谈。
                      "in_candidate_set": True,
                      "gate_outcome": "selected",
                      "has_rank": v in top_set})
    excluded = [{"variable": var, "in_candidate_set": False,
                 "gate_outcome": row.get("disposition"),
                 "detail": row.get("detail"), "is_truth": row.get("truth")}
                for var, row in truth.items() if row.get("disposition") != "selected"]

    k3 = docs["k3"]
    return {
        "schema": EVIDENCE_SCHEMA,
        "run_id": run_id,
        "template_id": f"{k}_{g}",
        "accident": {"code": k, "name_cn": (EK.load_fault_templates(k) or {}).get("name_cn"),
                     "group": g, "k3_template_id": k3["template_id"]},
        "algorithm": {
            "engine": "lingam", "n_bootstrap": payload.get("n_bootstrap"),
            "top_k": len(candidates),
            "rank_key": "(-total_effect, in_degree, -weighted_reach, -reach_size, -consensus_rate)",
            "gate_ratio": payload.get("gate_ratio"),
            "n_variables": len(payload.get("variables") or []),
            "n_stable_edges": len(edges),
        },
        "nodes": nodes,
        "excluded_variables": excluded,
        "stable_edges": edges,
        "candidates": candidates,
        "knowledge": {
            "k2": docs["k2"],
            "k3": k3,
            "supplementary": docs["supplementary"],
            "notes": [payload.get("k2", {}).get("note"),
                      "补充条目来自人工审核（V23-DESIGN-061），只标角色，不改 K2 数值口径与 K3 链。"],
        },
        "conflicts": _conflicts(candidates, docs),
        "graph_diagnosis": _graph_diagnosis(candidates, edges, docs),
        "provenance": {
            "files": [f"reports/llm_explanation_{k}_{g}.json",
                      f"signatures/signature_{k}_{g}.json",
                      "Code/external_knowledge/variable_dictionary.yaml",
                      "Code/external_knowledge/physics_relations.yaml",
                      "Code/external_knowledge/fault_templates.yaml",
                      "Code/external_knowledge/supplementary_mechanisms.yaml",
                      "Code/external_knowledge/positive_mechanisms.yaml"],
        },
        "allowed_terms": sorted(_allowed_terms(k, docs)),
    }


def _allowed_terms(k: str, docs: Dict[str, Any]) -> set:
    """词表补充：模板代码、K2/K3 标签与知识条目 id 中出现的 UPPER 片段。"""
    terms = {"K1", "K2", "K3", "JSON", k}
    ids = [r.get("id") for r in docs["k2"]]
    ids.append(docs["k3"].get("template_id"))
    ids += [e.get("id") for e in docs["supplementary"]]
    for item in ids:
        if item:
            terms.update(LE._UPPER_VAR_RE.findall(str(item)))
    return terms


def build_prompt(pack: Dict[str, Any]) -> str:
    return ("以下为 CausalTrace v2.3 的证据包（只读产物，未重算因果）。\n"
            f"请逐项判定 candidates 中 {len(pack['candidates'])} 个候选的合理性，"
            "并严格按 schema 输出 JSON。\n\n"
            "【输出 schema】\n" + _PROMPT_SCHEMA + "\n\n"
            "【证据包】\n" + json.dumps(pack, ensure_ascii=False) + "\n")


# ==================== 校验 ====================
def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    m = _JSON_FENCE_RE.search(text)
    raw = m.group(1) if m else text
    start = raw.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


_CLAIM_NEG = ("未进", "未进入", "不在候选", "不属于候选", "未纳入", "未入选", "未选入",
              "排除在候选", "没有进入候选", "未作为候选", "未被列为候选", "没进入候选")
_CLAIM_POS = ("在候选集", "属于候选", "是候选", "已进入候选", "在候选内")


def _candidate_claims(doc: Dict[str, Any], pack: Dict[str, Any]) -> List[str]:
    """硬检查：模型不得对“某变量在不在候选集”做与证据包相反的断言。

    V23-DESIGN-075 的由来：LOCA_low 第二版正文写“正向注入起点 WLR 未进候选”，
    而 `tracing/root_candidates_LOCA_low.json` 中 WLR 已入选（`rank: null`，只是未进前五）。
    这里把两类状态分开：`in_candidate_set`（恒 true 的入模变量）与 `has_rank`（是否前五）。
    """
    state: Dict[str, bool] = {n["variable"]: bool(n.get("in_candidate_set", True))
                              for n in pack.get("nodes") or []}
    for e in pack.get("excluded_variables") or []:
        state[e["variable"]] = False
    texts: List[str] = [doc.get("conclusion") or ""]
    for v in doc.get("verdicts") or []:
        texts.append(v.get("mechanism") or "")
        texts.extend(v.get("conflicts") or [])
    texts.extend(doc.get("conflict_notes") or [])
    texts.extend(doc.get("uncertainty_notes") or [])

    errors: List[str] = []
    for text in texts:
        for sent in re.split(r"[。；;\n]", text):
            if "候选" not in sent:
                continue
            names = [nm for nm in state
                     if re.search(rf"(?<![A-Za-z0-9_]){re.escape(nm)}(?![A-Za-z0-9_])", sent)]
            if not names:
                continue
            neg = any(k in sent for k in _CLAIM_NEG)
            pos = any(k in sent for k in _CLAIM_POS)
            for nm in names:
                if neg and state[nm]:
                    errors.append(f"候选集状态断言不实：{nm} 已在候选集（gate_outcome=selected），"
                                  "正文却称其未进入候选")
                if pos and not state[nm]:
                    errors.append(f"候选集状态断言不实：{nm} 不在候选集"
                                  "（gate_outcome 见 excluded_variables），正文却称其在候选集")
    seen: set = set()
    uniq: List[str] = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    return uniq


def validate_verdict(doc: Dict[str, Any], pack: Dict[str, Any]) -> Dict[str, Any]:
    """程序侧校验（方案 §6 的 8 条）；任一条不过即判失败。"""
    errors: List[str] = []
    warnings: List[str] = []
    if doc.get("schema") != VERDICT_SCHEMA:
        errors.append(f"schema 不符：{doc.get('schema')!r}")
    if doc.get("run_id") != pack["run_id"]:
        errors.append("run_id 与证据包不一致")
    if doc.get("template_id") != pack["template_id"]:
        errors.append("template_id 与证据包不一致")
    if doc.get("ranking_source") != "algorithm":
        errors.append("ranking_source 必须为 algorithm")

    cand_rank = {c["variable"]: c["rank"] for c in pack["candidates"]}
    edge_refs = {f'{e["from"]}->{e["to"]}' for e in pack["stable_edges"]}
    kn_ids = {r["id"] for r in pack["knowledge"]["k2"]}
    kn_ids.add(pack["knowledge"]["k3"]["template_id"])
    kn_ids.update(e["id"] for e in pack["knowledge"]["supplementary"])

    verdicts = doc.get("verdicts")
    if not isinstance(verdicts, list):
        errors.append("verdicts 不是数组")
        verdicts = []
    seen = set()
    for v in verdicts:
        if not isinstance(v, dict):
            errors.append("verdicts 元素不是对象")
            continue
        var = v.get("variable")
        if var not in cand_rank:
            errors.append(f"候选集合外变量：{var!r}")
            continue
        if var in seen:
            errors.append(f"候选重复：{var}")
        seen.add(var)
        if v.get("rank") != cand_rank[var]:
            errors.append(f"名次不符/疑似重排：{var} 输出 {v.get('rank')}，算法 {cand_rank[var]}")
        if v.get("verdict") not in VERDICT_VALUES:
            errors.append(f"verdict 枚举非法：{var} -> {v.get('verdict')!r}")
        for ref in v.get("graph_basis") or []:
            if ref not in edge_refs:
                errors.append(f"图引用不存在：{var} -> {ref}")
        for ref in v.get("knowledge_basis") or []:
            if ref not in kn_ids:
                errors.append(f"知识引用不存在：{var} -> {ref}")
        if v.get("verdict") == "unsupported" and not (v.get("conflicts") or []):
            errors.append(f"unsupported 未给冲突依据：{var}")
        if v.get("verdict") == "undetermined" and not (doc.get("uncertainty_notes") or []):
            errors.append(f"undetermined 未给 uncertainty_notes：{var}")
        text = " ".join(str(x) for x in
                        [v.get("mechanism") or ""] +
                        list(v.get("conflicts") or []) +
                        list(v.get("knowledge_basis") or []))
        faith = LE.verify_faithfulness(text, pack)
        if not faith["faithful"]:
            errors.append(f"忠实性校验未过：{var} 未支撑数值 {faith['unsupported']}，"
                          f"未知词 {faith['unknown']}")
        for t in LE._UPPER_VAR_RE.findall(str(v.get("mechanism") or "")):
            if t not in {n["variable"] for n in pack["nodes"]} and t not in pack["allowed_terms"]:
                errors.append(f"越界变量名：{var} -> {t}")

    if len(seen) != len(cand_rank):
        errors.append(f"候选覆盖不全：{len(seen)}/{len(cand_rank)}")
    if not (doc.get("uncertainty_notes") or doc.get("conflict_notes")):
        warnings.append("conflict_notes 与 uncertainty_notes 均为空")

    # 结论章：必须是模型写的正文，且不得越界（变量名/数值/知识 id 同口径校验）
    concl = doc.get("conclusion")
    if not isinstance(concl, str) or not concl.strip():
        errors.append("缺少 conclusion（结论章正文）")
    else:
        if len(concl) > 300:
            errors.append(f"conclusion 超长（{len(concl)} 字 > 300）")
        faith = LE.verify_faithfulness(concl, pack)
        if not faith["faithful"]:
            errors.append(f"conclusion 忠实性校验未过：未支撑数值 {faith['unsupported']}，"
                          f"未知词 {faith['unknown']}")
        vars_in_graph = {n["variable"] for n in pack["nodes"]}
        for t in LE._UPPER_VAR_RE.findall(concl):
            if t not in vars_in_graph and t not in pack["allowed_terms"]:
                errors.append(f"conclusion 越界变量名：{t}")
    errors.extend(_candidate_claims(doc, pack))
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "n_verdicts": len(seen)}


# ==================== 执行 ====================
def _repair_prompt(pack: Dict[str, Any], prev: str, errors: List[str]) -> str:
    return (build_prompt(pack) +
            "\n【上一次输出未通过校验】\n- " + "\n- ".join(errors[:20]) +
            "\n请只修正上述问题，重新输出完整 JSON；候选、变量与名次不得改动。\n"
            "上一次输出：\n" + prev[:6000] + "\n")


def run_template_verdict(run_id: str, k: str, g: str,
                         timeout: Optional[float] = None) -> Dict[str, Any]:
    """对单个模板执行一次判定式解释（含至多 1 次修复）；失败降级为 rule。"""
    pack = build_evidence_pack(run_id, k, g)
    pack_sha = sha256_json(pack)
    cred = LE.resolve_llm_credentials()
    out: Dict[str, Any] = {
        "schema": VERDICT_SCHEMA,
        "run_id": run_id,
        "template_id": f"{k}_{g}",
        "switch_env": VERDICT_ENV,
        "switch_value": os.environ.get(VERDICT_ENV),
        "evidence_sha256": pack_sha,
        "backend": "rule",
        "explanation_mode": "rule_fallback",
        "llm": {"key_source": cred["key_source"], "model": cred["model"],
                "base_url_source": cred["base_url_source"], "attempts": 0},
        "validation": {"ok": False, "errors": [], "warnings": []},
        "degraded_reason": None,
        "verdict": None,
        "raw_text": None,
        "evidence_pack": pack,
    }
    reason = LE.llm_unavailable_reason()
    if reason:
        out["degraded_reason"] = reason
        return out

    prompt = build_prompt(pack)
    last_text = ""
    attempts = 1 + MAX_REPAIR
    for i in range(attempts):
        out["llm"]["attempts"] = i + 1
        try:
            text = LE._call_chat_completions(prompt, timeout=timeout,
                                             system_prompt=VERDICT_SYSTEM_PROMPT)
        except Exception as exc:                              # noqa: BLE001 - 网络/鉴权统一降级
            out["degraded_reason"] = f"调用失败：{type(exc).__name__}: {str(exc)[:200]}"
            return out
        last_text = text
        doc = _extract_json(text)
        if doc is None:
            check = {"ok": False, "errors": ["输出不是合法 JSON"], "warnings": []}
        else:
            check = validate_verdict(doc, pack)
        if check["ok"]:
            out.update({"backend": "llm", "explanation_mode": "llm_verified",
                        "verdict": doc, "validation": check, "raw_text": text})
            return out
        logger.warning(f"[D6] 判定式解释校验未过（第 {i + 1} 次）：{check['errors'][:5]}")
        if i + 1 < attempts:
            prompt = _repair_prompt(pack, text, check["errors"])
        else:
            out["validation"] = check
            out["degraded_reason"] = "校验未通过（已尝试修复）"
    out["raw_text"] = last_text
    return out


def write_verdict(out: Dict[str, Any], k: str, g: str,
                  subdir: str = "reports") -> Path:
    """落盘判定结果（默认写入当前 run 的 reports 下，文件名带 verdict 前缀，不覆盖旧文件）。"""
    name = f"llm_explanation_verdict_{k}_{g}.json"
    return artifact.write_artifact(name, out, subdir)

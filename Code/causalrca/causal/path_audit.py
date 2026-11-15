"""多跳后置审计（方案 §4.2.7；V23-DESIGN-108 修订）：抽贡献最大路径 + 全部违规路径，逐跳判定。

原则：
  * 只读图，不回改 `B`/`T`/`rank`；违规只报冲突；
  * 路径长度只记录（`max_hops` 是计算上限，不是物理跳数约束）；
  * 逐跳三项：禁令 / 角色公理 / 极性；缺依据标 `unsupported`，不冒充“知识支持”。

V23-DESIGN-108（2026-11-15）修订 —— 针对“审计无判别力”：
  1) 角色 6→4 重新映射为**因果角色**（不是流水线顺序）：
       fault_indicator（注入=破口泄漏流量指示器）/ actuator（控制动作）/
       process（物理过程量，含传导与状态）/ measurement（测量仪表）。
     只保留一条公理：`measurement` 必须是终端（仪表→物理量 判违规）。
     原“后果不能作上游”“驱动必须在最前”这类整链顺序要求取消——它们把
     “过程量互相耦合”误判成违规，是本轮 2471 条违规的主因。
  2) 极性拆成两件事：
       * 硬证据：条目里**带出处**的边级 sign；sign 缺失/无出处一律 unsupported；
       * 自洽检查：变量级 expected_direction 只用于**矛盾检测**（两条方向已知时，
         边的符号应等于两方向之积），不当作“支持”。矛盾分硬/软两级：
         硬=知识自带 sign 与方向矛盾；软=学到的边符号与方向矛盾。
  3) 统计口径分层：`counts_all_scored` 是全枚举分布（无偏），`counts_top` 是
     贡献最大 top_n 样本，`counts_violating_extra` 是被追加的违规路径。
     原口径把“全部违规路径”并进分母，导致违规占比虚高、无法判读。

V23-DESIGN-112（2026-11-15，第一批①–③）—— 不改图，只改审计判定与记账：
  ① 禁边两端记账。建图层（`constraints.derive_rules` / `build_ontology` /
     `apply_ontology_constraint`）逐条登记每条禁边去向：`applied`（估到并清零）/
     `exempted_competitive`（被竞争项豁免）/ `dropped_no_endpoint`（端点不在变量集）。
     审计层不再拿“建图前已清零的表”当检查，改为**扫描最终稳定边全集**，
     只报“最终图里仍存在被禁边”这种真命中（`forbidden_audit`）。
  ② 软违规拆两种性质：
       (a) 与**有出处**的边级 sign 冲突，且变量级方向也有出处 → 仍判 `violating`（hard）；
       (b) 只有单侧有出处、或学到的边符号与变量级方向冲突 → 判 `needs_recheck`
           （“待复核信号”），**不进违规分母**，原文理由保留。
     映射与阈值一律未动，变化只来自“有无出处”这一条既有字段。
  ③ 角色 6→4 映射为草案、无专家签字，**不再单独构成 violating**：只作为
     “为什么判 unsupported”的理由保留（`role_advisory`）。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Set, Tuple

# 原始 6 种角色 → 4 类因果角色（V23-DESIGN-108 重新映射）
ROLE_MAP = {
    "injection": "fault_indicator",
    "response_control": "actuator",
    "driver": "process",
    "response_conduction": "process",
    "response": "process",
    "response_state": "process",
    "response_indicator": "measurement",
}

# 角色公理：只约束“仪表不得作为上游物理原因”，其余组合不设整链顺序要求。
# V23-DESIGN-112：该映射为草案、无专家签字，**只作降级理由，不单独判违规**。
ROLE_AXIOMS = {"measurement_terminal"}

FORBID_DENY = "violating"
PASS = "supported"
UNSUP = "unsupported"
RECHECK = "needs_recheck"   # 待复核信号：有冲突但至少一侧无出处，不构成违规
NA = "not_applicable"
SATISFIED = "satisfied"

VIOLATION_HARD = "hard"
VIOLATION_SOFT = "soft"      # 已废止（V23-DESIGN-112 起改判 RECHECK），仅保留常量做旧产物对照

ROLE_ADVISORY_PREFIX = "【角色映射·草案，无专家签字】"

STATUS_ORDER = ("compliant", "unsupported", "violating")


def _load_knowledge(accident: str) -> Dict:
    """读两文件，构造角色/方向/边级 sign/反向禁令索引。"""
    import yaml
    from causalrca.evidence.constraints import KNOW_DIR, FILES
    from causalrca.evidence.knowledge import COMPETITIVE_PAIRS, COMPETITIVE_PAIRS_BY_CLASS
    role, raw_role, direction, edge_sign = {}, {}, {}, {}
    dir_sourced: Dict[str, bool] = {}
    for name in FILES:
        doc = yaml.safe_load((KNOW_DIR / name).read_text(encoding="utf-8")) or {}
        for ent in doc.get("entries") or []:
            if accident not in (ent.get("applicable_accidents") or []):
                continue
            var = ent.get("variable")
            rr = ent.get("role_in_accident")
            raw_role[var] = rr
            role[var] = ROLE_MAP.get(rr)
            direction[var] = ent.get("expected_direction")
            # 变量级方向是否“有出处”：与 direction 同口径（后写覆盖），供 ② 判“单侧无凭据”
            dir_sourced[var] = bool(ent.get("sourced"))
            for e in ent.get("asserted_edges") or []:
                if not isinstance(e, dict):
                    continue
                key = (e.get("from"), e.get("to"))
                edge_sign[key] = {
                    "sign": e.get("sign"),
                    "reverse_forbidden": bool(e.get("reverse_forbidden")),
                    "sourced": bool(e.get("sourced")),
                    "entry_id": ent.get("id"),
                    "expected_direction": ent.get("expected_direction"),
                    "raw_role": rr,
                }
    # 与约束层同口径：competitive 双向对豁免反向禁令（否则会把已被豁免的边误判违规）
    comp: Set[Tuple[str, str]] = set()
    for a, b in COMPETITIVE_PAIRS_BY_CLASS.get(accident, COMPETITIVE_PAIRS):
        comp.add((a, b))
        comp.add((b, a))
    return {"role": role, "raw_role": raw_role, "direction": direction,
            "dir_sourced": dir_sourced, "edge": edge_sign, "comp": comp}


def _role_check(ks: Dict, a: str, b: str) -> Tuple[str, str]:
    """角色检查（V23-DESIGN-112 起降级为“降级理由”，不再单独判违规）。

    返回 UNSUP + 保留原始理由，使路径落到 `unsupported` 而不是 `violating`；
    `role_advisory=true` 标记该 hop 仅因角色草案被判无依据。
    """
    ra, rb = ks["role"].get(a), ks["role"].get(b)
    if ra is None or rb is None:
        return NA, "角色未登记（无约束可判）"
    if ra == "measurement" and rb != "measurement":
        return (UNSUP,
                f"{ROLE_ADVISORY_PREFIX} measurement→{rb}：仪表不得作为物理量上游；"
                f"据此不足以判违规，只作 unsupported 理由")
    return NA, f"{ra}→{rb} 无角色顺序约束"


def _implied_sign(dir_a: Optional[str], dir_b: Optional[str],
                  src_a: bool, src_b: bool) -> Tuple[Optional[str], bool]:
    """两条变量级方向已知且为二元时，边符号应为二者之积；并给出该推断是否两侧都有出处。"""
    if dir_a in ("pos", "neg") and dir_b in ("pos", "neg"):
        return ("pos" if dir_a == dir_b else "neg"), bool(src_a and src_b)
    return None, False


def _polarity_check(ks: Dict, u: str, v: str,
                    weights: Dict[Tuple[str, str], float]
                    ) -> Tuple[str, str, Optional[str], Optional[str]]:
    """极性：硬证据（带出处 sign）/ 无支撑 / 冲突（hard 违规 或 needs_recheck 待复核）。

    返回 (status, reason, entry_id, violation_kind)；violation_kind 非空即 hard。
    """
    src_u = bool(ks.get("dir_sourced", {}).get(u))
    src_v = bool(ks.get("dir_sourced", {}).get(v))
    dir_u, dir_v = ks["direction"].get(u), ks["direction"].get(v)
    implied, implied_sourced = _implied_sign(dir_u, dir_v, src_u, src_v)
    e = ks["edge"].get((u, v))
    if e:
        sign = e.get("sign")
        if not e.get("sourced"):
            return UNSUP, "边级 sign 未注明出处（不得当证据）", e.get("entry_id"), None
        if sign not in ("pos", "neg"):
            return UNSUP, f"边级 sign={sign!r} 非方向性，不能判极性", e.get("entry_id"), None
        if implied is not None and sign != implied:
            if implied_sourced:
                return (FORBID_DENY,
                        f"知识自带 sign={sign} 与两端有出处方向之积={implied} 矛盾",
                        e.get("entry_id"), VIOLATION_HARD)
            return (RECHECK,
                    f"带出处边级 sign={sign} 与无出处变量方向之积={implied} 不一致："
                    f"单侧无凭据，不构成矛盾（待复核）", e.get("entry_id"), None)
        return PASS, f"带出处边级 sign={sign}", e.get("entry_id"), None
    # 无条目边级断言：只做“方向自洽”检查，不当作支持
    if implied is None:
        return UNSUP, "该跳无带出处边级极性，且两端方向不完整，无法校验", None, None
    w = weights.get((u, v))
    if w is None or abs(w) <= 0.0:
        return UNSUP, "该跳学到的边权重为 0，符号无意义", None, None
    learned = "pos" if w > 0 else "neg"
    if learned != implied:
        side = "有出处" if implied_sourced else "无出处"
        return (RECHECK,
                f"学到的边符号={learned} 与{side}变量方向之积={implied} 不一致："
                f"数据与方向推断的差异，非知识冲突（待复核）", None, None)
    return UNSUP, "方向自洽但无带出处边级极性（不算支持）", None, None


def _forbidden_check(fset: Set[Tuple[str, str]], ks: Dict,
                     u: str, v: str) -> Tuple[str, str, Optional[str], Optional[str]]:
    """单跳禁边判定；返回 (status, reason, entry_id, violation_kind)。

    V23-DESIGN-112：只有**有出处**的 reverse_forbidden 才判 hard；
    无出处的反向断言降级为待复核信号。最终图级的真命中由 `forbidden_audit` 负责。
    """
    if (u, v) in fset:
        return FORBID_DENY, f"{u}→{v} 命中硬禁边（最终图仍存在，建图层失效）", None, "hard_forbidden_edge"
    rev = ks["edge"].get((v, u))
    if rev and rev.get("reverse_forbidden"):
        if (u, v) in ks.get("comp", set()):
            return PASS, "未命中禁令（竞争对已豁免反向）", rev.get("entry_id"), None
        if rev.get("sourced"):
            return (FORBID_DENY, f"{u}→{v} 是断言 {v}→{u} 的反向（reverse_forbidden）",
                    rev.get("entry_id"), "hard_reverse_forbidden")
        return (RECHECK,
                f"{u}→{v} 是断言 {v}→{u} 的反向，但该断言无出处，只记待复核",
                rev.get("entry_id"), None)
    return PASS, "未命中禁令", None, None


def _enumerate(adj: Dict[str, List[str]], starts: Sequence[str],
               max_hops: int, max_paths: int) -> Tuple[List[List[str]], bool]:
    paths: List[List[str]] = []
    truncated = False

    def dfs(node: str, path: List[str]) -> None:
        nonlocal truncated
        if len(paths) >= max_paths:
            truncated = True
            return
        if len(path) >= 2:
            paths.append(list(path))
        if len(path) - 1 >= max_hops:
            return
        for nb in adj.get(node, []):
            if nb not in path:
                path.append(nb)
                dfs(nb, path)
                path.pop()
                if len(paths) >= max_paths:
                    truncated = True
                    return

    for s in starts:
        dfs(s, [s])
        if len(paths) >= max_paths:
            truncated = True
            break
    return paths, truncated


def _empty_counts() -> Dict[str, int]:
    return {k: 0 for k in STATUS_ORDER}


def _paths_between(adj: Dict[str, List[str]], a: str, c: str,
                   max_hops: int, max_paths: int) -> Tuple[List[List[str]], bool]:
    """枚举 a→c 的简单路径（含直连 a→c）。只用于审计侧判定。"""
    out: List[List[str]] = []
    truncated = False

    def dfs(node: str, path: List[str]) -> None:
        nonlocal truncated
        if len(out) >= max_paths:
            truncated = True
            return
        if node == c and len(path) >= 2:
            out.append(list(path))
            return
        if len(path) - 1 >= max_hops:
            return
        for nb in adj.get(node, []):
            if nb not in path:
                path.append(nb)
                dfs(nb, path)
                path.pop()

    dfs(a, [a])
    return out, truncated


def must_pass_through(adj: Dict[str, List[str]], a: str, c: str,
                      via: Sequence[str], max_hops: int = 8,
                      max_paths: int = 1000) -> Dict:
    """审计侧语义：A→C **不许直连**；若存在 A→C 路径，则必须经过 `via` 中任一节点。

    * 只判定、不改图、不接门控、不进 `rank_v2`（V23-DESIGN-114）；
    * `vacuous`=图上不存在 A→C 路径（约束无对象），`pass`=所有路径都经过 via，
      `fail`=存在不经 via 的路径（含直连）。
    """
    paths, truncated = _paths_between(adj, a, c, max_hops, max_paths)
    viaset = set(via)
    through = [p for p in paths if any(n in viaset for n in p[1:-1])]
    violating = [p for p in paths if p not in through]
    direct = [p for p in paths if len(p) == 2]
    if not paths:
        verdict = "vacuous"
    elif violating:
        verdict = "fail"
    else:
        verdict = "pass"
    return {
        "from": a, "to": c, "must_pass_through": sorted(viaset),
        "verdict": verdict,
        "direct_edge": bool(direct),
        "n_paths": len(paths),
        "n_paths_through": len(through),
        "n_paths_violating": len(violating),
        "violating_paths": violating[:20],
        "truncated": bool(truncated),
        "max_hops": max_hops, "max_paths": max_paths,
        "note": ("A→C 不许直连；若存在路径则必须经过 via。只在审计侧判定，"
                 "不改图、不接门控、不进 rank_v2"),
    }


def _triad_demos(accident: str) -> List[Tuple[str, str, List[str]]]:
    """从 K3 `expected_chain` 取相邻三元组作演示用必经中转约束（A→B→C）。"""
    try:
        from causalrca.evidence.external_knowledge import load_fault_templates
        for t in load_fault_templates():
            if t.get("code") != accident:
                continue
            chain = list(t.get("expected_chain") or [])
            out = []
            for i in range(min(2, max(len(chain) - 2, 0))):
                out.append((chain[i], chain[i + 2], [chain[i + 1]]))
            return out
    except Exception:  # noqa: BLE001 — 演示层失败不得影响审计主产物
        return []
    return []


def audit_template(accident: str, group: str, variables: Sequence[str],
                   stable_edges: Sequence[Dict], forbidden_rules: Sequence[Dict],
                   policy: Dict, run_id: Optional[str] = None,
                   constraint_meta: Optional[Dict] = None) -> Dict:
    """生成单模板 path_audit 文档（不落盘）。"""
    pa = (policy or {}).get("path_audit") or {}
    max_hops = int(pa.get("max_hops", 8))
    max_paths = int(pa.get("max_paths", 1000))
    top_n = int(pa.get("top_n", 20))
    include_violating = bool(pa.get("include_all_violating", True))

    ks = _load_knowledge(accident)
    w = {(e["from"], e["to"]): float(e.get("weight_median", 0.0)) for e in stable_edges}
    adj: Dict[str, List[str]] = {}
    for e in stable_edges:
        adj.setdefault(e["from"], []).append(e["to"])
    fset = {(f["from"], f["to"]) for f in forbidden_rules}

    # ① 审计层只报“最终图里仍存在被禁边”这种真命中：扫描稳定边全集，不只被审计路径
    final_edges = {(e["from"], e["to"]) for e in stable_edges}
    present = [{"from": f["from"], "to": f["to"],
                "constraint_id": f.get("constraint_id"),
                "source_entry_id": f.get("source_entry_id")}
               for f in forbidden_rules if (f["from"], f["to"]) in final_edges]
    ledger_entries = list((constraint_meta or {}).get("ledger") or [])
    by_disp: Dict[str, int] = {}
    for ent in ledger_entries:
        by_disp[str(ent.get("disposition"))] = by_disp.get(str(ent.get("disposition")), 0) + 1
    rates = (constraint_meta or {}).get("removed_forbidden_rates") or {}
    applied = [{"from": k.split("->")[0], "to": k.split("->")[1],
                "hit_rate": v.get("rate"), "basis": v.get("basis")}
               for k, v in rates.items()]
    never_triggered = [{"from": f["from"], "to": f["to"],
                        "constraint_id": f.get("constraint_id")}
                       for f in forbidden_rules if f"{f['from']}->{f['to']}" not in rates]
    forbidden_audit = {
        "scope": "最终稳定边全集（不只被审计路径）；建图层已清零的边不应再出现",
        "n_stable_edges_scanned": len(stable_edges),
        "n_rules_in_effect": len(fset),
        "n_present_in_final_graph": len(present),
        "present_in_final_graph": present,
        "build_ledger_counts": by_disp,
        "build_ledger": ledger_entries,
        "applied_in_fits": {"n_edges": len(applied), "edges": applied},
        "never_triggered": {"n_edges": len(never_triggered), "edges": never_triggered},
        "verdict": "pass" if not present else "fail",
        "note": ("建图层逐条记账 in_effect/armed、exempted_competitive、dropped_no_endpoint；"
                 "拟合层记录 applied（实际清零）与 never_triggered（估图未出现该边）；"
                 "审计层只对最终图复查，命中即为建图层失效（真违规）"),
    }

    # ⑦ 有出处覆盖率：一等指标，与结论并列披露（不设门槛，只给比例与置信上限）
    n_sourced_edges = sum(
        1 for (u, v) in final_edges
        if (ks["edge"].get((u, v)) or {}).get("sourced")
        and (ks["edge"].get((u, v)) or {}).get("sign") in ("pos", "neg"))
    n_vars_sourced = sum(1 for v in variables if ks.get("dir_sourced", {}).get(v))
    ratio = round(n_sourced_edges / max(len(final_edges), 1), 4)
    provenance_coverage = {
        "n_stable_edges": len(final_edges),
        "n_edges_with_sourced_sign": n_sourced_edges,
        "edge_ratio": ratio,
        "n_variables": len(variables),
        "n_variables_with_sourced_entry": n_vars_sourced,
        "metric": "带边级出处的跳数 / 总跳数（边级）；不是条目级 sourced",
        "confidence_cap": (f"结论置信上限受此限制：本模板边级有出处覆盖仅 "
                           f"{n_sourced_edges}/{len(final_edges)} = {ratio:.1%}"),
    }

    # ⑥ 必经中转（纯后置、演示用）：A→C 不许直连，若存在路径必须经过 via。
    #    不接门控、不进 rank_v2；三元组取自 K3 expected_chain，仅证明能力可用。
    mpt: List[Dict] = []
    for a, c, via in _triad_demos(accident):
        if a not in variables or c not in variables:
            continue
        r = must_pass_through(adj, a, c, via, max_hops, max_paths)
        r.update({"demo_only": True, "not_gating": True,
                  "source": "K3 expected_chain 相邻三元组（A→B→C）"})
        mpt.append(r)
    must_pass_through_doc = {
        "enabled": True,
        "scope": "audit_only",
        "n_checks": len(mpt),
        "checks": mpt,
        "note": ("本轮只演示能力：不改图、不接门控、不进 rank_v2；"
                 "三元组来自 K3 链，未逐条做物理复核，不得当作新增约束"),
    }

    raw_paths, truncated = _enumerate(adj, list(variables), max_hops, max_paths)
    scored: List[Dict] = []
    for nodes in raw_paths:
        contrib = 1.0
        ok = True
        for a, b in zip(nodes, nodes[1:]):
            if (a, b) not in w:
                ok = False
                break
            contrib *= w[(a, b)]
        if not ok:
            continue
        hops = []
        for a, b in zip(nodes, nodes[1:]):
            fc, fr, fid, fkind = _forbidden_check(fset, ks, a, b)
            rc, rr = _role_check(ks, a, b)
            pc, pr, pid, vkind = _polarity_check(ks, a, b, w)
            statuses = [fc, rc, pc]
            if any(s == FORBID_DENY for s in statuses):
                ev = FORBID_DENY
            elif any(s == RECHECK for s in statuses):
                ev = RECHECK
            elif all(s in (PASS, SATISFIED, NA) for s in statuses) and pc == PASS:
                ev = PASS
            else:
                ev = UNSUP
            hops.append({"from": a, "to": b,
                         "forbidden_check": fc, "role_check": rc,
                         "polarity_check": pc, "evidence_status": ev,
                         "violation_kind": (fkind or vkind) if ev == FORBID_DENY else None,
                         "role_advisory": bool(rc == UNSUP),
                         # ④ 该跳是否有“带出处的边级 sign”（只看得分依据，不看得分结论）
                         "sourced_sign": bool(
                             (ks["edge"].get((a, b)) or {}).get("sourced")
                             and (ks["edge"].get((a, b)) or {}).get("sign") in ("pos", "neg")),
                         "direction_conflict": bool(
                             pc == FORBID_DENY and "方向之积" in (pr or "")),
                         "source_refs": [x for x in (fid, pid) if x],
                         "reason": "；".join(filter(None, [
                             None if fc == PASS else fr,
                             None if rc in (PASS, NA) else rr,
                             None if pc == PASS else pr])) or "逐跳有依据"})
        kinds = [h["violation_kind"] for h in hops if h["violation_kind"]]
        n_sup = sum(1 for h in hops if h["evidence_status"] == PASS)
        recheck_hops = [h for h in hops if h["evidence_status"] == RECHECK]
        n_sourced = sum(1 for h in hops if h["sourced_sign"])
        density = ("all" if n_sourced == len(hops)
                   else ("none" if n_sourced == 0 else "partial"))
        if kinds:
            # 现有违规全部为 hard（禁边真命中 / 有出处反向 / 有出处 sign 与有出处方向矛盾）
            result, vkind_path = "violating", VIOLATION_HARD
        elif n_sup == len(hops):
            result, vkind_path = "compliant", None
        else:
            result, vkind_path = "unsupported", None
        missing = next((i for i, h in enumerate(hops)
                        if h["evidence_status"] == UNSUP), None)
        viol = next((h for h in hops if h["evidence_status"] == FORBID_DENY), None)
        scored.append({
            "nodes": nodes, "length": len(nodes) - 1,
            "contribution": contrib, "abs_contribution": abs(contrib),
            "result": result, "violation_kind": vkind_path, "hop_results": hops,
            "n_hops": len(hops), "n_hops_supported": n_sup,
            "evidence_coverage": round(n_sup / max(len(hops), 1), 4),
            # ④ 证据密度：带边级出处的跳数 / 总跳数（分类分档，不设数值门槛）
            "n_hops_sourced": n_sourced,
            "evidence_density": density,
            "needs_recheck": bool(recheck_hops),
            "n_hops_recheck": len(recheck_hops),
            "recheck_reason": (recheck_hops[0]["reason"] if recheck_hops else None),
            "direction_conflict_hops": sum(1 for h in hops if h["direction_conflict"]),
            "missing_hop_index": missing,
            "missing_reason": (hops[missing]["reason"] if missing is not None else None),
            "violation_type": (viol["reason"] if viol else None),
            "constraint_id": (next((h["source_refs"][0] for h in hops
                                    if h["evidence_status"] == FORBID_DENY
                                    and h["source_refs"]), None)),
            "source_entry_id": (viol["source_refs"][0] if viol and viol["source_refs"] else None),
        })

    scored.sort(key=lambda p: (-p["abs_contribution"], p["length"], p["nodes"]))
    counts_all = _empty_counts()
    kinds_all = {VIOLATION_HARD: 0, VIOLATION_SOFT: 0}
    n_recheck_paths_all = 0
    n_recheck_hops_all = 0
    n_role_advisory_hops_all = 0
    for p in scored:
        counts_all[p["result"]] += 1
        if p["result"] == "violating" and p["violation_kind"]:
            kinds_all[p["violation_kind"]] += 1
        if p["needs_recheck"]:
            n_recheck_paths_all += 1
        n_recheck_hops_all += p["n_hops_recheck"]
        n_role_advisory_hops_all += sum(1 for h in p["hop_results"] if h["role_advisory"])

    # ⑤ 按贡献挑路径：先按 |贡献| 降序，累计覆盖到 target 为止，再并上全部踩到禁边的路径。
    #    target 是“覆盖目标”，不是“跳数门槛”；全枚举计数另存 counts_all_scored 作对照。
    target = float(pa.get("contribution_coverage", 0.9))
    total_abs = sum(p["abs_contribution"] for p in scored)
    selected: List[Dict] = []
    cum = 0.0
    for p in scored:
        selected.append(p)
        cum += p["abs_contribution"]
        if total_abs > 0 and cum / total_abs >= target:
            break
    n_by_target = len(selected)
    coverage_ratio = round(cum / total_abs, 4) if total_abs > 0 else 0.0

    n_extra = 0
    if include_violating:
        seen = {id(p) for p in selected}
        for p in scored:
            if p["result"] == "violating" and id(p) not in seen:
                selected.append(p)
                seen.add(id(p))
                n_extra += 1
    for i, p in enumerate(selected, 1):
        p["path_id"] = f"P{i}"
        p["contribution_rank"] = i

    counts = _empty_counts()
    n_sel_recheck_paths = 0
    n_sel_recheck_hops = 0
    density_hist: Dict[str, int] = {}
    for p in selected:
        counts[p["result"]] += 1
        if p["needs_recheck"]:
            n_sel_recheck_paths += 1
        n_sel_recheck_hops += p["n_hops_recheck"]
        density_hist[p["evidence_density"]] = density_hist.get(p["evidence_density"], 0) + 1
    hist: Dict[str, int] = {}
    for p in selected:
        hist[str(p["length"])] = hist.get(str(p["length"]), 0) + 1

    # ⑤ 保留 top_n 样本计数，供旧口径对照（不改其定义）
    top = scored[:top_n]
    counts_top = _empty_counts()
    n_recheck_paths_top = 0
    n_recheck_hops_top = 0
    density_top: Dict[str, int] = {}
    for p in top:
        counts_top[p["result"]] += 1
        if p["needs_recheck"]:
            n_recheck_paths_top += 1
        n_recheck_hops_top += p["n_hops_recheck"]
        density_top[p["evidence_density"]] = density_top.get(p["evidence_density"], 0) + 1

    selection = {
        "mode": "contribution_cumulative_plus_forbidden",
        "target_coverage": target,
        "coverage_ratio": coverage_ratio,
        "n_paths_scored": len(scored),
        "n_selected_by_target": n_by_target,
        "n_selected": len(selected),
        "n_selected_forbidden_extra": n_extra,
        "truncated": bool(truncated),
        "enumeration_truncated": bool(truncated),
        "note": ("按 |贡献| 降序累加到覆盖目标所需的最少路径数，再并上全部踩到禁边的路径；"
                 "被审路径数 < 全枚举数时，counts 只代表被审集合，对照看 counts_all_scored"),
    }

    return {
        "template": f"{accident}_{group}",
        "group": group,
        "run_id": run_id,
        "schema": "causaltrace/path_audit/v4",
        "audit_revision": "V23-DESIGN-112(第一批①②③) + V23-DESIGN-113(第二批④⑤⑦)",
        "paths": selected,
        "counts": counts,
        "counts_all_scored": counts_all,
        "counts_all_scored_kind": kinds_all,
        "counts_top": counts_top,
        "counts_top_kind": {VIOLATION_HARD: sum(
            1 for p in top if p["result"] == "violating")},
        "counts_violating_extra": _empty_counts(),
        # ② 待复核信号：不进三档违规分母，单列
        "counts_recheck": {
            "all_scored": {"n_paths": n_recheck_paths_all, "n_hops": n_recheck_hops_all},
            "selected": {"n_paths": n_sel_recheck_paths, "n_hops": n_sel_recheck_hops},
            "top": {"n_paths": n_recheck_paths_top, "n_hops": n_recheck_hops_top},
            "n_role_advisory_hops_all": n_role_advisory_hops_all,
            "note": ("待复核信号=有冲突但至少一侧无出处（或数据符号 vs 方向推断），"
                     "不构成违规、不进 violating 分母，原文理由保留在 paths[].recheck_reason"),
        },
        # ④ 证据密度分档（分类，无数值门槛）：all / partial / none
        "evidence_density": {
            "selected": density_hist,
            "top": density_top,
            "definition": ("带边级出处的跳数 / 总跳数：all=每跳都有带出处边级 sign；"
                           "none=一条都没有；partial=两者之间。分档只用于披露，不设阈值"),
        },
        # ⑦ 一等指标：有出处覆盖率与置信上限
        "provenance_coverage": provenance_coverage,
        # ⑥ 必经中转（纯后置演示，不接门控、不进 rank_v2）
        "must_pass_through": must_pass_through_doc,
        # ⑤ 挑选口径与覆盖比例
        "selection": selection,
        "forbidden_audit": forbidden_audit,
        "audit_sample": {
            "n_scored": len(scored),
            "n_top": min(top_n, len(scored)),
            "n_selected": len(selected),
            "n_selected_by_target": n_by_target,
            "n_selected_forbidden_extra": n_extra,
            "n_violating_extra": n_extra,
            "n_recorded": len(selected),
            "top_n": top_n,
            "coverage_target": target,
            "coverage_ratio": coverage_ratio,
        },
        "role_map": dict(ROLE_MAP),
        "role_axioms": sorted(ROLE_AXIOMS),
        "role_policy": {
            "advisory_only": True,
            "note": "6→4 角色映射为草案、无专家签字：只作 unsupported 的理由，不单独判违反",
        },
        "polarity_policy": {
            "edge_sign_requires_sourced": True,
            "direction_consistency": "contradiction_only",
            "hard": "带出处边级 sign 与两端均有出处的方向之积矛盾（真违规）",
            "needs_recheck": "单侧无出处、或学到的边符号与方向推断不一致（待复核，不判违规）",
            "soft": "已废止（V23-DESIGN-112 起改判 needs_recheck）",
        },
        "n_paths_enumerated": len(raw_paths),
        "truncated": bool(truncated),
        "max_paths": max_paths,
        "max_hops": max_hops,
        "path_length_hist": dict(sorted(hist.items(), key=lambda x: int(x[0]))),
        "enumeration_method": "simple_paths_dfs_v1",
        "note": ("多跳是默认：逐跳判定，路径长度只记录，不作为约束参数；"
                 "unsupported=未违反但无带出处边级依据，不可当知识支持；"
                 "violating 只保留 hard（最终图仍有禁边 / 有出处反向断言 / 带出处 sign 与"
                 "有出处方向矛盾）；原 soft（学到符号 vs 方向）改判 needs_recheck，不进违规分母；"
                 "角色映射降级为 unsupported 理由；禁边在审计层只复扫最终稳定边全集。"
                 "paths/counts 是“按贡献累计覆盖 target + 全部禁边路径”的被审集合，"
                 "counts_all_scored 是全枚举无偏分布（对照用），counts_top 是贡献最大 top_n 样本；"
                 "三者不可混算。证据密度分档与有出处覆盖率只作披露，不设数值门槛"),
    }

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

# 角色公理：只约束“仪表不得作为上游物理原因”，其余组合不设整链顺序要求
ROLE_AXIOMS = {"measurement_terminal"}

FORBID_DENY = "violating"
PASS = "supported"
UNSUP = "unsupported"
NA = "not_applicable"
SATISFIED = "satisfied"

VIOLATION_HARD = "hard"
VIOLATION_SOFT = "soft"

STATUS_ORDER = ("compliant", "unsupported", "violating")


def _load_knowledge(accident: str) -> Dict:
    """读两文件，构造角色/方向/边级 sign/反向禁令索引。"""
    import yaml
    from causalrca.evidence.constraints import KNOW_DIR, FILES
    from causalrca.evidence.knowledge import COMPETITIVE_PAIRS, COMPETITIVE_PAIRS_BY_CLASS
    role, raw_role, direction, edge_sign = {}, {}, {}, {}
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
            "edge": edge_sign, "comp": comp}


def _role_check(ks: Dict, a: str, b: str) -> Tuple[str, str]:
    """角色公理检查：只判“仪表作上游”，其余组合 not_applicable。"""
    ra, rb = ks["role"].get(a), ks["role"].get(b)
    if ra is None or rb is None:
        return NA, "角色未登记（无约束可判）"
    if ra == "measurement" and rb != "measurement":
        return FORBID_DENY, f"measurement→{rb}（仪表不得作为物理量上游）"
    return NA, f"{ra}→{rb} 无角色顺序约束"


def _implied_sign(dir_a: Optional[str], dir_b: Optional[str]) -> Optional[str]:
    """两条变量级方向已知且为二元时，边符号应为二者之积。"""
    if dir_a in ("pos", "neg") and dir_b in ("pos", "neg"):
        return "pos" if dir_a == dir_b else "neg"
    return None


def _polarity_check(ks: Dict, u: str, v: str,
                    weights: Dict[Tuple[str, str], float]
                    ) -> Tuple[str, str, Optional[str], Optional[str]]:
    """极性：硬证据（带出处 sign）/ 无支撑 / 矛盾（硬或软）。"""
    dir_u, dir_v = ks["direction"].get(u), ks["direction"].get(v)
    implied = _implied_sign(dir_u, dir_v)
    e = ks["edge"].get((u, v))
    if e:
        sign = e.get("sign")
        if not e.get("sourced"):
            return UNSUP, "边级 sign 未注明出处（不得当证据）", e.get("entry_id"), None
        if sign not in ("pos", "neg"):
            return UNSUP, f"边级 sign={sign!r} 非方向性，不能判极性", e.get("entry_id"), None
        if implied is not None and sign != implied:
            return (FORBID_DENY,
                    f"知识自带 sign={sign} 与两端方向之积={implied} 矛盾",
                    e.get("entry_id"), VIOLATION_HARD)
        return PASS, f"带出处边级 sign={sign}", e.get("entry_id"), None
    # 无条目边级断言：只做“方向自洽”矛盾检测，不当作支持
    if implied is None:
        return UNSUP, "该跳无带出处边级极性，且两端方向不完整，无法校验", None, None
    w = weights.get((u, v))
    if w is None or abs(w) <= 0.0:
        return UNSUP, "该跳学到的边权重为 0，符号无意义", None, None
    learned = "pos" if w > 0 else "neg"
    if learned != implied:
        return (FORBID_DENY,
                f"学到的边符号={learned} 与两端方向之积={implied} 矛盾",
                None, VIOLATION_SOFT)
    return UNSUP, "方向自洽但无带出处边级极性（不算支持）", None, None


def _forbidden_check(fset: Set[Tuple[str, str]], ks: Dict,
                     u: str, v: str) -> Tuple[str, str, Optional[str]]:
    if (u, v) in fset:
        return FORBID_DENY, f"{u}→{v} 命中硬禁边", None
    rev = ks["edge"].get((v, u))
    if rev and rev.get("reverse_forbidden") and (u, v) not in ks.get("comp", set()):
        return FORBID_DENY, f"{u}→{v} 是断言 {v}→{u} 的反向（reverse_forbidden）", rev.get("entry_id")
    return PASS, "未命中禁令", None


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


def audit_template(accident: str, group: str, variables: Sequence[str],
                   stable_edges: Sequence[Dict], forbidden_rules: Sequence[Dict],
                   policy: Dict, run_id: Optional[str] = None) -> Dict:
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
            fc, fr, fid = _forbidden_check(fset, ks, a, b)
            rc, rr = _role_check(ks, a, b)
            pc, pr, pid, vkind = _polarity_check(ks, a, b, w)
            statuses = [fc, rc, pc]
            if FORBID_DENY in statuses:
                ev = FORBID_DENY
            elif all(s in (PASS, SATISFIED, NA) for s in statuses) and pc == PASS:
                ev = PASS
            else:
                ev = UNSUP
            hops.append({"from": a, "to": b,
                         "forbidden_check": fc, "role_check": rc,
                         "polarity_check": pc, "evidence_status": ev,
                         "violation_kind": (vkind if FORBID_DENY in statuses else None),
                         "direction_conflict": bool(
                             pc == FORBID_DENY and "方向之积" in (pr or "")),
                         "source_refs": [x for x in (fid, pid) if x],
                         "reason": "；".join(filter(None, [
                             None if fc == PASS else fr,
                             None if rc in (PASS, NA) else rr,
                             None if pc == PASS else pr])) or "逐跳有依据"})
        kinds = [h["violation_kind"] for h in hops if h["violation_kind"]]
        n_sup = sum(1 for h in hops if h["evidence_status"] == PASS)
        if VIOLATION_HARD in kinds:
            result, vkind_path = "violating", VIOLATION_HARD
        elif VIOLATION_SOFT in kinds:
            result, vkind_path = "violating", VIOLATION_SOFT
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
    for p in scored:
        counts_all[p["result"]] += 1
        if p["result"] == "violating" and p["violation_kind"]:
            kinds_all[p["violation_kind"]] += 1

    top = scored[:top_n]
    counts_top = _empty_counts()
    kinds_top = {VIOLATION_HARD: 0, VIOLATION_SOFT: 0}
    for p in top:
        counts_top[p["result"]] += 1
        if p["result"] == "violating" and p["violation_kind"]:
            kinds_top[p["violation_kind"]] += 1

    n_extra = 0
    if include_violating:
        seen = {tuple(p["nodes"]) for p in top}
        for p in scored:
            if p["result"] == "violating" and tuple(p["nodes"]) not in seen:
                top.append(p)
                seen.add(tuple(p["nodes"]))
                n_extra += 1
    for i, p in enumerate(top, 1):
        p["path_id"] = f"P{i}"
        p["contribution_rank"] = i

    counts = _empty_counts()
    for p in top:
        counts[p["result"]] += 1
    hist: Dict[str, int] = {}
    for p in top:
        hist[str(p["length"])] = hist.get(str(p["length"]), 0) + 1

    return {
        "template": f"{accident}_{group}",
        "group": group,
        "run_id": run_id,
        "schema": "causaltrace/path_audit/v2",
        "paths": top,
        "counts": counts,
        "counts_all_scored": counts_all,
        "counts_all_scored_kind": kinds_all,
        "counts_top": counts_top,
        "counts_top_kind": kinds_top,
        "counts_violating_extra": _empty_counts(),
        "audit_sample": {
            "n_scored": len(scored),
            "n_top": min(top_n, len(scored)),
            "n_violating_extra": n_extra,
            "n_recorded": len(top),
            "top_n": top_n,
        },
        "role_map": dict(ROLE_MAP),
        "role_axioms": sorted(ROLE_AXIOMS),
        "polarity_policy": {
            "edge_sign_requires_sourced": True,
            "direction_consistency": "contradiction_only",
            "hard": "带出处 sign 与两端方向之积矛盾",
            "soft": "学到的边符号与两端方向之积矛盾",
        },
        "n_paths_enumerated": len(raw_paths),
        "truncated": bool(truncated),
        "max_paths": max_paths,
        "max_hops": max_hops,
        "path_length_hist": dict(sorted(hist.items(), key=lambda x: int(x[0]))),
        "enumeration_method": "simple_paths_dfs_v1",
        "note": ("多跳是默认：逐跳判定，路径长度只记录，不作为约束参数；"
                 "unsupported=未违反但无带出处边级依据，不可当知识支持；"
                 "violating 分 hard（禁令/仪表作上游/知识自带 sign 与方向矛盾）与 "
                 "soft（学到的边符号与方向矛盾）；违规只报冲突，不回改图。"
                 "counts_all_scored 是全枚举无偏分布，counts_top 是贡献最大样本，"
                 "counts_violating_extra 为追加的违规路径，三者不可混算"),
    }

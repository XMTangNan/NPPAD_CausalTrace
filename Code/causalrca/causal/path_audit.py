"""多跳后置审计（方案 §4.2.7）：从 T 的支持集抽贡献最大路径 + 全部违规路径，逐跳判定。

原则：
  * 只读图，不回改 `B`/`T`/`rank`；违规只报冲突；
  * 路径长度只记录（`max_hops` 是计算上限，不是物理跳数约束）；
  * 逐跳三项：禁令 / 角色转移 / 极性；缺依据标 `unsupported`，不冒充“知识支持”。
"""
from __future__ import annotations

import itertools
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

# 原始 6 种角色 → 4 类规范角色（草案，需专家确认；方案 §4.2.7）
ROLE_MAP = {
    "injection": "driver",
    "response_control": "driver",
    "driver": "driver",
    "response_conduction": "response",
    "response": "response",
    "response_state": "consequence",
    "response_indicator": "observation",
}

# 允许的角色转移（A→B）
ALLOWED_TRANSITIONS = {
    ("driver", "response"),
    ("driver", "consequence"),
    ("response", "response"),
    ("response", "consequence"),
    ("response", "observation"),
}

FORBID_DENY = "violating"
PASS = "supported"
UNSUP = "unsupported"


def _load_knowledge(accident: str) -> Dict:
    """读两文件，构造角色/方向/边级 sign/反向禁令索引。"""
    import yaml
    from causalrca.evidence.constraints import KNOW_DIR, FILES
    from causalrca.evidence.knowledge import COMPETITIVE_PAIRS, COMPETITIVE_PAIRS_BY_CLASS
    role, direction, edge_sign = {}, {}, {}
    for name in FILES:
        doc = yaml.safe_load((KNOW_DIR / name).read_text(encoding="utf-8")) or {}
        for ent in doc.get("entries") or []:
            if accident not in (ent.get("applicable_accidents") or []):
                continue
            var = ent.get("variable")
            raw_role = ent.get("role_in_accident")
            role[var] = ROLE_MAP.get(raw_role)
            direction[var] = ent.get("expected_direction")
            for e in ent.get("asserted_edges") or []:
                if not isinstance(e, dict):
                    continue
                key = (e.get("from"), e.get("to"))
                edge_sign[key] = {
                    "sign": e.get("sign"),
                    "reverse_forbidden": bool(e.get("reverse_forbidden")),
                    "sourced": e.get("sourced"),
                    "entry_id": ent.get("id"),
                    "expected_direction": ent.get("expected_direction"),
                    "raw_role": raw_role,
                }
    # 与约束层同口径：competitive 双向对豁免反向禁令（否则会把已被豁免的边误判违规）
    comp: Set[Tuple[str, str]] = set()
    for a, b in COMPETITIVE_PAIRS_BY_CLASS.get(accident, COMPETITIVE_PAIRS):
        comp.add((a, b))
        comp.add((b, a))
    return {"role": role, "direction": direction, "edge": edge_sign, "comp": comp}


def _role_check(a: Optional[str], b: Optional[str]) -> Tuple[str, str]:
    if a is None or b is None:
        return UNSUP, "角色缺失或映射不确定"
    if (a, b) in ALLOWED_TRANSITIONS:
        return PASS, f"{a}→{b} 允许"
    return FORBID_DENY, f"{a}→{b} 不允许（反向驱动/观测或后果作上游）"


def _polarity_check(ks: Dict, u: str, v: str) -> Tuple[str, str, Optional[str]]:
    e = ks["edge"].get((u, v))
    if not e:
        return UNSUP, "无该跳边级 sign（变量级方向不能替代边级极性）", None
    sign, exp = e.get("sign"), e.get("expected_direction")
    if sign in ("pos", "neg") and exp in ("pos", "neg") and sign != exp:
        return FORBID_DENY, f"边级 sign={sign} 与条目 expected_direction={exp} 冲突", e.get("entry_id")
    if sign is None:
        return UNSUP, "边级 sign 缺失", e.get("entry_id")
    return PASS, f"边级 sign={sign}", e.get("entry_id")


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
            rc, rr = _role_check(ks["role"].get(a), ks["role"].get(b))
            pc, pr, pid = _polarity_check(ks, a, b)
            statuses = [fc, rc, pc]
            if FORBID_DENY in statuses:
                ev = FORBID_DENY
            elif all(s == PASS for s in statuses):
                ev = PASS
            else:
                ev = UNSUP
            hops.append({"from": a, "to": b,
                         "forbidden_check": fc, "role_check": rc,
                         "polarity_check": pc, "evidence_status": ev,
                         "source_refs": [x for x in (fid, pid) if x],
                         "reason": "；".join(filter(None, [
                             None if fc == PASS else fr,
                             None if rc == PASS else rr,
                             None if pc == PASS else pr])) or "逐跳有依据"})
        if FORBID_DENY in [h["evidence_status"] for h in hops]:
            result = "violating"
        elif all(h["evidence_status"] == PASS for h in hops):
            result = "compliant"
        else:
            result = "unsupported"
        missing = next((i for i, h in enumerate(hops)
                        if h["evidence_status"] == UNSUP), None)
        viol = next((h for h in hops if h["evidence_status"] == FORBID_DENY), None)
        scored.append({
            "nodes": nodes, "length": len(nodes) - 1,
            "contribution": contrib, "abs_contribution": abs(contrib),
            "result": result, "hop_results": hops,
            "missing_hop_index": missing,
            "missing_reason": (hops[missing]["reason"] if missing is not None else None),
            "violation_type": (viol["reason"] if viol else None),
            "constraint_id": (next((h["source_refs"][0] for h in hops
                                    if h["evidence_status"] == FORBID_DENY
                                    and h["source_refs"]), None)),
            "source_entry_id": (viol["source_refs"][0] if viol and viol["source_refs"] else None),
        })

    scored.sort(key=lambda p: (-p["abs_contribution"], p["length"], p["nodes"]))
    top = scored[:top_n]
    if include_violating:
        seen = {tuple(p["nodes"]) for p in top}
        for p in scored:
            if p["result"] == "violating" and tuple(p["nodes"]) not in seen:
                top.append(p)
                seen.add(tuple(p["nodes"]))
    for i, p in enumerate(top, 1):
        p["path_id"] = f"P{i}"
        p["contribution_rank"] = i

    counts = {"compliant": 0, "unsupported": 0, "violating": 0}
    for p in top:
        counts[p["result"]] += 1
    hist: Dict[str, int] = {}
    for p in top:
        hist[str(p["length"])] = hist.get(str(p["length"]), 0) + 1

    return {
        "template": f"{accident}_{group}",
        "group": group,
        "run_id": run_id,
        "schema": "causaltrace/path_audit/v1",
        "paths": top,
        "counts": counts,
        "n_paths_enumerated": len(raw_paths),
        "truncated": bool(truncated),
        "max_paths": max_paths,
        "max_hops": max_hops,
        "path_length_hist": dict(sorted(hist.items(), key=lambda x: int(x[0]))),
        "enumeration_method": "simple_paths_dfs_v1",
        "note": ("多跳是默认：逐跳判定，路径长度只记录，不作为约束参数；"
                 "unsupported=未违反但无依据，不可当知识支持；违规只报冲突，不回改图"),
    }

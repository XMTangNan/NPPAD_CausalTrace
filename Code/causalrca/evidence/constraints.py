"""外部知识 → 硬禁边（constraints_v1）：主链唯一注入通道（方案 §4.2.3）。

纪律（预注册）：
  * 只读 `Code/external_knowledge/{positive,supplementary}_mechanisms.yaml`；
  * 只有两类条目进约束层：
      R-a `sourced: true` 且边级 `sourced: true` 且 `reverse_forbidden: true`
          → 禁 (to -> from)；
      R-b `sourced: true` 且 `forbidden_outgoing.sourced: true`
          → 禁 (variable -> targets[*])；
  * 与按类 competitive 对（双向反馈）冲突的边跳过；
  * `hard_forbid_path` 本轮**预注册为空**：路径级禁令没有可靠依据，且
    `prior_knowledge=0` 会先定序再删边（E13），故不使用；
  * 产物 `constraints_v1.yaml` + `.sha256` 冻结；缺失/哈希不符即中止。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Set, Tuple

KNOW_DIR = Path(__file__).resolve().parents[2] / "external_knowledge"
FILES = ("positive_mechanisms.yaml", "supplementary_mechanisms.yaml")
CONSTRAINTS_PATH = KNOW_DIR / "constraints_v1.yaml"
CONSTRAINTS_SHA_PATH = KNOW_DIR / "constraints_v1.sha256"
CONSTRAINTS_VERSION = "constraints_v1"
ACCIDENTS = ("LOCA", "SGATR", "FLB")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()


def file_hashes() -> Dict[str, str]:
    return {name: sha256_file(KNOW_DIR / name) for name in FILES}


def _entries() -> List[Dict]:
    import yaml
    out: List[Dict] = []
    for name in FILES:
        doc = yaml.safe_load((KNOW_DIR / name).read_text(encoding="utf-8")) or {}
        for ent in doc.get("entries") or []:
            out.append({**ent, "_file": name})
    return out


def _competitive(accident: str) -> Set[Tuple[str, str]]:
    from causalrca.evidence.knowledge import COMPETITIVE_PAIRS, COMPETITIVE_PAIRS_BY_CLASS
    pairs = COMPETITIVE_PAIRS_BY_CLASS.get(accident, COMPETITIVE_PAIRS)
    out: Set[Tuple[str, str]] = set()
    for a, b in pairs:
        out.add((a, b))
        out.add((b, a))
    return out


def derive_rules(accident: str) -> List[Dict]:
    """该事故类型下由外部知识推出的禁单边（去重、排除 competitive）。"""
    comp = _competitive(accident)
    rules: List[Dict] = []
    seen: Set[Tuple[str, str]] = set()

    def _add(src, dst, basis, origin, entry_id):
        if not src or not dst or src == dst:
            return
        if (src, dst) in comp or (src, dst) in seen:
            return
        seen.add((src, dst))
        rules.append({"from": src, "to": dst, "basis": basis, "origin": origin,
                      "constraint_id": f"constraints_v1:{entry_id}:{src}->{dst}",
                      "source_entry_id": entry_id})

    for ent in _entries():
        if ent.get("sourced") is not True:
            continue
        if accident not in (ent.get("applicable_accidents") or []):
            continue
        eid = ent.get("id")
        for e in ent.get("asserted_edges") or []:
            if not isinstance(e, dict):
                continue
            if not (e.get("sourced") is True and e.get("reverse_forbidden") is True):
                continue
            _add(e.get("to"), e.get("from"),
                 f"外部知识 {eid}：断言 {e.get('from')}→{e.get('to')} 单向，禁其反向",
                 "hard_forbid_edge", eid)
        fo = ent.get("forbidden_outgoing") or {}
        if isinstance(fo, dict) and fo.get("sourced") is True:
            for tgt in fo.get("targets") or []:
                _add(ent.get("variable"), tgt,
                     f"外部知识 {eid}：{ent.get('variable')} 为响应/指示量，不得驱动 {tgt}",
                     "hard_forbid_edge", eid)
    return rules


def build_frozen_doc() -> Dict:
    """生成 constraints_v1 文档（含知识源文件哈希与已知冲突）。"""
    from causalrca.evidence.knowledge import ALLOWED_RELATIONS
    from causalrca.evidence.external_knowledge import load_fault_templates

    rules = {k: derive_rules(k) for k in ACCIDENTS}
    conflicts = []
    for k, rs in rules.items():
        rset = {(r["from"], r["to"]) for r in rs}
        chain = list(load_fault_templates(k)["expected_chain"])
        chain_edges = set(zip(chain, chain[1:]))
        allowed = {(r["from"], r["to"]) for r in ALLOWED_RELATIONS.get(k, [])}
        for e in sorted(rset & chain_edges):
            conflicts.append({"accident": k, "edge": list(e),
                              "against": "K3_expected_chain"})
        for e in sorted(rset & allowed):
            conflicts.append({"accident": k, "edge": list(e),
                              "against": "ALLOWED_RELATIONS"})
    entries = _entries()
    return {
        "schema": "causaltrace/constraints/v1",
        "version": CONSTRAINTS_VERSION,
        "frozen_at": "2026-11-14",
        "frozen_by": "agent (V23-CODE-046)",
        "source": "external_knowledge 两文件 → 硬禁边；AI 代审、非专家确认",
        "source_files_sha256": file_hashes(),
        "n_entries": len(entries),
        "n_sourced_entries": sum(1 for e in entries if e.get("sourced") is True),
        "injection_rule": ("只注入 sourced=true 的 reverse_forbidden 反向与 "
                           "forbidden_outgoing；unsourced 不进制约层"),
        "hard_forbid_edge": rules,
        "hard_forbid_path": [],
        "prior_knowledge": {
            "mode": "bidirectional_only",
            "entries": [],
            "note": "本轮不使用 prior_knowledge=0（会翻序造边，E13）；预注册为空",
        },
        "known_conflicts": conflicts,
        "n_rules": {k: len(v) for k, v in rules.items()},
        "citable": False,
        "note": "AI 代审、非专家确认；真人专家可逐条推翻",
    }


def write_constraints() -> Path:
    """生成并冻结 constraints_v1.yaml；返回路径。"""
    import yaml
    doc = build_frozen_doc()
    text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=120)
    CONSTRAINTS_PATH.write_text(text, encoding="utf-8")
    CONSTRAINTS_SHA_PATH.write_text(sha256_file(CONSTRAINTS_PATH) + "\n",
                                    encoding="utf-8")
    return CONSTRAINTS_PATH


def load_constraints(verify: bool = True) -> Dict | None:
    """读取并校验冻结约束；文件缺失返回 None，哈希不符抛 RuntimeError。"""
    import yaml
    if not CONSTRAINTS_PATH.is_file():
        return None
    if verify:
        if not CONSTRAINTS_SHA_PATH.is_file():
            raise RuntimeError(f"[constraints] 缺冻结哈希文件：{CONSTRAINTS_SHA_PATH}")
        expected = CONSTRAINTS_SHA_PATH.read_text(encoding="utf-8").strip()
        actual = sha256_file(CONSTRAINTS_PATH)
        if actual != expected:
            raise RuntimeError(
                f"[constraints] constraints_v1.yaml 哈希不符：actual={actual} "
                f"expected={expected}（冻结件，跑前不得改）")
    doc = yaml.safe_load(CONSTRAINTS_PATH.read_text(encoding="utf-8")) or {}
    if doc.get("version") != CONSTRAINTS_VERSION:
        raise RuntimeError(f"[constraints] 版本不符：{doc.get('version')!r}")
    return doc


def constraints_for(accident: str) -> List[Dict]:
    """取该类事故的硬禁边；文件缺失时返回 []（由编排器负责中止）。"""
    doc = load_constraints()
    if not doc:
        return []
    return list((doc.get("hard_forbid_edge") or {}).get(accident) or [])

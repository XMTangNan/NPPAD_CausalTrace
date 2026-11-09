"""V2.3 D3：外部核物理知识层加载、校验与合并（方案 §5.2 / §5.3）。

知识分层（§5.2.1）：
  L1 NPPAD README → variable_dictionary.yaml（96 变量字典）
  L2 核电工程手册 → physics_relations.yaml（K2 五类近似关系）
  L3 FMEA/故障树  → fault_templates.yaml（K3 12 类机制模板）
  L4 专家公差     → tolerance_overrides.yaml（σ_floor 逐变量物理下限）
  K1 禁止边补充   → forbidden_edges.yaml

知识纪律（§5.2.2）：外部知识只扩展、不覆盖代码本体；允许边只标记不强制添加；
冲突时以代码本体为准，外部知识记入报告（不静默丢弃）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from causalrca.config.paths import CODE_ROOT
from rca.v21.knowledge import ENTITY_TABLE

# Code/external_knowledge：锚点取自新包常量，本文件搬迁后无需再数 parents 层级
EXT_KNOWLEDGE_DIR: Path = CODE_ROOT / "external_knowledge"

YAML_FILES = ("variable_dictionary", "physics_relations", "fault_templates",
              "forbidden_edges", "tolerance_overrides")

REQUIRED_VAR_FIELDS = ("subsystem", "meaning", "unit")
REQUIRED_TEMPLATE_FIELDS = ("code", "name_cn", "trigger", "expected_chain",
                            "expected_sign_direction", "key_symptoms",
                            "applicable_group", "source", "verification")
REQUIRED_RELATION_FIELDS = ("id", "name", "lhs", "rhs", "rhs_operator",
                            "expected_sign", "applicable_accidents", "missing_terms")
DEFAULT_FALLBACK_RATIO = 0.01


def load_yaml(name: str) -> Dict[str, Any]:
    """加载 external_knowledge 下的 YAML（name 可带或不带 .yaml 后缀）。"""
    fname = name if name.endswith((".yaml", ".yml")) else f"{name}.yaml"
    path = EXT_KNOWLEDGE_DIR / fname
    if not path.is_file():
        raise FileNotFoundError(f"外部知识文件缺失: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 语法错误（{fname}）: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"YAML 顶层不是映射（{fname}）")
    return data


def load_variable_dictionary() -> Dict[str, Any]:
    """返回变量字典 YAML 全文（source / note / n_variables / variables）。"""
    return load_yaml("variable_dictionary")


def load_physics_relations() -> List[Dict[str, Any]]:
    """返回 K2 五类近似物理关系（字段与 physics_constraints.PHYSICS_RELATIONS 一致）。"""
    return list(load_yaml("physics_relations").get("relations", []))


def load_fault_templates(code: Optional[str] = None):
    """返回 K3 故障模板：code 给定返回单个模板(dict)，否则返回全部(list)。"""
    templates = list(load_yaml("fault_templates").get("templates", []))
    if code is None:
        return templates
    for tpl in templates:
        if tpl.get("code") == code:
            return tpl
    raise KeyError(f"fault_templates 中无事故代码 {code}")


def load_forbidden_rules() -> Dict[str, Any]:
    """返回 K1 禁止边规则全文（markers / safety_step_vars / rules / exceptions）。"""
    return load_yaml("forbidden_edges")


def load_tolerance_overrides() -> Dict[str, Any]:
    """返回 σ_floor 配置 {"by_unit": {...}, "by_variable": {...}, "fallback": {...}}。"""
    doc = load_yaml("tolerance_overrides")
    return {"by_unit": dict(doc.get("by_unit") or {}),
            "by_variable": dict(doc.get("by_variable") or {}),
            "fallback": dict(doc.get("fallback") or {})}


def validate_against_entity_table(et: Dict[str, Dict[str, str]] | None = None) -> Dict[str, Any]:
    """核对 5 个 YAML 与代码实体表的一致性。

    检查项：变量字典未知变量名 / 缺字段 / 条目数与声明不符；故障模板缺字段、未知变量、
    重复 code；物理关系缺字段、重复 id。返回 {"ok": bool, "errors": [...], "warnings": [...]}。
    """
    et = ENTITY_TABLE if et is None else et
    errors: List[str] = []
    warnings: List[str] = []

    # ---- L1 变量字典 ----
    vdoc = load_variable_dictionary()
    var_map = vdoc.get("variables") or {}
    if not isinstance(var_map, dict) or not var_map:
        errors.append("variable_dictionary.variables 缺失或非映射")
        var_map = {}
    for name, meta in var_map.items():
        if name not in et:
            errors.append(f"variable_dictionary: 未知变量 {name}（不在 ENTITY_TABLE）")
            continue
        if not isinstance(meta, dict):
            errors.append(f"variable_dictionary: {name} 条目不是映射")
            continue
        missing = [f for f in REQUIRED_VAR_FIELDS if not meta.get(f)]
        if missing:
            errors.append(f"variable_dictionary: {name} 缺字段 {missing}")
            continue
        for f in REQUIRED_VAR_FIELDS:
            if str(meta[f]) != str(et[name][f]):
                warnings.append(f"variable_dictionary: {name}.{f} 与 ENTITY_TABLE 不一致"
                                f"（yaml={meta[f]!r}, code={et[name][f]!r}）")
    n_declared = vdoc.get("n_variables")
    if n_declared is not None and int(n_declared) != len(var_map):
        errors.append(f"variable_dictionary: n_variables={n_declared} 与实条目数 {len(var_map)} 不符")
    missing_vars = sorted(set(et) - set(var_map))
    if missing_vars:
        warnings.append(f"variable_dictionary: 未登记 ENTITY_TABLE 变量 {missing_vars}")

    # ---- L2 物理关系 ----
    seen_rel: set = set()
    for rel in load_physics_relations():
        rid = rel.get("id")
        if not rid:
            errors.append("physics_relations: 关系缺 id")
            continue
        if rid in seen_rel:
            errors.append(f"physics_relations: 重复 id {rid}")
        seen_rel.add(rid)
        missing = [f for f in REQUIRED_RELATION_FIELDS if f not in rel]
        if missing:
            errors.append(f"physics_relations: {rid} 缺字段 {missing}")

    # ---- L3 故障模板 ----
    seen_code: set = set()
    for tpl in load_fault_templates():
        code = tpl.get("code")
        if not code:
            errors.append("fault_templates: 模板缺 code")
            continue
        if code in seen_code:
            errors.append(f"fault_templates: 重复 code {code}")
        seen_code.add(code)
        missing = [f for f in REQUIRED_TEMPLATE_FIELDS if f not in tpl]
        if missing:
            errors.append(f"fault_templates: {code} 缺字段 {missing}")
        chain = list(tpl.get("expected_chain") or []) + list(tpl.get("trigger") or [])
        for v in chain:
            if v not in et:
                errors.append(f"fault_templates: {code} 的 expected_chain/trigger 含未知变量 {v}")

    # ---- K1 禁止边 ----
    fdoc = load_forbidden_rules()
    rule_ids = [r.get("id") for r in fdoc.get("rules") or []]
    if sorted(rule_ids) != ["R1", "R2", "R3"]:
        errors.append(f"forbidden_edges: 规则应为 R1/R2/R3，实际 {rule_ids}")
    for var in list(fdoc.get("markers") or []) + list(fdoc.get("safety_step_vars") or []):
        if var not in et:
            errors.append(f"forbidden_edges: 未知变量 {var}")

    # ---- L4 σ_floor ----
    tol = load_tolerance_overrides()
    for var, floor in tol["by_variable"].items():
        if var not in et:
            errors.append(f"tolerance_overrides: 未知变量 {var}")
        elif not isinstance(floor, (int, float)) or floor <= 0:
            errors.append(f"tolerance_overrides: {var} 的下限非法 {floor!r}")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def sigma_floor_for(variable: str, mu: float, unit: Optional[str] = None) -> Tuple[float, str]:
    """返回 (σ 下限, 来源)。来源 "physical"=逐变量/逐量纲物理下限，"fallback"=1%·|μ|。

    unit 为 None 时按 ENTITY_TABLE 反查量纲；仍无配置则回退 1%·|μ|（方案 §4.4.1）。
    """
    if unit is None:
        ent = ENTITY_TABLE.get(variable)
        unit = ent.get("unit") if ent else None
    tol = load_tolerance_overrides()
    if variable in tol["by_variable"]:
        return float(tol["by_variable"][variable]), "physical"
    if unit is not None and unit in tol["by_unit"]:
        return float(tol["by_unit"][unit]), "physical"
    ratio = float(tol["fallback"].get("ratio", DEFAULT_FALLBACK_RATIO))
    return abs(float(mu)) * ratio, "fallback"


BACKWARD_DIRECTIONS = ("backward", "reverse", "reversed", -1, "-1", "back")


def _normalize_edge(edge: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """稳定边 -> (src, dst)；direction 为 backward/-1 时按反向解读端点。"""
    src, dst = edge.get("from"), edge.get("to")
    if not src or not dst:
        return None, None
    direction = edge.get("direction", "forward")
    if direction in BACKWARD_DIRECTIONS:
        return dst, src
    return src, dst


def match_fault_template(code: str, stable_edges: List[Dict[str, Any]],
                         variables: List[str]) -> Dict[str, Any]:
    """K3：用 L3 故障模板的 expected_chain 校验稳定边覆盖（辅助证据，不改排名）。

    expected_chain 相邻两两成边；与 stable_edges（from/to/direction）逐条比对：
      - 命中（matched）：稳定边方向与模板链同向；
      - 反向（reversed）：稳定边方向与模板链相反；
      - 缺失（missing）：模板链上无对应稳定边（区分“变量未入模”与“仅有向边缺失”）。
    coverage = n_matched / n_chain_edges。模板不存在时返回 available=False，不臆造。
    """
    try:
        tpl = load_fault_templates(code)
    except (KeyError, FileNotFoundError) as exc:
        return {"available": False, "template_code": code,
                "reason": f"fault_templates 无 {code} 模板: {exc}"}

    chain = [str(v) for v in (tpl.get("expected_chain") or [])]
    chain_edges = list(zip(chain, chain[1:]))
    vset = set(variables or [])
    directed = set()
    for e in stable_edges or []:
        src, dst = _normalize_edge(e)
        if src and dst:
            directed.add((src, dst))

    n_matched, n_reversed = 0, 0
    reversed_edges: List[Dict[str, str]] = []
    missing_edges: List[Dict[str, str]] = []
    for u, v in chain_edges:
        if (u, v) in directed:
            n_matched += 1
        elif (v, u) in directed:
            n_reversed += 1
            reversed_edges.append({"template": f"{u}->{v}", "stable": f"{v}->{u}"})
        else:
            reason = ("variable_not_in_model" if (u not in vset or v not in vset)
                      else "no_stable_edge")
            missing_edges.append({"template": f"{u}->{v}", "reason": reason})

    n_chain = len(chain_edges)
    return {
        "available": True,
        "template_code": code,
        "expected_chain": chain,
        "n_chain_edges": n_chain,
        "n_matched": n_matched,
        "n_reversed": n_reversed,
        "coverage": round(n_matched / n_chain, 4) if n_chain else 0.0,
        "reversed_edges": reversed_edges,
        "missing_edges": missing_edges,
        "verification": tpl.get("verification"),
        "source": tpl.get("source"),
        "note": "K3 覆盖率为辅助证据，不参与 T=(I−B)⁻¹ 排序；"
                "模板来自方案 §4.3/README，非训练数据反推",
    }


def merge_into_ontology(ontology: Dict[str, Any],
                        ext_relations: List[Dict[str, Any]]
                        ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """把外部允许边合并进代码本体：只追加，不覆盖、不删除。

    - 只追加 ontology.allowed 中尚不存在的 (from, to)；
    - 与现有 forbidden 边同向冲突 → 跳过并记入 report["skipped_forbidden_conflict"]；
    - 端点不在本体变量表中 → 跳过并记入 report["skipped_missing_variable"]；
    - forbidden / competitive 原样保留（本节只扩展 allowed）。
    """
    merged = dict(ontology)
    allowed = [dict(r) for r in ontology.get("allowed", [])]
    forbidden = {(f["from"], f["to"]) for f in ontology.get("forbidden", [])}
    existing = {(r["from"], r["to"]) for r in allowed}
    vset = set(ontology.get("variables", []))

    added: List[Dict[str, Any]] = []
    skipped_forbidden: List[Dict[str, Any]] = []
    skipped_missing: List[Dict[str, Any]] = []
    n_skipped_existing = 0

    for rel in ext_relations:
        src, dst = rel.get("from"), rel.get("to")
        if not src or not dst:
            continue
        edge = dict(rel)
        if (src, dst) in forbidden:
            skipped_forbidden.append(edge)
            continue
        if (src, dst) in existing:
            n_skipped_existing += 1
            continue
        if src not in vset or dst not in vset:
            skipped_missing.append(edge)
            continue
        allowed.append(edge)
        existing.add((src, dst))
        added.append(edge)

    merged["allowed"] = allowed
    merged["forbidden"] = list(ontology.get("forbidden", []))
    merged["competitive"] = list(ontology.get("competitive", []))
    report = {
        "n_added": len(added),
        "added": added,
        "n_skipped_existing": n_skipped_existing,
        "skipped_forbidden_conflict": skipped_forbidden,
        "n_skipped_missing_variable": len(skipped_missing),
        "skipped_missing_variable": skipped_missing,
    }
    return merged, report

"""V2.3 D1/D3 知识层单测：12 类本体 + 外部知识 YAML 加载/校验/σ_floor 合并。

覆盖方案 §4.3（12 类分组关系）、§5.2（外部知识接入）、§4.4.1（σ_floor）、§5.3.2（K2）。
可直接 python tests/test_v23_knowledge_external.py 运行（不依赖 pytest）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rca.v21 import knowledge as K
from rca.v21 import external_knowledge as EK

try:  # 另一 agent 负责的 12 类注册表；缺失时回退到方案 §4.3 的固定清单
    from rca.v21.accident_config import ALL_CODES
except Exception:  # pragma: no cover
    ALL_CODES = ["LOCA", "LOCAC", "SLBIC", "SLBOC", "SGATR", "SGBTR",
                 "RW", "RI", "FLB", "MD", "LR", "LLB"]

PILOT_CODES = ["LOCA", "SGATR", "FLB"]
NEW_CODES = ["LOCAC", "SLBIC", "SLBOC", "SGBTR", "RW", "RI", "MD", "LR", "LLB"]


def _class_variables(code):
    """该类本体涉及的全部变量（trigger + 关键响应 + 关系端点）。"""
    vars_ = []
    for rel in K.ALLOWED_RELATIONS[code]:
        for v in (rel["from"], rel["to"]):
            if v not in vars_:
                vars_.append(v)
    return vars_


# ==================== 1. 12 类本体关系 ====================
def test_all_12_classes_registered():
    assert len(ALL_CODES) == 12
    for code in ALL_CODES:
        assert code in K.ALLOWED_RELATIONS, f"{code} 未登记 ALLOWED_RELATIONS"
        assert K.ALLOWED_RELATIONS[code], f"{code} 关系为空"


def test_relation_variables_in_entity_table():
    for code in ALL_CODES:
        for rel in K.ALLOWED_RELATIONS[code]:
            assert rel["from"] in K.ENTITY_TABLE, f"{code}: 未知 from {rel['from']}"
            assert rel["to"] in K.ENTITY_TABLE, f"{code}: 未知 to {rel['to']}"
            assert rel["direction"] in ("forward", "bidirectional")
            assert rel["level"] in ("L1", "L2", "L3")
            assert rel.get("basis"), f"{code}: 关系缺 basis"


def test_new_classes_evidence_pending():
    for code in NEW_CODES:
        for rel in K.ALLOWED_RELATIONS[code]:
            assert rel.get("evidence") == "design_pending_data", \
                f"{code} 关系 {rel['from']}->{rel['to']} 缺 evidence 口径"


def test_pilot_relations_untouched():
    """pilot 三类源头条目一字未改：无 evidence 字段，build_ontology 读取时补 frozen。"""
    for code in PILOT_CODES:
        for rel in K.ALLOWED_RELATIONS[code]:
            assert "evidence" not in rel, f"{code} 冻结条目被改动（出现 evidence 字段）"
    ont = K.build_ontology("LOCA", _class_variables("LOCA"))
    assert all(r["evidence"] == "frozen" for r in ont["allowed"])


def test_rw_ri_sign_by_class():
    for code in ("RW", "RI"):
        for rel in K.ALLOWED_RELATIONS[code]:
            assert rel["sign_by_class"] == {"RW": 1, "RI": -1}


# ==================== 2. build_ontology 12 类可跑 ====================
def test_build_ontology_all_12_classes():
    for code in ALL_CODES:
        vars_ = _class_variables(code)
        ont = K.build_ontology(code, vars_)
        assert ont["accident_type"] == code
        assert ont["allowed"], f"{code} allowed 为空"
        assert ont["entities"] and set(ont["entities"]) == set(vars_)
        # marker 不自禁：任何变量不得出现 from==to 的 forbidden 自环
        for fe in ont["forbidden"]:
            assert fe["from"] != fe["to"], f"{code} 出现自禁边 {fe}"
        # 竞争边按类表（未登记类回退全局），不引入表外变量
        allowed_pairs = K.COMPETITIVE_PAIRS_BY_CLASS.get(code, K.COMPETITIVE_PAIRS)
        got = {tuple(c["pair"]) for c in ont["competitive"]}
        assert got <= {p for p in allowed_pairs if p[0] in vars_ and p[1] in vars_}


def test_unknown_accident_type_raises():
    try:
        K.build_ontology("NOT_A_CODE", ["P", "WLR"])
    except ValueError:
        return
    raise AssertionError("未知事故类型未抛 ValueError")


# ==================== 3. 外部知识 YAML ====================
def test_all_yaml_files_load():
    assert EK.EXT_KNOWLEDGE_DIR.is_dir()
    vdoc = EK.load_variable_dictionary()
    assert vdoc["source"] and vdoc["note"]
    assert len(vdoc["variables"]) == len(K.ENTITY_TABLE) == 96

    rels = EK.load_physics_relations()
    assert len(rels) == 5
    assert {r["id"] for r in rels} == {
        "R1_inventory_leak", "R2_sg_water_level", "R3_eci_trigger",
        "R4_tube_leak", "R5_reactivity_power"}

    tpls = EK.load_fault_templates()
    assert isinstance(tpls, list) and len(tpls) == 12
    one = EK.load_fault_templates("LOCA")
    assert isinstance(one, dict) and one["code"] == "LOCA"

    fdoc = EK.load_forbidden_rules()
    assert fdoc["source"] and fdoc["note"]
    assert [r["id"] for r in fdoc["rules"]] == ["R1", "R2", "R3"]
    assert fdoc["exceptions"] == [["P", "WLR"]]

    tol = EK.load_tolerance_overrides()
    assert set(tol) >= {"by_unit", "by_variable"}
    assert tol["by_unit"]["bar"] == 0.5


def test_missing_file_and_bad_yaml():
    try:
        EK.load_yaml("no_such_file_xyz")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("缺文件未抛 FileNotFoundError")


def test_validate_against_entity_table():
    res = EK.validate_against_entity_table(K.ENTITY_TABLE)
    assert res["ok"] is True, res["errors"]
    assert res["errors"] == []
    assert res["warnings"] == []          # 96 变量与 ENTITY_TABLE 逐字段一致


# ==================== 4. σ_floor ====================
def test_sigma_floor_for_units():
    floor, src = EK.sigma_floor_for("P", 156.0, "bar")
    assert (floor, src) == (0.5, "physical")
    floor, src = EK.sigma_floor_for("LVPZ", 55.0, "%")
    assert (floor, src) == (0.5, "physical")
    floor, src = EK.sigma_floor_for("WFWA", 1915.0, "t/h")
    assert (floor, src) == (5.0, "physical")
    mu = 294.3
    floor, src = EK.sigma_floor_for("TAVG", mu, "degC")
    assert src == "fallback" and abs(floor - 0.01 * abs(mu)) < 1e-12
    # unit=None 时按 ENTITY_TABLE 反查量纲
    assert EK.sigma_floor_for("PSGB", 70.0, None) == (0.5, "physical")
    assert EK.sigma_floor_for("RM2", 1000.0, None)[1] == "fallback"


# ==================== 5. merge_into_ontology ====================
def test_merge_into_ontology_only_appends():
    vars_ = _class_variables("LOCA")
    ont = K.build_ontology("LOCA", vars_)
    n_allowed, n_forbidden = len(ont["allowed"]), len(ont["forbidden"])

    new_edge = {"from": "LVPZ", "to": "P", "direction": "forward", "level": "L2",
                "basis": "外部知识补充：稳压器水位与压力耦合", "evidence": "design_pending_data"}
    dup_edge = dict(K.ALLOWED_RELATIONS["LOCA"][0])          # WLR→VOL 已存在
    conflict = {"from": "WECS", "to": "WLR", "direction": "forward", "level": "L2",
                "basis": "故意与 R2/R3 禁止边同向冲突"}
    absent = {"from": "MCRT", "to": "VOL", "direction": "forward", "level": "L2",
              "basis": "端点不在本体变量表"}

    merged, report = EK.merge_into_ontology(ont, [new_edge, dup_edge, conflict, absent])

    assert report["n_added"] == 1
    assert report["added"][0]["from"] == "LVPZ"
    assert report["n_skipped_existing"] == 1
    assert [e["to"] for e in report["skipped_forbidden_conflict"]] == ["WLR"]
    assert report["n_skipped_missing_variable"] == 1

    assert len(merged["allowed"]) == n_allowed + 1
    assert len(merged["forbidden"]) == n_forbidden          # 不删禁止边
    # 输入本体不被就地修改
    assert len(ont["allowed"]) == n_allowed
    # 幂等：再次合并同一外部边不重复追加
    merged2, report2 = EK.merge_into_ontology(merged, [new_edge])
    assert report2["n_added"] == 0 and report2["n_skipped_existing"] == 1


# ==================== 6. 故障模板 ====================
def test_fault_templates_cover_12_and_vars_known():
    tpls = EK.load_fault_templates()
    codes = [t["code"] for t in tpls]
    assert sorted(codes) == sorted(ALL_CODES)
    for t in tpls:
        assert t["trigger"], f"{t['code']} 缺 trigger"
        for v in t["trigger"] + t["expected_chain"] + t["key_symptoms"]:
            assert v in K.ENTITY_TABLE, f"{t['code']} 含未知变量 {v}"
        for v, sign in t["expected_sign_direction"].items():
            assert v in t["expected_chain"]
            assert sign in ("pos", "neg")
        expect = "frozen" if t["code"] in PILOT_CODES else "design_pending_data"
        assert t["verification"] == expect, f"{t['code']} verification 应为 {expect}"
        assert t["applicable_group"] in (1, 2, 3, 4, 5, 6)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  OK {fn.__name__}")
    print(f"\n{len(fns)} tests passed")

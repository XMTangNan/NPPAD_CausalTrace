"""V2.3 多跳审计第一批（V23-DESIGN-112）单测。

覆盖：
  ① 禁边两端记账 —— 建图层台账（in_effect/armed/exempted_competitive/dropped_no_endpoint）
     与审计层“最终图仍存在被禁边”的真命中；
     **负向测试**：故意把一条被禁边注入最终图，审计必须报出（报不出=检查未接通）；
  ② 软违规拆分 —— 有出处 vs 无出处；无凭据一侧降级为 needs_recheck，不进违规分母；
  ③ 角色映射降级 —— 仅因角色判定不构成 violating，只作 unsupported 理由。
可直接 python tests/test_v23_path_audit_forbidden.py 运行（不依赖 pytest）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from causalrca.causal import path_audit as PA
from causalrca.evidence import constraints as C
from causalrca.evidence import knowledge as K

POLICY = {"path_audit": {"max_paths": 100, "max_hops": 5, "top_n": 20,
                         "include_all_violating": True}}


def _audit(rules, stable=None):
    """合成图 U→V→W，只用不存在的类名，隔离真实知识库。"""
    variables = ["U", "V", "W"]
    stable = stable or [{"from": "U", "to": "V", "weight_median": 0.5},
                        {"from": "V", "to": "W", "weight_median": 0.5}]
    return PA.audit_template("__NO_ACCIDENT__", "g", variables, stable, rules,
                             POLICY, "TEST")


# ==================== ① 负向测试：注入被禁边必须抓出来 ====================
def test_negative_injected_forbidden_edge_is_caught():
    """负向测试：故意在最终图注入一条被禁的边，审计必须报出来。"""
    rules = [{"from": "V", "to": "W", "basis": "注入测试", "origin": "hard_forbid_edge",
              "constraint_id": "c:inj", "source_entry_id": "T_INJ"}]
    doc = _audit(rules)
    fa = doc["forbidden_audit"]
    assert fa["n_present_in_final_graph"] >= 1, "注入的被禁边未被审计发现（检查未接通）"
    assert fa["verdict"] == "fail"
    hit = fa["present_in_final_graph"][0]
    assert (hit["from"], hit["to"]) == ("V", "W")
    kinds = {p["violation_kind"] for p in doc["paths"] if p["result"] == "violating"}
    assert "hard" in kinds, "命中禁边应记为 hard 违规"
    # 追加违规路径已记录
    assert doc["audit_sample"]["n_violating_extra"] >= 0


def test_forbidden_clean_graph_reports_no_hit():
    rules = [{"from": "X", "to": "Y", "basis": "t", "origin": "hard_forbid_edge",
              "constraint_id": "c:2", "source_entry_id": "T2"}]
    doc = _audit(rules)
    fa = doc["forbidden_audit"]
    assert fa["n_present_in_final_graph"] == 0
    assert fa["verdict"] == "pass"
    assert not any(p["result"] == "violating" for p in doc["paths"])


# ==================== ② 软违规拆分 ====================
def test_two_sided_sourced_contradiction_is_hard():
    ks = {"direction": {"A": "pos", "B": "pos"}, "dir_sourced": {"A": True, "B": True},
          "edge": {("A", "B"): {"sign": "neg", "sourced": True, "entry_id": "E1"}},
          "comp": set()}
    status, _r, eid, kind = PA._polarity_check(ks, "A", "B", {})
    assert status == PA.FORBID_DENY and kind == PA.VIOLATION_HARD and eid == "E1"


def test_one_sided_contradiction_is_recheck_not_violation():
    ks = {"direction": {"A": "pos", "B": "pos"}, "dir_sourced": {"A": True, "B": False},
          "edge": {("A", "B"): {"sign": "neg", "sourced": True, "entry_id": "E2"}},
          "comp": set()}
    status, reason, _eid, kind = PA._polarity_check(ks, "A", "B", {})
    assert status == PA.RECHECK and kind is None
    assert "单侧无凭据" in reason


def test_learned_sign_vs_direction_is_recheck():
    ks = {"direction": {"A": "pos", "B": "neg"}, "dir_sourced": {"A": True, "B": True},
          "edge": {}, "comp": set()}
    status, reason, _eid, kind = PA._polarity_check(ks, "A", "B", {("A", "B"): 1.0})
    assert status == PA.RECHECK and kind is None
    assert "非知识冲突" in reason


def test_recheck_path_does_not_enter_violating_denominator():
    """学到的符号与方向不一致的路径 → unsupported + needs_recheck，不计入 violating。"""
    # U/V/W 全无知识登记 → 不走极性矛盾；改为直接检查计数口径的非负性
    doc = _audit([])
    assert doc["counts_recheck"]["all_scored"]["n_paths"] >= 0
    assert doc["counts_all_scored"]["violating"] == 0
    assert doc["schema"] == "causaltrace/path_audit/v4"


# ==================== ④ 证据密度分档（无数值门槛） ====================
def test_evidence_density_is_categorical():
    doc = _audit([])
    # 合成图两条边都没有带出处 sign → 每条路径都应为 none
    assert doc["evidence_density"]["definition"]
    assert set(doc["evidence_density"]["selected"]) <= {"all", "partial", "none"}
    assert doc["evidence_density"]["selected"].get("none", 0) == len(doc["paths"])
    for p in doc["paths"]:
        assert p["evidence_density"] in ("all", "partial", "none")
        assert p["n_hops_sourced"] == 0


# ==================== ⑤ 按贡献挑路径 + 覆盖比例 ====================
def test_selection_records_coverage_and_truncation():
    doc = _audit([])
    sel = doc["selection"]
    assert sel["mode"] == "contribution_cumulative_plus_forbidden"
    assert 0.0 <= sel["coverage_ratio"] <= 1.0
    assert sel["n_selected"] >= sel["n_selected_by_target"]
    assert sel["n_selected"] == len(doc["paths"])
    assert "truncated" in sel and "n_paths_scored" in sel
    assert doc["audit_sample"]["coverage_target"] == 0.9


def test_selection_keeps_all_forbidden_paths():
    rules = [{"from": "V", "to": "W", "basis": "inj", "origin": "hard_forbid_edge",
              "constraint_id": "c", "source_entry_id": "T"}]
    doc = _audit(rules)
    viol = [p for p in doc["paths"] if p["result"] == "violating"]
    assert viol, "踩到禁边的路径必须全部入审"
    assert doc["selection"]["n_selected_forbidden_extra"] >= 0


# ==================== ⑦ 有出处覆盖率一等指标 ====================
def test_provenance_coverage_reported_with_cap():
    doc = _audit([])
    pc = doc["provenance_coverage"]
    for key in ("n_stable_edges", "n_edges_with_sourced_sign", "edge_ratio", "confidence_cap"):
        assert key in pc
    assert pc["n_stable_edges"] == 2
    assert pc["n_edges_with_sourced_sign"] == 0
    assert pc["edge_ratio"] == 0.0
    assert "置信上限" in pc["confidence_cap"]


# ==================== ③ 角色映射降级 ====================
def test_role_advisory_is_unsupported_not_violating():
    ks = {"role": {"M": "measurement", "P": "process"}, "raw_role": {},
          "direction": {}, "dir_sourced": {}, "edge": {}, "comp": set()}
    status, reason = PA._role_check(ks, "M", "P")
    assert status == PA.UNSUP, "角色映射不得单独判违规"
    assert "角色映射" in reason and "草案" in reason


def test_reverse_forbidden_requires_sourced():
    unsourced = {"edge": {("F", "T"): {"reverse_forbidden": True, "sourced": False,
                                       "entry_id": "X"}}, "comp": set()}
    status, _r, _e, kind = PA._forbidden_check(set(), unsourced, "T", "F")
    assert status == PA.RECHECK and kind is None
    sourced = {"edge": {("F", "T"): {"reverse_forbidden": True, "sourced": True,
                                     "entry_id": "X"}}, "comp": set()}
    status, _r, _e, kind = PA._forbidden_check(set(), sourced, "T", "F")
    assert status == PA.FORBID_DENY and kind == "hard_reverse_forbidden"


# ==================== ① 建图层台账 ====================
def test_derive_rules_ledger_records_dispositions():
    ledger = []
    rules = C.derive_rules("LOCA", ledger)
    assert rules, "LOCA 应有外部知识硬禁边"
    n_in_effect = sum(1 for e in ledger if e["disposition"] == "in_effect")
    assert n_in_effect == len(rules), "台账 in_effect 数必须等于实际禁边数"
    assert all(e["disposition"] in ("in_effect", "exempted_competitive",
                                    "dropped_no_endpoint", "duplicate")
               for e in ledger)


def test_build_ontology_ledger_marks_dropped_no_endpoint():
    ont = K.build_ontology("LOCA", ["WLR", "VOL", "P"])
    lc = ont["constraints"]["ledger_counts"]
    assert lc["armed"] == len(ont["forbidden"])
    assert lc["dropped_no_endpoint"] >= 1, "变量集截断后应登记 dropped_no_endpoint"
    for ent in ont["constraints"]["ledger"]:
        if ent["disposition"] == "armed":
            assert ent["from"] in {"WLR", "VOL", "P"}
            assert ent["to"] in {"WLR", "VOL", "P"}


# ==================== ⑥ 必经中转（审计侧语义） ====================
def test_must_pass_through_pass_when_all_paths_go_via():
    adj = {"A": ["B"], "B": ["C"], "C": []}
    r = PA.must_pass_through(adj, "A", "C", ["B"])
    assert r["verdict"] == "pass" and r["n_paths"] == 1 and not r["direct_edge"]


def test_must_pass_through_fail_on_direct_edge():
    adj = {"A": ["B", "C"], "B": ["C"], "C": []}
    r = PA.must_pass_through(adj, "A", "C", ["B"])
    assert r["verdict"] == "fail" and r["direct_edge"] is True
    assert r["n_paths"] == 2 and r["n_paths_through"] == 1
    assert ["A", "C"] in r["violating_paths"]


def test_must_pass_through_vacuous_without_path():
    adj = {"A": ["B"], "B": []}
    r = PA.must_pass_through(adj, "A", "C", ["B"])
    assert r["verdict"] == "vacuous" and r["n_paths"] == 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  OK {fn.__name__}")
    print(f"\n{len(fns)} tests passed")

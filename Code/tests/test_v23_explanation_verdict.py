"""判定式解释路径（V23-DESIGN-062）与补充机理（V23-DESIGN-061）的守卫测试。"""
from __future__ import annotations

import os

import pytest

from causalrca.evidence import external_knowledge as EK
from causalrca.reporting import explanation_verdict as EV

PACK = {
    "schema": EV.EVIDENCE_SCHEMA,
    "run_id": "r1",
    "template_id": "LOCA_low",
    "nodes": [{"variable": "RHMT"}, {"variable": "LVPZ"}],
    "stable_edges": [{"from": "LVPZ", "to": "RHMT", "weight_median": -0.213042,
                      "weight_iqr": 0.0, "dir_e": 1.0, "f_e": 1.0, "count": 100}],
    "candidates": [{"rank": 1, "variable": "RHMT", "total_effect": 3.472075},
                   {"rank": 2, "variable": "LVPZ", "total_effect": 1.035767}],
    "knowledge": {
        "k2": [{"id": "R1_inventory_leak"}],
        "k3": {"template_id": "K3:LOCA", "expected_chain": ["WLR"]},
        "supplementary": [{"id": "S5_LOCA_RHMT_response"}],
    },
    "allowed_terms": ["K2", "K3", "LOCA"],
}


def _doc(**overrides):
    doc = {
        "schema": EV.VERDICT_SCHEMA,
        "run_id": "r1",
        "template_id": "LOCA_low",
        "ranking_source": "algorithm",
        "verdicts": [
            {"rank": 1, "variable": "RHMT", "verdict": "undetermined",
             "graph_basis": ["LVPZ->RHMT"],
             "knowledge_basis": ["S5_LOCA_RHMT_response"],
             "mechanism": "图上有入边，但知识条目标注为响应量。",
             "conflicts": []},
            {"rank": 2, "variable": "LVPZ", "verdict": "undetermined",
             "graph_basis": [], "knowledge_basis": [],
             "mechanism": "缺少知识覆盖。", "conflicts": []},
        ],
        "conflict_notes": [],
        "uncertainty_notes": ["缺少覆盖 LVPZ 的条目"],
        # 结论章正文（V23-DESIGN-065 起为必填）：只复述名次与判定，不引入新变量/新数值
        "conclusion": "算法名次为 RHMT、LVPZ；两项均无法判断，缺可支持的机理条目。",
    }
    doc.update(overrides)
    return doc


def test_verdict_switch_defaults_off(monkeypatch):
    monkeypatch.delenv(EV.VERDICT_ENV, raising=False)
    assert EV.verdict_enabled() is False
    monkeypatch.setenv(EV.VERDICT_ENV, "1")
    assert EV.verdict_enabled() is True
    monkeypatch.setenv(EV.VERDICT_ENV, "off")
    assert EV.verdict_enabled() is False


def test_validate_rejects_false_candidate_set_claim():
    """候选集状态断言必须与证据包一致（V23-DESIGN-075）。"""
    pack = dict(PACK)
    pack["nodes"] = [{"variable": "RHMT", "in_candidate_set": True, "has_rank": True},
                     {"variable": "WLR", "in_candidate_set": True, "has_rank": False}]
    pack["excluded_variables"] = [{"variable": "WBK", "gate_outcome": "score_rejected"}]
    doc = _doc()
    doc["uncertainty_notes"] = ["WLR 未进候选；WBK 在候选集内。"]
    res = EV.validate_verdict(doc, pack)
    assert res["ok"] is False
    assert any("WLR" in e for e in res["errors"]), res["errors"]
    assert any("WBK" in e for e in res["errors"]), res["errors"]


def test_validate_accepts_wellformed():
    res = EV.validate_verdict(_doc(), PACK)
    assert res["ok"] is True, res["errors"]


def test_validate_rejects_reordering():
    doc = _doc()
    doc["verdicts"][0]["rank"] = 2
    res = EV.validate_verdict(doc, PACK)
    assert res["ok"] is False
    assert any("名次" in e for e in res["errors"])


def test_validate_rejects_unknown_knowledge_id():
    doc = _doc()
    doc["verdicts"][0]["knowledge_basis"] = ["R9_not_exist"]
    res = EV.validate_verdict(doc, PACK)
    assert res["ok"] is False
    assert any("知识引用不存在" in e for e in res["errors"])


def test_validate_rejects_fabricated_number():
    doc = _doc()
    doc["verdicts"][0]["mechanism"] = "总效应 9.99，明显最高。"
    res = EV.validate_verdict(doc, PACK)
    assert res["ok"] is False
    assert any("忠实性" in e for e in res["errors"])


def test_validate_rejects_unsupported_without_conflict():
    doc = _doc()
    doc["verdicts"][0]["verdict"] = "unsupported"
    res = EV.validate_verdict(doc, PACK)
    assert res["ok"] is False
    assert any("unsupported" in e for e in res["errors"])


def test_supplementary_entries_never_promote_response_to_root():
    entries = EK.load_supplementary_mechanisms().get("entries") or []
    assert entries, "补充机理文件应存在且非空"
    for ent in entries:
        if str(ent.get("role_in_accident", "")).startswith("response"):
            assert ent.get("can_be_root") is False, ent.get("id")


def test_supplementary_ids_unique_and_variables_known():
    entries = EK.load_supplementary_mechanisms().get("entries") or []
    ids = [e.get("id") for e in entries]
    assert len(ids) == len(set(ids))
    et = EK.load_variable_dictionary().get("variables") or {}
    for ent in entries:
        assert ent.get("variable") in et, ent.get("id")
    report = EK.validate_against_entity_table(et)
    assert report["ok"] is True, report["errors"]


def test_llm_credentials_never_leak_value(monkeypatch):
    monkeypatch.delenv(EV.LE.V23_LLM_API_KEY_ENV, raising=False)
    monkeypatch.delenv(EV.LE.LLM_API_KEY_ENV, raising=False)
    cred = EV.LE.resolve_llm_credentials()
    assert cred["key_source"] in ("missing",) or cred["key_source"].startswith(("file:", "env:"))
    # 只检查来源标签，不对密钥内容做任何断言或输出
    assert "api_key" in cred

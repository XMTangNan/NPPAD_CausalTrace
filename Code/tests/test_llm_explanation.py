"""v2.1 R5 LLM 解释层单测（不真实调 LLM，用 mock 验证校验与降级）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rca.v21 import llm_explanation as LE


def _fake_rec():
    return {
        "condition": "FULL", "true": "LOCA", "severity": 4, "pred": "LOCA",
        "template": {"k": "LOCA", "g": "high", "marker": "WLR"},
        "first_layer": {"E1": 1.0, "E2_raw": 0.5, "E3_base_raw": 0.3,
                        "E3_path_raw": 0.7, "E2_norm": 0.5, "E3_path_norm": 0.8,
                        "fusion_score": 0.9, "s1": 0.9, "s2": 0.5, "margin": 0.4,
                        "tau": 0.0, "delta": 0.025, "abstain_reason": None},
        "A": [{"variable": "VOL", "entity": {}, "observed_d": -10.0}],
        "outside_reach": [{"variable": "RH", "entity": {}, "observed_d": 1.0}],
        "ranked_nodes": [
            {"node": "VOL", "entity": {}, "rank_L": 1, "rank_A": 2, "rrf": 0.0167,
             "magnitude_unstable": False, "a_unestimable": False, "observed_d": -10.0,
             "cL": {"cL": -3.0, "piL": 0.9},
             "cA": {"cA": -11.0, "piA": 0.95, "estimable": True},
             "effective": {"c": -11.0, "source": "A"}, "depth": 1,
             "path": ["WLR", "VOL"],
             "path_edges": [{"edge": "WLR->VOL", "grounded": True,
                             "uncertain_direction": False, "consensus_conflict": False,
                             "consensus_single": False, "severity_flipped": False,
                             "competitive": False}],
             "grounded": True, "cf_support": {"agree": 0.8, "gCF": 1.0}},
        ],
        "cf_template_gcf": 1.0,
        "flags": {"n_ranked": 1, "ungrounded_edges": [],
                  "uncertain_direction_edges": [], "consensus_conflict_edges": [],
                  "consensus_single_edges": [], "severity_flipped_edges": [],
                  "competitive_edges": [], "magnitude_unstable_nodes": [],
                  "cf_fail_nodes": [], "outside_reach_variables": ["RH"]},
        "llm_used": False, "code_template_fallback": True,
    }


def test_faithfulness_variable_hallucination():
    rec = _fake_rec()
    # XXYZ 不在合法集合，应触发 mismatch
    r = LE.check_faithfulness("传播链 WLR→VOL 之外，XXYZ 变量也异常。", rec)
    assert not r["pass"]
    assert any(m["field"] == "variable_existence" and m["found"] == "XXYZ"
               for m in r["mismatches"])


def test_faithfulness_legal_variables_pass():
    rec = _fake_rec()
    # 合法变量 VOL/RH/WLR + 术语 RRF/LiNGAM 不应触发
    r = LE.check_faithfulness("WLR 到 VOL 的 RRF=0.0167，RH 在可达域外（LiNGAM 图）。", rec)
    assert r["pass"], r["mismatches"]


def test_faithfulness_rrf_value():
    rec = _fake_rec()
    # 幻觉数值 0.9999 不在 ranked_nodes.rrf 集合 -> mismatch
    r = LE.check_faithfulness("VOL 的 RRF=0.9999", rec)
    assert any(m["field"] == "rrf_value" for m in r["mismatches"])
    # 实际数值 0.0167 应通过
    r2 = LE.check_faithfulness("VOL 的 RRF=0.0167", rec)
    assert all(m["field"] != "rrf_value" for m in r2["mismatches"])


def test_prompt_context_contains_key_fields():
    rec = _fake_rec()
    ctx = LE._build_prompt_context(rec)
    assert "LOCA" in ctx and "WLR" in ctx
    assert "RRF=0.0167" in ctx
    assert "VOL" in ctx and "RH" in ctx
    assert "融合分" in ctx or "fusion" in ctx


def test_valid_variables_union():
    rec = _fake_rec()
    vs = LE._valid_variables(rec)
    assert {"WLR", "VOL", "RH", "LOCA"} <= vs


def test_degrade_no_api_key():
    rec = _fake_rec()
    orig = LE._get_api_key
    LE._get_api_key = lambda: ""   # mock 无 key
    try:
        r = LE.generate_llm_explanation(rec)
        assert r["llm_used"] is False and r["degraded"] is True
        assert r["degraded_reason"] == "无 API key"
        assert "根因" in r["llm_explanation"]  # 降级到模板文本
    finally:
        LE._get_api_key = orig


def test_degrade_llm_failure():
    rec = _fake_rec()
    orig_key, orig_call = LE._get_api_key, LE._call_deepseek_api
    LE._get_api_key = lambda: "sk-fake"
    LE._call_deepseek_api = lambda prompt: None   # mock 调用失败
    try:
        r = LE.generate_llm_explanation(rec)
        assert r["llm_used"] is False and r["degraded"] is True
        assert r["degraded_reason"] == "LLM 调用失败/无输出"
    finally:
        LE._get_api_key, LE._call_deepseek_api = orig_key, orig_call


def test_llm_success_path_with_faithfulness():
    rec = _fake_rec()
    orig_key, orig_call = LE._get_api_key, LE._call_deepseek_api
    LE._get_api_key = lambda: "sk-fake"
    # 合法输出（含通俗解读，无幻觉变量，RRF 用实际值）
    LE._call_deepseek_api = lambda prompt: (
        "【判定】LOCA 工况。\n【传播链】WLR→VOL 的 RRF=0.0167。\n"
        "【结论】稳定识别出的有向关系。\n【通俗解读】就像水管破了漏水，压力自然下降。")
    try:
        r = LE.generate_llm_explanation(rec)
        assert r["llm_used"] is True and r["degraded"] is False
        assert r["faithful_pass"] is True
        assert "通俗解读" in r["llm_explanation"]
    finally:
        LE._get_api_key, LE._call_deepseek_api = orig_key, orig_call


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n全部 {len(tests)} 个 R5 LLM 解释层单测通过")


if __name__ == "__main__":
    _run_all()

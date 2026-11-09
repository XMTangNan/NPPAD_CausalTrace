"""V2.3 D6/D7/D10 单测：解释忠实性、离线 rule 后端、LLM 降级、9 份报告、全链编排。

原则：涉及写盘一律落 tmp_path（monkeypatch artifact.V23_SUBDIRS），不污染正式产物；
需要真实证据的用例只读取 output/rca_v23 下已存在的 9 模板产物（不存在则 skip）。
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

from rca.v21 import artifact
from rca.v21 import llm_explanation as LE
from rca.v21 import report_template as RT
from rca.v21 import run_v23_all as RA
from rca.v21 import run_v23_report as RR

REAL_V23 = Path(artifact.V23_OUTPUT)


def _patch_output(monkeypatch, root):
    """把产物根整体指向 tmp（保持同名子目录），返回 subs 字典。"""
    root.mkdir(parents=True, exist_ok=True)
    subs = {}
    for name in list(artifact.V23_SUBDIRS):
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        subs[name] = d
    monkeypatch.setattr(artifact, "V23_SUBDIRS", subs)
    return subs


def _copy_real_case(subs, k="LOCA", g="mid"):
    """把真实模板的 4 个只读输入复制到 tmp，供报告链路使用。"""
    pairs = [("tracing", f"root_candidates_{k}_{g}.json"),
             ("signatures", f"signature_{k}_{g}.json"),
             ("reports", f"summary_{k}.json"),
             ("reports", f"diff_{k}.json")]
    for sub, name in pairs:
        src = Path(artifact.V23_OUTPUT) / sub / name
        if not src.exists():
            pytest.skip(f"缺真实产物 {sub}/{name}")
        shutil.copy2(src, subs[sub] / name)


# ==================== 忠实性校验 ====================
def test_verify_faithfulness_flags_unsupported_number_and_var():
    payload = {"variables": ["WLR", "VOL"], "n_bootstrap": 100,
               "top_k": 3, "score": 1.25}
    text = "Top-3 根因 WLR 得分 1.25，bootstrap 100 次；另见 987.65 与 ZZZZ。"
    res = LE.verify_faithfulness(text, payload)
    assert res["faithful"] is False
    assert res["n_numbers_unsupported"] == 1
    assert res["unsupported"] == ["987.65"]
    assert res["n_vars_unknown"] == 1
    assert res["unknown"] == ["ZZZZ"]


def test_verify_faithfulness_accepts_all_supported_text():
    payload = {"variables": ["WLR", "VOL"], "n_bootstrap": 100,
               "top_k": 3, "score": 1.25}
    text = "Top-3 根因 WLR 得分 1.25，bootstrap 100 次。"
    res = LE.verify_faithfulness(text, payload)
    assert res["faithful"] is True
    assert res["n_numbers_unsupported"] == 0 and res["n_vars_unknown"] == 0


# ==================== rule 后端（真实产物，离线） ====================
def test_rule_backend_on_real_loca_mid():
    rc = REAL_V23 / "tracing" / "root_candidates_LOCA_mid.json"
    if not rc.exists():
        pytest.skip("缺真实产物 tracing/root_candidates_LOCA_mid.json")
    payload = LE.build_payload("LOCA", "mid")
    res = LE.generate_explanation(payload, backend="rule")
    top1 = payload["root_candidates"][0]["variable"]
    assert res["backend"] == "rule"
    assert res["degraded"] is False
    assert res["degraded_reason"] is None
    assert top1 in res["text"]
    assert res["faithfulness"]["faithful"] is True
    assert res["faithfulness"]["n_numbers_unsupported"] == 0


# ==================== llm 后端降级 ====================
def test_llm_backend_degrades_to_rule_when_unreachable(monkeypatch):
    rc = REAL_V23 / "tracing" / "root_candidates_LOCA_mid.json"
    if not rc.exists():
        pytest.skip("缺真实产物 tracing/root_candidates_LOCA_mid.json")
    monkeypatch.setenv(LE.V23_LLM_BASE_URL_ENV, "http://127.0.0.1:9")
    monkeypatch.setenv(LE.V23_LLM_API_KEY_ENV, "sk-test-not-used")
    monkeypatch.setattr(LE, "V23_LLM_TIMEOUT_S", 2)
    payload = LE.build_payload("LOCA", "mid")
    res = LE.generate_explanation(payload, backend="llm")
    assert res["backend"] == "rule"                 # 明确回退
    assert res["requested_backend"] == "llm"
    assert res["degraded"] is True
    assert res["degraded_reason"]                   # 非空，绝不静默成功
    assert "llm" in res["degraded_reason"].lower()
    assert res["faithfulness"]["faithful"] is True


# ==================== D7 报告 ====================
def test_report_LOCA_mid_json_and_txt(monkeypatch, tmp_path):
    subs = _patch_output(monkeypatch, tmp_path / "rca_v23")
    _copy_real_case(subs, "LOCA", "mid")
    expected_run_id = json.loads(
        (subs["tracing"] / "root_candidates_LOCA_mid.json").read_text(encoding="utf-8")
    )["run_id"]

    run_id = RR.main(["--classes", "LOCA", "--groups", "mid", "--no-manifest"])
    assert run_id == expected_run_id

    jpath = subs["reports"] / "rca_report_LOCA_mid.json"
    tpath = subs["reports"] / "rca_report_LOCA_mid.txt"
    assert jpath.exists() and tpath.exists()
    assert (subs["reports"] / "rca_report_LOCA_mid.txt.meta.json").exists()

    rep = json.loads(jpath.read_text(encoding="utf-8"))
    assert rep["run_id"] == expected_run_id
    assert set(rep["sections"]) == set(RT.SECTION_KEYS)
    assert len(rep["sections"]) == 8
    assert rep["sections"]["1_conclusion_top5"]["candidates"]
    assert rep["sections"]["8_reproducibility"]["run_id"] == expected_run_id
    assert rep["sections"]["8_reproducibility"]["inputs"]

    text = tpath.read_text(encoding="utf-8")
    for title in RT.SECTION_TITLES.values():
        assert title in text
    assert "未产出（原因：" in text           # K2/偏离度缺失如实标注
    assert expected_run_id in text


# ==================== D10 全链编排 ====================
def test_run_v23_all_dry_run_prints_six_steps_without_writing(monkeypatch, tmp_path, capsys):
    subs = _patch_output(monkeypatch, tmp_path / "rca_v23")
    rc = RA.main(["--dry-run", "--classes", "LOCA"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "共 7 个步骤" in out
    for i in range(1, 8):
        assert f"[{i}/7]" in out
    # 不写盘：所有子目录为空
    assert all(not list(d.iterdir()) for d in subs.values())
    assert not (subs["reports"] / "run_v23_all_summary.json").exists()


def test_run_v23_all_records_failure_and_returns_nonzero(monkeypatch, tmp_path, capsys):
    # 模拟 D2 执行器未就绪（即使真实模块已被别的用例导入，也强制走失败路径）
    import rca.v21 as v21_pkg
    monkeypatch.delattr(v21_pkg, "run_v23_tolerances", raising=False)
    monkeypatch.setitem(sys.modules, "rca.v21.run_v23_tolerances", None)
    subs = _patch_output(monkeypatch, tmp_path / "rca_v23")
    rc = RA.main(["--classes", "LOCA", "--groups", "mid"])
    capsys.readouterr()
    assert rc == 1
    summary = json.loads(
        (subs["reports"] / "run_v23_all_summary.json").read_text(encoding="utf-8"))
    steps = summary["steps"]
    assert len(steps) == 1
    assert steps[0]["name"] == "tolerances"
    assert steps[0]["status"] == "failed"
    assert "D2 公差执行器未就绪" in steps[0]["error"]
    assert summary["n_steps_failed"] == 1
    assert summary["failed"]

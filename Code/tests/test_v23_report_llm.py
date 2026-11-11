"""V2.3 D6/D7/D10 单测：解释忠实性、离线 rule 后端、LLM 降级、9 份报告、全链编排。

原则：涉及写盘一律落 tmp_path（monkeypatch artifact.V23_RUNS），不污染正式产物；
需要真实证据的用例只读取 output/latest 指向的 run 下已存在的 9 模板产物（不存在则 skip）。
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

from causalrca.io import artifact
from causalrca.reporting import llm_explanation as LE
from causalrca.reporting import report_template as RT
from causalrca.cli import run_v23_all as RA
from causalrca.cli import run_v23_report as RR

_LATEST_RUN = artifact.latest_run()
REAL_V23 = artifact.run_path(_LATEST_RUN) if _LATEST_RUN else None
FIXTURE_DIR = Path(__file__).parent / "fixtures"


class _RunDirs:
    """延迟解析 run 根下的子目录（run 根 = <tmp>/<当前绑定 run_id>）。"""

    def __init__(self, root):
        self.root = Path(root)

    def __getitem__(self, name):
        d = Path(artifact.run_root()) / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def values(self):
        return [self[n] for n in artifact.V23_SUBDIR_NAMES]


def _patch_output(monkeypatch, root, run_id=None):
    """把唯一产物根指向 tmp（run 根 = <tmp>/<run_id>），run_id 固定便于预置输入。

    A 阶段产物（variable_sets/）从 latest run 只读拷贝，避免单测依赖真实 output 的写入痕迹。
    """
    latest = artifact.latest_run()
    src = artifact.run_path(latest) / "variable_sets" if latest else None
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    run_id = run_id or f"v23c-{root.name}"
    monkeypatch.setattr(artifact, "V23_RUNS", root)
    monkeypatch.setattr(artifact, "V23_LATEST", root / "latest")
    monkeypatch.setattr(artifact, "new_run_id",
                        lambda prefix="v23c": f"{prefix}-{root.name}")
    monkeypatch.setattr(artifact, "_RUN_ID", run_id)
    if src and src.exists():
        dst = root / run_id / "variable_sets"
        if True:
            dst.mkdir(parents=True, exist_ok=True)
            for f in sorted(src.glob("*.json")):
                shutil.copy2(f, dst / f.name)
    return _RunDirs(root)


def _copy_real_case(subs, k="LOCA", g="mid"):
    """把真实模板的 4 个只读输入复制到 tmp，供报告链路使用。"""
    if REAL_V23 is None:
        pytest.skip("runs/ 下无有效产物")
    pairs = [("tracing", f"root_candidates_{k}_{g}.json"),
             ("signatures", f"signature_{k}_{g}.json"),
             ("reports", f"summary_{k}.json"),
             ("reports", f"diff_{k}.json")]
    for sub, name in pairs:
        src = REAL_V23 / sub / name
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


# ==================== K2 挂载路径（V23-CODE-022 回归） ====================
def test_load_k2_reads_real_auxiliary_payload():
    """真实签名 fixture：K2 挂在 auxiliary.k2，必须被正确读出（不能写“未产出”）。"""
    sig = json.loads((FIXTURE_DIR / "signature_LOCA_high_real.json")
                     .read_text(encoding="utf-8"))
    aux = sig["auxiliary"]["k2"]
    k2 = LE._load_k2("LOCA", "high", sig)
    assert k2["available"] is True
    assert k2["source"].endswith("#auxiliary.k2")
    assert k2["n_relations"] == aux["n_relations_applicable"]
    assert k2["n_violation"] == aux["n_violation"]
    assert k2["n_not_violated"] == aux["n_not_violated"]
    assert k2["n_undecidable"] == aux["n_undecidable"]
    assert len(k2["relations"]) == aux["n_relations_applicable"]


def _bind_real():
    """只读真实产物：把进程绑定到 latest run（不写盘）。"""
    if _LATEST_RUN is None:
        pytest.skip("runs/ 下无有效产物")
    artifact.bind_run(_LATEST_RUN)


def test_load_k2_legacy_top_level_and_missing():
    _bind_real()
    legacy = {"k2": {"relations": [{"relation_id": "R1", "status": "violation"}],
                     "n_violation": 1}}
    assert LE._load_k2("LOCA", "low", legacy)["n_violation"] == 1
    missing = LE._load_k2("LOCA", "low", {})
    assert missing["available"] is False and missing["reason"]


def test_payload_k2_available_on_real_signature():
    _bind_real()
    sig_path = REAL_V23 / "signatures" / "signature_LOCA_high.json"
    if not sig_path.exists():
        pytest.skip("缺真实产物 signatures/signature_LOCA_high.json")
    payload = LE.build_payload("LOCA", "high")
    assert payload["k2"]["available"] is True
    assert payload["k2"]["n_relations"] >= 1


# ==================== rule 后端（真实产物，离线） ====================
def test_rule_backend_on_real_loca_mid():
    _bind_real()
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
    _bind_real()
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
    subs = _patch_output(monkeypatch, tmp_path / "runs")
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
    subs = _patch_output(monkeypatch, tmp_path / "runs")
    rc = RA.main(["--dry-run", "--classes", "LOCA"])
    out = capsys.readouterr().out
    n_steps = len(RA.STEPS)
    assert rc == 0
    assert f"共 {n_steps} 个步骤" in out
    for i in range(1, n_steps + 1):
        assert f"[{i}/{n_steps}]" in out
    # 不写盘：除预置的 A 阶段输入外，所有子目录为空
    assert all(not list(subs[n].iterdir())
               for n in artifact.V23_SUBDIR_NAMES if n != "variable_sets")
    assert not (subs["reports"] / "run_v23_all_summary.json").exists()


def test_run_v23_all_records_failure_and_returns_nonzero(monkeypatch, tmp_path, capsys):
    # 模拟 D2 执行器未就绪（即使真实模块已被别的用例导入，也强制走失败路径）
    import causalrca.cli as _cli_pkg
    monkeypatch.delattr(_cli_pkg, "run_v23_tolerances", raising=False)
    monkeypatch.setitem(sys.modules, "causalrca.cli.run_v23_tolerances", None)
    # 前置 A1/B 两步用桩替代，避免用例真的去跑变量选取与数据审计
    monkeypatch.setattr(RA, "step_variable_sets", lambda run_id: "ok")
    monkeypatch.setattr(RA, "step_audit", lambda run_id, classes: "ok")
    subs = _patch_output(monkeypatch, tmp_path / "runs")
    rc = RA.main(["--classes", "LOCA", "--groups", "mid",
                  "--rerun-reason", "E-R3 单测：验证失败路径与步骤记录"])
    capsys.readouterr()
    assert rc == 1
    summary = json.loads(
        (subs["reports"] / "run_v23_all_summary.json").read_text(encoding="utf-8"))
    steps = summary["steps"]
    assert [s["name"] for s in steps] == ["variable_sets", "audit", "tolerances"]
    assert steps[-1]["status"] == "failed"
    assert "D2 公差执行器未就绪" in steps[-1]["error"]
    assert summary["n_steps_failed"] == 1
    assert summary["failed"]


# ==================== V23-CODE-034：汇总文本不得出现内部编号 ====================
def _minimal_class_payload() -> dict:
    tpl = {
        "template": "LOCA_low", "group": "low", "marker": "WLR",
        "n_variables": 55, "n_stable_edges": 61, "n_bootstrap": 5,
        "top1": "RHMT", "top1_total_effect": 1.5,
        "top5": [{"rank": i + 1, "variable": v, "subsystem": "示例",
                  "total_effect": 1.0}
                 for i, v in enumerate(["RHMT", "LVPZ", "PRB", "TRB", "P"])],
        "k2": {"available": True, "n_relations": 10, "n_violation": 2,
               "n_undecidable": 0},
        "k3": {"available": True, "coverage": 0.5, "n_matched": 2,
               "n_chain_edges": 4},
        "deviation": {"available": True, "n_variables": 50,
                      "level_counts": {"L1": 1}},
        "stability_degraded": False,
        "cond_IminusB": 10.0, "cond_IminusB_after_clip": 10.0,
    }
    return {
        "schema": "causaltrace/v2.3/llm-class-payload",
        "run_id": "v23-test", "accident_type": "LOCA",
        "template_ids": ["LOCA_low"], "templates": [tpl],
        "cross_group": {"available": True, "top1_by_group": {"low": "RHMT"},
                        "top1_stable": True, "n_vars_in_all_groups": 3},
        "cross_group_diff": {"available": True, "n_top1_switches": 0,
                             "n_mechanism_switches": 0},
        "limitations": ["示例局限"], "inputs": [],
        "allowed_terms": list(LE._V23_ALLOWED_TERMS),
    }


def test_class_summary_text_has_no_internal_ids():
    """V23-CODE-034：rule 汇总文本不得写内部编号（否则被忠实性校验判为未支撑变量）。"""
    payload = _minimal_class_payload()
    text = LE.render_rule_class_summary(payload)
    for token in ("V23", "DESIGN", "§"):
        assert token not in text, f"汇总文本仍含内部编号标记：{token}"
    faith = LE.verify_summary_faithfulness(text, payload)
    assert faith["faithful"] is True, faith
    assert faith["n_vars_unknown"] == 0
    assert faith["n_numbers_unsupported"] == 0

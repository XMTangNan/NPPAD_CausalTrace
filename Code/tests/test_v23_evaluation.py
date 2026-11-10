"""D11 留出集评测的守卫（V23-DESIGN-035）。

覆盖：Wilson 区间与 v2.1 同式、检索指标手算、名次归一、以及 evaluate() 落盘契约
（只落 metrics/、9→池内模板数、summary 与逐例一致）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from causalrca.io import artifact
from causalrca.reporting import evaluation as EV


# ==================== 纯函数 ====================
def test_wilson_ci95_matches_v21_reference():
    """p=1 时下界 = 1/(1+z²/n)，与 v2.1 baseline_metrics.json 的 20/20、60/60 一致。"""
    lo, hi = EV.wilson_ci95(20, 20)
    assert lo == pytest.approx(0.8389, abs=1e-3)
    assert hi == 1.0
    lo60, _ = EV.wilson_ci95(60, 60)
    assert lo60 == pytest.approx(0.9398, abs=1e-3)
    assert EV.wilson_ci95(0, 0) == [None, None]


def test_retrieval_metrics_hand_computed():
    ranking = ["LOCA_low", "SGATR_low", "FLB_low", "LOCA_mid", "LOCA_high"]
    rel = ["LOCA_low", "LOCA_mid", "LOCA_high"]
    m = EV.retrieval_metrics(ranking, rel, "LOCA_low")
    assert m["hit1"] == 1.0 and m["hit3"] == 1.0
    assert m["mrr"] == 1.0
    assert m["p2"] == 0.5                     # 前 2 名只有 1 个同类
    assert m["p3"] == pytest.approx(1 / 3)    # 前 3 名只有 1 个同类
    assert m["recall3"] == pytest.approx(1 / 3)
    assert m["ap3"] == pytest.approx(1 / 3)   # AP@3 = (1/1)/min(3,3)
    assert m["exact_hit1"] == 1.0

    ranking2 = ["SGATR_low", "LOCA_low", "LOCA_mid", "FLB_low", "LOCA_high"]
    m2 = EV.retrieval_metrics(ranking2, rel, "LOCA_low")
    assert m2["hit1"] == 0.0 and m2["hit3"] == 1.0
    assert m2["mrr"] == 0.5
    assert m2["p3"] == pytest.approx(2 / 3)
    assert m2["ap3"] == pytest.approx((0.5 + 2 / 3) / 3, abs=1e-6)
    assert m2["first_correct_rank"] == 2

    m3 = EV.retrieval_metrics(["SGATR_low", "SGATR_mid", "FLB_low"], rel, "LOCA_low")
    assert m3["hit1"] == 0.0 and m3["hit3"] == 0.0 and m3["mrr"] == 0.0
    assert m3["ap3"] == 0.0


def test_percentile_ranks_handles_ties():
    r = EV.percentile_ranks({"A": 1.0, "B": 5.0, "C": 5.0, "D": 9.0})
    assert r["A"] == pytest.approx(0.25)
    assert r["B"] == pytest.approx(r["C"])
    assert r["D"] == 1.0
    assert EV.percentile_ranks({}) == {}


# ==================== 落盘契约 ====================
def _tmp_run(monkeypatch, tmp_path) -> str:
    """把产物根指到 tmp，并把最新 run 的 registry/tracing 复制进来（只读输入）。"""
    latest = artifact.latest_run()
    if not latest:
        pytest.skip("没有可用的 v2.3 run（先跑一次全链）")
    src = artifact.run_path(latest)
    if not (src / "tracing").exists():
        pytest.skip(f"{latest} 缺 tracing/，无法评测")
    monkeypatch.setattr(artifact, "V23_RUNS", tmp_path)
    monkeypatch.setattr(artifact, "V23_LATEST", tmp_path / "latest")
    run_id = "v23pytest-eval"
    root = tmp_path / run_id
    for sub in ("registry", "tracing"):
        shutil.copytree(src / sub, root / sub)
    monkeypatch.setattr(artifact, "_RUN_ID", run_id)
    return run_id


def test_evaluate_writes_metrics_only_and_summary_consistent(monkeypatch, tmp_path):
    run_id = _tmp_run(monkeypatch, tmp_path)
    res = EV.evaluate(run_id, classes=["LOCA"], groups=["low", "mid"])

    root = artifact.run_path(run_id)
    assert (root / "metrics").is_dir()
    names = sorted(p.name for p in (root / "metrics").iterdir())
    assert names == ["metrics_LOCA_low.json", "metrics_LOCA_mid.json",
                     "metrics_summary.json"]
    # 其它子目录不得被评测写脏
    assert not (root / "reports").exists()

    per_tpl = json.loads((root / "metrics" / "metrics_LOCA_low.json").read_text(encoding="utf-8"))
    assert per_tpl["run_id"] == run_id
    assert per_tpl["n_test"] == 7                      # LOCA low 留出 7 条
    assert len(per_tpl["traces"]) == 7
    assert per_tpl["pool"] == ["LOCA_low", "LOCA_mid"]  # 池 = 给定类 × 组
    for tr in per_tpl["traces"]:
        assert tr["accident_type"] == "LOCA"
        assert tr["severity"] in (2, 33, 4, 14, 20, 22, 28, 31, 33)
        assert set(tr["ranking"]) == set(EV.CHANNELS)
        for ch in EV.CHANNELS:
            assert sorted(tr["ranking"][ch]) == sorted(per_tpl["pool"])

    summary = json.loads((root / "metrics" / "metrics_summary.json").read_text(encoding="utf-8"))
    assert summary["n_traces"] == 14                    # low 7 + mid 7
    assert summary["global"]["causal"]["n"] == 14
    assert summary["global"]["causal"]["hit1_wilson95"][0] is not None
    assert set(summary["per_class"]) == {"LOCA"}
    assert summary["leakage_note"]


def test_evaluate_rejects_missing_topology(monkeypatch, tmp_path):
    run_id = _tmp_run(monkeypatch, tmp_path)
    (tmp_path / run_id / "tracing" / "topology_LOCA_low.json").unlink()
    with pytest.raises(FileNotFoundError):
        EV.evaluate(run_id, classes=["LOCA"], groups=["low"])

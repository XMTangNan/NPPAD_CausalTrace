"""D11 留出集评测的守卫（V23-DESIGN-036：事故内、变量级口径）。

覆盖：Wilson 区间、变量级命中指标手算、真值只来自 config/injection_truth.py、
溯源排序与 root_candidates.rank 一致、遮蔽/去遮蔽两通道的候选差异、
落盘契约、缺输入必须报错。
"""
from __future__ import annotations

import json
import shutil

import pytest

from causalrca.config import injection_truth as IT
from causalrca.io import artifact
from causalrca.reporting import evaluation as EV


# ==================== 纯函数 ====================
def test_wilson_ci95_reference_values():
    """Wilson 是统计工具（非 v2.1 判据）：p=1 时下界 = 1/(1+z²/n)。"""
    lo, hi = EV.wilson_ci95(20, 20)
    assert lo == pytest.approx(0.8389, abs=1e-3)
    assert hi == 1.0
    assert EV.wilson_ci95(60, 60)[0] == pytest.approx(0.9398, abs=1e-3)
    assert EV.wilson_ci95(0, 0) == [None, None]


def test_ranking_metrics_variable_level():
    """变量级命中：真值是该事故的始发变量集，排序是候选变量排序。"""
    m = EV.ranking_metrics(["WLR", "WECS", "FRZR"], ["WLR"])
    assert (m["hit1"], m["hit3"]) == (1.0, 1.0)
    assert m["mrr"] == 1.0
    assert m["p2"] == 0.5 and m["p3"] == pytest.approx(1 / 3)
    assert m["recall3"] == 1.0 and m["ap3"] == 1.0
    assert m["jaccard3"] == pytest.approx(1 / 3)

    m2 = EV.ranking_metrics(["WECS", "FRZR", "WLR", "WHPI"], ["WLR"])
    assert (m2["hit1"], m2["hit3"]) == (0.0, 1.0)
    assert m2["mrr"] == pytest.approx(1 / 3)
    assert m2["p2"] == 0.0 and m2["ap3"] == pytest.approx(1 / 3)
    assert m2["first_rank"] == 3

    m3 = EV.ranking_metrics(["WECS", "FRZR", "WHPI", "WLR"], ["WLR"])
    assert m3["hit3"] == 0.0 and m3["mrr"] == pytest.approx(0.25)
    assert m3["first_rank"] == 4

    # 多变量真值（LOCA 三变量）：recall@3 = 2/3，AP@3 = (1 + 0 + 2/3)/3
    m4 = EV.ranking_metrics(["WLR", "WECS", "MBK"], ["WLR", "WBK", "MBK"])
    assert (m4["hit1"], m4["hit3"]) == (1.0, 1.0)
    assert m4["recall3"] == pytest.approx(2 / 3)
    assert m4["ap3"] == pytest.approx((1 + 0 + 2 / 3) / 3)
    assert m4["jaccard3"] == pytest.approx(2 / 4)
    assert m4["n_gt"] == 3 and m4["n_gt_in_ranking"] == 2

    # 真值完全不在候选里：全部 0，且 n_gt_in_ranking=0（区分“没找对”与“不在候选”）
    m5 = EV.ranking_metrics(["WECS", "FRZR"], ["WLR"])
    assert (m5["hit1"], m5["hit3"], m5["mrr"]) == (0.0, 0.0, 0.0)
    assert m5["n_gt_in_ranking"] == 0


def test_injection_truth_is_the_only_source():
    """真值只来自 config/injection_truth.py；未登记类直接报错，禁止代理。"""
    assert IT.truth_for("LOCA") == ("WLR", "WBK", "MBK")
    assert IT.truth_for("FLB") == ("WFLB",)
    with pytest.raises(KeyError):
        IT.truth_for("SLBIC")          # 未登记：不许回退成关键响应/forced 集
    with pytest.raises(KeyError):
        EV.IT.truth_for("NOT_A_CLASS")


def test_deviation_ranking_uses_only_deviation():
    d = {"A": 1.0, "B": 9.0, "C": 5.0}
    assert EV.deviation_ranking(["A", "B", "C"], d) == ["B", "C", "A"]
    # 缺 d 的变量排最后，不参与因果判断
    assert EV.deviation_ranking(["A", "X"], {"A": 1.0}) == ["A", "X"]


# ==================== 真实产物一致性 ====================
def _latest_run() -> str:
    latest = artifact.latest_run()
    if not latest:
        pytest.skip("没有可用的 v2.3 run（先跑一次全链）")
    if not (artifact.run_path(latest) / "tracing").exists():
        pytest.skip(f"{latest} 缺 tracing/，无法评测")
    return latest


def test_causal_ranking_matches_persisted_root_candidates():
    """溯源排序必须与 root_candidates.rank 完全一致（否则评测与产物口径脱节）。"""
    latest = _latest_run()
    checked = 0
    for p in sorted((artifact.run_path(latest) / "tracing").glob("root_candidates_*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        k, g = p.stem.replace("root_candidates_", "").rsplit("_", 1)
        tpl = EV.load_template(k, g, latest)
        order = EV.causal_ranking(tpl)
        ranked = [c["variable"] for c in sorted(doc["root_candidates"], key=lambda c: c["rank"])]
        assert order[:len(ranked)] == ranked
        checked += 1
    assert checked >= 9


def test_production_masks_injection_point_and_unmasked_restores_it():
    """生产口径遮蔽注入点（total_effect=None、不进候选）；评测模式把它放回候选。"""
    latest = _latest_run()
    tpl = EV.load_template("LOCA", "high", latest)
    marker = tpl["marker"]
    assert marker == "WLR"
    assert tpl["nodes"][marker].get("total_effect") is None
    assert tpl["marker_info"]["rank"] is None
    assert marker not in EV.causal_ranking(tpl)
    assert marker in EV.causal_ranking_unmasked(tpl)


# ==================== 落盘契约 ====================
def _tmp_run(monkeypatch, tmp_path) -> str:
    """把产物根指到 tmp，并把最新 run 的只读输入复制进来。"""
    latest = _latest_run()
    src = artifact.run_path(latest)
    monkeypatch.setattr(artifact, "V23_RUNS", tmp_path)
    monkeypatch.setattr(artifact, "V23_LATEST", tmp_path / "latest")
    run_id = "v23pytest-eval"
    root = tmp_path / run_id
    for sub in ("registry", "tracing", "signatures"):
        shutil.copytree(src / sub, root / sub)
    monkeypatch.setattr(artifact, "_RUN_ID", run_id)
    return run_id


def test_evaluate_writes_metrics_only(monkeypatch, tmp_path):
    run_id = _tmp_run(monkeypatch, tmp_path)
    res = EV.evaluate(run_id, classes=["LOCA"], groups=["low", "mid"])

    root = artifact.run_path(run_id)
    assert sorted(p.name for p in (root / "metrics").iterdir()) == [
        "metrics_LOCA_low.json", "metrics_LOCA_mid.json", "metrics_summary.json"]
    assert not (root / "reports").exists()

    tpl = json.loads((root / "metrics" / "metrics_LOCA_low.json").read_text(encoding="utf-8"))
    assert tpl["run_id"] == run_id
    assert tpl["n_test"] == 7
    gt = tpl["ground_truth"]
    assert gt["all_registered"] == ["WLR", "WBK", "MBK"]
    assert "WLR" in gt["variables"] and set(gt["variables"]) <= set(gt["all_registered"])
    assert gt["source"] == "causalrca/config/injection_truth.py::INJECTION_TRUTH"
    assert gt["suspect"] == ["WBK", "MBK"]
    assert tpl["injection_point_masking"]["masked"] is True
    assert tpl["injection_point_masking"]["variable"] == "WLR"
    assert set(tpl["clip_sensitivity"]["clips"]) == {lab for lab, _c in EV.CLIP_VARIANTS}
    assert {"hit1", "hit3", "mrr", "marker_rank_median"} <= set(
        tpl["clip_sensitivity"]["clips"]["clip_off"])
    assert tpl["clip_sensitivity"]["clips"]["clip5"]["n"] == 7
    assert tpl["candidate_set"]["source"].startswith("tracing/topology_LOCA_low.json")
    for tr in tpl["traces"]:
        assert tr["accident_type"] == "LOCA"
        assert set(tr["metrics"]) == set(EV.CHANNELS)
        assert tr["gt"] == gt["variables"]
        # 生产口径遮蔽 WLR → 真值不可能在候选里，命中结构性为 0
        assert "WLR" not in tr["gt_in_candidate_set"]["causal"]
        assert tr["metrics"]["causal"]["hit1"] == 0.0
        assert 0.0 <= tr["metrics"]["deviation_equal_weight"]["hit3"] <= 1.0

    summary = json.loads((root / "metrics" / "metrics_summary.json").read_text(encoding="utf-8"))
    assert summary["n_traces"] == 14
    assert summary["ground_truth"]["by_class"] == {"LOCA": ["WLR", "WBK", "MBK"]}
    assert summary["not_v21_paradigm"].startswith("不做事故类型")
    assert summary["comparability_with_teacher"]["same_task"] is True
    assert summary["comparability_with_teacher"]["comparable_numbers"] is False
    assert summary["global"]["causal"]["n"] == 14
    assert summary["global"]["causal"]["hit1"] == 0.0       # 遮蔽 → 结构性 0
    assert set(summary["per_template"]["LOCA_low"]) == set(EV.CHANNELS)


def test_clip_sensitivity_is_first_class():
    """B_CLIP 敏感性必须并列四列，且翻转模板要被显式标出（V23-DESIGN-038）。"""
    latest = _latest_run()
    tpl = EV.load_template("LOCA", "high", latest)
    ranks = {lab: EV.causal_ranking_unmasked(tpl, clip=c).index("WLR") + 1
             for lab, c in EV.CLIP_VARIANTS}
    assert ranks["clip5"] == 1 and ranks["clip_off"] == 3     # 命中随截断翻转

    summary = json.loads((artifact.run_path(latest) / "metrics" / "metrics_summary.json")
                         .read_text(encoding="utf-8"))
    labels = {lab for lab, _c in EV.CLIP_VARIANTS}
    assert set(summary["clip_sensitivity"]["clips"]) == labels
    for lab in labels:
        g = summary["clip_sensitivity"]["clips"][lab]["global"]
        assert {"hit1", "hit3", "mrr", "ap3", "marker_rank_median", "n"} <= set(g)
    assert "LOCA_high" in summary["clip_sensitivity"]["flips"]["templates"]


def test_evaluate_rejects_missing_topology(monkeypatch, tmp_path):
    run_id = _tmp_run(monkeypatch, tmp_path)
    (tmp_path / run_id / "tracing" / "topology_LOCA_low.json").unlink()
    with pytest.raises(FileNotFoundError):
        EV.evaluate(run_id, classes=["LOCA"], groups=["low"])

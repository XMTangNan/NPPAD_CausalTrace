"""v2.3 D 阶段（辅助证据接线）单测：K2 三态 / K3 模板覆盖 / 每类公差 / 偏离度 / 公差执行器。

覆盖验收点：
- D5：match_fault_template 命中/反向/缺失计数与 coverage，未知类 available=False；
- D4：LOCA sev-1 主窗切片三态计数之和 = 适用关系数；变量缺失 → undecidable；
- D2：V23-CODE-016 修正后 excluded 只含本类 marker + 安全动作变量（不含 RHRD/PPM），
  thresholds 逐变量带 sigma_floor 与来源；
- D9：偏离度 d 截断到 10、Z 病态/饱和变量被跳过（合成 tolerance + 合成 df）；
- D2 执行器：LOCA 单类小样本落 tmp_path，产物带 run_id/schema/source。
"""
import json

import numpy as np
import pandas as pd
import pytest

from rca.rca_config_v21 import SAFETY_STEP_VARS
from rca.v21 import artifact
from rca.v21 import data as D
from rca.v21 import deviation as DEV
from rca.v21 import external_knowledge as EK
from rca.v21 import physics_constraints as PC
from rca.v21 import run_v23_tolerances as RT
from rca.v21 import tolerance as T


def _patch_output(monkeypatch, root):
    """artifact 唯一产物根整体指向 tmp（同名子目录），不污染正式 output。"""
    root.mkdir(parents=True, exist_ok=True)
    subs = {}
    for name in list(artifact.V23_SUBDIRS):
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        subs[name] = d
    monkeypatch.setattr(artifact, "V23_SUBDIRS", subs)
    monkeypatch.setattr(artifact, "V23_OUTPUT", root)
    return subs


def _read(sub_dir, name):
    with open(sub_dir / name, encoding="utf-8") as f:
        return json.load(f)


# ==================== D5：K3 模板覆盖 ====================
def test_match_fault_template_loca_hit_reverse_missing():
    tpl = EK.load_fault_templates("LOCA")
    chain = list(tpl["expected_chain"])
    n_chain = len(chain) - 1
    hit_u, hit_v = chain[0], chain[1]      # 模板同向 → 命中
    rev_u, rev_v = chain[1], chain[2]      # 稳定边反向 → 反向
    edges = [
        {"from": hit_u, "to": hit_v, "direction": "forward", "f_e": 0.9, "dir_e": 0.95},
        {"from": rev_v, "to": rev_u, "direction": 1, "f_e": 0.9, "dir_e": 0.95},
    ]
    res = EK.match_fault_template("LOCA", edges, chain)

    assert res["available"] is True
    assert res["template_code"] == "LOCA"
    assert res["expected_chain"] == chain
    assert res["n_chain_edges"] == n_chain
    assert res["n_matched"] == 1
    assert res["n_reversed"] == 1
    assert res["coverage"] == pytest.approx(round(1 / n_chain, 4))
    assert {"template": f"{rev_u}->{rev_v}", "stable": f"{rev_v}->{rev_u}"} \
        in res["reversed_edges"]
    # 其余模板链边缺失
    assert len(res["missing_edges"]) == n_chain - 2
    assert all(m["reason"] == "no_stable_edge" for m in res["missing_edges"])
    assert res["verification"] and res["source"]

    # direction="backward"/-1 与端点倒置等价
    res2 = EK.match_fault_template(
        "LOCA", [{"from": rev_u, "to": rev_v, "direction": -1}], chain)
    assert res2["n_reversed"] == 1 and res2["n_matched"] == 0


def test_match_fault_template_unknown_code_not_fabricated():
    res = EK.match_fault_template("NOPE", [], [])
    assert res["available"] is False
    assert "reason" in res and "NOPE" in res["reason"]


def test_match_fault_template_missing_variable_reason():
    tpl = EK.load_fault_templates("LOCA")
    chain = list(tpl["expected_chain"])
    res = EK.match_fault_template("LOCA", [], [chain[0]])  # 只给链首变量
    reasons = {m["reason"] for m in res["missing_edges"]}
    assert "variable_not_in_model" in reasons


# ==================== D4：K2 三态 ====================
def test_k2_loca_sev1_main_window_tri_state_sum():
    df = D.load_trajectory("LOCA", 1)
    win = PC.main_window("LOCA")
    res = PC.evaluate_all_relations("LOCA", df, severity=1)

    assert res["window"] == [float(win[0]), float(win[1])]
    assert res["n_points"] == len(D.window_slice(df, win[0], win[1]))
    assert res["n_relations_applicable"] > 0
    assert (res["n_violation"] + res["n_not_violated"] + res["n_undecidable"]
            == res["n_relations_applicable"])
    assert all(r["status"] in (PC.VIOLATION, PC.NOT_VIOLATED, PC.UNDECIDABLE)
               for r in res["relations"])
    assert "辅助证据" in res["note"]


def test_k2_missing_variables_undecidable():
    df = pd.DataFrame({"TIME": [300, 400, 500, 600]})
    res = PC.evaluate_all_relations("LOCA", df, severity=1)
    assert res["n_relations_applicable"] == res["n_undecidable"]
    assert res["n_violation"] == 0


def test_k2_evaluate_groups_uses_group_max_severity():
    groups = PC.evaluate_groups("LOCA")["groups"]
    for g, res in groups.items():
        assert res["group"] == g
        assert res["severity"] == PC.representative_severity("LOCA", g)
        assert (res["n_violation"] + res["n_not_violated"] + res["n_undecidable"]
                == res["n_relations_applicable"])


# ==================== D2：每类公差 ====================
def test_tolerance_excluded_only_own_marker_and_safety():
    tol = T.compute_tolerance("LOCA")
    excluded = set(tol["excluded_vars"])
    # V23-CODE-016：只排除本类 marker(WLR) + 安全动作变量
    assert excluded == {"WLR"} | (set(SAFETY_STEP_VARS) & excluded)
    assert "RHRD" not in excluded and "PPM" not in excluded
    assert tol["thresholds"], "LOCA 应有可用阈值变量"
    assert tol["quality"]["steady_segment_points"] > 0


def test_tolerance_sigma_floor_per_variable():
    tol = T.compute_tolerance("LOCA")
    for var, th in tol["thresholds"].items():
        assert "sigma_floor" in th, f"{var} 缺 sigma_floor"
        assert th["sigma_floor_source"] in ("physical", "fallback")
        assert th["sigma_robust"] >= th["sigma_floor"] - 1e-12
        assert th["sigma_robust"] >= th["sigma_mad"] - 1e-12


# ==================== D9：偏离度 ====================
def test_deviation_clip_and_skip_pathological(monkeypatch):
    tol = {
        "thresholds": {
            "WLR": {"mu": 0.0, "sigma_robust": 1e-3,
                    "quantiles": {"p50": 0.0}, "z_pathology": False, "saturated": False},
            "P": {"mu": 100.0, "sigma_robust": 1.0,
                  "quantiles": {"p50": 100.0}, "z_pathology": True, "saturated": False},
            "TAVG": {"mu": 50.0, "sigma_robust": 0.5,
                     "quantiles": {"p50": 50.0}, "z_pathology": False, "saturated": True},
            "VOL": {"mu": 10.0, "sigma_robust": 1.0,
                    "quantiles": {"p50": 10.0}, "z_pathology": False, "saturated": False},
        }
    }
    df = pd.DataFrame({"TIME": [0, 1, 2],
                       "WLR": [0.0, 0.0, 5.0],
                       "P": [100.0, 900.0, 100.0],
                       "TAVG": [50.0, 900.0, 50.0],
                       "VOL": [10.0, 11.5, 10.0]})
    monkeypatch.setattr(D, "load_trajectory", lambda k, s: df)

    dev = DEV.compute_deviation("LOCA", 100, tol)
    assert set(dev["deviations"]) == {"WLR", "VOL"}   # Z 病态/饱和被跳过
    assert dev["deviations"]["WLR"]["d"] == 10.0      # 截断到 10
    assert dev["deviations"]["VOL"]["d"] == pytest.approx(1.5)
    assert dev["level_counts"]["severe"] == 1
    assert sum(dev["level_counts"].values()) == dev["n_variables"] == 2


# ==================== D2 执行器端到端（小样本）====================
def _synthetic_sev1(n=140):
    t = np.arange(n, dtype=float) * 10.0
    base = np.sin(np.arange(n) * 0.1) * 1e-3
    return pd.DataFrame({
        "TIME": t,
        "WLR": 0.0,
        "WHPI": 1.5 + base,
        "WECS": 0.0,
        "RHRD": 0.0,
        "PPM": 0.0,
        "VOL": 1000.0 + base,
        "P": 155.0 + base * 10,
        "TAVG": 290.0 + base * 5,
        "THA": 290.0 + base * 5,
    })


def test_run_v23_tolerances_loca_end2end(tmp_path, monkeypatch):
    subs = _patch_output(monkeypatch, tmp_path / "d2")
    monkeypatch.setattr(D, "load_trajectory", lambda k, s: _synthetic_sev1())

    run_id = RT.main(["--classes", "LOCA", "--no-manifest"])
    doc = _read(subs["tolerances"], "tolerance_LOCA.json")

    assert doc["run_id"] == run_id and run_id.startswith("v23d-")
    assert doc["schema"] and doc["artifact"] == "tolerance_LOCA"
    assert doc["accident_type"] == "LOCA"
    assert doc["source"].endswith("/LOCA/LOCA_1.csv")
    assert doc["thresholds"] and doc["quality"]["quality_pass"] in (True, False)
    assert set(doc["excluded_vars"]) <= {"WLR"} | set(SAFETY_STEP_VARS)

    skipped = _read(subs["tolerances"], "skipped_classes.json")
    assert skipped["run_id"] == run_id
    assert skipped["n_missing"] >= 1
    assert skipped["entries"] and all(e["status"] == "data_missing"
                                      for e in skipped["entries"])

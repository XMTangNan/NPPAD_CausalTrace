"""v2.3 B 阶段数据层单测：B4 数据审计 / B5 Z 病态 / B6 变点检测。

运行：cd Code && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_v23_data_audit.py -q
说明：只断言结构与判定逻辑，不断言具体数值型结论（变点位置等属数据事实，见审计产物）。
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from causalrca.io import artifact
from causalrca.data.loader import (
    ClassDataMissing, ensure_class_available, list_class_severities,
)
from causalrca.data.audit import (
    audit_class_data, cusum_changepoints, pelt_dp_changepoints,
    run_audit, sigma_floor_for, variable_z_profile,
)

TOP_KEYS = {"schema", "accident_type", "group", "run_id", "source"}


# ==================== B6 纯函数：CUSUM / DP ====================
def _three_steps():
    return np.r_[np.zeros(30), np.full(30, 10.0), np.zeros(30)]


def test_cusum_and_dp_locate_three_step_boundaries():
    x = _three_steps()
    for cps in (cusum_changepoints(x, threshold=3.0),
                pelt_dp_changepoints(x, penalty=1.0)):
        assert cps, "三段阶梯必须检出变点"
        assert any(abs(i - 30) <= 1 for i in cps), cps
        assert any(abs(i - 60) <= 1 for i in cps), cps


def test_changepoints_constant_and_short_series():
    assert cusum_changepoints(np.ones(60)) == []
    assert pelt_dp_changepoints(np.ones(60)) == []
    assert cusum_changepoints([1.0, 2.0, 3.0]) == []
    assert pelt_dp_changepoints([1.0, 2.0, 3.0]) == []


# ==================== B5 纯函数：σ_floor / Z 病态 ====================
def test_sigma_floor_physical_and_fallback():
    bar = sigma_floor_for("P", 100.0)              # ENTITY_TABLE: P -> bar
    assert bar["floor"] == pytest.approx(0.5)
    assert bar["source"] == "physical"

    unknown = sigma_floor_for("NOT_A_REAL_VAR", 100.0)   # 无 unit 登记
    assert unknown["source"] == "fallback"
    assert unknown["floor"] == pytest.approx(1.0)        # 1% * |100|


def test_variable_z_profile_flags_pathology():
    rng = np.random.default_rng(7)
    base = 100.0 + rng.normal(0, 0.01, 200)         # μ=100, σ=0.01 → σ/|μ|=1e-4
    main = np.r_[np.full(100, 100.0), np.full(20, 1100.0)]   # 主窗 +1000 偏移
    p = variable_z_profile("P", base, main)
    assert p["potential_pathology"] is True
    assert p["sigma_floor_source"] == "physical"
    assert p["max_abs_z_raw"] > 100
    assert p["z_pathology"] is True
    assert p["max_abs_z_clipped"] <= 10.0


# ==================== B4 数据审计（真实数据）====================
def test_b4_loca_has_100_trajectories_and_all_columns():
    sevs = list_class_severities("LOCA")
    assert sevs == list(range(1, 101))
    audit = audit_class_data("LOCA", sevs)
    assert audit["n_trajectories"] == 100
    assert audit["column_check"]["missing"] == []
    assert audit["severity_missing_vs_2_100"] == []
    for key in TOP_KEYS:
        assert key in audit
    assert isinstance(audit["checks"], list) and "pass" in audit


def test_data_missing_class_raises():
    with pytest.raises(ClassDataMissing) as exc:
        ensure_class_available("SLBIC")
    err = exc.value
    assert err.code == "SLBIC"
    assert err.status == "data_missing"
    assert "SLBIC" in str(err) and "data_missing" in str(err)
    assert err.expected_dir.name == "SLBIC"


# ==================== 端到端：三份产物落盘 ====================
def test_run_audit_loca_writes_three_reports(tmp_path, monkeypatch):
    # 单测不得污染正式产物目录：把 run 根重定向到 tmp_path（产物落 <tmp>/<run_id>/audit）
    monkeypatch.setattr(artifact, "V23_RUNS", tmp_path)
    monkeypatch.setattr(artifact, "_RUN_ID", "pytest-v23-data-audit")
    summary = run_audit("LOCA", run_id="pytest-v23-data-audit")
    audit_dir = artifact.subdir_path("audit", "pytest-v23-data-audit")
    names = ["data_audit_LOCA.json", "z_pathology_audit_LOCA.json",
             "changepoint_report_LOCA.json"]
    for name in names:
        path = audit_dir / name
        assert path.exists(), path
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        assert TOP_KEYS <= set(payload), (name, set(payload))
        assert payload["accident_type"] == "LOCA"
        assert payload["run_id"] == "pytest-v23-data-audit"
        assert payload["source"].endswith("/LOCA")
    assert summary["n_trajectories"] == 100
    assert set(summary["paths"]) == {"data_audit", "z_pathology_audit",
                                     "changepoint_report"}

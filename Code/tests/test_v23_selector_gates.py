"""V3 / V5 门控判定（V23-DESIGN-031/032）单元测试。

V0–V7 是**变量选取阶段的质量门控编号**，与版本号 v2.1 / v2.3 无关。
本文件只测判定函数本身，不依赖真实数据与因果发现。
"""
import pytest

from causalrca.features import variable_selector as VS


# ==================== V5：有效集语义 ====================
def _drop(var, retained, corr, stage="stage3_duplicate"):
    return {
        "var": var,
        "retained": retained,
        "pair": sorted([retained, var]),
        "corr": corr,
        "stage": stage,
    }


def test_v5_duplicate_twin_counts_as_covered():
    """重复测点剔除一路、保留另一路 → 有效集覆盖，V5 判 pass。"""
    ok, note = VS.classify_v5(
        key_responses=["PSGA", "LSGA", "RM2"],          # 去重后的有效集
        key_responses_configured=["PSGA", "PSGB", "LSGA", "LSGB", "RM2"],
        selected=["PSGA", "LSGA", "RM2", "TAVG"],
        dropped_key_response=[_drop("PSGB", "PSGA", 0.999916),
                              _drop("LSGB", "LSGA", 0.994073)],
    )
    assert ok is True
    # note 必须写清 pair / 相关系数 / 保留哪一路 / 剔除阶段
    assert "PSGA/PSGB" in note and "0.999916" in note
    assert "保留 PSGA 剔除 PSGB" in note
    assert "@stage3_duplicate" in note
    assert "3/5 路" in note


def test_v5_missing_non_duplicate_response_still_fails():
    """非重复测点的关键响应缺失 → 仍判 False（放宽仅限等价代表）。"""
    ok, note = VS.classify_v5(
        key_responses=["PSGA", "LSGA", "RM2"],
        key_responses_configured=["PSGA", "PSGB", "LSGA", "LSGB", "RM2"],
        selected=["PSGA", "LSGA"],                       # RM2 缺失
        dropped_key_response=[_drop("PSGB", "PSGA", 0.999916),
                              _drop("LSGB", "LSGA", 0.994073)],
    )
    assert ok is False
    assert "缺失 ['RM2']" in note


def test_v5_without_dedupe_is_plain_pass():
    ok, note = VS.classify_v5(
        key_responses=["P", "TAVG", "VOL"], key_responses_configured=["P", "TAVG", "VOL"],
        selected=["P", "TAVG", "VOL", "WECS"], dropped_key_response=[])
    assert ok is True
    assert "无重复测点剔除" in note


# ==================== V3：豁免式 pass ====================
def test_v3_protected_high_vif_is_exempted_not_failed():
    """保护变量 VIF 远超阈值 → pass_with_exemption，且逐项披露。"""
    status, note, exempt = VS.classify_v3(
        vif_all={"P": 2.0, "TAVG": 3.1, "VOL": 2161.6, "WECS": 1.2},
        selected=["P", "TAVG", "VOL", "WECS"],
        protected=["P", "VOL"],            # VOL 是 forced 关键响应
        forced=["P", "VOL"],
        subsystem_reps=[],
    )
    assert status == "pass_with_exemption"
    assert "豁免不等于通过" in note
    assert "max_nonprot=3.1" in note
    assert [e["var"] for e in exempt] == ["P", "VOL"]
    vol = next(e for e in exempt if e["var"] == "VOL")
    assert vol["vif"] == pytest.approx(2161.6, abs=0.1)
    assert vol["vif_ge_threshold"] is True
    assert "forced" in vol["reason"]


def test_v3_non_protected_offender_fails():
    """非保护变量 VIF ≥ 阈值 → fail，即使保护变量也高。"""
    status, note, _ = VS.classify_v3(
        vif_all={"P": 600.0, "TAVG": 700.0, "VOL": 2161.6},
        selected=["P", "TAVG", "VOL"],
        protected=["VOL"], forced=["VOL"], subsystem_reps=[],
    )
    assert status == "fail"
    assert "'P': 600.0" in note and "'TAVG': 700.0" in note


def test_v3_clean_pass_has_no_exemption():
    status, note, exempt = VS.classify_v3(
        vif_all={"A": 1.5, "B": 2.0, "C": 3.0},
        selected=["A", "B", "C"], protected=[], forced=[], subsystem_reps=[])
    assert status == "pass"
    assert exempt == []
    assert "无保护变量豁免" in note


def test_v3_na_requires_reason():
    status, note, _ = VS.classify_v3(
        vif_all={}, selected=["A", "B"], protected=[], forced=[], subsystem_reps=[],
        vif_computed=False)
    assert status == "n/a" and "无法计算" in note
    status, note, _ = VS.classify_v3(
        vif_all={"A": 1.0, "B": 1.0}, selected=["A", "B"],
        protected=[], forced=[], subsystem_reps=[])
    assert status == "n/a" and "<3" in note


def test_v3_exempt_reason_marks_subsystem_rep():
    _, _, exempt = VS.classify_v3(
        vif_all={"A": 1.0, "B": 1.0, "C": 900.0},
        selected=["A", "B", "C"], protected=["C"], forced=[], subsystem_reps=["C"])
    assert "subsystem_rep" in exempt[0]["reason"]

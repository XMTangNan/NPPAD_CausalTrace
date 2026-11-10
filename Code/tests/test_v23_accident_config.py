"""v2.3 A 阶段单测：12 类事故注册表（accident_config）与其派生契约。

覆盖（STRUCTURE.md §4 / 方案 §4.1/§4.3/附录 A）：
1) 12 类齐全、顺序固定、6 个机理组均有成员；
2) 每类必需字段齐全、字段取值自洽（marker=trigger_vars[0]、marker_type/severity_unit 合法）；
3) 数据状态如实登记：ready ≥20 且目录存在、partial 0<n<20、data_missing n==0 且目录不存在；
4) rca_config_v21 仅派生不另立列表：ACCIDENT_TYPES 仍为 3 类兼容、ALL 12 类、
   MARKER_COLS 只收专用指示器（不含 WSTA/WSTB/RHRD/PPM/TBLD/WLD 过程量）；
5) variable_selector 兼容字典覆盖 12 类，且 pilot 三类保持 v2.1 内联集合（SGATR 含 PSGB/LSGB）；
6) 所有触发/关键响应变量名都在 knowledge.ENTITY_TABLE 中（防拼写错误）。

可直接 python tests/test_v23_accident_config.py 运行（不依赖 pytest）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from causalrca.config import accident_config as AC
from causalrca.evidence.knowledge import ENTITY_TABLE
from rca import rca_config_v21 as CFG
from causalrca.features import variable_selector as VS

EXPECTED_ORDER = [
    "LOCA", "LOCAC", "SLBIC", "SLBOC", "SGATR", "SGBTR",
    "RW", "RI", "FLB", "MD", "LR", "LLB",
]
REQUIRED_FIELDS = [
    "code", "name_cn", "group", "marker", "trigger_vars", "key_responses",
    "t_inject", "main_window", "learn_window", "baseline_window",
    "severity_bands", "n_trajectories", "status", "data_dir", "notes",
    "n_trajectories_design", "marker_type", "severity_unit",
]
PROCESS_MARKERS_FORBIDDEN_IN_MARKER_COLS = [
    "WSTA", "WSTB", "RHRD", "PPM", "TBLD", "WLD",
]


# ==================== 1. 规模与顺序 ====================
def test_twelve_classes_fixed_order():
    assert AC.ACCIDENT_ORDER == EXPECTED_ORDER
    assert AC.ALL_CODES == EXPECTED_ORDER
    assert len(AC.REGISTRY) == 12
    assert list(AC.REGISTRY) == EXPECTED_ORDER


def test_six_groups_all_populated():
    groups = {cfg["group"] for cfg in AC.REGISTRY.values()}
    assert groups == {1, 2, 3, 4, 5, 6}, groups
    for g in range(1, 7):
        members = [c for c in EXPECTED_ORDER if AC.REGISTRY[c]["group"] == g]
        assert members, f"组{g} 无成员"
    # 方案 §4.3 组内成员固定
    assert [c for c in EXPECTED_ORDER if AC.REGISTRY[c]["group"] == 4] == ["RW", "RI", "MD"]


def test_unknown_code_raises_keyerror():
    for fn in (AC.get_accident, AC.key_responses, AC.trigger_vars, AC.marker_of):
        with pytest.raises(KeyError):
            fn("NOT_A_CLASS")


# ==================== 2. 字段完整与自洽 ====================
def test_required_fields_present_and_typed():
    for code, cfg in AC.REGISTRY.items():
        missing = [f for f in REQUIRED_FIELDS if f not in cfg]
        assert not missing, f"{code} 缺字段 {missing}"
        assert cfg["code"] == code
        assert isinstance(cfg["name_cn"], str) and cfg["name_cn"]
        assert isinstance(cfg["group"], int) and 1 <= cfg["group"] <= 6
        assert isinstance(cfg["t_inject"], float) and cfg["t_inject"] == 110.0
        assert cfg["marker"] == cfg["trigger_vars"][0], f"{code} marker 应为首个触发变量"
        for key in ("main_window", "learn_window", "baseline_window",
                    "severity_bands"):
            assert key in cfg and cfg[key], f"{code}.{key} 为空"
        for key in ("main_window", "learn_window", "baseline_window"):
            lo, hi = cfg[key]
            assert lo < hi, f"{code}.{key} 窗口非法"
        assert cfg["severity_bands"] == {"low": (2, 33), "mid": (34, 66),
                                         "high": (67, 100)}
        assert cfg["marker_type"] in ("indicator", "process")
        assert isinstance(cfg["severity_unit"], str) and cfg["severity_unit"]
        assert isinstance(cfg["n_trajectories_design"], int)
        assert cfg["n_trajectories_design"] > 0
        assert isinstance(cfg["notes"], str) and cfg["notes"]


def test_window_by_group():
    for code, cfg in AC.REGISTRY.items():
        if cfg["group"] == 4:
            assert cfg["main_window"] == (300.0, 1000.0)
            assert cfg["learn_window"] == (60.0, 1000.0)
            assert cfg["baseline_window"] == (520.0, 1030.0)
        else:
            assert cfg["main_window"] == (300.0, 1500.0)
            assert cfg["learn_window"] == (60.0, 1500.0)
            assert cfg["baseline_window"] == (520.0, 2030.0)


def test_marker_type_and_severity_unit_table():
    """方案 §4.1/§4.3：仅专用故障指示器通道为 indicator，其余为过程量。"""
    indicator = {c for c, cfg in AC.REGISTRY.items()
                 if cfg["marker_type"] == "indicator"}
    assert indicator == {"LOCA", "LOCAC", "SGATR", "SGBTR", "FLB"}
    units = {c: cfg["severity_unit"] for c, cfg in AC.REGISTRY.items()}
    assert units["LOCA"] == "% of 100 cm²"
    assert units["SGATR"] == "% of 1 full tube"
    assert units["RW"] == "% (+/-) withdrawn"
    assert units["RI"] == "% (+/-) insertion"
    assert units["MD"] == "% of unborated injection"
    assert units["LR"] == "% of full load rejected"
    assert units["LLB"] == "% of nominal letdown flow"


def test_key_responses_and_trigger_per_appendix_a():
    expected = {
        "LOCA": (["WLR"], ["P", "TAVG", "VOL", "WECS", "WHPI"]),
        "LOCAC": (["WLR"], ["P", "TAVG", "VOL", "WECS", "WHPI"]),
        "SLBIC": (["WSTA", "WSTB"], ["PSGA", "PSGB", "WFWA", "WFWB", "TAVG"]),
        "SLBOC": (["WSTA", "WSTB"], ["PSGA", "PSGB", "WFWA", "WFWB", "TAVG"]),
        "SGATR": (["WTRA"], ["PSGA", "LSGA", "RM2"]),
        "SGBTR": (["WTRB"], ["PSGB", "LSGB", "RM2"]),
        "RW": (["RHRD"], ["PWNT", "TAVG", "P", "QMWT"]),
        "RI": (["RHRD"], ["PWNT", "TAVG", "P", "QMWT"]),
        "FLB": (["WFLB"], ["WFWA", "WFWB", "TAVG", "PWNT", "PSGA"]),
        "MD": (["PPM"], ["PWNT", "TAVG", "P", "QMWT"]),
        "LR": (["TBLD"], ["WFWA", "WFWB", "TAVG", "PWNT", "PSGA"]),
        "LLB": (["WLD"], ["VOL", "P", "LVPZ", "WECS"]),
    }
    for code, (trig, resp) in expected.items():
        assert AC.trigger_vars(code) == trig, code
        assert AC.key_responses(code) == resp, code
        assert AC.marker_of(code) == trig[0], code
    # SGATR 注册表取附录 A 原文，v2.1 额外集合在 notes 留痕
    assert "PSGB" not in AC.key_responses("SGATR")
    assert "PSGB" in AC.REGISTRY["SGATR"]["notes"]


# ==================== 3. 数据状态如实登记 ====================
def test_status_buckets():
    assert AC.READY_CODES == ["LOCA", "SGATR", "FLB"]
    assert AC.PARTIAL_CODES == ["LOCAC"]
    assert len(AC.MISSING_CODES) == 8
    assert set(AC.MISSING_CODES) == {
        "SLBIC", "SLBOC", "SGBTR", "RW", "RI", "MD", "LR", "LLB"}
    assert all(AC.REGISTRY[c]["status"] == "ready" for c in AC.READY_CODES)
    assert all(AC.REGISTRY[c]["status"] == "partial" for c in AC.PARTIAL_CODES)


def test_status_consistent_with_disk():
    for code, cfg in AC.REGISTRY.items():
        path = AC.data_path(code)
        assert cfg["status"] in ("ready", "partial", "data_missing")
        if cfg["status"] == "ready":
            assert cfg["n_trajectories"] >= 20, code
            assert path.is_dir(), f"{code} 目录应存在: {path}"
            n_csv = len(list(path.glob("*.csv")))
            assert cfg["n_trajectories"] == n_csv, f"{code} 登记轨迹数与实际 csv 不符"
        elif cfg["status"] == "partial":
            assert 0 < cfg["n_trajectories"] < 20, code
            assert path.is_dir(), f"{code} 目录应存在（partial）"
            assert cfg["n_trajectories"] == len(list(path.glob("*.csv")))
        else:
            assert cfg["n_trajectories"] == 0, code
            assert not path.exists(), f"{code} 标记 data_missing 但目录存在: {path}"


def test_data_missing_notes_state_skip_and_no_fabrication():
    for code in AC.MISSING_CODES:
        notes = AC.REGISTRY[code]["notes"]
        assert "skipped_classes.json" in notes, code
        assert "禁止编造" in notes, code
        assert "待数据核实" in notes, code


def test_partial_notes_declare_insufficient():
    notes = AC.REGISTRY["LOCAC"]["notes"]
    assert "不足以三分组" in notes and "5" in notes


# ==================== 4. rca_config_v21 派生 ====================
def test_rca_config_derived_constants():
    assert CFG.ACCIDENT_TYPES == ["LOCA", "SGATR", "FLB"]   # v2.1 兼容
    assert CFG.ALL_ACCIDENT_TYPES == EXPECTED_ORDER
    assert CFG.READY_ACCIDENT_TYPES == ["LOCA", "SGATR", "FLB"]
    assert CFG.PARTIAL_ACCIDENT_TYPES == ["LOCAC"]
    assert len(CFG.MISSING_ACCIDENT_TYPES) == 8
    assert CFG.MARKERS == {c: AC.REGISTRY[c]["marker"] for c in EXPECTED_ORDER}


def test_marker_cols_only_indicator_channels():
    assert CFG.MARKER_COLS == ["WLR", "WTRA", "WTRB", "WFLB"]
    for m in PROCESS_MARKERS_FORBIDDEN_IN_MARKER_COLS:
        assert m not in CFG.MARKER_COLS, f"过程量 {m} 不得进入 MARKER_COLS"
    # MARKER_COLS 必须是注册表中 marker_type=="indicator" 的去重保序集合
    expected = list(dict.fromkeys(
        AC.REGISTRY[c]["marker"] for c in EXPECTED_ORDER
        if AC.REGISTRY[c]["marker_type"] == "indicator"))
    assert CFG.MARKER_COLS == expected


# ==================== 5. variable_selector 兼容层 ====================
def test_selector_key_responses_cover_all_and_keep_v21():
    assert set(VS.ACCIDENT_KEY_RESPONSES) == set(EXPECTED_ORDER)
    assert len(VS.ACCIDENT_KEY_RESPONSES) == 12
    # pilot 三类保持 v2.1 实际内联集合（避免改变已验收 V1-V7）
    assert VS.ACCIDENT_KEY_RESPONSES["LOCA"] == ["P", "TAVG", "VOL", "WECS", "WHPI"]
    assert VS.ACCIDENT_KEY_RESPONSES["SGATR"] == ["PSGA", "PSGB", "LSGA", "LSGB", "RM2"]
    assert {"PSGB", "LSGB"} <= set(VS.ACCIDENT_KEY_RESPONSES["SGATR"])
    assert VS.ACCIDENT_KEY_RESPONSES["FLB"] == [
        "WFWA", "WFWB", "TAVG", "PWNT", "PSGA", "PSGB"]
    # 非 pilot 类直接取注册表附录 A 原文
    assert VS.ACCIDENT_KEY_RESPONSES["LLB"] == AC.key_responses("LLB")


def test_selector_accessors_consistent_with_registry():
    for code in EXPECTED_ORDER:
        assert VS.get_trigger_var(code) == CFG.MARKERS[code] == AC.marker_of(code)
        assert VS.get_key_responses(code) == VS.ACCIDENT_KEY_RESPONSES[code]


# ==================== 6. 变量名必须存在于本体实体表 ====================
def test_all_variables_registered_in_entity_table():
    for code, cfg in AC.REGISTRY.items():
        for v in cfg["trigger_vars"] + cfg["key_responses"]:
            assert v in ENTITY_TABLE, f"{code} 变量 {v} 不在 knowledge.ENTITY_TABLE"
    for code, resp in VS.ACCIDENT_KEY_RESPONSES.items():
        for v in resp:
            assert v in ENTITY_TABLE, f"变量选取兼容字典 {code} 变量 {v} 未登记"

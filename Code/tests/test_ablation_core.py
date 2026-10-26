"""§13.11 核心消融单测（独立运行，不依赖数据文件）。

可直接 python tests/test_ablation_core.py 运行，也可被 pytest 收集。
覆盖：full 与 pipeline.score_trajectory 对拍；−E1 分量置 0 不重归一；
−ANM/−LiNGAM 引擎强制；−E3-base/−E3-path 拓扑通道；macro_f1=null 不崩。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKER_COLS, MARKERS, W_FULL,
)
from rca.v21.pipeline import score_trajectory, compute_metrics, apply_minmax
from rca.v21.ablation_core import score_ablation, ABLATIONS

V_THR = ["X1", "X2"]


def _sig(marker, cA_x1, cL_x1, reach, piA=0.92, piL=0.9, est_A=True):
    return {
        "variables": V_THR + [marker], "marker": marker,
        "cL": {"X1": {"cL": cL_x1, "piL": piL},
               "X2": {"cL": 0.0, "piL": 0.0}},
        "cA": {"X1": {"cA": cA_x1, "piA": piA, "estimable": est_A},
               "X2": {"cA": 0.0, "piA": 0.0, "estimable": False}},
        "frontier": {"layers": {"1": list(reach)}, "reach": list(reach),
                     "depth_of": {v: 1 for v in reach}},
        "group_pattern": {"variables": V_THR,
                          "template_d": [1.0, 0.0], "template_over": ["X1"]},
    }


def _world(cA_x1=1.0, cL_x1=1.0, reach=("X1",), piA=0.92, piL=0.9, est_A=True):
    sigs, gates = {}, {}
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            sigs[(k, g)] = _sig(MARKERS[k], cA_x1, cL_x1, reach, piA, piL, est_A)
            gates[(k, g)] = {"edge": {"X1": 1.0, "X2": 1.0}, "template": 1.0}
    baseline = {"mu": {"X1": 0.0, "X2": 0.0},
                "sigma": {"X1": 1.0, "X2": 1.0}}
    calibration = {
        "tau": 0.2, "delta": 0.05,
        "minmax": {
            "E2": {k: {"min": 0.0, "max": 2.0, "zero_range": False}
                   for k in ACCIDENT_TYPES},
            "E3_path": {k: {"min": 0.0, "max": 2.0, "zero_range": False}
                        for k in ACCIDENT_TYPES},
        },
    }
    eps = {"LOCA": 1.0, "SGATR": 0.5, "FLB": 2.0}
    return sigs, gates, baseline, calibration, eps


def test_full_matches_pipeline():
    sigs, gates, baseline, calibration, eps = _world()
    T = 121
    Xm = np.column_stack([np.full(T, 5.0), np.zeros(T)])
    ref = score_trajectory(Xm, V_THR, "MASKED", V_THR, sigs, gates,
                           baseline, calibration)
    ab = score_ablation(Xm, V_THR, "MASKED", V_THR, sigs, gates,
                        baseline, calibration, None, "full")
    assert ab == ref

    Xf = np.column_stack([np.full(T, 5.0), np.zeros(T),
                          np.full(T, 5.0), np.zeros(T), np.zeros(T)])
    cols_f = V_THR + list(MARKER_COLS)
    ref_f = score_trajectory(Xf, cols_f, "FULL", V_THR, sigs, gates,
                             baseline, calibration, epsilon=eps)
    ab_f = score_ablation(Xf, cols_f, "FULL", V_THR, sigs, gates,
                          baseline, calibration, eps, "full")
    assert ab_f == ref_f


def test_E1_zeroed_full_equivalent_masked():
    sigs, gates, baseline, calibration, eps = _world()
    T = 121
    Xf = np.column_stack([np.full(T, 5.0), np.zeros(T),
                          np.full(T, 5.0), np.zeros(T), np.zeros(T)])
    cols_f = V_THR + list(MARKER_COLS)
    full = score_ablation(Xf, cols_f, "FULL", V_THR, sigs, gates,
                          baseline, calibration, eps, "full")
    e1 = score_ablation(Xf, cols_f, "FULL", V_THR, sigs, gates,
                        baseline, calibration, eps, "E1")
    # E1 向量仍计算并落盘，但融合分中 E1 贡献=0（不重归一：E2/E3 权重保持 0.35）
    assert e1["e1"] == full["e1"] == {"LOCA": 1.0, "SGATR": 0.0, "FLB": 0.0}
    for k in ACCIDENT_TYPES:
        assert abs(e1["fusion"][k]
                   - (full["fusion"][k] - W_FULL["E1"] * full["e1"][k])) < 1e-12

    # MASKED 无 E1：−E1 与 full 逐字段等价
    Xm = np.column_stack([np.full(T, 5.0), np.zeros(T)])
    full_m = score_ablation(Xm, V_THR, "MASKED", V_THR, sigs, gates,
                            baseline, calibration, None, "full")
    e1_m = score_ablation(Xm, V_THR, "MASKED", V_THR, sigs, gates,
                          baseline, calibration, None, "E1")
    assert e1_m == full_m


def test_engine_ablation_uses_only_specified_engine():
    # cA 与 cL 符号相反：X1 的 cL=+1、cA=−1；d[X1]>0
    sigs, gates, baseline, calibration, _ = _world(cA_x1=-1.0, cL_x1=1.0)
    T = 121
    X = np.column_stack([np.full(T, 5.0), np.zeros(T)])
    res_anm = score_ablation(X, V_THR, "MASKED", V_THR, sigs, gates,
                             baseline, calibration, None, "ANM")
    res_lin = score_ablation(X, V_THR, "MASKED", V_THR, sigs, gates,
                             baseline, calibration, None, "LiNGAM")
    res_full = score_ablation(X, V_THR, "MASKED", V_THR, sigs, gates,
                              baseline, calibration, None, "full")
    # −ANM 只用 L：cL[X1]=+1 与 d>0 同号 → E3_base=1、E3_path=1
    assert res_anm["class_raw"]["LOCA"]["e3_base"] == 1.0
    assert res_anm["class_raw"]["LOCA"]["e3_path"] == 1.0
    # −LiNGAM 只用 A：cA[X1]=−1 与 d>0 反号 → E3_base=0、E3_path=0.5
    assert res_lin["class_raw"]["LOCA"]["e3_base"] == 0.0
    assert res_lin["class_raw"]["LOCA"]["e3_path"] == 0.5
    # full 在 piA>=0.7 时优先 A → 与 −LiNGAM 同
    assert res_full["class_raw"] == res_lin["class_raw"]
    # 两变体得分不同
    assert res_anm["fusion"] != res_lin["fusion"]


def test_topology_ablation_channel():
    # reach 空 → e3_path 退化为 nan→类地板 0；e3_base 正常 1.0
    sigs, gates, baseline, calibration, _ = _world(cA_x1=1.0, cL_x1=1.0, reach=())
    T = 121
    X = np.column_stack([np.full(T, 5.0), np.zeros(T)])
    full = score_ablation(X, V_THR, "MASKED", V_THR, sigs, gates,
                          baseline, calibration, None, "full")
    ebase = score_ablation(X, V_THR, "MASKED", V_THR, sigs, gates,
                           baseline, calibration, None, "E3-base")
    epath = score_ablation(X, V_THR, "MASKED", V_THR, sigs, gates,
                           baseline, calibration, None, "E3-path")
    mm = calibration["minmax"]["E3_path"]["LOCA"]
    # full 与 −E3-base 都用 path 通道 → 归一化 E3 分量一致
    assert full["class_norm"] == ebase["class_norm"]
    assert full["class_norm"]["e3_path"]["LOCA"] == apply_minmax(
        full["class_raw"]["LOCA"]["e3_path"], mm)
    # −E3-path 只用 base 通道
    assert epath["class_norm"]["e3_path"]["LOCA"] == apply_minmax(
        epath["class_raw"]["LOCA"]["e3_base"], mm)
    # base 与 path 数值确实不同
    assert epath["class_raw"]["LOCA"]["e3_base"] != epath["class_raw"]["LOCA"]["e3_path"]
    assert full["class_raw"]["LOCA"]["e3_base"] == 1.0
    assert full["class_raw"]["LOCA"]["e3_path"] == 0.0


def test_compute_metrics_null_macro_f1_no_crash():
    rows = [
        {"true": "LOCA", "pred": "LOCA", "accepted": True,
         "s1": 0.9, "s2": 0.2, "margin": 0.7, "n_activation": 2},
        {"true": "LOCA", "pred": "LOCA", "accepted": True,
         "s1": 0.8, "s2": 0.2, "margin": 0.6, "n_activation": 2},
        {"true": "SGATR", "pred": "SGATR", "accepted": True,
         "s1": 0.7, "s2": 0.2, "margin": 0.5, "n_activation": 1},
        {"true": "SGATR", "pred": "LOCA", "accepted": True,
         "s1": 0.6, "s2": 0.2, "margin": 0.4, "n_activation": 1},
        {"true": "FLB", "pred": "LOCA", "accepted": True,
         "s1": 0.5, "s2": 0.2, "margin": 0.3, "n_activation": 1},
        {"true": "FLB", "pred": "FLB", "accepted": False,
         "s1": 0.1, "s2": 0.0, "margin": 0.1, "n_activation": 0},
    ]
    m = compute_metrics(rows)
    assert m["macro_f1"] is None
    assert "FLB" in m["macro_f1_null_classes"]
    assert m["per_class"]["FLB"]["f1"] is None
    assert m["n"] == 6 and m["n_accepted"] == 5


def test_ablation_enum_contains_all_eight():
    assert ABLATIONS == ["full", "E1", "E2", "E3", "ANM", "LiNGAM",
                         "E3-base", "E3-path"]


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n全部 {len(fns)} 个消融核心单测通过")


if __name__ == "__main__":
    _run_all()

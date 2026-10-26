"""v2.1 §13.11 三类神经符号/结构消融单测（合成数据，可直接运行或 pytest 收集）。

覆盖：
- −过程本体：σ≈0 但 MAD 正常的变量，μ±3σ 恒激活而 μ±3·MAD 不再恒激活；
- MASKED-strict：融合里 E3 分量恒 0、E3′ 被计算但未进入融合；
- G_k′ 边保留率统计口径。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rca.v21.ablation_symbolic import (
    observe_main_window_mad, score_trajectory_strict, edge_retention,
    aggregate_edge_stats_no_marker,
)
from rca.v21.pipeline import observe_main_window
from rca.v21.inversion import build_group_pattern, signed_median_z


def test_mad_activation_vs_sigma():
    """σ 极小但 MAD 正常：μ±3σ 恒激活，μ±3·MAD 不再恒激活（过程本体过紧的消融效果）。"""
    v_thr = ["V1"]
    cols = ["V1"]
    baseline = {"mu": {"V1": 0.0}, "sigma": {"V1": 0.01}}   # σ≈0
    mad = {"V1": 1.0}                                        # MAD 正常
    T = 121
    X = np.full((T, 1), 2.0)
    obs_sig = observe_main_window(X, cols, v_thr, baseline)
    obs_mad = observe_main_window_mad(X, cols, v_thr, baseline, mad)
    assert "V1" in obs_sig["A"], f"μ±3σ 应恒激活: {obs_sig['A']}"
    assert "V1" not in obs_mad["A"], f"μ±3·MAD 不应激活: {obs_mad['A']}"
    # d 口径不变（σ 尺度），只有 A 换口径
    assert obs_mad["d"][0] == obs_sig["d"][0]


def _synth_sig(v_thr):
    """合成严格签名：cL/frontier 非空，使 E3′ 可算出非零诊断量。"""
    first = v_thr[0]
    frontier = {"layers": {1: [first], 2: [], 3: []},
                "reach": [first], "depth_of": {first: 1}}
    return {
        "variables": list(v_thr),
        "marker": "M",
        "group_pattern": {"variables": list(v_thr),
                          "template_d": [1.0] * len(v_thr),
                          "template_over": [first]},
        # cA 非空：effective_marker_map 以 cA 驱动，E3′ 才有非零诊断量
        "cA": {v: {"cA": 1.0, "piA": 1.0, "estimable": True} for v in v_thr},
        "cL": {v: {"cL": 1.0, "piL": 1.0} for v in v_thr},
        "frontier": frontier,
        "stable_edges": [{"from": "M", "to": first, "f_e": 1.0, "dir_e": 1.0}],
    }


def test_strict_e3_zero_not_fused():
    """MASKED-strict：E3 融合分量恒 0，E3′ 被计算但未进入融合。"""
    v_thr = ["V1", "V2"]
    sig = _synth_sig(v_thr)
    sigs = {(k, g): sig for k in ("LOCA", "SGATR", "FLB")
            for g in ("low", "mid", "high")}
    gates = {(k, g): {"edge": {}, "template": 1.0}
             for k in ("LOCA", "SGATR", "FLB") for g in ("low", "mid", "high")}
    baseline = {"mu": {"V1": 0.0, "V2": 0.0}, "sigma": {"V1": 1.0, "V2": 1.0}}
    calibration = {
        "tau": 0.0, "delta": 0.0,
        "minmax": {"E2": {k: {"min": 0.0, "max": 1.0, "zero_range": False}
                          for k in ("LOCA", "SGATR", "FLB")},
                   "E3_path": {k: {"min": 0.0, "max": 1.0, "zero_range": False}
                               for k in ("LOCA", "SGATR", "FLB")}},
    }
    T = 121
    X = np.zeros((T, 2))
    X[:, 0] = 4.0
    cols = ["V1", "V2"]
    res = score_trajectory_strict(X, cols, "MASKED", v_thr, sigs, gates,
                                  baseline, calibration)
    # E3 融合分量恒 0：fusion = 0.5*e2_norm + 0.5*0
    for k in ("LOCA", "SGATR", "FLB"):
        e2n = res["class_norm"]["e2"][k]
        assert res["e3_component_zero"][k] == 0.0
        assert abs(res["fusion"][k] - 0.5 * e2n) < 1e-12, res["fusion"]
    # E3′ 被计算且非零（合成 cL/frontier 下应有信息），但未进入融合
    assert res["e3p_diagnostic_class_raw"]["LOCA"] == 1.0, res["e3p_diagnostic_class_raw"]
    assert res["e3b_diagnostic_class_raw"]["LOCA"] == 1.0, res["e3b_diagnostic_class_raw"]


def test_edge_retention():
    main_sig = {
        "marker": "M",
        "stable_edges": [
            {"from": "A", "to": "B", "f_e": 1.0, "dir_e": 1.0},
            {"from": "B", "to": "C", "f_e": 1.0, "dir_e": 1.0},
            {"from": "M", "to": "A", "f_e": 1.0, "dir_e": 1.0},
        ],
    }
    strict_sig = {
        "stable_edges": [
            {"from": "A", "to": "B", "f_e": 1.0, "dir_e": 1.0},
            {"from": "C", "to": "B", "f_e": 1.0, "dir_e": 1.0},  # 反向（方向不一致）
        ],
    }
    r = edge_retention(main_sig, strict_sig)
    # 主签名非 marker 稳定边 = A→B, B→C（共 2）
    assert r["main_stable_edges_nonmarker"] == 2
    assert r["undirected_overlap"] == 2        # {A,B} 与 {B,C} 都重合
    assert r["direction_agree"] == 1            # A→B 同向，B→C vs C→B 反向
    assert r["directed_overlap"] == 1
    assert abs(r["direction_consistency"] - 0.5) < 1e-12


def test_aggregate_edge_stats_no_marker():
    variables = ["A", "B"]
    # B[A,B] = A→B：B[1,0]=0.7，B[0,1]=0（单向）
    B_list = []
    for _ in range(4):
        B = np.zeros((2, 2))
        B[1, 0] = 0.7
        B_list.append(B)
    stats = aggregate_edge_stats_no_marker(B_list, variables)
    edges = {(e["from"], e["to"]): e for e in stats["edges"]}
    assert edges[("A", "B")]["f_e"] == 1.0
    assert edges[("A", "B")]["dir_e"] == 1.0
    assert edges[("B", "A")]["f_e"] == 0.0


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n全部 {len(fns)} 个消融单测通过")


if __name__ == "__main__":
    _run_all()

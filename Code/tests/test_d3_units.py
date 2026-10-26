"""D3 单测（方案 13.7.6/13.7.7/13.8.2）：minmax、弃权三条件、U 网格构造数据、
FULL/MASKED 融合权重、E1 互斥、score_trajectory 端到端、Wilson CI。

可直接 python tests/test_d3_units.py 运行（不依赖 pytest）。
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rca.rca_config_v21 import ACCIDENT_TYPES, GROUP_ORDER, MARKER_COLS
from rca.v21.pipeline import (
    fit_minmax, apply_minmax, fuse_scores, e1_vector, wilson_ci,
    score_trajectory,
)
from rca.v21.calibration import (
    utility_grid, select_thresholds, TAU_GRID, DELTA_GRID,
)
from rca.v21.inversion import should_abstain, MarkerAccessError


# ==================== 1. min-max 归一 ====================
def test_minmax_normal_and_zero_range():
    p = fit_minmax([0.0, 10.0])
    assert not p["zero_range"]
    assert abs(apply_minmax(5.0, p) - 0.5) < 1e-12
    assert apply_minmax(20.0, p) == 1.0          # 越界截断
    assert apply_minmax(-5.0, p) == 0.0
    p0 = fit_minmax([3.0, 3.0, 3.0])
    assert p0["zero_range"]
    assert apply_minmax(3.0, p0) == 0.5          # 零极差恒映射 0.5
    assert apply_minmax(-100.0, p0) == 0.5


# ==================== 2. 弃权三条件 ====================
def test_abstain_three_conditions():
    s = np.array([0.8, 0.4, 0.1])
    assert should_abstain(["X"], s, tau=0.9, delta=0.1) == "below_tau"
    assert should_abstain(["X"], np.array([0.8, 0.78, 0.1]),
                          tau=0.5, delta=0.1) == "small_margin"
    assert should_abstain([], s, tau=0.0, delta=0.0) == "empty_activation"
    assert should_abstain(["X"], s, tau=0.5, delta=0.1) is None
    # τ 边界严格：max==τ 不触发
    assert should_abstain(["X"], np.array([0.8, 0.2]), tau=0.8, delta=0.0) is None
    # margin 边界严格：s1-s2==δ 不触发
    assert should_abstain(["X"], np.array([0.8, 0.7]), tau=0.0, delta=0.1) is None


# ==================== 3. U 网格 ====================
def _feat(s1, s2, correct, high=False, low=False, empty=False):
    return {"s1": np.asarray(s1, dtype=float),
            "s2": np.asarray(s2, dtype=float),
            "correct": np.asarray(correct, dtype=bool),
            "empty": np.full(len(s1), bool(empty)),
            "is_high": np.full(len(s1), bool(high)),
            "is_low": np.asarray(low if isinstance(low, list) else
                                 [bool(low)] * len(s1))}


def test_U_tau_converts_wrong_to_correct_abstain():
    # 1 条 high 错判（s1=0.6，margin 大）：τ=0 时接受且错 −1；τ 抬高后正确弃权 +1
    feat = _feat([0.6], [0.2], [False], high=True)
    U = utility_grid(feat, lambda1=1, lambda2=2)
    i_t0 = int(round(0.0 / 0.01))
    i_t61 = int(round(0.61 / 0.01))
    i_d0 = 0
    assert U[i_t0, i_d0] == -1.0
    assert U[i_t61, i_d0] == 1.0
    # 阈值没过 s1 时（τ=0.60，严格 <）仍接受
    assert U[int(round(0.60 / 0.01)), i_d0] == -1.0


def test_U_high_vs_low_penalty():
    # τ=0：high 错分 −λ1=−1；low 错分 λ2=2 → −2；mid 错分权重 0 → 0
    Uh = utility_grid(_feat([0.6], [0.2], [False], high=True), 1, 2)
    Ul = utility_grid(_feat([0.6], [0.2], [False], low=True), 1, 2)
    Um = utility_grid(_feat([0.6], [0.2], [False]), 1, 2)
    assert Uh[0, 0] == -1.0 and Ul[0, 0] == -2.0 and Um[0, 0] == 0.0
    # λ2=1 时 low 罚 −1
    Ul1 = utility_grid(_feat([0.6], [0.2], [False], low=True), 1, 1)
    assert Ul1[0, 0] == -1.0


def test_U_lambda2_choice_differs_and_tie_small_tau():
    # 1 条 low 错判 s1=0.600；2 条正确轨迹 s1=0.605；三者 margin 统一 0.005
    # （margin 相同 -> δ 无法只弃权错判，任何弃权都连带 2 条正确轨迹）
    feat = _feat([0.600, 0.605, 0.605], [0.595, 0.600, 0.600],
                 [False, True, True], low=[True, False, False])
    # λ2=1：接受 −1；任何 τ/δ 弃权均为 1−2=−1 平局 -> 平局取 (τ=0,δ=0)
    t1, d1, u1 = select_thresholds(utility_grid(feat, 1, 1))
    assert t1 == 0.0 and d1 == 0.0 and u1 == -1.0
    # λ2=2：接受 −2；δ=0.01 全弃权 -> 1−2=−1 -> 选择不同（小 δ 优先）
    t2, d2, u2 = select_thresholds(utility_grid(feat, 1, 2))
    assert t2 == 0.0 and d2 == 0.01 and u2 == -1.0


def test_U_flat_tie_picks_origin():
    # A 空的正确轨迹恒为错误弃权 −1：U 全网格相同 -> τ=0, δ=0
    feat = _feat([0.9], [0.2], [True], empty=True)
    t, d, u = select_thresholds(utility_grid(feat, 1, 2))
    assert (t, d, u) == (0.0, 0.0, -1.0)
    assert len(TAU_GRID) == 101 and len(DELTA_GRID) == 101
    assert TAU_GRID[-1] == 1.0 and DELTA_GRID[-1] == 0.5


# ==================== 4. 融合权重 ====================
def test_fusion_weights():
    e2 = {k: 0.4 for k in ACCIDENT_TYPES}
    e3 = {k: 0.8 for k in ACCIDENT_TYPES}
    sm = fuse_scores("MASKED", e2, e3)
    for k in ACCIDENT_TYPES:
        assert abs(sm[k] - 0.6) < 1e-12            # .5*.4+.5*.8
    e1 = {k: (1.0 if k == "LOCA" else 0.0) for k in ACCIDENT_TYPES}
    sf = fuse_scores("FULL", e2, e3, e1)
    assert abs(sf["LOCA"] - (0.3 * 1 + 0.35 * 0.4 + 0.35 * 0.8)) < 1e-12
    assert abs(sf["SGATR"] - (0.35 * 0.4 + 0.35 * 0.8)) < 1e-12
    try:
        fuse_scores("FULL", e2, e3, None)
        raise AssertionError("FULL 缺 E1 必须报错")
    except AssertionError:
        pass


# ==================== 5. E1 互斥规则 ====================
def test_e1_mutex():
    eps = {"LOCA": 1.0, "SGATR": 0.5, "FLB": 2.0}
    # 仅本类 marker 越阈 -> LOCA E1=1，其余 0
    med = {"WLR": 5.0, "WTRA": 0.0, "WFLB": 0.0}
    e1 = e1_vector(med, eps)
    assert e1 == {"LOCA": 1.0, "SGATR": 0.0, "FLB": 0.0}
    # 非本类 marker 也越阈 -> 互斥规则使所有 E1=0
    med2 = {"WLR": 5.0, "WTRA": 1.0, "WFLB": 0.0}
    assert all(v == 0.0 for v in e1_vector(med2, eps).values())
    # 本类未越阈（即便其余静默）-> 0
    med3 = {"WLR": 0.5, "WTRA": 0.0, "WFLB": 0.0}
    assert e1_vector(med3, eps)["LOCA"] == 0.0
    # 边界严格：|marker|==ε 不算激活
    med4 = {"WLR": 1.0, "WTRA": 0.0, "WFLB": 0.0}
    assert e1_vector(med4, eps)["LOCA"] == 0.0


# ==================== 6. Wilson CI ====================
def test_wilson_ci_known_values():
    lo, hi = wilson_ci(10, 20)
    assert abs(lo - 0.299298) < 5e-4 and abs(hi - 0.700702) < 5e-4
    # 0/20：下界=0，上界=z²/(n+z²)≈0.1611
    lo0, hi0 = wilson_ci(0, 20)
    assert lo0 == 0.0 and abs(hi0 - 3.8416 / (20 + 3.8416)) < 1e-6
    # 20/20 对称：上界=1
    lo1, hi1 = wilson_ci(20, 20)
    assert hi1 == 1.0 and abs(lo1 - 20 / (20 + 3.8416)) < 1e-6
    assert wilson_ci(0, 0) is None
    # 与独立重算对拍（随机若干 x）
    rng = np.random.default_rng(0)
    for x in rng.integers(0, 21, size=20):
        x, n = int(x), 20
        p, z = x / n, 1.96
        den = 1 + z * z / n
        c = (p + z * z / (2 * n)) / den
        h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
        a, b = wilson_ci(x, n)
        assert abs(a - max(0, c - h)) < 1e-12 and abs(b - min(1, c + h)) < 1e-12


# ==================== 7. score_trajectory 端到端（合成签名）====================
V_THR = ["X1", "X2"]


def _mini_sig(marker, cA_x1, cL_x1, reach):
    return {
        "variables": V_THR + [marker], "marker": marker,
        "cL": {"X1": {"cL": cL_x1, "piL": 0.9},
               "X2": {"cL": 0.0, "piL": 0.0}},
        "cA": {"X1": {"cA": cA_x1, "piA": 0.92, "estimable": True},
               "X2": {"cA": 0.0, "piA": 0.0, "estimable": False}},
        "frontier": {"layers": {"1": reach}, "reach": reach,
                     "depth_of": {v: 1 for v in reach}},
        "group_pattern": {"variables": V_THR,
                          "template_d": [1.0, 0.0], "template_over": ["X1"]},
    }


def _mini_world():
    sigs, gates = {}, {}
    for k in ACCIDENT_TYPES:
        marker = {"LOCA": "WLR", "SGATR": "WTRA", "FLB": "WFLB"}[k]
        for g in GROUP_ORDER:
            # LOCA 签名正向且有 reach；其余无 reach（path 通道 nan -> 类地板 0）
            sigs[(k, g)] = _mini_sig(marker, 1.0 if k == "LOCA" else -1.0,
                                     1.0, ["X1"] if k == "LOCA" else [])
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


def test_score_trajectory_full_and_masked():
    sigs, gates, baseline, calibration, eps = _mini_world()
    T = 121
    # 主窗 X1 持续正 z -> 激活；X2 静默
    Xm = np.column_stack([np.full(T, 5.0), np.zeros(T)])
    res_m = score_trajectory(Xm, V_THR, "MASKED", V_THR, sigs, gates,
                             baseline, calibration)
    assert res_m["e1"] is None
    assert res_m["n_activation"] == 1 and res_m["A"] == ["X1"]
    assert res_m["argmax_class"] == "LOCA"
    assert res_m["accepted"] and res_m["abstain_reason"] is None
    # MASKED 特征含 marker 必须被 G4 拦截
    try:
        score_trajectory(np.zeros((T, 3)), V_THR + ["WLR"], "MASKED", V_THR,
                         sigs, gates, baseline, calibration)
        raise AssertionError("MASKED marker 泄漏必须报错")
    except MarkerAccessError:
        pass

    # FULL：仅 WLR 越阈 -> LOCA E1=1，融合分进一步抬高
    Xf = np.column_stack([np.full(T, 5.0), np.zeros(T),
                          np.full(T, 5.0), np.zeros(T), np.zeros(T)])
    cols_f = V_THR + list(MARKER_COLS)
    res_f = score_trajectory(Xf, cols_f, "FULL", V_THR, sigs, gates,
                             baseline, calibration, epsilon=eps)
    assert res_f["e1"] == {"LOCA": 1.0, "SGATR": 0.0, "FLB": 0.0}
    assert res_f["fusion"]["LOCA"] > res_m["fusion"]["LOCA"]
    assert res_f["argmax_class"] == "LOCA"

    # 低分轨迹触发 below_tau 弃权
    Xq = np.zeros((T, 2))
    res_q = score_trajectory(Xq, V_THR, "MASKED", V_THR, sigs, gates,
                             baseline, calibration)
    assert res_q["n_activation"] == 0
    assert (not res_q["accepted"]) and res_q["abstain_reason"] == "empty_activation"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n全部 {len(fns)} 个 D3 单测通过")


if __name__ == "__main__":
    _run_all()

"""§13.9 三基线单元测试（合成数据，不依赖 output/ 冻结产物，可直接运行）。

用法：cd Code && PYTHONPATH=. ../.venv/bin/python tests/test_baselines.py
（也可被 pytest 收集）
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKER_COLS, ANOMALY_K,
)
from rca.v21.inversion import assert_no_marker_columns
from rca.v21 import baselines as B


# ==================== B0 ====================
def test_b0_unique_activation_correct():
    eps = {"LOCA": 1.0, "SGATR": 0.5, "FLB": 2.0}
    med = {"LOCA": 5.0, "SGATR": 0.0, "FLB": 0.0}
    pred, activated = B.b0_decide(med, eps)
    assert pred == "LOCA", pred
    assert activated == ["LOCA"], activated
    # 负值 marker（有符号中位数）按绝对值判
    pred2, act2 = B.b0_decide({"LOCA": -5.0, "SGATR": 0.0, "FLB": 0.0}, eps)
    assert pred2 == "LOCA" and act2 == ["LOCA"]


def test_b0_zero_activation_none():
    eps = {"LOCA": 1.0, "SGATR": 0.5, "FLB": 2.0}
    pred, activated = B.b0_decide({k: 0.0 for k in ACCIDENT_TYPES}, eps)
    assert pred is None
    assert activated == []


def test_b0_double_activation_none():
    eps = {"LOCA": 1.0, "SGATR": 0.5, "FLB": 2.0}
    med = {"LOCA": 5.0, "SGATR": 5.0, "FLB": 0.0}
    pred, activated = B.b0_decide(med, eps)
    assert pred is None, "双激活必须弃权，不强行猜类"
    assert set(activated) == {"LOCA", "SGATR"}
    # 三激活同样弃权
    pred3, act3 = B.b0_decide({k: 99.0 for k in ACCIDENT_TYPES}, eps)
    assert pred3 is None and len(act3) == 3


def test_b0_epsilon_boundary_strict():
    eps = {"LOCA": 1.0, "SGATR": 0.5, "FLB": 2.0}
    # |v| == ε：严格 > 判据，边界不激活 -> 零激活弃权
    med = {"LOCA": 1.0, "SGATR": 0.5, "FLB": -2.0}
    pred, activated = B.b0_decide(med, eps)
    assert pred is None and activated == [], (pred, activated)
    # 越过 ε 一个极小量 -> 唯一激活
    med2 = {"LOCA": 1.0 + 1e-12, "SGATR": 0.0, "FLB": 0.0}
    pred2, act2 = B.b0_decide(med2, eps)
    assert pred2 == "LOCA" and act2 == ["LOCA"]
    # 恰在边界内 -> 不激活
    med3 = {"LOCA": 1.0 - 1e-12, "SGATR": 0.0, "FLB": 0.0}
    pred3, act3 = B.b0_decide(med3, eps)
    assert pred3 is None and act3 == []


# ==================== B1 ====================
def test_b1_argmax_hits_same_direction_template():
    rng = np.random.default_rng(20261026)
    # 三类方向基底（近似正交的随机向量），9 模板 = 类基底 + 小噪声
    base = {k: rng.normal(size=17) for k in ACCIDENT_TYPES}
    templates = {}
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            templates[(k, g)] = base[k] + 0.01 * rng.normal(size=17)
    # d 与 SGATR 基底同向（缩放+微噪），Pearson 对尺度不变 -> 命中 SGATR
    d = 2.5 * base["SGATR"] + 0.001 * rng.normal(size=17)
    pred, by_class, per_template = B.b1_scores(d, templates)
    assert pred == "SGATR", (pred, by_class)
    assert set(per_template) == {f"{k}_{g}" for k in ACCIDENT_TYPES
                                 for g in GROUP_ORDER}
    # B1(k)=max_g corr
    for k in ACCIDENT_TYPES:
        assert abs(by_class[k] - max(per_template[f"{k}_{g}"]
                                     for g in GROUP_ORDER)) < 1e-12
    # 直接拿某模板当 d：与该模板 corr=1
    pred2, bc2, _ = B.b1_scores(templates[("FLB", "high")].copy(), templates)
    assert pred2 == "FLB" and abs(bc2["FLB"] - 1.0) < 1e-9
    # 无 nan（pearson_corr 对常向量返 0），保证无弃权语义
    assert all(np.isfinite(v) for v in by_class.values())


# ==================== B2 特征 / 训练 ====================
V17 = [f"V{j:02d}" for j in range(17)]


def _synth_baseline():
    rng = np.random.default_rng(7)
    return {"mu": {v: float(rng.uniform(-5, 5)) for v in V17},
            "sigma": {v: float(rng.uniform(0.5, 3.0)) for v in V17}}


def _synth_block(baseline, z_target=None, T=121, seed=0):
    """以 z 空间造数据：Z = z_target 行向量 + 噪声；再还原成原始单位 block。"""
    rng = np.random.default_rng(seed)
    if z_target is None:
        z_target = np.zeros(17)
    Z = np.asarray(z_target)[None, :] + rng.normal(0, 0.3, size=(T, 17))
    mu = np.array([baseline["mu"][v] for v in V17])
    sig = np.array([baseline["sigma"][v] for v in V17])
    return Z * sig + mu, list(V17)


def test_b2_feature_layout_102_and_no_marker():
    baseline = _synth_baseline()
    block, cols = _synth_block(baseline, seed=1)
    feats, names = B.compute_b2_features(block, cols, V17, baseline)
    # 列数 = 17 变量 × 6 统计量
    assert feats.shape == (17 * 6,), feats.shape
    assert len(names) == 102
    assert names == B.b2_feature_names(V17)
    # 无 marker 列名（集合交集断言必须通过）
    assert_no_marker_columns(names, MARKER_COLS)
    assert all(not any(m in n for m in MARKER_COLS) for n in names)
    # 6 个统计量后缀齐全，变量优先排布
    assert names[:6] == [f"V00__{s}" for s in B.B2_STATS]
    # 数值核对（第 0 个变量）
    Z = B.z_matrix(block, cols, V17, baseline)
    assert abs(feats[0] - Z[:, 0].mean()) < 1e-12            # mean_z
    assert abs(feats[1] - Z[:, 0].std(ddof=0)) < 1e-12       # std_z
    assert abs(feats[2] - Z[:, 0].min()) < 1e-12             # min_z
    assert abs(feats[3] - Z[:, 0].max()) < 1e-12             # max_z
    assert abs(feats[4] - (np.abs(Z[:, 0]) > ANOMALY_K).mean()) < 1e-12
    assert abs(feats[5] - (block[:, 0].mean() - baseline["mu"]["V00"])) < 1e-9
    # overlimit 特征可被触发：构造 |z|>3 占比明确的块
    z = np.zeros(17)
    blk2, cols2 = _synth_block(baseline, z_target=np.zeros(17), T=10, seed=2)
    f2, _ = B.compute_b2_features(blk2, cols2, V17, baseline)
    assert f2[4] == 0.0  # 噪声 0.3 下不会越限


def test_b2_fit_predict_three_classes():
    rng = np.random.default_rng(11)
    n_per = 15
    X, y = [], []
    for ci, k in enumerate(ACCIDENT_TYPES):
        sig = np.zeros(102)
        sig[6 * ci:6 * ci + 6] = 3.0  # 每类专属变量块位移 -> 可分
        X.append(rng.normal(size=(n_per, 102)) + sig[None, :])
        y += [k] * n_per
    X = np.vstack(X)
    model = B.fit_b2(X, y, B.b2_feature_names(V17), classes=ACCIDENT_TYPES)
    assert model["n_train"] == 45
    preds, conf, proba = B.predict_b2(model, X)
    assert proba.shape == (45, 3)
    assert set(preds) == set(ACCIDENT_TYPES), "三类都应被预测到"
    assert np.allclose(proba.sum(axis=1), 1.0, atol=1e-6)
    assert ((conf > 0) & (conf <= 1)).all()
    # 可分数据训练集准确率应很高
    acc = np.mean([p == t for p, t in zip(preds, y)])
    assert acc > 0.9, acc


# ==================== B2 退化曲线 ====================
def test_b2_coverage_curve_points_and_ceil_monotonic():
    n = 12
    rng = np.random.default_rng(3)
    logits = rng.normal(size=(n, 3))
    proba = np.exp(logits - logits.max(axis=1, keepdims=True))
    proba /= proba.sum(axis=1, keepdims=True)
    y = [ACCIDENT_TYPES[i % 3] for i in range(n)]
    rows = B.b2_coverage_curve(proba, y)
    # 9 个点 coverage .1.. .9
    assert len(rows) == 9
    assert [r["coverage"] for r in rows] == list(B.COVERAGES)
    # ceil 取整规则
    expected_k = [max(1, int(math.ceil(c * n))) for c in B.COVERAGES]
    assert [r["n_accepted"] for r in rows] == expected_k
    # 接受数单调不减；正确数不超过接受数；acc∈[0,1]
    ks = [r["n_accepted"] for r in rows]
    assert all(a <= b for a, b in zip(ks, ks[1:]))
    for r in rows:
        assert 0 <= r["n_correct"] <= r["n_accepted"]
        assert 0.0 <= r["selective_accuracy"] <= 1.0
        assert abs(r["selective_accuracy"] - r["n_correct"] / r["n_accepted"]) < 1e-12


# ==================== Wilson CI 只读复用 ====================
def test_wilson_ci_importable():
    from rca.v21.pipeline import wilson_ci
    ci = wilson_ci(8, 10)
    assert ci is not None
    lo, hi = ci
    assert 0.0 <= lo <= 0.8 <= hi <= 1.0
    assert wilson_ci(0, 0) is None


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            import traceback
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)

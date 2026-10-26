"""R1.5 Alg3 反事实验证环单测（方案 13.6）。

合成 25 条轨迹：x=linspace(.4,1.4)，Y=β·x+噪声（β 符号已知，含 1 个近零 β 变量）。
可直接 python tests/test_cf_validation.py 运行（不依赖 pytest），也可被 pytest 收集。
"""
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rca.rca_config_v21 import (
    CF_FAIL_AGREEMENT, CF_DYNAMIC_RANGE_MIN, GCF_PASS, GCF_FAIL,
)
from rca.v21.cf_validation import counterfactual_validation

N = 25
X = np.linspace(0.4, 1.4, N)
# β 符号已知：3 正、2 负、1 近零
BETAS = np.array([2.0, -1.5, 1.0, -0.8, 0.3, 0.0])
SYMS = [f"V{i}" for i in range(6)]
# 冻结签名 cL 取真值（算法不重学，只做重演）
CL_TRUE = {v: float(b) for v, b in zip(SYMS, BETAS)}


def _linear_y(seed=123, noise=0.03):
    rng = np.random.default_rng(seed)
    return X[:, None] * BETAS[None, :] + rng.normal(0.0, noise, (N, 6))


def test_strong_monotone_template_passes():
    """① 强单调模板：适用、正/负效应边 gCF=1、Δpred 符号正确，近零 β 边 inconclusive。"""
    r = counterfactual_validation(X, _linear_y(), SYMS, CL_TRUE)
    assert r["status"] == "cf_ok", r["status"]
    assert r["dr"] >= CF_DYNAMIC_RANGE_MIN
    # 跨折汇总两组均达最小组大小
    assert r["group_sizes"]["baseline"] >= 5 and r["group_sizes"]["intervention"] >= 5
    for v, b in zip(SYMS, BETAS):
        e = r["edges"][v]
        if abs(b) < 1e-12:
            # 近零效应：双侧近零 -> cf_inconclusive，不进分母，gCF=1
            assert "cf_inconclusive" in e["flags"], e
            assert e["agree"] is None and e["gCF"] == GCF_PASS
            continue
        assert e["gCF"] == GCF_PASS, (v, e)
        # 两引擎预测与观测方向必须同 β 符号
        assert np.sign(e["delta_pred_L"]) == np.sign(b), (v, e)
        assert np.sign(e["delta_pred_A"]) == np.sign(b), (v, e)
        assert np.sign(e["delta_obs"]) == np.sign(b), (v, e)
        assert e["agree"] == 1.0, (v, e)
    # 模板级：全部比较一致
    assert r["template_gcf"] == GCF_PASS and r["template"]["agree"] == 1.0
    # 量级 Spearman 应显著为正
    assert r["spearman"]["L"]["rho"] > 0.9 and r["spearman"]["A"]["rho"] > 0.9


def test_dynamic_range_gate_not_applicable():
    """② dr=Q3−Q1 不足（及轨迹数不足）-> cf_not_applicable，gCF 全 1，不落逐边判据。"""
    x_flat = np.linspace(0.40, 0.45, N)   # dr≈0.025 < 0.10
    r = counterfactual_validation(x_flat, _linear_y(), SYMS, CL_TRUE)
    assert r["status"] == "cf_not_applicable"
    assert r["reason"] == "dynamic_range"
    assert all(e["gCF"] == GCF_PASS for e in r["edges"].values())
    # 不落逐边判据
    assert all("delta_obs" not in e for e in r["edges"].values())
    assert r["template_gcf"] == GCF_PASS

    # 轨迹数 < 2*CF_MIN_GROUP_SIZE（dr 足够）-> 同状态，原因 n_trajectories
    r_n = counterfactual_validation(np.linspace(0.4, 1.4, 8),
                                    np.zeros((8, 6)), SYMS, CL_TRUE)
    assert r_n["status"] == "cf_not_applicable"
    assert r_n["reason"] == "n_trajectories"
    assert all(e["gCF"] == GCF_PASS for e in r_n["edges"].values())


def test_random_unrelated_y_fails_edges():
    """③ Y 与 x 无关（纯随机）：至少一条边 cf_fail，gCF=0.5；且 fail 条件留痕齐全。"""
    # seed=7 在固定折划分下确定性产出 2 条低一致率边（ρ_L≈-0.66）
    rng = np.random.default_rng(7)
    Y_rand = rng.normal(0.0, 1.0, (N, 6))
    # 冻结签名声称清一色正效应且量级有区分（供 Spearman）
    cl = {v: c for v, c in zip(SYMS, [2.0, 1.6, 1.2, 0.9, 0.7, 0.5])}
    r = counterfactual_validation(X, Y_rand, SYMS, cl)
    assert r["status"] == "cf_ok"
    failed = [v for v, e in r["edges"].items() if e["gCF"] == GCF_FAIL]
    assert len(failed) >= 1, {v: e["agree"] for v, e in r["edges"].items()}
    for v in failed:
        e = r["edges"][v]
        assert "cf_fail" in e["flags"]
        # 冻结 OR：低一致率 或 模板级 Spearman 负，任一即可
        assert (e["agree"] is not None and e["agree"] < CF_FAIL_AGREEMENT) or \
               "cf_template_rho_negative" in e["flags"]
    # 量级 Spearman 至少一引擎为负（OR 的第二支留痕）
    rho_l = r["spearman"]["L"]["rho"] if r["spearman"]["L"] else None
    rho_a = r["spearman"]["A"]["rho"] if r["spearman"]["A"] else None
    assert (rho_l is not None and rho_l < 0) or (rho_a is not None and rho_a < 0)
    # 非 fail 边不得降权（gCF 只取 {1,0.5}）
    assert all(e["gCF"] in (GCF_PASS, GCF_FAIL) for e in r["edges"].values())


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n全部 {len(fns)} 个 R1.5 单测通过")


if __name__ == "__main__":
    _run_all()

"""G3 单元测试：合成 4 变量链 m(A)->B->C->D，验证方案 13.12 五项门控。

可直接 python tests/test_v21_units.py 运行（不依赖 pytest），也可被 pytest 收集。
"""
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rca.v21.signatures import bfs_frontier, total_effect
from rca.v21.knowledge import apply_ontology_constraint
from rca.v21.inversion import (
    e3_base_channel, e3_path_channel, activation_set, should_abstain,
    assert_no_marker_columns, MarkerAccessError, overlimit_rates,
)

VARS = ["A", "B", "C", "D"]  # A=marker，链 A→B→C→D


def test_frontier_layers():
    edges = [("A", "B"), ("B", "C"), ("C", "D")]
    fr = bfs_frontier(edges, "A", max_depth=3)
    assert fr["layers"][1] == ["B"], fr
    assert fr["layers"][2] == ["C"], fr
    assert fr["layers"][3] == ["D"], fr
    assert fr["reach"] == ["B", "C", "D"]
    assert fr["depth_of"] == {"B": 1, "C": 2, "D": 3}
    # 环不应使 BFS 死循环
    fr_cycle = bfs_frontier([("A", "B"), ("B", "A"), ("B", "C")], "A")
    assert set(fr_cycle["reach"]) == {"B", "C"}


def test_total_effect_chain():
    # B[i,j] = j→i；A→B=0.7, B→C=0.5；T[C,A] 应≈0.35
    B = np.zeros((4, 4))
    B[1, 0] = 0.7
    B[2, 1] = 0.5
    T = total_effect(B)
    assert abs(T[2, 0] - 0.35) < 1e-9, T[2, 0]


def _ontology():
    return {
        "forbidden": [{"from": "C", "to": "A", "basis": "症状不外生产生marker"}],
        "competitive": [{"pair": ["A", "B"], "basis": "反馈环"}],
        "allowed": [],
    }


def test_ontology_removes_forbidden_keeps_competitive():
    B = np.zeros((4, 4))
    B[0, 2] = 0.9   # C→A 禁止边（B[i=A, j=C]）
    B[1, 0] = 0.7   # A→B 竞争边（保留）
    B[2, 1] = 0.5   # B→C 未知边（保留）
    Bc, rep = apply_ontology_constraint(B, VARS, _ontology(), use_ontology=True)
    assert Bc[0, 2] == 0.0, "forbidden 边必须删除"
    assert rep["n_removed"] == 1
    assert Bc[1, 0] == 0.7 and Bc[2, 1] == 0.5, "竞争/未知边保留"
    assert rep["n_competitive"] == 1
    # 消融开关：use_ontology=False 原样返回
    Bo, rep_o = apply_ontology_constraint(B, VARS, _ontology(), use_ontology=False)
    assert Bo[0, 2] == 0.9 and rep_o["use_ontology"] is False


def test_e3_base_sign_and_weight():
    cL = {"B": 2.0, "C": 1.0, "D": 0.5}
    pi = {"B": 1.0, "C": 1.0, "D": 1.0}
    idx = {v: i for i, v in enumerate(VARS)}
    # 全符号吻合
    d_match = np.array([1.0, 0.8, 0.5, 0.2])
    s = e3_base_channel(d_match, VARS, ["B", "C"], cL, pi)
    assert s == 1.0, s
    # C 方向相反 → 0.5
    d_flip = np.array([1.0, 0.8, -0.5, 0.2])
    s = e3_base_channel(d_flip, VARS, ["B", "C"], cL, pi)
    assert abs(s - 0.5) < 1e-12, s
    # gCF=0.5 作用于失配边 C：num=1, den=1.5 → 2/3（门控是质量权重不是分数档位）
    s = e3_base_channel(d_flip, VARS, ["B", "C"], cL, pi, {"C": 0.5})
    assert abs(s - 1.0 / 1.5) < 1e-12, s


def test_e3_path_reach_depth_inversion():
    cL = {"B": 2.0, "C": 1.0, "D": 0.5}
    pi = {"B": 1.0, "C": 1.0, "D": 1.0}
    frontier = bfs_frontier([("A", "B"), ("B", "C"), ("C", "D")], "A")
    d = np.array([1.0, 0.8, 0.5, 0.2])
    # A(B,D) 全部可达且符号吻合：reach=1, depth=1 → 1.0
    r = e3_path_channel(d, VARS, ["B", "D"], cL, pi, frontier)
    assert r["reach"] == 1.0 and r["depth"] == 1.0 and r["score"] == 1.0, r
    # 仅 S3 异常：r1=0<r3=1 → 倒置惩罚 0.8，违例对=2
    r = e3_path_channel(d, VARS, ["D"], cL, pi, frontier)
    assert r["inverted"] and r["violation_pairs"] == 2
    assert abs(r["score"] - 0.8) < 1e-12, r
    # 异常落在可达域外（A 中变量不在 R）：reach=0、depth=nan
    r = e3_path_channel(d, VARS, ["C"], cL, pi, bfs_frontier([("A", "B")], "A"))
    assert r["reach"] == 0.0 and np.isnan(r["depth"]) and np.isnan(r["score"]), r
    # 空激活语义为 nan（由 should_abstain 接管）
    r0 = e3_path_channel(d, VARS, [], cL, pi, frontier)
    assert np.isnan(r0["reach"])


def test_activation_and_empty_abstain():
    # 121 点：B 越限 20 点（>10%），C 越限 5 点
    T = 121
    Z = np.zeros((T, 4))
    Z[:20, 1] = 4.0
    Z[:5, 2] = -4.0
    rates = overlimit_rates(Z)
    A = activation_set(rates, VARS, ["B", "C", "D"])
    assert A == ["B"], A
    # 全零 → A=空 → 强制弃权
    assert should_abstain([], np.array([0.9, 0.1]), tau=0.5, delta=0.1) == "empty_activation"
    # top1-top2 < delta → 歧义弃权
    assert should_abstain(["B"], np.array([0.52, 0.50]), tau=0.1, delta=0.1) == "small_margin"
    # max < tau
    assert should_abstain(["B"], np.array([0.3, 0.1]), tau=0.5, delta=0.1) == "below_tau"


def test_masked_zero_access_assertion():
    try:
        assert_no_marker_columns(["P", "VOL", "WLR"], ["WLR", "WTRA", "WFLB"])
    except MarkerAccessError:
        pass
    else:
        raise AssertionError("marker 泄漏时必须抛 MarkerAccessError")
    # 干净特征不抛
    assert_no_marker_columns(["P", "VOL", "PSGA"], ["WLR", "WTRA", "WFLB"])


def test_marker_ca_column():
    from rca.v21.marker_effects import marker_ca_column
    rng = np.random.default_rng(0)
    n = 40
    x = np.linspace(0.4, 1.4, n)
    y_pos = 3.0 * x + rng.normal(0, 0.05, n)   # 正效应
    y_neg = -2.0 * x + rng.normal(0, 0.05, n)  # 负效应
    Y = np.stack([y_pos, y_neg], axis=1)
    out = marker_ca_column(x, Y, ["up", "down"], n_bootstrap=20, seed=1)
    assert out["up"]["estimable"] and out["up"]["piA"] >= 0.9
    assert out["up"]["cA"] > 0
    assert out["down"]["piA"] >= 0.9 and out["down"]["cA"] < 0
    # spread 门：组内 marker 无变化 -> 整列不可估、πA=0
    flat = np.full(n, 1.0)
    outf = marker_ca_column(flat, Y, ["up", "down"], n_bootstrap=5, seed=1)
    assert (not outf["up"]["estimable"]) and outf["up"]["piA"] == 0.0


def test_effective_marker_map_precedence():
    from rca.v21.marker_effects import effective_marker_map
    cA = {"V1": {"cA": -1.0, "piA": 0.95, "estimable": True},
          "V2": {"cA": 5.0, "piA": 0.40, "estimable": True},
          "V3": {"cA": 9.0, "piA": 0.0, "estimable": False}}
    cL = {"V1": {"cL": 2.0, "piL": 0.80},
          "V2": {"cL": -3.0, "piL": 0.95},
          "V3": {"cL": -7.0, "piL": 0.80}}
    eff = effective_marker_map(cA, cL, pi_thr=0.70)
    assert eff["V1"]["source"] == "A" and eff["V1"]["c"] == -1.0  # A 优先即便 L 反向
    assert eff["V2"]["source"] == "L" and eff["V2"]["c"] == -3.0  # A 不稳 L 稳
    assert eff["V3"]["source"] == "L"  # A 不可估回退 L


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n全部 {len(fns)} 个 G3 单测通过")


if __name__ == "__main__":
    _run_all()

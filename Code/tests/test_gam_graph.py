"""GAM 全对向图（13.5.4）单测：合成数据，迭代 2 次，变量 ≤5，独立运行不依赖 pytest。

直接运行：cd Code && PYTHONPATH=. python tests/test_gam_graph.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rca.v21.gam_graph import (
    pair_vote_matrix, bootstrap_pair_counts, consensus_classify,
)

N_BOOT = 2          # 规格：迭代次数 = 2
MAX_VARS = 5        # 规格：变量 ≤ 5


# ---------- 1. 非线性链 y = x^2 + 噪声，z 独立 ----------
def _chain_blocks(seed=0, n_traj=6, T=100):
    """6×100=600 点；变量序 [x, y, z]，真实方向 x→y，z 与两者独立。"""
    rng = np.random.default_rng(seed)
    blocks = []
    for _ in range(n_traj):
        x = rng.normal(size=T)
        y = x ** 2 + 0.2 * rng.normal(size=T)
        z = rng.normal(size=T)
        blocks.append(np.column_stack([x, y, z]))
    return blocks


def test_nonlinear_chain_direction():
    blocks = _chain_blocks()
    assert blocks[0].shape == (100, 3) and blocks[0].shape[1] <= MAX_VARS
    last = None
    per_iter = []
    for res in bootstrap_pair_counts(blocks, ["x", "y", "z"],
                                     n_boot=N_BOOT, seed=7):
        last = res
        per_iter.append((res["edge_counts"]["x->y"], res["edge_counts"]["y->x"]))
    c = last["edge_counts"]
    # 允许单次 0 票，但 2 次聚合后 x→y 必须占多数
    assert c["x->y"] >= 1, per_iter
    assert c["x->y"] > c["y->x"], per_iter
    # 每轮一张票：两方向票合计不超过迭代数
    assert c["x->y"] + c["y->x"] <= N_BOOT


# ---------- 2. consensus_classify 三标签 ----------
def test_consensus_classify_three_labels():
    sig = {"stable_edges": [
        {"from": "a", "to": "b"},   # 6 同向 / 1 反向 → consensus
        {"from": "c", "to": "d"},   # 1 同向 / 7 反向 → conflict
        {"from": "m", "to": "n"},   # 3 同向 / 2 反向（均 <5）→ single_lingam
    ]}
    pair_counts = {"edge_counts": {
        "a->b": 6, "b->a": 1,
        "c->d": 1, "d->c": 7,
        "m->n": 3, "n->m": 2,
    }, "n_boot": 10}
    out = consensus_classify(sig, pair_counts)
    assert out["a->b"]["label"] == "consensus", out["a->b"]
    assert out["a->b"]["n_same"] == 6 and out["a->b"]["n_rev"] == 1
    assert out["a->b"]["n_boot"] == 10
    assert out["c->d"]["label"] == "conflict", out["c->d"]
    assert out["c->d"]["n_same"] == 1 and out["c->d"]["n_rev"] == 7
    assert out["m->n"]["label"] == "single_lingam", out["m->n"]
    assert out["m->n"]["n_same"] == 3 and out["m->n"]["n_rev"] == 2
    assert out["summary"] == {"n_consensus": 1, "n_conflict": 1,
                              "n_single_lingam": 1, "n_stable": 3}

    # 边界：5:5 平票（都≥5）→ consensus（规格 n_same>=n_rev 优先）
    sig_tie = {"stable_edges": [{"from": "a", "to": "b"}]}
    out_tie = consensus_classify(
        sig_tie, {"edge_counts": {"a->b": 5, "b->a": 5}, "n_boot": 10})
    assert out_tie["a->b"]["label"] == "consensus"
    # 反向 5:4（同向不足 5，但反向满足 conflict）→ conflict
    out_rev = consensus_classify(
        sig_tie, {"edge_counts": {"a->b": 4, "b->a": 5}, "n_boot": 10})
    assert out_rev["a->b"]["label"] == "conflict"
    # 4:4（均不足 5）→ single_lingam
    out_mid = consensus_classify(
        sig_tie, {"edge_counts": {"a->b": 4, "b->a": 4}, "n_boot": 10})
    assert out_mid["a->b"]["label"] == "single_lingam"

    # 扁平 pair_counts（无 edge_counts 外壳）口径：n_boot 显式传入
    out_flat = consensus_classify(
        sig_tie, {"a->b": 6, "b->a": 2}, n_boot=10)
    assert out_flat["a->b"]["label"] == "consensus"
    assert out_flat["a->b"]["n_boot"] == 10


# ---------- 3. 常量列对不抛异常且记 0 ----------
def test_constant_column_pair_records_zero():
    rng = np.random.default_rng(3)
    blocks = []
    for _ in range(4):
        T = 50
        a = rng.normal(size=T)
        b = a + 0.1 * rng.normal(size=T)
        c = np.ones(T)                      # 常量列
        blocks.append(np.column_stack([a, b, c]))
    V, conf, seconds = pair_vote_matrix(blocks, ["a", "b", "c"],
                                       np.random.default_rng(1))
    assert V.shape == (3, 3)
    # 涉及常量 c 的对（a,c）（b,c）双向投票均为 0
    assert V[0, 2] == 0 and V[2, 0] == 0
    assert V[1, 2] == 0 and V[2, 1] == 0
    assert conf["a||c"] == 0.0 and conf["b||c"] == 0.0
    assert seconds["a||c"] == 0.0 and seconds["b||c"] == 0.0
    # Bootstrap 累计中常量相关有序边始终 0，且全程不抛异常
    last = None
    for res in bootstrap_pair_counts(blocks, ["a", "b", "c"],
                                     n_boot=N_BOOT, seed=11):
        last = res
    for edge in ("a->c", "c->a", "b->c", "c->b"):
        assert last["edge_counts"][edge] == 0, edge


# ---------- 4. 断点续跑：RNG 序列对齐，续跑 == 全新跑 ----------
def test_resume_rng_alignment():
    rng = np.random.default_rng(5)
    blocks = [np.column_stack([rng.normal(size=80),
                               rng.normal(size=80),
                               rng.normal(size=80)])
              for _ in range(4)]
    vars_ = ["p", "q", "r"]

    fresh = list(bootstrap_pair_counts(blocks, vars_, n_boot=N_BOOT, seed=42))
    # 先跑 1 轮落盘（模拟），再从 existing_iters=1 + 第 1 轮累计值续跑
    gen = bootstrap_pair_counts(blocks, vars_, n_boot=N_BOOT, seed=42)
    first = next(gen)
    resumed = list(bootstrap_pair_counts(
        blocks, vars_, n_boot=N_BOOT, seed=42, existing_iters=1,
        initial_counts=first["edge_counts"],
        initial_conf_sum=first["pair_conf_sum"],
        initial_seconds_sum=first["pair_seconds_sum"]))
    assert len(resumed) == 1 and resumed[0]["iter"] == 1
    assert resumed[0]["edge_counts"] == fresh[-1]["edge_counts"]
    assert resumed[0]["pair_conf_sum"] == fresh[-1]["pair_conf_sum"]
    assert resumed[0]["iter"] == fresh[-1]["iter"]


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"[PASS] {fn.__name__}")
    print(f"\n全部 {len(fns)} 个测试通过")


if __name__ == "__main__":
    _run_all()

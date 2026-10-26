"""GAM-ANM 全对向图（方案 v2.1 13.5.4）：10 次轨迹级 Bootstrap 成对判向 + 共识三分类。

定位（方案冻结，不得挪用）：
- GAM 全图结果**仅**用于对 LiNGAM stable_edges 做 consensus / conflict / single_lingam
  三分类标注；不供 BFS 传播拓扑，不进任何评分通道；
- GAM_FULL_N_BOOTSTRAP=10 是方案冻结预算，禁止在代码里自动降配（超预算只报告、由人决定）。

判向直接复用 core.nonlinear_baseline.anm_pair_direction
（pygam LinearGAM(s(0, n_splines=10, lam=0.6)) + MI 残差独立性），
本模块禁止另写任何判向逻辑。

Bootstrap 口径与 rca.v21.signatures.bootstrap_lingam_template 严格一致：
  rng = np.random.default_rng(seed)
  sampled = rng.integers(0, n_traj, size=n_traj)   # 轨迹级重采样，严禁时间点重采样
故同 seed 同模板时 GAM 与 LiNGAM 的每轮轨迹抽样序列完全相同。
"""
from __future__ import annotations

import time
from typing import Dict, Generator, List, Optional, Sequence, Tuple

import numpy as np

from core.nonlinear_baseline import anm_pair_direction
from rca.rca_config_v21 import GAM_CONSENSUS_MIN
from core.utils import logger

# 常量列判定（标准化后常量列严格为 0；与 anm 内部 _mi 的 1e-12 口径一致）
CONST_STD_EPS = 1e-12


def pair_vote_matrix(blocks: Sequence[np.ndarray], variables: List[str],
                     rng: np.random.Generator
                     ) -> Tuple[np.ndarray, Dict[str, float], Dict[str, float]]:
    """对全部块 vstack 后跑一次全对向 GAM-ANM 判向。

    Args:
        blocks: 本轮 Bootstrap 抽中的轨迹块（每块 [T, P]，列序 = variables）。
        variables: V_graph 变量名序列。
        rng: 模板 Bootstrap 同源 RNG（判向内部随机性由 anm_pair_direction 自身种子控制，
            此处不另作随机化，保留同口径接口）。

    Returns:
        V: n×n 投票矩阵。上三角 V[i,j]（i<j）∈ {+1, 0, -1}：
           +1 = variables[i] → variables[j]，-1 = 反向，0 = 未定/常量对；
           下三角 V[j,i] = -V[i,j] 仅作镜像，累计票数时只读取上三角。
        conf: 每个无序对 "{u}||{v}"（u 在 variables 中先于 v）的 conf=|MI_rev-MI_fwd|。
        seconds: 每个无序对的实测判向耗时（秒；常量对为 0），供预算核对。
        常量列（std≈0）涉及的对不调 anm_pair_direction，直接记 0。
    """
    data = np.vstack(blocks)
    n = len(variables)
    V = np.zeros((n, n), dtype=int)
    conf: Dict[str, float] = {}
    seconds: Dict[str, float] = {}
    stds = data.std(axis=0)

    for i in range(n):
        for j in range(i + 1, n):
            key = f"{variables[i]}||{variables[j]}"
            if stds[i] <= CONST_STD_EPS or stds[j] <= CONST_STD_EPS:
                # 常量列：跳过判向（anm 内部也会得 0，此处省去无效拟合）
                conf[key] = 0.0
                seconds[key] = 0.0
                continue
            t0 = time.perf_counter()
            direction, c = anm_pair_direction(data[:, i], data[:, j])
            seconds[key] = time.perf_counter() - t0
            V[i, j] = int(direction)
            V[j, i] = -int(direction)
            conf[key] = float(c)
    return V, conf, seconds


def bootstrap_pair_counts(blocks: Sequence[np.ndarray], variables: List[str],
                          n_boot: int, seed: int, existing_iters: int = 0,
                          initial_counts: Optional[Dict[str, int]] = None,
                          initial_conf_sum: Optional[Dict[str, float]] = None,
                          initial_seconds_sum: Optional[Dict[str, float]] = None
                          ) -> Generator[Dict, None, None]:
    """轨迹级 Bootstrap 全对向判向，逐次迭代 yield 累计结果（断点续跑粒度 = 1 次迭代）。

    每完成一次迭代即 yield，调用方必须落盘后再继续。

    existing_iters / initial_*：断点续跑时传入已完成迭代数与磁盘累计值；
    RNG 先按序消费已完成迭代的 integers 抽样，保证续跑与全新跑的抽样序列严格一致。

    Yields（每次迭代一个 dict，全部为累计口径）：
        iter: 本轮迭代的全局序号（0-based，[existing_iters, n_boot)）
        edge_counts: {"{u}->{v}": 累计有序边票数}，含全部 n(n-1) 个有序键
        pair_conf_sum: 无序对 conf 累计（均值由调用方除以 n_done）
        pair_seconds_sum: 无序对耗时累计（预算外推用）
        n_decided: 本轮判出方向的对数
        iter_seconds: 本轮全部对判向耗时合计（不含 vstack）
    """
    rng = np.random.default_rng(seed)
    n_traj = len(blocks)
    n = len(variables)

    ordered_keys = [f"{variables[u]}->{variables[v]}"
                    for u in range(n) for v in range(n) if u != v]
    pair_keys = [f"{variables[i]}||{variables[j]}"
                 for i in range(n) for j in range(i + 1, n)]

    counts: Dict[str, int] = {k: 0 for k in ordered_keys}
    if initial_counts:
        for k, v in initial_counts.items():
            if k in counts:
                counts[k] = int(v)
    conf_sum: Dict[str, float] = {k: 0.0 for k in pair_keys}
    if initial_conf_sum:
        conf_sum.update({k: float(v) for k, v in initial_conf_sum.items() if k in conf_sum})
    seconds_sum: Dict[str, float] = {k: 0.0 for k in pair_keys}
    if initial_seconds_sum:
        seconds_sum.update({k: float(v) for k, v in initial_seconds_sum.items()
                            if k in seconds_sum})

    if existing_iters > n_boot:
        raise ValueError(f"existing_iters={existing_iters} > n_boot={n_boot}")
    # 消费已完成迭代的抽样，对齐全新跑的 RNG 状态
    for _ in range(existing_iters):
        rng.integers(0, n_traj, size=n_traj)

    for it in range(existing_iters, n_boot):
        sampled = rng.integers(0, n_traj, size=n_traj)
        sample = [blocks[int(i)] for i in sampled]
        V, conf, pair_seconds = pair_vote_matrix(sample, variables, rng)

        n_decided = 0
        iter_seconds = 0.0
        for i in range(n):
            for j in range(i + 1, n):
                pkey = f"{variables[i]}||{variables[j]}"
                conf_sum[pkey] += conf[pkey]
                seconds_sum[pkey] += pair_seconds[pkey]
                iter_seconds += pair_seconds[pkey]
                d = int(V[i, j])
                if d == 1:
                    counts[f"{variables[i]}->{variables[j]}"] += 1
                    n_decided += 1
                elif d == -1:
                    counts[f"{variables[j]}->{variables[i]}"] += 1
                    n_decided += 1
        logger.info(f"  GAM-full bootstrap {it + 1}/{n_boot}: "
                    f"{n_decided}/{len(pair_keys)} 对判出方向，判向耗时 {iter_seconds:.1f}s")
        yield {
            "iter": it,
            "edge_counts": dict(counts),
            "pair_conf_sum": dict(conf_sum),
            "pair_seconds_sum": dict(seconds_sum),
            "n_decided": n_decided,
            "iter_seconds": iter_seconds,
        }


def consensus_classify(sig: Dict, pair_counts: Dict,
                       n_boot: Optional[int] = None,
                       min_agree: int = GAM_CONSENSUS_MIN) -> Dict:
    """对 LiNGAM stable_edges 逐条做 GAM 共识三分类（方案 13.5.4）。

    Args:
        sig: LiNGAM 签名（仅用 sig["stable_edges"]，元素含 from/to）。
        pair_counts: 两种口径均可：
          (a) run_gam_graph 的落盘 state：含 "edge_counts"（有序边票数）与 "n_boot"；
          (b) 扁平 {"{u}->{v}": 票数}（单测构造），此时 n_boot 需显式传入，
              缺省按 n_same+n_rev 计（不含未定票）。
    规则（MIN=GAM_CONSENSUS_MIN=5）：
        n_same≥5 且 n_same≥n_rev        → "consensus"
        n_rev ≥5 且 n_rev >n_same       → "conflict"
        否则                            → "single_lingam"

    Returns:
        {"{u}->{v}": {"label","n_same","n_rev","n_boot"}, ..., "summary": {
            "n_consensus","n_conflict","n_single_lingam","n_stable"}}
    """
    if isinstance(pair_counts, dict) and "edge_counts" in pair_counts:
        pc = pair_counts["edge_counts"]
        if n_boot is None:
            n_boot = pair_counts.get("n_boot")
    else:
        pc = dict(pair_counts or {})

    out: Dict[str, Dict] = {}
    summary = {"n_consensus": 0, "n_conflict": 0, "n_single_lingam": 0, "n_stable": 0}
    for e in sig["stable_edges"]:
        u, v = e["from"], e["to"]
        key = f"{u}->{v}"
        n_same = int(pc.get(key, 0))
        n_rev = int(pc.get(f"{v}->{u}", 0))
        nb = int(n_boot) if n_boot is not None else n_same + n_rev

        if n_same >= min_agree and n_same >= n_rev:
            label = "consensus"
        elif n_rev >= min_agree and n_rev > n_same:
            label = "conflict"
        else:
            label = "single_lingam"
        out[key] = {"label": label, "n_same": n_same, "n_rev": n_rev, "n_boot": nb}
        summary[f"n_{label}"] += 1
    summary["n_stable"] = len(out)
    out["summary"] = summary
    return out

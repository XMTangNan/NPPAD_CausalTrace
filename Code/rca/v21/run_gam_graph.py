"""执行器：9 模板 GAM-ANM 全对向图（13.5.4，10 次轨迹 Bootstrap）+ LiNGAM 稳定边共识三分类。

⚠️ 方案 v2.1 冻结：
- GAM 全图产物**仅**用于对 LiNGAM stable_edges 做 consensus/conflict/single_lingam
  共识标注，**不供 BFS 传播拓扑、不进任何评分通道**；
- GAM_FULL_N_BOOTSTRAP=10 是冻结预算，**禁止代码里自动降配**；若超预算只报告，由人决定。

同窗 / 同轨迹 Bootstrap 口径：
- 学图窗 [60,1500]（LEARN_WINDOW_*），加载方式与 run_r1._load_template_inputs 完全同款；
- 块经与 LiNGAM 完全相同的 transform_graph_block 标准化（V_thr 共享 mu/sigma、
  marker 平台尺度、WHPI/WECS 模板内尺度；仿射变换逐块做与拼池做等价）；
- 每模板 rng=np.random.default_rng(GLOBAL_SEED)、rng.integers 轨迹重采样，
  与 bootstrap_lingam_template 同种子同序列。

断点续跑：每完成 1 次迭代立即落盘 gam_graph_{k}_{g}.json；重跑从 n_done 续到满 10 次
（RNG 先消费已完成迭代抽样，续跑与全新跑严格同序列）。--force 重算。

产物（data.save_json 相对 output/rca_v21/）：
- gam_graph_{k}_{g}.json：k/g/variables/n_boot/seed、逐有序边累计票数、
  每无序对 conf/耗时累计、逐迭代时间戳与已完成迭代数；
- consensus_{k}_{g}.json：共识三分类（10 次全部完成后才生成；不回写 signature 文件）。

用法：
  cd Code && PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_gam_graph            # 全量 9 模板
  cd Code && PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_gam_graph --smoke    # LOCA_low 2 次
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKERS, GAM_FULL_N_BOOTSTRAP, GLOBAL_SEED,
    LEARN_WINDOW_START, LEARN_WINDOW_END,
)
from rca.v21 import data as D
from rca.v21.run_r1 import _load_template_inputs
from rca.v21.signatures import (
    load_signature, compute_graph_only_scale, transform_graph_block,
)
from rca.v21.gam_graph import bootstrap_pair_counts, consensus_classify
from core.utils import logger

PLAN_PAIR_SECONDS = 0.49     # 方案 13.5.4 估计：0.49s/对
PLAN_ITER_SECONDS = 93.0     # 方案估计：约 93s/迭代（190 对）
PLAN_TOTAL_HOURS = 1.5       # 方案估计：9 模板 × 10 次约 1.5h


def _new_state(k: str, g: str, variables: List[str], n_boot: int,
               seed: int, sevs: List[int]) -> Dict:
    n = len(variables)
    ordered = [f"{variables[u]}->{variables[v]}"
               for u in range(n) for v in range(n) if u != v]
    pairs = [f"{variables[i]}||{variables[j]}"
             for i in range(n) for j in range(i + 1, n)]
    return {
        "k": k, "g": g, "variables": list(variables),
        "n_boot": n_boot, "n_done": 0, "seed": seed,
        "learn_window": [LEARN_WINDOW_START, LEARN_WINDOW_END],
        "n_train_trajectories": len(sevs), "train_severities": sevs,
        "edge_counts": {e: 0 for e in ordered},
        "pair_conf_sum": {p: 0.0 for p in pairs},
        "pair_seconds_sum": {p: 0.0 for p in pairs},
        "iter_log": [],
        "note": "GAM 全对向图仅供 LiNGAM 稳定边共识三分类标注（13.5.4），"
                "不供 BFS 拓扑、不进评分",
    }


def _load_learn_zblocks(manifest: Dict, vsets: Dict, baseline: Dict,
                        scale: Dict, k: str, g: str
                        ) -> Tuple[List[str], List[np.ndarray], List[int]]:
    """与 run_r1 同款加载学图窗原始块，再按 LiNGAM 同参数逐块标准化。"""
    gvars, learn_blocks, _, sevs = _load_template_inputs(manifest, vsets, k, g)
    marker = MARKERS[k]
    graph_only = compute_graph_only_scale(learn_blocks, gvars, marker, baseline["mu"])
    z_blocks = [
        transform_graph_block(b, gvars, marker, baseline["mu"], baseline["sigma"],
                              scale["median_platform"][k], graph_only)
        for b in learn_blocks
    ]
    return gvars, z_blocks, sevs


def run_template(k: str, g: str, n_boot: int, manifest: Dict, vsets: Dict,
                 baseline: Dict, scale: Dict, force: bool,
                 out_name: str) -> Dict:
    """单模板断点续跑，返回最终 state（每迭代落盘）。"""
    gvars, z_blocks, sevs = _load_learn_zblocks(
        manifest, vsets, baseline, scale, k, g)

    existing = None
    if not force:
        try:
            existing = D.load_json(out_name)
        except FileNotFoundError:
            existing = None
    if existing is not None:
        if existing.get("variables") != gvars or existing.get("seed") != GLOBAL_SEED:
            raise RuntimeError(f"{out_name} 与当前冻结 variables/seed 不一致，需 --force")
        if existing.get("n_boot") != n_boot:
            raise RuntimeError(f"{out_name} n_boot={existing.get('n_boot')} "
                               f"与本次 {n_boot} 不一致，需 --force")
        state = existing
        logger.info(f"[resume] {out_name}: 已完成 {state['n_done']}/{n_boot}，继续")
    else:
        state = _new_state(k, g, gvars, n_boot, GLOBAL_SEED, sevs)

    n_done = int(state["n_done"])
    if n_done >= n_boot:
        logger.info(f"[skip] {out_name} 已凑满 {n_boot} 次（--force 重算）")
        return state

    for res in bootstrap_pair_counts(
            z_blocks, gvars, n_boot=n_boot, seed=GLOBAL_SEED,
            existing_iters=n_done,
            initial_counts=state["edge_counts"],
            initial_conf_sum=state["pair_conf_sum"],
            initial_seconds_sum=state["pair_seconds_sum"]):
        state["edge_counts"] = res["edge_counts"]
        state["pair_conf_sum"] = res["pair_conf_sum"]
        state["pair_seconds_sum"] = res["pair_seconds_sum"]
        state["n_done"] = res["iter"] + 1
        state["iter_log"].append({
            "iter": res["iter"],
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "pair_seconds": round(res["iter_seconds"], 4),
            "n_decided": res["n_decided"],
        })
        D.save_json(state, out_name)
        logger.info(f"[saved] {out_name}: {state['n_done']}/{n_boot} 次已落盘")
    return state


def _print_smoke_budget(state: Dict) -> None:
    """打印每对实测耗时、每迭代耗时、外推 10 次与 9 模板总时长（预算核对）。"""
    n_done = max(state["n_done"], 1)
    per_pair = sorted(
        ((p, s / n_done) for p, s in state["pair_seconds_sum"].items()),
        key=lambda x: -x[1])
    print("\n===== GAM 全图 smoke 预算实测（LOCA_low，20 变量 190 对，25 轨迹×145 点）=====")
    print("每对平均判向耗时（秒，按耗时降序；常量对为 0）：")
    for p, sec in per_pair:
        print(f"  {p:<14s} {sec:6.3f}s")
    pair_mean = float(np.mean([s for _, s in per_pair]))
    pair_med = float(np.median([s for _, s in per_pair]))
    iter_seconds = [e["pair_seconds"] for e in state["iter_log"]]
    iter_mean = float(np.mean(iter_seconds))
    print(f"\n每对耗时: 均值 {pair_mean:.3f}s  中位 {pair_med:.3f}s  "
          f"（方案估计 {PLAN_PAIR_SECONDS}s）")
    print(f"每迭代判向耗时: {iter_seconds} s，均值 {iter_mean:.1f}s（方案估计 {PLAN_ITER_SECONDS:.0f}s）")
    per_template_10 = iter_mean * GAM_FULL_N_BOOTSTRAP
    total_9 = per_template_10 * 9
    print(f"外推单模板 10 次: {per_template_10/60:.1f} min")
    print(f"外推 9 模板 × 10 次: {total_9/60:.1f} min = {total_9/3600:.2f} h"
          f"（方案估计 {PLAN_TOTAL_HOURS}h）")
    over = total_9 / 3600 - PLAN_TOTAL_HOURS
    if total_9 / 3600 > PLAN_TOTAL_HOURS * 1.5:
        print(f"⚠️ 外推超出方案估计 50%（> {PLAN_TOTAL_HOURS*1.5:.2f}h，"
              f"超 {over/PLAN_TOTAL_HOURS*100:.0f}%）：按约定只报告、不自动降配，等人工决定。")
    else:
        print("外推未超方案估计 +50% 阈值。")
    print("注：以上仅含判向耗时，未计块加载/标准化/落盘开销（量级远小于判向）。\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="仅 LOCA_low、2 次迭代，打印每对/每迭代耗时与外推")
    ap.add_argument("--force", action="store_true", help="忽略已有产物重算")
    args = ap.parse_args()

    manifest = D.load_json("split_manifest.json")
    baseline = D.load_json("shared_baseline.json")
    vsets = D.load_json("variable_sets.json")
    scale = D.load_json("marker_scale.json")

    if args.smoke:
        jobs = [("LOCA", "low")]
        n_boot = 2
    else:
        jobs = [(k, g) for k in ACCIDENT_TYPES for g in GROUP_ORDER]
        n_boot = GAM_FULL_N_BOOTSTRAP

    for k, g in jobs:
        # smoke 用独立文件名，避免污染/触发全量产物的断点逻辑
        out_name = (f"gam_graph_smoke_{k}_{g}.json" if args.smoke
                    else f"gam_graph_{k}_{g}.json")
        t0 = time.time()
        state = run_template(k, g, n_boot, manifest, vsets, baseline, scale,
                             force=args.force, out_name=out_name)
        logger.info(f"[done] {k}/{g}: {state['n_done']}/{n_boot} 次，"
                    f"耗时 {time.time()-t0:.1f}s -> {out_name}")

        if args.smoke:
            _print_smoke_budget(state)
            logger.info("smoke 仅 2 次（< GAM_CONSENSUS_MIN=5），不生成 consensus 产物")
            continue

        # 10 次全部完成后计算共识三分类（不回写 signature）
        sig = load_signature(k, g)
        if sig is None:
            raise FileNotFoundError(f"缺 signature_{k}_{g}.json，先跑 run_r1")
        result = consensus_classify(sig, state)
        consensus = {
            "k": k, "g": g, "marker": MARKERS[k],
            "n_boot": n_boot, "gam_graph": out_name,
            "classification": {ek: ev for ek, ev in result.items() if ek != "summary"},
            "summary": result["summary"],
        }
        cname = f"consensus_{k}_{g}.json"
        D.save_json(consensus, cname)
        logger.info(f"[consensus] {cname}: {result['summary']}")


if __name__ == "__main__":
    main()

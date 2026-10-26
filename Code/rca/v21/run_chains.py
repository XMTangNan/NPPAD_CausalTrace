"""13.10 Phase R5 执行器：对被接受测试轨迹产出两层 root_cause_chain JSON。

- 接受状态以 metrics_{cond}_{g}.csv（run_r3 冻结产物）为准，行序与 run_r3 相同
  （g → true 类 → manifest test_severities 升序），本文件按同序重建 (true,sev) 映射，
  并对接受轨迹重算观测/评分做一致性断言（纯评分、零学图，单条约毫秒级）；
- FULL/MASKED 各自按本条件分数选判定模板 g（chains.select_template）；
- FULL 与 MASKED 都出链（FULL 为主）；落盘 chains/root_cause_chain_{cond}_{true}_{sev}.json；
- 汇总 chain_summary.json（ungrounded 计数、标注计数、outside_reach 频次、
  vs §4.2 冻结关系方向一致率 frozen_relation_direction_rate_{k} 与 n）。

用法（cd Code）：
  PYTHONPATH=. python -m rca.v21.run_chains            # 正式：全部接受轨迹
  PYTHONPATH=. python -m rca.v21.run_chains --smoke    # 冒烟：每类 1 条 FULL，文件名加 _smoke
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKER_COLS,
    MAIN_WINDOW_START, MAIN_WINDOW_END, V21_OUTPUT, CF_DIR,
)
from rca.v21 import data as D
from rca.v21.signatures import load_signature
from rca.v21.pipeline import (
    load_cf_gate, observe_main_window, score_trajectory,
)
from rca.v21.inversion import assert_no_marker_columns
from rca.v21 import chains as C
from core.utils import logger

CHAIN_DIR = V21_OUTPUT / "chains"
CONDITIONS = ["FULL", "MASKED"]


def _load_all():
    manifest = D.load_json("split_manifest.json")
    baseline = D.load_json("shared_baseline.json")
    vsets = D.load_json("variable_sets.json")
    scale = D.load_json("marker_scale.json")
    calibration = D.load_json("calibration_params.json")
    sigs, gates, cf_objs, consensus = {}, {}, {}, {}
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            sig = load_signature(k, g)
            if sig is None:
                raise RuntimeError(f"R1 签名缺失 {k}_{g}，先完成 run_r1")
            sigs[(k, g)] = sig
            cf = D.load_json(f"cf_{k}_{g}.json", out_dir=CF_DIR)
            cf_objs[(k, g)] = cf
            gates[(k, g)] = load_cf_gate(cf)
            con_path = V21_OUTPUT / f"consensus_{k}_{g}.json"
            consensus[(k, g)] = json.load(open(con_path, encoding="utf-8"))
    return (manifest, baseline, vsets, scale, calibration,
            sigs, gates, cf_objs, consensus)


def _read_metrics_rows() -> Dict[Tuple[str, str], List[Dict]]:
    """读 metrics_{cond}_{g}.csv，按 run_r3 行序重建 (true,sev) 映射。"""
    manifest = D.load_json("split_manifest.json")
    out: Dict[Tuple[str, str], List[Dict]] = {}
    for cond in CONDITIONS:
        for g in GROUP_ORDER:
            with open(V21_OUTPUT / f"metrics_{cond}_{g}.csv", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            stream = [(k, sev) for k in ACCIDENT_TYPES
                      for sev in manifest["classes"][k]["groups"][g]["test_severities"]]
            assert len(rows) == len(stream), (cond, g, len(rows), len(stream))
            mapped = []
            for row, (true_k, sev) in zip(rows, stream):
                assert row["true"] == true_k, (cond, g, row, true_k)
                mapped.append({"true": true_k, "sev": int(sev),
                               "pred_csv": row["pred"],
                               "accepted": row["accepted"] == "True"})
            out[(cond, g)] = mapped
    return out


def _save_chain(rec: Dict, smoke: bool) -> Path:
    CHAIN_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "_smoke" if smoke else ""
    name = (f"root_cause_chain_{rec['condition']}_{rec['true']}_"
            f"{rec['severity']}{suffix}.json")
    path = CHAIN_DIR / name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2, default=str)
    return path


def _build_one(condition: str, true_k: str, sev: int, pred: str,
               X_full: np.ndarray, full_cols: List[str],
               v_thr: List[str], inputs, d_map: Dict[str, float]) -> Dict:
    (manifest, baseline, vsets, scale, calibration,
     sigs, gates, cf_objs, consensus) = inputs
    if condition == "FULL":
        block, cols = X_full, full_cols
    else:
        pos = [full_cols.index(v) for v in v_thr]
        block, cols = X_full[:, pos], list(v_thr)
        assert_no_marker_columns(cols, MARKER_COLS)
    res = score_trajectory(block, cols, condition, v_thr, sigs, gates,
                           baseline, calibration, epsilon=scale["epsilon"])
    if not res["accepted"] or res["argmax_class"] != pred:
        raise AssertionError(
            f"重算与 metrics csv 不一致 {condition}/{true_k}/{sev}: "
            f"csv={pred} rescore={res['argmax_class']} accepted={res['accepted']}")
    g_star, _ = C.select_template(res, pred, condition, calibration)
    rec = C.build_chain(
        condition=condition, true_k=true_k, severity=sev, pred=pred,
        accepted=True, res=res, d_map=d_map,
        sig=sigs[(pred, g_star)], g=g_star,
        cf_obj=cf_objs[(pred, g_star)],
        consensus_obj=consensus[(pred, g_star)],
        calibration=calibration)
    return rec


def run(smoke: bool = False) -> Dict:
    t0 = time.time()
    inputs = _load_all()
    (manifest, baseline, vsets, scale, calibration,
     sigs, gates, cf_objs, consensus) = inputs
    v_thr = vsets["V_thr"]
    full_cols = list(v_thr) + list(MARKER_COLS)
    rows_map = _read_metrics_rows()

    accepted_counts = {c: sum(1 for g in GROUP_ORDER
                              for r in rows_map[(c, g)] if r["accepted"])
                       for c in CONDITIONS}
    logger.info(f"accepted counts: {accepted_counts} (smoke={smoke})")

    emitted: List[Dict] = []

    if smoke:
        # 每类 1 条 FULL 接受轨迹（low→high 组序内取第一）
        for k in ACCIDENT_TYPES:
            pick = None
            for g in GROUP_ORDER:
                for r in rows_map[("FULL", g)]:
                    if r["true"] == k and r["accepted"]:
                        pick = (g, r)
                        break
                if pick:
                    break
            if pick is None:
                raise RuntimeError(f"smoke 失败：{k} 无 FULL 接受轨迹")
            g, r = pick
            X, cols, _ = D.load_window(k, r["sev"], MAIN_WINDOW_START,
                                       MAIN_WINDOW_END, full_cols)
            obs = observe_main_window(X, cols, v_thr, baseline)
            d_map = {v: float(obs["d"][i]) for i, v in enumerate(v_thr)}
            rec = _build_one("FULL", k, r["sev"], r["pred_csv"], X, cols,
                             v_thr, inputs, d_map)
            path = _save_chain(rec, smoke=True)
            emitted.append(rec)
            logger.info(f"[smoke] {k} sev={r['sev']} g={rec['template']['g']} "
                        f"n_ranked={rec['flags']['n_ranked']} -> {path.name}")
    else:
        for g in GROUP_ORDER:
            # 按 (true,sev) 合并两条件，每轨迹只加载一次 FULL 窗
            by_traj: Dict[Tuple[str, int], Dict[str, Dict]] = {}
            for cond in CONDITIONS:
                for r in rows_map[(cond, g)]:
                    by_traj.setdefault((r["true"], r["sev"]), {})[cond] = r
            for (true_k, sev), cond_rows in by_traj.items():
                if not any(r["accepted"] for r in cond_rows.values()):
                    continue
                X, cols, _ = D.load_window(true_k, sev, MAIN_WINDOW_START,
                                           MAIN_WINDOW_END, full_cols)
                obs = observe_main_window(X, cols, v_thr, baseline)
                d_map = {v: float(obs["d"][i]) for i, v in enumerate(v_thr)}
                for cond in CONDITIONS:
                    r = cond_rows.get(cond)
                    if r is None or not r["accepted"]:
                        continue
                    rec = _build_one(cond, true_k, sev, r["pred_csv"], X, cols,
                                     v_thr, inputs, d_map)
                    path = _save_chain(rec, smoke=False)
                    emitted.append(rec)
            logger.info(f"[group={g}] cumulative emitted={len(emitted)}")

    elapsed = time.time() - t0
    summary = C.summarize_chains(emitted, accepted_counts, elapsed_seconds=elapsed)
    summary["mode"] = "smoke" if smoke else "full"
    summary_name = "chain_summary_smoke.json" if smoke else "chain_summary.json"
    with open(V21_OUTPUT / summary_name, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    ds = summary["frozen_relation_direction"]
    for k in ACCIDENT_TYPES:
        logger.info(f"[direction/{k}] rate={ds[k]['rate']} n={ds[k]['n']} "
                    f"(consistent={ds[k]['n_consistent']}, "
                    f"inconsistent={ds[k]['n_inconsistent']}, "
                    f"noncomparable={ds[k]['n_noncomparable']})")
    logger.info(f"ungrounded_edges_total={summary['ungrounded_edges_total']} "
                f"emitted={summary['chains_emitted']} elapsed={elapsed:.1f}s "
                f"-> {summary_name}")
    return summary


def main():
    ap = argparse.ArgumentParser(description="13.10 R5 两层传播链输出")
    ap.add_argument("--smoke", action="store_true",
                    help="每类 1 条 FULL 接受轨迹；产物文件名加 _smoke")
    args = ap.parse_args()
    run(smoke=args.smoke)


if __name__ == "__main__":
    main()

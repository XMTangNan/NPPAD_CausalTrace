"""D3 轨迹级留一标定执行器（方案 13.7.6/13.7.7）。

用法（cd Code）：
  正式：PYTHONPATH=. python -m rca.v21.run_calibration            # 断点续跑
        PYTHONPATH=. python -m rca.v21.run_calibration --force    # 重算
  冒烟：PYTHONPATH=. python -m rca.v21.run_calibration --limit 3  # 仅前 3 条，
        产物写 calib_smoke.json，绝不覆盖正式 calibration_params.json

每完成 10 条落 calib_loo_checkpoint.json（冒烟带 _smoke 后缀），重跑可续。
"""
from __future__ import annotations

import argparse
import time

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, V21_OUTPUT, CF_DIR,
)
from rca.v21 import data as D
from rca.v21.signatures import load_signature
from rca.v21.pipeline import load_cf_gate, PROTOCOL_D3
from rca.v21.calibration import (
    calibration_jobs, evaluate_loo, assemble_calibration, json_ready,
)
from core.utils import logger

CHECKPOINT_NAME = "calib_loo_checkpoint.json"
CHECKPOINT_SMOKE_NAME = "calib_loo_checkpoint_smoke.json"
PARAMS_NAME = "calibration_params.json"
SMOKE_NAME = "calib_smoke.json"


def _load_inputs():
    manifest = D.load_json("split_manifest.json")
    baseline = D.load_json("shared_baseline.json")
    vsets = D.load_json("variable_sets.json")
    scale = D.load_json("marker_scale.json")
    final_sigs, gates = {}, {}
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            sig = load_signature(k, g)
            if sig is None:
                raise RuntimeError(f"R1 签名缺失 {k}_{g}，先完成 run_r1")
            final_sigs[(k, g)] = sig
            cf = D.load_json(f"cf_{k}_{g}.json", out_dir=CF_DIR)
            gates[(k, g)] = load_cf_gate(cf)
    return manifest, baseline, vsets, scale, final_sigs, gates


def _load_checkpoint(path, force):
    if force or not path.exists():
        return {"protocol": PROTOCOL_D3, "n_total": None, "records": []}
    ck = D.load_json(path.name, out_dir=path.parent)
    logger.info(f"[resume] 已有 {len(ck['records'])} 条标定记录：{path}")
    return ck


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略检查点全部重算")
    ap.add_argument("--limit", type=int, default=None,
                    help="冒烟：仅跑前 N 条标定轨迹，产物写 calib_smoke.json")
    args = ap.parse_args()
    smoke = args.limit is not None

    manifest, baseline, vsets, scale, final_sigs, gates = _load_inputs()
    v_thr = vsets["V_thr"]
    jobs = calibration_jobs(manifest)
    if smoke:
        jobs = jobs[: args.limit]

    ck_path = V21_OUTPUT / (CHECKPOINT_SMOKE_NAME if smoke else CHECKPOINT_NAME)
    ck = _load_checkpoint(ck_path, args.force)
    ck["n_total"] = len(jobs)
    done_ids = {r["id"] for r in ck["records"]}

    t_start = time.time()
    n_new = 0
    for job in jobs:
        rid = f"{job[0]}/{job[2]}"
        if rid in done_ids:
            continue
        rec = evaluate_loo(job, v_thr, final_sigs, gates,
                           baseline, manifest, vsets, scale)
        ck["records"].append(rec)
        done_ids.add(rid)
        n_new += 1
        if n_new % 10 == 0:
            ck["n_done"] = len(ck["records"])
            D.save_json(json_ready(ck), ck_path.name, out_dir=ck_path.parent)
            eta = (time.time() - t_start) / n_new * (len(jobs) - len(ck["records"]))
            logger.info(f"[checkpoint] {len(ck['records'])}/{len(jobs)} "
                        f"LOO 单条均时 {np_mean(ck['records']):.1f}s，预计剩余 {eta/60:.1f} min")
    ck["n_done"] = len(ck["records"])
    D.save_json(json_ready(ck), ck_path.name, out_dir=ck_path.parent)

    per_loo = [r["elapsed_s"] for r in ck["records"]]
    timing = {"n_records": len(ck["records"]),
              "mean_loo_s": (sum(per_loo) / len(per_loo)) if per_loo else None,
              "total_loo_s": sum(per_loo),
              "wall_s": time.time() - t_start}
    params = assemble_calibration(ck["records"])

    if smoke:
        out = {"smoke": True, "limit": args.limit, "timing": timing,
               "params": params, "records": ck["records"]}
        path = D.save_json(json_ready(out), SMOKE_NAME)
        logger.info(f"[smoke] {len(ck['records'])} 条 -> {path}（正式文件未触碰）")
    else:
        if len(ck["records"]) < len(calibration_jobs(manifest)):
            logger.info(f"[incomplete] {len(ck['records'])}/"
                        f"{len(calibration_jobs(manifest))}，仅更新检查点，"
                        "未写 calibration_params.json；续跑后自动汇总")
        else:
            path = D.save_json(json_ready(params), PARAMS_NAME)
            logger.info(f"[done] 标定参数 -> {path}")
    full_jobs_n = len(calibration_jobs(manifest))
    if timing["mean_loo_s"]:
        logger.info(f"LOO 均时 {timing['mean_loo_s']:.1f}s；外推全量 {full_jobs_n} 条"
                    f"约 {timing['mean_loo_s'] * full_jobs_n / 60:.1f} min")
    logger.info(f"tau={params['tau']} delta={params['delta']} U={params['U']} "
                f"spearman={params['calib_topology_spearman']} flags={params['flags']}")
    logger.info(f"sensitivity={params['sensitivity']}")


def np_mean(records):
    v = [r["elapsed_s"] for r in records]
    return sum(v) / len(v) if v else 0.0


if __name__ == "__main__":
    main()

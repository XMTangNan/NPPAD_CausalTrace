"""R1.5 执行器：9 模板（3 类 x 3 严重度组）Alg3 反事实验证环。

断点续跑：output/rca_v21/cf_validation/cf_<k>_<g>.json 已存在则跳过（--force 重算）。
冒烟：  cd Code && PYTHONPATH=. python -m rca.v21.run_cf --smoke   # 仅 LOCA_low，真实数据
全量：  cd Code && PYTHONPATH=. python -m rca.v21.run_cf
"""
from __future__ import annotations

import argparse
import time

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKERS, CF_DIR, GLOBAL_SEED,
    MAIN_WINDOW_START, MAIN_WINDOW_END, MAIN_WINDOW_POINTS,
)
from rca.v21 import data as D
from rca.v21.signatures import load_signature
from rca.v21.marker_effects import trajectory_steady_pairs
from rca.v21.cf_validation import counterfactual_validation
from core.utils import logger


def cf_path(k: str, g: str):
    return CF_DIR / f"cf_{k}_{g}.json"


def _load_main_blocks(manifest: dict, vsets: dict, k: str, g: str):
    """加载模板训练轨迹主窗块（[300,1500]），口径与 run_r1 一致。"""
    sevs = manifest["classes"][k]["groups"][g]["train_template_severities"]
    gvars = vsets["V_graph"][k]
    blocks = []
    for sev in sevs:
        Xm, cols, tm = D.load_window(k, sev, MAIN_WINDOW_START, MAIN_WINDOW_END, gvars)
        assert cols == gvars and len(tm) == MAIN_WINDOW_POINTS, (k, g, sev, len(tm))
        blocks.append(Xm)
    return gvars, blocks, sevs


def run_one(k: str, g: str, manifest: dict, baseline: dict, vsets: dict,
            scale: dict) -> dict:
    sig = load_signature(k, g)
    if sig is None:
        raise FileNotFoundError(f"缺少冻结签名 signature_{k}_{g}.json，先跑 run_r1")
    marker = MARKERS[k]
    v_thr = vsets["V_thr"]
    gvars, main_blocks, sevs = _load_main_blocks(manifest, vsets, k, g)

    x, Y, symptoms = trajectory_steady_pairs(
        main_blocks, gvars, marker, v_thr,
        baseline["mu"], baseline["sigma"], scale["median_platform"][k])
    assert symptoms == v_thr
    # 冻结签名 cL（不重学）
    cL = {v: float(sig["cL"][v]["cL"]) for v in symptoms}

    result = counterfactual_validation(x, Y, symptoms, cL, seed=GLOBAL_SEED)
    result["accident_type"] = k
    result["group"] = g
    result["marker"] = marker
    result["train_severities"] = list(sevs)
    result["signature_file"] = f"signature_{k}_{g}.json"
    result["main_window"] = [MAIN_WINDOW_START, MAIN_WINDOW_END]
    return result


def main():
    ap = argparse.ArgumentParser(description="R1.5 Alg3 反事实验证环")
    ap.add_argument("--smoke", action="store_true", help="仅 LOCA_low 真实跑一个模板")
    ap.add_argument("--force", action="store_true", help="已存在也重算")
    args = ap.parse_args()

    manifest = D.load_json("split_manifest.json")
    baseline = D.load_json("shared_baseline.json")
    vsets = D.load_json("variable_sets.json")
    scale = D.load_json("marker_scale.json")

    jobs = [("LOCA", "low")] if args.smoke else \
        [(k, g) for k in ACCIDENT_TYPES for g in GROUP_ORDER]

    timings = []
    for k, g in jobs:
        path = cf_path(k, g)
        if path.exists() and not args.force:
            logger.info(f"[skip] {path.name} 已存在（--force 重算）")
            continue
        t0 = time.time()
        result = run_one(k, g, manifest, baseline, vsets, scale)
        D.save_json(result, path.name, CF_DIR)
        dt = time.time() - t0
        s = result["summary"]
        timings.append((f"{k}_{g}", dt))
        if result["status"] == "cf_not_applicable":
            logger.info(f"[done] {k}/{g}: cf_not_applicable reason={result['reason']} "
                        f"n={result['n']} dr={result['dr']:.4f} {dt:.1f}s")
        else:
            logger.info(
                f"[done] {k}/{g}: status=cf_ok n={result['n']} dr={result['dr']:.3f} "
                f"judgeable={s['n_judgeable_edges']} fail={s['n_fail_edges']} "
                f"inconc={s['n_inconclusive_edges']} "
                f"rhoLneg={s['rho_negative_L']} rhoAneg={s['rho_negative_A']} "
                f"template_gcf={result['template_gcf']} {dt:.1f}s")

    if timings:
        logger.info("timing (template,s): %s",
                    [(name, round(dt, 2)) for name, dt in timings])
        logger.info("R1.5 实跑合计 %.1f s", sum(dt for _, dt in timings))


if __name__ == "__main__":
    main()

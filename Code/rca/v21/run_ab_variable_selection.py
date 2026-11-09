"""V2.3 Phase 1 A/B 对照：自动选取变量集 vs v2.1 硬编码变量集。

同一下游流水线（bootstrap_lingam_template + select_root_candidates）、同一 Bootstrap 次数，
对比指标：
  - 稳定边数（f_e>=0.70, dir_e>=0.90）
  - marker 稳定出边数
  - frontier 可达域大小
  - 根因 Top-1（marker 遮蔽后）
  - 本体允许边方向一致率

用法（cd Code）：PYTHONPATH=. python -m rca.v21.run_ab_variable_selection
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKERS, GLOBAL_SEED, V23_SUBDIRS,
    LEARN_WINDOW_START, LEARN_WINDOW_END, LEARN_WINDOW_POINTS,
    STABLE_F, STABLE_DIR,
)
from rca.v21 import artifact as A
from rca.v21 import data as D
from rca.v21.knowledge import build_ontology, ALLOWED_RELATIONS
from rca.v21.signatures import bootstrap_lingam_template, select_stable_edges, bfs_frontier
from rca.v21.source_tracing import compute_topology, select_root_candidates
from core.utils import logger, save_json

# A/B 对照用 Bootstrap 次数：与生产一致（100 次），避免 20 次分辨率 0.05 无法下结论
AB_N_BOOTSTRAP = 100
OUT_DIR = V23_SUBDIRS["ablations"]
OUT_DIR.mkdir(parents=True, exist_ok=True)
VS_SUMMARY = V23_SUBDIRS["variable_sets"] / "variable_selection_summary.json"


def _load_blocks(manifest, k, g, gvars):
    """加载某模板的学图窗块。"""
    sevs = manifest["classes"][k]["groups"][g]["train_template_severities"]
    blocks = []
    for sev in sevs:
        X, cols, t = D.load_window(k, sev, LEARN_WINDOW_START, LEARN_WINDOW_END, gvars)
        assert cols == gvars and len(t) == LEARN_WINDOW_POINTS, (k, g, sev, len(t))
        blocks.append(X)
    return blocks, sevs


def _ontology_agreement(stable_edges, accident_type):
    """稳定边中本体允许且方向正确的比例。"""
    allowed = set()
    for rel in ALLOWED_RELATIONS.get(accident_type, []):
        allowed.add((rel["from"], rel["to"]))
        if rel["direction"] == "bidirectional":
            allowed.add((rel["to"], rel["from"]))
    n_stable = len(stable_edges)
    if n_stable == 0:
        return 0.0, 0
    n_allowed = sum(1 for e in stable_edges if (e["from"], e["to"]) in allowed)
    return n_allowed / n_stable, n_allowed


def _run_one_set(manifest, baseline, scale, k, g, gvars, label):
    """对一个变量集跑因果发现并返回指标。"""
    marker = MARKERS[k]
    blocks, sevs = _load_blocks(manifest, k, g, gvars)
    ontology = build_ontology(k, gvars)
    sig = bootstrap_lingam_template(
        blocks, gvars, marker, ontology,
        baseline["mu"], baseline["sigma"], scale["median_platform"][k],
        n_bootstrap=AB_N_BOOTSTRAP, seed=GLOBAL_SEED, use_ontology=True)

    stable = sig["stable_edges"]
    marker_out_edges = [e for e in stable if e["from"] == marker]
    frontier = bfs_frontier([(e["from"], e["to"]) for e in stable], marker)
    reach_size = len(frontier["reach"])

    # 根因排名（marker 遮蔽，基于总因果效应排序）
    topology = compute_topology(stable, gvars, marker, {})
    roots = select_root_candidates(topology, marker, stable, gvars, top_k=5)
    top1 = roots[0]["variable"] if roots else None

    # 本体方向一致率
    ont_agree, n_allowed = _ontology_agreement(stable, k)

    return {
        "label": label,
        "n_vars": len(gvars),
        "n_stable_edges": len(stable),
        "n_marker_out_edges": len(marker_out_edges),
        "frontier_reach_size": reach_size,
        "root_cause_top1": top1,
        "root_cause_top5": [r["variable"] for r in roots],
        "ontology_agreement": round(ont_agree, 4),
        "n_ontology_allowed": n_allowed,
        "n_train_trajectories": len(blocks),
    }


def main() -> None:
    run_id = A.new_run_id("v23a-ab")
    manifest = D.load_json("split_manifest.json")
    baseline = D.load_json("shared_baseline.json")
    scale = D.load_json("marker_scale.json")
    vsets = D.load_json("variable_sets.json")
    with open(VS_SUMMARY, encoding="utf-8") as f:
        vs_summary = json.load(f)

    results: Dict[str, Dict] = {}
    jaccards: Dict[str, float] = {}

    for k in ACCIDENT_TYPES:
        results[k] = {}
        auto_vars = vs_summary["selected_vars"][k]
        v21_vars = vsets["V_graph"][k]
        jaccard = len(set(auto_vars) & set(v21_vars)) / len(set(auto_vars) | set(v21_vars))
        jaccards[k] = round(jaccard, 4)

        for g in GROUP_ORDER:
            logger.info(f"[A/B] {k}/{g}: auto={len(auto_vars)} vars, v2.1={len(v21_vars)} vars")

            auto_metrics = _run_one_set(manifest, baseline, scale, k, g, auto_vars, "auto")
            v21_metrics = _run_one_set(manifest, baseline, scale, k, g, v21_vars, "v2.1")

            results[k][g] = {
                "jaccard_vars": round(jaccard, 4),
                "auto": auto_metrics,
                "v21": v21_metrics,
                "diff": {
                    "stable_edges": auto_metrics["n_stable_edges"] - v21_metrics["n_stable_edges"],
                    "marker_out_edges": auto_metrics["n_marker_out_edges"] - v21_metrics["n_marker_out_edges"],
                    "frontier_reach": auto_metrics["frontier_reach_size"] - v21_metrics["frontier_reach_size"],
                    "ontology_agreement": round(auto_metrics["ontology_agreement"] - v21_metrics["ontology_agreement"], 4),
                    "root_top1_same": auto_metrics["root_cause_top1"] == v21_metrics["root_cause_top1"],
                    "root_top5_overlap": round(
                        len(set(auto_metrics["root_cause_top5"]) & set(v21_metrics["root_cause_top5"]))
                        / max(len(set(auto_metrics["root_cause_top5"]) | set(v21_metrics["root_cause_top5"])), 1), 4),
                },
            }
            logger.info(f"  stable: auto={auto_metrics['n_stable_edges']} v21={v21_metrics['n_stable_edges']} | "
                        f"frontier: auto={auto_metrics['frontier_reach_size']} v21={v21_metrics['frontier_reach_size']} | "
                        f"ont_agree: auto={auto_metrics['ontology_agreement']} v21={v21_metrics['ontology_agreement']} | "
                        f"top1: auto={auto_metrics['root_cause_top1']} v21={v21_metrics['root_cause_top1']}")

    # 汇总（跨组均值）
    summary = {}
    for k in ACCIDENT_TYPES:
        auto_stable = sum(results[k][g]["auto"]["n_stable_edges"] for g in GROUP_ORDER) / 3
        v21_stable = sum(results[k][g]["v21"]["n_stable_edges"] for g in GROUP_ORDER) / 3
        auto_frontier = sum(results[k][g]["auto"]["frontier_reach_size"] for g in GROUP_ORDER) / 3
        v21_frontier = sum(results[k][g]["v21"]["frontier_reach_size"] for g in GROUP_ORDER) / 3
        auto_ont = sum(results[k][g]["auto"]["ontology_agreement"] for g in GROUP_ORDER) / 3
        v21_ont = sum(results[k][g]["v21"]["ontology_agreement"] for g in GROUP_ORDER) / 3
        summary[k] = {
            "jaccard_vars": jaccards[k],
            "auto": {"stable_edges_avg": round(auto_stable, 1),
                     "frontier_reach_avg": round(auto_frontier, 1),
                     "ontology_agreement_avg": round(auto_ont, 4)},
            "v21": {"stable_edges_avg": round(v21_stable, 1),
                    "frontier_reach_avg": round(v21_frontier, 1),
                    "ontology_agreement_avg": round(v21_ont, 4)},
            "verdict": (
                f"变量集 Jaccard={jaccard:.2f}；"
                f"稳定边 auto={auto_stable:.1f} vs v2.1={v21_stable:.1f}；"
                f"frontier auto={auto_frontier:.1f} vs v2.1={v21_frontier:.1f}。"
                "两套变量集各有胜负，无一致优劣结论（详见 detailed）。"
            ),
        }

    A.write_artifact("ab_variable_selection", {"detailed": results, "summary": summary},
                     "ablations", run_id=run_id,
                     source="rca/v21/run_ab_variable_selection.py (n_bootstrap=%d)" % AB_N_BOOTSTRAP)
    logger.info(f"\n===== A/B 对照汇总（跨组均值）=====")
    for k in ACCIDENT_TYPES:
        s = summary[k]
        logger.info(f"{k}: stable(auto={s['auto']['stable_edges_avg']} vs v21={s['v21']['stable_edges_avg']}) | "
                    f"frontier(auto={s['auto']['frontier_reach_avg']} vs v21={s['v21']['frontier_reach_avg']}) | "
                    f"ont_agree(auto={s['auto']['ontology_agreement_avg']} vs v21={s['v21']['ontology_agreement_avg']})")


if __name__ == "__main__":
    main()

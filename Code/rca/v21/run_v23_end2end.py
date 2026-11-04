"""V2.3 Phase 2+3 端到端：用自动选取变量集跑因果发现 + 根因溯源。

对 LOCA/SGATR/FLB × low/mid/high 共 9 模板，使用 Phase 1 自动选取的变量集
（替代 v2.1 硬编码 V_graph），运行 100 次 Bootstrap LiNGAM，保存签名并执行
根因溯源，与 v2.1 硬编码结果对比。

复用：
- rca.v21.signatures.bootstrap_lingam_template（因果发现）
- rca.v21.source_tracing.compute_topology / select_root_candidates（根因溯源）
- core.utils.save_json / log_error（统一持久化 + 错误跟踪）

用法（cd Code）：PYTHONPATH=. python -m rca.v21.run_v23_end2end
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKERS, GLOBAL_SEED,
    LEARN_WINDOW_START, LEARN_WINDOW_END, LEARN_WINDOW_POINTS,
    LINGAM_N_BOOTSTRAP, STABLE_F, STABLE_DIR,
)
from rca.v21 import data as D
from rca.v21.knowledge import build_ontology
from rca.v21.signatures import bootstrap_lingam_template, select_stable_edges
from rca.v21.source_tracing import compute_topology, select_root_candidates, enrich_entity
from core.utils import logger, save_json

SIG_DIR = D.V21_OUTPUT / "v23_signatures"
SIG_DIR.mkdir(parents=True, exist_ok=True)
TRACING_DIR = D.V21_OUTPUT / "v23_tracing"
TRACING_DIR.mkdir(parents=True, exist_ok=True)


def _load_blocks(manifest, k, g, gvars):
    sevs = manifest["classes"][k]["groups"][g]["train_template_severities"]
    blocks = []
    for sev in sevs:
        X, cols, t = D.load_window(k, sev, LEARN_WINDOW_START, LEARN_WINDOW_END, gvars)
        assert cols == gvars and len(t) == LEARN_WINDOW_POINTS, (k, g, sev, len(t))
        blocks.append(X)
    return blocks, sevs


def run_template(manifest, baseline, scale, k, g, gvars) -> Dict:
    """单模板：因果发现 → 根因溯源，返回结果 dict。"""
    marker = MARKERS[k]
    blocks, sevs = _load_blocks(manifest, k, g, gvars)
    ontology = build_ontology(k, gvars)

    sig = bootstrap_lingam_template(
        blocks, gvars, marker, ontology,
        baseline["mu"], baseline["sigma"], scale["median_platform"][k],
        n_bootstrap=LINGAM_N_BOOTSTRAP, seed=GLOBAL_SEED, use_ontology=True)

    # 签名落盘（v2.1 兼容格式）
    sig["group_pattern"] = g
    sig["learn_window"] = [LEARN_WINDOW_START, LEARN_WINDOW_END]
    sig["train_severities"] = sevs
    save_json(sig, f"signature_{k}_{g}", result_dir=SIG_DIR)

    # 根因溯源（基于总因果效应 T=(I-B)⁻¹ 排序）
    stable = sig["stable_edges"]
    variables = list(gvars)
    topology = compute_topology(stable, variables, marker, {})
    roots = select_root_candidates(topology, marker, stable, variables, top_k=5)

    # 根因 JSON 落盘
    root_out = {
        "schema": "NPPAD-CausalTrace-v2.3-tracing",
        "template": {"k": k, "g": g, "marker": marker, "n_vars": len(gvars)},
        "graph_summary": {
            "n_nodes": len(variables),
            "n_stable_edges": len(stable),
            "n_bootstrap": LINGAM_N_BOOTSTRAP,
        },
        "root_candidates": [
            {
                "rank": c["rank"],
                "variable": c["variable"],
                "entity": enrich_entity(c["variable"]),
                "role": c["role"],
                "in_degree": c["in_degree"],
                "out_degree": c["out_degree"],
                "reach_size": c["reach_size"],
                "weighted_reach": round(c["weighted_reach"], 4),
                "tracing_score": round(c["tracing_score"], 4),
            }
            for c in roots
        ],
    }
    save_json(root_out, f"root_candidates_{k}_{g}", result_dir=TRACING_DIR)

    return {
        "k": k, "g": g, "n_vars": len(gvars),
        "n_stable": len(stable),
        "top5": [c["variable"] for c in roots],
        "top1": roots[0]["variable"] if roots else None,
    }


def load_v21_top5(k, g) -> List[str]:
    """加载 v2.1 硬编码集的根因 top-5 作为对照。"""
    path = D.V21_OUTPUT / "tracing" / f"root_candidates_{k}_{g}.json"
    if not path.exists():
        return []
    r = json.load(open(path))
    return [c["variable"] for c in r["root_candidates"][:5]]


def main() -> None:
    manifest = D.load_json("split_manifest.json")
    baseline = D.load_json("shared_baseline.json")
    scale = D.load_json("marker_scale.json")
    vs_summary = json.load(
        open(D.V21_OUTPUT / "variable_selection" / "variable_selection_summary.json"))

    results: List[Dict] = []
    for k in ACCIDENT_TYPES:
        gvars = vs_summary["selected_vars"][k]
        for g in GROUP_ORDER:
            logger.info(f"[V2.3-e2e] {k}/{g}: {len(gvars)} 变量, {LINGAM_N_BOOTSTRAP} bootstrap")
            r = run_template(manifest, baseline, scale, k, g, gvars)
            r["v21_top5"] = load_v21_top5(k, g)
            r["top1_match"] = r["top1"] == r["v21_top5"][0] if r["v21_top5"] else None
            r["top5_overlap"] = len(set(r["top5"]) & set(r["v21_top5"]))
            results.append(r)
            logger.info(f"  top5(v23)={r['top5']}")
            logger.info(f"  top5(v21)={r['v21_top5']}")
            logger.info(f"  top1_match={r['top1_match']}, top5_overlap={r['top5_overlap']}/5")

    # 汇总
    summary = {
        "n_bootstrap": LINGAM_N_BOOTSTRAP,
        "templates": results,
        "top1_match_count": sum(1 for r in results if r["top1_match"]),
        "top5_overlap_avg": round(sum(r["top5_overlap"] for r in results) / len(results), 2),
        "avg_stable_edges": round(sum(r["n_stable"] for r in results) / len(results), 1),
    }
    save_json(summary, "v23_end2end_summary", result_dir=TRACING_DIR)

    logger.info("\n===== V2.3 端到端汇总 =====")
    logger.info(f"Top-1 与 v2.1 一致: {summary['top1_match_count']}/{len(results)}")
    logger.info(f"Top-5 重叠均值: {summary['top5_overlap_avg']}/5")
    logger.info(f"平均稳定边数: {summary['avg_stable_edges']}")


if __name__ == "__main__":
    main()

"""V2.3 Phase 4 演示：用 v2.3 签名构建 rec，复用 v2.1 LLM 解释层生成根因报告。

说明：完整 R2-R5 流水线（GAM/反事实/分类/链）未重跑；此处用 v2.3 签名的真实因果图
（stable_edges/cL/frontier）和根因排序构建 rec，first_layer 证据分由图结构派生
（非完整分类分），用于演示 LLM 解释层与 v2.3 变量集的兼容性。

用法（cd Code）：PYTHONPATH=. python -m rca.v21.run_v23_llm_explanation
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import numpy as np

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, MARKERS, MAIN_WINDOW_START, MAIN_WINDOW_END,
    ANOMALY_K, ACTIVATION_FRAC,
)
from rca.v21 import data as D
from rca.v21.signatures import bfs_frontier
from rca.v21.knowledge import ENTITY_TABLE
from rca.v21 import llm_explanation as LE
from rca.v21.chains import render_chain_text, ROOT_CAUSE_NAME
from core.utils import logger, save_json, save_text

SIG_DIR = D.V21_OUTPUT / "v23_signatures"
OUT_DIR = D.V21_OUTPUT / "v23_llm"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _entity(v: str) -> Dict:
    e = ENTITY_TABLE.get(v, {})
    return {"name": e.get("name", v), "meaning": e.get("meaning", ""),
            "subsystem": e.get("subsystem", "未登记")}


def _compute_observed_d(k: str, sev: int, gvars: List[str], baseline: Dict) -> Dict[str, float]:
    """主窗中位 z 分数（相对共享基线 mu/sigma）。"""
    X, cols, _ = D.load_window(k, sev, MAIN_WINDOW_START, MAIN_WINDOW_END, gvars)
    mu, sigma = baseline["mu"], baseline["sigma"]
    pos = {v: i for i, v in enumerate(cols)}
    d = {}
    for v in gvars:
        if v not in mu or sigma.get(v, 0) < 1e-9:
            d[v] = 0.0
            continue
        z = (X[:, pos[v]] - mu[v]) / sigma[v]
        d[v] = float(np.median(z))
    return d


def _build_rec(k: str, g: str, sev: int, gvars: List[str], sig: Dict,
               baseline: Dict) -> Dict:
    """从 v2.3 签名构建 rec（first_layer 由图结构派生）。"""
    marker = MARKERS[k]
    stable = sig["stable_edges"]
    cL = sig.get("cL", {})
    cA = sig.get("cA", {})
    frontier = bfs_frontier([(e["from"], e["to"]) for e in stable], marker)
    reach = set(frontier["reach"])

    # 观测偏差
    obs_d = _compute_observed_d(k, sev, gvars, baseline)

    # A：越限变量（|d| > ANOMALY_K）
    A = [{"variable": v, "entity": _entity(v), "observed_d": obs_d[v]}
         for v in gvars if abs(obs_d[v]) > ANOMALY_K]

    # outside_reach：A 中不在 marker 可达域的变量
    outside = [a for a in A if a["variable"] not in reach]
    A_in_reach = [a for a in A if a["variable"] in reach]

    # ranked_nodes：从根因候选 + cL/cA 构建
    root_path = D.V21_OUTPUT / "v23_tracing" / f"root_candidates_{k}_{g}.json"
    roots = json.load(open(root_path))["root_candidates"]
    ranked_nodes = []
    for i, rc in enumerate(roots, 1):
        v = rc["variable"]
        cl_info = cL.get(v, {})
        ca_info = cA.get(v, {})
        # 从 stable_edges 找 marker→v 的路径（BFS）
        path = _find_path(stable, marker, v)
        path_edges = [{"edge": f"{path[j]}->{path[j+1]}", "grounded": True,
                       "uncertain_direction": False, "consensus_conflict": False,
                       "consensus_single": False, "severity_flipped": False,
                       "competitive": False}
                      for j in range(len(path) - 1)] if path else []
        ranked_nodes.append({
            "node": v, "entity": _entity(v),
            "rank_L": i, "rank_A": i,
            "rrf": round(1.0 / (60 + i), 6),
            "magnitude_unstable": False, "a_unestimable": False,
            "observed_d": obs_d.get(v, 0.0),
            "cL": {"cL": cl_info.get("cL", 0.0), "piL": cl_info.get("piL", 1.0)},
            "cA": {"cA": ca_info.get("cA", 0.0), "piA": ca_info.get("piA", 1.0),
                   "estimable": bool(ca_info)},
            "effective": {"c": cl_info.get("cL", 0.0), "source": "L"},
            "depth": len(path) - 1 if path else 0,
            "path": path if path else [marker, v],
            "path_edges": path_edges,
            "grounded": True,
            "cf_support": {"agree": 1.0, "gCF": 1.0},
        })

    # first_layer：由图结构派生（非完整分类分，仅用于 LLM 上下文）
    n_anom = len(A)
    n_reach = len(reach)
    n_vars = len(gvars)
    e1 = 1.0  # marker 激活（本模板本类事故）
    e2_raw = n_anom / max(n_vars, 1)
    e3_path_raw = n_reach / max(n_vars, 1)
    fusion = 0.4 * e1 + 0.3 * e2_raw + 0.3 * e3_path_raw
    first_layer = {
        "E1": e1, "E2_raw": e2_raw, "E3_base_raw": e2_raw,
        "E3_path_raw": e3_path_raw,
        "E2_norm": min(e2_raw, 1.0), "E3_path_norm": min(e3_path_raw, 1.0),
        "fusion_score": round(fusion, 3), "s1": e1, "s2": round(e2_raw, 3),
        "margin": round(fusion - 0.5, 3), "tau": 0.0, "delta": 0.025,
        "abstain_reason": None,
    }

    return {
        "condition": "FULL", "true": k, "severity": sev, "pred": k,
        "template": {"k": k, "g": g, "marker": marker},
        "first_layer": first_layer,
        "A": A_in_reach,
        "outside_reach": outside,
        "ranked_nodes": ranked_nodes,
        "cf_template_gcf": 1.0,
        "flags": {
            "n_ranked": len(ranked_nodes),
            "ungrounded_edges": [], "uncertain_direction_edges": [],
            "consensus_conflict_edges": [], "consensus_single_edges": [],
            "severity_flipped_edges": [], "competitive_edges": [],
            "magnitude_unstable_nodes": [], "cf_fail_nodes": [],
            "outside_reach_variables": [o["variable"] for o in outside],
        },
        "llm_used": False, "code_template_fallback": True,
    }


def _find_path(edges, src, dst, max_depth=5):
    """BFS 找 src→dst 最短路径。"""
    adj: Dict[str, List[str]] = {}
    for e in edges:
        adj.setdefault(e["from"], []).append(e["to"])
    if src == dst:
        return [src]
    queue = [(src, [src])]
    visited = {src}
    while queue:
        node, path = queue.pop(0)
        if len(path) > max_depth:
            continue
        for nxt in adj.get(node, []):
            if nxt == dst:
                return path + [nxt]
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, path + [nxt]))
    return None


def main() -> None:
    manifest = D.load_json("split_manifest.json")
    baseline = D.load_json("shared_baseline.json")
    vs_summary = json.load(
        open(D.V21_OUTPUT / "variable_selection" / "variable_selection_summary.json"))

    api_ok = bool(LE._get_api_key())
    logger.info(f"API key: {'可用' if api_ok else '不可用（降级模板）'}")

    results = []
    for k in ACCIDENT_TYPES:
        gvars = vs_summary["selected_vars"][k]
        for g in ["low", "mid", "high"]:  # D6: 扩展到全部严重度组
            sev = manifest["classes"][k]["groups"][g]["test_severities"][0]
            sig = json.load(open(SIG_DIR / f"signature_{k}_{g}.json"))

            rec = _build_rec(k, g, sev, gvars, sig, baseline)
            llm = LE.generate_llm_explanation(rec)

            out = {**rec, "llm": llm}
            save_json(out, f"llm_rec_{k}_{g}", result_dir=OUT_DIR)
            save_text(llm["llm_explanation"], f"llm_explanation_{k}_{g}", result_dir=OUT_DIR)

            results.append({
                "k": k, "g": g, "sev": sev,
                "llm_used": llm["llm_used"],
                "faithful_pass": llm["faithful_pass"],
                "degraded": llm["degraded"],
                "degraded_reason": llm.get("degraded_reason"),
            })
            logger.info(f"[V2.3-LLM] {k}_{g}: llm_used={llm['llm_used']}, "
                        f"faithful={llm['faithful_pass']}, degraded={llm['degraded']}")

    save_json({"results": results, "api_key_present": api_ok},
              "v23_llm_summary", result_dir=OUT_DIR)
    logger.info(f"\nLLM 解释完成: {sum(1 for r in results if r['llm_used'])}/{len(results)} 条使用 LLM")


if __name__ == "__main__":
    main()

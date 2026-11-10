"""v2.3 C3b/C3c：跨严重度汇总与差异报告（只读 tracing/root_candidates_*）。

铁律（Code/STRUCTURE.md §1 单一来源、方案 v2.3 C4）：
- 只读同一 run 的 `output/rca_v23/tracing/root_candidates_{K}_{g}.json`，
  禁止重算因果、禁止读取旧 `v22/v21` 产物补数据；
- 命令行必须显式给 `--run-id`，并与本批签名/溯源里的 run_id 逐一核对，
  不一致立即报错退出（禁止混源）；
- 产物落 `output/rca_v23/reports/summary_{K}.json` 与 `diff_{K}.json`，
  记录输入文件 sha256 清单。

用法（cd Code）：
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.severity_aggregation --run-id v23c-...
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from causalrca.config.constants_v23 import EDGE_DIFF_THRESHOLD, MECH_JACCARD_THRESHOLD
from causalrca.config.constants_v21 import GROUP_ORDER, MARKERS, READY_CODES
from causalrca.io import artifact
from causalrca.io.logging import logger

CONSENSUS_NOTE = "单引擎稳定边，不等同共识边"


# ==================== 读取与 run_id 门控 ====================
def _load(sub: str, name: str) -> Dict:
    path = Path(artifact.V23_SUBDIRS[sub]) / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"缺产物 {path}（先跑 run_v23_end2end）")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_root_candidates(k: str, g: str) -> Dict:
    return _load("tracing", f"root_candidates_{k}_{g}")


def load_signature(k: str, g: str) -> Dict:
    return _load("signatures", f"signature_{k}_{g}")


def check_run_id(k: str, g: str, run_id: str) -> None:
    """签名与溯源必须同为本次 run_id，否则拒绝混用。"""
    for sub, name in (("signatures", f"signature_{k}_{g}"),
                      ("tracing", f"root_candidates_{k}_{g}")):
        doc = _load(sub, name)
        got = doc.get("run_id")
        if got != run_id:
            raise ValueError(
                f"{sub}/{name}.json 的 run_id={got} 与 --run-id={run_id} 不一致，"
                f"拒绝生成汇总（禁止混源）")


def _sha_inputs(k: str, groups: Sequence[str]) -> List[Dict]:
    out = []
    for g in groups:
        for sub, name in (("signatures", f"signature_{k}_{g}"),
                          ("tracing", f"root_candidates_{k}_{g}")):
            p = Path(artifact.V23_SUBDIRS[sub]) / f"{name}.json"
            out.append({"path": f"{sub}/{name}.json",
                        "sha256": artifact.sha256_file(p)})
    return out


# ==================== 汇总 ====================
def _top5(doc: Dict) -> List[str]:
    return [c["variable"] for c in doc["root_candidates"][:5]]


def _ranks(doc: Dict) -> Dict[str, int]:
    return {c["variable"]: c["rank"] for c in doc["root_candidates"]}


def build_summary(k: str, run_id: str,
                  groups: Sequence[str] = tuple(GROUP_ORDER)) -> Dict:
    """三组 Top-5 跨组一致性、名次变化、marker 排名（同一 run_id）。"""
    docs = {}
    for g in groups:
        docs[g] = load_root_candidates(k, g)
    top5 = {g: _top5(docs[g]) for g in groups}
    ranks = {g: _ranks(docs[g]) for g in groups}

    pairwise = []
    for i in range(len(groups) - 1):
        g1, g2 = groups[i], groups[i + 1]
        s1, s2 = set(top5[g1]), set(top5[g2])
        pairwise.append({
            "pair": [g1, g2],
            "overlap": len(s1 & s2),
            "jaccard": round(len(s1 & s2) / max(len(s1 | s2), 1), 3),
            "only_in_from": sorted(s1 - s2),
            "only_in_to": sorted(s2 - s1),
            "same_top1": bool(top5[g1] and top5[g2] and top5[g1][0] == top5[g2][0]),
        })

    all_vars = sorted({v for g in groups for v in ranks[g]})
    in_all = [v for v in all_vars if all(v in ranks[g] for g in groups)]
    rank_changes = {}
    for v in all_vars:
        rs = {g: ranks[g].get(v) for g in groups}
        present = [(g, r) for g, r in rs.items() if r is not None]
        shift = None
        if len(present) >= 2:
            shift = max(r for _, r in present) - min(r for _, r in present)
        rank_changes[v] = {"ranks": rs, "n_groups_in_top5": len(present),
                           "max_shift": shift}

    marker = docs[groups[0]]["template"]["marker"]
    appears = any(marker in ranks[g] for g in groups)
    summary = {
        "accident_type": k,
        "marker": marker,
        "groups": list(groups),
        "top5_by_group": top5,
        "rank_by_group": {g: ranks[g] for g in groups},
        "cross_group": {
            "pairwise": pairwise,
            "vars_in_all_groups": in_all,
            "n_vars_in_all_groups": len(in_all),
            "mean_pairwise_overlap": round(
                sum(p["overlap"] for p in pairwise) / max(len(pairwise), 1), 3),
            "top1_by_group": {g: (top5[g][0] if top5[g] else None) for g in groups},
            "top1_stable": len({t for t in (top5[g][0] if top5[g] else None
                                            for g in groups)}) == 1,
        },
        "rank_changes": rank_changes,
        "marker_rank": {
            "variable": marker,
            "ranks": {g: ranks[g].get(marker) for g in groups},
            "appears_in_root_candidates": appears,
            "note": "marker 为故障指示器，溯源流程中遮蔽，不参与根因排名",
        },
        "edge_counts": {g: docs[g]["n_stable_edges"] for g in groups},
        "n_consensus": {g: docs[g].get("n_consensus") for g in groups},
        "consensus_available": bool(docs[groups[0]].get("consensus_available")),
        "consensus_note": (CONSENSUS_NOTE if not docs[groups[0]].get("consensus_available")
                           else "已接入 GAM 共识分类"),
        "n_bootstrap": docs[groups[0]].get("n_bootstrap"),
        "engine": docs[groups[0]].get("engine"),
        "inputs": _sha_inputs(k, groups),
    }
    return summary


# ==================== 差异 ====================
def build_diff(k: str, run_id: str,
               groups: Sequence[str] = tuple(GROUP_ORDER)) -> Dict:
    """low→mid→high 的入榜/出榜/位移 + 边数与机理切换点（同一 run_id）。"""
    docs = {g: load_root_candidates(k, g) for g in groups}
    top5 = {g: _top5(docs[g]) for g in groups}
    ranks = {g: _ranks(docs[g]) for g in groups}
    edge_counts = {g: docs[g]["n_stable_edges"] for g in groups}

    transitions = []
    for i in range(len(groups) - 1):
        g1, g2 = groups[i], groups[i + 1]
        s1, s2 = set(top5[g1]), set(top5[g2])
        shift = {}
        for v in sorted(s1 | s2):
            r1, r2 = ranks[g1].get(v), ranks[g2].get(v)
            shift[v] = {"from": r1, "to": r2,
                        "delta": (None if (r1 is None or r2 is None) else r2 - r1)}
        transitions.append({
            "from_group": g1, "to_group": g2,
            "entered": sorted(s2 - s1),
            "exited": sorted(s1 - s2),
            "retained": sorted(s1 & s2),
            "rank_shift": shift,
            "top5_jaccard": round(len(s1 & s2) / max(len(s1 | s2), 1), 3),
            "top1": [top5[g1][0] if top5[g1] else None,
                     top5[g2][0] if top5[g2] else None],
            "top1_switched": bool(top5[g1] and top5[g2] and top5[g1][0] != top5[g2][0]),
            "n_stable_edges": [edge_counts[g1], edge_counts[g2]],
            "edge_count_diff_ratio": round(
                abs(edge_counts[g2] - edge_counts[g1]) / max(edge_counts[g1], 1), 3),
        })

    edge_diffs = [{"from_group": t["from_group"], "to_group": t["to_group"],
                   "count_from": t["n_stable_edges"][0],
                   "count_to": t["n_stable_edges"][1],
                   "diff_ratio": t["edge_count_diff_ratio"]}
                  for t in transitions if t["edge_count_diff_ratio"] > EDGE_DIFF_THRESHOLD]
    mech = [{"from_group": t["from_group"], "to_group": t["to_group"],
             "top5_jaccard": t["top5_jaccard"],
             "only_in_from": t["exited"], "only_in_to": t["entered"]}
            for t in transitions if t["top5_jaccard"] < MECH_JACCARD_THRESHOLD]
    top1_switches = [{"from_group": t["from_group"], "to_group": t["to_group"],
                      "from": t["top1"][0], "to": t["top1"][1]}
                     for t in transitions if t["top1_switched"]]

    diff = {
        "accident_type": k,
        "marker": docs[groups[0]]["template"]["marker"],
        "groups": list(groups),
        "transitions": transitions,
        "edge_counts": edge_counts,
        "edge_count_diffs": edge_diffs,
        "top1_switches": top1_switches,
        "mechanism_switches": mech,
        "n_clipped_edges": {g: docs[g].get("n_clipped_edges") for g in groups},
        "cond_IminusB": {g: docs[g].get("cond_IminusB") for g in groups},
        "consensus_available": bool(docs[groups[0]].get("consensus_available")),
        "consensus_note": CONSENSUS_NOTE,
        "n_bootstrap": docs[groups[0]].get("n_bootstrap"),
        "inputs": _sha_inputs(k, groups),
        "summary": (f"Top1 切换 {len(top1_switches)} 次; 边数差异>50% 组对 "
                    f"{len(edge_diffs)} 个; 机理切换点 {len(mech)} 个"),
    }
    return diff


# ==================== 落盘 ====================
def run_class(k: str, run_id: str,
              groups: Sequence[str] = tuple(GROUP_ORDER)) -> Dict:
    for g in groups:
        check_run_id(k, g, run_id)
    summary = build_summary(k, run_id, groups)
    diff = build_diff(k, run_id, groups)
    src = f"tracing/root_candidates_{k}_*/signature_{k}_*"
    artifact.write_artifact(f"summary_{k}", summary, "reports", run_id=run_id,
                            accident_type=k, group=None, source=src)
    artifact.write_artifact(f"diff_{k}", diff, "reports", run_id=run_id,
                            accident_type=k, group=None, source=src)
    logger.info(f"[C3b] summary_{k}: {diff['summary']}")
    return diff


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="v2.3 C4：汇总 + 差异（只读 tracing）")
    ap.add_argument("--run-id", required=True,
                    help="必须与本批签名/溯源的 run_id 一致，否则拒绝执行")
    ap.add_argument("--classes", default=",".join(READY_CODES))
    ap.add_argument("--groups", default=",".join(GROUP_ORDER))
    ap.add_argument("--no-manifest", action="store_true")
    args = ap.parse_args(argv)
    classes = [c.strip().upper() for c in args.classes.split(",") if c.strip()]
    groups = [g.strip() for g in args.groups.split(",") if g.strip()]
    for k in classes:
        if k not in MARKERS:
            raise ValueError(f"未登记的事故类别: {k}")
        run_class(k, args.run_id, groups)
    logger.info(f"[v23c] summary/diff 完成 run_id={args.run_id} classes={classes}")
    if not args.no_manifest:
        artifact.build_manifest(args.run_id)


if __name__ == "__main__":
    main()

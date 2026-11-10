"""真值可达性审计（B2 / V23-DESIGN-043）。

**只读**：读取本 run 已落盘的 `variable_sets/selection_report_<CLASS>.json`，逐变量登记最终去向，
不重跑变量选择、不改变任何选择结果。产物：
- `audit/truth_reachability_<CLASS>.json`（每类一张表）
- `audit/truth_reachability.json`（汇总：哪些真值变量被门控挡在门外）

去向口径（按优先级判定，与选择器的阶段一一对应）：
`selected` → `stage1_rejected` → `dedup_merged`（阶段3 重复测点合并）→ `vif_dropped`（阶段4）
→ `anchor_dropped`（锚定去冗余/剔除）→ `score_rejected`（未入选但仍在候选并集）
→ `not_in_candidates`（候选并集里根本没有，属数据/本体缺列）。
"""
import json
from typing import Dict, Iterable, List, Optional, Sequence

from causalrca.config import injection_truth as IT
from causalrca.io import artifact
from causalrca.io.logging import logger

DISPOSITION_NOTE = {
    "selected": "入选最终变量集",
    "stage1_rejected": "阶段1 数据质量门控剔除（全 NaN / 高缺失 / 近常量 / 全零）",
    "dedup_merged": "阶段3 精确重复测点合并（同物理量保留另一路）",
    "vif_dropped": f"阶段4 VIF 迭代剔除",
    "anchor_dropped": "锚定阶段剔除（去冗余/缺列等）",
    "score_rejected": "评分排序未进集合（仍属候选，未被门控剔除）",
    "not_in_candidates": "候选并集中不存在（数据列缺失或本体未收录）",
}


def _rank_of(scores: Dict[str, float], var: str) -> Optional[int]:
    if var not in scores:
        return None
    ordered = sorted(scores, key=lambda v: (-float(scores[v]), v))
    return ordered.index(var) + 1


def _disposition(report: Dict, var: str) -> Dict:
    """返回单个变量的最终去向（只读判定，不改 report）。"""
    selected = set(report.get("selected") or ())
    if var in selected:
        return {"disposition": "selected", "detail": None}

    for d in report.get("stage1_excluded") or ():
        if d.get("var") == var:
            return {"disposition": "stage1_rejected", "detail": d.get("reason")}

    for d in report.get("dropped_key_response") or ():
        if d.get("var") == var:
            return {"disposition": "dedup_merged",
                    "detail": f"retained={d.get('retained')} corr={d.get('corr')}"}

    for d in report.get("vif_log") or ():
        if d.get("var") == var:
            return {"disposition": "vif_dropped",
                    "detail": f"vif={d.get('vif'):.3e} n_vars_before={d.get('n_vars_before')}"}

    for d in (report.get("paper_anchor") or {}).get("dropped_anchor") or ():
        if d.get("var") == var:
            return {"disposition": "anchor_dropped",
                    "detail": f"stage={d.get('stage')} reason={d.get('reason')}"}

    scores = report.get("scores") or {}
    if var in scores:
        return {"disposition": "score_rejected", "detail": None}
    return {"disposition": "not_in_candidates", "detail": None}


def audit_class(report: Dict, truth: Sequence[str]) -> Dict:
    """单类真值可达性表（不改变选择结果）。"""
    scores = report.get("scores") or {}
    rows: List[Dict] = []
    for v in truth:
        disp = _disposition(report, v)
        rows.append({
            "var": v,
            "truth": True,
            "disposition": disp["disposition"],
            "detail": disp["detail"],
            "in_candidate_scores": v in scores,
            "score": (round(float(scores[v]), 6) if v in scores else None),
            "score_rank": _rank_of(scores, v),
        })
    blocked = [r["var"] for r in rows
               if r["disposition"] not in ("selected", "score_rejected")]
    not_selected = [r["var"] for r in rows if r["disposition"] != "selected"]
    return {
        "accident_type": report.get("accident_type"),
        "group": report.get("group"),
        "n_candidates_stage1": report.get("n_candidates_stage1"),
        "n_selected": report.get("n_selected"),
        "truth": list(truth),
        "rows": rows,
        "summary": {
            "n_truth": len(rows),
            "n_selected": sum(1 for r in rows if r["disposition"] == "selected"),
            "n_blocked_by_gate": len(blocked),
            "blocked_by_gate": blocked,
            "gate_blocks_truth": bool(blocked),
            # 未入选 = 不在最终变量集 → 评测候选集里不可达（含评分淘汰，属选股口径而非门控）
            "not_selected": not_selected,
            "truth_all_selected": not not_selected,
            "disposition_legend": DISPOSITION_NOTE,
        },
        "policy": ("只读审计：逐变量留痕，不干预选择结果；门控是否挡真值由此表回答，"
                   "不得据此回改选择器"),
    }


def audit_run(run_id: str, classes: Iterable[str], write: bool = True) -> Dict:
    """对给定类别逐类审计，写 `audit/truth_reachability*.json`（write=True）。"""
    root = artifact.run_path(run_id)
    per_class: Dict[str, Dict] = {}
    written: List[str] = []
    for k in classes:
        p = root / "variable_sets" / f"selection_report_{k}.json"
        if not p.exists():
            logger.warning(f"[truth-reachability] 缺 {p}，跳过 {k}")
            continue
        report = json.loads(p.read_text(encoding="utf-8"))
        truth = list(IT.truth_for(k))
        payload = audit_class(report, truth)
        payload["run_id"] = run_id
        payload["source"] = f"variable_sets/selection_report_{k}.json（只读）"
        per_class[k] = payload
        if write:
            written.append(artifact.write_artifact(
                f"truth_reachability_{k}", payload, "audit", run_id=run_id,
                accident_type=k, source=f"variable_sets/selection_report_{k}.json"))
    out = {
        "run_id": run_id,
        "definition": ("真值可达性审计（B2）：真值的每个变量在变量选择各阶段被如何处理——"
                       "入选/评分淘汰/VIF 剔除/去重合并/锚定剔除/数据门控剔除"),
        "why": ("门控（V0–V7）是设计取舍，可能把真值变量挡在门外；该审计只留痕，"
                "不改变选择，也不用于反推调参"),
        "per_class": per_class,
        "summary": {
            k: {
                "truth": v["truth"],
                "dispositions": {r["var"]: r["disposition"] for r in v["rows"]},
                "gate_blocks_truth": v["summary"]["gate_blocks_truth"],
                "blocked_by_gate": v["summary"]["blocked_by_gate"],
                "not_selected": v["summary"]["not_selected"],
                "truth_all_selected": v["summary"]["truth_all_selected"],
            } for k, v in per_class.items()
        },
    }
    if write:
        artifact.write_artifact("truth_reachability", out, "audit", run_id=run_id,
                                source="variable_sets/（只读审计）")
    logger.info(f"[truth-reachability] {len(per_class)} 类；"
                f"{ {k: v['summary']['blocked_by_gate'] for k, v in per_class.items()} }")
    return out

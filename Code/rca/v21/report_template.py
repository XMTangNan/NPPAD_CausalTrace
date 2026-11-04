"""V2.3 D7: 结构化 RCA 报告模板（C6 模板兜底）。

将根因排名 + 传导路径 + 异常状态 + K2/K3 证据组装为可校验的结构化报告。
LLM 不可用时作为兜底解释。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from rca.v21 import data as D
from rca.v21.knowledge import ENTITY_TABLE
from core.utils import logger, save_json, save_text

TRACE_DIR = Path(D.V21_OUTPUT) / "v23_tracing"
SIG_DIR = Path(D.V21_OUTPUT) / "v23_signatures"
OUT_DIR = Path(D.V21_OUTPUT) / "v23_rca_reports"
OUT_DIR.mkdir(parents=True, exist_ok=True)

GROUP_ORDER = ["low", "mid", "high"]


def _build_conduction_path(top1: str, topology: Dict, max_depth: int = 3) -> List[str]:
    """从 Top1 根因出发，沿出边构建传导路径。"""
    path = [top1]
    current = top1
    for _ in range(max_depth):
        # 找出边中 consensus_rate 最高的下游
        outs = [(e["to"], e.get("consensus_rate", 0))
                for e in topology if e["from"] == current]
        if not outs:
            break
        outs.sort(key=lambda x: -x[1])
        nxt = outs[0][0]
        if nxt in path:
            break
        path.append(nxt)
        current = nxt
    return path


def generate_rca_report(k: str, g: str) -> Dict:
    """生成单模板的结构化 RCA 报告。"""
    rc = D.load_json(str(TRACE_DIR / f"root_candidates_{k}_{g}.json"))
    sig = D.load_json(str(SIG_DIR / f"signature_{k}_{g}.json"))

    candidates = rc["root_candidates"]
    top1 = candidates[0]
    top5 = candidates[:5]

    # 传导路径
    path = _build_conduction_path(top1["variable"], sig["stable_edges"])

    # K1 本体标签
    top1_info = ENTITY_TABLE.get(top1["variable"], {})

    # Top5 证据向量
    evidence = []
    for c in top5:
        ent = ENTITY_TABLE.get(c["variable"], {})
        evidence.append({
            "variable": c["variable"],
            "subsystem": ent.get("subsystem", ""),
            "meaning": ent.get("meaning", ""),
            "role": c.get("role", ""),
            "in_degree": c.get("in_degree", 0),
            "out_degree": c.get("out_degree", 0),
            "tracing_score": c.get("tracing_score", 0),
        })

    report = {
        "accident_type": k,
        "severity_group": g,
        "template": f"{k}_{g}",
        "root_cause_top1": {
            "variable": top1["variable"],
            "subsystem": top1_info.get("subsystem", ""),
            "meaning": top1_info.get("meaning", ""),
            "role": top1.get("role", ""),
            "tracing_score": top1.get("tracing_score", 0),
        },
        "conduction_path": path,
        "top5_evidence": evidence,
        "n_consensus_edges": len(sig["stable_edges"]),
        "n_variables": len(sig["variables"]),
        "report_version": "NPPAD-CausalTrace-v2.3-rca-template",
    }
    return report


def report_to_text(report: Dict) -> str:
    """结构化报告转可读文本（LLM 降级兜底）。"""
    r = report["root_cause_top1"]
    lines = [
        f"=== NPPAD 根因分析报告：{report['template']} ===",
        f"",
        f"【根因判定】",
        f"  Top1 根因: {r['variable']} ({r['meaning']})",
        f"  子系统: {r['subsystem']}",
        f"  角色: {r['role']}",
        f"  溯源得分: {r['tracing_score']}",
        f"",
        f"【传导路径】",
        f"  {' → '.join(report['conduction_path'])}",
        f"",
        f"【Top5 候选证据】",
    ]
    for i, e in enumerate(report["top5_evidence"], 1):
        lines.append(f"  {i}. {e['variable']} ({e['subsystem']}): "
                     f"in={e['in_degree']}, out={e['out_degree']}, "
                     f"score={e['tracing_score']}")
    lines.extend([
        f"",
        f"【图统计】共识边 {report['n_consensus_edges']} 条, 变量 {report['n_variables']} 个",
        f"",
        f"⚠️ 本报告由结构化模板生成（C6 兜底），未调用 LLM。",
        f"   根因判定基于拓扑溯源（in_degree + weighted_reach + consensus_rate）。",
    ])
    return "\n".join(lines)


def run_all_reports():
    """为所有 3 类×3 组生成 RCA 报告。"""
    from rca.rca_config_v21 import ACCIDENT_TYPES
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            report = generate_rca_report(k, g)
            save_json(report, f"RCA_report_{k}_{g}", result_dir=OUT_DIR)
            save_text(report_to_text(report), f"RCA_report_{k}_{g}", result_dir=OUT_DIR)
            logger.info(f"[D7-report] {k}_{g}: Top1={report['root_cause_top1']['variable']}, "
                        f"路径={'→'.join(report['conduction_path'])}")


if __name__ == "__main__":
    run_all_reports()

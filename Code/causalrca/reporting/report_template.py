"""V2.3 D7：9 份结构化 RCA 报告（3 类 × 3 组）。

设计原则（方案 v2.3 §6.1 D7）：
  1. 单一证据来源：一律经由 llm_explanation.build_payload 只读 v2.3 产物，
     不重算因果、不读 v2.1/v2.2 旧目录；
  2. 章节固定 8 节（结论与 Top-5 / 因果图摘要 / 跨组一致性 / 物理一致性 K2 /
     故障模板 K3 / 偏离度 / 局限性 / 复现信息），json 与 txt 结构一一对应；
  3. 数据缺失写“未产出（原因：…）”，绝不输出空表假装完整；
  4. LLM 解释若未通过忠实性校验，报告正文顶部警示。

**结果层撤出（V23-DESIGN-063）**：本渲染器不再展示已判定不可信的评价指标——
跨组 Jaccard/重叠数值、条件数门结论、VIF 门结论、偏差通道 d 的档位计数。
这些量仍在 json 的 `sections` 里对应字段中（带 `reported=false`），但正文只写
“已撤出结果层（原因见方案 §7.10）”，不打印数值。依据：跨组一致性实测与随机不可区分，
VIF 门/条件数门结构上不会失败，d 跨变量不可比。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from causalrca.io.logging import logger
from causalrca.io import artifact
from causalrca.reporting import llm_explanation as LE
from causalrca.config.accident_config import READY_CODES

GROUPS: List[str] = ["low", "mid", "high"]

SECTION_KEYS: List[str] = [
    "1_conclusion_top5",
    "2_graph_summary",
    "3_cross_group",
    "4_physics_k2",
    "5_fault_template_k3",
    "6_deviation",
    "7_limitations",
    "8_reproducibility",
]
SECTION_TITLES: Dict[str, str] = {
    "1_conclusion_top5": "结论与 Top-5 根因",
    "2_graph_summary": "因果图摘要",
    "3_cross_group": "跨组一致性",
    "4_physics_k2": "物理一致性 K2（三态）",
    "5_fault_template_k3": "故障模板 K3（覆盖率/反向边）",
    "6_deviation": "通道偏移强度（已撤出结果层；≠根因）",
    "7_limitations": "局限性",
    "8_reproducibility": "复现信息",
}
REPORT_CMD_TMPL = ("cd Code && PYTHONPATH=. ../.venv/bin/python -m "
                   "rca.v21.run_v23_report --classes {k} --groups {g} --run-id {run_id}")
ROLE_CN = {
    "upstream_driver": "上游驱动源",
    "conduction_hub": "传导枢纽",
    "intermediate": "中间传导",
    "physical_root": "物理根因",
    "injection_source": "注入源",
}


def _role(role: Optional[str]) -> str:
    return ROLE_CN.get(role or "", role or "未标注")


def _miss(reason: str) -> str:
    return f"未产出（原因：{reason}）"


def _f(x, suffix: str = "") -> str:
    v = LE._num_or_none(x)
    return "未产出" if v is None else f"{v:.{LE.FAITHFUL_DECIMALS}f}{suffix}"


def _pct(x) -> str:
    v = LE._num_or_none(x)
    return "未产出" if v is None else f"{v * 100:.{LE.FAITHFUL_DECIMALS}f}%"


def _fmt_edge(e) -> str:
    """K3 边条目统一转文本（模板/稳定边/缺失原因三种形态）。"""
    return LE._fmt_edge(e)


def _load_llm_explanation(k: str, g: str) -> Optional[Dict]:
    """读同 run_id 的 D6 解释产物（缺失不算错误，报告照出）。"""
    path = artifact.subdir_path("reports") / f"llm_explanation_{k}_{g}.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"[D7] 读取 {path} 失败：{exc}")
        return None


def generate_rca_report(k: str, g: str, run_id: Optional[str] = None,
                        banner_note: Optional[str] = None) -> Dict:
    """生成单模板报告 dict（8 节固定键）；数据缺失如实标注。"""
    payload = LE.build_payload(k, g, run_id=run_id)
    roots = payload["root_candidates"]

    sections: Dict[str, Dict] = {}
    sections["1_conclusion_top5"] = {
        "n_candidates": len(roots),
        "candidates": roots[:5],
        "top1": roots[0]["variable"] if roots else None,
        "note": "按总因果效应 T=(I−B)⁻¹ 排序；单引擎稳定边口径",
    }

    sections["2_graph_summary"] = {
        "engine": payload["engine"],
        "n_variables": payload["n_variables"],
        "n_available_vars": payload["n_available_vars"],
        "gate_ratio": payload["gate_ratio"],
        "n_bootstrap": payload["n_bootstrap"],
        "n_stable_edges": payload["n_stable_edges"],
        "b_clip": payload["b_clip"],
        "n_clipped_edges": payload["n_clipped_edges"],
        "cond_IminusB": payload["cond_IminusB"],
        "cond_IminusB_after_clip": payload["cond_IminusB_after_clip"],
        "stability": payload.get("stability"),
        "consensus_available": payload["consensus_available"],
        "n_consensus": payload["n_consensus"],
        "baseline_coverage": payload["baseline_coverage"],
        # cond(I−B) 与 VIF 门已撤出结果层（V23-DESIGN-063）：阈值无来源、9/9 全过，
        # 数值仅留档；正文只写“已撤出”，不打印数字。
        "result_layer": {
            "reported": False,
            "reliability": "unsupported",
            "metric_status": "known_unusable",
            "withdrawn_metrics": ["cond_gate", "vif_gate"],
            "disposition": "处置待定 2026-11-12",
        },
    }

    cross = payload["cross_group"]
    cdiff = payload["cross_group_diff"]
    sections["3_cross_group"] = {
        "available": bool(cross.get("available")),
        "reason": cross.get("reason"),
        "top1_by_group": cross.get("top1_by_group"),
        "top1_stable": cross.get("top1_stable"),
        "mean_pairwise_overlap": cross.get("mean_pairwise_overlap"),
        "n_vars_in_all_groups": cross.get("n_vars_in_all_groups"),
        "pairwise": cross.get("pairwise"),
        "edge_counts": cross.get("edge_counts"),
        "diff": {
            "available": bool(cdiff.get("available")),
            "reason": cdiff.get("reason"),
            "n_top1_switches": cdiff.get("n_top1_switches"),
            "n_mechanism_switches": cdiff.get("n_mechanism_switches"),
            "n_edge_count_diffs": cdiff.get("n_edge_count_diffs"),
            "transitions": cdiff.get("transitions"),
        },
        # 跨组一致性数值（重叠/Jaccard/秩相关）已撤出结果层：实测与随机不可区分
        # （Jaccard 0.2632 vs 随机 0.2144，三对均值≥实测的概率 0.30；名次标准差
        # 1.89–2.87 vs 3.22–3.40；单调占比 1/15–5/15 vs 0.27–0.33）。
        "result_layer": {
            "reported": False,
            "reliability": "unsupported",
            "metric_status": "known_unusable",
            "withdrawn_metrics": ["topk_jaccard_across_groups",
                                  "cross_group_consistency",
                                  "rank_std_across_groups", "monotonicity"],
            "disposition": "处置待定 2026-11-12",
        },
    }

    sections["4_physics_k2"] = dict(payload["k2"])
    sections["5_fault_template_k3"] = dict(payload["k3"])
    sections["6_deviation"] = {**payload["deviation"],
                               "note": ("偏离度 ≠ 根因；档位为本模板内相对分位"
                                        "（p50/p70/p85），跨模板不可比绝对量"),
                               # d 跨变量不可比（量纲与各自噪声/下限归一，d_raw 到 1.67e5）：
                               # 档位计数已撤出结果层，仅内部留档。
                               "result_layer": {
                                   "reported": False,
                                   "reliability": "unsupported",
                                   "metric_status": "known_unusable",
                                   "withdrawn_metrics": ["deviation_channel"],
                                   "disposition": "处置待定 2026-11-12",
                               }}
    sections["7_limitations"] = {"items": list(payload.get("limitations") or [])}
    sections["8_reproducibility"] = {
        "run_id": payload["run_id"],
        "command": REPORT_CMD_TMPL.format(k=k, g=g, run_id=payload["run_id"]),
        "upstream_command": ("cd Code && PYTHONPATH=. ../.venv/bin/python -m "
                             f"rca.v21.run_v23_all --classes {k} --groups {g} "
                             f"--n-bootstrap {payload['n_bootstrap']}"),
        "inputs": payload["inputs"],
    }

    llm_doc = _load_llm_explanation(k, g)
    llm_info: Dict = {"available": llm_doc is not None}
    if llm_doc is not None and llm_doc.get("run_id") not in (None, payload["run_id"]):
        llm_info = {"available": False,
                    "run_id": llm_doc.get("run_id"),
                    "reason": (f"解释产物 run_id={llm_doc.get('run_id')} 与本报告 "
                               f"{payload['run_id']} 不同源，已忽略")}
        llm_doc = None
    warning = None
    if llm_doc is not None:
        faith = llm_doc.get("faithfulness") or {}
        llm_info.update({
            "backend": llm_doc.get("backend"),
            "degraded_reason": llm_doc.get("degraded_reason"),
            "faithful": faith.get("faithful"),
            "n_numbers_unsupported": faith.get("n_numbers_unsupported"),
            "unsupported": faith.get("unsupported") or [],
            "unknown": faith.get("unknown") or [],
            "run_id": llm_doc.get("run_id"),
        })
        if faith.get("faithful") is False:
            warning = (f"警示：LLM 解释未通过忠实性校验——未支撑数字 "
                       f"{faith.get('n_numbers_unsupported')} 个 {faith.get('unsupported')}，"
                       f"未知变量名 {faith.get('n_vars_unknown')} 个 {faith.get('unknown')}；"
                       f"正文以规则后端解释为准。")

    return {
        "template": f"{k}_{g}",
        "accident_type": k,
        "group": g,
        "marker": payload["marker"],
        "run_id": payload["run_id"],
        "banner_note": banner_note,
        "section_order": list(SECTION_KEYS),
        "sections": sections,
        # 报告级指标口径（V23-DESIGN-063）：已撤出的评价指标不在正文出现，
        # 数值只留在对应 section 的字段里供内部核对。
        "metrics_policy": {
            "reported": False,
            "reliability": "unsupported",
            "metric_status": "known_unusable",
            "withdrawn_metrics": ["hit1", "hit3", "mrr", "p2", "p3", "recall3", "ap3",
                                  "jaccard3", "wilson95", "rank_distribution",
                                  "topk_jaccard_across_groups", "cross_group_consistency",
                                  "rank_std_across_groups", "monotonicity",
                                  "vif_gate", "cond_gate", "clip_flip", "deviation_channel"],
            "premises": ["P1 无独立真值", "P2 样本不独立（n_eff=9，不是 60）",
                         "P3 门槛不可失败"],
            "disposition": "处置待定 2026-11-12",
            "see": "方案 §7.10",
        },
        "llm_explanation": llm_info,
        "warning": warning,
    }


def report_to_text(report: Dict) -> str:
    """报告渲染为可读中文文本（与 json 章节同序同源）。"""
    s = report["sections"]
    L: List[str] = []
    L.append(f"CausalTrace v2.3 根因分析报告 · {report['accident_type']} / {report['group']} 组")
    L.append(f"marker={report['marker']}，run_id={report['run_id']}")
    if report.get("banner_note"):
        L.append(f"[{report['banner_note']}]")
    if report.get("warning"):
        L.append("")
        L.append(f"[{report['warning']}]")
    L.append("")
    L.append("[指标口径] 已判定不可信的评价指标一律不在本报告展示（hit@k / MRR / 跨组 Jaccard /"
             " 名次标准差 / 条件数门 / VIF 门 / 偏差 d 档位）：它们与随机不可区分，或作为门"
             "结构上不会失败。数值仅留档在 json 对应字段（`reported=false`），"
             "原因见方案 §7.10（V23-DESIGN-063，处置待定 2026-11-12）。")
    L.append("[解读边界] 根因名次反映传导起点顺序与总效应排序，不等于物理强度；同一物理量的"
             "多量程/冗余通道（如 SGATR 的 NSGA/LSGA）可能被回归劈成对同一组目标反号、近等幅"
             "的系数，此类共线分裂不作物理强度解读（诊断标记见签名产物 "
             "redundancy_diagnostics，仅记录、不参与排序）。")

    # 一、结论
    L.append(f"一、{SECTION_TITLES['1_conclusion_top5']}")
    c = s["1_conclusion_top5"]
    if c["n_candidates"]:
        for i, cand in enumerate(c["candidates"], start=1):
            L.append(f"  第{i}名 {cand['variable']}（{cand['subsystem']}，{_role(cand['role'])}）："
                     f"总效应 {_f(cand['total_effect'])}，入度 {cand['in_degree']}，"
                     f"出度 {cand['out_degree']}，可达 {cand['reach_size']}")
        L.append(f"  Top-1：{c['top1']}；{c['note']}")
    else:
        L.append(f"  {_miss('根因候选为空，先检查 run_v23_end2end 溯源产物')}")
    L.append("")

    # 二、图摘要
    g2 = s["2_graph_summary"]
    L.append(f"二、{SECTION_TITLES['2_graph_summary']}")
    L.append(f"  引擎 {g2['engine']}，入模变量 {g2['n_variables']} 个"
             f"（本体 {g2['n_available_vars']} 个，门控比例 {_pct(g2['gate_ratio'])}），"
             f"bootstrap {g2['n_bootstrap']} 次。")
    L.append(f"  稳定边 {g2['n_stable_edges']} 条；B 截断阈值 {_f(g2['b_clip'])}，"
             f"被截断边 {g2['n_clipped_edges']} 条。")
    L.append("  条件数 cond(I−B)：已撤出结果层（阈值 10000 无来源、实测 9/9 全过，"
             "不具区分能力；原值见 json 的 2_graph_summary 字段，仅内部留档）。")
    L.append("  VIF 门（阈值 500）与条件数门（阈值 10000）**仅作记录，不构成通过判定**："
             "两者都不提供质量合格证，引用时只说“数值多大”，不得写“通过/稳定”。")
    stab = g2.get("stability") or {}
    if stab:
        L.append("  数值稳定性判词（stability_label）已撤出结果层：它由 cond 门与截断比阈值"
                 "合成，9/9 全部通过、无失败样例，不具区分能力；仅内部留档。")
    L.append(f"  共识：n_consensus={'null' if g2['n_consensus'] is None else g2['n_consensus']}"
             f"（单引擎口径，稳定边不构成多引擎共识）。")
    baseline = g2.get("baseline_coverage") or {}
    if baseline:
        L.append(f"  基线覆盖：期望 {baseline.get('expected_points')} 点，"
                 f"实际均 {_f(baseline.get('mean_actual_points'))} 点，"
                 f"{baseline.get('n_truncated')} 条恢复段不完整。")
    L.append("")

    # 三、跨组一致性
    g3 = s["3_cross_group"]
    L.append(f"三、{SECTION_TITLES['3_cross_group']}")
    if g3["available"]:
        t1 = g3.get("top1_by_group") or {}
        L.append("  三组 Top-1：" + "，".join(f"{gn}={t1.get(gn)}" for gn in t1) + "。")
        L.append(f"  Top-1 跨组是否相同：{'是' if g3.get('top1_stable') else '否'}；"
                 f"三组共同入榜变量 {g3.get('n_vars_in_all_groups')} 个。")
        L.append("  跨组一致性数值（Top-5 重叠 / Jaccard / Spearman / 名次标准差 / 单调占比）"
                 "已撤出结果层：实测与随机不可区分（Jaccard 0.2632 vs 随机 0.2144、"
                 "名次标准差 1.89–2.87 vs 3.22–3.40、单调占比 1/15–5/15 vs 0.27–0.33），"
                 "原值仅内部留档，不得读作“稳定”。")
        d = g3["diff"]
        if d["available"]:
            L.append(f"  差异报告：Top-1 切换 {d['n_top1_switches']} 次，"
                     f"机理切换点 {d['n_mechanism_switches']} 个，"
                     f"边数差异超阈值组对 {d['n_edge_count_diffs']} 个。")
        else:
            L.append(f"  {_miss(d.get('reason') or 'diff 产物缺失')}")
    else:
        L.append(f"  {_miss(g3.get('reason') or 'summary 产物缺失')}")
    L.append("")

    # 四、K2
    g4 = s["4_physics_k2"]
    L.append(f"四、{SECTION_TITLES['4_physics_k2']}")
    if g4.get("available"):
        L.append(f"  适用关系 {g4['n_relations']} 条：违反 {g4['n_violation']}，"
                 f"未违反 {g4['n_not_violated']}，不可判定 {g4['n_undecidable']}"
                 f"（不可判定比例 {_pct(g4['undecidable_ratio'])}）。")
        for r in g4.get("relations") or []:
            L.append(f"    {r['relation_id']}: {r['status']}")
        L.append(f"  来源：{g4['source']}；{g4.get('note')}")
    else:
        L.append(f"  {_miss(g4.get('reason') or 'K2 产物缺失')}")
    L.append("")

    # 五、K3
    g5 = s["5_fault_template_k3"]
    L.append(f"五、{SECTION_TITLES['5_fault_template_k3']}")
    if g5.get("available"):
        L.append(f"  expected_chain {g5.get('n_chain_edges')} 条相邻边中命中 "
                 f"{g5.get('n_matched')} 条，覆盖率 {_pct(g5['coverage'])}；"
                 f"verification={g5.get('verification')}。")
        L.append(f"  稳定边反向 {g5['n_reverse_edges']} 条（{LE.REVERSE_EDGE_NOTE}）。")
        if g5.get("reverse_edges"):
            L.append("  反向边：" + "、".join(_fmt_edge(e) for e in g5["reverse_edges"]))
        if g5.get("missing_edges"):
            L.append("  模板链缺失：" + "、".join(_fmt_edge(e) for e in g5["missing_edges"]))
    else:
        L.append(f"  {_miss(g5.get('reason') or 'K3 模板缺失')}")
    L.append("")

    # 六、偏离度
    g6 = s["6_deviation"]
    L.append(f"六、{SECTION_TITLES['6_deviation']}")
    if g6.get("available"):
        L.append(f"  偏离通道 d（{g6.get('n_variables')} 个变量）的档位计数已撤出结果层："
                 "d 的分母是各变量自己的噪声/物理下限，量纲不同（pcm / dk/k / CPM / m3），"
                 "跨变量比较无物理含义（d_raw 到 1.67e5），只能同变量内沿时间看；"
                 "原值仅内部留档。")
        L.append(f"  来源：{g6.get('source')}；{g6.get('note')}")
    else:
        L.append(f"  {_miss(g6.get('reason') or '偏离度产物缺失')}")
    L.append("")

    # 七、局限
    L.append(f"七、{SECTION_TITLES['7_limitations']}")
    items = s["7_limitations"]["items"]
    if items:
        for it in items:
            L.append(f"  - {it}")
    else:
        L.append("  未登记局限项")
    L.append("")

    # 八、复现
    g8 = s["8_reproducibility"]
    L.append(f"八、{SECTION_TITLES['8_reproducibility']}")
    L.append(f"  run_id：{g8['run_id']}")
    L.append(f"  命令：{g8['command']}")
    L.append(f"  上游全链：{g8['upstream_command']}")
    if g8["inputs"]:
        L.append("  输入 sha256：")
        for i in g8["inputs"]:
            L.append(f"    {i['path']}  {i['sha256']}")
    else:
        L.append(f"  {_miss('未解析到输入文件')}")
    L.append("")
    L.append("（本报告由规则模板生成，全部数字来自同 run_id 的 v2.3 JSON 产物。）")
    return "\n".join(L)


def write_report(report: Dict) -> Dict[str, str]:
    """落盘 <run 根>/reports/rca_report_{K}_{g}.json 与 .txt（txt 带 .meta.json）。"""
    k, g = report["accident_type"], report["group"]
    name = f"rca_report_{k}_{g}"
    run_id = report["run_id"]
    source = ("tracing/root_candidates_{k}_{g}.json;signatures/signature_{k}_{g}.json;"
              "reports/summary_{k}.json;reports/diff_{k}.json").format(k=k, g=g)
    json_path = artifact.write_artifact(
        name, report, "reports", run_id=run_id, accident_type=k, group=g,
        source=source)
    txt_path = artifact.write_text_artifact(
        name, report_to_text(report), "reports", run_id=run_id,
        accident_type=k, group=g, source=source)
    return {"json": str(json_path), "txt": str(txt_path)}


def run_all_reports(classes: Optional[List[str]] = None,
                    groups: Optional[List[str]] = None,
                    run_id: Optional[str] = None,
                    banner_note: Optional[str] = None) -> List[Dict]:
    """3 类 × 3 组 = 9 份报告；单模板失败不掩盖，汇总后抛出。"""
    classes = list(classes or READY_CODES)
    groups = list(groups or GROUPS)
    done: List[Dict] = []
    errors: List[str] = []
    for k in classes:
        for g in groups:
            try:
                rep = generate_rca_report(k, g, run_id=run_id, banner_note=banner_note)
                paths = write_report(rep)
                done.append({"template": rep["template"],
                             "run_id": rep["run_id"], "paths": paths})
            except Exception as exc:  # noqa: BLE001 - 逐模板记录失败原因
                logger.error(f"[D7] {k}_{g} 报告生成失败：{type(exc).__name__}: {exc}")
                errors.append(f"{k}_{g}: {type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError(f"报告生成失败 {len(errors)} 份：{errors}")
    return done


if __name__ == "__main__":
    r = generate_rca_report("LOCA", "mid")
    print(report_to_text(r))

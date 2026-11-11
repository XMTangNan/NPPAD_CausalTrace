"""第八步（D7）主路径：判定式解读报告 = 算法排序 + 模型判定 + 规则兜底。

**产物定义变更（V23-DESIGN-065）**：第八步的人读交付物是
``reports/rca_report_{K}_{g}.txt``，其中"Top-5 逐个四态判定与机理"由 LLM 依据证据包写，
不再是纯规则稿；``reports/llm_explanation_{K}_{g}.json`` 降为**证据附件**
（证据包 + 校验记录 + lineage），不再是人读正文。

固定章节顺序（不可增删、不可重排）：
  1 图结构与 Top-5（算法给定）→ 2 逐候选四态判定与依据（模型）
  → 3 冲突与不确定项（模型 + 程序预计算）→ 4 结论（模型）→ 5 局限性与复现信息

降级：任一校验不过（越界变量 / 编造数值 / 知识 id 不存在 / 擅自重排 / 调用失败），
该模板落回规则稿，并在抬头写 ``未经模型判断``，失败原因写进证据附件 json 的
``validation`` 与 ``degraded_reason``。默认开启，不再需要环境变量开关。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from causalrca.io.logging import logger
from causalrca.io import artifact
from causalrca.reporting import explanation_verdict as EV
from causalrca.reporting import llm_explanation as LE
from causalrca.reporting import report_template as RT

SECTION_ORDER = ["1_graph_top5", "2_candidate_verdicts", "3_conflicts_uncertainty",
                 "4_conclusion", "5_limitations"]
SECTION_TITLES = {
    "1_graph_top5": "图结构与 Top-5（算法给定，不可重排）",
    "2_candidate_verdicts": "逐候选四态判定与依据（模型判定）",
    "3_conflicts_uncertainty": "冲突与不确定项",
    "4_conclusion": "结论（模型，基于上述证据）",
    "5_limitations": "局限性与复现信息",
}
VERDICT_CN = {"supported": "有支撑", "partially_supported": "部分支撑",
              "unsupported": "无支撑", "undetermined": "无法判断"}


def _f(x: Any, nd: int = 4) -> str:
    if x is None:
        return "未产出"
    try:
        return f"{float(x):.{nd}g}"
    except (TypeError, ValueError):
        return str(x)


def _node_map(pack: Dict) -> Dict[str, Dict]:
    return {n["variable"]: n for n in pack.get("nodes") or []}


def _edge_coef(pack: Dict) -> Dict[str, Dict]:
    return {f"{e['from']}->{e['to']}": e for e in pack.get("stable_edges") or []}


def _section_graph_top5(payload: Dict, pack: Dict) -> List[str]:
    L: List[str] = []
    alg = pack.get("algorithm") or {}
    L.append("一、图结构与 Top-5（算法给定，不可重排）")
    L.append(f"  入模变量 {alg.get('n_variables')} 个，稳定边 {alg.get('n_stable_edges')} 条，"
             f"bootstrap {alg.get('n_bootstrap')} 次（{alg.get('engine')} 单引擎）。")
    L.append(f"  排序键：{alg.get('rank_key')}；Top-k = {alg.get('top_k')}。")
    L.append("")
    L.append("  Top-5（名次由算法给出，模型不得改动）：")
    for c in pack.get("candidates") or []:
        L.append(f"    第{c['rank']}名 {c['variable']}（总效应 {_f(c.get('total_effect'))}；"
                 f"入度 {c.get('in_degree')}，出度 {c.get('out_degree')}，"
                 f"可达 {c.get('reach_size')}）")
    L.append("")
    L.append("  候选的稳定入/出边与系数见第二章（系数为 bootstrap 中位权重）。")
    L.append("")
    return L


def _section_verdicts(pack: Dict, verdicts: List[Dict]) -> List[str]:
    L: List[str] = []
    edge = _edge_coef(pack)
    nodes = _node_map(pack)
    L.append("二、逐候选四态判定与依据（模型判定）")
    for v in sorted(verdicts, key=lambda x: x.get("rank") or 0):
        var = v.get("variable")
        info = nodes.get(var) or {}
        L.append("")
        L.append(f"  #{v.get('rank')} {var} — {VERDICT_CN.get(v.get('verdict'), v.get('verdict'))}"
                 + (f"（{info.get('meaning_cn')}，{info.get('subsystem')}）"
                    if info.get("meaning_cn") else ""))
        basis = v.get("graph_basis") or []
        if basis:
            L.append("    图依据：" + "；".join(
                f"{b}({_f((edge.get(b) or {}).get('weight_median'))})" for b in basis))
        else:
            L.append("    图依据：未引用稳定边")
        if v.get("knowledge_basis"):
            L.append("    知识依据：" + "、".join(v["knowledge_basis"]))
        L.append(f"    机理：{v.get('mechanism') or '未给出'}")
        if v.get("conflicts"):
            L.append("    冲突：" + "；".join(v["conflicts"]))
    L.append("")
    return L


def _section_conflicts(pack: Dict, doc: Dict) -> List[str]:
    L: List[str] = []
    L.append("三、冲突与不确定项")
    pre = pack.get("conflicts") or []
    if pre:
        L.append("  3.1 程序预计算冲突（读证据包时已确定，非模型生成）：")
        for c in pre:
            subj = c.get("subject") or c.get("variable") or "-"
            L.append(f"    - {subj}：{c.get('type')} — {c.get('detail')}")
    notes = doc.get("conflict_notes") or []
    if notes:
        L.append("  3.2 模型识别的冲突：")
        L.extend(f"    - {n}" for n in notes)
    unc = doc.get("uncertainty_notes") or []
    if unc:
        L.append("  3.3 不确定项（缺哪条证据）：")
        L.extend(f"    - {n}" for n in unc)
    diag = pack.get("graph_diagnosis") or []
    verdicts = doc.get("verdicts") or []
    if diag:
        L.append("  3.4 图为何这样排（程序诊断，只列可复核的图事实）：")
        for d in diag:
            L.append(f"    - [{d.get('type')}] {d.get('subject')}：{d.get('detail')}")
    if not (pre or notes or unc or diag):
        L.append("  未记录冲突或不确定项。")
    L.append("")
    return L


def _section_conclusion(doc: Dict, pack: Dict) -> List[str]:
    L: List[str] = ["四、结论（模型，基于上述证据）"]
    text = (doc.get("conclusion") or "").strip()
    if text:
        L.append("  " + text)
    else:
        top = (pack.get("candidates") or [{}])[0]
        L.append("  模型未给出结论句；仅保留算法排序事实："
                 f"第 1 名 {top.get('variable')}（总效应 {_f(top.get('total_effect'))}）。")
    L.append("")
    return L


def _section_limits(payload: Dict, meta: Dict) -> List[str]:
    L: List[str] = ["五、局限性与复现信息"]
    for item in payload.get("limitations") or []:
        L.append(f"  - {item}")
    L.append("  - 本报告不含 hit@k / MRR / 跨组 Jaccard / 条件数门 / 偏差 d 档位等已撤出结果层的指标"
             "（见方案 §7.10）。")
    L.append("")
    L.append(f"  lineage：本次仅重生成报告，图与签名复用 {meta['source_run_id']}，未重学图。")
    L.append(f"  复现：{meta['command']}")
    if meta.get("evidence_sha256"):
        L.append(f"  证据包 sha256：{meta['evidence_sha256']}")
    if meta.get("mode") != "llm_verified":
        L.append(f"  校验/降级：{meta.get('degraded_reason') or '未知原因'}")
    L.append("")
    return L


def render_verdict_report(payload: Dict, pack: Dict, out: Dict, meta: Dict) -> str:
    """按固定章节顺序渲染人读报告；正文判定段来自模型，其余为程序陈述。"""
    doc = out.get("verdict") or {}
    verdicts = doc.get("verdicts") or []
    k, g = payload["accident_type"], payload["group"]
    L: List[str] = []
    L.append(f"=== CausalTrace v2.3 · RCA 解读报告：{k} · {g} ===")
    L.append(f"run_id：{meta['run_id']}（lineage：{meta['source_run_id']}）")
    if meta["mode"] == "llm_verified":
        L.append("解释模式：模型判定稿（排序由算法给定，模型只判合理性、不改名次）")
    else:
        L.append("解释模式：**未经模型判断**（规则稿兜底；失败原因见下与证据附件 json）")
        if meta.get("degraded_reason"):
            L.append(f"  降级原因：{meta['degraded_reason']}")
        if out.get("validation", {}).get("errors"):
            L.append("  校验错误：" + "；".join(out["validation"]["errors"][:6]))
    L.append("根因定义：同类事故内最先动起来、并把影响传下去的变量（方案 §0.1）；")
    L.append("          本报告的“根因”是以传导起点为定义的根因，不是经现场证据确认的唯一物理起因。")
    L.append("")
    L.append("章节顺序固定：1 图结构与 Top-5 → 2 逐候选判定 → 3 冲突与不确定 → 4 结论 → 5 局限与复现")
    L.append("")
    L += _section_graph_top5(payload, pack)
    L += _section_verdicts(pack, verdicts)
    L += _section_conflicts(pack, doc)
    L += _section_conclusion(doc, pack)
    L += _section_limits(payload, meta)
    return "\n".join(L).rstrip() + "\n"


def generate_one(source_run_id: str, k: str, g: str,
                 out_run_id: Optional[str] = None,
                 timeout: Optional[float] = None) -> Dict[str, Any]:
    """生成单个模板的判定式报告；失败按规则稿兜底。返回落盘摘要。"""
    out_run_id = out_run_id or f"{source_run_id}-r8"
    # 读取阶段必须绑定源 run：`LE.build_payload` 内部用 `_read_optional`（不接 run_id 实参）。
    artifact.bind_run(source_run_id)
    payload = LE.build_payload(k, g, run_id=source_run_id)
    out = EV.run_template_verdict(source_run_id, k, g, timeout=timeout)
    pack = out.get("evidence_pack") or EV.build_evidence_pack(source_run_id, k, g)
    mode = out.get("explanation_mode") or "rule_fallback"
    # 写入阶段切到新 run（report-only），源 run 一个文件都不碰。
    artifact.bind_run(out_run_id)

    meta = {
        "run_id": out_run_id,
        "source_run_id": source_run_id,
        "mode": mode,
        "degraded_reason": out.get("degraded_reason"),
        "evidence_sha256": out.get("evidence_sha256"),
        "command": ("cd Code && PYTHONPATH=. ../.venv/bin/python -m "
                    f"causalrca.cli.run_v23_step8_report --source-run-id {source_run_id} "
                    f"--template {k}_{g}"),
    }
    if mode == "llm_verified":
        text = render_verdict_report(payload, pack, out, meta)
    else:
        # 规则稿兜底需要重新读 payload；此刻进程已绑定到 out_run（写入阶段），
        # 而 `_read_optional` 只认绑定 run，故必须先切回源 run，否则会误报“缺 tracing”。
        artifact.bind_run(source_run_id)
        head = [f"=== CausalTrace v2.3 · RCA 解读报告：{k} · {g} ===",
                f"run_id：{out_run_id}（lineage：{source_run_id}）",
                "解释模式：**未经模型判断**（判定式解释校验未过，回落规则稿）",
                f"  降级原因：{out.get('degraded_reason') or '未知'}",
                "  校验错误：" + "；".join((out.get("validation") or {}).get("errors", [])[:6]),
                "", "—— 以下为规则稿正文（非模型判定）——", ""]
        text = "\n".join(head) + RT.report_to_text(RT.generate_rca_report(k, g, run_id=source_run_id))

    txt_path = artifact.write_text_artifact(
        f"rca_report_{k}_{g}", text, "reports", run_id=out_run_id,
        source=source_run_id, accident_type=k, group=g,
        explanation_mode=mode, validation_ok=bool((out.get("validation") or {}).get("ok")))
    ev_doc = {
        "schema": "causaltrace/step8_evidence_attachment/v1",
        "run_id": out_run_id,
        "lineage": {"source_run_id": source_run_id, "regenerated_at":
                    datetime.now().isoformat(timespec="seconds"),
                    "graph_reused": True, "report_only": True},
        "template_id": f"{k}_{g}",
        "explanation_mode": mode,
        "llm": out.get("llm"),
        "validation": out.get("validation"),
        "degraded_reason": out.get("degraded_reason"),
        "evidence_sha256": out.get("evidence_sha256"),
        "verdict": out.get("verdict"),
        "evidence_pack": pack,
        "human_readable": f"reports/rca_report_{k}_{g}.txt",
    }
    json_path = artifact.write_artifact(
        f"llm_explanation_{k}_{g}", ev_doc, "reports", run_id=out_run_id,
        accident_type=k, group=g, source=source_run_id)
    logger.info(f"[D7] {k}_{g} mode={mode} txt={txt_path} json={json_path}")
    return {"template": f"{k}_{g}", "mode": mode, "run_id": out_run_id,
            "source_run_id": source_run_id, "txt": str(txt_path), "json": str(json_path),
            "validation": out.get("validation"),
            "degraded_reason": out.get("degraded_reason")}

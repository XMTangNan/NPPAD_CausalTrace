"""v2.3 C3 图件：只读 signatures/ + tracing/，生成分组图与汇总图（不重算因果）。

产物（全部经 rca.v21.artifact 落盘到 output/rca_v23/graphs/）：
- graph_{K}_{g}.html        12 类 × 3 组的传导图（每组一张）
- graph_{K}_summary.html    单类三组并集图 + 频次投票榜
- graph_all_summary.html    全类总览

图内元素（方案 v2.3 C3）：
- 节点=变量（marker 高亮，Root Top-5 描边加粗，Top1 最粗）
- 边=稳定边（粗细=f_e，箭头=方向），单引擎状态下每条边 consensus=null
- 面板=Top-5 根因表（变量/总效应/排名/来源路径数）+ C3a 信息门控说明 + 图例
- 辅助证据面板（D3）：K2 残差三态小表、K3 故障模板覆盖条、偏离度四色边框/图例，
  统一注明"基础排序仅用 T=(I−B)⁻¹ 总效应；K2/K3/偏离度为辅助证据，不改变排名"
- 图例口径：稳定边（LiNGAM bootstrap f_e≥0.70, dir_e≥0.90），禁止称"共识边"

用法（cd Code）：
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_graphs --run-id v23c-...
"""
from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from rca.rca_config_v21 import GROUP_ORDER, MARKERS, READY_CODES
from rca.v21 import artifact
from rca.v21 import graph_visualization as GV
from rca.v21.knowledge import ENTITY_TABLE
from core.utils import logger

STABLE_LEGEND = "稳定边（LiNGAM bootstrap f_e≥0.70, dir_e≥0.90）"
ENGINE_LEGEND = ("单引擎口径：仅 LiNGAM，GAM 共识未接入（consensus=null），"
                 "稳定边不代表多引擎一致")
# 图上口径（禁止出现"共识边"字样：单引擎下不存在多引擎一致性的边）
GRAPH_NOTE = "单引擎结果：稳定边不代表多引擎一致（GAM 共识未接入，consensus=null）"

_CSS = """
body{font-family:"Segoe UI","Microsoft YaHei",sans-serif;margin:0;background:#f7f9fb;color:#1a1a2e}
header{padding:18px 26px;background:#1a1a2e;color:#fff}
header h1{margin:0 0 6px;font-size:19px}
header .meta{font-size:12px;color:#bdc3c7;font-family:Consolas,monospace}
.wrap{display:flex;gap:18px;padding:18px 26px;align-items:flex-start;flex-wrap:wrap}
.card{background:#fff;border:1px solid #e1e6ea;border-radius:8px;padding:14px 16px;
box-shadow:0 1px 3px rgba(0,0,0,.05)}
h2{font-size:14px;margin:0 0 10px;color:#2c3e50;letter-spacing:.3px}
table{border-collapse:collapse;font-size:12px}
th,td{padding:5px 9px;border-bottom:1px solid #eceff1;text-align:left}
th{background:#f2f5f7;color:#34495e;font-weight:600}
td.num{text-align:right;font-family:Consolas,monospace}
.legend span{display:inline-flex;align-items:center;gap:6px;margin-right:14px;font-size:12px}
.sw{display:inline-block;width:26px;height:0;border-top-width:2px;border-top-style:solid}
.rank1{font-weight:700;color:#c0392b}
.note{font-size:11.5px;color:#5d6d7e;line-height:1.7}
.gate{background:#fdf6e3;border-left:3px solid #f39c12;padding:9px 12px;font-size:12px}
.bar{height:12px;background:#eceff1;border-radius:6px;overflow:hidden;margin:6px 0}
.bar>div{height:100%;background:#2980b9}
.dot{display:inline-block;width:10px;height:10px;border-radius:2px}
.foot{padding:10px 26px 26px;font-size:11px;color:#7f8c8d}
"""


# ==================== 读取 ====================
def _load(sub: str, name: str) -> Dict:
    path = Path(artifact.V23_SUBDIRS[sub]) / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"缺产物 {path}（先跑 run_v23_end2end）")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _check_run_id(doc: Dict, name: str, run_id: str) -> None:
    got = doc.get("run_id")
    if got != run_id:
        raise ValueError(f"{name} 的 run_id={got} 与 --run-id={run_id} 不一致，"
                         f"拒绝出图（禁止混源）")


def _try_load(sub: str, name: str, run_id: str) -> Optional[Dict]:
    path = Path(artifact.V23_SUBDIRS[sub]) / f"{name}.json"
    if not path.exists():
        return None
    doc = _load(sub, name)
    _check_run_id(doc, name, run_id)
    return doc


def _write(name: str, body: str, run_id: str, k: Optional[str],
           g: Optional[str], source: str) -> None:
    artifact.write_text_artifact(name, body, "graphs", run_id=run_id,
                                 accident_type=k, group=g, source=source, suffix=".html")


# ==================== 公共片段 ====================
def _head(title: str, run_id: str, generated_at: str, subtitle: str) -> str:
    return (f'<header><h1>{html.escape(title)}</h1>'
            f'<div class="meta">run_id={html.escape(run_id)} &nbsp;|&nbsp; '
            f'生成时间={html.escape(generated_at)} &nbsp;|&nbsp; '
            f'{html.escape(subtitle)}</div></header>')


def _legend_block() -> str:
    return (
        '<div class="legend">'
        f'<span><i class="sw" style="border-top:2px solid #2c3e50"></i>{STABLE_LEGEND}</span>'
        '<span><i class="sw" style="border-top:3px solid #7f8c8d"></i>粗细=f_e（效应频率）</span>'
        '<span>箭头 = 因果方向 from→to</span>'
        '<span>描边加粗 = Root Top-5（Top1 最粗）</span>'
        '<span style="background:#e74c3c;color:#fff;padding:1px 6px;border-radius:3px">'
        'marker 故障指示器</span>'
        '</div>'
        f'<div class="note" style="margin-top:6px">{html.escape(ENGINE_LEGEND)}</div>')


def _svg_defs() -> str:
    return ('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="#5d6d7e"/></marker></defs>')


_ROLE_LABEL = {
    "injection_source": "故障指示器",
    "physical_root": "物理根因",
    "upstream_driver": "上游驱动",
    "conduction_hub": "传导枢纽",
    "intermediate": "中间节点",
    "terminal": "末端症状",
}

# ==================== 辅助证据（K2/K3/偏离度，均不改排名）====================
DEVIATION_COLORS = {
    "normal": "#2ecc71",     # 正常-绿
    "moderate": "#f1c40f",   # 中等-黄
    "large": "#e67e22",      # 较大-橙
    "severe": "#e74c3c",     # 严重-红
}
DEVIATION_LEGEND = ("通道偏移强度（模板内相对）：档位按本模板 d_raw 的 p50/p70/p85 切分，"
                    "p95 以上标“极端”；d=min(d_raw,10) 仅展示；≠根因")
AUX_RANK_NOTE = ("基础排序仅用 T=(I−B)⁻¹ 总效应；K2/K3/偏离度为辅助证据，不改变排名")
K2_STATUS_LABEL = {"violation": "违反", "not_violated": "未违反", "undecidable": "不可判定"}
K2_STATUS_COLOR = {"violation": "#e74c3c", "not_violated": "#2ecc71",
                   "undecidable": "#95a5a6"}


def _k2_card(k2: Optional[Dict]) -> str:
    if not k2:
        return ('<div class="card" style="max-width:430px"><h2>物理一致性（K2）</h2>'
                '<div class="note">本 run 签名未含 auxiliary.k2（D 阶段签名重算后填充）。</div>'
                f'<div class="note">{html.escape(AUX_RANK_NOTE)}</div></div>')
    rows = ""
    for r in k2.get("relations", []):
        st = r.get("status", "")
        color = K2_STATUS_COLOR.get(st, "#7f8c8d")
        corr = r.get("correlation")
        rows += (f'<tr><td>{html.escape(str(r.get("relation_id", "-")))}</td>'
                 f'<td style="color:{color};font-weight:600">'
                 f'{html.escape(K2_STATUS_LABEL.get(st, st))}</td>'
                 f'<td class="note">{html.escape(str(r.get("reason", "-")))}</td>'
                 f'<td class="num">{"" if corr is None else corr}</td></tr>')
    return (
        '<div class="card" style="max-width:430px"><h2>物理一致性（K2 残差三态）</h2>'
        f'<div class="note">代表严重度 sev={k2.get("representative_severity")}；'
        f'主窗 {k2.get("window")}；适用 {k2.get("n_relations_applicable")} 条：'
        f'违反 {k2.get("n_violation")} / 未违反 {k2.get("n_not_violated")} / '
        f'不可判定 {k2.get("n_undecidable")}</div>'
        '<table><tr><th>关系</th><th>三态</th><th>原因</th><th>相关系数</th></tr>'
        f'{rows}</table>'
        '<div class="note" style="margin-top:6px">满足约束≠方向证明、违反≠链错'
        f'（可能遗漏项导致）。{html.escape(AUX_RANK_NOTE)}</div></div>')


def _k3_card(k3: Optional[Dict]) -> str:
    if not k3 or not k3.get("available", True):
        reason = (k3 or {}).get("reason", "本 run 签名未含 auxiliary.k3（D 阶段签名重算后填充）")
        return ('<div class="card" style="max-width:430px"><h2>故障模板（K3）</h2>'
                f'<div class="note">{html.escape(str(reason))}</div>'
                f'<div class="note">{html.escape(AUX_RANK_NOTE)}</div></div>')
    cov = float(k3.get("coverage") or 0.0)
    rev = "".join(f'<li>{html.escape(r.get("stable", ""))} ⟂ 模板 '
                  f'{html.escape(r.get("template", ""))}</li>'
                  for r in k3.get("reversed_edges", [])) or "<li>无</li>"
    miss = {}
    for m in k3.get("missing_edges", []):
        miss[m.get("reason", "-")] = miss.get(m.get("reason", "-"), 0) + 1
    miss_txt = "，".join(f"{k}={v}" for k, v in miss.items()) or "无"
    return (
        '<div class="card" style="max-width:430px"><h2>故障模板（K3）覆盖</h2>'
        f'<div class="bar"><div style="width:{cov * 100:.1f}%"></div></div>'
        f'<div class="note">coverage=<b>{cov:.1%}</b>'
        f'（命中 {k3.get("n_matched")}/{k3.get("n_chain_edges")} 条模板链边，'
        f'反向 {k3.get("n_reversed")}）；模板={html.escape(str(k3.get("template_code")))}'
        f'，核实={html.escape(str(k3.get("verification")))}</div>'
        f'<div class="note" style="margin-top:6px">反向边：<ul>{rev}</ul></div>'
        f'<div class="note">缺失边：{html.escape(miss_txt)}</div>'
        f'<div class="note" style="margin-top:6px">{html.escape(AUX_RANK_NOTE)}</div></div>')


def _deviation_card(dev: Optional[Dict]) -> str:
    counts = (dev or {}).get("level_counts", {})
    sw = "".join(
        f'<span><i class="dot" style="background:{color}"></i>{name}'
        f'（{counts.get(name, 0)}）</span>'
        for name, color in DEVIATION_COLORS.items())
    return ('<div class="card" style="max-width:340px"><h2>通道偏移强度（模板内相对）</h2>'
            f'<div class="legend" style="flex-wrap:wrap">{sw}</div>'
            f'<div class="note" style="margin-top:6px">代表严重度 '
            f'sev={(dev or {}).get("severity", "-")}，变量数 {(dev or {}).get("n_variables", 0)}；'
            f'{html.escape(DEVIATION_LEGEND)}</div>'
            f'<div class="note">{html.escape(AUX_RANK_NOTE)}</div></div>')


# ==================== 分组图 ====================
def _layout(variables: Sequence[str], nodes: Dict[str, Dict], marker: str
            ) -> Dict[str, Dict[str, float]]:
    """复用 graph_visualization 的 depth 分层布局。"""
    return GV._layout_nodes(list(variables), nodes, marker)


def render_group_html(k: str, g: str, sig: Dict, rc: Dict, topo: Dict,
                      run_id: str, generated_at: str) -> str:
    marker = rc["template"]["marker"]
    stable = sig["stable_edges"]
    nodes_metrics = topo["nodes"]
    roots = rc["root_candidates"]
    root_vars = [c["variable"] for c in roots]
    top1 = root_vars[0] if root_vars else None

    # 辅助证据（签名 auxiliary；缺省则面板退化为"未接入"，不改排名）
    aux = sig.get("auxiliary") or {}
    dev = aux.get("deviation") or {}
    dev_levels = dev.get("levels") or {}

    sub_nodes, sub_edges = GV._extract_conduction_subgraph(stable, root_vars)
    variables = [v for v in topo["variables"] if v in sub_nodes or v == marker]
    positions = _layout(variables, nodes_metrics, marker)
    width = max((p["x"] for p in positions.values()), default=0) + GV.NODE_W + GV.PADDING
    height = max((p["y"] for p in positions.values()), default=0) + GV.NODE_H + GV.PADDING

    edge_svgs: List[str] = []
    for e in sub_edges:
        u, v = e["from"], e["to"]
        if u not in positions or v not in positions:
            continue
        pu, pv = positions[u], positions[v]
        path = GV._edge_path(pu["x"], pu["y"], pv["x"], pv["y"])
        sw = 1.0 + max(0.0, e["f_e"] - 0.70) / 0.30 * 2.5
        tip = (f"{u}->{v} | f_e={e['f_e']:.2f} dir_e={e['dir_e']:.2f} "
               f"weight_median={e.get('weight_median', 0):.4f} | consensus=null（单引擎）")
        edge_svgs.append(
            f'<path d="{path}" fill="none" stroke="#5d6d7e" stroke-width="{sw:.2f}" '
            f'marker-end="url(#arrow)" opacity="0.85"><title>{html.escape(tip)}</title></path>')

    node_svgs: List[str] = []
    for v in variables:
        pos = positions[v]
        info = nodes_metrics.get(v, {})
        role = "injection_source" if v == marker else info.get("role", "intermediate")
        fill = GV.ROLE_COLORS.get(role, GV.DEFAULT_COLOR)
        dev_color = DEVIATION_COLORS.get(dev_levels.get(v, ""))
        if v == marker:
            stroke, sw, fill = "#4a148c", 4.0, "#e74c3c"
        else:
            stroke = dev_color or ("#1a1a2e" if v == top1
                                   else "#34495e" if v in root_vars else "#b9c2c9")
            sw = 4.0 if v == top1 else (2.6 if v in root_vars else 1.2)
        rank = root_vars.index(v) + 1 if v in root_vars else None
        badge = f" #{rank}" if rank else (" ●marker" if v == marker else "")
        meaning = ENTITY_TABLE.get(v, {}).get("meaning", v)
        te = info.get("total_effect")
        tip = (f"{v}（{meaning}）\n角色={_ROLE_LABEL.get(role, role)}\n"
               f"in_degree={info.get('in_degree', 0)} out_degree={info.get('out_degree', 0)}\n"
               f"reach_size={info.get('reach_size', 0)} depth={info.get('depth', 0)}\n"
               f"总效应={'%.4f' % te if isinstance(te, (int, float)) else '-'}\n"
               f"通道偏移强度={dev_levels.get(v, '未计算')}（模板内相对，≠根因）")
        node_svgs.append(
            f'<g><rect x="{pos["x"]:.1f}" y="{pos["y"]:.1f}" width="{GV.NODE_W}" '
            f'height="{GV.NODE_H}" rx="6" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{sw}"><title>{html.escape(tip)}</title></rect>'
            f'<text x="{pos["x"] + GV.NODE_W / 2:.1f}" y="{pos["y"] + 18:.1f}" '
            f'text-anchor="middle" font-size="12.5" font-weight="600" fill="#fff">'
            f'{html.escape(v)}{html.escape(badge)}</text>'
            f'<text x="{pos["x"] + GV.NODE_W / 2:.1f}" y="{pos["y"] + 33:.1f}" '
            f'text-anchor="middle" font-size="10" fill="#f4f6f7">'
            f'{html.escape(str(meaning)[:12])}</text></g>')

    rows = "".join(
        f'<tr><td class="num {"rank1" if c["rank"] == 1 else ""}">{c["rank"]}</td>'
        f'<td><b>{html.escape(c["variable"])}</b></td>'
        f'<td>{html.escape(str(ENTITY_TABLE.get(c["variable"], {}).get("meaning", "-")))}</td>'
        f'<td class="num">{c["total_effect"]:.4f}</td>'
        f'<td class="num">{c["source_paths"]}</td>'
        f'<td class="num">{c.get("reach_size", 0)}</td></tr>'
        for c in roots)

    gating = rc.get("gating", {})
    n_av = gating.get("n_available_vars", len(ENTITY_TABLE))
    n_in = gating.get("n_in_model", len(topo["variables"]))
    ratio = gating.get("gate_ratio", round(n_in / max(n_av, 1), 4))

    data = {
        "run_id": run_id, "generated_at": generated_at,
        "accident_type": k, "group": g, "marker": marker,
        "engine": rc.get("engine", "lingam"),
        "consensus_available": bool(rc.get("consensus_available")),
        "n_stable_edges": rc["n_stable_edges"],
        "n_clipped_edges": rc.get("n_clipped_edges"),
        "cond_IminusB": rc.get("cond_IminusB"),
        "b_clip": rc.get("b_clip"),
        "gating": gating,
        "nodes": [{"id": v, "role": ("injection_source" if v == marker
                                     else nodes_metrics.get(v, {}).get("role")),
                   "depth": nodes_metrics.get(v, {}).get("depth"),
                   "x": round(positions[v]["x"], 1), "y": round(positions[v]["y"], 1)}
                  for v in variables],
        "edges": [{"from": e["from"], "to": e["to"], "f_e": e["f_e"],
                   "dir_e": e["dir_e"], "weight_median": e.get("weight_median"),
                   "consensus": None} for e in sub_edges],
        "root_candidates": roots,
        "auxiliary": aux,
    }

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{k} / {g} 传导图（{run_id}）</title><style>{_CSS}</style></head><body>
{_head(f"{k} · {g} 组 · 因果传导图（单引擎 LiNGAM）", run_id, generated_at,
       f"marker={marker} | 入模变量 {n_in} | 稳定边 {rc['n_stable_edges']}")}
<div class="wrap">
  <div class="card"><h2>传导子图（Root 出发可达）</h2>
    <svg width="{width:.0f}" height="{height:.0f}">{_svg_defs()}{''.join(edge_svgs)}{''.join(node_svgs)}</svg>
    <div style="margin-top:8px">{_legend_block()}</div>
  </div>
  <div class="card"><h2>Root Top-5（按总因果效应）</h2>
    <table><tr><th>排名</th><th>变量</th><th>含义</th><th>总效应</th>
      <th>来源路径数</th><th>传导广度</th></tr>{rows}</table>
    <div class="note" style="margin-top:8px">
      总效应 = T=(I−B)⁻¹ 对症状端（marker + 末端节点）的 |效应| 之和；
      b_clip={rc.get('b_clip')}，n_clipped_edges={rc.get('n_clipped_edges')}，
      cond(I−B)={rc.get('cond_IminusB')}。<br>{html.escape(GRAPH_NOTE)}。
    </div>
  </div>
  <div class="card" style="max-width:340px"><h2>C3a 信息门控</h2>
    <div class="gate">本体 {n_av} 变量中，本组实际入模 <b>{n_in}</b> 个
      （门控比例 {ratio:.1%}）。未入模变量不参与学图与排名，本图仅展示传导子图
      {len(variables)} 个节点 / {len(sub_edges)} 条稳定边。</div>
    <div class="note" style="margin-top:8px">
      边数={rc['n_stable_edges']}，截断边={rc.get('n_clipped_edges')}，
      引擎=lingam，n_bootstrap={rc.get('n_bootstrap')}。
    </div>
  </div>
  {_k2_card(aux.get("k2"))}
  {_k3_card(aux.get("k3"))}
  {_deviation_card(aux.get("deviation"))}
</div>
<div class="foot">NPPAD-CausalTrace v2.3 C 阶段 · 同源产物 run_id={html.escape(run_id)}</div>
<script id="graph-data" type="application/json">{json.dumps(data, ensure_ascii=False)}</script>
</body></html>"""


# ==================== 单类汇总图 ====================
def render_class_summary_html(k: str, run_id: str, generated_at: str,
                              sigs: Dict[str, Dict], rcs: Dict[str, Dict],
                              topos: Dict[str, Dict]) -> str:
    groups = [g for g in GROUP_ORDER if g in sigs]
    marker = rcs[groups[0]]["template"]["marker"]
    edge_groups: Dict[str, set] = defaultdict(set)
    edge_obj: Dict[str, Dict] = {}
    for g in groups:
        for e in sigs[g]["stable_edges"]:
            key = f"{e['from']}->{e['to']}"
            edge_groups[key].add(g)
            edge_obj.setdefault(key, e)

    vote: Dict[str, List[int]] = defaultdict(list)
    rank_by_group: Dict[str, Dict[str, int]] = {}
    for g in groups:
        rank_by_group[g] = {c["variable"]: c["rank"]
                            for c in rcs[g]["root_candidates"]}
        for c in rcs[g]["root_candidates"]:
            vote[c["variable"]].append(c["rank"])

    variables = sorted({e["from"] for e in edge_obj.values()}
                       | {e["to"] for e in edge_obj.values()} | set(vote))
    nodes_metrics = topos[groups[0]]["nodes"]
    positions = _layout(variables, nodes_metrics, marker)
    width = max((p["x"] for p in positions.values()), default=0) + GV.NODE_W + GV.PADDING
    height = max((p["y"] for p in positions.values()), default=0) + GV.NODE_H + GV.PADDING

    shade = {1: "#cfd8dc", 2: "#7f8c8d", 3: "#2c3e50"}
    edge_svgs: List[str] = []
    for key, gs in edge_groups.items():
        e = edge_obj[key]
        u, v = e["from"], e["to"]
        if u not in positions or v not in positions:
            continue
        pu, pv = positions[u], positions[v]
        sw = 0.8 + (len(gs) - 1) * 1.6 + max(0.0, e["f_e"] - 0.7) * 1.5
        edge_svgs.append(
            f'<path d="{GV._edge_path(pu["x"], pu["y"], pv["x"], pv["y"])}" fill="none" '
            f'stroke="{shade[len(gs)]}" stroke-width="{sw:.2f}" marker-end="url(#arrow)" '
            f'opacity="0.85"><title>'
            f'{html.escape(key + " | n_groups=" + str(len(gs)) + "/" + str(len(groups)) + " | consensus=null")}'
            f'</title></path>')

    node_svgs: List[str] = []
    for v in variables:
        pos = positions[v]
        fill = "#e74c3c" if v == marker else GV.ROLE_COLORS.get(
            nodes_metrics.get(v, {}).get("role", "intermediate"), GV.DEFAULT_COLOR)
        stroke, sw = ("#1a1a2e", 3.2) if v in vote else ("#b9c2c9", 1.2)
        node_svgs.append(
            f'<g><rect x="{pos["x"]:.1f}" y="{pos["y"]:.1f}" width="{GV.NODE_W}" '
            f'height="{GV.NODE_H}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="{sw}">'
            f'<title>{html.escape(v)}（{ENTITY_TABLE.get(v, {}).get("meaning", v)}）</title></rect>'
            f'<text x="{pos["x"] + GV.NODE_W / 2:.1f}" y="{pos["y"] + 26:.1f}" '
            f'text-anchor="middle" font-size="12" font-weight="600" fill="#fff">'
            f'{html.escape(v)}</text></g>')

    rows = ""
    for v, rs in sorted(vote.items(), key=lambda kv: (min(kv[1]), kv[0])):
        per = ""
        for g in groups:
            rk = rank_by_group[g].get(v)
            per += f'<td class="num">{rk if rk else "—"}</td>'
        rows += (f'<tr><td><b>{html.escape(v)}</b></td>'
                 f'<td>{html.escape(str(ENTITY_TABLE.get(v, {}).get("meaning", "-")))}</td>'
                 f'{per}<td class="num"><b>{len(rs)}</b></td></tr>')

    group_stat = ""
    for g in groups:
        top1_g = rcs[g]["root_candidates"][0]["variable"] if rcs[g]["root_candidates"] else "-"
        group_stat += (f'<tr><td>{g}</td>'
                       f'<td class="num">{len(rcs[g]["root_candidates"])}</td>'
                       f'<td class="num">{rcs[g]["n_stable_edges"]}</td>'
                       f'<td class="num">{rcs[g].get("n_clipped_edges")}</td>'
                       f'<td class="num">{rcs[g].get("cond_IminusB")}</td>'
                       f'<td><b>{html.escape(str(top1_g))}</b></td></tr>')

    data = {"run_id": run_id, "generated_at": generated_at, "accident_type": k,
            "marker": marker, "groups": groups,
            "edge_group_membership": {kk: sorted(vv) for kk, vv in edge_groups.items()},
            "edges": [{"from": e["from"], "to": e["to"], "f_e": e["f_e"],
                       "dir_e": e["dir_e"], "weight_median": e.get("weight_median"),
                       "n_groups": len(edge_groups[f"{e['from']}->{e['to']}"]),
                       "consensus": None} for e in edge_obj.values()],
            "vote": {v: rs for v, rs in vote.items()},
            "rank_by_group": rank_by_group}
    gcols = "".join(f"<th>{g} 排名</th>" for g in groups)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{k} 三组汇总图（{run_id}）</title><style>{_CSS}</style></head><body>
{_head(f"{k} · 三组汇总（low/mid/high 稳定边并集 + 频次投票）", run_id, generated_at,
       f"marker={marker} | 组数={len(groups)}")}
<div class="wrap">
  <div class="card"><h2>三组稳定边并集</h2>
    <svg width="{width:.0f}" height="{height:.0f}">{_svg_defs()}{''.join(edge_svgs)}{''.join(node_svgs)}</svg>
    <div class="legend" style="margin-top:8px">
      <span><i class="sw" style="border-top:3px solid #2c3e50"></i>3 组均稳定</span>
      <span><i class="sw" style="border-top:3px solid #7f8c8d"></i>2 组稳定</span>
      <span><i class="sw" style="border-top:2px solid #cfd8dc"></i>单组稳定</span>
      <span>箭头=方向</span>
      <span>{STABLE_LEGEND}</span>
    </div>
    <div class="note" style="margin-top:6px">{html.escape(ENGINE_LEGEND)}；
      并集只表示组间稳定性重叠，不代表多引擎一致。</div>
  </div>
  <div class="card"><h2>频次投票榜（三组 Top-5）</h2>
    <table><tr><th>变量</th><th>含义</th>{gcols}<th>入榜组数</th></tr>{rows}</table>
    <h2 style="margin-top:14px">各组关键指标</h2>
    <table><tr><th>组</th><th>候选数</th><th>稳定边</th><th>截断边</th>
      <th>cond(I−B)</th><th>Top1</th></tr>{group_stat}</table>
  </div>
</div>
<div class="foot">NPPAD-CausalTrace v2.3 C 阶段 · 同源产物 run_id={html.escape(run_id)}</div>
<script id="graph-data" type="application/json">{json.dumps(data, ensure_ascii=False)}</script>
</body></html>"""


# ==================== 全类总览 ====================
def render_all_summary_html(rows: List[Dict], run_id: str, generated_at: str,
                            tail: str = "") -> str:
    body = ""
    for r in rows:
        body += (f'<tr><td><b>{r["k"]}</b></td><td>{r["g"]}</td>'
                 f'<td class="num">{r["n_stable_edges"]}</td>'
                 f'<td class="num">{r["n_clipped_edges"]}</td>'
                 f'<td class="num">{r["cond"]}</td>'
                 f'<td class="num">{r["top1_stable"]}</td>'
                 f'<td class="num">{r["overlap5"]}</td>'
                 f'<td>{html.escape(str(r["top1"]))}</td></tr>')
    data = {"run_id": run_id, "generated_at": generated_at, "rows": rows}
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>v2.3 C 阶段全类总览（{run_id}）</title><style>{_CSS}</style></head><body>
{_head("v2.3 C 阶段 · 全类分组总览（单引擎 LiNGAM）", run_id, generated_at,
       f"模板数={len(rows)}")}
<div class="wrap"><div class="card" style="width:100%">
  <h2>各模板关键指标</h2>
  <table><tr><th>类别</th><th>组</th><th>稳定边</th><th>截断边</th>
    <th>cond(I−B)</th><th>clip 敏感性 Top1 稳定</th><th>Top5 最小重叠</th>
    <th>Top1 根因</th></tr>{body}</table>
  <div class="note" style="margin-top:8px">{html.escape(GRAPH_NOTE)}；
    稳定边筛选口径：{html.escape(STABLE_LEGEND)}；截断阈值 b_clip=5.0（敏感性证据见
    tracing/clip_sensitivity_*.json）。{html.escape(tail)}</div>
</div></div>
<div class="foot">NPPAD-CausalTrace v2.3 C 阶段 · 同源产物 run_id={html.escape(run_id)}</div>
<script id="graph-data" type="application/json">{json.dumps(data, ensure_ascii=False)}</script>
</body></html>"""


# ==================== 主流程 ====================
def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="v2.3 C3：只读签名/溯源出图")
    ap.add_argument("--run-id", required=True,
                    help="必须与本批签名/溯源的 run_id 一致，否则拒绝出图")
    ap.add_argument("--classes", default=",".join(READY_CODES))
    ap.add_argument("--groups", default=",".join(GROUP_ORDER))
    ap.add_argument("--no-manifest", action="store_true")
    args = ap.parse_args(argv)
    classes = [c.strip().upper() for c in args.classes.split(",") if c.strip()]
    groups = [g.strip() for g in args.groups.split(",") if g.strip()]
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    overview: List[Dict] = []

    for k in classes:
        if k not in MARKERS:
            raise ValueError(f"未登记的事故类别: {k}")
        sigs, rcs, topos = {}, {}, {}
        for g in groups:
            sig = _try_load("signatures", f"signature_{k}_{g}", args.run_id)
            rc = _try_load("tracing", f"root_candidates_{k}_{g}", args.run_id)
            topo = _try_load("tracing", f"topology_{k}_{g}", args.run_id)
            if sig is None or rc is None or topo is None:
                logger.warning(f"[C3] 跳过 {k}/{g}（本 run 无产物）")
                continue
            sigs[g], rcs[g], topos[g] = sig, rc, topo
            src = f"signatures/signature_{k}_{g}.json;tracing/root_candidates_{k}_{g}.json"
            _write(f"graph_{k}_{g}",
                   render_group_html(k, g, sig, rc, topo, args.run_id, generated_at),
                   args.run_id, k, g, src)
            overview.append({
                "k": k, "g": g, "n_stable_edges": rc["n_stable_edges"],
                "n_clipped_edges": rc.get("n_clipped_edges"),
                "cond": rc.get("cond_IminusB"),
                "top1_stable": (rc.get("clip_sensitivity_top1_stable")
                                if rc.get("clip_sensitivity_top1_stable") is not None
                                else ""),
                "overlap5": "",
                "top1": (rc["root_candidates"][0]["variable"]
                         if rc["root_candidates"] else None),
            })
            logger.info(f"[C3] graph_{k}_{g}.html 已生成")

        if len(sigs) == len(groups):
            _write(f"graph_{k}_summary",
                   render_class_summary_html(k, args.run_id, generated_at,
                                             sigs, rcs, topos),
                   args.run_id, k, None,
                   f"signatures/signature_{k}_*.json")
            logger.info(f"[C3] graph_{k}_summary.html 已生成")
        elif sigs:
            logger.warning(f"[C3] {k} 组不全（{list(sigs)}），不出三组汇总图")

    # 总览补充裁剪敏感性与 Top5 重叠
    for row in overview:
        sens = _try_load("tracing", f"clip_sensitivity_{row['k']}_{row['g']}",
                         args.run_id)
        if sens:
            row["top1_stable"] = "是" if sens.get("top1_stable") else "否"
            row["overlap5"] = sens.get("min_rank_overlap_at_5")
        row["cond"] = row["cond"] if row["cond"] is not None else "—"

    if overview:
        _write("graph_all_summary",
               render_all_summary_html(overview, args.run_id, generated_at),
               args.run_id, None, None, "tracing/clip_sensitivity_*.json")
        logger.info(f"[C3] graph_all_summary.html 已生成（{len(overview)} 模板）")

    if not args.no_manifest:
        artifact.build_manifest(args.run_id)


if __name__ == "__main__":
    main()

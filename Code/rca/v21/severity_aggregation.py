"""V2.3 §4.6 Step3-4: 跨严重度汇总视图 + 差异分析报告。

C3b: 合并 low/mid/high 三组稳定边（并集），边上标注出现组，
     根因排名取三组 Top5 频率投票 → graph_{k}_summary.html
C3c: Top1 切换 / 边数差异>50% / 机理切换点 → diff_report_{k}.json
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Sequence

from rca.v21 import data as D
from core.utils import logger, save_json

GROUP_ORDER = ["low", "mid", "high"]
GROUP_COLORS = {"low": "#3498db", "mid": "#f39c12", "high": "#e74c3c"}
EDGE_DIFF_THRESHOLD = 0.5  # 边数差异 > 50% 触发差异标注


def _load_signature(k: str, g: str) -> Dict:
    return D.load_json(f"v23_signatures/signature_{k}_{g}.json")


def _load_root_candidates(k: str, g: str) -> Dict:
    return D.load_json(f"v23_tracing/root_candidates_{k}_{g}.json")


def build_union_edges(k: str) -> List[Dict]:
    """合并三组稳定边为并集，每条边标注出现组 + 各组 dir_e/f_e。"""
    union: Dict[tuple, Dict] = {}
    for g in GROUP_ORDER:
        sig = _load_signature(k, g)
        for e in sig["stable_edges"]:
            key = (e["from"], e["to"])
            if key not in union:
                union[key] = {"from": e["from"], "to": e["to"], "groups": [], "dir_e": {}}
            union[key]["groups"].append(g)
            union[key]["dir_e"][g] = e["dir_e"]
    return list(union.values())


def frequency_vote_roots(k: str) -> List[Dict]:
    """根因频率投票：某变量在多少组中进入 Top5，按出现组数+均分排名排序。"""
    vote: Dict[str, Dict] = defaultdict(lambda: {"groups": [], "ranks": [], "subsystem": ""})
    for g in GROUP_ORDER:
        rc = _load_root_candidates(k, g)
        for c in rc["root_candidates"][:5]:
            v = c["variable"]
            vote[v]["groups"].append(g)
            vote[v]["ranks"].append(c.get("rank", 99))
            vote[v]["subsystem"] = c.get("entity", {}).get("subsystem", "")
    result = []
    for v, info in vote.items():
        result.append({
            "variable": v,
            "n_groups": len(info["groups"]),
            "groups": info["groups"],
            "avg_rank": round(sum(info["ranks"]) / len(info["ranks"]), 2),
            "subsystem": info["subsystem"],
        })
    result.sort(key=lambda x: (-x["n_groups"], x["avg_rank"]))
    return result


def build_diff_report(k: str) -> Dict:
    """C3c 差异分析报告。"""
    edge_counts = {}
    top1_by_group = {}
    top5_by_group = {}
    for g in GROUP_ORDER:
        sig = _load_signature(k, g)
        rc = _load_root_candidates(k, g)
        edge_counts[g] = len(sig["stable_edges"])
        top1_by_group[g] = rc["root_candidates"][0]["variable"] if rc["root_candidates"] else None
        top5_by_group[g] = [c["variable"] for c in rc["root_candidates"][:5]]

    # Top1 切换
    top1_switches = []
    for i in range(len(GROUP_ORDER) - 1):
        g1, g2 = GROUP_ORDER[i], GROUP_ORDER[i + 1]
        if top1_by_group[g1] != top1_by_group[g2]:
            top1_switches.append({
                "from_group": g1, "to_group": g2,
                "from": top1_by_group[g1], "to": top1_by_group[g2],
            })

    # 边数差异 > 50%
    edge_diffs = []
    for i in range(len(GROUP_ORDER) - 1):
        g1, g2 = GROUP_ORDER[i], GROUP_ORDER[i + 1]
        c1, c2 = edge_counts[g1], edge_counts[g2]
        base = max(c1, 1)
        ratio = abs(c2 - c1) / base
        if ratio > EDGE_DIFF_THRESHOLD:
            edge_diffs.append({
                "from_group": g1, "to_group": g2,
                "count_from": c1, "count_to": c2, "diff_ratio": round(ratio, 3),
            })

    # 机理切换点：Top5 集合 Jaccard 最低的相邻组
    mechanism_switches = []
    for i in range(len(GROUP_ORDER) - 1):
        g1, g2 = GROUP_ORDER[i], GROUP_ORDER[i + 1]
        s1, s2 = set(top5_by_group[g1]), set(top5_by_group[g2])
        jacc = len(s1 & s2) / max(len(s1 | s2), 1)
        if jacc < 0.5:
            mechanism_switches.append({
                "from_group": g1, "to_group": g2,
                "top5_jaccard": round(jacc, 3),
                "only_in_from": list(s1 - s2), "only_in_to": list(s2 - s1),
            })

    return {
        "accident_type": k,
        "edge_counts": edge_counts,
        "top1_by_group": top1_by_group,
        "top5_by_group": top5_by_group,
        "top1_switches": top1_switches,
        "edge_count_diffs": edge_diffs,
        "mechanism_switches": mechanism_switches,
        "summary": (
            f"Top1 切换 {len(top1_switches)} 次; "
            f"边数差异>50% 组对 {len(edge_diffs)} 个; "
            f"机理切换点 {len(mechanism_switches)} 个"
        ),
    }


def render_summary_html(k: str, union_edges: Sequence[Dict],
                        roots: Sequence[Dict], marker: str) -> str:
    """渲染跨严重度汇总图 HTML：边按出现组数着色，右侧频率投票榜。"""
    # 节点集合
    nodes = set()
    for e in union_edges:
        nodes.add(e["from"])
        nodes.add(e["to"])

    # 按出现组数给边着色：3组=红, 2组=橙, 1组=灰
    def edge_color(groups):
        n = len(groups)
        if n == 3:
            return GROUP_COLORS["high"]
        if n == 2:
            return GROUP_COLORS["mid"]
        return "#95a5a6"

    # 简易分层布局：按 BFS 深度
    adj = defaultdict(list)
    for e in union_edges:
        adj[e["from"]].append(e["to"])
    root_vars = [r["variable"] for r in roots]
    depth = {v: -1 for v in nodes}
    for r in root_vars:
        if r in nodes:
            depth[r] = 0
    from collections import deque
    q = deque([r for r in root_vars if r in nodes])
    while q:
        u = q.popleft()
        for v in adj.get(u, []):
            if depth.get(v, -1) < 0:
                depth[v] = depth[u] + 1
                q.append(v)
    # 孤立节点放最后一层
    max_d = max(depth.values()) if depth else 0
    for v in nodes:
        if depth.get(v, -1) < 0:
            depth[v] = max_d + 1

    # 布局坐标
    layers = defaultdict(list)
    for v, d in depth.items():
        layers[d].append(v)
    col_w = 120
    row_h = 50
    pos = {}
    for d, vs in layers.items():
        for i, v in enumerate(vs):
            pos[v] = (d * col_w + 60, (i + 0.5) * row_h)

    width = (max(depth.values()) + 2) * col_w
    height = max(len(v) for v in layers.values()) * row_h + 40

    # 生成 SVG 边
    edge_svg = []
    for e in union_edges:
        x1, y1 = pos[e["from"]]
        x2, y2 = pos[e["to"]]
        color = edge_color(e["groups"])
        g_label = "/".join(e["groups"])
        sw = 1 + len(e["groups"])
        edge_svg.append(
            f'<path d="M {x1} {y1} C {(x1+x2)/2} {y1}, {(x1+x2)/2} {y2}, {x2} {y2}" '
            f'fill="none" stroke="{color}" stroke-width="{sw}" '
            f'opacity="0.7"><title>{e["from"]}->{e["to"]} [{g_label}]</title></path>'
        )

    # 生成 SVG 节点
    node_svg = []
    for v in nodes:
        x, y = pos[v]
        is_root = v in set(root_vars)
        is_top1 = is_root and root_vars and v == root_vars[0]
        if is_top1:
            fill, stroke, fw = "#e74c3c", "#c0392b", "bold"
        elif is_root:
            fill, stroke, fw = "#f39c12", "#e67e22", "normal"
        else:
            fill, stroke, fw = "#ecf0f1", "#bdc3c7", "normal"
        node_svg.append(
            f'<g class="node"><circle cx="{x}" cy="{y}" r="20" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="2"/>'
            f'<text x="{x}" y="{y+4}" text-anchor="middle" font-size="10" '
            f'font-weight="{fw}" fill="#2c3e50">{v}</text></g>'
        )

    # 频率投票榜
    rank_rows = []
    for i, r in enumerate(roots[:10]):
        rank_rows.append(
            f"<tr><td>{i+1}</td><td>{r['variable']}</td>"
            f"<td>{r['subsystem']}</td><td>{r['n_groups']}/3</td>"
            f"<td>{r['avg_rank']}</td></tr>"
        )

    # 图例
    c_high = GROUP_COLORS["high"]
    c_mid = GROUP_COLORS["mid"]
    legend = (
        f'<div style="font-size:11px;margin-top:8px">'
        f'<span style="color:{c_high}">&#9679;</span> 3组共有 '
        f'<span style="color:{c_mid}">&#9679;</span> 2组共有 '
        f'<span style="color:#95a5a6">&#9679;</span> 1组独有 '
        f'| marker={marker}'
        f"</div>"
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{k} 跨严重度汇总图</title></head>
<body style="font-family:sans-serif;margin:16px">
<h2>{k} 跨严重度因果汇总（low/mid/high 边并集 + 根因频率投票）</h2>
<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
{''.join(edge_svg)}
{''.join(node_svg)}
</svg>
{legend}
<h3>根因频率投票榜（出现组数 / 平均排名）</h3>
<table border="1" cellspacing="0" cellpadding="4" style="font-size:12px;border-collapse:collapse">
<tr><th>Rank</th><th>变量</th><th>子系统</th><th>出现组数</th><th>平均排名</th></tr>
{''.join(rank_rows)}
</table>
</body></html>"""
    return html


def run_severity_aggregation(k: str, marker: str) -> Dict:
    """执行 C3b + C3c：生成汇总图与差异报告。"""
    union_edges = build_union_edges(k)
    roots = frequency_vote_roots(k)
    diff = build_diff_report(k)

    html = render_summary_html(k, union_edges, roots, marker)
    out_html = Path(D.V21_OUTPUT) / "v23_graphs" / f"graph_{k}_summary.html"
    out_html.write_text(html, encoding="utf-8")
    logger.info(f"[C3b] {k} 汇总图 -> {out_html.name} ({len(union_edges)} 条并集边)")

    save_json(diff, f"diff_report_{k}", result_dir=Path(D.V21_OUTPUT) / "v23_tracing")
    logger.info(f"[C3c] {k} 差异报告: {diff['summary']}")
    return diff

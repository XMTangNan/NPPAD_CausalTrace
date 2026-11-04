"""v2.2 因果图可视化：0 依赖原生 HTML+SVG（方案 §4）。

仅使用 Python 标准库生成 .html，内嵌 SVG + CSS，不引入任何第三方库。
"""
from __future__ import annotations

import html
from collections import defaultdict, deque
from typing import Dict, List, Optional, Sequence, Set, Tuple

from rca.v21.knowledge import ENTITY_TABLE


# ==================== 视觉编码（方案 §4.3） ====================
ROLE_COLORS = {
    "injection_source": "#e74c3c",   # marker 红
    "physical_root": "#e67e22",      # 物理根因 橙
    "upstream_driver": "#e67e22",    # 上游驱动 橙
    "conduction_hub": "#3498db",     # 传导枢纽 蓝
    "intermediate": "#95a5a6",       # 中间 灰
    "terminal": "#27ae60",           # 末端症状 绿
}
DEFAULT_COLOR = "#95a5a6"

EDGE_COLORS = {
    "consensus": "#2c3e50",          # 黑
    "conflict": "#e74c3c",           # 红
    "single_lingam": "#bdc3c7",      # 灰
}

# 布局参数
NODE_W = 120
NODE_H = 44
H_GAP = 130      # 层间水平间距
V_GAP = 22       # 层内节点垂直间距
PADDING = 40


def _node_role(
    var: str,
    marker: str,
    root_roles: Dict[str, str],
    in_deg: int,
    out_deg: int,
) -> str:
    """确定节点角色。"""
    if var == marker:
        return "injection_source"
    if var in root_roles:
        return root_roles[var]
    if out_deg == 0:
        return "terminal"
    if in_deg <= 1:
        return "physical_root" if out_deg < 3 else "upstream_driver"
    if in_deg >= 2 and out_deg >= 2:
        return "conduction_hub"
    return "intermediate"


def _layout_nodes(
    variables: Sequence[str],
    topology: Dict[str, Dict],
    marker: str,
) -> Dict[str, Dict[str, float]]:
    """按 depth 分层布局，返回每个节点的 {x, y} 坐标。"""
    # 按 depth 分组
    layers: Dict[int, List[str]] = {}
    for v in variables:
        d = topology.get(v, {}).get("depth", 0)
        if d < 0:
            d = 99  # 不可达节点放最右
        layers.setdefault(d, []).append(v)

    # 层内按变量名字典序排列
    for d in layers:
        layers[d].sort()

    max_depth = max(layers.keys()) if layers else 0
    positions: Dict[str, Dict[str, float]] = {}

    layer_max = max(len(ly) for ly in layers.values()) if layers else 1
    layer_total_h = layer_max * NODE_H + (layer_max - 1) * V_GAP

    for d in sorted(layers.keys()):
        nodes = layers[d]
        n = len(nodes)
        total_h = n * NODE_H + (n - 1) * V_GAP
        # 每层垂直居中于画布
        y_start = PADDING + (layer_total_h - total_h) / 2
        for i, v in enumerate(nodes):
            x = PADDING + d * (NODE_W + H_GAP)
            y = y_start + i * (NODE_H + V_GAP)
            positions[v] = {"x": x, "y": y}

    return positions


def _edge_path(
    x1: float, y1: float, x2: float, y2: float,
) -> str:
    """生成贝塞尔曲线路径（从节点右边到节点左边）。"""
    sx = x1 + NODE_W
    sy = y1 + NODE_H / 2
    tx = x2
    ty = y2 + NODE_H / 2
    mx = (sx + tx) / 2
    return f"M {sx:.1f} {sy:.1f} C {mx:.1f} {sy:.1f}, {mx:.1f} {ty:.1f}, {tx:.1f} {ty:.1f}"


def _extract_conduction_subgraph(
    stable_edges: Sequence[Dict],
    root_vars: Sequence[str],
) -> Tuple[Set[str], List[Dict]]:
    """提取传导子图：根变量 + 它们能传导到的所有下游变量。

    返回 (subgraph_nodes, subgraph_edges)：
      subgraph_nodes: 根变量 ∪ 从根变量出发 BFS 可达的所有变量
      subgraph_edges: 两端都在 subgraph_nodes 中的稳定边
    """
    adj: Dict[str, List[str]] = defaultdict(list)
    for e in stable_edges:
        adj[e["from"]].append(e["to"])

    # 从所有根变量出发 BFS
    visited: Set[str] = set(root_vars)
    q = deque(root_vars)
    while q:
        u = q.popleft()
        for v in adj.get(u, []):
            if v not in visited:
                visited.add(v)
                q.append(v)

    # 只保留两端都在子图中的边
    sub_edges = [e for e in stable_edges if e["from"] in visited and e["to"] in visited]
    return visited, sub_edges


def render_graph_html(
    stable_edges: Sequence[Dict],
    topology: Dict[str, Dict],
    consensus_clf: Dict[str, Dict],
    root_candidates: Sequence[Dict],
    marker: str,
    template_name: str,
    deviation_data: Optional[Dict[str, Dict]] = None,
) -> str:
    """生成精简的 HTML+SVG 因果图：仅显示传导子图（根变量 + 受影响下游）。

    设计原则：
    - 只显示与溯源相关的变量（根变量及其传导可达的下游），剔除无关/孤立变量
    - Top1 根变量标红加粗（最可能根因），Top 2-5 橙色，其余传导节点灰色
    - marker 作为普通变量，不特殊标注
    - 右侧显示 Top 5 根变量排行榜
    """
    root_vars = [c["variable"] for c in root_candidates]
    root_set = set(root_vars)
    top1_var = root_vars[0] if root_vars else None

    # 只保留 consensus 边（用户要求：去掉 conflict 和 single_lingam 边）
    cons_edges = [
        e for e in stable_edges
        if consensus_clf.get(f"{e['from']}->{e['to']}", {}).get("label") == "consensus"
    ]

    # C3a 信息门控：consensus 边数 < 5 标注"信息不足"，根因排名标为"不可靠"
    info_insufficient = len(cons_edges) < 5
    if info_insufficient:
        logger.info(f"[C3a] {template_name}: consensus 边数={len(cons_edges)} < 5 → 信息不足水印")

    # 提取传导子图（仅基于 consensus 边）
    sub_nodes, sub_edges = _extract_conduction_subgraph(cons_edges, root_vars)

    # 从根变量出发的 BFS 深度（用于分层布局）
    adj: Dict[str, List[str]] = defaultdict(list)
    for e in sub_edges:
        adj[e["from"]].append(e["to"])

    depth: Dict[str, int] = {v: -1 for v in sub_nodes}
    for r in root_vars:
        depth[r] = 0
    q = deque(root_vars)
    while q:
        u = q.popleft()
        for v in adj.get(u, []):
            if depth.get(v, -1) == -1:
                depth[v] = depth[u] + 1
                q.append(v)
    # 兜底
    max_d = max((d for d in depth.values() if d >= 0), default=0)
    for v in sub_nodes:
        if depth.get(v, -1) == -1:
            depth[v] = max_d + 1

    # 分层布局 + 交叉最小化（barycenter 启发式）
    layers: Dict[int, List[str]] = {}
    for v in sub_nodes:
        d = depth[v]
        layers.setdefault(d, []).append(v)

    # 第一层（根变量）按 tracing_score 降序
    layer0 = layers.get(0, [])
    layer0.sort(key=lambda v: -topology.get(v, {}).get("tracing_score", 0))
    layers[0] = layer0

    # 邻接表（正向+反向，用于 barycenter）
    fwd: Dict[str, List[str]] = defaultdict(list)   # u -> 下游
    bwd: Dict[str, List[str]] = defaultdict(list)   # v -> 上游
    for e in sub_edges:
        fwd[e["from"]].append(e["to"])
        bwd[e["to"]].append(e["from"])

    sorted_depths = sorted(layers.keys())

    def assign_positions(layers_dict: Dict[int, List[str]]) -> Dict[str, float]:
        """给每层节点分配 y 坐标，返回 var->y 映射。"""
        pos_y: Dict[str, float] = {}
        lmax = max(len(ly) for ly in layers_dict.values()) if layers_dict else 1
        ltotal = lmax * NODE_H + (lmax - 1) * V_GAP
        for d in sorted_depths:
            nodes = layers_dict[d]
            n = len(nodes)
            total_h = n * NODE_H + (n - 1) * V_GAP
            y_start = PADDING + (ltotal - total_h) / 2
            for i, v in enumerate(nodes):
                pos_y[v] = y_start + i * (NODE_H + V_GAP)
        return pos_y

    # 多轮 barycenter 交叉最小化（从上到下 + 从下到上）
    for _ in range(8):
        pos_y = assign_positions(layers)
        # 从上到下
        for d in sorted_depths[1:]:
            nodes = layers[d]
            def bary_upper(v: str) -> float:
                ups = bwd.get(v, [])
                ys = [pos_y[u] for u in ups if u in pos_y]
                return sum(ys) / len(ys) if ys else pos_y.get(v, 0)
            nodes.sort(key=bary_upper)
            layers[d] = nodes
        pos_y = assign_positions(layers)
        # 从下到上
        for d in reversed(sorted_depths[:-1]):
            nodes = layers[d]
            def bary_lower(v: str) -> float:
                downs = fwd.get(v, [])
                ys = [pos_y[u] for u in downs if u in pos_y]
                return sum(ys) / len(ys) if ys else pos_y.get(v, 0)
            nodes.sort(key=bary_lower)
            layers[d] = nodes

    # 最终位置
    pos_y = assign_positions(layers)
    positions: Dict[str, Dict[str, float]] = {}
    for d in sorted_depths:
        for v in layers[d]:
            x = PADDING + d * (NODE_W + H_GAP)
            positions[v] = {"x": x, "y": pos_y[v]}

    # 画布尺寸
    max_x = max((p["x"] for p in positions.values()), default=0) + NODE_W + PADDING
    max_y = max((p["y"] for p in positions.values()), default=0) + NODE_H + PADDING

    # ---- 构建 SVG 边（仅子图内） ----
    edge_svgs: List[str] = []
    for e in sub_edges:
        u, v = e["from"], e["to"]
        if u not in positions or v not in positions:
            continue
        pos_u = positions[u]
        pos_v = positions[v]
        path = _edge_path(pos_u["x"], pos_u["y"], pos_v["x"], pos_v["y"])
        label = consensus_clf.get(f"{u}->{v}", {}).get("label", "single_lingam")
        color = EDGE_COLORS.get(label, EDGE_COLORS["single_lingam"])
        dash = "stroke-dasharray=\"6,4\"" if label == "conflict" else ""
        sw = 1.0 + max(0.0, (e["f_e"] - 0.7)) / 0.3 * 2.0
        tooltip = html.escape(f"{u}->{v} | f_e={e['f_e']:.2f} dir_e={e['dir_e']:.2f} | {label}")
        edge_svgs.append(
            f'  <path d="{path}" fill="none" stroke="{color}" stroke-width="{sw:.1f}" {dash}>'
            f'<title>{tooltip}</title></path>'
        )

    # ---- 构建 SVG 节点（仅子图内） ----
    DEV_COLORS = {"normal": "#2ecc71", "moderate": "#f1c40f", "large": "#e67e22", "severe": "#e74c3c"}
    # 展示得分 = 总因果效应 total_effect（论文 Algorithm 2 T=(I-B)⁻¹）
    max_total_effect = max((c.get("total_effect", c.get("tracing_score", 0)) for c in root_candidates), default=1) or 1
    node_svgs: List[str] = []
    for v in sub_nodes:
        pos = positions[v]
        info = topology.get(v, {})
        entity = ENTITY_TABLE.get(v, {})
        meaning = entity.get("meaning", v)

        # 偏离度数据
        dev_info = (deviation_data or {}).get(v)
        dev_level = dev_info.get("level", "") if dev_info else ""
        dev_d = dev_info.get("d", 0) if dev_info else 0
        dev_color = DEV_COLORS.get(dev_level, "#95a5a6")

        # 填充色 = 偏离度色（有数据时），否则灰色
        fill = dev_color if dev_info else "#95a5a6"
        # 边框 = 排名标识（用粗细+深色描边区分，颜色全部留给偏离度）
        if v == top1_var:
            stroke = "#1a1a2e"
            stroke_w = "4.0"
        elif v in root_set:
            stroke = "#34495e"
            stroke_w = "2.5"
        else:
            stroke = "#7f8c8d"
            stroke_w = "1.5"

        rank_label = ""
        if v in root_set:
            rank = root_vars.index(v) + 1
            rank_label = f" #{rank}"

        dev_str = f"\n偏离度 d={dev_d:.2f} ({dev_level})" if dev_info else ""
        quantile_str = f"\n分位: {dev_info['quantile_at_max']} (值={dev_info['value_at_max']})" if dev_info else ""
        te = info.get("total_effect", info.get("tracing_score", 0))
        tt = html.escape(
            f"{v} ({meaning})\n"
            f"子系统: {entity.get('subsystem', '-')}\n"
            f"in_degree={info.get('in_degree', 0)} out_degree={info.get('out_degree', 0)}\n"
            f"reach_size={info.get('reach_size', 0)} "
            f"总因果效应={te:.4f}"
            f"{dev_str}{quantile_str}"
        )
        node_svgs.append(
            f'  <g class="node">'
            f'<rect x="{pos["x"]:.1f}" y="{pos["y"]:.1f}" '
            f'width="{NODE_W}" height="{NODE_H}" rx="6" ry="6" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_w}">'
            f'<title>{tt}</title></rect>'
            f'<text x="{pos["x"] + NODE_W / 2:.1f}" y="{pos["y"] + 18:.1f}" '
            f'text-anchor="middle" font-size="13" font-weight="bold" fill="#fff">{html.escape(v)}{html.escape(rank_label)}</text>'
            f'<text x="{pos["x"] + NODE_W / 2:.1f}" y="{pos["y"] + 33:.1f}" '
            f'text-anchor="middle" font-size="10" fill="#ecf0f1">{html.escape(meaning[:12])}</text>'
            f'</g>'
        )

    # ---- Top 5 排行榜（HTML 表格，放在 SVG 右侧） ----
    # 展示得分 = 总因果效应 total_effect（论文 Algorithm 2 T=(I-B)⁻¹）
    rank_rows = ""
    for c in root_candidates:
        v = c["variable"]
        entity = ENTITY_TABLE.get(v, {})
        meaning = entity.get("meaning", v)
        dev_info = (deviation_data or {}).get(v)
        dev_cell = (
            f'<span style="color:{dev_info["color"]};font-weight:bold">{dev_info["d"]:.1f}</span>'
            if dev_info else "-"
        )
        te = c.get("total_effect", c.get("tracing_score", 0))
        rank_rows += (
            f"<tr>"
            f"<td>{c['rank']}</td>"
            f"<td><b>{html.escape(v)}</b></td>"
            f"<td>{html.escape(meaning)}</td>"
            f"<td>{c['in_degree']}</td>"
            f"<td>{c['reach_size']}</td>"
            f"<td>{te:.4f}</td>"
            f"<td>{dev_cell}</td>"
            f"</tr>"
        )

    # ---- 组装 HTML ----
    svg_content = "\n".join(edge_svgs + node_svgs)
    n_sub = len(sub_nodes)
    n_sub_edges = len(sub_edges)

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>因果图 - {html.escape(template_name)}</title>
<style>
  body {{ margin: 0; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", "WenQuanYi Micro Hei", sans-serif; background: #f5f6fa; }}
  .header {{ padding: 12px 20px; background: #2c3e50; color: #fff; }}
  .header h1 {{ margin: 0; font-size: 18px; }}
  .header p {{ margin: 4px 0 0; font-size: 13px; color: #bdc3c7; }}
  .container {{ display: flex; padding: 16px; gap: 16px; align-items: flex-start; }}
  .graph-box {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px; }}
  svg {{ display: block; }}
  .rank-box {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; min-width: 360px; }}
  .rank-box h2 {{ margin: 0 0 12px; font-size: 15px; color: #2c3e50; border-bottom: 2px solid #e74c3c; padding-bottom: 6px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ padding: 6px 8px; text-align: left; border-bottom: 1px solid #ecf0f1; }}
  th {{ background: #f8f9fa; color: #7f8c8d; font-weight: 600; }}
  tr:nth-child(1) td {{ background: #fde8e8; font-weight: bold; }}
  tr:nth-child(1) td:first-child {{ color: #e74c3c; }}
  .legend {{ margin-top: 14px; font-size: 12px; color: #7f8c8d; line-height: 1.8; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .swatch {{ width: 14px; height: 14px; border-radius: 3px; display: inline-block; }}
  .node rect {{ cursor: pointer; transition: opacity 0.15s; }}
  .node:hover rect {{ opacity: 0.85; }}
  path {{ cursor: pointer; }}
  path:hover {{ stroke-width: 3 !important; }}
  .watermark {{ position: relative; }}
  .watermark::after {{
    content: "⚠ 信息不足（共识边<5），根因排名不可靠";
    position: absolute; top: 50%; left: 50%;
    transform: translate(-50%, -50%) rotate(-25deg);
    font-size: 28px; color: rgba(192, 57, 43, 0.25); font-weight: bold;
    pointer-events: none; white-space: nowrap;
  }}
</style>
</head>
<body>
<div class="header">
  <h1>因果图 — {html.escape(template_name)}</h1>
  <p>节点填充色=数据偏离度（d=|x-μ|/σ_robust） | 粗黑框=Top1根因 | 中框=Top2-5 | 根因按总因果效应 T=(I-B)⁻¹ 排序 | 仅显示 consensus 共识边传导子图({n_sub}节点/{n_sub_edges}边){' ⚠ 信息不足' if info_insufficient else ''}</p>
</div>
<div class="container">
  <div class="graph-box{' watermark' if info_insufficient else ''}">
<svg width="{max_x + 20}" height="{max_y + 20}" xmlns="http://www.w3.org/2000/svg">
{svg_content}
</svg>
  </div>
  <div class="rank-box">
    <h2>🏆 Top 5 根变量排行榜{' <span style="color:#c0392b;font-size:12px">（信息不足，不可靠）</span>' if info_insufficient else ''}</h2>
    <table>
      <tr><th>#</th><th>变量</th><th>含义</th><th>入度</th><th>传导广度</th><th>总因果效应</th><th>偏离度</th></tr>
      {rank_rows}
    </table>
    <div class="legend">
      <div style="margin-bottom:8px;font-weight:600;color:#34495e">根因排名（边框粗细）</div>
      <div class="legend-item"><span class="swatch" style="background:#95a5a6;border:3px solid #1a1a2e"></span> Top1 最可能根因（粗黑框）</div>
      <div class="legend-item"><span class="swatch" style="background:#95a5a6;border:2px solid #34495e"></span> Top 2-5 根变量（中框）</div>
      <div style="margin:8px 0;font-weight:600;color:#34495e">数据偏离度 d = |x−μ|/σ_robust（节点填充色）</div>
      <div class="legend-item"><span class="swatch" style="background:#2ecc71"></span> 正常 (d&lt;3)</div>
      <div class="legend-item"><span class="swatch" style="background:#f1c40f"></span> 中等 (3≤d&lt;5)</div>
      <div class="legend-item"><span class="swatch" style="background:#e67e22"></span> 较大 (5≤d&lt;7)</div>
      <div class="legend-item"><span class="swatch" style="background:#e74c3c"></span> 严重 (d≥7)</div>
      <hr style="margin:8px 0;border:none;border-top:1px solid #ecf0f1">
      <div class="legend-item"><span style="width:20px;height:2px;background:#2c3e50;display:inline-block"></span> consensus 共识边</div>
      <hr style="margin:8px 0;border:none;border-top:1px solid #ecf0f1">
      <div style="font-size:11px;color:#95a5a6;line-height:1.6;background:#fdf2e9;padding:8px;border-radius:4px">
        <b>⚠ 偏离度 ≠ 根因</b><br>
        1. d 大可能是饱和/触发信号，非因果起点；<br>
        2. 上游变量 d 不一定最大（潜伏期）；<br>
        3. 多根因时排序依赖总因果效应 T=(I-B)⁻¹，非 d；<br>
        4. Z 病态变量（σ/|μ|&lt;1%）和饱和变量不显示 d。
      </div>
    </div>
  </div>
</div>
</body>
</html>"""
    return html_doc

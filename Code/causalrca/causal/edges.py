"""图边与链文本纯函数（V0–V7 之外的图/文本工具）。

来源：2026-11-10 从 `rca/v21/chains.py`（v2.1 冻结实现）中抽取，仅搬迁
v2.3 依赖的部分：edge_key / stable_edge_index / validate_path_edges
（source_tracing 的忠实性校验）与 ROOT_CAUSE_NAME / render_chain_text 及其
私有渲染助手（llm_explanation 的链文本）。**算法口径逐字未改**。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

from causalrca.config.constants_v21 import STABLE_DIR
from causalrca.evidence.knowledge import ENTITY_TABLE

# 13.10 冻结值 60：RRF 倒数排名融合常数 k_RRF（方案 §5 R5 / §13.10）；
# 取值统一在 causalrca.config.constants_v23.RRF_K，本模块只做显式取用。
# 13.10 措辞纪律：事故工况名（marker 只是故障指示器，不是根因）。
ROOT_CAUSE_NAME = {
    "LOCA": "LOCA（一回路破口）工况",
    "SGATR": "SGATR（蒸汽发生器A传热管破裂）工况",
    "FLB": "FLB（给水/蒸汽管道破裂）工况",
}

# ==================== 纯函数：图 / BFS / 忠实性 ====================
def edge_key(src: str, dst: str) -> str:
    return f"{src}->{dst}"

def stable_edge_index(stable_edges: Sequence[Dict]) -> Dict[str, Dict]:
    """stable_edges（元素 dict from/to/f_e/dir_e/weight_median）-> {u->v: edge}。"""
    return {edge_key(e["from"], e["to"]): dict(e) for e in stable_edges}

def validate_path_edges(path: Sequence[str], edge_idx: Dict[str, Dict]
                        ) -> List[Dict]:
    """逐边独立忠实性校验（不依赖路径来源）：边必须 ∈ stable_edges。

    正常 BFS 路径恒 grounded；单测通过注入坏边验证 ungrounded 检测。
    """
    out: List[Dict] = []
    for u, v in zip(path[:-1], path[1:]):
        key = edge_key(u, v)
        e = edge_idx.get(key)
        out.append({
            "edge": key, "from": u, "to": v,
            "grounded": e is not None,
            "f_e": (None if e is None else float(e["f_e"])),
            "dir_e": (None if e is None else float(e["dir_e"])),
            "uncertain_direction": bool(e is not None and e["dir_e"] < STABLE_DIR),
        })
    return out

def _json_float(x) -> Optional[float]:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x

# ==================== 单轨迹传播链构建 ====================
def _entity(v: str) -> Dict[str, str]:
    return ENTITY_TABLE.get(v, {"subsystem": "未登记", "meaning": v, "unit": "-"})

# ==================== 模板化解释文本（四上下文槽位，不接 LLM）====================
def _arrow(z: float) -> str:
    if z > 0:
        return "↑"
    if z < 0:
        return "↓"
    return "≈0"

def _vname(v: str) -> str:
    e = _entity(v)
    return f"{v}（{e['meaning']}，{e['subsystem']}）"

def render_chain_text(rec: Dict) -> str:
    """四槽位冻结代码模板；措辞纪律见模块 docstring（RCA-019）。"""
    k, g = rec["template"]["k"], rec["template"]["g"]
    marker = rec["template"]["marker"]
    fl = rec["first_layer"]
    lines: List[str] = []

    # 槽位②：判定类与第一层三证据分（固定根因措辞）
    lines.append(f"【判定】根因：{ROOT_CAUSE_NAME[k]}；直接证据：故障指示器 {marker}"
                 f"（{_entity(marker)['meaning']}，故障指示器而非根因）激活；"
                 f"传播链自 {marker} 起。")
    e1_txt = "不适用（MASKED 条件 marker 已掩蔽）" if fl["E1"] is None \
        else f"{fl['E1']:.3f}"
    lines.append(
        f"第一层证据（判定模板 {k}_{g}）：E1={e1_txt}；"
        f"E2 原始分={fl['E2_raw']:.3f}（归一 {fl['E2_norm']:.3f}）；"
        f"E3_path 原始分={fl['E3_path_raw']:.3f}（归一 {fl['E3_path_norm']:.3f}）"
        + ("" if fl["E3_base_raw"] is None
           else f"；E3_base 原始分={fl['E3_base_raw']:.3f}")
        + f"；融合分={fl['fusion_score']:.3f}，margin={fl['margin']:.3f}"
        + f"（τ={fl['tau']}，δ={fl['delta']}，状态=接受）。")

    # 槽位①：观测异常（中文名 + 方向）
    if rec["A"]:
        a_txt = "、".join(f"{_vname(a['variable'])}{_arrow(a['observed_d'])}"
                         for a in rec["A"])
        lines.append(f"【观测异常】主窗内 {len(rec['A'])} 个变量越过过程本体 μ±3σ "
                     f"异常集 A(x)：{a_txt}。")
    else:
        lines.append("【观测异常】主窗内无变量进入异常集 A(x)。")

    # 槽位③：传播链（WLR 风格，按 RRF 序；只用稳定识别出的有向关系）
    nodes = rec["ranked_nodes"]
    if not nodes:
        lines.append(f"【传播链】A(x) 中无变量位于 {marker} 的稳定边可达域内，"
                     f"本次不出传播链（可达域外异常不硬排）。")
    else:
        lines.append("【传播链】以下为自 marker 起、按双引擎 RRF 排序的"
                     "稳定识别出的有向关系（非因果实证结论；RRF 为相对排序强度）：")
        for i, n in enumerate(nodes, 1):
            tags = _node_text_tags(n)
            tag_txt = f" {' '.join(tags)}" if tags else ""
            if all(a["grounded"] for a in n["path_edges"]):
                chain = " → ".join(n["path"])
                cl_txt = (f"|cL|={abs(n['cL']['cL']):.3g}/πL={n['cL']['piL']:.2f}"
                          if n["cL"] else "cL 缺失")
                ca_txt = (f"|cA|={abs(n['cA']['cA']):.3g}/πA={n['cA']['piA']:.2f}"
                          if n["cA"] else "cA 不可估")
                lines.append(
                    f"  {i}) {chain}｜RRF={n['rrf']:.4f}"
                    f"（rank_L={n['rank_L']}, rank_A={n['rank_A']}，{cl_txt}，{ca_txt}）"
                    f"{tag_txt}  节点本体：{_entity(n['node'])['subsystem']}")
            else:
                # 未落地边一律不进正文（边标识仅保留在 JSON ranked_nodes 留痕）
                lines.append(f"  {i}) 节点 {_vname(n['node'])} 的候选路径含未落地边，"
                             f"按忠实性校验不进入正文（明细见 JSON 留痕）。")

    # 槽位④：不确定性段
    lines.extend(_uncertainty_lines(rec))
    lines.append("【生成方式】本解释由 13.10 冻结代码模板生成（LLM 不可用时代码模板兜底，"
                 "llm_used=false，code_template_fallback=true），未调用任何 LLM 服务。")
    return "\n".join(lines)

def _node_text_tags(n: Dict) -> List[str]:
    tags = []
    if n["magnitude_unstable"]:
        tags.append("[量级不稳：πL<0.70或跨严重度翻转，rank_L已置末档]")
    if n["a_unestimable"]:
        tags.append("[cA不可估，rank_A末档]")
    edge_tags = []
    for a in n["path_edges"]:
        if a["uncertain_direction"]:
            edge_tags.append(f"[{a['edge']} 方向稳定率<0.90，方向不确定]")
        if a["consensus_conflict"]:
            edge_tags.append(f"[{a['edge']} 两引擎方向冲突]")
        if a["consensus_single"]:
            edge_tags.append(f"[{a['edge']} 仅 LiNGAM 单边识别]")
        if a["severity_flipped"]:
            edge_tags.append(f"[{a['edge']} 跨严重度符号翻转]")
        if a["competitive"]:
            edge_tags.append(f"[{a['edge']} 物理竞争边/反馈环，方向不预设]")
    return tags + edge_tags

def _uncertainty_lines(rec: Dict) -> List[str]:
    fl = rec["flags"]
    items: List[str] = []
    if fl["outside_reach_variables"]:
        v_txt = "、".join(_vname(v) for v in fl["outside_reach_variables"])
        items.append("异常但不在 marker 下游、未纳入传播链（outside_reach，不硬排）：" + v_txt)
    if fl["consensus_conflict_edges"]:
        items.append("两引擎共识冲突边：" + "、".join(fl["consensus_conflict_edges"]))
    if fl["consensus_single_edges"]:
        items.append("仅 LiNGAM 单边识别（single_lingam）："
                     + "、".join(fl["consensus_single_edges"]))
    if fl["uncertain_direction_edges"]:
        items.append("方向稳定率 <0.90 的边："
                     + "、".join(fl["uncertain_direction_edges"]))
    if fl["severity_flipped_edges"]:
        items.append("跨严重度符号翻转边：" + "、".join(fl["severity_flipped_edges"]))
    if fl["competitive_edges"]:
        items.append("物理竞争边/反馈环（双向保留、方向不预设）："
                     + "、".join(fl["competitive_edges"]))
    if fl["magnitude_unstable_nodes"]:
        items.append("低 π/翻转 marker 效应变量（量级不作排序依据）："
                     + "、".join(fl["magnitude_unstable_nodes"]))
    if fl["ungrounded_edges"]:
        # 未落地边标识不进正文（仅 JSON flags/ranked_nodes 留痕）
        items.append(f"存在 {len(fl['ungrounded_edges'])} 条未通过忠实性校验的边"
                     f"（已从正文剔除，明细见 JSON 留痕）")
    if fl["cf_fail_nodes"]:
        items.append("Alg3 反事实重演未通过（gCF=0.5）的变量："
                     + "、".join(fl["cf_fail_nodes"]))
    if rec.get("cf_template_gcf") == 0.5:
        items.append("该模板整体 Alg3 重演门控 template_gcf=0.5（模板级 cf_fail）。")
    if not items:
        items.append("未触发强制不确定性标注。")
    return ["【不确定性】" + items[0]] + [f"  - {x}" for x in items[1:]]

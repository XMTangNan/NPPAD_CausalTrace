"""13.10 Phase R5：两层输出与解释（神经环节③；修复 RCA-019/020）。

第一层：事故类型 + E1/E2/E3 三证据分 + 归一融合分 + margin + 弃权状态。
第二层（RCA-019 修复）：对接受轨迹，在判定类 k 的 D0 指定模板 g 上，**从 marker 出发**
沿 signature.stable_edges 做 BFS，对 A(x)∩reach 的候选变量做双引擎 RRF 排序拼链：
    rank_L：|cL[Y]| 降序（LiNGAM 总效应列）；
    rank_A：|cA[Y]| 降序（轨迹级 GAM marker 列）；
    RRF：score(Y) = 1/(RRF_K+rank_L) + 1/(RRF_K+rank_A)，rank 为 1 基名次。
爆炸量级闸门（RCA-015）：marker->Y 在 severity_flipped_marker_edges 名单或 πL<0.70 时，
rank_L 强制最末档并标 magnitude_unstable；cA not_estimable 的 Y rank_A 最末档。

忠实性（RCA-020 修复）：路径边独立校验必须全部 ∈ stable_edges，否则标 ungrounded
且该边不出现在 chain_text；BFS 正常情况下保证 grounded，校验独立存在（单测注入坏边）。
可达域外异常不硬排：A(x)\\reach 进 outside_reach 与不确定性段，不赋链上位置。

解释为冻结代码模板（llm_used=false, code_template_fallback=true），不调用任何 LLM 服务；
措辞纪律：禁止“根因是 WLR/WTRA/WFLB”，统一“根因：LOCA/SGATR/FLB 工况；
直接证据：marker 激活；传播链自 marker 起”；禁止“已证实因果”，用“稳定识别出的有向关系”。

冻结常量来源：RRF_K=60 为 13.10 冻结值（rca_config_v21.py 未登记，按实现冻结规格
在本模块顶部唯一定义，禁止散落）；π 闸门复用 STABLE_F=0.70；方向不稳定门槛 STABLE_DIR=0.90。
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, W_FULL, W_MASKED,
    STABLE_F, STABLE_DIR, FRONTIER_MAX_DEPTH,
)
from rca.v21.knowledge import ENTITY_TABLE, COMPETITIVE_PAIRS
from rca.v21.signatures import bfs_frontier
from rca.v21.marker_effects import effective_marker_map
from rca.v21.pipeline import apply_minmax
from core.utils import logger

# 13.10 冻结值 60：RRF 倒数排名融合常数 k_RRF（方案 §5 R5 / §13.10）。
RRF_K = 60
# 13.10 措辞纪律：事故工况名（marker 只是故障指示器，不是根因）。
ROOT_CAUSE_NAME = {
    "LOCA": "LOCA（一回路破口）工况",
    "SGATR": "SGATR（蒸汽发生器A传热管破裂）工况",
    "FLB": "FLB（给水/蒸汽管道破裂）工况",
}

# §4.2 冻结传播链登记的 marker→变量 期望符号（仅登记方向明确的 L1/L2 关系）。
# 来源：方案 §4.2 三类冻结关系表；~ / 0 / ? / L3 / 双向竞争（WLR↔P、WFWA↔LSGA）
# 一律不登记 -> 不进方向一致率分母（WLR↔P 按 13.10 明确不计为不一致）。
# sign = 链自 marker 起的总效应方向；chain 记录 §4.2 原表链与证据等级，便于审计。
FROZEN_MARKER_DIRECTION: Dict[str, Dict[str, Dict]] = {
    "LOCA": {
        "VOL":  {"sign": -1, "level": "L1", "chain": "WLR→VOL（泄漏→装量↓）"},
        "LVPZ": {"sign": -1, "level": "L1", "chain": "WLR→VOL→LVPZ（装量↓→液位↓）"},
        "TAVG": {"sign": -1, "level": "L2", "chain": "P→TAVG（失压→饱和温度↓）"},
        "PSGA": {"sign": -1, "level": "L2", "chain": "THA→PSGA（一回路→二回路热传导）"},
        "LSGA": {"sign": +1, "level": "L2", "chain": "安注致涨（§4.3 实测 13.7~14.9）"},
        "WHPI": {"sign": +1, "level": "L1", "chain": "P↓→WHPI↑（安注响应，sev25 已投入）"},
        "WECS": {"sign": +1, "level": "L1", "chain": "P↓→WHPI↑→WECS↑（安注响应）"},
    },
    "SGATR": {
        "LSGA": {"sign": +1, "level": "L1", "chain": "WTRA→LSGA（管侧漏入壳侧→水位↑）"},
        "PSGA": {"sign": +1, "level": "L2", "chain": "WTRA→PSGA（二次侧压力微升）"},
        "VOL":  {"sign": -1, "level": "L2", "chain": "WTRA→VOL（一回路缓失装量，弱）"},
        "P":    {"sign": -1, "level": "L2", "groups": ("mid", "high"),
                 "chain": "WTRA→VOL→P（缓失压，高严重度才现）"},
        "WHPI": {"sign": +1, "level": "L1", "groups": ("high",),
                 "chain": "P↓→WHPI（仅高严重度投入）"},
        "WECS": {"sign": +1, "level": "L1", "groups": ("high",),
                 "chain": "WHPI→WECS（安注汇入，仅高严重度）"},
    },
    "FLB": {
        "WFWA": {"sign": -1, "level": "L1", "groups": ("high",),
                 "chain": "WFLB→WFWA（破口分流→给水↓，sev25 无变化，高严重度）"},
        "WSTA": {"sign": -1, "level": "L2", "groups": ("high",),
                 "chain": "WFLB→WSTA（高严重度蒸汽量塌陷）"},
        "WSTB": {"sign": -1, "level": "L2", "groups": ("high",),
                 "chain": "WFLB→WSTB（高严重度蒸汽量塌陷）"},
        "P":    {"sign": -1, "level": "L2", "groups": ("high",),
                 "chain": "二回路→一回路 P（§4.3 主表为 ~，高严重度 −）"},
        "WHPI": {"sign": +1, "level": "L2", "groups": ("high",),
                 "chain": "P↓→WHPI（高严重度安注）"},
        "WECS": {"sign": +1, "level": "L1", "groups": ("high",),
                 "chain": "WHPI→WECS（高严重度安注汇入）"},
    },
}


# ==================== 纯函数：图 / BFS / 忠实性 ====================
def edge_key(src: str, dst: str) -> str:
    return f"{src}->{dst}"


def stable_edge_index(stable_edges: Sequence[Dict]) -> Dict[str, Dict]:
    """stable_edges（元素 dict from/to/f_e/dir_e/weight_median）-> {u->v: edge}。"""
    return {edge_key(e["from"], e["to"]): dict(e) for e in stable_edges}


def bfs_shortest_paths(stable_edges: Sequence[Dict], marker: str,
                       max_depth: int = FRONTIER_MAX_DEPTH
                       ) -> Dict[str, object]:
    """沿 stable_edges 从 marker 做确定性 BFS（邻接按变量名排序）。

    深度上限与 signatures.bfs_frontier 的 FRONTIER_MAX_DEPTH 一致（reach 同口径）；
    本函数额外保留父指针用于最短路径还原。
    """
    adj: Dict[str, List[str]] = {}
    for e in stable_edges:
        adj.setdefault(e["from"], []).append(e["to"])
    for u in adj:
        adj[u] = sorted(adj[u])
    parents = {marker: None}
    depth = {marker: 0}
    queue = [marker]
    head = 0
    while head < len(queue):
        u = queue[head]
        head += 1
        if depth[u] >= max_depth:
            continue
        for v in adj.get(u, []):
            if v not in parents:
                parents[v] = u
                depth[v] = depth[u] + 1
                queue.append(v)
    return {"adj": adj, "parents": parents, "depth": depth}


def shortest_path(bfs: Dict[str, object], target: str) -> Optional[List[str]]:
    """还原 marker→target 的 BFS 最短路径（含两端）；不可达返回 None。"""
    parents = bfs["parents"]
    if target not in parents:
        return None
    path = [target]
    while parents[path[-1]] is not None:
        path.append(parents[path[-1]])
    path.reverse()
    return path


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


def annotate_path_edges(path: Sequence[str],
                        edge_idx: Dict[str, Dict],
                        consensus_clf: Optional[Dict[str, Dict]] = None,
                        flipped_edges: Optional[Sequence[str]] = None
                        ) -> List[Dict]:
    """忠实性 + 方向稳定率 + E^cons 三分类 + 严重度翻转 + 竞争边 逐边标注。"""
    flipped = set(flipped_edges or [])
    comp = set()
    for a, b in COMPETITIVE_PAIRS:
        comp.add((a, b))
        comp.add((b, a))
    ann = validate_path_edges(path, edge_idx)
    for a in ann:
        key = a["edge"]
        clf = (consensus_clf or {}).get(key)
        label = clf["label"] if clf else None
        a["consensus"] = label                      # consensus/conflict/single_lingam/None
        a["consensus_conflict"] = label == "conflict"
        a["consensus_single"] = label == "single_lingam"
        a["severity_flipped"] = key in flipped
        a["competitive"] = (a["from"], a["to"]) in comp
    return ann


# ==================== 纯函数：双引擎排名 + RRF（爆炸闸门）====================
def _tiered_order(candidates: Sequence[str],
                  magnitude: Dict[str, float],
                  gated: Dict[str, bool]) -> List[str]:
    """正常档按 |效应| 降序、并列变量名升序；闸门档置最末并按变量名稳定排序。"""
    return sorted(candidates,
                  key=lambda y: (1 if gated.get(y) else 0,
                                 0.0 if gated.get(y) else -magnitude.get(y, 0.0),
                                 y))


def rank_candidates(candidates: Sequence[str],
                    cL: Dict[str, Dict], cA: Dict[str, Dict],
                    marker: str,
                    flipped_edges: Optional[Sequence[str]] = None
                    ) -> Dict[str, Dict]:
    """返回每候选 {rank_L, rank_A, magnitude_unstable, a_unestimable, |cL|,|cA|,piL,piA}。

    rank_L 爆炸闸门（RCA-015）：marker->Y 在 severity_flipped 名单 或 πL<STABLE_F(.70)
    或 cL 缺失 -> 最末档 + magnitude_unstable。
    rank_A 闸门：cA not_estimable / 缺失 -> 最末档。
    rank 为 1 基名次（RRF 标准约定）。
    """
    flipped = set(flipped_edges or [])
    mag_l, gate_l, pi_l = {}, {}, {}
    mag_a, gate_a, pi_a = {}, {}, {}
    for y in candidates:
        info_l = cL.get(y)
        mag_l[y] = abs(float(info_l["cL"])) if info_l else 0.0
        pi_l[y] = (float(info_l["piL"]) if info_l else 0.0)
        gate_l[y] = (info_l is None
                     or edge_key(marker, y) in flipped
                     or pi_l[y] < STABLE_F)
        info_a = cA.get(y)
        mag_a[y] = abs(float(info_a["cA"])) if info_a else 0.0
        pi_a[y] = (float(info_a.get("piA", 0.0)) if info_a else 0.0)
        gate_a[y] = not (info_a is not None and info_a.get("estimable"))

    order_l = _tiered_order(candidates, mag_l, gate_l)
    order_a = _tiered_order(candidates, mag_a, gate_a)
    rl = {y: i + 1 for i, y in enumerate(order_l)}
    ra = {y: i + 1 for i, y in enumerate(order_a)}
    return {y: {"rank_L": rl[y], "rank_A": ra[y],
                "magnitude_unstable": bool(gate_l[y]),
                "a_unestimable": bool(gate_a[y]),
                "abs_cL": mag_l[y], "abs_cA": mag_a[y],
                "piL": pi_l[y], "piA": pi_a[y]}
            for y in candidates}


def rrf_score(rank_l: int, rank_a: int, k: int = RRF_K) -> float:
    """13.10 RRF：1/(k+rank_L)+1/(k+rank_A)，rank 1 基。"""
    return 1.0 / (k + rank_l) + 1.0 / (k + rank_a)


# ==================== 模板选择（预测类 k 下融合代理最高的 g）====================
def select_template(res: Dict, k: str, condition: str,
                    calibration: Dict) -> Tuple[str, Dict[str, Dict]]:
    """D0 已冻结主证据=E3_path（d0_decision.json branch=none），故判定模板

    g* = argmax_g e3_path（并列先比 e2，再按 GROUP_ORDER low→high 确定性取）。
    注意：不得把类级 minmax 标定参数套到单模板原始分上（标定量定义在类级
    s(k)=max_g 上，模板级套归一在方法论上无定义）；low/mid 组 frontier 为空
    时 e3_path=nan→0 地板，因此 g* 常落 high——这是"仅高严重度拓扑可辨识"
    的真实结构，非选择偏差。
    """
    detail: Dict[str, Dict] = {}
    for g in GROUP_ORDER:
        raw = res["per_template"][f"{k}_{g}"]
        detail[g] = {"e2_raw": _json_float(raw["e2"]),
                     "e3_path_raw": _json_float(raw["e3_path"]),
                     "e3_base_raw": _json_float(raw["e3_base"])}
    g_star = max(GROUP_ORDER,
                 key=lambda g: (detail[g]["e3_path_raw"] or 0.0,
                                detail[g]["e2_raw"],
                                -GROUP_ORDER.index(g)))
    return g_star, detail


def _json_float(x) -> Optional[float]:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


# ==================== 单轨迹传播链构建 ====================
def _entity(v: str) -> Dict[str, str]:
    return ENTITY_TABLE.get(v, {"subsystem": "未登记", "meaning": v, "unit": "-"})


def build_chain(*, condition: str, true_k: str, severity: int, pred: str,
                accepted: bool, res: Dict, d_map: Dict[str, float],
                sig: Dict, g: str,
                cf_obj: Optional[Dict] = None,
                consensus_obj: Optional[Dict] = None,
                calibration: Optional[Dict] = None) -> Dict:
    """组装一条 13.10 两层记录（不含文本，文本由 render_chain_text 生成）。"""
    marker = sig["marker"]
    A = list(res["A"])
    stable = sig["stable_edges"]
    edge_idx = stable_edge_index(stable)
    # 复用 signatures.bfs_frontier 的 layers/reach；路径由独立 BFS 父指针还原。
    front = bfs_frontier([(e["from"], e["to"]) for e in stable], marker)
    reach = set(front["reach"])
    bfs = bfs_shortest_paths(stable, marker)
    assert set(bfs["parents"]) - {marker} == reach, "BFS reach 与 bfs_frontier 不一致"

    flipped = sig.get("flags", {}).get("severity_flipped_marker_edges", [])
    consensus_clf = (consensus_obj or {}).get("classification", {})
    cL, cA = sig["cL"], sig.get("cA", {})
    candidates = [y for y in A if y in reach]
    ranks = rank_candidates(candidates, cL, cA, marker, flipped)
    eff = effective_marker_map(cA, cL)

    cf_edges = (cf_obj or {}).get("edges", {})
    ranked_nodes: List[Dict] = []
    for y in candidates:
        rinfo = ranks[y]
        rrf = rrf_score(rinfo["rank_L"], rinfo["rank_A"])
        path = shortest_path(bfs, y) or []
        ann = annotate_path_edges(path, edge_idx, consensus_clf, flipped)
        grounded = all(a["grounded"] for a in ann)
        cf_e = cf_edges.get(y)
        node = {
            "node": y, "entity": _entity(y),
            "rank_L": rinfo["rank_L"], "rank_A": rinfo["rank_A"],
            "rrf": float(rrf),
            "magnitude_unstable": rinfo["magnitude_unstable"],
            "a_unestimable": rinfo["a_unestimable"],
            "observed_d": float(d_map.get(y, 0.0)),
            "cL": (None if y not in cL else {"cL": float(cL[y]["cL"]),
                                             "piL": float(cL[y]["piL"])}),
            "cA": (None if y not in cA else {"cA": float(cA[y]["cA"]),
                                             "piA": float(cA[y].get("piA", 0.0)),
                                             "estimable": bool(cA[y].get("estimable"))}),
            "effective": (None if y not in eff
                          else {"c": float(eff[y]["c"]), "source": eff[y]["source"]}),
            "depth": int(bfs["depth"].get(y, -1)),
            "path": path, "path_edges": ann, "grounded": grounded,
            "cf_support": (None if cf_e is None
                           else {"agree": _json_float(cf_e.get("agree")),
                                 "gCF": _json_float(cf_e.get("gCF")),
                                 "agree_A": _json_float(cf_e.get("agree_A")),
                                 "agree_L": _json_float(cf_e.get("agree_L")),
                                 "flags": list(cf_e.get("flags", []))}),
        }
        ranked_nodes.append(node)
    ranked_nodes.sort(key=lambda n: (-n["rrf"], n["node"]))

    outside = [{"variable": y, "entity": _entity(y),
                "observed_d": float(d_map.get(y, 0.0))}
               for y in A if y not in reach]
    outside.sort(key=lambda o: o["variable"])

    flags = _aggregate_flags(ranked_nodes, outside, marker)
    if flags["ungrounded_edges"]:
        # BFS 正常不触发；触发即说明路径来源被污染，正文已剔除、此处留运行日志。
        logger.warning(f"[R5] {condition}/{true_k}/{sev} 检出未落地边: "
                       f"{flags['ungrounded_edges']}")
    first_layer = _first_layer(res, pred, calibration)
    rec = {
        "schema": "NPPAD-CausalTrace-v2.1-R5-13.10",
        "condition": condition, "true": true_k, "severity": severity,
        "pred": pred, "accepted": bool(accepted),
        "template": {"k": pred, "g": g,
                     "marker": marker,
                     "selection": "预测类 k 下逐模板归一融合代理 argmax_g"
                                  "（FULL/MASKED 各自分数；E1 类级常量）",
                     "template_scores": select_template(res, pred, condition,
                                                        calibration)[1]
                     if calibration is not None else None},
        "A": [{"variable": y, "entity": _entity(y),
               "observed_d": float(d_map.get(y, 0.0))} for y in A],
        "outside_reach": outside,
        "ranked_nodes": ranked_nodes,
        "first_layer": first_layer,
        "cf_template_gcf": _json_float((cf_obj or {}).get("template_gcf")),
        "flags": flags,
        "llm_used": False,
        "code_template_fallback": True,
    }
    rec["chain_text"] = render_chain_text(rec)
    return rec


def _first_layer(res: Dict, k: str, calibration: Optional[Dict]) -> Dict:
    raw = res["class_raw"][k]
    norm = res["class_norm"]
    return {
        "E1": (None if res.get("e1") is None else float(res["e1"][k])),
        "E2_raw": float(raw["e2"]),
        "E3_base_raw": _json_float(raw["e3_base"]),
        "E3_path_raw": float(raw["e3_path"]),
        "E2_norm": float(norm["e2"][k]),
        "E3_path_norm": float(norm["e3_path"][k]),
        "fusion_score": float(res["fusion"][k]),
        "s1": res["s1"], "s2": res["s2"], "margin": res["margin"],
        "tau": (None if calibration is None else float(calibration["tau"])),
        "delta": (None if calibration is None else float(calibration["delta"])),
        "abstain_reason": res["abstain_reason"],
    }


def _aggregate_flags(ranked_nodes: Sequence[Dict], outside: Sequence[Dict],
                     marker: str) -> Dict:
    def edges_with(attr: str) -> List[str]:
        out = []
        for n in ranked_nodes:
            for a in n["path_edges"]:
                if a.get(attr):
                    out.append(a["edge"])
        return sorted(set(out))

    return {
        "n_ranked": len(ranked_nodes),
        "ungrounded_edges": sorted({a["edge"] for n in ranked_nodes
                                    for a in n["path_edges"] if not a["grounded"]}),
        "uncertain_direction_edges": edges_with("uncertain_direction"),
        "consensus_conflict_edges": edges_with("consensus_conflict"),
        "consensus_single_edges": edges_with("consensus_single"),
        "severity_flipped_edges": edges_with("severity_flipped"),
        "competitive_edges": edges_with("competitive"),
        "magnitude_unstable_nodes": sorted(n["node"] for n in ranked_nodes
                                           if n["magnitude_unstable"]),
        "cf_fail_nodes": sorted(n["node"] for n in ranked_nodes
                                if (n["cf_support"] or {}).get("gCF") == 0.5),
        "outside_reach_variables": [o["variable"] for o in outside],
    }


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


# ==================== 第二层评估：vs §4.2 冻结关系方向一致率 ====================
def chain_effective_sign(node: Dict) -> Optional[int]:
    """链上节点用于方向核对的 marker 效应符号：cA 优先、cL 兜底（effective_marker_map）。"""
    eff = node.get("effective")
    if not eff:
        return None
    c = eff["c"]
    if abs(c) < 1e-12:
        return None
    return 1 if c > 0 else -1


def direction_consistency(chains: Sequence[Dict]
                          ) -> Dict[str, Dict]:
    """逐类统计链上 marker→§4.2 冻结核心关系变量的方向一致率。

    分母 = 链类 k（=判定类）下 ranked_nodes 中在 FROZEN_MARKER_DIRECTION[k] 登记、
    且链上效应符号可判的 (链, 变量) 对；WLR↔P / WFWA↔LSGA 等竞争/不预设关系
    本就不在冻结表内（不计为不一致）。
    """
    stats = {k: {"n_consistent": 0, "n_inconsistent": 0,
                 "n_noncomparable": 0, "n": 0, "rate": None,
                 "by_condition": {}, "inconsistent_cases": []}
             for k in ACCIDENT_TYPES}
    for rec in chains:
        k = rec["pred"]
        cond = rec["condition"]
        bc = stats[k]["by_condition"].setdefault(
            cond, {"n_consistent": 0, "n_inconsistent": 0,
                   "n_noncomparable": 0, "n": 0})
        frozen = FROZEN_MARKER_DIRECTION.get(k, {})
        rec_g = (rec.get("template") or {}).get("g")
        seen = set()
        for node in rec["ranked_nodes"]:
            y = node["node"]
            if y not in frozen or y in seen:
                continue
            seen.add(y)
            allowed_groups = frozen[y].get("groups")
            if allowed_groups is not None and rec_g not in allowed_groups:
                # 冻结方向仅在特定严重度组可判（如 FLB 高严重度），低组不计入
                stats[k]["n_noncomparable"] += 1
                bc["n_noncomparable"] += 1
                continue
            sign = chain_effective_sign(node)
            if sign is None:
                stats[k]["n_noncomparable"] += 1
                bc["n_noncomparable"] += 1
                continue
            ok = sign == frozen[y]["sign"]
            stats[k]["n"] += 1
            bc["n"] += 1
            if ok:
                stats[k]["n_consistent"] += 1
                bc["n_consistent"] += 1
            else:
                stats[k]["n_inconsistent"] += 1
                bc["n_inconsistent"] += 1
                stats[k]["inconsistent_cases"].append(
                    {"condition": cond, "true": rec["true"],
                     "severity": rec["severity"], "variable": y,
                     "chain_sign": sign, "frozen_sign": frozen[y]["sign"],
                     "effective_source": (node.get("effective") or {}).get("source"),
                     "frozen_chain": frozen[y]["chain"]})
    for k in ACCIDENT_TYPES:
        s = stats[k]
        s["rate"] = (s["n_consistent"] / s["n"] if s["n"] else None)
        for bc in s["by_condition"].values():
            bc["rate"] = (bc["n_consistent"] / bc["n"] if bc["n"] else None)
    return stats


# ==================== 汇总 ====================
def summarize_chains(chains: Sequence[Dict],
                     accepted_counts: Dict[str, int],
                     elapsed_seconds: Optional[float] = None) -> Dict:
    """chain_summary.json：计数 + 标注统计 + outside_reach 频次 + 第二层方向一致率。"""
    emitted = Counter(c["condition"] for c in chains)
    def count_edges(attr: str) -> int:
        return sum(len(c["flags"][attr]) for c in chains)
    outside_freq = Counter(v for c in chains
                           for v in c["flags"]["outside_reach_variables"])
    dir_stats = direction_consistency(chains)
    summary = {
        "schema": "NPPAD-CausalTrace-v2.1-R5-13.10-summary",
        "accepted_trajectories": {c: int(accepted_counts.get(c, 0))
                                  for c in ("FULL", "MASKED")},
        "chains_emitted": {c: int(emitted.get(c, 0))
                           for c in ("FULL", "MASKED")},
        "ungrounded_edges_total": int(sum(
            len(c["flags"]["ungrounded_edges"]) for c in chains)),
        "annotation_counts": {
            "uncertain_direction_edges": count_edges("uncertain_direction_edges"),
            "consensus_conflict_edges": count_edges("consensus_conflict_edges"),
            "consensus_single_edges": count_edges("consensus_single_edges"),
            "severity_flipped_edges": count_edges("severity_flipped_edges"),
            "competitive_edges": count_edges("competitive_edges"),
            "magnitude_unstable_nodes": sum(
                len(c["flags"]["magnitude_unstable_nodes"]) for c in chains),
            "cf_fail_nodes": sum(len(c["flags"]["cf_fail_nodes"]) for c in chains),
        },
        "outside_reach_frequency": dict(sorted(outside_freq.items())),
        "frozen_relation_direction": dir_stats,
    }
    for k in ACCIDENT_TYPES:
        summary[f"frozen_relation_direction_rate_{k}"] = dir_stats[k]["rate"]
        summary[f"n_{k}"] = dir_stats[k]["n"]
    if elapsed_seconds is not None:
        summary["elapsed_seconds"] = float(elapsed_seconds)
    return summary

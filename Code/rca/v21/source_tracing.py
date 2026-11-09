"""v2.2 变量溯源算法：基于 v2.1 因果图的拓扑反溯 + 总因果效应排序。

核心约束（与论文 Algorithm 2 对齐）：
- 排序主依据 = 总因果效应矩阵 T = (I−B)⁻¹ 中，候选变量对效应变量的总影响
- 拓扑指标（in_degree/reach_size/consensus_rate）作为次级排序键和兜底
- marker 遮蔽：不参与根变量排名
- 不用幅值 |cL|/|cA| 作为排序依据
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from rca.v21.chains import edge_key, validate_path_edges
from rca.v21.knowledge import ENTITY_TABLE


# ==================== 配置（方案 §1.3 决策） ====================
TOP_K_ROOT = 5            # 根变量默认输出 Top 5
B_CLIP = 5.0              # B 矩阵元素截断阈值：|B|>B_CLIP 视为共线性伪影
MAX_PATH_LEN = 4          # 传导路径最大边数
MAX_PATH_DEPTH = 5        # 传导路径最大节点数（= MAX_PATH_LEN + 1）
MAX_PATHS_PER_ROOT = 20   # 每个根变量输出的路径数上限（防组合爆炸）


# ==================== 数据加载 ====================
def load_signature(sig_path: Path) -> Dict:
    with open(sig_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_consensus(cons_path: Path) -> Dict[str, Dict]:
    with open(cons_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("classification", {})


# ==================== 图构建 ====================
def build_graph(
    stable_edges: Sequence[Dict],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]], Dict[str, Dict]]:
    """从 stable_edges 构建邻接表、反向邻接表、边索引。

    返回 (adj, radj, edge_idx)：
      adj[u] = [v, ...]   正向出边
      radj[v] = [u, ...]  反向入边
      edge_idx["u->v"] = edge_dict
    """
    adj: Dict[str, List[str]] = defaultdict(list)
    radj: Dict[str, List[str]] = defaultdict(list)
    edge_idx: Dict[str, Dict] = {}
    for e in stable_edges:
        u, v = e["from"], e["to"]
        adj[u].append(v)
        radj[v].append(u)
        edge_idx[edge_key(u, v)] = e
    return dict(adj), dict(radj), edge_idx


# ==================== 拓扑指标计算 ====================
def compute_topology(
    stable_edges: Sequence[Dict],
    variables: Sequence[str],
    marker: str,
    consensus_clf: Dict[str, Dict],
) -> Dict[str, Dict]:
    """计算每个节点的拓扑指标（方案 §3.2）。

    指标：in_degree, out_degree, reach_size, weighted_reach, consensus_rate, depth
    """
    adj, radj, _ = build_graph(stable_edges)
    all_vars = list(variables)

    in_deg = {v: len(radj.get(v, [])) for v in all_vars}
    out_deg = {v: len(adj.get(v, [])) for v in all_vars}

    # depth：多源 BFS，从所有 in_degree=0 的节点（含 marker）同时出发
    # 确保所有节点都有合理分层，避免不可达节点 depth=-1 导致可视化溢出
    depth: Dict[str, int] = {v: -1 for v in all_vars}
    roots = [v for v in all_vars if in_deg.get(v, 0) == 0]
    if roots:
        q = deque()
        for r in roots:
            depth[r] = 0
            q.append(r)
        while q:
            u = q.popleft()
            for v in adj.get(u, []):
                if depth[v] == -1:
                    depth[v] = depth[u] + 1
                    q.append(v)
    # 兜底：仍有 -1 的节点（孤立环），放到最大层+1
    max_d = max((d for d in depth.values() if d >= 0), default=0)
    for v in all_vars:
        if depth[v] == -1:
            depth[v] = max_d + 1

    # reach / reach_size / weighted_reach：从每个节点出发的 BFS
    reach_size: Dict[str, int] = {}
    weighted_reach: Dict[str, float] = {}
    for src in all_vars:
        visited: Set[str] = set()
        dist: Dict[str, int] = {src: 0}
        q = deque([src])
        while q:
            u = q.popleft()
            for v in adj.get(u, []):
                if v not in visited and v != src:
                    visited.add(v)
                    dist[v] = dist[u] + 1
                    q.append(v)
        reach_size[src] = len(visited)
        weighted_reach[src] = sum(1.0 / dist[v] for v in visited) if visited else 0.0

    # consensus_rate：出边中 consensus 标签占比
    consensus_rate: Dict[str, float] = {}
    for v in all_vars:
        out_edges = adj.get(v, [])
        if not out_edges:
            consensus_rate[v] = 0.0
            continue
        n_cons = sum(
            1 for u in out_edges
            if consensus_clf.get(edge_key(v, u), {}).get("label") == "consensus"
        )
        consensus_rate[v] = n_cons / len(out_edges)

    result: Dict[str, Dict] = {}
    for v in all_vars:
        result[v] = {
            "in_degree": in_deg[v],
            "out_degree": out_deg[v],
            "reach_size": reach_size[v],
            "weighted_reach": round(weighted_reach[v], 4),
            "consensus_rate": round(consensus_rate[v], 4),
            "depth": depth[v],
        }
    return result


# ==================== 总因果效应矩阵（论文 Algorithm 2）====================
def build_total_effect_matrix(
    stable_edges: Sequence[Dict],
    variables: Sequence[str],
) -> Tuple[np.ndarray, Dict[str, int]]:
    """从稳定边重建直接效应矩阵 B，计算总因果效应 T = (I−B)⁻¹。

    T[i, j] = 变量 j 对变量 i 的总因果效应（直接 + 间接）。
    用 weight_median（bootstrap 中位数）作为 B[i,j] 的估计。

    数值稳定性修正（ALGO, 2026-11-09）：
      LiNGAM 在多重共线变量对上回归系数可跨 6 数量级（如 PSGA/PSGB→WSTB
      weight=±2.7e5），直接求逆导致 T 条件数 ~1e11、得分无物理意义。
      修正：对 B 元素做硬截断 |B|≤B_CLIP(=5)。标准化数据下因果效应应在
      [-1,1] 量级，|B|>5 必为共线性伪影。截断保留 435/505 边不变，
      仅压制 70 个极值，(I-B) 条件数从 ~1e11 降至 ~3-4，总效应排序有意义。
    """
    idx = {v: i for i, v in enumerate(variables)}
    n = len(variables)
    B = np.zeros((n, n), dtype=float)
    for e in stable_edges:
        i, j = idx[e["to"]], idx[e["from"]]   # B[to, from] = from -> to 的直接效应
        B[i, j] = e.get("weight_median", 0.0)
    # 数值稳定：截断共线性伪影。标准化数据下因果效应应在 [-1,1] 量级，
    # |B|>5 必为多重共线导致的回归爆炸（如 PSGA/PSGB→WSTB = ±2.7e5）。
    # 硬截断保留绝大多数合理边不变，仅压制极值，保证 (I-B) 良态。
    B = np.clip(B, -B_CLIP, B_CLIP)
    # DAG 保证 (I-B) 可逆
    try:
        T = np.linalg.inv(np.eye(n) - B)
    except np.linalg.LinAlgError:
        T = np.eye(n)  # 兜底：奇异时退化为单位阵（无间接效应）
    return T, idx


# ==================== 根变量筛选 ====================
def classify_role(in_deg: int, out_deg: int) -> str:
    """拓扑角色标注（方案 §3.4）。"""
    if out_deg == 0:
        return "terminal"               # 末端症状（不影响任何其他变量）
    if in_deg <= 1:
        if out_deg >= 3:
            return "upstream_driver"    # 上游驱动节点
        return "physical_root"          # 物理根因层
    if in_deg >= 2 and out_deg >= 2:
        return "conduction_hub"         # 传导枢纽
    return "intermediate"


def select_root_candidates(
    topology: Dict[str, Dict],
    marker: str,
    stable_edges: Sequence[Dict],
    variables: Sequence[str],
    top_k: int = TOP_K_ROOT,
) -> List[Dict]:
    """筛选根变量：论文 Algorithm 2 — 基于总因果效应 T=(I−B)⁻¹ 排序。

    主排序键：total_effect_score（候选变量对所有效应变量的总因果效应绝对值之和）
    效应变量 = terminal 节点（out_degree=0）+ marker（症状/异常端）
    次级排序键：in_degree ASC, weighted_reach DESC, reach_size DESC, consensus_rate DESC
    marker 遮蔽：不参与根变量排名。
    """
    # 1. 计算总因果效应矩阵 T
    T, idx = build_total_effect_matrix(stable_edges, variables)

    # 2. 确定效应变量（症状端）：terminal 节点 + marker
    effect_vars: Set[str] = set()
    for v, info in topology.items():
        if info["out_degree"] == 0:
            effect_vars.add(v)
    effect_vars.add(marker)
    effect_idx = [idx[v] for v in effect_vars if v in idx]

    candidates: List[Dict] = []
    for v, info in topology.items():
        if v == marker:
            continue
        # 总因果效应得分 = 该变量对所有效应变量的 |T| 之和
        j = idx[v]
        total_effect = float(np.sum(np.abs(T[effect_idx, j]))) if effect_idx else 0.0
        tracing_score = round(total_effect, 6)
        ent = ENTITY_TABLE.get(v, {})
        candidates.append({
            "variable": v,
            "subsystem": ent.get("subsystem", "未登记"),
            "meaning": ent.get("meaning", ""),
            "in_degree": info["in_degree"],
            "out_degree": info["out_degree"],
            "reach_size": info["reach_size"],
            "weighted_reach": info["weighted_reach"],
            "consensus_rate": info["consensus_rate"],
            "total_effect": tracing_score,
            "tracing_score": tracing_score,
            "role": classify_role(info["in_degree"], info["out_degree"]),
            "is_marker": False,
        })

    # 主排序：总因果效应 DESC；次级：拓扑指标
    candidates.sort(key=lambda c: (
        -c["total_effect"],
        c["in_degree"],
        -c["weighted_reach"],
        -c["reach_size"],
        -c["consensus_rate"],
    ))

    # 优先选有传导能力的节点（out_degree > 0），不足再补孤立节点
    conductive = [c for c in candidates if c["out_degree"] > 0]
    isolated = [c for c in candidates if c["out_degree"] == 0]
    ordered = conductive + isolated
    selected = ordered[:top_k]

    for i, c in enumerate(selected, 1):
        c["rank"] = i
    return selected


# ==================== 传导路径生成 ====================
def _dfs_paths(
    adj: Dict[str, List[str]],
    start: str,
    max_len: int,
) -> List[List[str]]:
    """从 start 出发 DFS 生成所有简单路径，边数 ≤ max_len。"""
    paths: List[List[str]] = []

    def dfs(node: str, path: List[str]) -> None:
        if len(path) >= 2:
            paths.append(list(path))
        if len(path) - 1 >= max_len:
            return
        for nb in adj.get(node, []):
            if nb not in path:
                path.append(nb)
                dfs(nb, path)
                path.pop()

    dfs(start, [start])
    return paths


def generate_conduction_paths(
    stable_edges: Sequence[Dict],
    root_vars: Sequence[str],
    consensus_clf: Dict[str, Dict],
    max_len: int = MAX_PATH_LEN,
    max_per_root: int = MAX_PATHS_PER_ROOT,
) -> Tuple[List[Dict], Dict]:
    """从每个根变量出发生成传导路径并做忠实性校验（方案 §5）。

    返回 (paths, faithfulness)：
      paths: 排序后的路径列表
      faithfulness: {"total", "grounded", "ungrounded_count"}
    """
    adj, _, edge_idx = build_graph(stable_edges)

    all_paths: List[Dict] = []
    n_total = 0
    n_ungrounded = 0

    for root in root_vars:
        raw_paths = _dfs_paths(adj, root, max_len)
        for path in raw_paths:
            n_total += 1
            edge_results = validate_path_edges(path, edge_idx)
            grounded = all(er["grounded"] for er in edge_results)
            if not grounded:
                n_ungrounded += 1
                continue  # 不通过忠实性校验的路径剔除

            # 边标注：补充 consensus 标签
            annotated_edges = []
            consensus_count = 0
            for er in edge_results:
                clf = consensus_clf.get(er["edge"], {})
                label = clf.get("label", "single_lingam")
                if label == "consensus":
                    consensus_count += 1
                annotated_edges.append({
                    "edge": er["edge"],
                    "from": er["from"],
                    "to": er["to"],
                    "f_e": er["f_e"],
                    "dir_e": er["dir_e"],
                    "consensus": label,
                    "grounded": er["grounded"],
                })

            all_paths.append({
                "root": root,
                "path": path,
                "length": len(path) - 1,
                "edges": annotated_edges,
                "consensus_count": consensus_count,
                "grounded": grounded,
            })

    # 排序：consensus_count DESC, length DESC, root ASC, path 字典序
    all_paths.sort(key=lambda r: (
        -r["consensus_count"],
        -r["length"],
        r["root"],
        r["path"],
    ))

    # 每个根变量限制输出数量
    limited: List[Dict] = []
    count_per_root: Dict[str, int] = defaultdict(int)
    for p in all_paths:
        if count_per_root[p["root"]] < max_per_root:
            limited.append(p)
            count_per_root[p["root"]] += 1

    faithfulness = {
        "total_paths": n_total,
        "grounded_paths": n_total - n_ungrounded,
        "ungrounded_count": n_ungrounded,
    }
    return limited, faithfulness


# ==================== 实体信息补全 ====================
def enrich_entity(variable: str) -> Dict:
    """从 ENTITY_TABLE 获取变量的子系统/含义/单位。"""
    info = ENTITY_TABLE.get(variable, {})
    return {
        "meaning": info.get("meaning", variable),
        "subsystem": info.get("subsystem", "未知"),
        "unit": info.get("unit", "-"),
    }

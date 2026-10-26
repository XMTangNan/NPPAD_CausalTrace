"""R1 分组正演签名：轨迹级 Bootstrap（Alg1）+ 独立求逆总效应（Alg2）+ 传播前沿。

对应方案 13.5。索引约定（与方案一核对一致）：
  B[i, j] = v_j -> v_i 的直接效应；T = (I-B)^{-1}，T[i, j] = v_j -> v_i 总效应。
Bootstrap 单位 = 轨迹（重采样轨迹编号后 vstack 主窗全部点），严禁时间点重采样。
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
from typing import Dict, List, Optional, Sequence, Tuple

from rca.rca_config_v21 import (
    LINGAM_N_BOOTSTRAP, STABLE_F, STABLE_DIR, FRONTIER_MAX_DEPTH,
    SIGNATURE_DIR, GLOBAL_SEED,
)
from rca.v21.knowledge import apply_ontology_constraint
from core.utils import logger


# ==================== 纯函数（G3 单测对象）====================
def total_effect(B: np.ndarray) -> np.ndarray:
    """Alg2：每次学图独立求 T=(I-B)^{-1}，禁止用聚合 B 单次求逆。"""
    return np.linalg.inv(np.eye(B.shape[0]) - B)


def bfs_frontier(edges: Sequence[Tuple[str, str]], marker: str,
                 max_depth: int = FRONTIER_MAX_DEPTH) -> Dict[str, object]:
    """从 marker 沿稳定有向边 BFS，返回分层 S1..S3、可达域 R、深度表。"""
    adj: Dict[str, List[str]] = {}
    for src, dst in edges:
        adj.setdefault(src, []).append(dst)
    layers: Dict[int, List[str]] = {}
    seen = {marker}
    frontier = {marker}
    for d in range(1, max_depth + 1):
        nxt = set()
        for node in frontier:
            for child in adj.get(node, []):
                if child not in seen:
                    nxt.add(child)
        layers[d] = sorted(nxt)
        seen |= nxt
        frontier = nxt
    reach = sorted(v for d in range(1, max_depth + 1) for v in layers[d])
    depth_of = {v: d for d, vs in layers.items() for v in vs}
    return {"layers": {d: layers[d] for d in range(1, max_depth + 1)},
            "reach": reach, "depth_of": depth_of}


def aggregate_edge_stats(B_list: List[np.ndarray], variables: List[str],
                         marker: str) -> Dict:
    """Alg1 四指标 + Alg2 marker 总效应列（pi=同号率，零值不计支持的保守口径）。"""
    B_arr = np.stack(B_list)
    n = len(variables)
    R = len(B_list)
    T_arr = np.stack([total_effect(B) for B in B_list])
    present = np.abs(B_arr) > 1e-10
    f_e = present.mean(axis=0)
    count = present.sum(axis=0)
    rev_count = count.T
    pair_total = count + rev_count
    dir_e = np.where(pair_total > 0, count / np.maximum(pair_total, 1), 0.0)

    idx = {v: i for i, v in enumerate(variables)}
    m = idx[marker]
    strength_med = np.median(B_arr, axis=0)
    strength_q1 = np.quantile(B_arr, 0.25, axis=0)
    strength_q3 = np.quantile(B_arr, 0.75, axis=0)

    t_col = T_arr[:, :, m]                       # (R, j) marker->j 总效应
    n_pos = (t_col > 1e-10).sum(axis=0)
    n_neg = (t_col < -1e-10).sum(axis=0)
    pi = np.maximum(n_pos, n_neg) / R
    cL = np.median(t_col, axis=0)

    edges = []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            edges.append({
                "from": variables[j], "to": variables[i],
                "f_e": float(f_e[i, j]), "dir_e": float(dir_e[i, j]),
                "weight_median": float(strength_med[i, j]),
                "weight_iqr": float(strength_q3[i, j] - strength_q1[i, j]),
                "count": int(count[i, j]),
            })
    cL_out = {variables[j]: {"cL": float(cL[j]), "piL": float(pi[j])}
              for j in range(n) if variables[j] != marker}
    return {"edges": edges, "cL": cL_out,
            "_pi_matrix": pi, "_cL_matrix": cL, "_idx": idx}


def select_stable_edges(edge_stats: Dict, f_thr: float = STABLE_F,
                        dir_thr: float = STABLE_DIR) -> List[Dict]:
    """稳定边：f_e>=0.70 且 dir_e>=0.90（13.5.3）。"""
    return [e for e in edge_stats["edges"]
            if e["f_e"] >= f_thr and e["dir_e"] >= dir_thr]


# ==================== 数据变换 ====================
def transform_graph_block(block: np.ndarray, variables: List[str], marker: str,
                          mu: Dict[str, float], sigma: Dict[str, float],
                          median_platform: float,
                          graph_only_scale: Dict[str, Tuple[float, float]]) -> np.ndarray:
    """原始块 -> 学图矩阵：V_thr 用共享 mu/sigma；marker 用平台尺度；图专用变量模板内尺度。"""
    Z = np.empty_like(block, dtype=float)
    for p, v in enumerate(variables):
        x = block[:, p]
        if v == marker:
            Z[:, p] = x / median_platform
        elif v in mu:
            Z[:, p] = (x - mu[v]) / sigma[v]
        else:
            m_v, s_v = graph_only_scale[v]
            Z[:, p] = (x - m_v) / s_v if s_v > 1e-12 else 0.0
    return Z


def compute_graph_only_scale(blocks: List[np.ndarray], variables: List[str],
                             marker: str, mu: Dict[str, float]
                             ) -> Dict[str, Tuple[float, float]]:
    """图专用变量（WHPI/WECS、类特有列）的模板内冻结 mu/sigma，仅学图用，不参与评分。"""
    pool = np.vstack(blocks)
    out = {}
    for p, v in enumerate(variables):
        if v == marker or v in mu:
            continue
        out[v] = (float(pool[:, p].mean()), float(pool[:, p].std()))
    return out


# ==================== 模板 Bootstrap（R1）====================
def bootstrap_lingam_template(blocks: List[np.ndarray], variables: List[str],
                              marker: str, ontology: Dict,
                              mu: Dict[str, float], sigma: Dict[str, float],
                              median_platform: float,
                              n_bootstrap: int = LINGAM_N_BOOTSTRAP,
                              seed: int = GLOBAL_SEED,
                              use_ontology: bool = True) -> Dict:
    """对一个 (k,g) 模板跑轨迹级 Bootstrap LiNGAM（每次本体约束后独立求逆）。"""
    from lingam import DirectLiNGAM
    rng = np.random.default_rng(seed)
    graph_only = compute_graph_only_scale(blocks, variables, marker, mu)
    n_traj = len(blocks)

    B_list: List[np.ndarray] = []
    n_removed_log: List[int] = []
    removed_set: Dict[str, Dict] = {}
    for _ in range(n_bootstrap):
        sampled = rng.integers(0, n_traj, size=n_traj)
        raw = np.vstack([blocks[i] for i in sampled])
        Z = transform_graph_block(raw, variables, marker, mu, sigma,
                                  median_platform, graph_only)
        model = DirectLiNGAM()
        model.fit(Z)
        B_c, rep = apply_ontology_constraint(model.adjacency_matrix_, variables,
                                             ontology, use_ontology)
        B_list.append(B_c)
        n_removed_log.append(rep["n_removed"])
        for e in rep["removed_forbidden"]:
            key = f"{e['from']}->{e['to']}"
            slot = removed_set.setdefault(key, {"count": 0, "basis": e["basis"]})
            slot["count"] += 1
    for v in removed_set.values():
        v["rate"] = v["count"] / n_bootstrap

    stats = aggregate_edge_stats(B_list, variables, marker)
    stable = select_stable_edges(stats)
    frontier = bfs_frontier([(e["from"], e["to"]) for e in stable], marker)
    unstable = [j for j, info in stats["cL"].items() if info["piL"] < STABLE_F]

    return {
        "variables": variables, "marker": marker,
        "n_train_trajectories": n_traj, "n_bootstrap": n_bootstrap,
        "median_platform": float(median_platform),
        "stable_edges": sorted(stable, key=lambda e: (-e["f_e"], e["from"], e["to"])),
        "all_edges": stats["edges"],
        "cL": stats["cL"],
        "frontier": frontier,
        "ontology_constraint": {
            "use_ontology": use_ontology,
            "removed_forbidden_rates": removed_set,
            "mean_removed_per_fit": float(np.mean(n_removed_log)),
        },
        "flags": {"unstable_magnitude_marker_edges": unstable},
    }


def fit_single_template(blocks: List[np.ndarray], variables: List[str],
                        marker: str, ontology: Dict,
                        mu: Dict[str, float], sigma: Dict[str, float],
                        median_platform: float,
                        use_ontology: bool = True) -> Dict:
    """D3 留一标定用：在全部给定块上确定性单次 DirectLiNGAM（无有放回重采样）。

    与 bootstrap_lingam_template 返回同构（n_bootstrap=1），单图边即标定拓扑。
    """
    from lingam import DirectLiNGAM
    graph_only = compute_graph_only_scale(blocks, variables, marker, mu)
    raw = np.vstack(blocks)
    Z = transform_graph_block(raw, variables, marker, mu, sigma,
                              median_platform, graph_only)
    model = DirectLiNGAM()
    model.fit(Z)
    B_c, rep = apply_ontology_constraint(model.adjacency_matrix_, variables,
                                         ontology, use_ontology)
    stats = aggregate_edge_stats([B_c], variables, marker)
    stable = select_stable_edges(stats)
    frontier = bfs_frontier([(e["from"], e["to"]) for e in stable], marker)
    return {
        "variables": variables, "marker": marker,
        "n_train_trajectories": len(blocks), "n_bootstrap": 1,
        "median_platform": float(median_platform),
        "stable_edges": sorted(stable, key=lambda e: (-e["f_e"], e["from"], e["to"])),
        "all_edges": stats["edges"],
        "cL": stats["cL"],
        "frontier": frontier,
        "ontology_constraint": {
            "use_ontology": use_ontology,
            "removed_forbidden_rates": {
                f"{e['from']}->{e['to']}": {"count": 1, "rate": 1.0, "basis": e["basis"]}
                for e in rep["removed_forbidden"]},
            "mean_removed_per_fit": float(rep["n_removed"]),
        },
        "flags": {"loo_single_fit": True},
    }


def save_signature(signature: Dict, accident_type: str, group: str,
                   out_dir: Path = SIGNATURE_DIR) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"signature_{accident_type}_{group}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(signature, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"saved -> {path}")
    return path


def load_signature(accident_type: str, group: str,
                   out_dir: Path = SIGNATURE_DIR) -> Optional[Dict]:
    path = Path(out_dir) / f"signature_{accident_type}_{group}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def annotate_severity_flip(signatures_by_group: Dict[str, Dict]) -> Dict[str, List[str]]:
    """marker->j 总效应符号跨组翻转 -> severity_flipped（辨识性证据，不删边）。"""
    flips = []
    first = next(iter(signatures_by_group.values()))
    for j in first["cL"]:
        signs = []
        for sig in signatures_by_group.values():
            c = sig["cL"].get(j)
            if c and abs(c["cL"]) > 1e-10 and c["piL"] >= STABLE_F:
                signs.append(np.sign(c["cL"]))
        if len(signs) >= 2 and len(set(signs)) > 1:
            flips.append(f"{first['marker']}->{j}")
    for sig in signatures_by_group.values():
        sig["flags"]["severity_flipped_marker_edges"] = flips
    return {"severity_flipped_marker_edges": flips}

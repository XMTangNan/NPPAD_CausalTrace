"""
Phase 1.5：本体约束因果发现。
- 禁止边：有明确物理排除依据时从结果中删除，记录依据与删除前后差异
- 允许边：仅当数据已学到时标记"本体支持"，未学到不强行添加
- 未知边：完全保留数据驱动结果

本体映射（ontology）由调用方传入，不在此处硬编码任何事故特有关系。
"""
import numpy as np
from typing import Dict, List, Tuple

from core.utils import logger


def apply_ontology_constraint(B: np.ndarray, variables: List[str],
                              ontology: Dict) -> Tuple[np.ndarray, Dict]:
    """对邻接矩阵 B 施加本体约束。
    返回 (B_constrained, constraint_report)。
    """
    n = len(variables)
    B_c = B.copy()
    var_idx = {v: i for i, v in enumerate(variables)}

    removed = []   # 被删除的禁止边
    marked = []    # 被标记为本体支持的允许边
    unknown = []   # 未知边（保留）

    for i in range(n):
        for j in range(n):
            if i == j or abs(B[i, j]) < 1e-10:
                continue
            src = variables[j]
            dst = variables[i]
            cat = _get_category(ontology, src, dst)
            if cat == "forbidden":
                basis = _get_forbidden_basis(ontology, src, dst)
                removed.append({"from": src, "to": dst, "weight": float(B[i, j]), "basis": basis})
                B_c[i, j] = 0.0
            elif cat == "allowed":
                marked.append({"from": src, "to": dst, "weight": float(B[i, j])})
            else:
                unknown.append({"from": src, "to": dst, "weight": float(B[i, j])})

    report = {
        "removed_forbidden": removed,
        "marked_allowed": marked,
        "kept_unknown": unknown,
        "n_removed": len(removed),
        "n_marked": len(marked),
        "n_unknown": len(unknown),
    }
    logger.info(f"本体约束: 删除禁止边 {len(removed)}, 标记允许边 {len(marked)}, "
                f"保留未知边 {len(unknown)}")
    return B_c, report


def _get_category(ontology: Dict, src: str, dst: str) -> str:
    for fe in ontology["forbidden_edges"]:
        if fe["from"] == src and fe["to"] == dst:
            return "forbidden"
    for rel in ontology["relations"]:
        if rel["from"] == src and rel["to"] == dst:
            return "allowed"
        if rel.get("direction") == "bidirectional" and rel["from"] == dst and rel["to"] == src:
            return "allowed"
    return "unknown"


def _get_forbidden_basis(ontology: Dict, src: str, dst: str) -> str:
    for fe in ontology["forbidden_edges"]:
        if fe["from"] == src and fe["to"] == dst:
            return fe["basis"]
    return ""


if __name__ == "__main__":
    pass

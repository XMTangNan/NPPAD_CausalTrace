"""
方法结合模块：LiNGAM（线性/非高斯）与 GAM-ANM（非线性 ANM）的融合。

策略：
- 边层面：共识边（两方法方向一致）= 最高可信度；冲突边（方向相反）= 不确定；单边 = 候选。
- 排序层面：Reciprocal Rank Fusion (RRF) 结合两方法排名。
- 标记变量对专项：两方法方向是否一致（通过 marker_a/marker_b 参数指定）。

依据：原论文同时跑 LiNGAM(20边) 和 DiffAN(15边) 但未说明如何结合（研读笔记 6.2）；
此处采用共识+RRF 的神经符号融合思路，使两种可辨识性假设互补。
"""
from typing import Dict, List


def classify_edges(lingam_edges: List[Dict], anm_edges: List[Dict]) -> Dict:
    """对两方法的稳定边做共识/冲突/单边分类。"""
    def edge_key(e):
        return (e["from"], e["to"])

    l_set = {edge_key(e): e for e in lingam_edges}
    a_set = {edge_key(e): e for e in anm_edges}

    # 共识边：方向完全一致
    consensus = []
    for k, e in l_set.items():
        if k in a_set:
            consensus.append({
                "from": e["from"], "to": e["to"],
                "lingam_f_e": e.get("f_e"), "anm_f_e": a_set[k].get("f_e"),
                "avg_f_e": (e.get("f_e", 0) + a_set[k].get("f_e", 0)) / 2,
            })

    # 冲突边：变量对相同但方向相反
    conflicting = []
    l_pairs = {(min(e["from"], e["to"]), max(e["from"], e["to"])): e
               for e in lingam_edges}
    for e in anm_edges:
        pk = (min(e["from"], e["to"]), max(e["from"], e["to"]))
        if pk in l_pairs:
            le = l_pairs[pk]
            if (le["from"], le["to"]) != (e["from"], e["to"]):
                conflicting.append({
                    "pair": f"{pk[0]}↔{pk[1]}",
                    "lingam_dir": f"{le['from']}->{le['to']}",
                    "anm_dir": f"{e['from']}->{e['to']}",
                })

    # 单边：仅一个方法识别
    lingam_only = [e for e in lingam_edges if edge_key(e) not in a_set]
    anm_only = [e for e in anm_edges if edge_key(e) not in l_set]

    return {
        "consensus": consensus,
        "conflicting": conflicting,
        "lingam_only": lingam_only,
        "anm_only": anm_only,
        "n_consensus": len(consensus),
        "n_conflicting": len(conflicting),
        "n_lingam_only": len(lingam_only),
        "n_anm_only": len(anm_only),
    }


def reciprocal_rank_fusion(lingam_ranking: List[Dict], anm_ranking: List[Dict],
                            k: int = 60) -> List[Dict]:
    """RRF 融合排序：score(v) = Σ 1/(k + rank_i(v))。
    排名越靠前分数越高；两方法都排前的变量得分最高。
    """
    lingam_rank = {r["variable"]: r["rank"] for r in lingam_ranking}
    anm_rank = {r["variable"]: r["rank"] for r in anm_ranking}
    all_vars = set(lingam_rank) | set(anm_rank)

    fused = []
    for var in all_vars:
        lr = lingam_rank.get(var, len(lingam_ranking) + 1)
        ar = anm_rank.get(var, len(anm_ranking) + 1)
        score = 1.0 / (k + lr) + 1.0 / (k + ar)
        # 附带两方法各自的排名
        lr_effect = next((r.get("median_total_effect", r.get("avg_marginal_effect"))
                          for r in lingam_ranking if r["variable"] == var), None)
        ar_effect = next((r.get("avg_marginal_effect", r.get("median_total_effect"))
                          for r in anm_ranking if r["variable"] == var), None)
        fused.append({
            "variable": var,
            "rrf_score": score,
            "lingam_rank": lr,
            "anm_rank": ar,
            "lingam_effect": lr_effect,
            "anm_effect": ar_effect,
        })
    fused.sort(key=lambda x: x["rrf_score"], reverse=True)
    for i, item in enumerate(fused):
        item["rank"] = i + 1
    return fused


def marker_direction_check(lingam_edges: List[Dict], anm_edges: List[Dict],
                           a: str, b: str) -> Dict:
    """标记变量对专项：两方法方向是否一致。

    Args:
        a, b: 待检查方向的两个变量名（如 WLR 与 P）。
    """
    def find_dir(edges, x, y):
        for e in edges:
            if e["from"] == x and e["to"] == y:
                return f"{x}->{y}"
            if e["from"] == y and e["to"] == x:
                return f"{y}->{x}"
        return "none"

    l_dir = find_dir(lingam_edges, a, b)
    a_dir = find_dir(anm_edges, a, b)
    return {
        "marker_a": a,
        "marker_b": b,
        "lingam": l_dir,
        "anm": a_dir,
        "agreement": l_dir == a_dir and l_dir != "none",
    }


def build_ensemble(lingam_edges, anm_edges, lingam_ranking, anm_ranking,
                   marker_a: str = "WLR", marker_b: str = "P") -> Dict:
    """完整方法结合输出。

    Args:
        marker_a, marker_b: 方向专项检查的两个变量名。
    """
    edge_class = classify_edges(lingam_edges, anm_edges)
    fused_ranking = reciprocal_rank_fusion(lingam_ranking, anm_ranking)
    marker_check = marker_direction_check(lingam_edges, anm_edges, marker_a, marker_b)
    return {
        "edge_classification": edge_class,
        "fused_ranking": fused_ranking,
        "marker_check": marker_check,
    }


if __name__ == "__main__":
    from core.utils import save_json
    lingam_edges = [
        {"from": "WLR", "to": "P", "f_e": 0.98},
        {"from": "LVPZ", "to": "P", "f_e": 0.98},
        {"from": "VOL", "to": "P", "f_e": 0.90},
        {"from": "THA", "to": "P", "f_e": 0.55},
    ]
    anm_edges = [
        {"from": "WLR", "to": "P", "f_e": 0.85},
        {"from": "LVPZ", "to": "P", "f_e": 0.92},
        {"from": "PWNT", "to": "P", "f_e": 0.80},
        {"from": "P", "to": "VOL", "f_e": 0.70},  # 与 LiNGAM 方向相反
    ]
    lingam_ranking = [
        {"rank": 1, "variable": "THA", "median_total_effect": 1.17},
        {"rank": 2, "variable": "LVPZ", "median_total_effect": 0.66},
        {"rank": 3, "variable": "PSGA", "median_total_effect": 0.55},
        {"rank": 4, "variable": "TAVG", "median_total_effect": 0.54},
        {"rank": 5, "variable": "QMWT", "median_total_effect": 0.52},
    ]
    anm_ranking = [
        {"rank": 1, "variable": "PWNT", "avg_marginal_effect": 14.12},
        {"rank": 2, "variable": "QMWT", "avg_marginal_effect": 10.66},
        {"rank": 3, "variable": "LVPZ", "avg_marginal_effect": 5.27},
        {"rank": 4, "variable": "WHPI", "avg_marginal_effect": -2.08},
        {"rank": 5, "variable": "WECS", "avg_marginal_effect": -2.08},
    ]
    result = build_ensemble(lingam_edges, anm_edges, lingam_ranking, anm_ranking)
    save_json(result, "ensemble_demo")
    print(f"共识边: {result['edge_classification']['n_consensus']}, "
          f"冲突: {result['edge_classification']['n_conflicting']}")
    print(f"标记方向一致: {result['marker_check']['agreement']}")
    print("RRF Top-5:")
    for r in result["fused_ranking"][:5]:
        print(f"  {r['rank']}. {r['variable']} (lingam={r['lingam_rank']}, anm={r['anm_rank']})")

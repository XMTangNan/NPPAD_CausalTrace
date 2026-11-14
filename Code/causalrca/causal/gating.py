"""候选池公差门（方案 §4.3.2/§4.3.8/§4.3.12）：先筛后排，无 λ、无权重。

纪律（预注册，跑前冻结）：
  * 排序分只有 `E_v = Σ_s |T[s,v]|`（由上游 `total_effect` 传入），本模块不改分数；
  * 知识只有两个落点：禁边/禁路径改 `B`（见 knowledge/signatures），公差门改候选池（本模块）；
  * 门槛 `g`、`not_evaluable` 处置、σ 桶参数全部来自 `gate_policy_v1.yaml`，
    文件缺失或 sha256 不符即抛错（不出排序）；
  * 被剔除者不物理删除：`in_pool=false` + `gate_status` + `rank_if_kept`。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from causalrca.io.logging import logger

POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "gate_policy_v1.yaml"
POLICY_SHA256 = "b4ad550b8183ce0f159531c5c7d58939f3a75ac6ac81f13238744b744a9331d3"

GATE_PASS = "pass"
GATE_FAIL = "fail"
GATE_NOT_EVALUABLE = "not_evaluable"

SIGMA_SOURCE_MEASURED = "measured_mad"
SIGMA_SOURCE_PHYSICAL = "physical_floor"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def policy_sha256() -> str:
    return _sha256(POLICY_PATH)


def load_policy(path: Optional[Path] = None, verify: bool = True) -> Dict:
    """读取并校验冻结门策略；缺失/版本或哈希不符 -> RuntimeError。"""
    import yaml
    p = Path(path) if path is not None else POLICY_PATH
    if not p.is_file():
        raise RuntimeError(f"[gate] 门策略文件缺失：{p}（策略缺失即中止，不出排序）")
    actual = _sha256(p)
    if verify and actual != POLICY_SHA256:
        raise RuntimeError(
            f"[gate] 门策略 sha256 不符：actual={actual} expected={POLICY_SHA256}"
            "（策略已冻结，跑前不得改）")
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if doc.get("version") != "gate_policy_v1":
        raise RuntimeError(f"[gate] 门策略版本不符：{doc.get('version')!r}")
    if doc.get("schema") != "causaltrace/gate_policy/v1":
        raise RuntimeError(f"[gate] 门策略 schema 不符：{doc.get('schema')!r}")
    return doc


# ==================== σ 五桶（读取 tolerance 的 sigma_v2）====================
def _sigma_block(th: Optional[Dict], policy: Dict) -> Dict:
    """从公差条目取 sigma_v2；缺失按 b5/not_calibrated 处理（不编下限）。"""
    if not th:
        return {"bucket": "b5", "sigma_source": None, "sigma_value": None,
                "sigma_rule_id": "sigma_b5_not_calibrated",
                "evaluability": "not_evaluable",
                "not_evaluable_reason": "not_calibrated"}
    sv = th.get("sigma_v2")
    if isinstance(sv, dict):
        out = dict(sv)
        out.setdefault("evaluability", "ok" if out.get("sigma_value") is not None
                       else "not_evaluable")
        return out
    # 兼容旧公差产物：没有 sigma_v2 一律判不可评估，不偷偷回退旧 σ
    return {"bucket": "b5", "sigma_source": None, "sigma_value": None,
            "sigma_rule_id": "sigma_b5_not_calibrated",
            "evaluability": "not_evaluable",
            "not_evaluable_reason": "not_calibrated"}


# ==================== 门判定 ====================
def build_gate(accident_type: str, group: str, variables: Sequence[str],
               total_effect: Dict[str, float], tolerance: Dict,
               deviation: Dict, policy: Dict) -> Dict:
    """逐变量判 gate_status，返回 gate 块（只读上游，不改分数）。"""
    g = float(policy.get("g"))
    thresholds = (tolerance or {}).get("thresholds") or {}
    devs = (deviation or {}).get("deviations") or {}
    tol_source = (tolerance or {}).get("tolerance_source",
                                       policy.get("tolerance_source", "data_proxy"))
    per: Dict[str, Dict] = {}
    for v in variables:
        th = thresholds.get(v)
        sv = _sigma_block(th, policy)
        d = devs.get(v) or {}
        d_raw = d.get("d_raw")
        evaluable = (sv.get("evaluability") == "ok") and (d_raw is not None)
        reason = None
        bucket = sv.get("bucket")
        if not evaluable:
            reason = sv.get("not_evaluable_reason") or d.get("d_status_reason")
            if d.get("d_status") == "not_evaluable":
                reason = d.get("d_status_reason") or reason
            if reason is None:
                reason = "not_calibrated"
            status = GATE_NOT_EVALUABLE
        elif float(d_raw) > g:
            status = GATE_PASS
        else:
            status = GATE_FAIL
        per[v] = {
            "variable": v,
            "gate_status": status,
            "gate_reason": reason,
            "deviation_raw": d_raw,
            "deviation_evaluability": sv.get("evaluability"),
            "sigma_source": sv.get("sigma_source"),
            "sigma_value": sv.get("sigma_value"),
            "sigma_rule_id": sv.get("sigma_rule_id"),
            "sigma_bucket": bucket,
            "tolerance_source": tol_source,
            "tolerance_entry_id": (f"tolerance_{accident_type}:{v}" if th else None),
            "total_effect": float(total_effect.get(v) or 0.0),
        }
    return {
        "accident_type": accident_type,
        "group": group,
        "gate_policy_version": policy.get("version"),
        "gate_rule_id": policy.get("gate_rule_id"),
        "g": g,
        "not_evaluable_policy": policy.get("not_evaluable_policy"),
        "tolerance_source": tol_source,
        "n_total": len(per),
        "variables": per,
    }


def _order_key(row: Dict, nodes: Dict[str, Dict]) -> tuple:
    """池内排序：效应分 DESC + 旧次键（逐位沿用 source_tracing）。"""
    nd = nodes.get(row["variable"], {})
    return (-float(row.get("total_effect") or 0.0),
            int(nd.get("in_degree") or 0),
            -float(nd.get("weighted_reach") or 0.0),
            -float(nd.get("reach_size") or 0.0),
            -float(nd.get("consensus_rate") or 0.0),
            row["variable"])


def apply_gate(candidates: Sequence[Dict], nodes: Dict[str, Dict],
               gate: Dict, policy: Dict) -> Dict:
    """先筛后排：池内按 E_v 排序，剔除者保留并给 rank_if_kept。"""
    per = gate["variables"]
    for c in candidates:
        info = per.get(c["variable"], {})
        c["gate_status"] = info.get("gate_status", GATE_NOT_EVALUABLE)
        c["gate_reason"] = info.get("gate_reason")
        c["in_pool"] = c["gate_status"] == GATE_PASS
        c["deviation_raw"] = info.get("deviation_raw")
        c["sigma_source"] = info.get("sigma_source")
        c["sigma_value"] = info.get("sigma_value")
        c["sigma_rule_id"] = info.get("sigma_rule_id")
        c["sigma_bucket"] = info.get("sigma_bucket")
        c["tolerance_source"] = info.get("tolerance_source")
        c["gate_rule_id"] = gate.get("gate_rule_id")

    keep_isolated = bool(policy.get("not_evaluable_policy") == "include")
    if keep_isolated:  # 宽松档：仅用于对照，默认不走
        for c in candidates:
            if c["gate_status"] == GATE_NOT_EVALUABLE:
                c["in_pool"] = True

    ranked_all = sorted(candidates, key=lambda r: _order_key(r, nodes))
    rank_if_kept: Dict[str, int] = {}
    conductive = [c for c in ranked_all if int(nodes.get(c["variable"], {})
                                               .get("out_degree") or 0) > 0]
    isolated = [c for c in ranked_all if int(nodes.get(c["variable"], {})
                                             .get("out_degree") or 0) == 0]
    for i, c in enumerate(conductive + isolated, 1):
        rank_if_kept[c["variable"]] = i

    pool = [c for c in ranked_all if c["in_pool"]]
    pool_cond = [c for c in pool if int(nodes.get(c["variable"], {})
                                        .get("out_degree") or 0) > 0]
    pool_iso = [c for c in pool if int(nodes.get(c["variable"], {})
                                       .get("out_degree") or 0) == 0]
    pool_order = pool_cond + pool_iso
    for i, c in enumerate(pool_order, 1):
        c["rank_v2"] = i
    for c in ranked_all:
        c["rank_if_kept"] = rank_if_kept.get(c["variable"])
        if not c["in_pool"]:
            c["rank_v2"] = None

    excluded = [{"variable": c["variable"], "gate_status": c["gate_status"],
                 "gate_reason": c["gate_reason"], "deviation_raw": c["deviation_raw"],
                 "sigma_source": c["sigma_source"], "sigma_rule_id": c["sigma_rule_id"],
                 "tolerance_entry_id": per.get(c["variable"], {}).get("tolerance_entry_id"),
                 "rank_if_kept": c["rank_if_kept"]}
                for c in ranked_all if not c["in_pool"]]
    return {
        "candidate_pool": {
            "n_total": len(ranked_all),
            "n_kept": len(pool_order),
            "n_excluded": len(excluded),
            "policy_version": gate.get("gate_policy_version"),
            "gate_rule_id": gate.get("gate_rule_id"),
            "g": gate.get("g"),
            "tolerance_source": gate.get("tolerance_source"),
            "excluded": excluded,
        },
        "pool_order": pool_order,
        "ranked_all": pool_order + [c for c in ranked_all if not c["in_pool"]],
        "rank_if_kept": rank_if_kept,
    }


def gate_compare(before_top: Sequence[str], after_pool_order: Sequence[Dict],
                 n_total: int) -> Dict:
    """门控 vs 全量对照（只进报告，不改运行口径）。"""
    after = [c["variable"] for c in after_pool_order]
    top1_before = before_top[0] if before_top else None
    top1_after = after[0] if after else None
    before_rank = {v: i for i, v in enumerate(before_top, 1)}
    n_changed = sum(1 for v, r in before_rank.items()
                    if (after.index(v) + 1 if v in after else None) != r)
    return {
        "n_rank_changed": n_changed,
        "top1_before": top1_before,
        "top1_after": top1_after,
        "top5_before": list(before_top[:5]),
        "top5_after": after[:5],
        "top5_set_diff": sorted(set(before_top[:5]) ^ set(after[:5])),
        "n_excluded": int(n_total - len(after)),
        "pool_size": len(after),
    }


def save_policy_manifest(run_id: Optional[str] = None) -> Dict:
    """返回可写进 manifest 的策略指纹。"""
    return {"gate_policy_version": "gate_policy_v1",
            "gate_policy_sha256": policy_sha256(),
            "gate_policy_path": "Code/causalrca/config/gate_policy_v1.yaml"}


def dump_json(doc: Dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

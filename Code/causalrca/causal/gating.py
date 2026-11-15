"""候选池公差门（方案 §4.3.2/§4.3.8/§4.3.12/§4.3.14）：先筛后排，无 λ、无权重。

纪律（预注册，跑前冻结）：
  * 排序分只有 `E_v = Σ_s |T[s,v]|`（由上游 `total_effect` 传入），本模块不改分数；
  * 知识只有两个落点：禁边/禁路径改 `B`（见 knowledge/signatures），公差门改候选池（本模块）；
  * 门槛按 σ 尺度来源分档（V23-DESIGN-107）：实测噪声档用零假设最大统计量分位，
    下限档沿用“超过 1 倍下限”的旧工程约定；
  * 门槛参数、σ 桶参数、结构性剔除规则全部来自 `gate_policy_v2.yaml`，
    文件缺失或 sha256 不符即抛错（不出排序）；
  * 被剔除者不物理删除：`in_pool=false` + `gate_status` + `rank_if_kept`。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from causalrca.io.logging import logger

POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "gate_policy_v2.yaml"
POLICY_SHA256 = "6ff290be1d5d5bbdd34891363b802dec379c24e9e9b7e1f41d86a58d6140a8f5"
POLICY_VERSION = "gate_policy_v2"

GATE_PASS = "pass"
GATE_FAIL = "fail"
GATE_NOT_EVALUABLE = "not_evaluable"
GATE_STRUCTURAL = "excluded_structural"

EXCL_NUMERIC = "numeric_fail"
EXCL_NOT_EVALUABLE = "not_evaluable"
EXCL_STRUCTURAL = "structural"

SIGMA_SOURCE_MEASURED = "measured_mad"
SIGMA_SOURCE_PHYSICAL = "physical_floor"
SIGMA_SOURCE_FALLBACK = "fallback_floor"


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
    if doc.get("version") != POLICY_VERSION:
        raise RuntimeError(f"[gate] 门策略版本不符：{doc.get('version')!r}")
    if doc.get("schema") != "causaltrace/gate_policy/v2":
        raise RuntimeError(f"[gate] 门策略 schema 不符：{doc.get('schema')!r}")
    return doc


def gate_threshold(sigma_source: Optional[str], n_t: Optional[int], policy: Dict) -> Dict:
    """按 σ 的尺度来源取门槛（V23-DESIGN-107）：两把尺子各有各的零假设。

    * measured_mad：d_raw 是注入后 n_t 点的 max|z|，零假设下分位为
      Φ⁻¹(1 − alpha/(2·n_t))；alpha 预注册，n_t 只来自数据长度。
    * floor_scale（physical/fallback）：沿用旧工程约定“超过 1 倍下限即超差”。
    """
    gcfg = policy.get("g") or {}
    if sigma_source == SIGMA_SOURCE_MEASURED:
        spec = gcfg.get("measured_mad") or {}
        alpha = float(spec.get("alpha", 0.05))
        n = int(n_t or 0)
        if n <= 0:
            raise RuntimeError("[gate] measured_mad 门槛需要 n_t>0（注入后点数）")
        from statistics import NormalDist
        value = float(NormalDist().inv_cdf(1.0 - alpha / (2.0 * n)))
        return {"value": value, "rule_id": "g_stat_null_max",
                "alpha": alpha, "n_t": n}
    return {"value": float(gcfg.get("floor_scale", 1.0)),
            "rule_id": "g_floor_scale", "alpha": None, "n_t": None}


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
               deviation: Dict, policy: Dict,
               n_post_inject_points: Optional[int] = None,
               marker: Optional[str] = None,
               safety_vars: Optional[Sequence[str]] = None) -> Dict:
    """逐变量判 gate_status，返回 gate 块（只读上游，不改分数）。

    V23-DESIGN-107：门槛按 σ 尺度来源分档；marker / safety 动作变量走预注册结构性剔除
    （与统计门分开记，避免把“标签泄漏”写成“不可评估”）。
    """
    thresholds = (tolerance or {}).get("thresholds") or {}
    devs = (deviation or {}).get("deviations") or {}
    tol_source = (tolerance or {}).get("tolerance_source",
                                       policy.get("tolerance_source", "data_proxy"))
    struct = (policy.get("structural_exclusions") or {})
    struct_on = bool(struct.get("enabled", True))
    safety = set(safety_vars or [])
    per: Dict[str, Dict] = {}
    for v in variables:
        th = thresholds.get(v)
        sv = _sigma_block(th, policy)
        d = devs.get(v) or {}
        d_raw = d.get("d_raw")
        bucket = sv.get("bucket")
        g_info = gate_threshold(sv.get("sigma_source"), n_post_inject_points, policy)

        # 结构性剔除优先于统计门（预注册，与结果无关）
        if struct_on and marker is not None and v == marker:
            status, reason = GATE_STRUCTURAL, "marker_variable"
        elif struct_on and v in safety:
            status, reason = GATE_STRUCTURAL, "safety_action_variable"
        else:
            evaluable = (sv.get("evaluability") == "ok") and (d_raw is not None)
            if not evaluable:
                reason = sv.get("not_evaluable_reason") or d.get("d_status_reason")
                if d.get("d_status") in ("not_evaluable", "not_comparable"):
                    reason = d.get("d_status_reason") or reason
                if reason is None:
                    reason = "no_scale"
                status = GATE_NOT_EVALUABLE
            elif float(d_raw) > g_info["value"]:
                status, reason = GATE_PASS, None
            else:
                status, reason = GATE_FAIL, None
        per[v] = {
            "variable": v,
            "gate_status": status,
            "gate_reason": reason,
            "deviation_raw": d_raw,
            "deviation_evaluability": sv.get("evaluability"),
            "sigma_source": sv.get("sigma_source"),
            "sigma_value": sv.get("sigma_value"),
            "sigma_rule_id": sv.get("sigma_rule_id"),
            "sigma_scale_kind": sv.get("sigma_scale_kind"),
            "insufficient_baseline_flag": sv.get("insufficient_baseline_flag"),
            "sigma_bucket": bucket,
            "g_threshold": g_info["value"],
            "g_rule_id": g_info["rule_id"],
            "tolerance_source": tol_source,
            "tolerance_entry_id": (f"tolerance_{accident_type}:{v}" if th else None),
            "total_effect": float(total_effect.get(v) or 0.0),
        }
    return {
        "accident_type": accident_type,
        "group": group,
        "gate_policy_version": policy.get("version"),
        "gate_rule_id": policy.get("gate_rule_id"),
        "g": (policy.get("g") or {}),
        "n_post_inject_points": n_post_inject_points,
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
        c["sigma_scale_kind"] = info.get("sigma_scale_kind")
        c["sigma_bucket"] = info.get("sigma_bucket")
        c["g_threshold"] = info.get("g_threshold")
        c["g_rule_id"] = info.get("g_rule_id")
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

    def _excl_class(status: str) -> str:
        if status == GATE_STRUCTURAL:
            return EXCL_STRUCTURAL
        if status == GATE_NOT_EVALUABLE:
            return EXCL_NOT_EVALUABLE
        return EXCL_NUMERIC

    excluded = [{"variable": c["variable"], "gate_status": c["gate_status"],
                 "gate_reason": c["gate_reason"], "deviation_raw": c["deviation_raw"],
                 "exclusion_class": _excl_class(c["gate_status"]),
                 "sigma_source": c["sigma_source"], "sigma_rule_id": c["sigma_rule_id"],
                 "sigma_bucket": c["sigma_bucket"], "g_threshold": c.get("g_threshold"),
                 "tolerance_entry_id": per.get(c["variable"], {}).get("tolerance_entry_id"),
                 "rank_if_kept": c["rank_if_kept"]}
                for c in ranked_all if not c["in_pool"]]
    n_by_class = {k: sum(1 for e in excluded if e["exclusion_class"] == k)
                  for k in (EXCL_NUMERIC, EXCL_NOT_EVALUABLE, EXCL_STRUCTURAL)}
    return {
        "candidate_pool": {
            "n_total": len(ranked_all),
            "n_kept": len(pool_order),
            "n_excluded": len(excluded),
            "n_excluded_by_class": n_by_class,
            "policy_version": gate.get("gate_policy_version"),
            "gate_rule_id": gate.get("gate_rule_id"),
            "g": gate.get("g"),
            "n_post_inject_points": gate.get("n_post_inject_points"),
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
    return {"gate_policy_version": POLICY_VERSION,
            "gate_policy_sha256": policy_sha256(),
            "gate_policy_path": "Code/causalrca/config/gate_policy_v2.yaml"}


def dump_json(doc: Dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

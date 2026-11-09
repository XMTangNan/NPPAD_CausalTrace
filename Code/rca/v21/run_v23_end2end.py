"""v2.3 C 阶段主执行器：签名 → 溯源（全部来自同一次计算 / 同一 run_id）。

职责（Code/STRUCTURE.md §2、方案 v2.3 §6.1 C1/C2）：
- 遍历 READY_CODES × 三组，逐模板跑轨迹级 Bootstrap LiNGAM（算法口径不变）
  并基于 T=(I−B)⁻¹ 输出根因榜；
- 签名、溯源、拓扑、B 截断敏感性全部通过 rca.v21.artifact 写入唯一产物根，
  并共用同一个 run_id（图与汇总由 run_v23_graphs / severity_aggregation 读取）；
- V23-STAT-001：v2.3 只有 LiNGAM 单引擎，n_consensus/n_conflict 一律写 null，
  consensus_available=false，禁止把稳定边当共识边；
- V23-ALGO-001：记录截断前条件数、被截断边清单、clip=3/5/10 排名敏感性；
- V23-DATA-002：按轨迹 t_last 截断恢复段窗口，在签名 meta 写 baseline_coverage；
- C1：PARTIAL_CODES 只做单模板 smoke（partial=true，不写签名/榜单），
  MISSING_CODES 跳过并落 tracing/skipped_classes.json，绝不生成占位结果。

用法（cd Code）：
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_end2end --n-bootstrap 100
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_end2end --classes LOCA --groups low
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_end2end --with-gam-consensus
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from rca.rca_config_v21 import (
    ALL_ACCIDENT_TYPES, GROUP_ORDER, MARKERS, GLOBAL_SEED,
    LEARN_WINDOW_START, LEARN_WINDOW_END, LEARN_WINDOW_POINTS,
    BASELINE_WINDOW_START, BASELINE_WINDOW_END, BASELINE_WINDOW_POINTS,
    LINGAM_N_BOOTSTRAP, CONSTANT_SIGMA, PHYSICAL_CANDIDATES,
    SAFETY_STEP_VARS, TIME_STEP, V21_OUTPUT, V23_SUBDIRS,
)
from rca.v21.accident_config import (
    READY_CODES, PARTIAL_CODES, MISSING_CODES, get_accident,
)
from rca.v21 import data as D
from rca.v21 import artifact
from rca.v21 import source_tracing as ST
from rca.v21.knowledge import build_ontology, ENTITY_TABLE
from rca.v21.signatures import bootstrap_lingam_template, fit_single_template
from core.utils import logger

# V23-CODE-021：必须读 A 阶段写入的 v2.3 变量集（V23_SUBDIRS["variable_sets"]），
# 不能读 rca_v21/variable_selection（11-03 旧集，LOCA 仍含 HUP）。
VS_SUMMARY = V23_SUBDIRS["variable_sets"] / "variable_selection_summary.json"
CONSENSUS_SOURCE_NONE = "none: v2.3 单引擎，GAM 共识未接入"
CONSENSUS_NOTE = "单引擎稳定边，不等同共识边"


# ==================== 输入 ====================
def load_inputs() -> Dict:
    """C 阶段输入（A/B 阶段已冻结的划分、共享基线、marker 尺度、变量集）。"""
    if not VS_SUMMARY.exists():
        raise FileNotFoundError(f"缺变量选取结果: {VS_SUMMARY}（先跑 A 阶段）")
    with open(VS_SUMMARY, encoding="utf-8") as f:
        vs = json.load(f)
    return {
        "manifest": D.load_json("split_manifest.json"),
        "baseline": D.load_json("shared_baseline.json"),
        "scale": D.load_json("marker_scale.json"),
        "selected_vars": vs["selected_vars"],
        "vs_report": {
            k: str(Path("rca_v23") / "variable_sets" / f"selection_report_{k}.json")
            for k in vs["selected_vars"]
        },
    }


def _baseline_coverage(k: str, sevs: Sequence[int],
                       t_last: Dict[int, float]) -> Dict:
    """V23-DATA-002：恢复段 [520,2030] 按轨迹 t_last 截断后的覆盖度（不改窗口常量）。"""
    rows = []
    for sev in sevs:
        last = float(t_last[sev])
        end = min(BASELINE_WINDOW_END, last)
        if end < BASELINE_WINDOW_START:
            n_actual = 0
        else:
            n_actual = min(BASELINE_WINDOW_POINTS,
                           int(round((end - BASELINE_WINDOW_START) / TIME_STEP)) + 1)
        rows.append({
            "severity": int(sev),
            "t_last": last,
            "n_points": n_actual,
            "expected_points": BASELINE_WINDOW_POINTS,
            "truncated": bool(last < BASELINE_WINDOW_END - 1e-9),
        })
    n_pts = [r["n_points"] for r in rows]
    return {
        "window": [BASELINE_WINDOW_START, BASELINE_WINDOW_END],
        "expected_points": BASELINE_WINDOW_POINTS,
        "n_trajectories": len(rows),
        "n_truncated": sum(1 for r in rows if r["truncated"]),
        "mean_actual_points": round(float(np.mean(n_pts)), 3) if n_pts else 0.0,
        "min_actual_points": int(min(n_pts)) if n_pts else 0,
        "mean_coverage_ratio": round(float(np.mean(n_pts)) / BASELINE_WINDOW_POINTS, 4)
        if n_pts else 0.0,
        "detail": rows,
        "note": "按轨迹 t_last 截断恢复段；全局窗口常量未改",
    }


def load_template(k: str, g: str, gvars: Sequence[str], manifest: Dict
                  ) -> Tuple[List[np.ndarray], List[int], Dict]:
    """加载模板训练轨迹的学图窗块 + t_last → (blocks, sevs, baseline_coverage)。"""
    sevs = list(manifest["classes"][k]["groups"][g]["train_template_severities"])
    blocks: List[np.ndarray] = []
    t_last: Dict[int, float] = {}
    for sev in sevs:
        df = D.load_trajectory(k, sev)
        t_last[sev] = float(df["TIME"].iloc[-1])
        sub = D.window_slice(df, LEARN_WINDOW_START, LEARN_WINDOW_END)
        if len(sub) != LEARN_WINDOW_POINTS:
            raise ValueError(
                f"{k}/{sev}: 学图窗点数 {len(sub)} != {LEARN_WINDOW_POINTS}")
        X = sub[list(gvars)].to_numpy(dtype=float)
        if not np.all(np.isfinite(X)):
            raise ValueError(f"{k}/{sev}: 学图窗含非有限值")
        blocks.append(X)
    return blocks, sevs, _baseline_coverage(k, sevs, t_last)


# ==================== 共识（单引擎默认 / 可选 GAM）====================
def load_consensus(k: str, g: str, run_id: str, enabled: bool) -> Dict:
    """返回共识上下文；未启用时一切为 None（V23-STAT-001）。

    只有显式 --with-gam-consensus 才读取 v2.1 遗留 consensus_{K}_{g}.json；
    文件自带 run_id 且与本 run 不一致时拒绝混用。
    """
    if not enabled:
        return {"available": False, "clf": {}, "source": CONSENSUS_SOURCE_NONE,
                "consensus_run_id": None, "sha256": None}
    if k not in ("LOCA", "SGATR", "FLB"):
        raise ValueError(f"{k} 不在 3 类 pilot 内，无 GAM 共识文件可接入")
    path = V21_OUTPUT / f"consensus_{k}_{g}.json"
    if not path.exists():
        raise FileNotFoundError(f"启用 --with-gam-consensus 但缺共识文件: {path}")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    cons_run = doc.get("run_id")
    if cons_run and cons_run != run_id:
        raise ValueError(
            f"拒绝混用: {path.name} 的 run_id={cons_run} != 本次 run_id={run_id}")
    return {
        "available": True,
        "clf": doc.get("classification", {}),
        "source": f"rca_v21/consensus_{k}_{g}.json",
        "consensus_run_id": cons_run,
        "sha256": artifact.sha256_file(path),
        "n_boot": doc.get("n_boot"),
        "note": ("GAM 共识来源文件无 run_id（v2.1 遗留），按 sha256 追溯"
                 if cons_run is None else "GAM 共识与本 run 同源"),
    }


def consensus_counts(stable_edges: Sequence[Dict], cons: Dict) -> Tuple[Optional[int], Optional[int]]:
    """单引擎返回 (None, None)；接入 GAM 后统计稳定边中的 consensus/conflict 数。"""
    if not cons["available"]:
        return None, None
    clf = cons["clf"]
    n_cons = sum(1 for e in stable_edges
                 if clf.get(f"{e['from']}->{e['to']}", {}).get("label") == "consensus")
    n_conf = sum(1 for e in stable_edges
                 if clf.get(f"{e['from']}->{e['to']}", {}).get("label") == "conflict")
    return n_cons, n_conf


# ==================== 单模板计算 ====================
def compute_template(k: str, g: str, gvars: Sequence[str], inputs: Dict,
                     n_bootstrap: int, cons: Dict) -> Dict:
    """纯计算（不落盘）：签名 + 溯源 + 截断敏感性。"""
    marker = MARKERS[k]
    blocks, sevs, cov = load_template(k, g, gvars, inputs["manifest"])
    ontology = build_ontology(k, gvars)
    gating = {
        "n_available_vars": len(ENTITY_TABLE),
        "n_in_model": len(gvars),
        "gate_ratio": round(len(gvars) / max(len(ENTITY_TABLE), 1), 4),
        "note": "96 变量本体中经 A1 四阶段漏斗入模的变量数（其余不参与学图与排名）",
    }
    meta = {
        "group_pattern": g,
        "learn_window": [LEARN_WINDOW_START, LEARN_WINDOW_END],
        "train_severities": sevs,
        "n_train_trajectories": len(blocks),
        "partial": False,
        "baseline_coverage": cov,
        "gating": gating,
    }

    sig = bootstrap_lingam_template(
        blocks, list(gvars), marker, ontology,
        inputs["baseline"]["mu"], inputs["baseline"]["sigma"],
        inputs["scale"]["median_platform"][k],
        n_bootstrap=n_bootstrap, seed=GLOBAL_SEED, use_ontology=True,
        extra_meta=meta,
    )

    stable = sig["stable_edges"]
    variables = list(gvars)
    topology = ST.compute_topology(stable, variables, marker, cons["clf"])
    traced = ST.trace_roots(topology, marker, stable, variables, top_k=5)
    roots = traced["root_candidates"]
    diag = traced["diagnostics"]

    # 拓扑节点补 role（供图着色，不改排序）
    nodes = {
        v: {**topology[v], "role": ST.classify_role(topology[v]["in_degree"],
                                                    topology[v]["out_degree"]),
            "total_effect": traced["total_effect"].get(v)}
        for v in variables
    }
    n_cons, n_conf = consensus_counts(stable, cons)
    return {
        "k": k, "g": g, "marker": marker, "variables": variables,
        "sig": sig, "stable": stable, "topology": topology, "nodes": nodes,
        "roots": roots, "diag": diag, "gating": gating,
        "baseline_coverage": cov, "n_severities": len(sevs),
        "n_consensus": n_cons, "n_conflict": n_conf,
    }


def _root_entry(c: Dict) -> Dict:
    return {
        "rank": c["rank"],
        "variable": c["variable"],
        "entity": ST.enrich_entity(c["variable"]),
        "role": c["role"],
        "in_degree": c["in_degree"],
        "out_degree": c["out_degree"],
        "reach_size": c["reach_size"],
        "weighted_reach": round(c["weighted_reach"], 4),
        "source_paths": c["in_degree"],
        "total_effect": round(c["total_effect"], 6),
        "tracing_score": round(c["tracing_score"], 6),
        "consensus_rate": c["consensus_rate"] if c["consensus_rate"] else None,
    }


# ==================== 单模板落盘 ====================
def run_template(k: str, g: str, gvars: Sequence[str], inputs: Dict,
                 n_bootstrap: int, run_id: str, cons: Dict) -> Dict:
    """计算并写入 signature / root_candidates / topology / clip_sensitivity。"""
    r = compute_template(k, g, gvars, inputs, n_bootstrap, cons)
    sig, diag, roots = r["sig"], r["diag"], r["roots"]
    # V23-ALGO-001：弃权门控（B_CLIP 保持 5，仅标记降级）
    stability = ST.stability_gate(diag, len(r["stable"]))
    sig.setdefault("auxiliary", {})["stability"] = stability
    sig_name = f"signature_{k}_{g}"
    sig_src = f"signatures/{sig_name}.json"

    artifact.write_artifact(
        sig_name, sig, "signatures", run_id=run_id, accident_type=k, group=g,
        source=inputs["vs_report"].get(k, "rca_v23/variable_sets/"))

    common = {
        "template": {"k": k, "g": g, "marker": r["marker"],
                     "n_vars": len(gvars), "n_train_trajectories": sig["n_train_trajectories"]},
        "engine": "lingam",
        "n_bootstrap": n_bootstrap,
        "consensus_available": cons["available"],
        "consensus_source": cons["source"],
        "consensus_run_id": cons["consensus_run_id"],
        "consensus_file_sha256": cons.get("sha256"),
        "consensus_note": (CONSENSUS_NOTE if not cons["available"]
                           else cons.get("note")),
        "b_clip": diag["b_clip"],
        "stability": stability,
        "gating": r["gating"],
        "baseline_coverage_summary": {
            "expected_points": r["baseline_coverage"]["expected_points"],
            "mean_actual_points": r["baseline_coverage"]["mean_actual_points"],
            "n_truncated": r["baseline_coverage"]["n_truncated"],
        },
    }

    root_payload = dict(common)
    root_payload.update({
        "n_nodes": len(gvars),
        "n_stable_edges": len(r["stable"]),
        "n_consensus": r["n_consensus"],
        "n_conflict": r["n_conflict"],
        "n_clipped_edges": diag["n_clipped_edges"],
        "cond_IminusB": diag["cond_IminusB"],
        "cond_IminusB_after_clip": diag["cond_IminusB_after_clip"],
        "clipped_edges": diag["clipped_edges"],
        "root_candidates": [_root_entry(c) for c in roots],
        "marker_info": {
            "variable": r["marker"],
            "role": "injection_source",
            "rank": None,
            "note": "故障指示器遮蔽，不参与根因排名，仅作传输入口展示",
        },
        "method_note": ("排序键=总因果效应 T=(I-B)^-1 对症状端(marker+terminal)的"
                        "|效应| 之和；次级 in_degree ASC/weighted_reach DESC。"
                        + (" " + CONSENSUS_NOTE if not cons["available"] else "")),
    })
    artifact.write_artifact(f"root_candidates_{k}_{g}", root_payload, "tracing",
                            run_id=run_id, accident_type=k, group=g, source=sig_src)

    topo_payload = dict(common)
    topo_payload.update({
        "variables": r["variables"],
        "nodes": r["nodes"],
        "n_nodes": len(gvars),
        "n_stable_edges": len(r["stable"]),
        "note": "拓扑指标只读自本 run 的签名稳定边；depth 用于图布局",
    })
    artifact.write_artifact(f"topology_{k}_{g}", topo_payload, "tracing",
                            run_id=run_id, accident_type=k, group=g, source=sig_src)

    sens = ST.clip_sensitivity(r["topology"], r["marker"], r["stable"], r["variables"])
    sens_payload = dict(common)
    sens_payload.update(sens)
    artifact.write_artifact(f"clip_sensitivity_{k}_{g}", sens_payload, "tracing",
                            run_id=run_id, accident_type=k, group=g, source=sig_src)

    return {
        "k": k, "g": g, "n_stable_edges": len(r["stable"]),
        "n_clipped_edges": diag["n_clipped_edges"],
        "cond_IminusB": diag["cond_IminusB"],
        "cond_IminusB_after_clip": diag["cond_IminusB_after_clip"],
        "top5": [c["variable"] for c in roots],
        "top1": roots[0]["variable"] if roots else None,
        "n_consensus": r["n_consensus"],
        "top1_stable_clip": sens["top1_stable"],
        "min_rank_overlap_at_5": sens["min_rank_overlap_at_5"],
        "clip_top1": "/".join(str(v["top1"]) for v in sens["variants"]),
        "clip_top5_overlap": "/".join(str(v["rank_overlap_at_5"]) for v in sens["variants"]),
        "stability_label": stability["label"],
        "stability_degraded": stability["degraded"],
        "clip_ratio": stability["clip_ratio"],
        "coverage_ratio": r["baseline_coverage"]["mean_coverage_ratio"],
    }


def repro_check(k: str, g: str, gvars: Sequence[str], inputs: Dict,
                n_bootstrap: int, cons: Dict, first: Dict) -> Dict:
    """C7：同参数重算一次，比对排名与稳定边数（GLOBAL_SEED 固定应逐项一致）。"""
    r = compute_template(k, g, gvars, inputs, n_bootstrap, cons)
    top5 = [c["variable"] for c in r["roots"]]
    same = (top5 == first["top5"] and len(r["stable"]) == first["n_stable_edges"])
    return {"template": f"{k}_{g}", "same": bool(same),
            "n_stable_edges": [first["n_stable_edges"], len(r["stable"])],
            "top5": [first["top5"], top5]}


# ==================== 缺数据 / 部分数据 ====================
def smoke_partial(code: str, inputs: Dict) -> Dict:
    """PARTIAL 类只允许单模板 smoke：单次拟合、描述性、不写签名/榜单。"""
    cfg = get_accident(code)
    marker = cfg["marker"]
    sevs = D.list_class_severities(code)
    if not sevs:
        return {"code": code, "status": "partial", "partial": True,
                "smoke": None, "reason": "无可用 CSV"}
    cols = list(D.load_trajectory(code, sevs[0]).columns)
    cand = [c for c in (list(PHYSICAL_CANDIDATES) + list(SAFETY_STEP_VARS) + [marker])
            if c in cols]
    cand = list(dict.fromkeys(cand))
    blocks, t_last, med = [], {}, []
    for sev in sevs:
        df = D.load_trajectory(code, sev)
        t_last[sev] = float(df["TIME"].iloc[-1])
        sub = D.window_slice(df, LEARN_WINDOW_START, LEARN_WINDOW_END)
        if len(sub) < 10:
            continue
        blocks.append(sub[cand].to_numpy(dtype=float))
        med.append(float(sub[marker].median()))
    if not blocks:
        return {"code": code, "status": "partial", "partial": True,
                "smoke": None, "reason": "学图窗点数不足"}
    pooled = np.vstack(blocks)
    sd = pooled.std(axis=0)
    gvars = [v for i, v in enumerate(cand) if sd[i] >= CONSTANT_SIGMA]
    keep = [cand.index(v) for v in gvars]
    blocks = [b[:, keep] for b in blocks]
    cov = _baseline_coverage(code, sevs, t_last)
    try:
        ontology = build_ontology(code, gvars)
        sig = fit_single_template(
            blocks, gvars, marker, ontology,
            inputs["baseline"]["mu"], inputs["baseline"]["sigma"],
            float(np.median(med)) if med else 1.0,
            use_ontology=True,
            extra_meta={"partial": True, "n_bootstrap": 1,
                        "baseline_coverage": cov})
        smoke = {
            "n_trajectories": len(blocks),
            "n_vars": len(gvars),
            "variables": gvars,
            "n_stable_edges": len(sig["stable_edges"]),
            "n_bootstrap": 1,
            "engine": "lingam",
            "baseline_coverage": {k2: cov[k2] for k2 in
                                  ("expected_points", "mean_actual_points",
                                   "n_truncated")},
            "note": "单模板 smoke：仅描述性验证加载/学图链路，不进 36 份验收，"
                    "不生成签名与根因榜产物",
        }
        return {"code": code, "status": "partial", "partial": True,
                "smoke": smoke, "reason": cfg["notes"]}
    except Exception as exc:  # 数据不足时如实记录，不生成占位结果
        return {"code": code, "status": "partial", "partial": True,
                "smoke": None, "reason": f"smoke 失败: {exc!r}"}


def skipped_entry(code: str) -> Dict:
    cfg = get_accident(code)
    return {"code": code, "status": cfg["status"], "partial": False,
            "reason": f"注册表 status={cfg['status']}；{cfg['notes']}",
            "data_dir": cfg["data_dir"]}


# ==================== 主流程 ====================
def main(argv: Optional[Sequence[str]] = None) -> str:
    ap = argparse.ArgumentParser(
        description="v2.3 C 阶段：同源签名 + 溯源（单 run_id）")
    ap.add_argument("--classes", default=",".join(ALL_ACCIDENT_TYPES),
                    help="逗号分隔，默认全部 12 类（按注册表状态分流）")
    ap.add_argument("--groups", default=",".join(GROUP_ORDER))
    ap.add_argument("--n-bootstrap", type=int, default=LINGAM_N_BOOTSTRAP)
    ap.add_argument("--with-gam-consensus", action="store_true",
                    help="接入 v2.1 遗留 GAM 共识文件（默认单引擎，共识字段为 null）")
    ap.add_argument("--no-partial-smoke", action="store_true")
    ap.add_argument("--no-manifest", action="store_true")
    ap.add_argument("--no-repro-check", action="store_true")
    args = ap.parse_args(argv)

    classes = [c.strip().upper() for c in args.classes.split(",") if c.strip()]
    groups = [g.strip() for g in args.groups.split(",") if g.strip()]
    unknown = [c for c in classes if c not in ALL_ACCIDENT_TYPES]
    if unknown:
        ap.error(f"未登记的事故类别: {unknown}")

    inputs = load_inputs()
    run_id = artifact.new_run_id("v23c")
    logger.info(f"[v23c] run_id={run_id} n_bootstrap={args.n_bootstrap} "
                f"classes={classes} groups={groups} "
                f"single_engine={not args.with_gam_consensus}")

    ready = [c for c in classes if c in READY_CODES]
    partial = [c for c in classes if c in PARTIAL_CODES]
    missing = [c for c in classes if c in MISSING_CODES]

    results: List[Dict] = []
    cons_cache: Dict[str, Dict] = {}
    for k in ready:
        gvars = inputs["selected_vars"].get(k)
        if not gvars:
            raise KeyError(f"{k} 缺自动选取变量集（variable_selection_summary）")
        for g in groups:
            logger.info(f"[v23c] {k}/{g}: {len(gvars)} 变量 × {args.n_bootstrap} bootstrap")
            cons = load_consensus(k, g, run_id, args.with_gam_consensus)
            cons_cache[f"{k}_{g}"] = cons
            r = run_template(k, g, gvars, inputs, args.n_bootstrap, run_id, cons)
            logger.info(f"  n_stable_edges={r['n_stable_edges']} "
                        f"n_clipped={r['n_clipped_edges']} "
                        f"cond={r['cond_IminusB']} top1={r['top1']}")
            results.append(r)

    skipped: List[Dict] = [skipped_entry(c) for c in missing]
    if not args.no_partial_smoke:
        for c in partial:
            entry = smoke_partial(c, inputs)
            if entry.get("smoke"):
                logger.info(f"[v23c] {c} partial smoke: "
                            f"{entry['smoke']['n_vars']} 变量, "
                            f"{entry['smoke']['n_stable_edges']} 稳定边（不落签名）")
            else:
                logger.warning(f"[v23c] {c} partial smoke 未完成: {entry['reason']}")
            skipped.append(entry)
    else:
        skipped += [{"code": c, "status": "partial", "partial": True,
                     "reason": "本 run 跳过 partial smoke"} for c in partial]

    # 注册表全量如实登记：即使本次 --classes 只选 ready 类，partial/missing 也要出现，
    # 避免 skipped_classes.json 因选择范围而“看起来没有缺数据类”。
    recorded = {e.get("code") for e in skipped}
    for c in list(PARTIAL_CODES) + list(MISSING_CODES):
        if c in recorded:
            continue
        skipped.append({"code": c,
                        "status": "partial" if c in PARTIAL_CODES else "data_missing",
                        "partial": c in PARTIAL_CODES,
                        "reason": "本 run 未选择该类（--classes 仅含 ready 类）"})

    artifact.write_artifact(
        "skipped_classes",
        {"entries": skipped,
         "n_ready": len(READY_CODES), "n_partial": len(PARTIAL_CODES),
         "n_missing": len(MISSING_CODES),
         "note": "partial 只做单模板 smoke（不生成签名/榜单）；data_missing 直接跳过，"
                 "禁止占位结果"},
        "tracing", run_id=run_id, source="rca/v21/accident_config.py")

    # ---- C7 复现性（同参数重算首个模板）----
    repro = None
    if results and not args.no_repro_check:
        first = results[0]
        repro = repro_check(first["k"], first["g"], inputs["selected_vars"][first["k"]],
                            inputs, args.n_bootstrap,
                            cons_cache[f"{first['k']}_{first['g']}"], first)
        logger.info(f"[REPRO] {repro}")

    # ---- 运行摘要（不落盘，落 tracing/tracing_report_v23.txt）----
    lines = [f"NPPAD-CausalTrace v2.3 C 阶段（run_id={run_id}）",
             f"n_bootstrap={args.n_bootstrap} | 引擎=lingam（单引擎）| "
             f"consensus_available={bool(args.with_gam_consensus)}",
             "", f"{'模板':<14}{'稳定边':>7}{'截断边':>7}{'截断比':>8}{'cond截断后':>12}"
                 f"{'Top1':>8}{'Top5重叠':>9}{'Top1(3/5/10)':>18}"
                 f"{'Top5∩(3/5/10)':>16}  {'稳定性':<8}"]
    for r in results:
        lines.append(f"{r['k'] + '_' + r['g']:<14}{r['n_stable_edges']:>7}"
                     f"{r['n_clipped_edges']:>7}{r['clip_ratio']:>8.3f}"
                     f"{r['cond_IminusB_after_clip']:>12.2f}"
                     f"{str(r['top1']):>8}{r['min_rank_overlap_at_5']:>9}"
                     f"{r['clip_top1']:>18}{r['clip_top5_overlap']:>16}"
                     f"  {r['stability_label']:<8}")
    n_degraded = sum(1 for r in results if r["stability_degraded"])
    lines.append(f"稳定性门控（V23-ALGO-001）：cond_after_clip>1e4 或 截断比>0.35 触发降级；"
                 f"本批降级 {n_degraded}/{len(results)} 个模板；B_CLIP=5 不变")
    lines.append("")
    for r in results:
        lines.append(f"[REPRO] {r['k']}_{r['g']} n_stable_edges={r['n_stable_edges']} "
                     f"top5={','.join(r['top5'])}")
    lines.append("")
    lines.append(f"partial={partial}：仅 smoke，不生成签名/榜单")
    lines.append(f"data_missing={missing}：已写 tracing/skipped_classes.json")
    if repro is not None:
        lines.append("REPRO_OK" if repro["same"] else f"REPRO_FAIL {repro}")
    artifact.write_text_artifact("tracing_report_v23", "\n".join(lines) + "\n",
                                 "tracing", run_id=run_id, source="tracing/root_candidates_*")

    if not args.no_manifest:
        artifact.build_manifest(run_id)

    print("\n".join(lines))
    logger.info(f"[v23c] done run_id={run_id}")
    return run_id


if __name__ == "__main__":
    main()

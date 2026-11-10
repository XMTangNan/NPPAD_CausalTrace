"""V2.3 D10：全链编排（同 run_id、产物同源、manifest 可核查）。

顺序（必需步骤任一失败即停止，失败写入 summary 并退出码 1）：
  1 variable_sets  A1 注册表 + 四阶段变量选取（含论文锚定，同 run_id）
  2 audit         B 数据审计 / Z 病态 / 变点
  3 tolerances    D2 公差标定 rca.v21.run_v23_tolerances.run_all(run_id=...)
  4 end2end       C 阶段 签名 + 溯源（单模板 causal 计算）
  5 graphs        C3 图件（只读签名/溯源）
  6 aggregation   C3b/C3c 汇总 + 差异（run_id 强校验）
  7 deviation     D9 偏离度 4 级着色
  8 explanation_reports  D6 解释 + D7 报告（9 份，读上面的产物）
  9 evaluation     D11 留出集变量级评测（事故内变量命中 + 纯偏差基线，只读签名/溯源）
 10 manifest      全量产物 sha256（manifest.json）
最后写 reports/run_v23_all_summary.json：各步状态/耗时/产物计数/跳过类/失败项。

A/B（auto vs v2.1 变量集）已从主链摘出（V23-DESIGN-019）：
  - 默认不跑；需要时显式 `--with-ab`（追加为第 9 步，manifest 前）；
  - 既有 A/B 结果冻结为一次性消融产物，归档 <run 根>/ablations/
    （frozen_ab_variable_selection_<run_id>.json），论文引用该冻结件，不再每轮重算。

说明：
  - run_id 由本编排器先行生成，再注入 run_v23_end2end（它内部调用
    artifact.new_run_id），保证全链单一 run_id；若注入后返回值不同则立刻报错；
  - 每一步都 try/except 记录 status=ok|skipped|failed 与异常文本，绝不假装全部通过；
  - --dry-run 只打印步骤计划，不落盘、不改 mtime。

用法（cd Code）：
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_all
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_all --dry-run
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_all --skip-llm --n-bootstrap 20
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_all --with-ab
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from typing import Dict, List, Optional

from causalrca.io.logging import logger
from causalrca.io import artifact
from causalrca.reporting import llm_explanation as LE
from causalrca.reporting import report_template as RT
from causalrca.config.accident_config import READY_CODES, PARTIAL_CODES, REGISTRY
from causalrca.config.constants_v23 import N_BOOTSTRAP_PROD, RUN_MODE_TEST, run_mode_for

GROUPS: List[str] = ["low", "mid", "high"]

STEPS: List[Dict[str, str]] = [
    {"name": "variable_sets", "layer": "A1",
     "desc": "注册表 + 四阶段变量选取（run_variable_selector，同 run_id）"},
    {"name": "audit", "layer": "B",
     "desc": "数据审计 / Z 病态 / 变点（data_audit.run_all，同 run_id）"},
    {"name": "tolerances", "layer": "D2",
     "desc": "公差标定 run_v23_tolerances.run_all(run_id=...)"},
    {"name": "end2end", "layer": "C",
     "desc": "签名 + 溯源（run_v23_end2end，单模板因果计算）"},
    {"name": "graphs", "layer": "C3",
     "desc": "图件生成（run_v23_graphs，只读签名/溯源）"},
    {"name": "aggregation", "layer": "C3b/C3c",
     "desc": "跨组汇总 + 差异（severity_aggregation，run_id 强校验）"},
    {"name": "deviation", "layer": "D9",
     "desc": "偏离度 4 级着色（deviation.run_all_deviations，同 run_id）"},
    {"name": "explanation_reports", "layer": "D6/D7",
     "desc": "根因解释（rule 离线）+ 9 份报告"},
    {"name": "evaluation", "layer": "D11",
     "desc": "留出集变量级评测（事故内变量命中 + 纯偏差基线；只读签名/溯源/真值表）"},
    {"name": "manifest", "layer": "C7",
     "desc": "manifest.json 全量产物 sha256 + lineage"},
]

# A/B 一次性消融（V23-DESIGN-019）：不在主链，仅 --with-ab 时插入 manifest 之前
AB_STEP: Dict[str, str] = {
    "name": "ab_variable_selection", "layer": "A/B(消融)",
    "desc": "auto vs v2.1 变量集对照（run_ab_variable_selection，同 run_id；默认不跑）",
}


# ==================== 单步执行 ====================
def _run_step(index: int, step: Dict, fn, results: List[Dict], total: int) -> Dict:
    """执行单步并记录 status/elapsed_s/detail/error，异常不逃逸。"""
    t0 = time.perf_counter()
    entry = {"index": index, "name": step["name"], "layer": step["layer"],
             "description": step["desc"], "status": "ok", "elapsed_s": 0.0,
             "detail": None, "error": None}
    logger.info(f"[D10] ({index}/{total}) {step['name']} - {step['desc']}")
    try:
        entry["detail"] = fn()
    except Exception as exc:  # noqa: BLE001 - 失败必须落盘，不吞
        entry["status"] = "failed"
        entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["traceback"] = traceback.format_exc(limit=6)
        logger.error(f"[D10] {step['name']} 失败：{entry['error']}")
    entry["elapsed_s"] = round(time.perf_counter() - t0, 3)
    results.append(entry)
    return entry


def _artifact_counts() -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for sub, d in artifact.subdirs().items():
        counts[sub] = len(list(d.glob("*"))) if d.exists() else 0
    return counts


def _skipped_classes() -> List[Dict]:
    """读 tracing/skipped_classes.json（end2end 写），失败不影响 summary。"""
    path = artifact.subdir_path("tracing") / "skipped_classes.json"
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return [{"code": e.get("code"), "status": e.get("status"),
                 "reason": e.get("reason")} for e in doc.get("entries") or []]
    except Exception as exc:  # noqa: BLE001
        return [{"code": None, "status": "unreadable", "reason": str(exc)}]


# ==================== 具体步骤实现 ====================
def _same_run_id(fn, run_id: str):
    """把执行器内部的 new_run_id 固定为本链 run_id（同源，禁止另起批次号）。"""
    orig = artifact.new_run_id
    artifact.new_run_id = lambda prefix="v23": run_id  # noqa: ARG005
    try:
        return fn()
    finally:
        artifact.new_run_id = orig


def step_variable_sets(run_id: str):
    """A1：注册表快照 + 四阶段变量选取；与下游同 run_id。"""
    from causalrca.cli.run_variable_selector import main as vs_main
    _same_run_id(vs_main, run_id)
    return "registry + variable_sets 完成"


def step_audit(run_id: str, classes: List[str]):
    """B4/B5/B6：数据审计；LOCAC 一并审计（与 v2.3 批次同范围）。"""
    from causalrca.data import audit as DA
    res = DA.run_all(codes=list(classes) + list(PARTIAL_CODES), run_id=run_id)
    return {"n_audited": res.get("n_audited"), "n_skipped": res.get("n_skipped")}


def step_tolerances(run_id: str, classes: List[str]):
    """D2 公差执行器（另一 agent 提供）：未就绪时明确报错，不静默跳过。

    优先 run_all(run_id=...)；若模块只提供 run_classes(classes, run_id)，
    则以同一 run_id 直接调用（不另起 run_id，保证同源）。
    """
    try:
        from causalrca.cli import run_v23_tolerances as TOL
    except ImportError as exc:
        raise RuntimeError(
            "D2 公差执行器未就绪：无法导入 rca.v21.run_v23_tolerances"
        ) from exc
    run_all = getattr(TOL, "run_all", None)
    if callable(run_all):
        run_all(run_id=run_id)
        return "run_v23_tolerances.run_all(run_id=...) 完成"
    run_classes = getattr(TOL, "run_classes", None)
    if callable(run_classes):
        TOL.run_classes(classes, run_id)
        return ("run_v23_tolerances.run_classes(classes, run_id) 完成"
                "（模块未提供 run_all，按同 run_id 适配调用）")
    raise RuntimeError("D2 公差执行器未就绪：run_v23_tolerances 无 run_all/run_classes")


def step_end2end(run_id: str, classes: List[str], groups: List[str],
                 n_bootstrap: int):
    """注入本链 run_id 后调用 end2end（不改其源码），并断言返回 run_id 一致。"""
    from causalrca.cli.run_v23_end2end import main as end2end_main
    orig = artifact.new_run_id
    artifact.new_run_id = lambda prefix="v23": run_id  # noqa: ARG005
    try:
        got = end2end_main(["--classes", ",".join(classes),
                            "--groups", ",".join(groups),
                            "--n-bootstrap", str(n_bootstrap),
                            "--no-manifest"])
    finally:
        artifact.new_run_id = orig
    if got != run_id:
        raise RuntimeError(f"end2end 返回 run_id={got} 与编排 run_id={run_id} 不一致")
    return f"run_id={got} n_bootstrap={n_bootstrap}"


def step_graphs(run_id: str, classes: List[str], groups: List[str]):
    from causalrca.reporting.run_v23_graphs import main as graphs_main
    graphs_main(["--run-id", run_id,
                 "--classes", ",".join(classes),
                 "--groups", ",".join(groups),
                 "--no-manifest"])
    return "图件完成"


def step_aggregation(run_id: str, classes: List[str], groups: List[str]):
    from causalrca.causal.severity_aggregation import main as aggregation_main
    aggregation_main(["--run-id", run_id,
                      "--classes", ",".join(classes),
                      "--groups", ",".join(groups),
                      "--no-manifest"])
    return "summary/diff 完成"


def step_deviation(run_id: str, classes: List[str], groups: List[str]) -> Dict:
    from causalrca.evidence.deviation import run_all_deviations
    res = run_all_deviations(run_id, classes=classes, groups=groups)
    return {"n_groups": len(res)}


def step_explanation_reports(run_id: str, classes: List[str], groups: List[str],
                             backend: str) -> Dict:
    """D6 解释 + D7 报告：模板级固定 rule；类级汇总 ×N + 全局结论 ×1（V23-DESIGN-020）。"""
    from causalrca.cli.run_v23_llm_explanation import (
        explain_one, explain_class, explain_global,
    )
    expl: List[Dict] = []
    exploerr: List[str] = []
    for k in classes:
        for g in groups:
            try:
                expl.append(explain_one(k, g, backend="rule"))
            except Exception as exc:  # noqa: BLE001
                exploerr.append(f"{k}_{g}: {type(exc).__name__}: {exc}")
    summaries: List[Dict] = []
    for k in classes:
        try:
            summaries.append(explain_class(k, backend=backend))
        except Exception as exc:  # noqa: BLE001
            exploerr.append(f"{k}(class): {type(exc).__name__}: {exc}")
    try:
        summaries.append(explain_global(classes, backend=backend))
    except Exception as exc:  # noqa: BLE001
        exploerr.append(f"global: {type(exc).__name__}: {exc}")
    try:
        reports = RT.run_all_reports(classes=classes, groups=groups, run_id=run_id)
        rep_err: List[str] = []
    except Exception as exc:  # noqa: BLE001
        reports, rep_err = [], [f"{type(exc).__name__}: {exc}"]
    if exploerr or rep_err:
        raise RuntimeError(f"解释/报告未全成功：explanations={exploerr} reports={rep_err}")
    return {"template_backend": "rule",
            "n_explanations": len(expl),
            "n_templates_degraded": sum(1 for e in expl if e["degraded"]),
            "n_unfaithful": sum(1 for e in expl if not e["faithful"]),
            "n_summaries": len(summaries),
            "n_summary_cache_hits": sum(1 for s in summaries if s["cache_hit"]),
            "n_summary_degraded": sum(1 for s in summaries if s["degraded"]),
            "summary_backend_requested": backend,
            "n_reports": len(reports)}


def step_evaluation(run_id: str, classes: List[str], groups: List[str]) -> Dict:
    """D11：留出集变量级评测（只读 split_manifest/topology/signature，不重算因果）。"""
    from causalrca.reporting import evaluation as EV
    res = EV.evaluate(run_id, classes=classes, groups=groups)
    g = res["summary"]["global"]
    return {"n_traces": res["n_traces"], "n_files": len(res["files"]),
            "granularity": "variable_level_in_accident",
            "causal_hit1": g["causal"]["hit1"],
            "unmasked_hit1": g["causal_unmasked_for_eval"]["hit1"],
            "baseline_hit1": g["deviation_equal_weight"]["hit1"]}


def step_ab_variable_selection(run_id: str):
    """A/B 对照：auto vs v2.1 变量集，依赖 A1 已落盘的变量集，同一 run_id。"""
    from causalrca.cli.run_ab_variable_selection import main as ab_main
    _same_run_id(ab_main, run_id)
    return "A/B 对照完成"


def step_manifest(run_id: str):
    path = artifact.build_manifest(run_id)
    return str(path)


def _plan_steps(with_ab: bool) -> List[Dict]:
    """主链 10 步；--with-ab 时在 manifest 前插入一次性 A/B 消融。"""
    steps = list(STEPS)
    if with_ab:
        steps.insert(len(steps) - 1, AB_STEP)
    return steps


def _step_fns(run_id: str, classes: List[str], groups: List[str],
              n_bootstrap: int, backend: str) -> Dict[str, object]:
    """步骤名 → 执行函数（参数已绑定，含本链 run_id）。"""
    return {
        "variable_sets": lambda: step_variable_sets(run_id),
        "audit": lambda: step_audit(run_id, classes),
        "tolerances": lambda: step_tolerances(run_id, classes),
        "end2end": lambda: step_end2end(run_id, classes, groups, n_bootstrap),
        "graphs": lambda: step_graphs(run_id, classes, groups),
        "aggregation": lambda: step_aggregation(run_id, classes, groups),
        "deviation": lambda: step_deviation(run_id, classes, groups),
        "explanation_reports": lambda: step_explanation_reports(run_id, classes, groups, backend),
        "evaluation": lambda: step_evaluation(run_id, classes, groups),
        "ab_variable_selection": lambda: step_ab_variable_selection(run_id),
        "manifest": lambda: step_manifest(run_id),
    }


# ==================== 主流程 ====================
def _print_plan(steps: List[Dict], classes: List[str], groups: List[str],
                n_bootstrap: int, backend: str) -> None:
    print("=" * 78)
    print("CausalTrace v2.3 全链计划（dry-run，不落盘）")
    print(f"  classes={classes} groups={groups} n_bootstrap={n_bootstrap} "
          f"explanation_backend={backend}")
    print("=" * 78)
    for i, step in enumerate(steps, start=1):
        print(f"[{i}/{len(steps)}] {step['layer']:<9} {step['name']:<20} {step['desc']}")
    print("=" * 78)
    print(f"共 {len(steps)} 个步骤；任一步失败即停止并写 reports/run_v23_all_summary.json")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="v2.3 全链编排（单一 run_id）")
    ap.add_argument("--n-bootstrap", type=int, default=N_BOOTSTRAP_PROD,
                    help=f"LiNGAM bootstrap 次数；生产档固定 {N_BOOTSTRAP_PROD}（方案 §6.4），"
                         f"测试/评测档用 N_BOOTSTRAP_TEST=5 且产物标 run_mode=test")
    ap.add_argument("--classes", nargs="+", default=None,
                    help=f"事故类型，默认 READY_CODES={READY_CODES}")
    ap.add_argument("--groups", nargs="+", default=None,
                    help=f"严重度组，默认 {GROUPS}")
    ap.add_argument("--skip-llm", action="store_true",
                    help="解释只用 rule 离线后端（等价于 --backend rule）")
    ap.add_argument("--dry-run", action="store_true", help="只打印步骤计划，不写盘")
    ap.add_argument("--with-ab", action="store_true",
                    help="追加一次性 A/B 消融（auto vs v2.1 变量集）；默认不跑")
    ap.add_argument("--rerun-reason", default=None,
                    help="全链重跑原因（方案 §6.4 强制）：必须命中 E-R1/E-R2/E-R3/E-R4 并写清具体项")
    args = ap.parse_args(argv)

    classes = list(args.classes or READY_CODES)
    unknown = [k for k in classes if k not in REGISTRY]
    if unknown:
        raise SystemExit(f"未知 accident_type：{unknown}")
    groups = list(args.groups or GROUPS)
    backend = "rule"  # 默认离线；llm 后端由 run_v23_llm_explanation --backend llm 显式启用
    llm_note = ("--skip-llm：解释后端固定 rule（离线）" if args.skip_llm
                else f"解释后端 rule（离线）；llm 可用性："
                     f"{LE.llm_unavailable_reason() or '环境变量齐全，可另行 --backend llm'}")

    if args.dry_run:
        _print_plan(_plan_steps(args.with_ab), classes, groups,
                    args.n_bootstrap, backend)
        print(f"  {llm_note}")
        print(f"  rerun_reason={args.rerun_reason or '（dry-run 不校验）'}")
        return 0

    if not args.rerun_reason:
        raise SystemExit(
            "全链重跑必须显式声明 --rerun-reason（方案 §6.4 强制）："
            "必须命中 E-R1 出对外数字 / E-R2 影响数值 / E-R3 产物结构或契约 / "
            "E-R4 跨阶段对不上，并写清具体项。未声明原因的重跑视为违规，产物不得引用。")

    run_id = artifact.new_run_id("v23")
    run_mode = run_mode_for(args.n_bootstrap)
    if run_mode == RUN_MODE_TEST:
        logger.warning(f"[D10] run_mode=test（n_bootstrap={args.n_bootstrap} < {N_BOOTSTRAP_PROD}）："
                       f"本 run 产物仅用于测试/评测回归，不得引用为论文数字")
    logger.info(f"[D10] run_id={run_id} classes={classes} groups={groups} "
                f"n_bootstrap={args.n_bootstrap} run_mode={run_mode} backend={backend} "
                f"with_ab={args.with_ab}")

    steps = _plan_steps(args.with_ab)
    fns = _step_fns(run_id, classes, groups, args.n_bootstrap, backend)
    results: List[Dict] = []
    for i, step in enumerate(steps, start=1):
        entry = _run_step(i, step, fns[step["name"]], results, len(steps))
        if entry["status"] != "ok":
            break

    failed = [f"{e['name']}: {e['error']}" for e in results
              if e["status"] == "failed"]
    summary = {
        "run_id": run_id,
        "parameters": {"classes": classes, "groups": groups,
                       "n_bootstrap": args.n_bootstrap,
                       "run_mode": run_mode,
                       "citable": run_mode != RUN_MODE_TEST,
                       "explanation_backend": backend,
                       "note": llm_note},
        "steps": results,
        "n_steps_planned": len(steps),
        "with_ab": args.with_ab,
        "rerun_reason": args.rerun_reason,
        "n_steps_done": len(results),
        "n_steps_ok": sum(1 for e in results if e["status"] == "ok"),
        "n_steps_failed": len(failed),
        "failed": failed,
        "artifact_counts": _artifact_counts(),
        "skipped_classes": _skipped_classes(),
        "llm_available": LE.llm_unavailable_reason() is None,
        "llm_unavailable_reason": LE.llm_unavailable_reason(),
    }

    manifest_ok = any(e["name"] == "manifest" and e["status"] == "ok" for e in results)
    summary_path = artifact.write_artifact("run_v23_all_summary", summary, "reports",
                                           run_id=run_id, source="causalrca/cli/run_v23_all.py")
    if manifest_ok:  # summary 落盘后刷新 manifest，使其纳入本次 summary
        artifact.build_manifest(run_id)

    print("=" * 78)
    print(f"[D10] run_id={run_id} steps={summary['n_steps_ok']}/{len(steps)} ok "
          f"failed={summary['n_steps_failed']}")
    for e in results:
        print(f"  - {e['index']}. {e['name']:<12} {e['status']:<8} "
              f"{e['elapsed_s']:>7.2f}s {e['error'] or ''}")
    print(f"[D10] summary -> {summary_path}")
    print("=" * 78)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

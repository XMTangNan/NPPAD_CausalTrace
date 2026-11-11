"""V2.3 D6 执行器：为 9 个模板生成“可核查、不编造”的根因解释。

特性：
  - 默认 backend=rule，纯离线（不发网络请求、不读 API key），确定性；
  - backend=llm 仅当 CAUSALTRACE_LLM_BASE_URL 与 CAUSALTRACE_LLM_API_KEY 同时存在时启用，
    失败自动降级 rule 并写 degraded_reason；
  - 只读本 run 的 {tracing,signatures,reports} 既有产物，不重算因果；
  - 产物 reports/llm_explanation_{K}_{g}.json，含 backend/degraded_reason/text/
    faithfulness/inputs(sha256)/run_id。

用法（cd Code）：
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_llm_explanation
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_llm_explanation --backend llm
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional

from causalrca.io.logging import logger
from causalrca.io import artifact
from causalrca.reporting import llm_explanation as LE
from causalrca.reporting import explanation_verdict as EV
from causalrca.config.accident_config import READY_CODES, REGISTRY

GROUPS: List[str] = ["low", "mid", "high"]


def explain_one(k: str, g: str, backend: str = "rule") -> Dict:
    """生成单个模板的解释并落盘，返回落盘摘要。"""
    payload = LE.build_payload(k, g)
    res = LE.generate_explanation(payload, backend=backend)
    faith = res["faithfulness"]
    doc = {
        "template": f"{k}_{g}",
        "marker": payload["marker"],
        "requested_backend": res["requested_backend"],
        "backend": res["backend"],
        "degraded": res["degraded"],
        "degraded_reason": res["degraded_reason"],
        "text": res["text"],
        "faithfulness": faith,
        "inputs": payload["inputs"],
        "payload": payload,
    }
    path = artifact.write_artifact(
        f"llm_explanation_{k}_{g}", doc, "reports", run_id=payload["run_id"],
        accident_type=k, group=g,
        source=(f"tracing/root_candidates_{k}_{g}.json;"
                f"signatures/signature_{k}_{g}.json;"
                f"reports/summary_{k}.json;reports/diff_{k}.json"))
    # V23-DESIGN-062：判定式解释为可开关附加产物，默认关闭；失败不影响规则稿
    if EV.verdict_enabled():
        try:
            verdict = EV.run_template_verdict(payload["run_id"], k, g)
            vpath = EV.write_verdict(verdict, k, g)
            logger.info(f"[D6] 判定式解释已落盘：{vpath}（mode={verdict['explanation_mode']}）")
        except Exception as exc:                              # noqa: BLE001 - 附加路径不得中断主链
            logger.warning(f"[D6] 判定式解释失败，保留规则稿：{type(exc).__name__}: {exc}")
    return {
        "template": f"{k}_{g}",
        "path": str(path),
        "run_id": payload["run_id"],
        "backend": res["backend"],
        "degraded": res["degraded"],
        "degraded_reason": res["degraded_reason"],
        "faithful": faith["faithful"],
        "n_numbers_unsupported": faith["n_numbers_unsupported"],
        "n_vars_unknown": faith["n_vars_unknown"],
        "top1": (payload["root_candidates"][0]["variable"]
                 if payload["root_candidates"] else None),
    }


def explain_class(k: str, backend: str = "rule",
                  run_id: Optional[str] = None) -> Dict:
    """类级汇总解释（每类 1 份，可走 llm；失败降级 rule，带同 run_id+哈希缓存）。"""
    payload = LE.build_class_payload(k, run_id=run_id)
    name = LE.CLASS_SUMMARY_NAME.format(k=k)
    res = LE.generate_summary(name, payload, LE.render_rule_class_summary,
                              "class", backend=backend)
    doc = {
        "scope": "class",
        "accident_type": k,
        "template_ids": payload["template_ids"],
        "requested_backend": backend,
        "backend": res["backend"],
        "degraded": res["degraded"],
        "degraded_reason": res["degraded_reason"],
        "input_sha256": res["input_sha256"],
        "cache_hit": res["cache_hit"],
        "text": res["text"],
        "faithfulness": res["faithfulness"],
        "inputs": payload["inputs"],
        "payload": payload,
    }
    path = artifact.write_artifact(
        name, doc, "reports", run_id=payload["run_id"], accident_type=k,
        source="reports/llm_explanation_{K}_{g}.json 聚合（low/mid/high）")
    return {"scope": "class", "template": k, "path": str(path),
            "run_id": payload["run_id"], "backend": res["backend"],
            "degraded": res["degraded"], "cache_hit": res["cache_hit"],
            "faithful": res["faithfulness"]["faithful"],
            "n_template_refs": res["faithfulness"].get("n_template_refs")}


def explain_global(classes: List[str], backend: str = "rule",
                   run_id: Optional[str] = None) -> Dict:
    """全局结论解释（1 份，跨类对比）。"""
    payload = LE.build_global_payload(classes, run_id=run_id)
    res = LE.generate_summary(LE.GLOBAL_SUMMARY_NAME, payload,
                              LE.render_rule_global_summary, "global",
                              backend=backend)
    doc = {
        "scope": "global",
        "classes": list(classes),
        "template_ids": payload["template_ids"],
        "requested_backend": backend,
        "backend": res["backend"],
        "degraded": res["degraded"],
        "degraded_reason": res["degraded_reason"],
        "input_sha256": res["input_sha256"],
        "cache_hit": res["cache_hit"],
        "text": res["text"],
        "faithfulness": res["faithfulness"],
        "inputs": payload["inputs"],
        "payload": payload,
    }
    path = artifact.write_artifact(
        LE.GLOBAL_SUMMARY_NAME, doc, "reports", run_id=payload["run_id"],
        source="reports/llm_explanation_class_*.json 聚合（跨类）")
    return {"scope": "global", "template": "ALL", "path": str(path),
            "run_id": payload["run_id"], "backend": res["backend"],
            "degraded": res["degraded"], "cache_hit": res["cache_hit"],
            "faithful": res["faithfulness"]["faithful"],
            "n_template_refs": res["faithfulness"].get("n_template_refs")}


def main(argv: Optional[List[str]] = None) -> str:
    ap = argparse.ArgumentParser(description="v2.3 D6 根因解释生成器（rule 离线 / llm 可选）")
    ap.add_argument("--classes", nargs="+", default=None,
                    help=f"事故类型，默认 READY_CODES={READY_CODES}")
    ap.add_argument("--groups", nargs="+", default=None,
                    help=f"严重度组，默认 {GROUPS}")
    ap.add_argument("--backend", choices=["rule", "llm"], default="rule",
                    help="仅作用于类级/全局汇总解释；模板级 9 份固定 rule（V23-DESIGN-020）")
    ap.add_argument("--run-id", default=None, help="只接受该 run_id 的输入，防止混源")
    ap.add_argument("--no-manifest", action="store_true", help="不刷新 manifest.json")
    args = ap.parse_args(argv)
    artifact.bind_run(args.run_id or artifact.latest_run())

    classes = list(args.classes or READY_CODES)
    unknown = [k for k in classes if k not in REGISTRY]
    if unknown:
        raise SystemExit(f"未知 accident_type：{unknown}")
    groups = list(args.groups or GROUPS)

    results: List[Dict] = []
    errors: List[str] = []
    for k in classes:
        for g in groups:
            try:
                if args.run_id:
                    LE.build_payload(k, g, run_id=args.run_id)  # 先校验同源
                # 模板级固定 rule：不再逐模板调用 LLM（V23-DESIGN-020）
                results.append(explain_one(k, g, backend="rule"))
            except Exception as exc:  # noqa: BLE001 - 逐模板记录，不掩盖
                logger.error(f"[D6] {k}_{g} 解释生成失败：{type(exc).__name__}: {exc}")
                errors.append(f"{k}_{g}: {type(exc).__name__}: {exc}")

    summaries: List[Dict] = []
    for k in classes:
        try:
            summaries.append(explain_class(k, backend=args.backend,
                                           run_id=args.run_id))
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[D6] {k} 类级汇总解释失败：{type(exc).__name__}: {exc}")
            errors.append(f"{k}(class): {type(exc).__name__}: {exc}")
    try:
        summaries.append(explain_global(classes, backend=args.backend,
                                        run_id=args.run_id))
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[D6] 全局结论解释失败：{type(exc).__name__}: {exc}")
        errors.append(f"global: {type(exc).__name__}: {exc}")

    run_id = args.run_id or (results[0]["run_id"] if results else None)
    if run_id is None:
        raise SystemExit(f"没有任何成功解释，失败项：{errors}")

    summary = {
        "n_requested": len(classes) * len(groups),
        "n_ok": len(results),
        "n_failed": len(errors),
        "template_backend": "rule",
        "summary_backend_requested": args.backend,
        "n_class_summaries": sum(1 for s in summaries if s["scope"] == "class"),
        "n_global_summaries": sum(1 for s in summaries if s["scope"] == "global"),
        "n_summary_cache_hits": sum(1 for s in summaries if s["cache_hit"]),
        "n_summary_degraded": sum(1 for s in summaries if s["degraded"]),
        "n_degraded": sum(1 for r in results if r["degraded"]),
        "n_unfaithful": (sum(1 for r in results if not r["faithful"])
                         + sum(1 for s in summaries if not s["faithful"])),
        "llm_available": LE.llm_unavailable_reason() is None,
        "llm_unavailable_reason": LE.llm_unavailable_reason(),
        "items": results,
        "summaries": summaries,
        "errors": errors,
    }
    artifact.write_artifact("llm_explanation_summary", summary, "reports",
                            run_id=run_id, source="reports/llm_explanation_{K}_{g}.json")

    print(f"[D6] run_id={run_id} templates=rule "
          f"ok={len(results)}/{len(classes) * len(groups)} "
          f"summaries={len(summaries)}（cache_hit={summary['n_summary_cache_hits']}, "
          f"degraded={summary['n_summary_degraded']}）unfaithful={summary['n_unfaithful']}")
    for r in results:
        print(f"  - {r['template']}: backend={r['backend']} faithful={r['faithful']} "
              f"top1={r['top1']}")
    for s in summaries:
        print(f"  - {s['scope']}:{s['template']} backend={s['backend']} "
              f"cache_hit={s['cache_hit']} faithful={s['faithful']}")
    if not args.no_manifest:
        artifact.build_manifest(run_id)
    if errors:
        print(f"[D6] 失败 {len(errors)} 项：{errors}", file=sys.stderr)
        raise SystemExit(1)
    return run_id


if __name__ == "__main__":
    main()

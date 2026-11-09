"""V2.3 D6 执行器：为 9 个模板生成“可核查、不编造”的根因解释。

特性：
  - 默认 backend=rule，纯离线（不发网络请求、不读 API key），确定性；
  - backend=llm 仅当 CAUSALTRACE_LLM_BASE_URL 与 CAUSALTRACE_LLM_API_KEY 同时存在时启用，
    失败自动降级 rule 并写 degraded_reason；
  - 只读 output/rca_v23/{tracing,signatures,reports} 的既有产物，不重算因果；
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

from core.utils import logger
from rca.v21 import artifact
from rca.v21 import llm_explanation as LE
from rca.v21.accident_config import READY_CODES, REGISTRY

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


def main(argv: Optional[List[str]] = None) -> str:
    ap = argparse.ArgumentParser(description="v2.3 D6 根因解释生成器（rule 离线 / llm 可选）")
    ap.add_argument("--classes", nargs="+", default=None,
                    help=f"事故类型，默认 READY_CODES={READY_CODES}")
    ap.add_argument("--groups", nargs="+", default=None,
                    help=f"严重度组，默认 {GROUPS}")
    ap.add_argument("--backend", choices=["rule", "llm"], default="rule",
                    help="rule=离线规则（默认）；llm=OpenAI 兼容接口（失败自动降级）")
    ap.add_argument("--run-id", default=None, help="只接受该 run_id 的输入，防止混源")
    ap.add_argument("--no-manifest", action="store_true", help="不刷新 manifest.json")
    args = ap.parse_args(argv)

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
                results.append(explain_one(k, g, backend=args.backend))
            except Exception as exc:  # noqa: BLE001 - 逐模板记录，不掩盖
                logger.error(f"[D6] {k}_{g} 解释生成失败：{type(exc).__name__}: {exc}")
                errors.append(f"{k}_{g}: {type(exc).__name__}: {exc}")

    run_id = args.run_id or (results[0]["run_id"] if results else None)
    if run_id is None:
        raise SystemExit(f"没有任何成功解释，失败项：{errors}")

    summary = {
        "n_requested": len(classes) * len(groups),
        "n_ok": len(results),
        "n_failed": len(errors),
        "requested_backend": args.backend,
        "n_degraded": sum(1 for r in results if r["degraded"]),
        "n_unfaithful": sum(1 for r in results if not r["faithful"]),
        "llm_available": LE.llm_unavailable_reason() is None,
        "llm_unavailable_reason": LE.llm_unavailable_reason(),
        "items": results,
        "errors": errors,
    }
    artifact.write_artifact("llm_explanation_summary", summary, "reports",
                            run_id=run_id, source="reports/llm_explanation_{K}_{g}.json")

    print(f"[D6] run_id={run_id} backend={args.backend} "
          f"ok={len(results)}/{len(classes) * len(groups)} "
          f"degraded={summary['n_degraded']} unfaithful={summary['n_unfaithful']}")
    for r in results:
        print(f"  - {r['template']}: backend={r['backend']} faithful={r['faithful']} "
              f"top1={r['top1']}")
    if not args.no_manifest:
        artifact.build_manifest(run_id)
    if errors:
        print(f"[D6] 失败 {len(errors)} 项：{errors}", file=sys.stderr)
        raise SystemExit(1)
    return run_id


if __name__ == "__main__":
    main()

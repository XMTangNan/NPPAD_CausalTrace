"""V2.3 D6-operator 执行器：操作者版解释（知识与判定彻底分离）。

特性：
  - **附加产物**：写 `reports/llm_explanation_operator_{K}_{g}.json`，
    不触碰、不覆盖既有 `llm_explanation_{K}_{g}.json`；
  - 只读本 run 的 tracing / signatures / reports / tolerances 与 registry.data_dir 原始 CSV，
    不重算因果、不改排序与门控（`ranking_modified=false`）；
  - 判定只用数据侧三样（首动先后、图结构、偏离幅度与阶跃），
    时序证据由 `causalrca.evidence.temporal` 现算；
  - 知识不进入 LLM 输入，只由渲染器补“物理背景（仅描述，不参与判定）”；
  - `--backend llm` 失败或输出未通过判定校验时，降级规则稿并写 `degraded_reason`。

用法（cd Code）：
  PYTHONPATH=. ../.venv/bin/python -m causalrca.cli.run_v23_operator_explanation \
      --run-id v23-20261115T211511 --classes LOCA SGATR FLB --groups low mid
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional

from causalrca.config.accident_config import READY_CODES, REGISTRY
from causalrca.io import artifact
from causalrca.io.logging import logger
from causalrca.reporting import operator_explanation as OE

GROUPS: List[str] = ["low", "mid", "high"]


def main(argv: Optional[List[str]] = None) -> str:
    ap = argparse.ArgumentParser(
        description="v2.3 操作者版解释（知识与判定分离，附加产物）")
    ap.add_argument("--classes", nargs="+", default=None,
                    help=f"事故类型，默认 READY_CODES={READY_CODES}")
    ap.add_argument("--groups", nargs="+", default=None,
                    help=f"严重度组，默认 {GROUPS}")
    ap.add_argument("--backend", choices=["rule", "llm", "auto"], default="llm",
                    help="默认 llm；无 key 或校验失败自动降级规则稿")
    ap.add_argument("--run-id", default=None, help="只读该 run_id 的产物，防止混源")
    ap.add_argument("--no-manifest", action="store_true", help="不刷新 manifest.json")
    args = ap.parse_args(argv)

    run_id = args.run_id or artifact.latest_run()
    if not run_id:
        raise SystemExit("没有可用 run_id：请 --run-id 指定或先跑主链")
    artifact.bind_run(run_id)

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
                doc = OE.explain_operator(k, g, backend=args.backend, run_id=run_id)
                results.append({
                    "template": doc["template"],
                    "backend": doc["backend"],
                    "degraded": doc["degraded"],
                    "degraded_reason": doc["degraded_reason"],
                    "verdict_counts": doc["verdict_counts"],
                    "pointing": ((doc.get("pointing") or {}).get("llm") or {}).get("variable"),
                    "validation_errors": len((doc.get("validation") or {}).get("errors") or []),
                    "faithful": (doc.get("faithfulness") or {}).get("faithful"),
                })
            except Exception as exc:                  # noqa: BLE001 - 逐模板记录，不掩盖
                logger.error(f"[D6-operator] {k}_{g} 失败：{type(exc).__name__}: {exc}")
                errors.append(f"{k}_{g}: {type(exc).__name__}: {exc}")

    print(f"[D6-operator] run_id={run_id} ok={len(results)}/{len(classes) * len(groups)} "
          f"degraded={sum(1 for r in results if r['degraded'])} "
          f"validation_failed={sum(1 for r in results if r['validation_errors'])}")
    for r in results:
        print(f"  - {r['template']}: backend={r['backend']} "
              f"counts={r['verdict_counts']} pointing={r['pointing']} "
              f"faithful={r['faithful']}")
    if not args.no_manifest:
        artifact.build_manifest(run_id)
    if errors:
        print(f"[D6-operator] 失败 {len(errors)} 项：{errors}", file=sys.stderr)
        raise SystemExit(1)
    return run_id


if __name__ == "__main__":
    main()

"""对既有 run 的单个模板执行“判定式解释”冒烟（V23-DESIGN-062）。

默认关闭：必须显式设置 ``CAUSALTRACE_LLM_VERDICT=1`` 才会真正调用 LLM。
产物默认写到 ``Code/experiments/verdict_probe/<run_id>/``（只读实验产物目录），
**不写入 run 目录**（历史产物不可变），也不写 output/ 顶层（V23-CODE-031）。

用法：
  CAUSALTRACE_LLM_VERDICT=1 .venv/bin/python -m causalrca.cli.run_v23_verdict_explanation \
      --run-id v23-20261111T022417 --template LOCA_low
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional

from causalrca.config.paths import CODE_ROOT
from causalrca.io import artifact
from causalrca.reporting import explanation_verdict as EV


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="单模板判定式解释冒烟（默认开关关闭）")
    ap.add_argument("--run-id", default=None, help="默认取 latest")
    ap.add_argument("--template", required=True, help="如 LOCA_low")
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--out-dir", default=None,
                    help="默认 Code/experiments/verdict_probe/<run_id>")
    ap.add_argument("--dry-run", action="store_true", help="只打印证据包摘要，不调用 LLM")
    args = ap.parse_args(argv)

    run_id = args.run_id or artifact.latest_run()
    if not run_id:
        raise SystemExit("没有可用 run_id")
    k, g = args.template.rsplit("_", 1)
    artifact.bind_run(run_id)

    if args.dry_run:
        pack = EV.build_evidence_pack(run_id, k, g)
        print(json.dumps({
            "run_id": run_id, "template": f"{k}_{g}",
            "n_stable_edges": len(pack["stable_edges"]),
            "candidates": [{"rank": c["rank"], "variable": c["variable"],
                            "in": len(c["incoming"]), "out": len(c["outgoing"])}
                           for c in pack["candidates"]],
            "knowledge_ids": ([r["id"] for r in pack["knowledge"]["k2"]]
                              + [pack["knowledge"]["k3"]["template_id"]]
                              + [e["id"] for e in pack["knowledge"]["supplementary"]]),
            "conflicts": pack["conflicts"],
            "evidence_sha256": EV.sha256_json(pack),
        }, ensure_ascii=False, indent=1))
        return 0

    if not EV.verdict_enabled():
        raise SystemExit(f"开关 {EV.VERDICT_ENV} 未开启（默认关闭）。"
                         f"如需冒烟请显式设置 {EV.VERDICT_ENV}=1")

    out = EV.run_template_verdict(run_id, k, g, timeout=args.timeout)
    out_dir = Path(args.out_dir) if args.out_dir else (
        Path(CODE_ROOT) / "experiments" / "verdict_probe" / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = out_dir / f"verdict_{k}_{g}.json"
    fp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"mode={out['explanation_mode']} backend={out['backend']} "
          f"attempts={out['llm']['attempts']} key_source={out['llm']['key_source']}")
    print(f"validation_ok={out['validation']['ok']} "
          f"errors={out['validation']['errors'][:6]}")
    print(f"degraded_reason={out['degraded_reason']}")
    for v in (out.get("verdict") or {}).get("verdicts", []):
        print(f"  #{v.get('rank')} {v.get('variable')}: {v.get('verdict')} | "
              f"graph={v.get('graph_basis')} | knowledge={v.get('knowledge_basis')} | "
              f"{v.get('mechanism')}")
    print(f"output={fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

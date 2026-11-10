"""V2.3 D7 执行器：一次生成 9 份结构化 RCA 报告（3 类 × 3 组）的 json + txt。

只读本 run 的既有产物（tracing/signatures/reports），不重算因果；
run_id 默认从 root_candidates 推导，传入 --run-id 时强制同源校验。

用法（cd Code）：
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_report
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_report --classes LOCA --groups mid
"""
from __future__ import annotations

import argparse
from typing import List, Optional

from causalrca.io.logging import logger
from causalrca.io import artifact
from causalrca.reporting import report_template as RT
from causalrca.config.accident_config import READY_CODES, REGISTRY


def main(argv: Optional[List[str]] = None) -> str:
    ap = argparse.ArgumentParser(description="v2.3 D7 结构化 RCA 报告生成器（9 份）")
    ap.add_argument("--classes", nargs="+", default=None,
                    help=f"事故类型，默认 READY_CODES={READY_CODES}")
    ap.add_argument("--groups", nargs="+", default=None,
                    help=f"严重度组，默认 {RT.GROUPS}")
    ap.add_argument("--run-id", default=None, help="只接受该 run_id 的输入，防止混源")
    ap.add_argument("--no-manifest", action="store_true", help="不刷新 manifest.json")
    args = ap.parse_args(argv)
    artifact.bind_run(args.run_id or artifact.latest_run())

    classes = list(args.classes or READY_CODES)
    unknown = [k for k in classes if k not in REGISTRY]
    if unknown:
        raise SystemExit(f"未知 accident_type：{unknown}")
    groups = list(args.groups or RT.GROUPS)

    done = RT.run_all_reports(classes=classes, groups=groups, run_id=args.run_id)
    run_id = args.run_id or done[0]["run_id"]
    mismatched = [d["template"] for d in done if d["run_id"] != run_id]
    if mismatched:
        raise SystemExit(f"报告 run_id 不同源：{mismatched} != {run_id}")

    print(f"[D7] run_id={run_id} 生成 {len(done)}/{len(classes) * len(groups)} 份报告")
    for d in done:
        print(f"  - {d['template']}: {d['paths']['json']}")
    if not args.no_manifest:
        artifact.build_manifest(run_id)
    return run_id


if __name__ == "__main__":
    main()

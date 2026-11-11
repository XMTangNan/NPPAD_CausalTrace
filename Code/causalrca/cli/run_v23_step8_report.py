"""V2.3 第八步（D7）执行器：判定式解读报告（模型判定 + 规则兜底，默认开启）。

与 ``run_v23_report.py`` 的区别（V23-DESIGN-065）：
  - 人读正文 ``reports/rca_report_{K}_{g}.txt`` 的"Top-5 逐个判定与机理"由 LLM 依据
    证据包写；``llm_explanation_{K}_{g}.json`` 降为证据附件；
  - **默认开启**，不再需要 ``CAUSALTRACE_LLM_VERDICT``；校验不过自动回落规则稿并标注
    ``未经模型判断``；
  - 只重生成报告：默认复用 ``--source-run-id`` 已落盘的图与签名（不重学图），
    产物写入**新的 run 目录**（默认 ``<source_run_id>-r8``）并写 ``lineage.json``，
    源 run 任何文件都不动。

用法（cd Code）：
  PYTHONPATH=. ../.venv/bin/python -m causalrca.cli.run_v23_step8_report \
      --source-run-id v23-20261111T022417 --template LOCA_low
  PYTHONPATH=. ../.venv/bin/python -c "..."  # 其余 8 份见 --all
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import List, Optional

from causalrca.config.accident_config import READY_CODES
from causalrca.io import artifact
from causalrca.reporting import verdict_report as VR


def _ensure_run_dir(run_id: str) -> None:
    artifact.subdir_path("reports", run_id).mkdir(parents=True, exist_ok=True)


def _write_lineage(run_id: str, source_run_id: str, templates: List[str]) -> str:
    root = artifact.subdir_path("reports", run_id).parent
    # 同一目录可能分多次生成（先 1 份样例、再其余 8 份）：模板清单按并集累加，不覆盖历史。
    prev: List[str] = []
    existing = root / "lineage.json"
    if existing.exists():
        try:
            prev = list(json.loads(existing.read_text(encoding="utf-8")).get("templates") or [])
        except (OSError, ValueError):
            prev = []
    merged = sorted(set(prev) | set(templates))
    doc = {
        "schema": "causaltrace/lineage/v1",
        "run_id": run_id,
        "lineage": {
            "source_run_id": source_run_id,
            "regenerated_at": datetime.now().isoformat(timespec="seconds"),
            "graph_reused": True,
            "report_only": True,
            "note": "图、签名、追踪产物复用源 run；本目录只含第八步重生成的报告与证据附件。",
        },
        "templates": merged,
        "stats_source": f"output/runs/{source_run_id}",
    }
    path = root / "lineage.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="v2.3 第八步：判定式解读报告（默认模型判定）")
    ap.add_argument("--source-run-id", default=None, help="复用哪个 run 的图与签名（默认 latest）")
    ap.add_argument("--out-run-id", default=None, help="输出 run 目录，默认 <source>-r8")
    ap.add_argument("--template", action="append", default=None,
                    help="模板，如 LOCA_low，可重复；与 --all 二选一")
    ap.add_argument("--all", action="store_true", help="全部已就绪事故类型 × 3 组（9 份）")
    ap.add_argument("--timeout", type=float, default=None, help="单次 LLM 调用超时（秒）")
    args = ap.parse_args(argv)

    source = args.source_run_id or artifact.latest_run()
    if not source:
        raise SystemExit("没有可用 --source-run-id（且无 latest）")
    out_run = args.out_run_id or f"{source}-r8"
    artifact.bind_run(source)

    if args.all:
        templates = [f"{k}_{g}" for k in READY_CODES for g in ("low", "mid", "high")]
    else:
        templates = list(args.template or [])
    if not templates:
        raise SystemExit("请指定 --template（可重复）或 --all")

    _ensure_run_dir(out_run)
    lineage = _write_lineage(out_run, source, templates)
    # 建目录/写 lineage 会把进程绑定切到 out_run；读取源 run 前必须切回。
    artifact.bind_run(source)

    done = []
    for t in templates:
        k, g = t.rsplit("_", 1)
        r = VR.generate_one(source, k, g, out_run_id=out_run, timeout=args.timeout)
        done.append(r)
        ok = bool((r.get("validation") or {}).get("ok"))
        print(f"[D7] {t}: mode={r['mode']} validation_ok={ok} "
              f"-> {r['txt']}")

    n_llm = sum(1 for d in done if d["mode"] == "llm_verified")
    print(f"[D7] source={source} out={out_run} n={len(done)} llm_verified={n_llm} "
          f"fallback={len(done) - n_llm}")
    print(f"[D7] lineage={lineage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

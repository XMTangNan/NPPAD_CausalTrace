"""v2.3 D2 执行器：每类 sev-1 公差基准 → `tolerances/tolerance_{K}.json`（同一 run_id）。

职责（Code/STRUCTURE.md §2 执行器层）：
- 只对注册表 `ready` 类计算公差并逐类落盘（LOCAC 仅描述性、8 类无数据）；
- `data_missing` 类跳过并写 `tolerances/skipped_classes.json`，禁止占位结果；
- 算法全部在 `rca.v21.tolerance.compute_tolerance`（纯函数，不写盘）。

用法（cd Code）：
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_tolerances
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_v23_tolerances --classes LOCA
"""
from __future__ import annotations

import argparse
from typing import Dict, List, Optional, Sequence

from causalrca.io import artifact
from causalrca.config.accident_config import (
    MISSING_CODES, PARTIAL_CODES, READY_CODES, get_accident,
)
from causalrca.evidence.tolerance import compute_tolerance
from causalrca.io.logging import logger


def run_classes(classes: Sequence[str], run_id: str) -> Dict[str, Dict]:
    """逐类计算公差并经 artifact 落盘（不在此处重算算法）。"""
    results: Dict[str, Dict] = {}
    for k in classes:
        tol = compute_tolerance(k)
        artifact.write_artifact(
            f"tolerance_{k}", tol, "tolerances", run_id=run_id, accident_type=k,
            source=f"nppad_repo/Operation_csv_data/{k}/{k}_1.csv")
        results[k] = tol
    return results


def _skipped_entries(missing: Sequence[str]) -> List[Dict]:
    entries = []
    for code in missing:
        cfg = get_accident(code)
        entries.append({"code": code, "status": cfg["status"],
                        "reason": f"注册表 status=data_missing；{cfg['notes']}",
                        "data_dir": cfg["data_dir"]})
    return entries


def write_skipped(run_id: str, classes: Sequence[str]) -> None:
    """data_missing / partial 类跳过清单（不留占位公差）。"""
    missing = [c for c in MISSING_CODES if c not in classes]
    partial = [c for c in PARTIAL_CODES if c not in classes]
    artifact.write_artifact(
        "skipped_classes",
        {"entries": _skipped_entries(missing),
         "n_ready": len(classes), "n_partial_not_run": len(partial),
         "n_missing": len(missing),
         "partial_not_run": list(partial),
         "note": "data_missing 类直接跳过；partial 类（LOCAC）仅描述性、不进 D2 验收，"
                 "均不生成占位公差"},
        "tolerances", run_id=run_id, source="causalrca/config/accident_config.py")


def run_all(run_id: str, classes: Optional[Sequence[str]] = None) -> Dict[str, Dict]:
    """D10 编排入口：同一 run_id 算 READY 类公差 + 写跳过清单（不另起 run_id）。"""
    classes = list(classes) if classes is not None else list(READY_CODES)
    results = run_classes(classes, run_id)
    write_skipped(run_id, classes)
    return results


def _quality_table(results: Dict[str, Dict], run_id: str,
                   classes: Sequence[str]) -> str:
    missing = [c for c in MISSING_CODES if c not in classes]
    partial = [c for c in PARTIAL_CODES if c not in classes]
    lines = [f"NPPAD-CausalTrace v2.3 D2 每类公差（run_id={run_id}）",
             f"{'类别':<8}{'基线点数':>9}{'变量数':>8}{'常量数':>8}{'Z病态数':>9}"
             f"{'病态比例':>10}{'quality':>10}"]
    for k, tol in results.items():
        q = tol["quality"]
        lines.append(f"{k:<8}{q['baseline_points']:>9}{q['n_variables_pass']:>8}"
                     f"{q['n_constant_pre_inject']:>8}{q['z_pathology_count']:>9}"
                     f"{q['z_pathology_ratio']:>10.1%}"
                     f"{('PASS' if q['quality_pass'] else 'FAIL'):>10}")
    lines.append("")
    lines.append("基线口径：注入前 [0,100]s 共 11 点（V23-DATA-004）；"
                 "准稳态段只记录、不参与 μ/σ")
    lines.append("排除口径：仅本类 marker + SAFETY_STEP_VARS（V23-CODE-016）")
    lines.append("σ 口径：max(1.4826*MAD, σ_floor(v))，σ_floor 来源逐变量记录")
    if missing:
        lines.append(f"data_missing 跳过 {len(missing)} 类：{missing}")
    if partial:
        lines.append(f"partial 不跑：{list(partial)}（仅描述性）")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> str:
    ap = argparse.ArgumentParser(description="v2.3 D2：每类 sev-1 公差基准")
    ap.add_argument("--classes", default=",".join(READY_CODES),
                    help="逗号分隔，默认注册表 ready 类")
    ap.add_argument("--no-manifest", action="store_true")
    args = ap.parse_args(argv)

    classes = [c.strip().upper() for c in args.classes.split(",") if c.strip()]
    unknown = [c for c in classes if c not in READY_CODES]
    if unknown:
        ap.error(f"非 ready 类不能计算公差: {unknown}（ready={READY_CODES}）")

    run_id = artifact.new_run_id("v23d")
    logger.info(f"[v23d] run_id={run_id} classes={classes}")
    results = run_all(run_id, classes)

    if not args.no_manifest:
        artifact.build_manifest(run_id)

    print(_quality_table(results, run_id, classes))
    logger.info(f"[v23d] done run_id={run_id}")
    return run_id


if __name__ == "__main__":
    main()

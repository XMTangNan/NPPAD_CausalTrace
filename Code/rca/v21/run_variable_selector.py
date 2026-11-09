"""V2.3 Phase 1 执行器：四阶段变量选取（12 类注册表驱动，实际执行 ready 类）。

用法（cd Code）：PYTHONPATH=. python -m rca.v21.run_variable_selector

产物（Code/STRUCTURE.md §2）：
  output/rca_v23/registry/accident_registry.json   12 类注册表快照
  output/rca_v23/registry/split_manifest.json      v2.1 冻结分组快照（只读输入留痕）
  output/rca_v23/variable_sets/selection_report_{K}.json
  output/rca_v23/variable_sets/variable_selection_summary.json
"""
from __future__ import annotations

from typing import Dict, List

from rca.rca_config_v21 import ACCIDENT_TYPES
from rca.v21 import artifact as A
from rca.v21 import data as D
from rca.v21.accident_config import (ALL_CODES, MISSING_CODES, PARTIAL_CODES,
                                     READY_CODES, REGISTRY)
from rca.v21.variable_selector import build_raw_dataframe, select_variables
from core.utils import logger


def main() -> None:
    run_id = A.new_run_id("v23a")
    logger.info(f"[变量选取] run_id={run_id}")

    # 注册表与分组快照：同一 run 的其余阶段都引用这两份
    A.write_artifact("accident_registry", {"registry": REGISTRY,
                                           "all_codes": ALL_CODES,
                                           "ready_codes": READY_CODES,
                                           "partial_codes": PARTIAL_CODES,
                                           "missing_codes": MISSING_CODES},
                     "registry", run_id=run_id, source="rca/v21/accident_config.py")
    manifest = D.load_json("split_manifest.json")
    A.write_artifact("split_manifest", {"classes": manifest["classes"]},
                     "registry", run_id=run_id,
                     source="output/rca_v21/split_manifest.json（v2.1 冻结）")

    all_reports: Dict[str, dict] = {}
    all_selected: Dict[str, List[str]] = {}
    skipped: Dict[str, str] = {}

    # 仅用 train 轨迹做变量选取，避免 test 信息泄漏（评审意见 P0-3）
    for k in ALL_CODES:
        if k not in ACCIDENT_TYPES:
            skipped[k] = ("partial：仅 5 条轨迹，不满足四阶段漏斗最小样本"
                          if k in PARTIAL_CODES else "data_missing：本地无该事故数据")
            continue
        train_sevs: List[int] = []
        for g in manifest["classes"][k]["groups"].values():
            train_sevs.extend(g.get("train_template_severities", []))
        train_sevs = sorted(set(train_sevs))
        raw_data, sev_by_traj = build_raw_dataframe(k, train_sevs)
        logger.info(f"[变量选取] {k}: 加载 {len(train_sevs)} 条 train 轨迹 "
                    f"(已排除 test), raw_data shape={raw_data.shape}")

        selected, report = select_variables(k, raw_data, sev_by_traj, random_state=42)

        # V6 可复现校验：同 seed 二次运行，比较选中集（评审 P1-8：原硬编码 True）
        selected2, _ = select_variables(k, raw_data, sev_by_traj, random_state=42)
        report["checks"]["V6_reproducible"] = (selected == selected2)
        all_reports[k] = report
        all_selected[k] = selected

        A.write_artifact(f"selection_report_{k}", report, "variable_sets", run_id=run_id,
                         accident_type=k, source=f"nppad_repo/Operation_csv_data/{k}")

    # 汇总（只汇总真正执行过的类，跳过类单列，绝不生成占位结论）
    summary = {
        "accident_types": sorted(all_selected),
        "selected_vars": all_selected,
        "checks_summary": {k: all_reports[k]["checks"] for k in all_selected},
        "n_selected": {k: len(all_selected[k]) for k in all_selected},
        # 三态口径：True=pass / False=fail / None=n/a（V3 不适用时不得判为失败）
        "all_pass": all(v is not False for r in all_reports.values()
                        for v in r["checks"].values()),
        "skipped": skipped,
    }
    A.write_artifact("variable_selection_summary", summary, "variable_sets", run_id=run_id,
                     source="rca/v21/run_variable_selector.py")

    logger.info("\n===== 变量选取结果汇总 =====")
    for k in all_selected:
        logger.info(f"{k}: {len(all_selected[k])} 变量 -> {all_selected[k]}")
    logger.info(f"全部 V1-V7 通过: {summary['all_pass']}；跳过 {len(skipped)} 类: {skipped}")


if __name__ == "__main__":
    main()

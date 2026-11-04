"""V2.3 Phase 1 执行器：在 LOCA/SGATR/FLB 上运行四阶段变量选取，输出选取报告。

用法（cd Code）：PYTHONPATH=. python -m rca.v21.run_variable_selector
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from rca.rca_config_v21 import ACCIDENT_TYPES, V21_OUTPUT
from rca.v21 import data as D
from rca.v21.variable_selector import build_raw_dataframe, select_variables
from core.utils import logger, save_json

OUT_DIR = V21_OUTPUT / "variable_selection"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    all_reports = {}
    all_selected = {}

    # 仅用 train 轨迹做变量选取，避免 test 信息泄漏（评审意见 P0-3）
    manifest = D.load_json("split_manifest.json")

    for k in ACCIDENT_TYPES:
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

        # 落盘：每类事故的选取报告
        save_json(report, f"selection_report_{k}", result_dir=OUT_DIR)
        logger.info(f"[变量选取] {k} 报告已落盘 -> {OUT_DIR / f'selection_report_{k}.json'}")

    # 汇总
    summary = {
        "accident_types": ACCIDENT_TYPES,
        "selected_vars": all_selected,
        "checks_summary": {
            k: all_reports[k]["checks"] for k in ACCIDENT_TYPES
        },
        "n_selected": {k: len(all_selected[k]) for k in ACCIDENT_TYPES},
        "all_pass": all(
            all(all_reports[k]["checks"].values()) for k in ACCIDENT_TYPES
        ),
    }
    save_json(summary, "variable_selection_summary", result_dir=OUT_DIR)
    logger.info(f"[变量选取] 汇总已落盘 -> {OUT_DIR / 'variable_selection_summary.json'}")

    # 控制台打印对照
    logger.info("\n===== 变量选取结果汇总 =====")
    for k in ACCIDENT_TYPES:
        logger.info(f"{k}: {len(all_selected[k])} 变量 -> {all_selected[k]}")
    logger.info(f"全部 V1-V7 通过: {summary['all_pass']}")


if __name__ == "__main__":
    main()

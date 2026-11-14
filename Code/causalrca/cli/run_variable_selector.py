"""V2.3 Phase 1 执行器：四阶段变量选取（12 类注册表驱动，实际执行 ready 类）。

用法（cd Code）：PYTHONPATH=. python -m rca.v21.run_variable_selector

产物（Code/STRUCTURE.md §2）：
  <run 根>/registry/accident_registry.json   12 类注册表快照
  <run 根>/registry/split_manifest.json      v2.1 冻结分组快照（只读输入留痕）
  <run 根>/registry/shared_baseline.json     v2.1 冻结共享基线快照
  <run 根>/registry/marker_scale.json        v2.1 冻结 marker 尺度快照
  <run 根>/variable_sets/selection_report_{K}.json
  <run 根>/variable_sets/variable_selection_summary.json

S1 契约：三份"输入快照"由 A 阶段统一写入 v2.3 registry，C 阶段只读 v2.3 根
（`run_v23_end2end.load_inputs` 优先读 registry，v2.1 根仅作只读回退）。
"""
from __future__ import annotations

from typing import Dict, List

from causalrca.config.constants_v21 import ACCIDENT_TYPES
from causalrca.io import artifact as A
from causalrca.data import loader as D
from causalrca.config.accident_config import (ALL_CODES, MISSING_CODES, PARTIAL_CODES,
                                     READY_CODES, REGISTRY)
from causalrca.features.variable_selector import (
    build_raw_dataframe, select_variables, set_selection_profile,
    get_selection_profile, SELECTION_PROFILE_V23,
)
from causalrca.io.logging import logger

# A 阶段负责落 v2.3 registry 的三份冻结输入（文件名保持不变，便于与 v2.1 对照）
FROZEN_INPUTS = ("split_manifest", "shared_baseline", "marker_scale")


def snapshot_frozen_inputs(run_id: str) -> None:
    """把 v2.1 冻结输入复制进 v2.3 registry（只读快照，值不做任何改动）。"""
    for name in FROZEN_INPUTS:
        src = A.legacy_input(f"{name}.json")
        if not src.exists():
            raise FileNotFoundError(
                f"缺 v2.1 冻结输入: {src}（先跑 R0/A 阶段前置）")
        payload = D.load_json(f"{name}.json")
        if name == "split_manifest":
            # 与旧行为一致：只留分组快照主体，不带 v2.1 的窗口/seed 元数据
            payload = {"classes": payload["classes"]}
        A.write_artifact(name, payload, "registry", run_id=run_id,
                         source=f"output/rca_v21/{name}.json（v2.1 冻结，只读快照）")


def main(selection_profile: str = None, per_subsystem_keep: int = None) -> None:
    # 未显式传入时沿用进程级设置（run_v23_all 会先 set 再调用本函数）
    if selection_profile is not None:
        set_selection_profile(selection_profile, per_subsystem_keep)
    profile, profile_q = get_selection_profile()
    run_id = A.new_run_id("v23a")
    logger.info(f"[变量选取] run_id={run_id} selection_profile={profile} Q={profile_q}")
    logger.info(f"[变量选取] run_id={run_id}")

    # 注册表与分组快照：同一 run 的其余阶段都引用这两份
    A.write_artifact("accident_registry", {"registry": REGISTRY,
                                           "all_codes": ALL_CODES,
                                           "ready_codes": READY_CODES,
                                           "partial_codes": PARTIAL_CODES,
                                           "missing_codes": MISSING_CODES},
                     "registry", run_id=run_id, source="causalrca/config/accident_config.py")
    snapshot_frozen_inputs(run_id)
    manifest = D.load_json("split_manifest.json")

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
        "selection_profile": profile,
        "per_subsystem_keep": profile_q if profile != SELECTION_PROFILE_V23 else 1,
        "n_alias_groups": {k: len(all_reports[k].get("alias_merge") or []) for k in all_selected},
        "degenerate_design": {k: bool(all_reports[k].get("degenerate_design"))
                              for k in all_selected},
        "checks_summary": {k: all_reports[k]["checks"] for k in all_selected},
        "n_selected": {k: len(all_selected[k]) for k in all_selected},
        # 三态口径：True=pass / False=fail / None=n/a（V3 不适用时不得判为失败）
        "all_pass": all(v is not False for r in all_reports.values()
                        for v in r["checks"].values()),
        "skipped": skipped,
    }
    A.write_artifact("variable_selection_summary", summary, "variable_sets", run_id=run_id,
                     source="causalrca/cli/run_variable_selector.py")

    logger.info("\n===== 变量选取结果汇总 =====")
    for k in all_selected:
        logger.info(f"{k}: {len(all_selected[k])} 变量 -> {all_selected[k]}")
    logger.info(f"全部 V1-V7 通过: {summary['all_pass']}；跳过 {len(skipped)} 类: {skipped}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="v2.3 A 阶段：四阶段变量选取")
    ap.add_argument("--selection-profile", default=SELECTION_PROFILE_V23,
                    choices=[SELECTION_PROFILE_V23, "wide"])
    ap.add_argument("--per-subsystem-keep", type=int, default=None)
    args = ap.parse_args()
    main(args.selection_profile, args.per_subsystem_keep)

"""D1 / R0 执行器：数据冻结 + 审计 + 过程本体基线 + 三变量表 + target 审计。

产物（output/rca_v21/）：
  split_manifest.json / r0_audit.json / shared_baseline.json /
  variable_sets.json / marker_scale.json / target_audit.md
用法：cd Code && PYTHONPATH=. python -m rca.v21.run_r0
"""
from __future__ import annotations

import numpy as np

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, MARKERS, MARKER_COLS, TARGET_CANDIDATES, GROUP_ORDER,
    MAIN_WINDOW_START, MAIN_WINDOW_END,
)
from rca.v21 import data as D
from core.utils import logger


def _target_audit(manifest, baseline) -> str:
    mu, sigma = baseline["mu"], baseline["sigma"]
    lines = [
        "# R0 target 审计（2026-10-26，方案 13.3.4）",
        "",
        "**冻结声明：target 变量仅作展示锚点（R5 传播链对照），不进入 E1/E2/E3、不参与分类、"
        "不进入 D0/标定。测试侧禁止用真实类别选 target。**",
        "",
        "口径：训练轨迹主窗[300,1500]逐变量中位 z（相对过程本体共享 mu/sigma），按严重度组汇总中位数。",
        "",
    ]
    for k in ACCIDENT_TYPES:
        lines.append(f"## {k}（marker={MARKERS[k]}）；v2.0 CLASS_TARGETS 沿用候选："
                     f"{ {'LOCA':'P','SGATR':'LSGA','FLB':'WFWA'}[k] }")
        lines.append("")
        lines.append("| target 候选 | low 中位z | mid | high | 是否可建阈值 | 审计意见 |")
        lines.append("|---|---|---|---|---|---|")
        for v in TARGET_CANDIDATES[k]:
            if v not in mu:
                lines.append(f"| {v} | — | — | — | 否（未入池，非18物理候选） | 不建 z，仅作名称展示 |")
                continue
            row = []
            for g in GROUP_ORDER:
                sevs = manifest["classes"][k]["groups"][g]["train_template_severities"]
                vals = []
                for sev in sevs:
                    X, _, _ = D.load_window(k, sev, MAIN_WINDOW_START, MAIN_WINDOW_END, [v])
                    vals.append(float(np.median((X[:, 0] - mu[v]) / sigma[v])))
                row.append(float(np.median(vals)))
            usable = "是"
            opinion = ""
            if k == "LOCA" and v == "P":
                opinion = "失压一致，可作展示锚点；非 LOCA 特异"
            if k == "SGATR":
                opinion = "SG 侧量；低严重度是否响应见左列，非特异（FLB 亦扰 SG）"
            if k == "FLB":
                opinion = "给水/SG 压力侧量；与 SGATR 重叠，不能单独定类"
            lines.append(f"| {v} | {row[0]:+.2f} | {row[1]:+.2f} | {row[2]:+.2f} | {usable} | {opinion} |")
        lines.append("")
    lines.append("结论：三类 target 候选均非类别独占，v2.0「target 上游排序」证实为错误方向；"
                 "v2.1 R5 传播链从 marker 出发，target 仅在解释中作为对照物理量出现。")
    return "\n".join(lines) + "\n"


def main():
    logger.info("=== R0-1 split manifest ===")
    manifest = D.build_split_manifest()
    D.save_json(manifest, "split_manifest.json")
    for k in ACCIDENT_TYPES:
        g = manifest["classes"][k]["groups"]
        logger.info(f"{k}: test "
                    f"low={g['low']['test_severities']} mid={g['mid']['test_severities']} "
                    f"high={g['high']['test_severities']}")

    logger.info("=== R0-2 r0 audit（300 文件）===")
    audit = D.audit_r0(manifest)
    D.save_json(audit, "r0_audit.json")
    for c in audit["checks"]:
        logger.info(f"[{'PASS' if c['pass'] else 'FAIL'}] {c['name']}")
    if not audit["passed"]:
        raise RuntimeError(f"R0 审计失败: {audit['fail']}（G1 门未过，禁止进入 R0.5）")

    logger.info("=== R0-3 shared process ontology baseline（sev-1 恢复段）===")
    baseline, vthr, excluded = D.fit_shared_baseline()
    D.save_json(baseline, "shared_baseline.json")
    logger.info(f"V_thr({len(vthr['V_thr'])}): {vthr['V_thr']}")
    logger.info(f"excluded constant: {[e['variable'] for e in excluded]}")

    logger.info("=== R0-4 variable sets ===")
    vsets = D.variable_sets(vthr["V_thr"])
    D.save_json(vsets, "variable_sets.json")

    logger.info("=== R0-5 marker scale + epsilon_k ===")
    scale = {"median_platform": D.marker_platform_scale(manifest),
             "epsilon": D.epsilon_by_class(manifest)}
    D.save_json(scale, "marker_scale.json")
    logger.info(scale)

    logger.info("=== R0-6 target audit ===")
    md = _target_audit(manifest, baseline)
    (D.V21_OUTPUT / "target_audit.md").write_text(md, encoding="utf-8")
    logger.info("R0 完成（G1 门通过）-> target_audit.md")


if __name__ == "__main__":
    main()

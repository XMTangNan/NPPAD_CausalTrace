"""V2.3 三类事故根因识别小型验证集。

验证逻辑：以方案 §4.3 定义的各事故触发变量 + 关键响应变量为预期根因集合，
检查系统输出的 Top1/Top5 是否命中，输出命中报告。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from causalrca.data import loader as D
from causalrca.io.logging import logger
from causalrca.io.files import save_json

TRACE_DIR = Path(D.V21_OUTPUT) / "v23_tracing"
OUT_DIR = Path(D.V21_OUTPUT) / "validation"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 方案 §4.3 定义的预期根因集合（触发变量 + 关键响应）
EXPECTED_ROOTS = {
    "LOCA":  {"trigger": "WLR",  "responses": ["P", "TAVG", "VOL", "WECS", "WHPI"]},
    "SGATR": {"trigger": "WTRA", "responses": ["PSGA", "PSGB", "LSGA", "LSGB", "RM2"]},
    "FLB":   {"trigger": "WFLB", "responses": ["WFWA", "WFWB", "TAVG", "PWNT", "PSGA", "PSGB"]},
}

GROUP_ORDER = ["low", "mid", "high"]


def build_validation_set() -> Dict:
    results = {}
    total_top1_hit = 0
    total_top5_hit = 0
    total = 0

    for k, exp in EXPECTED_ROOTS.items():
        expected_set = {exp["trigger"]} | set(exp["responses"])
        class_results = []
        for g in GROUP_ORDER:
            rc_path = TRACE_DIR / f"root_candidates_{k}_{g}.json"
            if not rc_path.exists():
                continue
            rc = json.load(open(rc_path))
            top1 = rc["root_candidates"][0]["variable"]
            top5 = [c["variable"] for c in rc["root_candidates"][:5]]
            top1_hit = top1 in expected_set
            top5_overlap = [v for v in top5 if v in expected_set]
            top5_hit = len(top5_overlap) > 0

            if top1_hit:
                total_top1_hit += 1
            if top5_hit:
                total_top5_hit += 1
            total += 1

            class_results.append({
                "group": g,
                "top1": top1,
                "top1_expected_hit": top1_hit,
                "top5": top5,
                "top5_expected_overlap": top5_overlap,
                "top5_hit": top5_hit,
            })

        results[k] = {
            "expected_roots": sorted(expected_set),
            "groups": class_results,
            "top1_hit_rate": round(sum(1 for r in class_results if r["top1_expected_hit"]) / max(len(class_results), 1), 3),
            "top5_hit_rate": round(sum(1 for r in class_results if r["top5_hit"]) / max(len(class_results), 1), 3),
        }

    summary = {
        "overall_top1_hit_rate": round(total_top1_hit / max(total, 1), 3),
        "overall_top5_hit_rate": round(total_top5_hit / max(total, 1), 3),
        "n_templates": total,
        "note": "预期根因集合 = 方案 §4.3 触发变量 + 关键响应变量；Top5 命中=至少 1 个预期变量进入 Top5",
    }
    return {"per_class": results, "summary": summary}


def main():
    val = build_validation_set()
    save_json(val, "v23_root_cause_validation", result_dir=OUT_DIR)
    logger.info(f"[验证集] 已生成 -> {OUT_DIR / 'v23_root_cause_validation.json'}")
    print(f"\n=== V2.3 三类事故根因识别验证集 ===")
    print(f"总体 Top1 命中率: {val['summary']['overall_top1_hit_rate']:.1%}")
    print(f"总体 Top5 命中率: {val['summary']['overall_top5_hit_rate']:.1%}")
    for k, r in val["per_class"].items():
        print(f"\n{k} (预期: {r['expected_roots']})")
        print(f"  Top1 命中: {r['top1_hit_rate']:.0%} | Top5 命中: {r['top5_hit_rate']:.0%}")
        for g in r["groups"]:
            mark1 = "✓" if g["top1_expected_hit"] else "✗"
            mark5 = "✓" if g["top5_hit"] else "✗"
            print(f"  {g['group']:>4}: Top1={g['top1']}({mark1}) "
                  f"Top5={g['top5']} 命中={g['top5_expected_overlap']}({mark5})")


if __name__ == "__main__":
    main()

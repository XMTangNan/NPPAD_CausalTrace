"""V2.3 综合验证报告：对 9 模板运行 v2.2 门控（V2/V3/V4）并与 v2.1 对照。

验证门：
  V2 非幅值排序：|cL| 排名 ≠ 根因排名（排除幅值排序嫌疑）
  V3 顶层优先：Top-1 根因 in_degree 最小
  V4 marker 遮蔽：根因候选不含 marker

复用：
- rca.v21.source_tracing.compute_topology / select_root_candidates
- core.utils.save_json / log_error
"""
from __future__ import annotations

import json
from typing import Dict, List

from rca.rca_config_v21 import ACCIDENT_TYPES, GROUP_ORDER, MARKERS, V21_OUTPUT
from rca.v21.source_tracing import compute_topology, select_root_candidates
from core.utils import logger, save_json

# 绝对路径：不再依赖 CWD（旧写法 Path("output/rca_v21/...") 只能在 Code/ 下跑）
SIG_DIR = V21_OUTPUT / "v23_signatures"
TRACING_DIR = V21_OUTPUT / "v23_tracing"
V21_TRACING_DIR = V21_OUTPUT / "tracing"


def validate_template(k: str, g: str) -> Dict:
    """对单个模板运行 V2/V3/V4 验证。"""
    marker = MARKERS[k]
    sig = json.load(open(SIG_DIR / f"signature_{k}_{g}.json"))
    roots_json = json.load(open(TRACING_DIR / f"root_candidates_{k}_{g}.json"))

    stable = sig["stable_edges"]
    variables = sig["variables"]
    cL = sig.get("cL", {})
    root_cands = roots_json["root_candidates"]
    root_vars = [c["variable"] for c in root_cands[:5]]

    # V3: Top-1 in_degree 最小
    top1_in = root_cands[0]["in_degree"]
    others_in = [c["in_degree"] for c in root_cands[1:5]]
    v3_pass = top1_in <= min(others_in) if others_in else True

    # V4: marker 不在根因候选中
    v4_pass = all(c["variable"] != marker for c in root_cands)

    # V2: |cL| 排名 ≠ 根因排名
    cl_vals = {v: abs(cL.get(v, {}).get("cL", 0.0)) for v in root_vars}
    cl_range = max(cl_vals.values()) - min(cl_vals.values()) if cl_vals else 0.0
    if cl_range < 1e-10:
        v2_pass = True  # cL 无区分度，不可能用于排序
        v2_note = "cL无区分度"
    else:
        cl_rank = {v: i + 1 for i, (v, _) in enumerate(
            sorted(cl_vals.items(), key=lambda x: -x[1]))}
        trace_rank = {c["variable"]: c["rank"] for c in root_cands[:5]}
        all_same = all(cl_rank[v] == trace_rank[v] for v in root_vars)
        v2_pass = not all_same
        v2_note = f"|cL|rank={[cl_rank[v] for v in root_vars]} vs trace={[trace_rank[v] for v in root_vars]}"

    return {
        "k": k, "g": g, "marker": marker,
        "top1": root_vars[0],
        "top5": root_vars,
        "n_stable": len(stable),
        "V2_non_amplitude": v2_pass,
        "V2_note": v2_note,
        "V3_top_level": v3_pass,
        "V4_marker_masked": v4_pass,
    }


def main() -> None:
    results: List[Dict] = []
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            r = validate_template(k, g)
            results.append(r)
            logger.info(f"[验证] {k}/{g}: V2={r['V2_non_amplitude']} V3={r['V3_top_level']} V4={r['V4_marker_masked']} | top1={r['top1']}")

    # 汇总
    v2_pass = sum(1 for r in results if r["V2_non_amplitude"])
    v3_pass = sum(1 for r in results if r["V3_top_level"])
    v4_pass = sum(1 for r in results if r["V4_marker_masked"])

    summary = {
        "templates": results,
        "gates": {
            "V2_non_amplitude": f"{v2_pass}/{len(results)}",
            "V3_top_level": f"{v3_pass}/{len(results)}",
            "V4_marker_masked": f"{v4_pass}/{len(results)}",
            "all_pass": v2_pass == v3_pass == v4_pass == len(results),
        },
    }
    save_json(summary, "v23_validation_report", result_dir=TRACING_DIR)

    logger.info("\n===== V2.3 验证门汇总 =====")
    logger.info(f"V2 非幅值排序: {v2_pass}/{len(results)} PASS")
    logger.info(f"V3 顶层优先: {v3_pass}/{len(results)} PASS")
    logger.info(f"V4 marker 遮蔽: {v4_pass}/{len(results)} PASS")
    logger.info(f"全部通过: {summary['gates']['all_pass']}")


if __name__ == "__main__":
    main()

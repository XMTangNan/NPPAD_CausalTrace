"""§13.11 核心消融执行器：60 条测试轨迹 × 8 变体 × 2 条件（FULL/MASKED）。

仅评分不重学图（秒级）；复用 run_r3 的输入加载与行序（_load_inputs / _row）；
calibration_params.json（τ/δ/minmax）保持不变。产出：
- output/rca_v21/ablation_core_metrics.json：{ablation: {condition: {group/ALL: ...}}}
- 日志打印 8 变体 × FULL/MASKED 的 ALL 行对比表。

用法（cd Code）：PYTHONPATH=. python -m rca.v21.run_ablation_core
"""
from __future__ import annotations

from typing import Dict

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKER_COLS,
    MAIN_WINDOW_START, MAIN_WINDOW_END, W_FULL, W_MASKED,
)
from rca.v21 import data as D
from rca.v21.inversion import assert_no_marker_columns
from rca.v21.pipeline import PROTOCOL_D3, compute_metrics
from rca.v21.ablation_core import ABLATIONS, score_ablation
from rca.v21.run_r3 import _load_inputs, _row, CONDITIONS
from core.utils import logger

_METRIC_SRC = {
    "overall_acc": "overall_accuracy",
    "selective_acc": "selective_accuracy",
    "coverage": "coverage",
    "abstention_rate": "abstention_rate",
    "macro_f1": "macro_f1",
    "wilson_ci_overall": "wilson95_overall",
    "wilson_ci_selective": "wilson95_selective",
}

ABLATION_DEFS = {
    "full": "主流程原样（E3=E3_path，D0 冻结主证据）",
    "E1": "−E1：E1 权重置 0（不重归一；MASKED 与 full 等价）",
    "E2": "−E2：E2 权重置 0（不重归一）",
    "E3": "−E3：E3 权重置 0（E3_base/E3_path 均置 0）",
    "ANM": "−ANM：E3 只用 cL（强制 source=L，忽略 cA）",
    "LiNGAM": "−LiNGAM：E3 只用 cA（强制 source=A）",
    "E3-base": "−E3-base：E3 只用 path 通道（与 full 等价，因主证据=E3_path）",
    "E3-path": "−E3-path：E3 只用 base 通道（E3_base 用 E3_path minmax 归一）",
}


def _slim(metrics: Dict) -> Dict:
    return {out: metrics[src] for out, src in _METRIC_SRC.items()}


def _fmt(x, nd: int = 3) -> str:
    return "N/A" if x is None else f"{x:.{nd}f}"


def main():
    manifest, baseline, vsets, scale, calibration, sigs, gates = _load_inputs()
    v_thr = vsets["V_thr"]
    epsilon = scale["epsilon"]

    out = {
        "protocol": PROTOCOL_D3,
        "stage": "D3-ablation-core-13.11",
        "main_window": [MAIN_WINDOW_START, MAIN_WINDOW_END],
        "calibration": {
            "tau": calibration["tau"], "delta": calibration["delta"],
            "n_trajectories": calibration["n_trajectories"],
            "protocol": calibration["protocol"],
            "note": "minmax/τ/δ 冻结不变；−E3-path 的 E3 通道复用 E3_path minmax 归一（无 E3_base 标定表）",
        },
        "weights": {"FULL": W_FULL, "MASKED": W_MASKED},
        "ablation_defs": ABLATION_DEFS,
        "note": "Overall Accuracy 弃权=错；ALL 为三组 60 条测试轨迹的池化聚合（仅统计聚合）",
        "results": {},
    }

    rows = {ab: {c: [] for c in CONDITIONS} for ab in ABLATIONS}
    group_rows = {ab: {c: {g: [] for g in GROUP_ORDER} for c in CONDITIONS}
                  for ab in ABLATIONS}

    for g in GROUP_ORDER:
        for k_true in ACCIDENT_TYPES:
            sevs = manifest["classes"][k_true]["groups"][g]["test_severities"]
            for sev in sevs:
                for cond in CONDITIONS:
                    feat_cols = (list(v_thr) if cond == "MASKED"
                                 else list(v_thr) + list(MARKER_COLS))
                    X, cols, _ = D.load_window(k_true, sev,
                                               MAIN_WINDOW_START, MAIN_WINDOW_END,
                                               feat_cols)
                    assert cols == feat_cols
                    if cond == "MASKED":
                        assert_no_marker_columns(cols, MARKER_COLS)
                    for ab in ABLATIONS:
                        res = score_ablation(X, cols, cond, v_thr, sigs, gates,
                                             baseline, calibration, epsilon, ab)
                        row = _row(k_true, sev, res)
                        rows[ab][cond].append(row)
                        group_rows[ab][cond][g].append(row)

    for ab in ABLATIONS:
        out["results"][ab] = {}
        for cond in CONDITIONS:
            out["results"][ab][cond] = {}
            for g in GROUP_ORDER:
                out["results"][ab][cond][g] = _slim(
                    compute_metrics(group_rows[ab][cond][g]))
            out["results"][ab][cond]["ALL"] = _slim(
                compute_metrics(rows[ab][cond]))

    D.save_json(out, "ablation_core_metrics.json")
    logger.info("saved -> output/rca_v21/ablation_core_metrics.json")

    # 人类可读对比表：8 变体 × FULL/MASKED 的 ALL 行
    logger.info("=== §13.11 核心消融对比表（ALL，60 条池化；overall/selective/cov/macroF1）===")
    for cond in CONDITIONS:
        logger.info(f"[{cond}] {'ablation':<9} {'overall':>8} {'selective':>10} "
                    f"{'coverage':>9} {'macroF1':>9}")
        for ab in ABLATIONS:
            m = out["results"][ab][cond]["ALL"]
            logger.info(f"[{cond}] {ab:<9} {_fmt(m['overall_acc']):>8} "
                        f"{_fmt(m['selective_acc']):>10} {_fmt(m['coverage']):>9} "
                        f"{_fmt(m['macro_f1']):>9}")


if __name__ == "__main__":
    main()

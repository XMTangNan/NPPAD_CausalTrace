"""V23-DESIGN-118：时序证据（数据侧，只读原始 CSV + 公差基线，不参与排序）。

定位
----
解释层的“谁是起点”在本系统里等于“**哪个测点最先异常**”，属数据侧问题，与物理知识
（“谁物理上导致谁”）是两个范畴。本模块只回答三件纯数据的事：

1. **首动时刻**：相对注入时刻、以及相对图上全部上游变量的越限先后；
2. **阶跃形态**：首动后 60s 与首动前 30s 的中位数差是否达到 3σ；
3. **可评估性**：阈值未标定 / σ 不可用 / 分析窗内无越阈，一律如实标“未评估”。

口径（本轮新设计；仓库内无既有首动模块）
------------------------------------------
- 基线：`tolerances/tolerance_{K}.json` 的 `mu` 与 `sigma_robust`
  （`baseline_source` = 注入前 `[0,100]s`，11 点；`baseline_rule` = V23-DATA-004）。
- 首动：`|x(t)-mu|/sigma_robust >= 3` **连续 2 点**，取首个满足条件的 t；
  搜索区间 `[t_inject, main_window[1]]`。
- 阶跃：首动后 60s 中位数与首动前 30s 中位数之差 `>= 3σ`。
- 组级：组内全部轨迹各算一次，取**中位数**；同时记录可评估轨迹数 `n_eval / n_group`。

已知局限（写进产物，不隐藏）
----------------------------
- 3σ 连续 2 点、阶跃 3σ/60s、采样 10s 均为**本轮新设计**，未做显著性检验；
- 基线只有 11 个点，噪声偏大时首动时刻容易误判，需现场复核；
- 饱和通道（`sigma_saturated=true`，注入前恒为 0、σ 由物理下限撑起）仍按 3σ 下限算首动，
  该 3σ 不是实测噪声的 3 倍，产物里逐项披露 `sigma_used / sigma_floor_source`；
- 阶跃窗口固定 60s/30s，短于 3 个采样点则判为不可算（`step=None`）。

边界
----
- 只读：不改排序、不改图、不改门控；本模块不写任何产物，由调用方决定是否落盘。
- 不读任何知识条目；“未评估”只由数据原因产生，不得用知识补。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from causalrca.config.registry import REGISTRY, SEVERITY_BANDS
from causalrca.data import loader as D
from causalrca.io import artifact
from causalrca.io.logging import logger

# 首动 / 阶跃口径常量（改动即改口径，需同步方案与台账）
FIRST_MOTION_Z = 3.0
FIRST_MOTION_NEED = 2
STEP_Z = 3.0
STEP_PRE_S = 30.0
STEP_POST_S = 60.0
EARLY_MARGIN_S = 20.0       # “明确更早”的补充强度标记，不参与档位判定
STEP_FRAC_MIN = 0.5         # 阶跃轨迹占比门槛：达到才算“有阶跃特征”
MIN_STEP_POINTS = 3         # 阶跃窗口少于 3 点判不可算

STATUS_OK = "ok"
STATUS_NOT_CALIBRATED = "not_calibrated"
STATUS_SIGMA_UNAVAILABLE = "sigma_unavailable"
STATUS_NO_CROSSING = "no_crossing"

STATUS_TEXT: Dict[str, str] = {
    STATUS_OK: "可评估",
    STATUS_NOT_CALIBRATED: "tolerance 未标定",
    STATUS_SIGMA_UNAVAILABLE: "sigma 不可用",
    STATUS_NO_CROSSING: "分析窗内无轨迹越阈",
}


def _read_json(path: Path) -> Dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _artifact_json(subdir: str, name: str, run_id: str) -> Dict:
    path = artifact.subdir_path(subdir, run_id) / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"缺 {subdir}/{name}.json（run_id={run_id}）")
    return _read_json(path)


def _load_tolerance(accident_type: str, run_id: str) -> Dict:
    path = artifact.subdir_path("tolerances", run_id) / f"tolerance_{accident_type}.json"
    if not path.is_file():
        raise FileNotFoundError(f"缺 tolerances/tolerance_{accident_type}.json（run_id={run_id}）")
    return _read_json(path)


def first_motion(t: np.ndarray, x: np.ndarray, mu: float, sigma: float,
                 t_inject: float, t_end: float) -> Tuple[Optional[float],
                                                         Optional[bool],
                                                         Optional[float]]:
    """单条轨迹的首动与阶跃。

    返回 `(t_first, is_step, step_sigma)`；分析窗内无越阈时三者均为 None。
    `is_step` 为 None 表示阶跃窗口点数不足、无法判定（不等于“无阶跃”）。
    """
    if sigma is None or sigma <= 0:
        return None, None, None
    z = np.abs(x - mu) / float(sigma)
    inside = np.where((t >= t_inject) & (t <= t_end))[0]
    for i in inside:
        if i + FIRST_MOTION_NEED > len(z):
            break
        if not bool(np.all(z[i:i + FIRST_MOTION_NEED] >= FIRST_MOTION_Z)):
            continue
        pre = x[(t >= t[i] - STEP_PRE_S) & (t < t[i])]
        post = x[(t >= t[i]) & (t <= t[i] + STEP_POST_S)]
        if pre.size < MIN_STEP_POINTS or post.size < MIN_STEP_POINTS:
            return float(t[i]), None, None
        delta = abs(float(np.median(post) - np.median(pre))) / float(sigma)
        return float(t[i]), bool(delta >= STEP_Z), float(delta)
    return None, None, None


def _sampling_interval(frames: Sequence[Dict]) -> float:
    for df in frames:
        if "TIME" not in df.columns or len(df) < 2:
            continue
        dt = np.diff(df["TIME"].to_numpy(float))
        if dt.size:
            return float(np.median(dt))
    return 10.0


def _variable_metrics(variable: str, threshold: Optional[Dict],
                      frames: Sequence[Dict], t_inject: float, t_end: float,
                      n_group: int) -> Dict:
    """单个变量的组级首动指标（轨迹间取中位数）。"""
    if not threshold:
        return {"status": STATUS_NOT_CALIBRATED, "status_text": STATUS_TEXT[STATUS_NOT_CALIBRATED],
                "n_eval": 0, "n_group": n_group}
    sv = threshold.get("sigma_v2") or {}
    sigma = threshold.get("sigma_robust")
    if sigma is None or float(sigma) <= 0:
        reason = sv.get("not_evaluable_reason") or STATUS_TEXT[STATUS_SIGMA_UNAVAILABLE]
        return {"status": STATUS_SIGMA_UNAVAILABLE, "status_text": str(reason),
                "n_eval": 0, "n_group": n_group}

    mu = float(threshold["mu"])
    sigma = float(sigma)
    times: List[float] = []
    steps: List[Optional[bool]] = []
    for df in frames:
        if variable not in df.columns:
            continue
        t = df["TIME"].to_numpy(float)
        x = df[variable].to_numpy(float)
        t_first, is_step, _delta = first_motion(t, x, mu, sigma, t_inject, t_end)
        if t_first is None:
            continue
        times.append(t_first)
        steps.append(is_step)

    if not times:
        return {"status": STATUS_NO_CROSSING, "status_text": STATUS_TEXT[STATUS_NO_CROSSING],
                "n_eval": 0, "n_group": n_group,
                "t_inject": t_inject, "t_end": t_end}

    known = [s for s in steps if s is not None]
    return {
        "status": STATUS_OK,
        "status_text": STATUS_TEXT[STATUS_OK],
        "n_eval": len(times),
        "n_group": n_group,
        "t_first_median": float(np.median(times)),
        "t_first_p10": float(np.percentile(times, 10)),
        "t_first_p90": float(np.percentile(times, 90)),
        "delay_vs_inject_median": float(np.median(times) - t_inject),
        "step_frac": (round(sum(1 for s in known if s) / float(len(known)), 3)
                      if known else None),
        "sigma_used": sigma,
        # 饱和通道（如注入前恒为 0 的破裂流量指示器）的 σ 由物理下限撑起：
        # 首动仍可算，但 3σ 不是实测噪声的 3 倍，需在产物里如实披露
        "sigma_floor_source": str(threshold.get("sigma_floor_source") or "unknown"),
        "sigma_saturated": bool(threshold.get("saturated")),
        "t_inject": t_inject,
        "t_end": t_end,
    }


def compute_temporal(accident_type: str, group: str,
                     run_id: Optional[str] = None) -> Dict:
    """计算一个模板的时序证据（组内全部轨迹），供解释层只读使用。"""
    run_id = run_id or artifact.current_run_id()
    if not run_id:
        raise ValueError("compute_temporal 需要显式 run_id（或先 artifact.bind_run）")
    if accident_type not in REGISTRY:
        raise KeyError(f"未知 accident_type: {accident_type}")
    if group not in SEVERITY_BANDS:
        raise KeyError(f"未知 group: {group}")

    cfg = REGISTRY[accident_type]
    tol = _load_tolerance(accident_type, run_id)
    sig = _artifact_json("signatures", f"signature_{accident_type}_{group}", run_id)
    rc = _artifact_json("tracing", f"root_candidates_{accident_type}_{group}", run_id)

    t_inject = float(tol.get("t_inject", cfg.get("t_inject", 110.0)))
    t_end = float(cfg["main_window"][1])
    lo, hi = SEVERITY_BANDS[group]
    sevs = [s for s in D.list_class_severities(accident_type) if lo <= s <= hi]

    frames = []
    for sev in sevs:
        try:
            frames.append(D.load_trajectory(accident_type, sev))
        except Exception as exc:                      # noqa: BLE001 - 单条缺失不阻断其余轨迹
            logger.warning(f"[D6-temporal] {accident_type}/{sev} 轨迹读取失败：{exc}")
    if not frames:
        raise FileNotFoundError(f"{accident_type}/{group} 无可用轨迹")

    dt = _sampling_interval(frames)
    thresholds = tol.get("thresholds") or {}
    variables = list(sig.get("variables") or [])
    edges = sig.get("stable_edges") or []
    upstream = {v: sorted({e.get("from") for e in edges if e.get("to") == v})
                for v in variables}

    metrics = {v: _variable_metrics(v, thresholds.get(v), frames, t_inject, t_end, len(sevs))
               for v in variables}

    candidates: List[Dict] = []
    for c in (rc.get("root_candidates") or [])[:5]:
        v = c.get("variable")
        ups = upstream.get(v, [])
        up_rows = [{"variable": u,
                    **{k: (metrics.get(u) or {}).get(k) for k in
                       ("status", "status_text", "t_first_median",
                        "delay_vs_inject_median", "step_frac", "n_eval", "n_group")}}
                   for u in ups]
        cm = metrics.get(v) or {}
        ct = cm.get("t_first_median")
        evaluated = [r for r in up_rows if r.get("status") == STATUS_OK
                     and r.get("t_first_median") is not None]
        candidates.append({
            "variable": v,
            "candidate": cm,
            "upstream": up_rows,
            "n_upstream": len(ups),
            "n_upstream_evaluated": len(evaluated),
            "n_upstream_unevaluated": len(ups) - len(evaluated),
            "zero_upstream": len(ups) == 0,
            # 补充强度标记：领先/落后达到 EARLY_MARGIN_S 且自身/上游有阶跃
            "temporal_supports_start": bool(
                cm.get("status") == STATUS_OK and len(ups) > 0
                and len(evaluated) == len(ups)
                and all(r["t_first_median"] >= ct + EARLY_MARGIN_S for r in evaluated)
                and (cm.get("step_frac") or 0.0) >= STEP_FRAC_MIN),
            "temporal_strong_reverse": bool(
                cm.get("status") == STATUS_OK and any(
                    r.get("t_first_median") is not None
                    and ct is not None
                    and r["t_first_median"] <= ct - EARLY_MARGIN_S
                    and (r.get("step_frac") or 0.0) >= STEP_FRAC_MIN
                    for r in evaluated)),
        })

    return {
        "template": f"{accident_type}_{group}",
        "run_id": run_id,
        "accident_type": accident_type,
        "group": group,
        "t_inject": t_inject,
        "t_end": t_end,
        "sampling_interval": dt,
        "n_group": len(sevs),
        "n_trajectories_loaded": len(frames),
        "definition": {
            "baseline": "tolerances/tolerance_{K}.json 的 mu / sigma_robust"
                        "（注入前 [0,100]s，11 点；V23-DATA-004）",
            "first_motion": "|x-mu|/sigma_robust >= 3 连续 2 点，"
                            "取首个 t ∈ [t_inject, main_window[1]]",
            "step": "首动后 60s 中位数与前 30s 中位数之差 >= 3σ",
            "group": "组内全部轨迹各算一次后取中位数；n_eval 为可评估轨迹数",
            "sampling_interval": "由原始 CSV 的 TIME 列中位步长现算",
            "status": "not_calibrated / sigma_unavailable / no_crossing 均为“未评估”，"
                      "只由数据原因产生",
            "provenance": "本轮新设计（V23-DESIGN-118）；只读 registry.data_dir 原始 CSV "
                          "与同 run 的 tolerance，不重跑因果",
        },
        "limitations": [
            "3σ 连续 2 点、阶跃 3σ/60s、采样间隔均由本轮新设计给定，未做显著性检验",
            "基线只有 11 个点，噪声偏大时首动时刻易误判，需现场复核",
            "阶跃窗口固定（前 30s / 后 60s），点数不足 3 时判为不可算（step_frac=null）",
            "组内取中位数会掩盖轨迹间分歧；n_eval/n_group 一并给出，供判断代表性",
            "饱和通道（sigma_saturated=true）的 σ 由物理下限撑起，首动仍按 3σ 下限计算，"
            "但该 3σ 不是实测噪声的 3 倍，结论需现场复核",
        ],
        "variables": metrics,
        "candidates": candidates,
    }

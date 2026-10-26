"""轨迹级 marker 效应列 C^A（13.5.2 + D1 修正 RCA-030/031）。

为什么需要：NPPAD 的 marker 是 t=110s 瞬时阶跃、主窗内分段恒定；
主窗学图使 LiNGAM 系统性把症状→marker 的伪方向（残差近零）判优，
含瞬态学图虽恢复边的存在性与拓扑，但同期系数对滞后响应变量（P/WHPI/功率）
产生时序伪反号。因此 marker→症状的"符号与量级"以轨迹级稳态响应回归（C^A）
为主证据：每条训练轨迹一个点 (x=主窗 marker z 中位, y=主窗症状 z 中位)，
GAM 在训练分布中位数处局部线性化，50 次轨迹 Bootstrap 给方向稳定率 πA。
拓扑（frontier/稳定边）仍由扩展窗 LiNGAM 提供。
"""
from __future__ import annotations

import numpy as np
from typing import Dict, List, Sequence, Tuple

from rca.rca_config_v21 import (
    GAM_MARKER_N_BOOTSTRAP, GAM_N_SPLINES, GAM_LAM, MARKER_X_SPREAD_MIN,
)
from core.utils import logger


def trajectory_steady_pairs(blocks_main: List[np.ndarray], gvars: Sequence[str],
                            marker: str, v_thr: Sequence[str], mu: Dict[str, float],
                            sigma: Dict[str, float], median_platform: float
                            ) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """每条轨迹一个稳态点：x=主窗 marker z 中位；Y=主窗 V_thr 症状 z 中位。"""
    mp = {v: p for p, v in enumerate(gvars)}
    x = np.zeros(len(blocks_main))
    Y = np.zeros((len(blocks_main), len(v_thr)))
    for i, X in enumerate(blocks_main):
        # marker 主窗中位（原始量纲 / 平台尺度）
        x[i] = float(np.median(X[:, mp[marker]]) / median_platform)
        for j, v in enumerate(v_thr):
            z = (X[:, mp[v]] - mu[v]) / sigma[v]
            Y[i, j] = float(np.median(z))
    return x, Y, list(v_thr)


def _local_slope(x: np.ndarray, y: np.ndarray, x0: float) -> float:
    """GAM 在 x0（训练中位）处的局部斜率；GAM 失败回退 OLS。"""
    from pygam import LinearGAM, s
    try:
        gam = LinearGAM(s(0, n_splines=GAM_N_SPLINES, lam=GAM_LAM)).fit(x.reshape(-1, 1), y)
        h = max(float(np.std(x)) * 0.05, 1e-6)
        return float((gam.predict(np.array([[x0 + h]])) -
                      gam.predict(np.array([[x0 - h]])))[0] / (2 * h))
    except Exception:
        if np.std(x) < 1e-12:
            return 0.0
        return float(np.polyfit(x, y, 1)[0])


def marker_ca_column(x: np.ndarray, Y: np.ndarray, symptoms: Sequence[str],
                     n_bootstrap: int = GAM_MARKER_N_BOOTSTRAP, seed: int = 42,
                     full_sample: bool = False
                     ) -> Dict[str, Dict]:
    """逐症状 GAM marker 列 + 轨迹 Bootstrap 方向稳定率 piA。

    x spread < MARKER_X_SPREAD_MIN 的模板整列标 not_estimable（πA=0）。
    full_sample=True（D3 留一标定单拟合）：不做有放回重采样，全样本单次斜率，
    无稳定性信息故 πA 取 1（单图口径），并标 single_fit。
    """
    n = len(x)
    spread = float(np.quantile(x, 0.9) - np.quantile(x, 0.1))
    out: Dict[str, Dict] = {}
    if spread < MARKER_X_SPREAD_MIN:
        for v in symptoms:
            out[v] = {"cA": 0.0, "piA": 0.0, "estimable": False,
                      "spread": spread, "n": n}
        logger.info(f"[C^A] 组内 marker z spread={spread:.3f} < {MARKER_X_SPREAD_MIN}，整列不可估")
        return out
    x0_full = float(np.median(x))
    if full_sample:
        for j, v in enumerate(symptoms):
            slope = 0.0 if np.std(Y[:, j]) < 1e-12 else _local_slope(x, Y[:, j], x0_full)
            out[v] = {"cA": float(slope), "piA": 1.0 if abs(slope) > 1e-10 else 0.0,
                      "estimable": abs(slope) > 1e-10, "single_fit": True,
                      "spread": spread, "n": n}
        return out

    rng = np.random.default_rng(seed)

    slopes = np.zeros((n_bootstrap, Y.shape[1]))
    for b in range(n_bootstrap):
        idx = rng.integers(0, n, size=n)
        xb, yb = x[idx], Y[idx]
        x0 = float(np.median(xb))
        for j in range(Y.shape[1]):
            if np.std(yb[:, j]) < 1e-12:
                slopes[b, j] = 0.0
            else:
                slopes[b, j] = _local_slope(xb, yb[:, j], x0)
    # 全量 cA（训练中位处）
    x0_full = float(np.median(x))
    for j, v in enumerate(symptoms):
        col = slopes[:, j]
        nz = col[np.abs(col) > 1e-10]
        pos, neg = int((col > 1e-10).sum()), int((col < -1e-10).sum())
        pi = max(pos, neg) / n_bootstrap
        out[v] = {"cA": float(_local_slope(x, Y[:, j], x0_full)),
                  "piA": float(pi), "estimable": True,
                  "n_pos": pos, "n_neg": neg, "spread": spread, "n": n}
    return out


def effective_marker_map(cA: Dict[str, Dict], cL: Dict[str, Dict],
                         pi_thr: float = 0.70) -> Dict[str, Dict]:
    """E3 评分用 marker 效应：C^A 优先（πA≥阈），LiNGAM 总效应列兜底，记录来源。"""
    eff = {}
    for v, infoA in cA.items():
        infoL = cL.get(v)
        if infoA.get("estimable") and infoA["piA"] >= pi_thr:
            eff[v] = {"c": infoA["cA"], "pi": infoA["piA"], "source": "A"}
        elif infoL is not None and infoL["piL"] >= pi_thr:
            eff[v] = {"c": infoL["cL"], "pi": infoL["piL"], "source": "L"}
        else:
            # 两引擎都不稳：取可用者但 π 给低权重（进分母但权重小），并标 none
            if infoA.get("estimable"):
                eff[v] = {"c": infoA["cA"], "pi": infoA["piA"], "source": "A_unstable"}
            elif infoL is not None:
                eff[v] = {"c": infoL["cL"], "pi": infoL["piL"], "source": "L_unstable"}
    return eff

"""R0 数据冻结层：加载 / 划分 / 审计 / 三变量表 / 过程本体共享基线（13.3）。

铁律：
- 测试侧只用本层产出的冻结参数（mu/sigma、变量表、manifest），禁止自标准化、禁止学图；
- sigma≈0 变量跳过并落盘，绝不数值兜底；
- marker 与 WHPI/WECS 不进过程本体 mu/sigma 池。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from rca.rca_config_v21 import (
    REPO_DATA, ACCIDENT_TYPES, MARKERS, MARKER_COLS, NON_PILOT_MARKERS,
    SAFETY_STEP_VARS, PHYSICAL_CANDIDATES, CANDIDATE_UNION, TARGET_CANDIDATES,
    SEVERITY_BANDS, GROUP_ORDER, TEST_QUOTA, BASELINE_SEVERITIES, SPLIT_SEED,
    MAIN_WINDOW_START, MAIN_WINDOW_END, BASELINE_WINDOW_START, BASELINE_WINDOW_END,
    CONSTANT_SIGMA, MARKER_INJECT_TOL, V21_OUTPUT,
)
from core.utils import logger


# ==================== 加载 ====================
def csv_path(accident_type: str, severity: int) -> Path:
    return REPO_DATA / accident_type / f"{severity}.csv"


def load_trajectory(accident_type: str, severity: int) -> pd.DataFrame:
    df = pd.read_csv(csv_path(accident_type, severity))
    if "TIME" not in df.columns:
        raise ValueError(f"{accident_type}/{severity}.csv 缺 TIME 列")
    return df


def window_slice(df: pd.DataFrame, start: float, end: float
                 ) -> pd.DataFrame:
    m = (df["TIME"] >= start - 1e-9) & (df["TIME"] <= end + 1e-9)
    return df.loc[m].reset_index(drop=True)


def load_window(accident_type: str, severity: int, start: float, end: float,
                columns: Optional[Sequence[str]] = None
                ) -> Tuple[np.ndarray, List[str], np.ndarray]:
    """返回 (X[T,P], 列名, time[T])；列缺失立即报错（禁止臆造字段）。"""
    df = window_slice(load_trajectory(accident_type, severity), start, end)
    cols = list(columns) if columns is not None else [c for c in df.columns if c != "TIME"]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(f"{accident_type}/{severity}.csv 缺列: {missing}")
    return df[cols].to_numpy(dtype=float), cols, df["TIME"].to_numpy(dtype=float)


# ==================== 划分（13.3.1）====================
def build_split_manifest(seed: int = SPLIT_SEED) -> Dict:
    """sev-1 固定基线源（不进组）；每组从严重度区间随机抽配额为测试，其余进训练模板。"""
    rng = np.random.default_rng(seed)
    manifest = {"seed": seed,
                "main_window": [MAIN_WINDOW_START, MAIN_WINDOW_END],
                "baseline_window": [BASELINE_WINDOW_START, BASELINE_WINDOW_END],
                "classes": {}}
    for k in ACCIDENT_TYPES:
        cls = {"marker": MARKERS[k], "baseline_severities": list(BASELINE_SEVERITIES),
               "groups": {}}
        for g in GROUP_ORDER:
            lo, hi = SEVERITY_BANDS[g]
            pool = list(range(lo, hi + 1))
            quota = TEST_QUOTA[g]
            picked = sorted(map(int, rng.choice(pool, size=quota, replace=False)))
            test_set = set(picked)
            train = [s for s in pool if s not in test_set]
            cls["groups"][g] = {"severity_band": [lo, hi],
                                "test_severities": picked,
                                "train_template_severities": train}
        # 零交集 / 配额 / sev-1 隔离断言
        all_test, all_train = [], []
        for g in GROUP_ORDER:
            all_test += cls["groups"][g]["test_severities"]
            all_train += cls["groups"][g]["train_template_severities"]
        assert len(all_test) == sum(TEST_QUOTA.values()) == 20
        assert set(all_test).isdisjoint(all_train)
        assert 1 not in all_test and 1 not in all_train
        assert set(all_test) | set(all_train) == set(range(2, 101))
        cls["n_test"] = len(all_test)
        cls["n_train_template"] = len(all_train)
        manifest["classes"][k] = cls
    manifest["normal_validation"] = "Normal/1.csv（不进训练/标定，仅弃权功能验证）"
    return manifest


def save_json(obj: Dict, name: str, out_dir: Path = V21_OUTPUT) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    return path


def load_json(name: str, out_dir: Path = V21_OUTPUT) -> Dict:
    with open(Path(out_dir) / name, encoding="utf-8") as f:
        return json.load(f)


# ==================== R0 审计（13.3）====================
def audit_r0(manifest: Dict) -> Dict:
    audit: Dict = {"checks": [], "fail": []}

    def check(name: str, ok: bool, detail: Dict):
        audit["checks"].append({"name": name, "pass": bool(ok), "detail": detail})
        if not ok:
            audit["fail"].append(name)

    # C0 列存在性（23 候选 + target 候选），跨三类各抽 1/50/100
    probe_sevs = [1, 50, 100]
    cols_ok, col_detail = True, {}
    for k in ACCIDENT_TYPES:
        df_cols = set(load_trajectory(k, 1).columns)
        need = set(CANDIDATE_UNION) | set(TARGET_CANDIDATES[k])
        miss = sorted(need - df_cols)
        col_detail[k] = {"n_cols_total": len(df_cols) + 1, "missing": miss}
        cols_ok &= not miss
    check("C0_candidate_columns_exist", cols_ok, col_detail)

    # C1 marker one-hot 互斥 + 注入时刻 + 主窗平台非零（全 300 文件）
    onehot_detail = {"violations": [], "inject_time_off": [], "zero_platform": []}
    n_rows_min = {k: 10**9 for k in ACCIDENT_TYPES}
    for k in ACCIDENT_TYPES:
        m = MARKERS[k]
        others = [c for c in MARKER_COLS if c != m]
        for sev in range(1, 101):
            df = load_trajectory(k, sev)
            n_rows_min[k] = min(n_rows_min[k], len(df))
            if float(df[others].abs().to_numpy().max()) > MARKER_INJECT_TOL:
                onehot_detail["violations"].append(f"{k}/{sev}")
            w = df[m].to_numpy()
            nz = np.argmax(np.abs(w) > MARKER_INJECT_TOL)
            if abs(df["TIME"].to_numpy()[nz] - 110.0) > 1e-6:
                onehot_detail["inject_time_off"].append(
                    f"{k}/{sev}: t={df['TIME'].to_numpy()[nz]}")
            main = window_slice(df, MAIN_WINDOW_START, MAIN_WINDOW_END)
            if abs(float(main[m].median())) <= MARKER_INJECT_TOL:
                onehot_detail["zero_platform"].append(f"{k}/{sev}")
    check("C1_marker_onehot_inject_110",
          not (onehot_detail["violations"] or onehot_detail["inject_time_off"]
               or onehot_detail["zero_platform"]),
          onehot_detail)

    # C2 窗口覆盖：主窗 300-1500（t 末点>=1500）；sev-1 基线窗 520-2030
    win_detail = {"main_short": [], "baseline_short": []}
    for k in ACCIDENT_TYPES:
        for sev in range(1, 101):
            tlast = float(load_trajectory(k, sev)["TIME"].iloc[-1])
            if tlast < MAIN_WINDOW_END - 1e-9:
                win_detail["main_short"].append(f"{k}/{sev}(t_last={tlast})")
        for sev in BASELINE_SEVERITIES:
            tlast = float(load_trajectory(k, sev)["TIME"].iloc[-1])
            if tlast < BASELINE_WINDOW_END - 1e-9:
                win_detail["baseline_short"].append(f"{k}/{sev}(t_last={tlast})")
    check("C2_window_coverage",
          not win_detail["main_short"] and not win_detail["baseline_short"],
          {**win_detail, "n_rows_min": n_rows_min})

    # C3 划分复核（独立重算，不信任 build 时断言）：配额、零交集、sev-1 隔离、全集覆盖
    split_ok, split_detail = True, {}
    for k in ACCIDENT_TYPES:
        test, train = [], []
        for g in GROUP_ORDER:
            gi = manifest["classes"][k]["groups"][g]
            t, tr = gi["test_severities"], gi["train_template_severities"]
            lo, hi = gi["severity_band"]
            inband = all(lo <= s <= hi for s in t) and all(lo <= s <= hi for s in tr)
            split_ok &= len(t) == TEST_QUOTA[g] and inband
            test += t
            train += tr
        split_ok &= (len(test) == 20 and set(test).isdisjoint(train)
                     and 1 not in test and 1 not in train
                     and set(test) | set(train) == set(range(2, 101)))
        split_detail[k] = {"n_test": len(test), "n_train": len(train)}
    check("C3_split_quota_disjoint", split_ok,
          {**split_detail, "quota_per_class": 20, "seed": manifest["seed"]})

    audit["passed"] = not audit["fail"]
    return audit


# ==================== 过程本体共享基线（13.4-2）====================
def fit_shared_baseline() -> Tuple[Dict, Dict, List[Dict]]:
    """三类 sev-1 恢复段 [520,2030] 池化（不含 marker/WHPI/WECS），估计 mu/sigma。"""
    blocks = []
    for k in ACCIDENT_TYPES:
        for sev in BASELINE_SEVERITIES:
            X, cols, t = load_window(k, sev, BASELINE_WINDOW_START, BASELINE_WINDOW_END,
                                     PHYSICAL_CANDIDATES)
            assert len(t) == 152, (k, sev, len(t))
            blocks.append(X)
    pooled = np.vstack(blocks)
    assert pooled.shape[1] == len(PHYSICAL_CANDIDATES)
    mu, sigma = pooled.mean(axis=0), pooled.std(axis=0)
    keep_mu, keep_sigma, excluded = {}, {}, []
    for i, v in enumerate(PHYSICAL_CANDIDATES):
        if sigma[i] < CONSTANT_SIGMA:
            excluded.append({"variable": v, "sigma": float(sigma[i]),
                             "reason": "基线窗sigma≈0，跳过不建阈值，禁止数值兜底"})
        else:
            keep_mu[v] = float(mu[i])
            keep_sigma[v] = float(sigma[i])
    # 描述性紧致性（不改 V_thr，仅 D0 前证据落盘）：σ 绝对值与 σ/|μ|
    tightness = {v: {"sigma": keep_sigma[v], "mu": keep_mu[v],
                     "cv_abs": (keep_sigma[v] / abs(keep_mu[v])) if abs(keep_mu[v]) > 1e-12 else None}
                 for v in keep_mu}
    baseline = {
        "window": [BASELINE_WINDOW_START, BASELINE_WINDOW_END],
        "source": [f"{k}/sev{s} 恢复段" for k in ACCIDENT_TYPES for s in BASELINE_SEVERITIES],
        "n_pooled_points": int(pooled.shape[0]),
        "mu": keep_mu, "sigma": keep_sigma,
        "tightness_descriptive": tightness,
        "excluded_constant": excluded,
        "never_in_pool": {"marker": MARKER_COLS, "safety_step": SAFETY_STEP_VARS},
        "baseline_note": "仿真稳态 σ 极小（如 TAVG σ≈0.015℃），真实故障可产生 |z|≫3；"
                         "A(x) 可能接近全集，属已知特性，D3 如实报告；−过程本体（MAD）消融作稳健对照",
    }
    v_thr = sorted(keep_mu, key=PHYSICAL_CANDIDATES.index)
    return baseline, {"V_thr": v_thr}, excluded


def variable_sets(v_thr: Sequence[str]) -> Dict:
    """三张变量表：V_thr / V_graph,k（含 marker 与安注）/ V_act（=V_thr 物理症状）。"""
    sets = {"V_thr": list(v_thr), "V_act": list(v_thr),
            "V_graph": {},
            "note": "V_thr=过程本体可建阈值的物理症状；V_graph,k=V_thr+安注阶跃+本类marker；"
                    "V_act=E3 分母候选（非 marker），与 V_thr 相同；marker/WHPI/WECS 不进 V_thr"}
    for k in ACCIDENT_TYPES:
        sets["V_graph"][k] = list(v_thr) + list(SAFETY_STEP_VARS) + [MARKERS[k]]
    return sets


def marker_platform_scale(manifest: Dict) -> Dict[str, float]:
    """median_platform_k = 全部训练轨迹主窗 marker 中位数 的中位数（z_marker 尺度，13.5.1）。"""
    out = {}
    for k in ACCIDENT_TYPES:
        sevs = [s for g in GROUP_ORDER
                for s in manifest["classes"][k]["groups"][g]["train_template_severities"]]
        vals = []
        for sev in sevs:
            df = window_slice(load_trajectory(k, sev), MAIN_WINDOW_START, MAIN_WINDOW_END)
            vals.append(float(df[MARKERS[k]].median()))
        out[k] = float(np.median(vals))
    return out


def epsilon_by_class(manifest: Dict) -> Dict[str, float]:
    """epsilon_k = 0.1 × min_训练轨迹(主窗 marker 平台中位数)，按类各算（13.7.2）。"""
    out = {}
    for k in ACCIDENT_TYPES:
        sevs = [s for g in GROUP_ORDER
                for s in manifest["classes"][k]["groups"][g]["train_template_severities"]]
        vals = []
        for sev in sevs:
            df = window_slice(load_trajectory(k, sev), MAIN_WINDOW_START, MAIN_WINDOW_END)
            vals.append(float(df[MARKERS[k]].median()))
        out[k] = 0.1 * float(min(vals))
    return out

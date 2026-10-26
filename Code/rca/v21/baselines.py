"""方案 v2.1 §13.9 三个冻结基线：B0 marker 规则 / B1 Pearson 最近类 / B2 XGBoost 窗口统计量。

口径冻结（不自由发挥）：
- B0（仅 FULL）：测试主窗三 marker 列有符号中位数 v_k，激活判据严格 |v_k| > ε_k
  （ε 取自 output/rca_v21/marker_scale.json，文件实际键名为 "epsilon"，按类各算）。
  唯一激活 marker 对应类为预测；0 个或 >1 个激活 -> 预测 None（弃权语义，单独计数，
  不强行猜类；指标分母含弃权且弃权计错）。MASKED 条件 B0=N/A（null，不是 0）。
- B1：d_x = 主窗 V_thr 序有符号中位 z（共享 μ/σ），与 9 个组模式 template_d 算
  Pearson（复用 inversion.pearson_corr；std≈0 时该函数返回 0.0），B1(k)=max_g corr，
  argmax_k 为预测，无弃权。FULL/MASKED 输入相同（V_thr 不含 marker）。
- B2：每轨迹 102 维特征（17 个 V_thr 变量 × 6 统计量），严禁严重度/文件名/marker 列。
  训练集 = 9 组合并的全部 train_template_severities（79×3=237 条），标签=类，
  XGBClassifier(**XGB_PARAMS)；FULL/MASKED 特征定义相同，各训练一版，预测必然一致
  （同数据/同超参/同 random_state）。
- B2 选择性预测退化曲线：按 max predict_proba 降序，coverage∈{.1,...,.9}，
  接受数取整规则 n_keep=ceil(coverage*N)（至少 1）。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from rca.rca_config_v21 import (
    ACCIDENT_TYPES, GROUP_ORDER, MARKERS, MARKER_COLS,
    MAIN_WINDOW_START, MAIN_WINDOW_END, ANOMALY_K, XGB_PARAMS, GLOBAL_SEED,
)
from rca.v21 import data as D
from rca.v21.inversion import (
    pearson_corr, signed_median_z, assert_no_marker_columns,
)
from core.utils import logger

# ==================== 后端：优先 xgboost，失败兜底 sklearn GBDT ====================
try:
    import xgboost
    from xgboost import XGBClassifier
    HAS_XGBOOST = True
    BACKEND_NAME = "xgboost.XGBClassifier"
    BACKEND_VERSION = xgboost.__version__
except Exception as _xgb_err:  # pragma: no cover - 仅在 xgboost 缺失时走到
    import sklearn
    from sklearn.ensemble import GradientBoostingClassifier
    HAS_XGBOOST = False
    BACKEND_NAME = "sklearn.GradientBoostingClassifier(fallback)"
    BACKEND_VERSION = sklearn.__version__
    _XGB_IMPORT_ERROR = repr(_xgb_err)

FALLBACK_NOTE = (
    "import xgboost 失败，按 §13.9 预注册兜底改用 sklearn "
    "GradientBoostingClassifier(max_depth=3,n_estimators=100,learning_rate=0.1,"
    "random_state=42)；该实现非 xgboost，数值结果不可与 xgboost 版直接等同"
)


# ==================== B0：marker 非零规则 ====================
def b0_decide(marker_medians: Dict[str, float],
              epsilon_by_class: Dict[str, float],
              classes: Sequence[str] = ACCIDENT_TYPES
              ) -> Tuple[Optional[str], List[str]]:
    """B0 纯判据。

    marker_medians: {类名 k: 该类 marker 列在测试主窗的有符号中位数 v_k}。
    激活判据：严格 |v_k| > ε_k（|v|==ε 不激活）。
    返回 (pred, activated)：恰好 1 个激活 -> pred=该类；否则 None（弃权）。
    """
    activated = [k for k in classes
                 if abs(float(marker_medians[k])) > float(epsilon_by_class[k])]
    pred = activated[0] if len(activated) == 1 else None
    return pred, activated


def load_marker_medians(accident_type: str, severity: int
                        ) -> Tuple[Dict[str, float], Dict[str, float]]:
    """加载测试主窗三 marker 列，返回 ({类: 有符号中位数}, {marker 列: 有符号中位数})。

    FULL 条件专用（MASKED 下禁止调用）；one-hot 互斥由 R0 C1 审计保证。
    """
    block, cols, _ = D.load_window(accident_type, severity,
                                   MAIN_WINDOW_START, MAIN_WINDOW_END,
                                   list(MARKER_COLS))
    assert cols == list(MARKER_COLS)
    med_by_col = {c: float(v) for c, v in zip(cols, np.median(block, axis=0))}
    by_class = {k: med_by_col[MARKERS[k]] for k in ACCIDENT_TYPES}
    return by_class, med_by_col


# ==================== 共享 z 变换 / B1 ====================
def z_matrix(block: np.ndarray, cols: Sequence[str], v_thr: Sequence[str],
             baseline: Dict) -> np.ndarray:
    """主窗原始块 -> V_thr 序 z 矩阵[T,P]；只用共享 μ/σ（禁止自标准化）。"""
    pos = {v: p for p, v in enumerate(cols)}
    mu, sigma = baseline["mu"], baseline["sigma"]
    Z = np.empty((block.shape[0], len(v_thr)), dtype=float)
    for j, v in enumerate(v_thr):
        Z[:, j] = (block[:, pos[v]] - float(mu[v])) / float(sigma[v])
    return Z


def signed_median_vthr(block: np.ndarray, cols: Sequence[str],
                       v_thr: Sequence[str], baseline: Dict) -> np.ndarray:
    """d_x = V_thr 序有符号中位 z（主窗，共享 μ/σ）。"""
    return signed_median_z(z_matrix(block, cols, v_thr, baseline))


def load_b1_templates(v_thr: Sequence[str]) -> Dict[Tuple[str, str], np.ndarray]:
    """读 9 个签名 group_pattern.template_d（V_thr 序组中位 z），延迟导入避免循环依赖。"""
    from rca.v21.signatures import load_signature
    templates: Dict[Tuple[str, str], np.ndarray] = {}
    for k in ACCIDENT_TYPES:
        for g in GROUP_ORDER:
            sig = load_signature(k, g)
            if sig is None:
                raise RuntimeError(f"签名缺失 signature_{k}_{g}.json，先完成 run_r1")
            gp = sig["group_pattern"]
            if list(gp["variables"]) != list(v_thr):
                raise ValueError(f"{k}_{g} group_pattern 变量序与 V_thr 不一致")
            templates[(k, g)] = np.asarray(gp["template_d"], dtype=float)
    return templates


def b1_scores(d_vthr: np.ndarray,
              templates: Dict[Tuple[str, str], np.ndarray],
              classes: Sequence[str] = ACCIDENT_TYPES,
              groups: Sequence[str] = GROUP_ORDER
              ) -> Tuple[str, Dict[str, float], Dict[str, float]]:
    """B1：与 9 模板 Pearson；B1(k)=max_g corr；argmax_k 预测（无弃权）。

    返回 (pred, by_class{k: max_g corr}, per_template{"k_g": corr})。
    """
    per_template = {f"{k}_{g}": float(pearson_corr(d_vthr, templates[(k, g)]))
                    for k in classes for g in groups}
    by_class = {k: max(per_template[f"{k}_{g}"] for g in groups) for k in classes}
    pred = max(classes, key=lambda k: by_class[k])  # 平分取 classes 序在先者
    return pred, by_class, per_template


# ==================== B2：102 维窗口统计量 ====================
# 每变量 6 个统计量（变量优先排布）；前 5 个在共享 μ/σ 的 z 空间，第 6 个为原始单位 Δ
B2_STATS: Tuple[str, ...] = (
    "mean_z", "std_z", "min_z", "max_z", "overlimit_rate",
    "mean_baseline_delta",
)


def b2_feature_names(v_thr: Sequence[str]) -> List[str]:
    """102 个列名（变量优先：v__stat）；不含任何 marker 列名。"""
    return [f"{v}__{s}" for v in v_thr for s in B2_STATS]


def compute_b2_features(block: np.ndarray, cols: Sequence[str],
                        v_thr: Sequence[str], baseline: Dict
                        ) -> Tuple[np.ndarray, List[str]]:
    """单条轨迹主窗 -> 102 维特征。

    特征定义（§13.9 冻结，17 个 V_thr 变量 × 6 统计量；z 一律用共享 μ/σ）：
      1) mean_z              = mean_t z_j(t)
      2) std_z               = std_t z_j(t)（ddof=0，与共享 σ 估计口径一致）
      3) min_z / 4) max_z    = 主窗 z 极值
      5) overlimit_rate      = |z_j|>ANOMALY_K(=3) 的时间点占比
      6) mean_baseline_delta = mean_t x_j(t) − μ_j（原始工程单位；
                               与 mean_z 仅差正系数 σ_j，对树模型分裂序等价，
                               按冻结口径单列为第 6 特征）
    禁止入模：severity/文件名等元信息、marker 列（WLR/WTRA/WFLB）、安注阶跃列。
    """
    Z = z_matrix(block, cols, v_thr, baseline)
    mu_vec = np.array([float(baseline["mu"][v]) for v in v_thr])
    pos = {v: p for p, v in enumerate(cols)}
    raw_mean = np.array([float(block[:, pos[v]].mean()) for v in v_thr])
    mean_z = Z.mean(axis=0)
    feats = np.empty(len(v_thr) * len(B2_STATS), dtype=float)
    for j in range(len(v_thr)):
        feats[6 * j:6 * j + 6] = (
            mean_z[j],
            Z[:, j].std(ddof=0),
            Z[:, j].min(),
            Z[:, j].max(),
            float((np.abs(Z[:, j]) > ANOMALY_K).mean()),
            raw_mean[j] - mu_vec[j],
        )
    names = b2_feature_names(v_thr)
    return feats, names


def build_b2_train_matrix(manifest: Dict, v_thr: Sequence[str], baseline: Dict
                          ) -> Tuple[np.ndarray, List[str], List[Tuple[str, int]]]:
    """9 组合并全部 train_template_severities（237 条）-> X[237,102]、标签、meta。

    训练特征列同样过 MASKED 零访问断言（V_thr 本不含 marker）。
    """
    rows: List[np.ndarray] = []
    labels: List[str] = []
    meta: List[Tuple[str, int]] = []
    expected_names = b2_feature_names(v_thr)
    for k in ACCIDENT_TYPES:
        sevs = [s for g in GROUP_ORDER
                for s in manifest["classes"][k]["groups"][g]["train_template_severities"]]
        for sev in sevs:
            block, cols, _ = D.load_window(k, sev, MAIN_WINDOW_START, MAIN_WINDOW_END,
                                           list(v_thr))
            assert cols == list(v_thr)
            assert_no_marker_columns(cols, MARKER_COLS)
            feats, names = compute_b2_features(block, cols, v_thr, baseline)
            assert names == expected_names
            rows.append(feats)
            labels.append(k)
            meta.append((k, int(sev)))
    return np.vstack(rows), labels, meta


def encode_labels(y: Sequence[str], classes: Sequence[str] = ACCIDENT_TYPES
                  ) -> np.ndarray:
    """字符串类名 -> 固定整数编码（classes 序；xgboost 3.x 不接受字符串 y）。"""
    mapping = {k: i for i, k in enumerate(classes)}
    return np.array([mapping[v] for v in y], dtype=int)


def _make_classifier():
    """XGBClassifier(**XGB_PARAMS)；缺失时等价深度=3/100 树/lr=.1 的 sklearn 兜底。"""
    if HAS_XGBOOST:
        return XGBClassifier(**XGB_PARAMS)
    return GradientBoostingClassifier(
        max_depth=XGB_PARAMS["max_depth"],
        n_estimators=XGB_PARAMS["n_estimators"],
        learning_rate=XGB_PARAMS["learning_rate"],
        random_state=XGB_PARAMS["random_state"],
    )


def fit_b2(X: np.ndarray, y: Sequence[str], feature_names: Sequence[str],
           classes: Sequence[str] = ACCIDENT_TYPES) -> Dict:
    """训练一版 B2；返回模型包（clf/classes/backend/版本/参数/兜底声明）。"""
    clf = _make_classifier()
    clf.fit(X, encode_labels(y, classes))
    return {
        "clf": clf,
        "classes": list(classes),
        "feature_names": list(feature_names),
        "backend": BACKEND_NAME,
        "backend_version": BACKEND_VERSION,
        "params": dict(XGB_PARAMS),
        "fallback_note": None if HAS_XGBOOST else FALLBACK_NOTE,
        "n_train": int(len(y)),
    }


def predict_b2(model: Dict, X: np.ndarray
               ) -> Tuple[List[str], np.ndarray, np.ndarray]:
    """返回 (pred 类名, max proba 置信度, proba[N,K]，列序=model['classes'])。"""
    proba = np.asarray(model["clf"].predict_proba(X), dtype=float)
    classes = model["classes"]
    if proba.shape[1] != len(classes):
        raise RuntimeError(f"proba 列数 {proba.shape[1]} != 类数 {len(classes)}")
    idx = np.argmax(proba, axis=1)
    preds = [classes[i] for i in idx]
    return preds, proba.max(axis=1), proba


def b2_cv5_accuracy(X: np.ndarray, y: Sequence[str],
                    classes: Sequence[str] = ACCIDENT_TYPES,
                    seed: int = GLOBAL_SEED
                    ) -> Tuple[float, List[float]]:
    """训练集 5 折 StratifiedKFold（shuffle，random_state=42）CV 准确率。

    每折独立新分类器（XGB_PARAMS 冻结），返回 (mean, 逐折准确率)。
    """
    from sklearn.model_selection import StratifiedKFold
    yi = encode_labels(y, classes)
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    folds: List[float] = []
    for tr_idx, te_idx in skf.split(X, yi):
        clf = _make_classifier()
        clf.fit(X[tr_idx], yi[tr_idx])
        pred = clf.predict(X[te_idx])
        folds.append(float(np.mean(np.asarray(pred) == yi[te_idx])))
    return float(np.mean(folds)), folds


# ==================== B2 选择性预测退化曲线 ====================
COVERAGES: Tuple[float, ...] = tuple(round(0.1 * i, 1) for i in range(1, 10))


def b2_coverage_curve(proba: np.ndarray, y_true: Sequence[str],
                      classes: Sequence[str] = ACCIDENT_TYPES,
                      coverages: Sequence[float] = COVERAGES) -> List[Dict]:
    """按 max proba 置信度降序的 selective accuracy 曲线。

    取整规则（冻结）：n_keep = max(1, ceil(coverage * N))，
    coverage∈{.1,...,.9} 共 9 点；selective_accuracy = top-n_keep 中正确数 / n_keep。
    """
    proba = np.asarray(proba, dtype=float)
    conf = proba.max(axis=1)
    yi = encode_labels(y_true, classes)
    order = np.argsort(-conf, kind="stable")  # 置信度降序，平分保持原序
    n = len(conf)
    rows: List[Dict] = []
    for c in coverages:
        n_keep = max(1, int(math.ceil(float(c) * n)))
        top = order[:n_keep]
        top_pred = np.argmax(proba[top], axis=1)
        n_correct = int((top_pred == yi[top]).sum())
        rows.append({"coverage": float(c), "n_accepted": int(n_keep),
                     "n_correct": n_correct,
                     "selective_accuracy": n_correct / n_keep})
    return rows

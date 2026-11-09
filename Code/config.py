"""全局配置（兼容 shim）。

实现已迁至 `causalrca.config.paths`（唯一实现）；本模块只做**显式 re-export**，
保持 `from config import ...` 等旧导入路径可用。

刻意不用 `globals().update(vars(_impl))`：动态注入会让 AST 分析、IDE 跳转、
类型检查全部失效，也会掩盖「同名不同契约」。

注意：`config.PROJECT_ROOT` = CausalTrace 仓库根；
`rca.rca_config_v21.PROJECT_ROOT` = Code/ 目录（两者同名不同义，勿合并）。
"""
from causalrca.config.paths import (
    PROJECT_ROOT,
    CODE_ROOT,
    REPO_DATA,
    NORMAL_DIR,
    OUTPUT_DIR,
    FIG_DIR,
    RESULT_DIR,
    RCA_OUTPUT_DIR,
    RCA_RESULT_DIR,
    RCA_SIGNATURE_DIR,
    PLAN1_OUTPUT_DIR,
    PLAN1_RESULT_DIR,
    WINDOW_START,
    WINDOW_END,
    TIME_STEP,
    WINDOW_POINTS,
    EARLY_WINDOW_START,
    EARLY_WINDOW_END,
    TRAIN_RATIO,
    RANDOM_SEED,
    BOOTSTRAP_N,
    F_E_THRESHOLD,
    S_E_THRESHOLD,
    ANOMALY_SIGMA,
    ANOMALY_ALPHA,
    ALPHA_GRID,
    STATIONARY_CV_THRESHOLD,
    SEVERITY_GROUPS,
    LLM_API_KEY_ENV,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_TIMEOUT,
    LLM_TEMPERATURE,
    LLM_TOP_K_INPUT,
)

__all__ = [
    "PROJECT_ROOT", "CODE_ROOT", "REPO_DATA", "NORMAL_DIR",
    "OUTPUT_DIR", "FIG_DIR", "RESULT_DIR",
    "RCA_OUTPUT_DIR", "RCA_RESULT_DIR", "RCA_SIGNATURE_DIR",
    "PLAN1_OUTPUT_DIR", "PLAN1_RESULT_DIR",
    "WINDOW_START", "WINDOW_END", "TIME_STEP", "WINDOW_POINTS",
    "EARLY_WINDOW_START", "EARLY_WINDOW_END",
    "TRAIN_RATIO", "RANDOM_SEED",
    "BOOTSTRAP_N", "F_E_THRESHOLD", "S_E_THRESHOLD",
    "ANOMALY_SIGMA", "ANOMALY_ALPHA", "ALPHA_GRID", "STATIONARY_CV_THRESHOLD",
    "SEVERITY_GROUPS",
    "LLM_API_KEY_ENV", "LLM_BASE_URL", "LLM_MODEL", "LLM_TIMEOUT",
    "LLM_TEMPERATURE", "LLM_TOP_K_INPUT",
]

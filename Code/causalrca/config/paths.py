"""全局路径与通用常量（方案一/方案二共用）。

本模块承接旧 `Code/config.py` 的全部常量，取值与旧模块逐字一致，仅把
`__file__` 锚点从「Code/ 上一级」改为「本文件向上 3 级」（causalrca/config/paths.py
→ Code/causalrca/config → Code/causalrca → Code → CausalTrace）。

注意命名契约（勿合并）：
- 本模块 `PROJECT_ROOT` = CausalTrace 仓库根（旧 config.py 口径）；
- `causalrca.config.constants_v21.PROJECT_ROOT` = Code/ 目录（旧 rca_config_v21 口径）。

事故类型相关的配置（目标变量、变量集、冻结关系、本体映射）见
`causalrca.config.registry`（旧 accidents/<type>/config.py 仍在用顶层 config）。
"""
from pathlib import Path

# ==================== 路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parents[3]  # CausalTrace/
CODE_ROOT = PROJECT_ROOT / "Code"
REPO_DATA = PROJECT_ROOT / "nppad_repo" / "Operation_csv_data"
NORMAL_DIR = REPO_DATA / "Normal"

OUTPUT_DIR = CODE_ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
FIG_DIR = OUTPUT_DIR / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR = OUTPUT_DIR / "results"
RESULT_DIR.mkdir(parents=True, exist_ok=True)

# 方案二 RCA 产出目录
RCA_OUTPUT_DIR = OUTPUT_DIR / "rca"
RCA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
RCA_RESULT_DIR = RCA_OUTPUT_DIR / "results"
RCA_RESULT_DIR.mkdir(parents=True, exist_ok=True)
RCA_SIGNATURE_DIR = RCA_OUTPUT_DIR / "signatures"
RCA_SIGNATURE_DIR.mkdir(parents=True, exist_ok=True)

# 方案一 LOCA 产出目录（历史产出归档）
PLAN1_OUTPUT_DIR = OUTPUT_DIR / "plan1_loca"
PLAN1_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PLAN1_RESULT_DIR = PLAN1_OUTPUT_DIR / "results"
PLAN1_RESULT_DIR.mkdir(parents=True, exist_ok=True)

# ==================== 时间窗口（通用，逐事故可覆盖）====================
# 主窗口：事故发展期 t=300~1500s（121 点，10s/步含端点）
WINDOW_START = 300.0
WINDOW_END = 1500.0
TIME_STEP = 10.0
WINDOW_POINTS = int((WINDOW_END - WINDOW_START) / TIME_STEP) + 1  # 121

# 早期对照窗口（可选）
EARLY_WINDOW_START = 110.0
EARLY_WINDOW_END = 600.0

# ==================== 数据划分 ====================
TRAIN_RATIO = 0.8  # 80 训练 / 20 验证
RANDOM_SEED = 42

# ==================== Bootstrap (Algorithm 1) ====================
BOOTSTRAP_N = 100          # 重采样次数
F_E_THRESHOLD = 0.5        # 有向边出现频率阈值
S_E_THRESHOLD = 0.6        # 边权稳定性阈值 s_e = 1/(1+σ_e)

# ==================== 异常标记 (Phase 3.5) ====================
ANOMALY_SIGMA = 3.0        # μ ± kσ
ANOMALY_ALPHA = 0.3        # 增强排序权重（敏感性分析会遍历）
ALPHA_GRID = [0.0, 0.1, 0.3, 0.5, 1.0]

# 稳态判据：变异系数 CV = std/mean < 该阈值视为近似稳态
STATIONARY_CV_THRESHOLD = 0.05

# ==================== 严重度分组（通用，逐事故可覆盖）====================
SEVERITY_GROUPS = {
    "low": (1, 33),
    "mid": (34, 66),
    "high": (67, 100),
}

# ==================== LLM 解释生成配置（Phase 5.5 可选增强）====================
LLM_API_KEY_ENV = "DEEPSEEK_API_KEY"          # 环境变量名
LLM_BASE_URL = "https://api.deepseek.com"     # DeepSeek API 端点
LLM_MODEL = "deepseek-flash"                  # DeepSeek V4.1-Flash 模型名
LLM_TIMEOUT = 60                              # 请求超时（秒）
LLM_TEMPERATURE = 0.0                         # 0 = 尽量确定，减少幻觉
LLM_TOP_K_INPUT = 10                          # 注入 prompt 的上游候选数

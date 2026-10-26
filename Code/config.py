"""
全局配置：数据路径、通用算法阈值、时间窗口、随机种子。
事故类型相关的配置（目标变量、变量集、冻结关系、本体映射）放 accidents/<type>/config.py。
"""
from pathlib import Path

# ==================== 路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # CausalTrace/
REPO_DATA = PROJECT_ROOT / "nppad_repo" / "Operation_csv_data"
NORMAL_DIR = REPO_DATA / "Normal"

OUTPUT_DIR = PROJECT_ROOT / "Code" / "output"
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

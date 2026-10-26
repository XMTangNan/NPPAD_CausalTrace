"""v2.1 冻结配置（方案文档第 13 节，2026-10-26 评审定稿）。

本模块是 v2.1 全部预注册常量的唯一来源，禁止在其他模块散落魔数。
对应：方案/NPPAD-CausalTrace-v2-跨事故因果签名RCA方案.md 第 13 节。
旧 rca_config.py 为 v2.0 产物，不得被 v21 模块 import。
"""
from pathlib import Path

# ==================== 路径 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # Code/
CAUSALTRACE_ROOT = PROJECT_ROOT.parent
REPO_DATA = CAUSALTRACE_ROOT / "nppad_repo" / "Operation_csv_data"
NORMAL_CSV = REPO_DATA / "Normal" / "1.csv"

V21_OUTPUT = PROJECT_ROOT / "output" / "rca_v21"
SIGNATURE_DIR = V21_OUTPUT / "signatures"
CF_DIR = V21_OUTPUT / "cf_validation"
for _d in (V21_OUTPUT, SIGNATURE_DIR, CF_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ==================== 类别 / marker（13.0/13.3）====================
ACCIDENT_TYPES = ["LOCA", "SGATR", "FLB"]
MARKERS = {"LOCA": "WLR", "SGATR": "WTRA", "FLB": "WFLB"}
MARKER_COLS = ["WLR", "WTRA", "WFLB"]
# marker one-hot 互斥是 R0 必检项；WTRB 等非 pilot 类别 marker 登记但不入模
NON_PILOT_MARKERS = ["WTRB"]

# ==================== 时间窗（13.3.2）====================
TIME_STEP = 10.0
MAIN_WINDOW_START = 300.0
MAIN_WINDOW_END = 1500.0
MAIN_WINDOW_POINTS = int((MAIN_WINDOW_END - MAIN_WINDOW_START) / TIME_STEP) + 1  # 121
BASELINE_WINDOW_START = 520.0
BASELINE_WINDOW_END = 2030.0
BASELINE_WINDOW_POINTS = int((BASELINE_WINDOW_END - BASELINE_WINDOW_START) / TIME_STEP) + 1  # 152
# 学图窗（D1 修正 RCA-030/031，2026-10-26）：含注入瞬态使外生 marker 方向可辨识；
# 测试观测/组模式仍用主窗 [300,1500]，过程本体仍用恢复段 [520,2030]
LEARN_WINDOW_START = 60.0
LEARN_WINDOW_END = 1500.0
LEARN_WINDOW_POINTS = int((LEARN_WINDOW_END - LEARN_WINDOW_START) / TIME_STEP) + 1  # 145

# marker 轨迹级 GAM（C^A）样条参数（n=25~28 轨迹点，保守少样条防过拟合）
GAM_N_SPLINES = 6
GAM_LAM = 1.0
MARKER_X_SPREAD_MIN = 0.05   # 组内 z_marker 中位跨度小于此值则该模板 C^A 不可估（πA=0）

# ==================== 严重度分组（13.3.1；sev-1 仅基线源，不属任何组）====================
SEVERITY_BANDS = {"low": (2, 33), "mid": (34, 66), "high": (67, 100)}
GROUP_ORDER = ["low", "mid", "high"]
TEST_QUOTA = {"low": 7, "mid": 7, "high": 6}  # 每类共 20 条
BASELINE_SEVERITIES = [1]
SPLIT_SEED = 42

# ==================== 变量候选（13.3.3，v2.0 §4.1 枚举的 23 列）====================
# 18 个非 marker 物理症状/状态列（V_act 候选）
PHYSICAL_CANDIDATES = [
    # 一回路热工 6
    "P", "VOL", "LVPZ", "TAVG", "THA", "DNBR",
    # 一回路流量 1
    "WRCA",
    # 反应性功率 3
    "QMWT", "PWNT", "RH",
    # 蒸汽发生器/二回路 8
    "PSGA", "PSGB", "LSGA", "LSGB", "WFWA", "WFWB", "WSTA", "WSTB",
]
# 安注阶跃变量：不入 V_thr，学图可保留但不进 E3 分母（13.3.3）
SAFETY_STEP_VARS = ["WHPI", "WECS"]
# 23 列审计起点 = 18 物理 + 3 marker + 2 安注
CANDIDATE_UNION = PHYSICAL_CANDIDATES + MARKER_COLS + SAFETY_STEP_VARS

# target 展示锚点候选（13.3.4），仅审计冻结，绝不参与分类
TARGET_CANDIDATES = {
    "LOCA": ["P"],
    "SGATR": ["LSGA", "LSGB", "PSGA", "TSAT", "TCA"],
    "FLB": ["WFWA", "WFWB", "PSGB"],
}

# ==================== R0 审计阈值 ====================
CONSTANT_SIGMA = 1e-6          # σ 小于此值视为不可估（跳过，禁止数值兜底）
MARKER_INJECT_TOL = 1e-8       # marker 非零判定（R0 注入时刻）

# ==================== R1 Bootstrap（13.5.2，按用途分配）====================
LINGAM_N_BOOTSTRAP = 100       # 每模板 LiNGAM 全图次数（不允许降配）
GAM_MARKER_N_BOOTSTRAP = 50    # marker 列单变量 GAM 次数（D2；超预算保护可降 20）
GAM_FULL_N_BOOTSTRAP = 10      # GAM 全对向图次数（仅供共识标注）
GAM_BUDGET_OVERRUN = 0.5       # 超预算 50% 触发降配
STABLE_F = 0.70                # 稳定边出现率（13.5.3）
STABLE_DIR = 0.90              # 稳定边方向一致率
FRONTIER_MAX_DEPTH = 3         # BFS 三层 S1/S2/S3
GAM_CONSENSUS_MIN = 5          # 共识边 GAM 10 次中同方向至少 5 次（13.5.4）

# ==================== 标准化 / marker 尺度（13.5.1）====================
# z_marker,k = marker / median_platform_k
MARKER_Z_SCALE = "median_platform"

# ==================== E1（13.7.2）====================
EPSILON_PLATFORM_FRAC = 0.1    # ε_k = 0.1 × min(训练轨迹 marker 主窗平台中位数)，按类各算

# ==================== 测试观测 / E2 / E3（13.7）====================
ANOMALY_K = 3.0                # 过程本体 μ±3σ
ACTIVATION_FRAC = 0.10         # |z|>3 时间点占比 >10% 才进 A(x)
E3_FRONTIER_INVERSION_PENALTY = 0.8  # r3>r1 前沿倒置惩罚
W_FULL = {"E1": 0.30, "E2": 0.35, "E3": 0.35}
W_MASKED = {"E2": 0.50, "E3": 0.50}

# ==================== R1.5 Alg3（13.6）====================
CF_N_FOLDS = 5
CF_MIN_GROUP_SIZE = 5          # Q1/Q3 组各至少 5 条轨迹
CF_DYNAMIC_RANGE_MIN = 0.10    # dr = Q3−Q1 中位 z_marker < 0.10 → cf_not_applicable
CF_INCONCLUSIVE_DELTA = 0.10   # |Δobs|<0.1 且 |Δpred|<0.1（z 单位）→ cf_inconclusive
CF_FAIL_AGREEMENT = 0.50       # 方向一致率 <0.5 → gCF=0.5
GCF_PASS = 1.0
GCF_FAIL = 0.5

# ==================== 弃权标定（13.7.6）====================
LAMBDA1_HIGH = 1
LAMBDA2_LOW = 2
LAMBDA2_GRID = [1, 2, 3]

# ==================== D0 门控（13.8.1）====================
D0_GROUP = "high"              # 仅 MASKED-高严重度 18 条
D0_BOOTSTRAP_B = 2000
D0_SEED = 42
D0_CI_LOW = 0.025
D0_CI_HIGH = 0.975

# ==================== B2 XGBoost（13.9，超参实验前冻结）====================
XGB_PARAMS = {"max_depth": 3, "n_estimators": 100, "learning_rate": 0.1,
              "random_state": 42}

# ==================== 随机种子 ====================
GLOBAL_SEED = 42

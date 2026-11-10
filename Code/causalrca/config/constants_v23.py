"""v2.3 新增阈值 / 规格常量（唯一来源）。

v2.1 的冻结常量仍只放 `causalrca.config.constants_v21`（论文口径，禁止改动）；
本文件只收 v2.3 引入、且被多个执行器或分析模块共用的取值。

铁律：这里**只搬家不改值**（数值与迁移前逐字一致）。任何改值都属于算法口径变更，
须先过方案评审并在 `Code/error_tracking.md` 登记，不能顺手在这里调数。

旧模块（`rca.v21.source_tracing` / `severity_aggregation` / `chains` / `llm_explanation`）
仍保留同名模块级名称，通过 `from causalrca.config.constants_v23 import ...` 显式取得，
因此 `monkeypatch.setattr(模块, "名称", ...)` 与旧的 `from 模块 import 名称` 均照旧可用。
"""
from typing import Optional

from causalrca.config.constants_v21 import LINGAM_N_BOOTSTRAP

# ==================== 溯源 / 传导（方案 §5.2、V23-ALGO-001）====================
TOP_K_ROOT = 5                    # 根变量默认输出 Top-K
B_CLIP = 5.0                      # B 矩阵元素硬截断：|B|>B_CLIP 视为共线性伪影
CLIP_SENSITIVITY = (3.0, 5.0, 10.0)  # ALGO-001 敏感性档位（不改默认值）
MAX_PATH_LEN = 4                  # 传导路径最大边数
MAX_PATH_DEPTH = 5                # 传导路径最大节点数（= MAX_PATH_LEN + 1）
MAX_PATHS_PER_ROOT = 20           # 每个根变量的路径数上限（防组合爆炸）
STABILITY_COND_MAX = 1e4          # 截断后 (I-B) 条件数上限
STABILITY_CLIP_RATIO_MAX = 0.35   # 被截断边 / 稳定边 上限

# ==================== 跨组/跨严重度聚合（方案 §7.3）====================
EDGE_DIFF_THRESHOLD = 0.5         # 边数差异 > 50% 触发差异标注
MECH_JACCARD_THRESHOLD = 0.5      # Top5 机理 Jaccard < 0.5 视为机理漂移

# ==================== 路径检索融合（方案 §5.4，13.10 冻结值）====================
RRF_K = 60

# ==================== LLM 解释（方案 §7.5）====================
LLM_FLOAT_TOL = 1e-3              # 解释文本数值抽取的浮点比较容差
LLM_FAITHFUL_DECIMALS = 2         # 数值对拍：保留 2 位小数后相等
LLM_TIMEOUT_S = 30                # 标准库 urllib 硬超时（测试可 monkeypatch）

# ==================== bootstrap 次数（方案 §6.4.6 / §7.7，V23-DESIGN-041/044）====================
# 正式口径（spec）：方案里描述的方法学口径，值 = v2.1 冻结值 100。
# **仅为口径声明，默认不执行**；只有显式 `--n-bootstrap 100`（或环境变量）才开启。
N_BOOTSTRAP_SPEC = LINGAM_N_BOOTSTRAP          # = 100
# 执行默认：所有执行路径（全链/端到端/A-B/实验脚本）默认取 5（用户 2026-11-10 指令，算力开销）。
N_BOOTSTRAP_DEFAULT = 5
RUN_MODE_SPEC = "spec"
RUN_MODE_REDUCED = "reduced"


def run_mode_for(n_bootstrap: Optional[int]) -> str:
    """按实际 bootstrap 次数标注 run 档位；未记录返回 "not_recorded"。

    仅作**标注**用途：不决定是否刷新 `output/latest`，也不自动决定 `citable`。
    """
    if n_bootstrap is None:
        return "not_recorded"
    return RUN_MODE_SPEC if int(n_bootstrap) >= N_BOOTSTRAP_SPEC else RUN_MODE_REDUCED


def stability_claims_allowed(n_bootstrap: Optional[int]) -> bool:
    """仅当实际次数达到正式口径（100）时，才允许声称边稳定性/引用 consistency_rate。"""
    return n_bootstrap is not None and int(n_bootstrap) >= N_BOOTSTRAP_SPEC


# ---- 兼容别名（V23-CODE-032）：改档时被改名的旧符号继续可用，避免外部入口 ImportError ----
N_BOOTSTRAP_PROD = N_BOOTSTRAP_SPEC   # 旧名：生产档
N_BOOTSTRAP_TEST = N_BOOTSTRAP_DEFAULT  # 旧名：测试档
RUN_MODE_PROD = RUN_MODE_SPEC
RUN_MODE_TEST = RUN_MODE_REDUCED

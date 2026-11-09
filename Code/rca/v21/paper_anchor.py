"""论文锚定（V23-DESIGN-018 / 返工版 V23-DESIGN-023）：把变量选取锚回 CausalTrace 原文。

原文事实（逐条可核）：
1. Feature Selector 支持「data-driven feature selection methods or manual selection
   based on domain expertise」——论文 p.2，Fig.2 管线说明段。
2. 评估实际使用「eight key variables identified through XGBoost feature selection and
   validated by domain experts」——论文 p.4，Evaluation and Results / Dataset Description。
3. 数据来自 potentiometers、load cells、drive temperatures、robot kinematics——同上段。

原文**未给出**这 8 个变量的名单，也未给出 XGBoost 超参、重要性阈值、候选池大小；
全文无 VIF/PCA/降维/变量集敏感性分析（Table 1 的 ablation 是本体/知识图谱消融）。

因此本模块把「论文锚定」落在论文真正写明、且可执行的两条上：
- **专家验证集优先**（第 2 条的 validated by domain experts）：用项目早期机理筛选集作为
  «专家验证集» 的可执行代理（`EARLY_MECHANISM_SET`，属工程假设，非论文名单）；
- **数量事实记录**（第 2 条的 eight）：`PAPER_N_VARIABLES=8` 只作事实记录与报告字段，
  不强行把 NPPAD 变量砍到 8（论文的 8 是其自身 96 通道数据集上的结论）。

**锚定语义（返工）**：候选优先 + 评分加权，**不是不可剔除的保护集**。
锚定变量可被 VIF 迭代或病态处理剔除，但剔除必须写理由与影响（见
`variable_selector` 的 `dropped_anchor` / `anchor_impact`）。
"""
from typing import Dict, Iterable, List, Optional

from rca.rca_config import CLASS_VARIABLES

# 论文事实（写进产物，便于审稿人对照页码）
PAPER_CITATIONS: Dict[str, str] = {
    "feature_selector": "p.2, Fig.2 管线说明: data-driven feature selection methods or "
                        "manual selection based on domain expertise",
    "final_variable_set": "p.4, Evaluation and Results / Dataset Description: eight key "
                          "variables identified through XGBoost feature selection and "
                          "validated by domain experts",
    "sensor_types": "p.4, 同段: potentiometers, load cells, drive temperatures, "
                    "robot kinematics",
    "not_reported": "原文未给出变量名单、XGBoost 超参与重要性阈值；无 VIF/PCA/"
                    "降维/变量集敏感性分析",
}
PAPER_N_VARIABLES = 8

# 原文未给出名单 → 明确为空，禁止凭空补名字
PAPER_EXPLICIT_VARIABLES: Dict[str, List[str]] = {}

# «专家验证集» 的可执行代理：项目早期机理筛选集（工程假设）
# 来源：commit 544f3a3 "Initial commit: NPPAD CausalTrace project"（2026-10-26 23:56:19）
#       Code/rca/rca_config.py::CLASS_VARIABLES（LOCA 14 / SGATR 15 / FLB 14）
EARLY_MECHANISM_SET: Dict[str, List[str]] = {
    k: list(v) for k, v in CLASS_VARIABLES.items()
}
EARLY_MECHANISM_SET_SOURCE = (
    "早期机理筛选集：rca.rca_config.CLASS_VARIABLES，"
    "来源 commit 544f3a3（2026-10-26 Initial commit）；"
    "LOCA 14 / SGATR 15 / FLB 14 个变量（工程假设，非论文名单，也非 v2.1 硬编码集）"
)

# 与真正的 v2.1 变量集（Code/output/rca_v21/variable_sets.json::V_graph，每类 20 个）
# 的事实差异，仅用于说明/待确认，不参与自动替换。
V21_SET_REFERENCE = "Code/output/rca_v21/variable_sets.json 的 V_graph（每类 20 变量）"
PROXY_VS_V21_DIFF: Dict[str, Dict[str, object]] = {
    "LOCA": {"early_n": 14, "v21_n": 20, "common": 13,
             "only_early": ["WRCA"],
             "only_v21": ["LSGA", "LSGB", "PSGB", "WFWA", "WFWB", "WSTA", "WSTB"]},
    "SGATR": {"early_n": 15, "v21_n": 20, "common": 11,
              "only_early": ["TF", "TSAT", "WLR", "WRCA"],
              "only_v21": ["DNBR", "LSGA", "LSGB", "WECS", "WFWA", "WFWB", "WHPI",
                           "WSTA", "WSTB"]},
    "FLB": {"early_n": 14, "v21_n": 20, "common": 12,
            "only_early": ["VOID", "WRCA"],
            "only_v21": ["LSGA", "LSGB", "WECS", "WFWA", "WFWB", "WHPI", "WSTA", "WSTB"]},
}

# 冲突裁决口径（可执行、单一来源；锚定可剔除，但必须留理由与影响）
CONFLICT_POLICY = {
    "mode": "candidate_weighted",
    "stage1_near_constant": "剔除并记录（σ<1e-6/唯一值<3 的列无分布，LiNGAM 无法建模）",
    "stage1_high_missing": "剔除并记录（缺失>50% 无法建模）",
    "z_pathological": "不单独剔除；仅作为数值病态标记记录（|σ/μ|<1%），"
                      "若同时 VIF 超标则在 VIF 迭代中剔除并记录 z_pathological=true",
    "high_vif": "剔除并记录（VIF≥500 时按 VIF 迭代剔除，写实测 VIF 与剔除前后 max_vif_all、变量数）",
    "missing_in_data": "记录为 missing_in_data，不报错（数据集未采集该列时锚定自然失效）",
}


def build_anchor(accident_type: str, available: Iterable[str]) -> Dict:
    """返回该事故类的锚定候选集与来源说明（不改动入参）。

    available: 数据中实际存在的数值列（阶段 1 之前），用于区分「数据没有」与「阶段 1 剔除」。
    返回的 anchor_variables 是**候选优先队列**，不是保护集。
    """
    avail = list(available)
    avail_set = set(avail)
    explicit = [v for v in PAPER_EXPLICIT_VARIABLES.get(accident_type, []) if v in avail_set]
    proxy_all = EARLY_MECHANISM_SET.get(accident_type, [])
    proxy = [v for v in proxy_all if v in avail_set]
    missing = [v for v in proxy_all if v not in avail_set]
    return {
        "source": "CausalTrace 原文 p.4 Dataset Description + p.2 Feature Selector",
        "citations": dict(PAPER_CITATIONS),
        "paper_n_variables": PAPER_N_VARIABLES,
        "paper_explicit_variables": explicit,
        "explicit_is_empty_reason": "原文未给出 8 个变量名单" if not explicit else "",
        "anchor_mode": "candidate_weighted",
        "anchor_proxy_source": EARLY_MECHANISM_SET_SOURCE,
        "v21_set_reference": V21_SET_REFERENCE,
        "proxy_vs_v21_diff": PROXY_VS_V21_DIFF.get(accident_type, {}),
        "anchor_variables": proxy,
        "missing_in_data": missing,
        "conflict_policy": dict(CONFLICT_POLICY),
        "note": ("论文锚定只承诺「专家验证集优先进入候选并加权，数据驱动只在其外补选/剔除病态」；"
                 "锚定变量可被 VIF/病态处理剔除，剔除须记录理由与影响；"
                 "具体变量名是工程代理（早期机理筛选集），不是论文原文，也不是 v2.1 硬编码集"),
    }


def stage1_drop_reason(var: str, stage1_log: List[Dict]) -> Optional[str]:
    """从阶段 1 日志取该锚定变量的剔除原因（无则 None）。"""
    for e in stage1_log:
        if e.get("var") == var:
            return str(e.get("reason"))
    return None

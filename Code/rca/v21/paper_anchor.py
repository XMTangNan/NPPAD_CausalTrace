"""论文锚定（V23-DESIGN-018）：把变量选取锚回 CausalTrace 原文。

原文事实（逐条可核）：
1. Feature Selector 支持「data-driven feature selection methods or manual selection
   based on domain expertise」——论文 p.2，Fig.2 管线说明段。
2. 评估实际使用「eight key variables identified through XGBoost feature selection and
   validated by domain experts」——论文 p.4，Evaluation and Results / Dataset Description。
3. 数据来自 potentiometers、load cells、drive temperatures、robot kinematics——同上段。

原文**未给出**这 8 个变量的名单，也未给出 XGBoost 超参、重要性阈值、候选池大小；
全文无 VIF/PCA/降维/变量集敏感性分析（Table 1 的 ablation 是本体/知识图谱消融）。

因此本模块把「论文锚定」落在论文真正写明、且可执行的两条上：
- **专家验证集优先**（第 2 条的 validated by domain experts）：用 v2.1 专家冻结集作为
  «专家验证集» 的可执行代理（`EXPERT_ANCHOR_PROXY`，属工程假设，非论文名单）；
- **数量事实记录**（第 2 条的 eight）：`PAPER_N_VARIABLES=8` 只作事实记录与报告字段，
  不强行把 NPPAD 变量砍到 8（论文的 8 是其自身 96 通道数据集上的结论）。
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

# «专家验证集» 的可执行代理：v2.1 项目内以事故机理冻结的变量集（工程假设）
EXPERT_ANCHOR_PROXY: Dict[str, List[str]] = {
    k: list(v) for k, v in CLASS_VARIABLES.items()
}

# 冲突裁决口径（可执行、单一来源）
CONFLICT_POLICY = {
    "constant_or_missing": "剔除并记录（σ<1e-6/唯一值<3/缺失>50% 的列无分布，LiNGAM 无法建模；"
                           "锚定不能强留不可建模列）",
    "z_pathological": "保留并标记（|σ/μ|<1% 属数值病态而非物理无关；锚定变量不因 Z 病态被剔除，"
                      "在报告中标记 z_pathological=true）",
    "high_vif": "保留并标记（锚定变量进入保护集，不参与 VIF 迭代剔除；报告 vif 实测值）",
    "missing_in_data": "记录为 missing_in_data，不报错（数据集未采集该列时锚定自然失效）",
}


def build_anchor(accident_type: str, available: Iterable[str]) -> Dict:
    """返回该事故类的锚定集与来源说明（不改动入参）。

    available: 数据中实际存在的数值列（阶段 1 之前），用于区分「数据没有」与「阶段 1 剔除」。
    """
    avail = list(available)
    avail_set = set(avail)
    explicit = [v for v in PAPER_EXPLICIT_VARIABLES.get(accident_type, []) if v in avail_set]
    proxy_all = EXPERT_ANCHOR_PROXY.get(accident_type, [])
    proxy = [v for v in proxy_all if v in avail_set]
    missing = [v for v in proxy_all if v not in avail_set]
    return {
        "source": "CausalTrace 原文 p.4 Dataset Description + p.2 Feature Selector",
        "citations": dict(PAPER_CITATIONS),
        "paper_n_variables": PAPER_N_VARIABLES,
        "paper_explicit_variables": explicit,
        "explicit_is_empty_reason": "原文未给出 8 个变量名单" if not explicit else "",
        "anchor_proxy_source": "v2.1 专家冻结集（rca.rca_config.CLASS_VARIABLES，工程假设）",
        "anchor_variables": proxy,
        "missing_in_data": missing,
        "conflict_policy": dict(CONFLICT_POLICY),
        "note": ("论文锚定只承诺「专家验证集必须进模型、数据驱动只在其外补选/剔除病态」；"
                 "具体变量名是工程代理，不是论文原文"),
    }


def stage1_drop_reason(var: str, stage1_log: List[Dict]) -> Optional[str]:
    """从阶段 1 日志取该锚定变量的剔除原因（无则 None）。"""
    for e in stage1_log:
        if e.get("var") == var:
            return str(e.get("reason"))
    return None

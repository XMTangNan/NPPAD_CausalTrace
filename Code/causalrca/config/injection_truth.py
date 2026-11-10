"""NPPAD 始发（注入）事件变量真值表 —— 变量级评测的**唯一**真值来源。

用途与边界
- 只供 `causalrca/reporting/evaluation.py`（D11 第 10 步）做命中率比对；
  **禁止**用于变量选取、建图、排序、解释生成——那会构成用答案做题。
- 不得用 `ACCIDENT_KEY_RESPONSES`、`forced/protected` 保护集、关键响应变量替代本表；
  这些都不是“注入点”。

来源与核对
- 逐条目对齐老师项目
  `故障溯源演示平台/nppad_rca_experiment/rca_experiment.py:34-47` 的 `GROUND_TRUTH`；
  该表按 NPPAD 事故表与变量表人工整理，标注为“直接始发变量，非穷尽物理根因”。
- 与 NPPAD 数据列核对：本仓库 3 类数据共 97 列（含 TIME），
  WLR / WBK / MBK / WTRA / WFLB 均存在。

待核项（不隐去）
- LOCA 的 `WBK` / `MBK`（破口流量积分量）：在老师项目注释中即属“可疑项”，
  本仓库变量选取阶段也未把它们纳入模型变量集。评测时它们仍计入真值集
  （与老师同处理：只把“数据里不存在的列”剔除），但会在产物里单列
  `gt_in_candidate_set`，便于区分“没找对”和“根本不在候选里”。

口径差异（不可与老师数字并排）
- 老师的 `RESPONSE_VARS` 人工先验降权（×0.30）、训练/评测文件重叠、12 类合图训练，
  本仓库三项都不做：真值仅用于打分、切分按 `split_manifest.test_severities` 严格互斥、
  每个 (类, 组) 单独建图。
"""

# 12 类始发变量（与老师 GROUND_TRUTH 一致）
INJECTION_TRUTH: dict[str, tuple[str, ...]] = {
    "LOCA": ("WLR", "WBK", "MBK"),
    "LOCAC": ("WLR", "WBK", "MBK"),
    "SGATR": ("WTRA",),
    "SGBTR": ("WTRB",),
    "FLB": ("WFLB",),
    "LLB": ("WLD",),
    "LR": ("TBLD",),
    "RI": ("RHRD", "RH"),
    "RW": ("RHRD", "RH"),
    "MD": ("PPM", "RHBR", "RH"),
    "LOF": ("WRCA", "WRCB", "RRCA", "RRCB", "RRCO"),
    "TT": ("TBLD",),
}

SOURCE = ("故障溯源演示平台/nppad_rca_experiment/rca_experiment.py:34-47 GROUND_TRUTH"
          "（按 NPPAD 事故表/变量表人工整理的直接始发变量，非穷尽物理根因）")

# 已知可疑 / 待核条目（披露用，不影响命中统计）
SUSPECT = {
    "LOCA": ("WBK", "MBK"),
    "LOCAC": ("WBK", "MBK"),
}


def truth_for(accident_type: str) -> tuple[str, ...]:
    if accident_type not in INJECTION_TRUTH:
        raise KeyError(f"{accident_type} 未登记始发变量真值，禁止用代理代替")
    return INJECTION_TRUTH[accident_type]

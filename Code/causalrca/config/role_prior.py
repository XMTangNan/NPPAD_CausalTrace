"""角色先验（B1 / V23-DESIGN-046）：外部响应变量名单作为**排序乘子**。

口径（吸收后的边界，N1 不变）：
- **不进主排序公式**：主排序仍是 `|T|` 行和（总因果效应）；本名单只作为可开关的乘子，
  命中者效应 `×0.30`，其余 `×1.0`（只乘权重，不做剔除）。
- **不改图、不改 T**：不修改 `stable_edges`、不修改 `B`，只在排序键上作用。
- **默认关闭**：`ROLE_PRIOR_ENABLED_DEFAULT = False`；开启时必须同时产出"有先验/无先验"
  两版排序与两套指标（评测侧 channel 成对出现，缺一即不合规）。
- **名单单点**：来源、权重、作用范围只在本文件定义，不得散落到调用点。
- **名单缺失不得回退猜测**：`resolve(enabled=True)` 在名单为空时直接抛错。

名单来源：外部项目 `RESPONSE_VARS`（41 项，A1 实验原样复制，未增删）。
"""
from typing import Iterable, Optional

ROLE_PRIOR_ENABLED_DEFAULT = False
ROLE_PRIOR_WEIGHT = 0.30
ROLE_PRIOR_SOURCE = "外部项目 RESPONSE_VARS（41 项，A1 原样复制）"
ROLE_PRIOR_VARS = frozenset({
    "WHPI", "WECS", "WSPY", "WCSP", "WCFT", "WLPI", "WCHG", "WUP",
    "P", "TAVG", "THA", "THB", "TCA", "TCB", "TSAT", "QMWT", "QMGA", "QMGB",
    "PWNT", "PWR", "TPCT", "VOID", "MH2", "CNH2", "PRB", "TRB", "LWRB",
    "LVPZ", "LVCR", "VOL", "PSGA", "PSGB", "LSGA", "LSGB", "NSGA", "NSGB",
    "WSTA", "WSTB", "TKLV", "RHMT", "RHFL",
})


def resolve(enabled: Optional[bool] = None) -> bool:
    """解析开关；`None` 取默认（关闭）。开启但名单为空 → 拒绝，不回退猜测。"""
    on = ROLE_PRIOR_ENABLED_DEFAULT if enabled is None else bool(enabled)
    if on and not ROLE_PRIOR_VARS:
        raise ValueError("角色先验已开启但名单为空：禁止回退猜测，请先在 role_prior.py 配置名单")
    return on


def is_prior(var: str) -> bool:
    return var in ROLE_PRIOR_VARS


def multiplier(var: str, enabled: bool) -> float:
    """命中名单的变量效应乘子（仅排序用）；未命中为 1.0。"""
    return ROLE_PRIOR_WEIGHT if (enabled and is_prior(var)) else 1.0


def missing_in(variables: Iterable[str]) -> list:
    """返回名单中与给定变量集无交集的项（诊断用，不参与排序）。"""
    vs = set(variables)
    return sorted(ROLE_PRIOR_VARS - vs) if vs else sorted(ROLE_PRIOR_VARS)

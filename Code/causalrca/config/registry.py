"""v2.3 12 类事故注册表：事故元数据的唯一来源（STRUCTURE.md §4）。

本模块承接旧 `Code/rca/v21/accident_config.py`，取值逐字一致；`__file__` 锚点按新位置
（causalrca/config/registry.py → parents[3]）重算，仍指向 .../CausalTrace。

字段与含义见 Code/STRUCTURE.md §4；权威取值来自方案 §4.1（12 类清单）、
§4.3（6 个机理组、触发/关键响应、时间窗）、附录 A（触发变量与关键响应速查表）。

铁律：
- 事故元数据只在本模块定义，其他模块（constants_v21 / variable_selector / 执行器）
  一律从本模块派生，禁止第二份 12 类列表；
- 本模块**不得** import `causalrca.config.constants_v21`（会被其反向 import，造成循环依赖），
  仓库根用 Path(__file__) 自行求解；
- 本地数据状态必须如实登记（§4.0）：仅 LOCA/SGATR/FLB 完整、LOCAC 5 条、
  其余 8 类无目录；无数据类禁止标记 ready、禁止编造轨迹数。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

# ==================== 路径（不 import constants_v21，避免循环依赖）====================
CAUSALTRACE_ROOT: Path = Path(__file__).resolve().parents[3]  # .../CausalTrace
REPO_DATA: Path = CAUSALTRACE_ROOT / "nppad_repo" / "Operation_csv_data"

# ==================== 12 类固定顺序（方案 §4.1 编号 1-12）====================
ACCIDENT_ORDER: List[str] = [
    "LOCA", "LOCAC", "SLBIC", "SLBOC", "SGATR", "SGBTR",
    "RW", "RI", "FLB", "MD", "LR", "LLB",
]

# ==================== 组级公共配置（方案 §4.3）====================
# 严重度分组：sev-1 为基线源，不属任何组（与 v2.1 §13.3.1 一致）
SEVERITY_BANDS: Dict[str, tuple] = {"low": (2, 33), "mid": (34, 66), "high": (67, 100)}

T_INJECT: float = 110.0  # 组1~6 注入时刻（方案 §4.3，待数据核实）
# 时间窗：主分析窗 / 学图窗 / 恢复段基线窗
_WINDOW_DEFAULT = {
    "main_window": (300.0, 1500.0),
    "learn_window": (60.0, 1500.0),
    "baseline_window": (520.0, 2030.0),
}
# 组4（反应性引入类：RW/RI/MD）响应快，窗口缩短（方案 §4.3 组4 例外）
_WINDOW_GROUP4 = {
    "main_window": (300.0, 1000.0),
    "learn_window": (60.0, 1000.0),
    "baseline_window": (520.0, 1030.0),
}

# 必需字段（STRUCTURE.md §4 + v2.3 增补字段），构造时校验，防止漏字段
_FIELDS = (
    "code", "name_cn", "group", "marker", "trigger_vars", "key_responses",
    "t_inject", "main_window", "learn_window", "baseline_window",
    "severity_bands", "n_trajectories", "status", "data_dir", "notes",
    "n_trajectories_design", "marker_type", "severity_unit",
)

_VALID_STATUS = ("ready", "partial", "data_missing")


def _windows(group: int) -> Dict[str, tuple]:
    return dict(_WINDOW_GROUP4 if group == 4 else _WINDOW_DEFAULT)


def _record(code: str, name_cn: str, group: int, marker: str,
            trigger_vars: List[str], key_responses: List[str],
            severity_unit: str, n_design: int, marker_type: str,
            status: str, n_trajectories: int, notes: str) -> Dict:
    """构造一条注册记录并做完整性自检（缺字段/非法 status 立即报错）。"""
    if status not in _VALID_STATUS:
        raise ValueError(f"非法 status: {code}={status}")
    rec = {
        "code": code,
        "name_cn": name_cn,
        "group": int(group),
        "marker": marker,
        "trigger_vars": list(trigger_vars),
        "key_responses": list(key_responses),
        "t_inject": T_INJECT,
        # data_dir 用绝对路径：rca/v21/data.py 的 ensure_class_available() 直接
        # Path(cfg["data_dir"]).glob，相对路径在 CWD=Code 下会误判 no_csv_found。
        "data_dir": str(REPO_DATA / code),
        "severity_bands": dict(SEVERITY_BANDS),
        "n_trajectories": int(n_trajectories),
        "status": status,
        "n_trajectories_design": int(n_design),
        "marker_type": marker_type,
        "severity_unit": severity_unit,
        "notes": notes,
    }
    rec.update(_windows(group))
    missing = [f for f in _FIELDS if f not in rec]
    if missing:
        raise ValueError(f"{code} 缺字段: {missing}")
    return rec


# ==================== 注册表：12 类（方案 §4.1 / §4.3 / 附录 A）====================
_NOTE_NO_DATA = (
    "本地无数据；代码层配置就绪，执行器跳过并落 skipped_classes.json；禁止编造。"
    "窗口/t_inject 待数据核实（方案 §4.3）。"
)

REGISTRY: Dict[str, Dict] = {
    "LOCA": _record(
        code="LOCA", name_cn="一回路热段失水事故", group=1, marker="WLR",
        trigger_vars=["WLR"],
        key_responses=["P", "TAVG", "VOL", "WECS", "WHPI"],
        severity_unit="% of 100 cm²", n_design=100, marker_type="indicator",
        status="ready", n_trajectories=100,
        notes="本地 100 条轨迹已核实（方案 §4.0）；窗口与 t_inject 沿用 v2.1 实测值。",
    ),
    "LOCAC": _record(
        code="LOCAC", name_cn="一回路冷段失水事故", group=1, marker="WLR",
        trigger_vars=["WLR"],
        key_responses=["P", "TAVG", "VOL", "WECS", "WHPI"],
        severity_unit="% of 100 cm²", n_design=100, marker_type="indicator",
        status="partial", n_trajectories=5,
        notes="目录存在但仅 5 个 csv（1/25/50/75/100），不足以三分组（方案 §4.0）；"
              "只做描述性，不进 36 份验收。窗口/t_inject 为方案设计值（方案 §4.3）。",
    ),
    "SLBIC": _record(
        code="SLBIC", name_cn="安全壳内蒸汽管道破裂", group=2, marker="WSTA",
        trigger_vars=["WSTA", "WSTB"],
        key_responses=["PSGA", "PSGB", "WFWA", "WFWB", "TAVG"],
        severity_unit="% of 100 cm²", n_design=100, marker_type="process",
        status="data_missing", n_trajectories=0,
        notes=_NOTE_NO_DATA + "marker 为过程量（方案 §4.3 组2：无专用指示器）。",
    ),
    "SLBOC": _record(
        code="SLBOC", name_cn="安全壳外蒸汽管道破裂", group=2, marker="WSTA",
        trigger_vars=["WSTA", "WSTB"],
        key_responses=["PSGA", "PSGB", "WFWA", "WFWB", "TAVG"],
        severity_unit="% of 100 cm²", n_design=100, marker_type="process",
        status="data_missing", n_trajectories=0,
        notes=_NOTE_NO_DATA + "marker 为过程量（方案 §4.3 组2）；与 SLBIC 靠安全壳隔离响应区分。",
    ),
    "SGATR": _record(
        code="SGATR", name_cn="蒸汽发生器A传热管破裂", group=3, marker="WTRA",
        trigger_vars=["WTRA"],
        key_responses=["PSGA", "LSGA", "RM2"],
        severity_unit="% of 1 full tube", n_design=100, marker_type="indicator",
        status="ready", n_trajectories=100,
        notes="本地 100 条轨迹已核实（方案 §4.0）；v2.1 内联变量集额外含 PSGB/LSGB"
              "（附录 A 原文仅 PSGA/LSGA/RM2），variable_selector 保留更全集以不改动已验收结果。",
    ),
    "SGBTR": _record(
        code="SGBTR", name_cn="蒸汽发生器B传热管破裂", group=3, marker="WTRB",
        trigger_vars=["WTRB"],
        key_responses=["PSGB", "LSGB", "RM2"],
        severity_unit="% of 1 full tube", n_design=110, marker_type="indicator",
        status="data_missing", n_trajectories=0,
        notes=_NOTE_NO_DATA + "与 SGATR 变量集 A/B 侧对称（方案 §4.3 组3）。",
    ),
    "RW": _record(
        code="RW", name_cn="提棒（控制棒抽出）", group=4, marker="RHRD",
        trigger_vars=["RHRD"],
        key_responses=["PWNT", "TAVG", "P", "QMWT"],
        severity_unit="% (+/-) withdrawn", n_design=100, marker_type="process",
        status="data_missing", n_trajectories=0,
        notes=_NOTE_NO_DATA + "marker 为过程量（反应性）；组4 缩短窗。",
    ),
    "RI": _record(
        code="RI", name_cn="插棒（控制棒插入）", group=4, marker="RHRD",
        trigger_vars=["RHRD"],
        key_responses=["PWNT", "TAVG", "P", "QMWT"],
        severity_unit="% (+/-) insertion", n_design=100, marker_type="process",
        status="data_missing", n_trajectories=0,
        notes=_NOTE_NO_DATA + "marker 为过程量；与 RW 变量集共享但响应符号相反（方案 §4.3 组4）。",
    ),
    "FLB": _record(
        code="FLB", name_cn="给水管道破裂", group=5, marker="WFLB",
        trigger_vars=["WFLB"],
        key_responses=["WFWA", "WFWB", "TAVG", "PWNT", "PSGA"],
        severity_unit="% of 100 cm²", n_design=100, marker_type="indicator",
        status="ready", n_trajectories=100,
        notes="本地 100 条轨迹已核实（方案 §4.0）；v2.1 内联变量集额外含 PSGB。"
              "方案 §4.3 组5 提示 WFWA 在 sev≈10 存在断点，需变点检测标注。",
    ),
    "MD": _record(
        code="MD", name_cn="慢化剂稀释", group=4, marker="PPM",
        trigger_vars=["PPM"],
        key_responses=["PWNT", "TAVG", "P", "QMWT"],
        severity_unit="% of unborated injection", n_design=100, marker_type="process",
        status="data_missing", n_trajectories=0,
        notes=_NOTE_NO_DATA + "marker 为过程量（硼浓度）；组4 缩短窗。",
    ),
    "LR": _record(
        code="LR", name_cn="甩负荷", group=5, marker="TBLD",
        trigger_vars=["TBLD"],
        key_responses=["WFWA", "WFWB", "TAVG", "PWNT", "PSGA"],
        severity_unit="% of full load rejected", n_design=100, marker_type="process",
        status="data_missing", n_trajectories=0,
        notes=_NOTE_NO_DATA + "marker 为过程量（汽机负荷）；与 FLB 响应模式不同（方案 §4.3 组5）。",
    ),
    "LLB": _record(
        code="LLB", name_cn="下泄管线破裂", group=6, marker="WLD",
        trigger_vars=["WLD"],
        key_responses=["VOL", "P", "LVPZ", "WECS"],
        severity_unit="% of nominal letdown flow", n_design=100, marker_type="process",
        status="data_missing", n_trajectories=0,
        notes=_NOTE_NO_DATA + "marker 为过程量（下泄流量）；与 LOCA 同属冷却剂丧失但路径不同。",
    ),
}

if list(REGISTRY) != ACCIDENT_ORDER:
    raise RuntimeError(f"REGISTRY 顺序与 ACCIDENT_ORDER 不一致: {list(REGISTRY)}")

# ==================== 派生常量 ====================
ALL_CODES: List[str] = list(ACCIDENT_ORDER)
READY_CODES: List[str] = [c for c in ACCIDENT_ORDER if REGISTRY[c]["status"] == "ready"]
PARTIAL_CODES: List[str] = [c for c in ACCIDENT_ORDER if REGISTRY[c]["status"] == "partial"]
MISSING_CODES: List[str] = [c for c in ACCIDENT_ORDER
                            if REGISTRY[c]["status"] == "data_missing"]


# ==================== 查询接口 ====================
def get_accident(code: str) -> Dict:
    """按事故代码取配置（浅拷贝）；未登记 code 抛 KeyError。"""
    if code not in REGISTRY:
        raise KeyError(f"未登记的事故类别: {code}")
    return dict(REGISTRY[code])


def key_responses(code: str) -> List[str]:
    """该类的关键响应变量（方案附录 A 原文）。"""
    return list(get_accident(code)["key_responses"])


def trigger_vars(code: str) -> List[str]:
    """该类的触发变量（方案附录 A 原文）。"""
    return list(get_accident(code)["trigger_vars"])


def marker_of(code: str) -> str:
    """该类的 marker 列（v2.3 约定 marker = trigger_vars[0]）。"""
    return str(get_accident(code)["marker"])


def data_path(code: str) -> Path:
    """该类数据目录的绝对路径（目录可能不存在，见 status=data_missing）。"""
    cfg = get_accident(code)
    p = Path(cfg["data_dir"])
    return p if p.is_absolute() else (CAUSALTRACE_ROOT / p)

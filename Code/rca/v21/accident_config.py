"""v2.3 12 类事故注册表（兼容 shim）。

实现已迁至 `causalrca.config.registry`（唯一实现）；本模块只做**显式 re-export**，
保持 `from rca.v21.accident_config import ...` 等旧导入路径可用。

刻意不用 `globals().update(vars(_impl))`：动态注入会让 AST 分析、IDE 跳转、
类型检查全部失效，也会掩盖「同名不同契约」。
"""
from causalrca.config.registry import (
    CAUSALTRACE_ROOT,
    REPO_DATA,
    ACCIDENT_ORDER,
    SEVERITY_BANDS,
    T_INJECT,
    REGISTRY,
    ALL_CODES,
    READY_CODES,
    PARTIAL_CODES,
    MISSING_CODES,
    get_accident,
    key_responses,
    trigger_vars,
    marker_of,
    data_path,
)

__all__ = [
    "CAUSALTRACE_ROOT", "REPO_DATA", "ACCIDENT_ORDER", "SEVERITY_BANDS", "T_INJECT",
    "REGISTRY", "ALL_CODES", "READY_CODES", "PARTIAL_CODES", "MISSING_CODES",
    "get_accident", "key_responses", "trigger_vars", "marker_of", "data_path",
]

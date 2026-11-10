"""v2.3 产物统一写入口（兼容 shim）。

实现已迁至 `causalrca.io.artifact`（唯一实现）；本模块只做**显式白名单 re-export**，
保持 `from causalrca.io import artifact` / `from causalrca.io.artifact import write_artifact` 可用。

为什么用模块级 `__getattr__` 而不是 `from ... import *` 或 `globals().update(...)`：
1. 禁止动态注入 —— 它让 AST 分析、IDE 跳转、类型检查全部失效；
2. 必须能接住对**本模块属性**的赋值：测试与 `run_v23_all` 会直接写
   `artifact.V23_SUBDIRS = tmp` / `artifact.V23_OUTPUT = tmp` / `artifact.new_run_id = ...`，
   而实现模块的函数读的是实现模块自己的全局变量。因此本模块把白名单属性的读写
   都转发给实现模块（`_ShimModule`），否则 monkeypatch 打不中实现，报告链会写错目录。
"""
import sys
from types import ModuleType

import causalrca.io.artifact as _impl

# 白名单：实现模块的公开契约（新增公开成员时必须同步登记）
_ALLOWED = frozenset({
    "SCHEMA",
    "V23_OUTPUT",
    "V23_SUBDIRS",
    "new_run_id",
    "write_artifact",
    "write_text_artifact",
    "sha256_file",
    "build_manifest",
    "legacy_input",
    "repo_relative",
})

# 导入时的原值快照：pytest 的 monkeypatch 认为本模块 dict 里没有这些名字（读走
# __getattr__），撤销时会执行 delattr；此处把 delattr 解释为「恢复原值」，
# 绝不能真的从实现模块删掉函数。
_ORIGINALS = {name: getattr(_impl, name) for name in _ALLOWED}


def __getattr__(name):
    if name in _ALLOWED:
        return getattr(_impl, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | _ALLOWED)


class _ShimModule(ModuleType):
    """把白名单属性的读写都转发到实现模块。"""

    def __setattr__(self, name, value):
        if name in _ALLOWED:
            setattr(_impl, name, value)
        else:
            super().__setattr__(name, value)

    def __delattr__(self, name):
        if name in _ALLOWED:
            setattr(_impl, name, _ORIGINALS[name])
        else:
            super().__delattr__(name)


sys.modules[__name__].__class__ = _ShimModule

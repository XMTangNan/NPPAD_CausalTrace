"""兼容 shim（过渡期）：日志/落盘辅助的旧路径。

唯一实现：
- `logger / ERROR_LOG / log_error` → `causalrca.io.logging`
- `save_json / load_json / save_text` → `causalrca.io.files`

本模块只做显式 re-export，不含逻辑。批 C 删除。
"""
from causalrca.io.files import load_json, save_json, save_text  # noqa: F401
from causalrca.io.logging import ERROR_LOG, log_error, logger  # noqa: F401

__all__ = ["ERROR_LOG", "log_error", "logger", "save_json", "load_json", "save_text"]

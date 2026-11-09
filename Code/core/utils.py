"""共享工具：日志、错误跟踪记录、持久化辅助。

日志部分（logger / ERROR_LOG / log_error）的唯一实现已迁至 `causalrca.io.logging`，
本模块做显式 re-export 以保留 `from core.utils import logger` 旧路径。
`save_json/load_json/save_text` 暂留此处（本轮只搬 logger）。
"""
import json
from pathlib import Path

from causalrca.config.paths import RESULT_DIR
from causalrca.io.logging import ERROR_LOG, log_error, logger  # noqa: F401  re-export

__all__ = ["ERROR_LOG", "log_error", "logger", "save_json", "load_json", "save_text"]


def save_json(obj, name, result_dir=None):
    d = result_dir or RESULT_DIR
    path = Path(d) / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"saved -> {path}")
    return path


def load_json(name, result_dir=None):
    d = result_dir or RESULT_DIR
    path = Path(d) / f"{name}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_text(text, name, result_dir=None):
    d = result_dir or RESULT_DIR
    path = Path(d) / f"{name}.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info(f"saved -> {path}")
    return path

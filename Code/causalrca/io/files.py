"""JSON / 文本落盘辅助（唯一实现）。

2026-11-10 从 `core/utils.py` 迁入（批 A）。行为逐字保持：`json.dump(...,
ensure_ascii=False, indent=2, default=str)`，路径为 `<dir>/<name>.json`。
旧路径 `from core.utils import save_json` 由 `core/utils.py` 显式 re-export。
"""
import json
from pathlib import Path

from causalrca.config.paths import RESULT_DIR
from causalrca.io.logging import logger

__all__ = ["save_json", "load_json", "save_text"]


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

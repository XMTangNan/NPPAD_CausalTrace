"""共享日志（唯一实现）：logger / ERROR_LOG / log_error。

承接旧 `Code/core/utils.py` 中的日志部分，日志器名、格式与 ERROR_LOG 路径保持不变；
`save_json/load_json/save_text` 仍留在 `core/utils.py`（本轮只搬 logger）。
"""
import json
import logging
from datetime import datetime

from causalrca.config.paths import OUTPUT_DIR

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("NPPAD-CausalTrace")
ERROR_LOG = OUTPUT_DIR / "error_log.jsonl"


def log_error(category, description, root_cause, solution, status="open"):
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "category": category,
             "description": description, "root_cause": root_cause,
             "solution": solution, "status": status}
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.warning(f"[{category}] {description} -> {solution} ({status})")

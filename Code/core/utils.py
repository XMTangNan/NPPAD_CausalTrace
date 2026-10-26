"""共享工具：日志、错误跟踪记录、持久化辅助。"""
import json, logging
from pathlib import Path
from datetime import datetime
from config import OUTPUT_DIR, RESULT_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("NPPAD-CausalTrace")
ERROR_LOG = OUTPUT_DIR / "error_log.jsonl"

def log_error(category, description, root_cause, solution, status="open"):
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "category": category, "description": description, "root_cause": root_cause, "solution": solution, "status": status}
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.warning(f"[{category}] {description} -> {solution} ({status})")

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

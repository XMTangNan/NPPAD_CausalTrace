"""v2.3 产物统一写入口（唯一实现，见 Code/STRUCTURE.md §2/§3）。

所有 v2.3 执行器必须通过本模块落盘，保证同一 run_id 的产物同源可追溯：
  - 顶层固定注入 schema / artifact / run_id / created_at / accident_type / group / source
  - 路径固定落在 rca_config_v21.V23_SUBDIRS 下，禁止执行器自己拼旧路径
  - manifest.json 记录全量产物的 sha256，供可复现性验收
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from rca.rca_config_v21 import V23_OUTPUT, V23_SUBDIRS
from core.utils import logger

SCHEMA = "causaltrtrace/v2.3"


def new_run_id(prefix: str = "v23") -> str:
    """生成一次端到端计算的 run_id；同一次计算的全部产物必须共用。"""
    return f"{prefix}-{datetime.now().strftime('%Y%m%dT%H%M%S')}"


def write_artifact(name: str, payload: Dict[str, Any], subdir: str, *,
                   run_id: str, accident_type: Optional[str] = None,
                   group: Optional[str] = None, source: Optional[str] = None,
                   artifact: Optional[str] = None) -> Path:
    """把 payload 写成 `output/rca_v23/<subdir>/<name>.json`，顶层补齐溯源字段。"""
    if subdir not in V23_SUBDIRS:
        raise KeyError(f"未知产物子目录 {subdir!r}，只允许 {sorted(V23_SUBDIRS)}")
    doc: Dict[str, Any] = {
        "schema": SCHEMA,
        "artifact": artifact or name,
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "accident_type": accident_type,
        "group": group,
        "source": source,
    }
    doc.update(payload)
    out_dir = V23_SUBDIRS[subdir]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"[v23] saved -> {path}")
    return path


def write_text_artifact(name: str, text: str, subdir: str, *,
                        run_id: str, suffix: str = ".txt", **meta) -> Path:
    """文本/HTML 类产物：同行写入 .meta.json 记录溯源字段。"""
    if subdir not in V23_SUBDIRS:
        raise KeyError(f"未知产物子目录 {subdir!r}")
    out_dir = V23_SUBDIRS[subdir]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}{suffix}"
    path.write_text(text, encoding="utf-8")
    write_artifact(f"{name}{suffix}.meta", {**meta, "bytes": len(text.encode('utf-8')),
                                            "sha256": sha256_file(path)},
                   subdir, run_id=run_id, artifact=name)
    logger.info(f"[v23] saved -> {path}")
    return path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(run_id: str, include: Iterable[str] = ("*.json", "*.csv", "*.png", "*.html", "*.md"),
                   skip: Iterable[str] = ("manifest.json",)) -> Path:
    """扫描 rca_v23 全量产物，写 manifest.json（path/size/sha256/run_id）。"""
    skip = set(skip)
    entries = []
    for sub in sorted(V23_SUBDIRS):
        d = V23_SUBDIRS[sub]
        for pattern in include:
            for p in sorted(d.glob(pattern)):
                if p.name in skip:
                    continue
                rel = p.relative_to(V23_OUTPUT).as_posix()
                entries.append({"path": rel, "dir": sub, "bytes": p.stat().st_size,
                                "sha256": sha256_file(p)})
    doc = {
        "schema": SCHEMA,
        "artifact": "manifest",
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "root": V23_OUTPUT.as_posix(),
        "n_files": len(entries),
        "files": entries,
    }
    path = V23_OUTPUT / "manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    logger.info(f"[v23] manifest -> {path} ({len(entries)} files)")
    return path

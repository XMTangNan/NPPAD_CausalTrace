"""v2.3 产物统一写入口（唯一实现，见 Code/STRUCTURE.md §2/§3）。

承接旧 `Code/rca/v21/artifact.py`，逻辑与取值逐字一致，仅把依赖改为新包路径。

所有 v2.3 执行器必须通过本模块落盘，保证同一 run_id 的产物同源可追溯：
  - 顶层固定注入 schema / artifact / run_id / created_at / accident_type / group / source
  - 路径一律由「run 根」派生：output/runs/<run_id>/<子目录>/，禁止执行器自己拼绝对路径
  - manifest.json 记录全量产物的 sha256，供可复现性验收
  - 写盘/建 manifest 成功后刷新 output/latest，指向当前有效 run

运行绑定（run 根）：一次进程只服务一个 run。`bind_run(run_id)` 绑定当前 run；
`new_run_id()` 生成即绑定；`write_artifact(run_id=...)` 遇到不同 run_id 会自动切换绑定。
没有绑定又未显式给 run_id 时立即报错，避免把产物写到无主目录。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from causalrca.config.constants_v21 import (
    V21_OUTPUT, V23_LATEST, V23_RUNS, V23_SUBDIR_NAMES,
)
from causalrca.config.constants_v23 import (
    N_BOOTSTRAP_SPEC, run_mode_for, stability_claims_allowed,
)
from causalrca.config.paths import PROJECT_ROOT
from causalrca.io.logging import logger

SCHEMA = "causaltrace/v2.3"

# 当前进程绑定的 run_id（None = 未绑定）；仅供 run_root()/subdir_path() 解析用。
_RUN_ID: Optional[str] = None


# ==================== run 根 ====================
def run_path(run_id: str) -> Path:
    """由 run_id 派生 run 根（纯函数，无副作用、不改绑定）。"""
    if not run_id:
        raise ValueError("run_id 不能为空")
    return V23_RUNS / run_id


def bind_run(run_id: str) -> Path:
    """把当前进程绑定到某个 run（同一次计算的全部产物必须共用）。"""
    global _RUN_ID
    root = run_path(run_id)
    root.mkdir(parents=True, exist_ok=True)
    _RUN_ID = run_id
    return root


def current_run_id() -> Optional[str]:
    return _RUN_ID


def run_root(run_id: Optional[str] = None) -> Path:
    """解析 run 根：显式 run_id 优先（并同步切换绑定），否则用当前绑定。"""
    if run_id:
        if run_id != _RUN_ID:
            return bind_run(run_id)
        return run_path(run_id)
    if not _RUN_ID:
        raise RuntimeError("未绑定 run：入口先 artifact.bind_run(run_id)，或显式传 run_id")
    return run_path(_RUN_ID)


def subdir_path(name: str, run_id: Optional[str] = None) -> Path:
    """run 根下的产物子目录（唯一合法写法；不存在则建）。"""
    if name not in V23_SUBDIR_NAMES:
        raise KeyError(f"未知产物子目录 {name!r}，只允许 {list(V23_SUBDIR_NAMES)}")
    d = run_root(run_id) / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def subdirs(run_id: Optional[str] = None) -> Dict[str, Path]:
    """当前 run 的全部子目录名 → 绝对路径。"""
    root = run_root(run_id)
    return {name: root / name for name in V23_SUBDIR_NAMES}


def set_latest(run_id: str) -> Path:
    """刷新 output/latest（纯文本，跨平台可靠；内容=run_id）。"""
    path = Path(V23_LATEST)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{run_id}\n", encoding="utf-8")
    return path


def latest_run() -> Optional[str]:
    """读 output/latest；文件缺失时取 runs/ 下字典序最大的 run（无则 None）。"""
    path = Path(V23_LATEST)
    if path.exists():
        rid = path.read_text(encoding="utf-8").strip()
        if rid:
            return rid
    cands = sorted(p.name for p in Path(V23_RUNS).iterdir() if p.is_dir()) \
        if Path(V23_RUNS).exists() else []
    return cands[-1] if cands else None


def legacy_input(name: str) -> Path:
    """v2.1 遗留的只读输入（output/rca_v21/<name>）。

    v2.3 只读不回写；所有对 v2.1 根的直接引用都走这里，避免执行器各自拼路径。
    """
    return V21_OUTPUT / name


def repo_relative(path) -> str:
    """转成仓库内相对路径（POSIX 风格），保证产物里记录的来源与 CWD 无关。"""
    p = Path(path).resolve()
    try:
        return p.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def new_run_id(prefix: str = "v23") -> str:
    """生成一次端到端计算的 run_id；同一次计算的全部产物必须共用。"""
    run_id = f"{prefix}-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    bind_run(run_id)
    return run_id


def write_artifact(name: str, payload: Dict[str, Any], subdir: str, *,
                   run_id: str, accident_type: Optional[str] = None,
                   group: Optional[str] = None, source: Optional[str] = None,
                   artifact: Optional[str] = None) -> Path:
    """把 payload 写成 `<run 根>/<subdir>/<name>.json`，顶层补齐溯源字段。"""
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
    out_dir = subdir_path(subdir, run_id)
    path = out_dir / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"[v23] saved -> {path}")
    return path


def write_text_artifact(name: str, text: str, subdir: str, *,
                        run_id: str, suffix: str = ".txt", **meta) -> Path:
    """文本/HTML 类产物：同行写入 .meta.json 记录溯源字段。"""
    out_dir = subdir_path(subdir, run_id)
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


def _probe_lineage(path: Path):
    """读 JSON 产物顶层 run_id/schema（非 JSON 或损坏返回 (None, None)）。"""
    if path.suffix != ".json":
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            head = json.load(f)
    except Exception:  # noqa: BLE001 — 血缘探测不得影响 manifest 生成
        return None, None
    if not isinstance(head, dict):
        return None, None
    return head.get("run_id"), head.get("schema")


def _probe_n_bootstrap(root: Path) -> Optional[int]:
    """从本 run 已落盘产物推断 bootstrap 次数（signatures → 全链 summary）。

    只读已有 JSON，不重算；推断不出返回 None（记为 not_recorded，不冒充生产档）。
    """
    sig_dir = root / "signatures"
    if sig_dir.exists():
        for p in sorted(sig_dir.glob("*.json")):
            try:
                with open(p, encoding="utf-8") as f:
                    n = json.load(f).get("n_bootstrap")
            except Exception:  # noqa: BLE001 — 推断失败不影响 manifest 生成
                continue
            if isinstance(n, int):
                return n
    s = root / "reports" / "run_v23_all_summary.json"
    if s.exists():
        try:
            with open(s, encoding="utf-8") as f:
                n = (json.load(f).get("parameters") or {}).get("n_bootstrap")
        except Exception:  # noqa: BLE001
            n = None
        if isinstance(n, int):
            return n
    return None


def build_manifest(run_id: str, include: Iterable[str] = ("*.json", "*.csv", "*.png", "*.html", "*.md", "*.txt"),
                   skip: Iterable[str] = ("manifest.json",),
                   citable: Optional[bool] = None,
                   refresh_latest: bool = True) -> Path:
    """扫描本 run 根下全量产物，写 <run 根>/manifest.json（path/size/sha256/lineage）。

    `path` 一律是相对本 run 根的 POSIX 路径，便于迁移/比对与禁止跨 run 串源。
    """
    skip = set(skip)
    root = run_root(run_id)
    entries = []
    for sub in sorted(V23_SUBDIR_NAMES):
        d = root / sub
        if not d.exists():
            continue
        for pattern in include:
            for p in sorted(d.glob(pattern)):
                if p.name in skip:
                    continue
                rel = p.relative_to(root).as_posix()
                own_run, own_schema = _probe_lineage(p)
                entries.append({"path": rel, "dir": sub, "bytes": p.stat().st_size,
                                "sha256": sha256_file(p),
                                "run_id": own_run, "schema": own_schema})
    runs: Dict[str, int] = {}
    n_json_no_run = 0
    n_non_json = 0
    for e in entries:
        if e["run_id"]:
            runs[e["run_id"]] = runs.get(e["run_id"], 0) + 1
        elif e["path"].endswith(".json"):
            n_json_no_run += 1
        else:
            n_non_json += 1
    n_bootstrap = _probe_n_bootstrap(root)
    run_mode = run_mode_for(n_bootstrap)
    doc = {
        "schema": SCHEMA,
        "artifact": "manifest",
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "root": root.as_posix(),
        "n_files": len(entries),
        "n_bootstrap": n_bootstrap,
        "run_mode": run_mode,
        # citable 不再由 bootstrap 次数自动推导（V23-DESIGN-044）：需人工显式声明；
        # 默认 null = 未声明。是否可声称边稳定性由 stability_claims_allowed 单独判定。
        # 2026-11-11：显式声明由调用方传入（run_v23_all --citable），仅 spec 档允许为 True。
        "citable": citable,
        "stability_claims_allowed": stability_claims_allowed(n_bootstrap),
        "lineage": {
            "run_id_of_manifest": run_id,
            "runs": runs,
            "n_json_without_run_id": n_json_no_run,
            "n_non_json_files": n_non_json,
            "note": ("run_id 取自产物 JSON 顶层；非 JSON（报告 txt/html 等）单列计数。"
                     "runs 中出现其他 run_id 即上游批次输入，可据此定位来源批次"),
        },
        "files": entries,
    }
    path = root / "manifest.json"
    if not doc["stability_claims_allowed"]:
        logger.warning(f"[v23] n_bootstrap={n_bootstrap}（未达正式口径 {N_BOOTSTRAP_SPEC}）："
                       f"报告中必须打印实际次数，不得声称边稳定性、不得引用 consistency_rate 作结论")
    # latest 正常刷新：默认即 5 次，若按旧规则拒刷会让工程链不可用（V23-DESIGN-044）
    # refresh_latest=False 仅供实验档显式关闭（V23-DESIGN-085），生产默认不变。
    doc["latest_updated"] = bool(refresh_latest)
    if refresh_latest:
        set_latest(run_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    logger.info(f"[v23] manifest -> {path} ({len(entries)} files, run_mode={run_mode}, "
                f"n_bootstrap={n_bootstrap})")
    return path

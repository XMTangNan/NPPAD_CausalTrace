"""架构守卫测试：分层、依赖方向、产物写盘唯一入口、代际边界。

最终结构（2026-11-10 批 A–C 之后）：
- `causalrca/{config,io,data,features,causal,evidence,reporting,cli}` = v2.3 正式实现；
- `legacy/v21/` = 冻结的 v2.1（只读，`FROZEN.md`）；
- `rca/` 只剩一个说明性 README，**不得有任何 .py**。
依赖方向单向：`causalrca` ↛ `legacy`/`rca`/`core`/`accidents`；`legacy` ↛ `rca`。
"""
import ast
import importlib
import pathlib

import pytest

LAYERS = {"config", "io", "data", "features", "causal", "evidence", "reporting", "cli"}
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]   # Code/
CAUSALRCA = PROJECT_ROOT / "causalrca"


def _pkg_root(name: str) -> pathlib.Path:
    return pathlib.Path(importlib.import_module(name).__file__).parent


def test_causalrca_layers_exist():
    root = _pkg_root("causalrca")
    for layer in LAYERS:
        assert (root / layer / "__init__.py").is_file(), f"缺少分层 causalrca/{layer}"


def test_causalrca_never_imports_legacy_paths():
    """v2.3 只允许依赖标准库/第三方/自身，禁止反向 import 旧代际。"""
    root = _pkg_root("causalrca")
    forbidden = {"rca", "core", "accidents", "legacy", "config", "plan1"}
    files = sorted(root.rglob("*.py"))
    assert files
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            else:
                continue
            for r in roots:
                assert r not in forbidden, f"{p.relative_to(root)} 反向依赖了禁用根 `{r}`"


def test_legacy_never_imports_removed_rca_package():
    """v2.1 冻结代码不得再引用已删除的 rca/ 包。"""
    root = _pkg_root("legacy")
    for p in sorted(root.rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module]
            for m in mods:
                assert not m.startswith("rca"), f"{p.relative_to(root)} 仍 import `{m}`"


def test_rca_directory_has_no_python_modules():
    """rca/ 只允许 README.md（过渡说明），不得再有可导入模块。"""
    code = pathlib.Path(__file__).resolve().parents[1]
    rca = code / "rca"
    if not rca.exists():
        return
    assert not list(rca.rglob("*.py")), "rca/ 下仍有 .py 模块"


def test_causalrca_has_no_generation_named_directories():
    """v2.3 新包不得再出现 `v21/`、`v23/` 这类代际目录（代际只在 legacy/ 里）。"""
    root = _pkg_root("causalrca")
    bad = [p.name for p in root.rglob("*") if p.is_dir() and ("v21" in p.name or "v23" in p.name)]
    assert not bad, f"causalrca 下仍有代际目录：{bad}"


def test_v23_thresholds_are_centralized_and_unchanged():
    """阈值只在 causalrca.config.constants_v23 定义，各模块取值不变（搬迁不许改口径）。"""
    from causalrca.config import constants_v23 as C23
    from causalrca.causal import severity_aggregation as SA
    from causalrca.causal import source_tracing as ST
    from causalrca.reporting import llm_explanation as LE

    assert (C23.TOP_K_ROOT, C23.B_CLIP) == (5, 5.0)
    assert C23.CLIP_SENSITIVITY == (3.0, 5.0, 10.0)
    assert (C23.MAX_PATH_LEN, C23.MAX_PATH_DEPTH, C23.MAX_PATHS_PER_ROOT) == (4, 5, 20)
    assert (C23.STABILITY_COND_MAX, C23.STABILITY_CLIP_RATIO_MAX) == (1e4, 0.35)
    assert (C23.EDGE_DIFF_THRESHOLD, C23.MECH_JACCARD_THRESHOLD) == (0.5, 0.5)
    assert (C23.RRF_K, C23.LLM_TIMEOUT_S, C23.LLM_FAITHFUL_DECIMALS) == (60, 30, 2)

    assert ST.B_CLIP == C23.B_CLIP and ST.TOP_K_ROOT == C23.TOP_K_ROOT
    assert ST.STABILITY_COND_MAX == C23.STABILITY_COND_MAX
    assert SA.EDGE_DIFF_THRESHOLD == C23.EDGE_DIFF_THRESHOLD
    assert LE.V23_LLM_TIMEOUT_S == C23.LLM_TIMEOUT_S
    assert LE.FAITHFUL_DECIMALS == C23.LLM_FAITHFUL_DECIMALS
    assert LE.FLOAT_TOL == C23.LLM_FLOAT_TOL


# v2.3 产物闭包：这些模块只允许通过 causalrca.io.artifact 落盘
V23_CLOSURE = (
    "causalrca.cli.run_variable_selector",
    "causalrca.data.audit",
    "causalrca.cli.run_v23_tolerances",
    "causalrca.cli.run_v23_end2end",
    "causalrca.reporting.run_v23_graphs",
    "causalrca.causal.severity_aggregation",
    "causalrca.cli.run_v23_llm_explanation",
    "causalrca.cli.run_v23_report",
    "causalrca.cli.run_v23_all",
)

_WRITE_FUNCS = {"open", "write_text", "write_bytes", "mkdir", "to_csv", "savefig", "dump"}


def _direct_writes(src: str):
    hits = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = getattr(f, "attr", None) or getattr(f, "id", None)
        if name not in _WRITE_FUNCS:
            continue
        if name != "open":
            hits.append((node.lineno, name))
            continue
        mode = None
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            mode = node.args[1].value
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = kw.value.value
        if isinstance(mode, str) and any(c in mode for c in "wax+"):
            hits.append((node.lineno, f"open(mode={mode!r})"))
    return hits


def test_v23_closure_writes_only_via_artifact():
    for modname in V23_CLOSURE:
        mod = importlib.import_module(modname)
        src = open(mod.__file__, encoding="utf-8").read()
        hits = _direct_writes(src)
        assert not hits, f"{modname} 绕过 artifact 直接写盘: {hits}"
        assert "V21_OUTPUT" not in src, f"{modname} 仍引用 v2.1 产物根（应走 artifact.legacy_input）"


def test_v21_legacy_writers_are_outside_v23_closure():
    legacy = {"legacy.v21.pipeline", "legacy.v21.signatures", "legacy.v21.run_r0",
              "legacy.v21.run_r3", "legacy.v21.run_baselines", "legacy.v21.run_chains",
              "legacy.v21.run_source_tracing"}
    assert not (legacy & set(V23_CLOSURE))


# ==================== 批 E：产物唯一根（output/runs/<run_id>/）====================
_LEGACY_ROOT_LITERALS = ("output/rca_v23", "rca_v23", "V23_SUBDIRS", "V23_OUTPUT")


def test_no_module_hardcodes_legacy_output_root():
    """唯一根由 artifact 按 run_id 派生：任何模块都不许再拼旧根字面路径。"""
    bad = []
    for p in CAUSALRCA.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for lit in _LEGACY_ROOT_LITERALS:
            if lit in text:
                bad.append(f"{p.relative_to(CAUSALRCA)}: {lit}")
    assert not bad, "仍出现旧产物根字面量（应改用 artifact.subdir_path/run_root）: " + str(bad)


def test_run_root_is_derived_from_run_id_only():
    """runs/<run_id>/ 由 artifact 派生；未绑定 run 时必须显式报错而非落到无主目录。"""
    from causalrca.io import artifact as A
    assert A.run_path("v23-demo").name == "v23-demo"
    assert A.run_path("v23-demo").parent == A.V23_RUNS
    saved = A._RUN_ID
    try:
        A._RUN_ID = None
        with pytest.raises(RuntimeError):
            A.run_root()
    finally:
        A._RUN_ID = saved


def test_output_has_single_run_root_contract():
    """output/ 顶层只允许 runs/、archive/、latest、README.md（+历史日志）。"""
    allowed = {"runs", "archive", "latest", "README.md", "error_log.jsonl"}
    top = {p.name for p in (PROJECT_ROOT / "output").iterdir()}
    assert top <= allowed, f"output/ 顶层出现非约定目录: {sorted(top - allowed)}"


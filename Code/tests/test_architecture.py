"""架构守卫测试：新包分层、旧路径 shim、依赖方向、落盘唯一入口。

约定（2026-11-10 批 A）：
- `causalrca/{config,io,data,features,causal,evidence,reporting,cli}` 是 v2.3 正式实现；
- `rca/v21/*.py` 是**过渡期显式 shim**（批 C 删除），只做 re-export，不得含逻辑；
- 依赖方向单向：`causalrca` 不得 import `rca` / `core` / `accidents` / `legacy` / 顶层 `config`。
"""
import ast
import importlib
import pathlib

import pytest

# 旧路径 -> 新路径（过渡期 shim 必须解析到同一对象）
SHIM_PAIRS = [
    ("rca.v21.data", "causalrca.data.loader"),
    ("rca.v21.data_audit", "causalrca.data.audit"),
    ("rca.v21.variable_selector", "causalrca.features.variable_selector"),
    ("rca.v21.paper_anchor", "causalrca.features.paper_anchor"),
    ("rca.v21.signatures", "causalrca.causal.signatures"),
    ("rca.v21.source_tracing", "causalrca.causal.source_tracing"),
    ("rca.v21.severity_aggregation", "causalrca.causal.severity_aggregation"),
    ("rca.v21.tolerance", "causalrca.evidence.tolerance"),
    ("rca.v21.deviation", "causalrca.evidence.deviation"),
    ("rca.v21.knowledge", "causalrca.evidence.knowledge"),
    ("rca.v21.external_knowledge", "causalrca.evidence.external_knowledge"),
    ("rca.v21.physics_constraints", "causalrca.evidence.physics_constraints"),
    ("rca.v21.llm_explanation", "causalrca.reporting.llm_explanation"),
    ("rca.v21.report_template", "causalrca.reporting.report_template"),
    ("rca.v21.run_v23_all", "causalrca.cli.run_v23_all"),
    ("rca.v21.accident_config", "causalrca.config.accident_config"),
]


def test_legacy_shims_resolve_to_same_objects():
    """旧路径可用，且暴露的是新包**同一个对象**（不是复制）。"""
    for old, new in SHIM_PAIRS:
        legacy = importlib.import_module(old)
        impl = importlib.import_module(new)
        public = [n for n in getattr(impl, "__all__", dir(impl)) if not n.startswith("_")]
        assert public, f"{new} 没有任何公开符号"
        for name in public:
            if getattr(impl, name, None) is None:
                continue
            assert getattr(legacy, name) is getattr(impl, name), f"{old}.{name} 与 {new} 不是同一对象"


def test_legacy_config_and_paths_still_import():
    """旧常量/路径入口仍可用。"""
    from causalrca.config import constants_v21 as rca_config_v21  # noqa: F401
    import config  # noqa: F401
    import core.utils as legacy_utils  # noqa: F401

    from causalrca.config import constants_v21 as new_consts
    from causalrca.io import logging as new_logging
    from causalrca.io.files import save_json as new_save_json

    assert rca_config_v21.V23_OUTPUT == new_consts.V23_OUTPUT
    assert legacy_utils.logger is new_logging.logger
    assert legacy_utils.save_json is new_save_json


def test_shim_assignment_reaches_implementation(monkeypatch, tmp_path):
    """向 shim 属性赋值必须落到实现模块（monkeypatch 报告链依赖此行为）。"""
    from causalrca.io import artifact as legacy_artifact
    from causalrca.io import artifact as new_artifact

    subs = {"reports": tmp_path}
    monkeypatch.setattr(legacy_artifact, "V23_SUBDIRS", subs)
    assert new_artifact.V23_SUBDIRS is subs
    assert legacy_artifact.V23_SUBDIRS is subs

    monkeypatch.setattr(legacy_artifact, "new_run_id", lambda prefix="v23": "fixed")
    assert new_artifact.new_run_id("v23") == "fixed"


def test_unknown_attribute_still_raises():
    """白名单外不得静默返回实现模块的私有成员。"""
    from causalrca.io import artifact as legacy_artifact

    with pytest.raises(AttributeError):
        getattr(legacy_artifact, "not_a_real_symbol")


def test_path_anchors_point_to_repo():
    """锚点搬迁后仍指向正确目录（同名不同义必须分开断言）。"""
    from causalrca.config import paths, constants_v21, registry

    repo_root = paths.PROJECT_ROOT
    assert (repo_root / "Code" / "causalrca").is_dir()
    assert paths.CODE_ROOT == repo_root / "Code"
    assert paths.REPO_DATA == repo_root / "nppad_repo" / "Operation_csv_data"

    # constants_v21 沿用旧契约：PROJECT_ROOT = Code/
    assert constants_v21.PROJECT_ROOT == paths.CODE_ROOT
    assert constants_v21.V23_OUTPUT == paths.CODE_ROOT / "output" / "rca_v23"

    assert registry.CAUSALTRACE_ROOT == repo_root
    assert registry.REPO_DATA == paths.REPO_DATA


def test_v23_thresholds_are_centralized_and_unchanged():
    """S3：v2.3 阈值只在 causalrca.config.constants_v23 定义，各模块取值不变。"""
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
    """返回模块里绕过 artifact 的直接写盘调用（行号, 形式）。"""
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
    """S4：v2.3 闭包禁止直接 mkdir/open(w)/to_csv 写产物，且不得触碰 V21_OUTPUT。"""
    for modname in V23_CLOSURE:
        mod = importlib.import_module(modname)
        src = open(mod.__file__, encoding="utf-8").read()
        hits = _direct_writes(src)
        assert not hits, f"{modname} 绕过 artifact 直接写盘: {hits}"
        assert "V21_OUTPUT" not in src, f"{modname} 仍引用 v2.1 产物根（应走 artifact.legacy_input）"


def test_v21_legacy_writers_are_outside_v23_closure():
    """v2.1 写盘模块不得被误当成 v2.3 闭包成员。"""
    legacy = {"rca.v21.pipeline", "rca.v21.signatures", "rca.v21.run_r0", "rca.v21.run_r3",
              "rca.v21.run_baselines", "rca.v21.run_chains", "rca.v21.run_source_tracing"}
    assert not (legacy & set(V23_CLOSURE))


# 禁止的依赖根：v2.3 新包不允许反向依赖旧包/冻结代码
FORBIDDEN_ROOTS = {"rca", "core", "accidents", "legacy", "config", "plan1"}


def test_causalrca_never_imports_legacy_paths():
    """依赖方向：`causalrca.*` 只能依赖标准库/第三方/自身，禁止反向 import 旧路径。"""
    root = pathlib.Path(importlib.import_module("causalrca").__file__).parent
    files = sorted(root.rglob("*.py"))
    assert files, "未找到 causalrca 包"
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
                assert r not in FORBIDDEN_ROOTS, \
                    f"{p.relative_to(root)} 反向依赖了禁用根 `{r}`"


def test_no_dynamic_globals_injection():
    """shim 禁止 globals().update(vars(...)) 式动态注入（会破坏 AST/IDE/类型检查）。"""
    for modname in ("rca.v21.artifact", "rca.rca_config_v21", "rca.v21.accident_config",
                    "config", "core.utils"):
        mod = importlib.import_module(modname)
        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            assert not (isinstance(func, ast.Attribute) and func.attr == "update"
                        and isinstance(func.value, ast.Call)
                        and getattr(func.value.func, "id", None) == "globals"), modname

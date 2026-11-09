"""架构守卫测试（S0）：旧导入路径必须仍可用，且解析到新包同一实现。

本文件刻意不依赖业务产物，只做导入与对象同一性断言：
1. 旧路径可导入（`rca.v21.*` / `rca.rca_config_v21` / `config` / `core.utils`）；
2. 旧路径暴露的对象与新包是**同一个对象**（不是复制），避免「同名不同契约」；
3. `__file__` 锚点在搬迁后仍解析到正确目录。
"""
import ast
import importlib

import pytest

# 允许在 `Code/` 下用 `PYTHONPATH=. ` 运行；这里只守卫导入形态


def test_legacy_import_paths_still_work():
    """第一条：旧导入路径仍可用（shim 生效）。"""
    from rca.v21 import artifact, data, signatures, source_tracing  # noqa: F401
    from rca.v21.artifact import write_artifact  # noqa: F401
    from rca import rca_config_v21  # noqa: F401
    from rca.v21 import accident_config  # noqa: F401
    import config  # noqa: F401
    from core.utils import logger, save_json  # noqa: F401

    assert callable(write_artifact)
    assert rca_config_v21.V23_OUTPUT is not None


def test_legacy_shims_resolve_to_same_objects():
    """旧路径解析到的对象必须与新包是同一实现（is 而非 ==）。"""
    from rca.v21 import artifact as legacy_artifact
    from rca.v21.artifact import write_artifact as legacy_write
    from rca import rca_config_v21 as legacy_cfg
    from rca.v21 import accident_config as legacy_registry
    import config as legacy_paths
    import core.utils as legacy_utils

    from causalrca.io import artifact as new_artifact
    from causalrca.config import constants_v21 as new_consts
    from causalrca.config import registry as new_registry
    from causalrca.config import paths as new_paths
    from causalrca.io import logging as new_logging

    assert legacy_write is new_artifact.write_artifact
    assert legacy_artifact.write_artifact is new_artifact.write_artifact
    assert legacy_artifact.build_manifest is new_artifact.build_manifest
    assert legacy_artifact.SCHEMA == new_artifact.SCHEMA

    assert legacy_cfg.V23_SUBDIRS is new_consts.V23_SUBDIRS
    assert legacy_cfg.V23_OUTPUT == new_consts.V23_OUTPUT
    assert legacy_cfg.STABLE_F == new_consts.STABLE_F
    assert legacy_cfg.MARKER_COLS == new_consts.MARKER_COLS

    assert legacy_registry.REGISTRY is new_registry.REGISTRY
    assert legacy_registry.get_accident is new_registry.get_accident

    assert legacy_paths.OUTPUT_DIR == new_paths.OUTPUT_DIR
    assert legacy_paths.SEVERITY_GROUPS is new_paths.SEVERITY_GROUPS

    assert legacy_utils.logger is new_logging.logger
    assert legacy_utils.log_error is new_logging.log_error


def test_shim_assignment_reaches_implementation(monkeypatch, tmp_path):
    """向 shim 属性赋值必须落到实现模块（monkeypatch 报告链依赖此行为）。"""
    from rca.v21 import artifact as legacy_artifact
    from causalrca.io import artifact as new_artifact

    subs = {"reports": tmp_path}
    monkeypatch.setattr(legacy_artifact, "V23_SUBDIRS", subs)
    assert new_artifact.V23_SUBDIRS is subs
    assert legacy_artifact.V23_SUBDIRS is subs

    monkeypatch.setattr(legacy_artifact, "new_run_id", lambda prefix="v23": "fixed")
    assert new_artifact.new_run_id("v23") == "fixed"


def test_unknown_attribute_still_raises():
    """白名单外不得静默返回实现模块的私有成员。"""
    from rca.v21 import artifact as legacy_artifact

    with pytest.raises(AttributeError):
        getattr(legacy_artifact, "not_a_real_symbol")


def test_path_anchors_point_to_repo():
    """五处锚点搬迁后仍指向正确目录（同名不同义必须分开断言）。"""
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
    """S3：v2.3 阈值只在 causalrca.config.constants_v23 定义，旧模块取值不变。"""
    from causalrca.config import constants_v23 as C23
    from rca.v21 import chains, llm_explanation as LE
    from rca.v21 import severity_aggregation as SA
    from rca.v21 import source_tracing as ST

    assert (C23.TOP_K_ROOT, C23.B_CLIP) == (5, 5.0)
    assert C23.CLIP_SENSITIVITY == (3.0, 5.0, 10.0)
    assert (C23.MAX_PATH_LEN, C23.MAX_PATH_DEPTH, C23.MAX_PATHS_PER_ROOT) == (4, 5, 20)
    assert (C23.STABILITY_COND_MAX, C23.STABILITY_CLIP_RATIO_MAX) == (1e4, 0.35)
    assert (C23.EDGE_DIFF_THRESHOLD, C23.MECH_JACCARD_THRESHOLD) == (0.5, 0.5)
    assert (C23.RRF_K, C23.LLM_TIMEOUT_S, C23.LLM_FAITHFUL_DECIMALS) == (60, 30, 2)

    # 旧模块仍暴露同名变量（读取方无需改），且取值与新包一致
    assert ST.B_CLIP == C23.B_CLIP and ST.TOP_K_ROOT == C23.TOP_K_ROOT
    assert ST.STABILITY_COND_MAX == C23.STABILITY_COND_MAX
    assert SA.EDGE_DIFF_THRESHOLD == C23.EDGE_DIFF_THRESHOLD
    assert chains.RRF_K == C23.RRF_K
    assert LE.V23_LLM_TIMEOUT_S == C23.LLM_TIMEOUT_S
    assert LE.FAITHFUL_DECIMALS == C23.LLM_FAITHFUL_DECIMALS
    assert LE.FLOAT_TOL == C23.LLM_FLOAT_TOL


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

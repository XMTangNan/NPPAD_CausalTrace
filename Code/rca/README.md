# rca/（已废弃）

v2.1 与 v2.3 的代码已分别迁移，本目录**不再包含任何可导入模块**：

| 旧路径 | 新路径 |
|---|---|
| `rca/v21/data.py` | `causalrca/data/loader.py` |
| `rca/v21/data_audit.py` | `causalrca/data/audit.py` |
| `rca/v21/variable_selector.py` / `paper_anchor.py` | `causalrca/features/` |
| `rca/v21/signatures.py` / `source_tracing.py` / `severity_aggregation.py` | `causalrca/causal/` |
| `rca/v21/tolerance.py` / `deviation.py` / `knowledge.py` / `external_knowledge.py` / `physics_constraints.py` | `causalrca/evidence/` |
| `rca/v21/llm_explanation.py` / `report_template.py` / `graph_visualization.py` / `run_v23_graphs.py` | `causalrca/reporting/` |
| `rca/v21/run_v23_*.py` / `run_variable_selector.py` / `run_ab_variable_selection.py` | `causalrca/cli/` |
| `rca/v21/pipeline.py` / `inversion.py` / `chains.py` / `baselines.py` / `calibration.py` / `ablation_*.py` / `gam_graph.py` / `cf_validation.py` / `marker_effects.py` / `run_r*.py` | `legacy/v21/`（冻结） |
| `rca/rca_config.py` / `rca_config_v21.py` / `rca_baselines.py` / `rca_data_loader.py` / `signature*.py` / `run_rca_pipeline.py` | 已删除（无引用） |

命令示例：`PYTHONPATH=. ../.venv/bin/python -m causalrca.cli.run_v23_all --dry-run`

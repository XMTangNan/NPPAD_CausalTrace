"""causalrca：CausalTrace v2.3 正式实现包（8 层）。

本包不 import 任何子模块，避免 `import causalrca` 触发建目录等 import 期副作用。
子包：
  - config/     paths、constants_v21（v2.1/v2.3 常量）、constants_v23、class_variables、
                accident_config、registry
  - io/         artifact（唯一产物写入口，output/runs/<run_id>/）、files、logging
  - data/       loader、audit
  - features/   variable_selector（V0–V7 门控）、paper_anchor
  - causal/     signatures、source_tracing、severity_aggregation、edges
  - evidence/   tolerance、deviation、physics_constraints、knowledge、external_knowledge
  - reporting/  run_v23_graphs、llm_explanation、report_template、graph_visualization
  - cli/        run_v23_all 等 8 个执行入口
v2.1 冻结实现在 legacy/v21/（只读），旧 rca/ 包已移除。
"""

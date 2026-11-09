"""causalrca：CausalTrace 的目标包结构（分批迁移中）。

本包不 import 任何子模块，避免 `import causalrca` 触发建目录等 import 期副作用。
子包：
  - config/  paths（旧 config.py）、constants_v21（旧 rca_config_v21.py）、
             registry（旧 rca/v21/accident_config.py）
  - io/      artifact（旧 rca/v21/artifact.py）、logging（旧 core/utils.py 的 logger）

旧导入路径（`config`、`rca.rca_config_v21`、`rca.v21.accident_config`、
`rca.v21.artifact`、`core.utils`）以显式 re-export shim 保留，见各旧文件文首说明。
"""

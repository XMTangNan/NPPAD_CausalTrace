"""配置层：paths / constants_v21 / registry。

本 `__init__` 刻意不 import 子模块：`constants_v21` 有 import 期建目录副作用，
若在此触发会让「只导入 registry 或 paths」也顺带建全部产物目录，改变建目录时机。
请显式 `from causalrca.config import paths` / `... import constants_v21` / `... import registry`。

命名契约（同名不同义，勿合并、勿 star re-export）：
  - paths.PROJECT_ROOT          = CausalTrace 仓库根（旧 config.py）
  - constants_v21.PROJECT_ROOT  = Code/ 目录（旧 rca_config_v21.py）
"""

"""IO 层：artifact（v2.3 统一写入口）/ logging（共享 logger）。

本 `__init__` 不 import 子模块：
  - `artifact` 在 import 期会读 constants_v21 并建产物目录；
  - 旧路径 shim（`rca.v21.artifact`、`core.utils`）自行决定加载时机。
"""

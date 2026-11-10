"""兼容 shim（过渡期）：实现已迁至 `causalrca.features.paper_anchor`。

只做显式 re-export（`import *` 依据目标模块的公开名/__all__），禁止 globals().update。
批 C 会删除本 shim，请改用新路径。
"""
from causalrca.features.paper_anchor import *  # noqa: F401,F403

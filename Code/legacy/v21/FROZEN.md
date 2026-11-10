# FROZEN：v2.1 冻结实现（legacy/v21）

> **只读。禁止被 v2.3（`causalrca/`）引用。**
> 本目录保留 v2.1 的 3 类事故（LOCA/SGATR/FLB）完整实现与执行器，用于论文复现与
> 历史结论对账；不再修 bug、不再调参、不再新增功能。任何改动都会使 v2.1 结论失去可复现性。

## 1. 冻结信息

| 项 | 值 |
|---|---|
| 冻结 commit | `44f613f`（批 A 完成、批 B 搬迁前；搬迁只改文件位置与 import 行） |
| 代码位置 | `Code/legacy/v21/` |
| 老测试 | `Code/tests/legacy/`（9 个，随本目录一起保留可跑） |
| 产物目录（只读） | `Code/output/archive/rca_v21_frozen/`（批 E 起；原 `Code/output/rca_v21/`）、`Code/output/archive/rca/`、`Code/output/archive/plan1_loca/` |
| 归档副本 | 同上（批 E 迁移，逐文件 sha256 不变，只读） |
| 状态 | 冻结（frozen），不再演进 |

## 2. 目录内容

| 模块 | 作用 |
|---|---|
| `pipeline.py` / `inversion.py` | D3 协议：反演、min-max 归一、指标、弃权 |
| `chains.py` | 13.10 两层输出与传播链（v2.3 只借用其纯函数，见 `causalrca/causal/edges.py`） |
| `baselines.py` | B0/B1/B2 基线 |
| `calibration.py` / `gam_graph.py` / `cf_validation.py` | D3 校准、GAM 图、反事实验证 |
| `ablation_core.py` / `ablation_symbolic.py` | 消融实验 |
| `marker_effects.py` | marker 有效性与列选择 |
| `run_r0.py` / `run_r1.py` / `run_r3.py` | R0/R1/R3 实验执行器 |
| `run_baselines.py` / `run_calibration.py` / `run_cf.py` | 基线与校准执行器 |
| `run_chains.py` / `run_d0.py` / `run_gam_graph.py` | 链/GAM 执行器 |
| `run_ablation_core.py` / `run_ablation_symbolic.py` | 消融执行器 |
| `run_llm_explanation.py` / `run_source_tracing.py` | v2.1 解释与溯源执行器 |

## 3. 复现命令（cd Code，PYTHONPATH=.）

```bash
# 主实验链（v2.1，3 类）
PYTHONPATH=. ../.venv/bin/python -m legacy.v21.run_r1
PYTHONPATH=. ../.venv/bin/python -m legacy.v21.run_r3

# 基线与校准
PYTHONPATH=. ../.venv/bin/python -m legacy.v21.run_baselines
PYTHONPATH=. ../.venv/bin/python -m legacy.v21.run_calibration

# 链 / GAM / 反事实 / 消融
PYTHONPATH=. ../.venv/bin/python -m legacy.v21.run_chains
PYTHONPATH=. ../.venv/bin/python -m legacy.v21.run_gam_graph
PYTHONPATH=. ../.venv/bin/python -m legacy.v21.run_cf
PYTHONPATH=. ../.venv/bin/python -m legacy.v21.run_ablation_core
PYTHONPATH=. ../.venv/bin/python -m legacy.v21.run_ablation_symbolic

# 老测试（9 个）
PYTHONPATH=. ../.venv/bin/python -m pytest tests/legacy -q
```

## 4. 依赖边界

- 本目录**允许**依赖 `causalrca` 中的公共设施（路径、日志、落盘、常量）与第三方库；
- `causalrca/` **禁止** import `legacy/`（`tests/test_architecture.py` 守卫）；
- v2.3 需要的少数纯函数（`edge_key` / `validate_path_edges` / `render_chain_text`）
  已**复制并冻结**在 `causalrca/causal/edges.py`，两边不再共享可变实现。
- 如需复用 v2.1 结论，请直接读取本目录的产物 JSON，不要改写本目录代码。

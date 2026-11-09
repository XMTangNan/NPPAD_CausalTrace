# CausalTrace 代码与产物结构约定（v2.3）

> 定稿：2026-11-10。本文件是 `Code/` 布局与 `Code/output/` 产物命名/位置的**唯一约定来源**。
> 新增模块、脚本、产物必须先对齐本文件；偏离需在 `Code/error_tracking.md` 登记 DESIGN 条目。

## 1. 分层原则

| 层 | 正式实现（唯一） | 兼容 shim（旧路径，勿新增逻辑） | 职责 |
|----|------------------|-------------------------------|------|
| 配置/注册表 | `causalrca/config/registry.py` | `rca/v21/accident_config.py` | 12 类事故元数据唯一来源：marker、注入时刻、窗口、分组、触发/关键响应、数据状态 |
| 路径常量 | `causalrca/config/paths.py` | `config.py` | 仓库根、`output/` 根、通用窗口/种子/LLM 端点 |
| 冻结常量 | `causalrca/config/constants_v21.py` | `rca/rca_config_v21.py` | v2.1 预注册常量（论文口径，禁止改值） |
| v2.3 阈值 | `causalrca/config/constants_v23.py` | 各算法模块同名变量 | v2.3 新增阈值（截断/条件数/路径上限/RRF/LLM 容差） |
| IO | `causalrca/io/artifact.py`、`causalrca/io/logging.py` | `rca/v21/artifact.py`、`core/utils.py` | 产物统一写入口 + 共享 logger |
| 数据/审计 | `rca/v21/data.py`、`data_audit.py` | — | 加载、划分、B4/B5/B6 审计 |
| 算法 | `rca/v21/{variable_selector,signatures,source_tracing,tolerance,severity_aggregation,physics_constraints,deviation,knowledge}.py` | — | 纯算法/纯函数优先，入口脚本不做算法 |
| 执行器 | `rca/v21/run_*.py` | — | 读取上游 JSON、调用算法、落盘、打印摘要 |
| 测试 | `Code/tests/` | — | 新模块必须配单测；结构约束见 `tests/test_architecture.py` |

**代码代际（哪个目录属于哪一代）**

| 目录 | 代际 | 状态 | 说明 |
|------|------|------|------|
| `causalrca/` | v2.3 目标包 | **正式实现，可改** | 分批迁移中：现存 `config/`、`io/`；其余子包按 S1–S5 逐步迁入 |
| `rca/v21/*.py`（除 `artifact.py`/`accident_config.py`） | v2.1 → v2.3 | 正式实现（算法/执行器） | 算法与执行器尚未迁入新包，仍是唯一实现 |
| `rca/v21/artifact.py`、`rca/v21/accident_config.py` | v2.1 路径 | **兼容 shim** | 显式 re-export，指向 `causalrca/` |
| `config.py`、`rca/rca_config_v21.py`、`core/utils.py` | v1/v2.1 路径 | **兼容 shim** | 显式 re-export，禁止再写新逻辑 |
| `rca/v21/run_r0.py`、`run_r3.py`、`run_baselines.py`、`run_chains.py`、`run_source_tracing.py`、`run_llm_explanation.py`、`run_v23_validation_set.py` | v2.1/v2.2 | 历史执行器（只读冻结产物） | 仍写 `output/rca_v21/`，不再更新，不得被 v2.3 链调用 |
| `accidents/` | v1 | 历史归档 | 不参与 v2.3 链 |

**依赖方向（只允许指向右/下，禁止反向）**

```
            causalrca/config/{paths,constants_v21,constants_v23,registry}
                                  │  (无第三方依赖，不 import rca.* / core.*)
                                  ▼
            causalrca/io/{artifact,logging}
                                  │
                                  ▼
   rca/v21 算法层（variable_selector → signatures → source_tracing → severity_aggregation …）
                                  │
                                  ▼
   rca/v21/run_*.py 执行器  ──►  output/rca_v23/（只经 artifact 写）
                                  │
                                  ▼
                        tests/（读代码与冻结产物）
```

铁律：`causalrca.config.*` 不得 import `rca.*`；`causalrca.io.*` 只依赖 `causalrca.config`；
旧 shim 单向依赖新包（新包不得反向 import 旧路径）；v2.3 闭包不得直接 `mkdir`/`open(w)`/`json.dump`。

**单一来源铁律**

1. 事故元数据只来自 `causalrca/config/registry.py`（旧路径 `rca/v21/accident_config.py` 为 shim）；`rca_config_v21.ACCIDENT_TYPES` 从注册表派生，禁止第二份 12 类列表。
2. 同一产物只有一条生成链：`signatures → tracing → graphs/reports → summary/diff`；下游只读上游 JSON，不重算、不复制。
3. 图、榜单、差异报告、RCA 报告必须来自**同一次**签名/溯源结果（manifest 记录生成批次 `run_id`）。
4. 缺数据的类别只在注册表中标记 `status=data_missing`，执行器跳过并落 `skipped_classes.json`；**禁止生成占位/伪结果**。
5. v2.3 链的三份跨代输入（`split_manifest` / `shared_baseline` / `marker_scale`）由 A 阶段写入 `registry/`，
   下游只读 v2.3 根；`output/rca_v21/` 仅作只读回退（`artifact.legacy_input()`），任何执行器不得回写。

## 2. 目标目录结构

```
Code/
├── STRUCTURE.md                  # 本文件
├── error_tracking.md             # 错误跟踪（唯一问题台账）
├── pyproject.toml                # 仅 pytest 配置与包元数据（不改变 PYTHONPATH=. 运行方式）
├── causalrca/                    # [v2.3 正式包，分批迁入]
│   ├── config/
│   │   ├── paths.py              # 路径与通用常量（旧 config.py）
│   │   ├── constants_v21.py      # v2.1 冻结常量（旧 rca/rca_config_v21.py）
│   │   ├── constants_v23.py      # v2.3 新增阈值（截断/条件数/RRF/LLM 容差）
│   │   └── registry.py           # 12 类注册表（旧 rca/v21/accident_config.py）
│   └── io/
│       ├── artifact.py           # v2.3 唯一写入口（旧 rca/v21/artifact.py）
│       └── logging.py            # logger / ERROR_LOG / log_error（旧 core/utils.py 日志段）
├── config.py                     # [shim] → causalrca/config/paths.py
├── core/utils.py                 # [shim] 日志 → causalrca/io/logging.py；save_json 暂留
├── rca/
│   ├── rca_config_v21.py         # [shim] → causalrca/config/constants_v21.py
│   └── v21/
│       ├── accident_config.py    # [shim] → causalrca/config/registry.py
│       ├── artifact.py           # [shim] → causalrca/io/artifact.py（读写转发，兼容 monkeypatch）
│       ├── data.py               # 加载/划分（按注册表状态门控）
│       ├── data_audit.py         # [新] B4 数据审计 + B5 Z 病态 + B6 变点
│       ├── variable_selector.py   # A1 论文锚定（候选加权、可剔除留痕）+ 四阶段漏斗
│       ├── paper_anchor.py        # A1 论文锚定单一来源（引文、早期机理筛选集代理、冲突裁决）
│       ├── knowledge.py          # 96 变量本体 + 12 类 ALLOWED_RELATIONS
│       ├── external_knowledge.py # [新] YAML → 本体合并（只扩展不覆盖）
│       ├── signatures.py         # C1 12类×3组因果签名（含 K2/偏离度挂载点）
│       ├── source_tracing.py     # C2 T=(I-B)^-1 根因排序（含 n_consensus）
│       ├── tolerance.py          # D2 每类 sev-1 独立基准 + 稳健σ + 分位数
│       ├── physics_constraints.py# D4 K2 五类近似关系残差三态
│       ├── deviation.py          # D9 偏离度 d=min(|x-μ|/σ_robust,10)
│       ├── severity_aggregation.py # C3b/C3c 汇总图 + 差异报告
│       ├── graph_visualization.py  # C3/C3a 分组 HTML + 信息门控
│       ├── llm_explanation.py      # D6 模板 rule + 类级/全局汇总（LLM 可选）+ 忠实性校验
│       ├── report_template.py      # D7 模板兜底
│       ├── run_v23_all.py        # [新] 阶段 D 一站式：审计→变量集→签名→溯源→图→汇总→报告
│       └── run_v23_graphs.py     # [新] 9/36 份分组 HTML + 12 份汇总图（只读 v23 JSON）
├── external_knowledge/           # [新] §5.2 L1-L4 YAML（与代码同仓、版本化）
│   ├── variable_dictionary.yaml
│   ├── physics_relations.yaml
│   ├── fault_templates.yaml
│   ├── forbidden_edges.yaml
│   └── tolerance_overrides.yaml
└── output/
    └── rca_v23/                  # v2.3 唯一产物根（新；旧 rca_v21/v23_* 冻结为历史）
        ├── manifest.json          # run_id、各产物相对路径/大小/sha256（artifact.build_manifest）
        ├── registry/
        │   ├── accident_registry.json      # 12 类注册表快照 + ready/partial/missing 清单
        │   ├── split_manifest.json         # train/test 分组快照（v2.1 冻结输入留痕）
        │   ├── shared_baseline.json        # 共享基线 μ/σ 快照（v2.1 冻结，S1 起由 A 阶段写入）
        │   └── marker_scale.json           # marker 尺度快照（v2.1 冻结，S1 起由 A 阶段写入）
        ├── variable_sets/
        │   ├── selection_report_{K}.json   # 论文锚定 + 四阶段漏斗报告（V0–V7、v3_status 三态、dropped_anchor/anchor_impact）
        │   └── variable_selection_summary.json
        ├── audit/data_audit_{K}.json
        ├── audit/z_pathology_audit_{K}.json
        ├── audit/changepoint_report_{K}.json
        ├── tolerances/tolerance_{K}.json
        ├── signatures/signature_{K}_{g}.json
        ├── tracing/root_candidates_{K}_{g}.json    # Top-5 + n_consensus/cond/clip 证据
        ├── tracing/topology_{K}_{g}.json           # 拓扑矩阵与节点/边
        ├── tracing/clip_sensitivity_{K}_{g}.json   # B 截断 3/5/10 敏感性
        ├── tracing/skipped_classes.json            # 缺数据/partial 跳过清单
        ├── graphs/graph_{K}_{g}.html
        ├── graphs/graph_{K}_summary.html
        ├── graphs/graph_all_summary.html
        ├── reports/summary_{K}.json                # C3b 跨组汇总
        ├── reports/diff_{K}.json                   # C3c 跨组差异
        ├── reports/deviation_{K}_{g}.json          # D9 偏离度四档计数
        ├── reports/rca_report_{K}_{g}.json
        ├── reports/rca_report_{K}_{g}.txt
        ├── reports/llm_explanation_{K}_{g}.json   # 模板级（固定 rule）
        ├── reports/llm_explanation_class_{K}.json # 类级汇总（LLM 可选，失败降级 rule）
        ├── reports/llm_explanation_global.json    # 全局结论（同上）
        ├── reports/run_v23_all_summary.json        # D10 全链 9 步状态 + rerun_reason
        ├── knowledge/knowledge_validation.json
        └── ablations/                              # 消融/对照（唯一允许再分一层的子目录）
            ├── frozen_ab_variable_selection_<run_id>.json  # 一次性 A/B 冻结件（frozen=true）
            └── ab_variable_selection.json          # 旧轮直跑产物（保留仅作溯源，不再更新）
```

**命名规则**

- 类别 `K` ∈ `{LOCA,LOCAC,SLBIC,SLBOC,SGATR,SGBTR,RW,RI,FLB,MD,LR,LLB}`；组 `g` ∈ `{low,mid,high}`。
- 文件名一律 `{产物类型}_{K}[_{g}].{json|html|txt}`，全大写类别、小写组名。
- JSON 顶层必须含 `schema`、`artifact`、`accident_type`、`group`（或 `null`）、`run_id`、`created_at`、`source`；由 `causalrca/io/artifact.py` 统一注入（旧路径 `rca/v21/artifact.py` 仅为 shim），执行器不得手写 json.dump 落盘 v2.3 产物（由 `tests/test_architecture.py` 卡住）。
- 不再出现 `v23_` 前缀子目录、`signatures_strict`、`signatures_no_ontology` 等并列目录；消融产物统一放 `output/rca_v23/ablations/{name}/`。
- 文本/HTML 产物由 `artifact.write_text_artifact` 同时写同名 `.meta.json`（含 sha256 与 run_id）。

## 3. 与旧产物的对应（收敛映射）

| 旧位置 | 新位置 | 处置 |
|--------|--------|------|
| `output/rca_v21/v23_signatures/` | `output/rca_v23/signatures/` | 重算后写入；旧目录不再更新 |
| `output/rca_v21/v23_tracing/root_candidates_*` | `output/rca_v23/tracing/` | 重算 |
| `output/rca_v21/v23_tracing/diff_report_*` | `output/rca_v23/reports/diff_{K}.json` | 由 `severity_aggregation` 在签名重算后统一重跑 |
| `output/rca_v21/v23_graphs/graph_{K}_{g}.html` | `output/rca_v23/graphs/` | 由 `run_v23_graphs.py` 从 v23 签名统一生成 |
| `output/rca_v21/v23_graphs/graph_{K}_summary.html` | `output/rca_v23/graphs/` | 同上，与分组图同批次 |
| `output/rca_v21/v23_llm/` | `output/rca_v23/reports/llm_explanation_*` | 12/36 份按注册表状态生成 |
| `output/rca_v21/variable_selection/` | `output/rca_v23/variable_sets/` | 重算 |
| `output/rca_v21/tolerances/` | `output/rca_v23/tolerances/` | 重算 |
| `output/rca_v21/{signatures,tracing,chains,...}` | 保持不动 | v2.1/v2.2 历史产物，只读 |

## 4. 12 类注册表字段（accident_config.py）

每类必须给出下列字段，缺数据类别同样给全，仅 `status` 不同：

```python
{
  "code": "LOCA",
  "name_cn": "一回路热段失水事故",
  "group": 1,                       # §4.3 六个机理组
  "marker": "WLR",
  "trigger_vars": ["WLR"],
  "key_responses": ["P","TAVG","VOL","WECS","WHPI"],
  "t_inject": 110.0,
  "main_window": (300.0, 1500.0),
  "learn_window": (60.0, 1500.0),
  "baseline_window": (520.0, 2030.0),
  "severity_bands": {"low": (2,33), "mid": (34,66), "high": (67,100)},
  "n_trajectories": 100,
  "status": "ready" | "partial" | "data_missing",
  "data_dir": "nppad_repo/Operation_csv_data/LOCA",
  "notes": "…",
}
```

`status` 规则：
- `ready`：≥ 20 条轨迹且三分组各 ≥ 5；
- `partial`：有数据但不足以按原协议（如 LOCAC 仅 5 条）→ 只做描述性，不进 36 份验收；
- `data_missing`：无目录 → 执行器跳过，`skipped_classes.json` 记录，error_tracking 登记 DATA 条目。

## 5. 执行入口（可复现）

```
cd Code
# 全链（验收/发布动作，9 步；需显式声明重跑原因，见下方“运行约定”）
PYTHONPATH=. python -m rca.v21.run_v23_all --n-bootstrap 100 --rerun-reason "E-R1 出对外数字：论文表格定稿"
PYTHONPATH=. python -m rca.v21.run_v23_all --classes LOCA SGATR FLB --n-bootstrap 100 --rerun-reason "E-R2 ..."
PYTHONPATH=. python -m rca.v21.run_v23_all --dry-run          # 只打印 9 步计划（不校验 rerun_reason）
PYTHONPATH=. python -m rca.v21.run_v23_all --skip-llm         # 解释后端固定 rule（默认即 rule）
PYTHONPATH=. python -m rca.v21.run_v23_all --with-ab ...      # 追加一次性 A/B 消融（默认关闭，10 步）

# 分步重跑（开发默认路径；仍必须传同一 --run-id，否则汇总/报告拒绝混源）
PYTHONPATH=. python -m rca.v21.run_variable_selector           # A 阶段：变量选取 + 注册表快照
PYTHONPATH=. python -m rca.v21.run_ab_variable_selection       # A/B 一次性消融（不随主链；产物需冻结归档）
PYTHONPATH=. python -m rca.v21.data_audit                      # B 阶段：B4/B5/B6
PYTHONPATH=. python -m rca.v21.run_v23_tolerances --run-id <id>
PYTHONPATH=. python -m rca.v21.run_v23_end2end --n-bootstrap 100
PYTHONPATH=. python -m rca.v21.run_v23_graphs --run-id <id>
PYTHONPATH=. python -m rca.v21.severity_aggregation --run-id <id>
PYTHONPATH=. python -m rca.v21.run_v23_llm_explanation --run-id <id>   # 模板 rule + 类级/全局（--backend llm 可选）
PYTHONPATH=. python -m rca.v21.run_v23_report --run-id <id>
PYTHONPATH=. pytest tests/ -q                                  # 单测 + 回归（137 例：133 passed / 4 既有失败）
```

> 注：4 例既有失败全部在 `tests/test_v23_report_llm.py`，根因是 `output/rca_v23/tracing/` 与
> `signatures/` 来自不同批次（`v23c-*` vs `v23-*`）导致跨阶段拒绝混源，与代码结构无关；
> 需按 §6.4 重算产物后消除，登记见 `error_tracking.md`。`cd Code && pytest tests -q` 亦可
> （`pyproject.toml` 仅补 `testpaths`/`pythonpath`，`PYTHONPATH=. python -m` 方式不变）。

**运行约定（强制，方案 §6.4）**
- 默认**禁止**每次改代码就跑全链；先用「受影响单测 + 单阶段重跑（复用上游 run_id）+ 冻结产物比对」验证。
- 仅 E-R1 出对外数字 / E-R2 影响数值 / E-R3 产物结构或契约 / E-R4 跨阶段对不上 四类例外允许全链，且必须传 `--rerun-reason` 并写入 `run_v23_all_summary.json`；未声明原因的重跑不得引用。
- 每轮全链留痕：run_id、rerun_reason、耗时、步数、相对上一轮的变量集/9 组 Top-1/稳定边变化。

`run_v23_all` 结束落盘：
- `output/rca_v23/manifest.json`：`run_id`、各产物相对路径/大小/sha256（六类扩展名全覆盖）；
- `output/rca_v23/reports/run_v23_all_summary.json`：9 步 `status: ok|skipped|failed`、耗时、产物计数、跳过类、`rerun_reason`、LLM 可用性；
- 任一步失败 → summary 记 `failed` 并非 0 退出，绝不静默跳过。

**不再使用**：`output/rca_v21/v23_*` 下的 `tracing_report_v23.txt` 等旧汇总（11-04 产物，已冻结只读）。验收结论以 `reports/summary_{K}.json`、`reports/diff_{K}.json` 与 `manifest.json` 为准。

## 6. 工作分解（与方案 §6.1 对齐）

| 阶段 | 条目 | 落点 | 验收 |
|------|------|------|------|
| A | A1 变量选取（论文锚定 + 四阶段） | `variable_selector.py` + `paper_anchor.py` | V0–V7 3/3 |
| A | A2 ENTITY_TABLE 96 | 已有 | 选中变量 0 未登记 |
| A | A3 `accident_config.py` 12 类 | 新建 | 12 条记录字段完整、status 明确 |
| A | A4 3 类 A/B 对照（一次性消融） | `ablations/frozen_ab_variable_selection_<run_id>.json` | 已冻结（frozen=true），不随主链重算 |
| B | B1 数据清单 | `registry/accident_registry.json` | 3 ready + 1 partial + 8 missing 如实记录 |
| B | B2 data.py 12 类 | 按注册表加载 | 缺类抛 `ClassDataMissing` 而非臆造 |
| B | B3 变量集 | `variable_sets/` | 3/3（+LOCAC 描述性） |
| B | B4 数据审计 | `data_audit.py` | 常量列/轨迹数/窗口 |
| B | B5 Z 病态审计 | `data_audit.py` | 病态清单 + max\|z\| + σ 分布 |
| B | B6 变点检测 | `data_audit.py`（PELT/CUSUM） | 分组边界建议 + marker 稳定性评级 |
| C | C1 签名 | `signatures.py` | 3 类×3 组 9 份（12 类待数据） |
| C | C2 溯源 | `source_tracing.py` | 9 份，含 n_consensus/n_conflict |
| C | C3/C3a 分组图 | `run_v23_graphs.py` | 9 份 + 信息门控水印 |
| C | C3b 汇总图 | `severity_aggregation.py` | 3 份 |
| C | C3c 差异报告 | `severity_aggregation.py` | 3 份，与同批签名同源 |
| C | C4 marker 弱化 | `source_tracing.py` | 根因候选不含 marker |
| D | D1 12 类 ALLOWED_RELATIONS | `knowledge.py` | 12 类齐全，缺数据类只做静态校验 |
| D | D2 每类公差 | `tolerance.py` | 3 份（+LOCAC 描述性） |
| D | D3 外部知识 | `external_knowledge/` + `external_knowledge.py` | YAML 全部加载无冲突 |
| D | D4 K2 残差三态 | `physics_constraints.py` 接入签名/图 | 三态比例落盘 |
| D | D5 K3 模板 | `external_knowledge/fault_templates.yaml` | 12 类模板，来源标注 |
| D | D6 解释（模板 rule + 类级/全局汇总） | `llm_explanation.py` | 9 模板 + 3 类级 + 1 全局，faithful 校验 + 降级标记 |
| D | D7 模板兜底 | `report_template.py` | 9 份 |
| D | D8 Alg3 | `cf_validation.py` | 复用，不改口径 |
| D | D9 偏离度 | `deviation.py` 接入图/榜单 | 9 份 deviation + 图内着色 |
| D | D10 端到端 | `run_v23_all.py` | 全链一次重算，manifest 可核对 |

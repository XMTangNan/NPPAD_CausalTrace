# CausalTrace 代码与产物结构约定（v2.3）

> 定稿：2026-11-10。本文件是 `Code/` 布局与 `Code/output/` 产物命名/位置的**唯一约定来源**。
> 新增模块、脚本、产物必须先对齐本文件；偏离需在 `Code/error_tracking.md` 登记 DESIGN 条目。

## 1. 分层原则

> **术语防误解**：**V0–V7 是变量选取阶段的质量门控编号**（见方案 §3.5），与版本号
> v2.1 / v2.3 无关；`v3_status` 指变量集中的 V3 门控三态，不是"v3 版本"。

| 层 | 正式实现（唯一） | 职责 |
|----|------------------|------|
| 配置/注册表 | `causalrca/config/registry.py`、`accident_config.py` | 12 类事故元数据唯一来源：marker、注入时刻、窗口、分组、触发/关键响应、数据状态 |
| 路径常量 | `causalrca/config/paths.py` | 仓库根、`output/` 根、通用窗口/种子/LLM 端点 |
| 冻结常量 | `causalrca/config/constants_v21.py`、`class_variables.py` | v2.1 预注册常量（论文口径，禁止改值） |
| v2.3 阈值 | `causalrca/config/constants_v23.py` | v2.3 新增阈值（截断/条件数/路径上限/RRF/LLM 容差） |
| IO | `causalrca/io/{artifact,logging,files}.py` | 产物统一写入口 + logger + JSON 落盘 |
| 数据/审计 | `causalrca/data/{loader,audit}.py` | 加载、划分、B4/B5/B6 审计 |
| 特征 | `causalrca/features/{variable_selector,paper_anchor}.py` | 论文锚定 + 四阶段漏斗（V0–V7 门控） |
| 因果 | `causalrca/causal/{signatures,source_tracing,severity_aggregation,edges}.py` | 签名、溯源、跨组汇总、图边纯函数 |
| 证据 | `causalrca/evidence/{knowledge,external_knowledge,tolerance,deviation,physics_constraints}.py` | 本体/外部知识、公差、偏离度、物理约束 |
| 报告 | `causalrca/reporting/{llm_explanation,report_template,graph_visualization,run_v23_graphs}.py` | 解释、模板、HTML 图、分组图执行器 |
| 入口 | `causalrca/cli/run_v23_*.py`、`run_variable_selector.py`、`run_ab_variable_selection.py` | 只做编排：读上游 JSON、调用算法、落盘、打印摘要 |
| 测试 | `Code/tests/`（v2.3）、`Code/tests/legacy/`（v2.1 老测试） | 新模块必须配单测；结构约束见 `tests/test_architecture.py` |

**代码代际（哪个目录属于哪一代）**

| 目录 | 代际 | 状态 | 说明 |
|------|------|------|------|
| `causalrca/` | v2.3 | **正式实现，可改** | 8 个分层子包：config / io / data / features / causal / evidence / reporting / cli |
| `legacy/v21/` | v2.1 | **冻结只读** | 3 类事故完整实现 + 执行器 + 复现命令，见 `legacy/v21/FROZEN.md`；禁止被 v2.3 引用 |
| `tests/legacy/` | v2.1 | 冻结（随 legacy 保留可跑） | 9 个老测试 |
| `rca/` | — | **已移除** | 只剩 `rca/README.md`（旧路径 → 新路径对照），不含任何 `.py` |
| `accidents/`、`core/`、`config.py`、`run_plan1_pipeline.py` | v1/gen1 | **已删除** | v2.3 闭包与测试都够不到；v2.1 仍需要的少数工具已随 `legacy/v21/` 归位 |

**依赖方向（单向，禁止反向）**

```
   causalrca/config  →  causalrca/io  →  data  →  features  →  causal  →  evidence  →  reporting  →  cli
                                                                                        │
                                                                                        ▼
                                                                       output/rca_v23/（只经 artifact 写）
   legacy/v21  ──(允许依赖 causalrca 的公共设施)──►  causalrca/config|io
   causalrca   ──✗ 禁止 import──►  legacy / rca / core / accidents / config(顶层)
```

铁律：`causalrca.*` 不得 import `legacy`/`rca`/`core`/`accidents`/顶层 `config`；
v2.3 闭包不得直接 `mkdir`/`open(w)`/`json.dump`；`legacy/v21/` 不得反向 import `rca`。

**单一来源铁律**

1. 事故元数据只来自 `causalrca/config/accident_config.py`（+ `registry.py`）；`ACCIDENT_TYPES` 从注册表派生，禁止第二份 12 类列表。旧路径 `rca/v21/accident_config.py` 已于批 C 删除。
2. 同一产物只有一条生成链：`signatures → tracing → graphs/reports → summary/diff`；下游只读上游 JSON，不重算、不复制。
3. 图、榜单、差异报告、RCA 报告必须来自**同一次**签名/溯源结果（manifest 记录生成批次 `run_id`）。
4. 缺数据的类别只在注册表中标记 `status=data_missing`，执行器跳过并落 `skipped_classes.json`；**禁止生成占位/伪结果**。
5. v2.3 链的三份跨代输入（`split_manifest` / `shared_baseline` / `marker_scale`）由 A 阶段写入 `registry/`，
   下游只读 v2.3 根；`output/rca_v21/` 仅作只读回退（`artifact.legacy_input()`），任何执行器不得回写。

## 2. 目标目录结构

```
Code/
├── STRUCTURE.md                  # 本文件（布局与产物命名的唯一约定）
├── error_tracking.md             # 错误跟踪（唯一问题台账）
├── pyproject.toml                # 仅 pytest 配置与包元数据
├── causalrca/                    # [v2.3 正式实现] 8 层，单向依赖
│   ├── config/
│   │   ├── paths.py              # 路径与通用常量（旧 config.py）
│   │   ├── constants_v21.py      # v2.1 冻结常量（论文口径，禁止改值）
│   │   ├── constants_v23.py      # v2.3 新增阈值（截断/条件数/RRF/LLM 容差）
│   │   ├── class_variables.py    # 各类可能变量集合（旧 rca/rca_config.CLASS_VARIABLES）
│   │   ├── registry.py           # 数据/产物路径注册
│   │   └── accident_config.py    # 12 类事故注册表门面
│   ├── io/
│   │   ├── artifact.py           # v2.3 唯一写入口（manifest/哈希/目录约束）
│   │   ├── logging.py            # logger / ERROR_LOG / log_error
│   │   └── files.py              # save_json / load_json / save_text
│   ├── data/
│   │   ├── loader.py             # 加载/窗口/划分（按注册表状态门控）
│   │   └── audit.py              # B4 数据审计 + B5 Z 病态 + B6 变点
│   ├── features/
│   │   ├── paper_anchor.py       # A1 论文锚定单一来源
│   │   └── variable_selector.py   # 四阶段漏斗 + V0–V7 门控 + v3_status/v5_note
│   ├── causal/
│   │   ├── signatures.py         # C1 12类×3组因果签名
│   │   ├── source_tracing.py     # C2 T=(I-B)^-1 根因排序（含 n_consensus）
│   │   ├── severity_aggregation.py # C3b/C3c 汇总图 + 差异报告
│   │   └── edges.py              # 图边/链文本纯函数（edge_key、render_chain_text…）
│   ├── evidence/
│   │   ├── knowledge.py          # 96 变量本体 + 12 类 ALLOWED_RELATIONS
│   │   ├── external_knowledge.py # YAML → 本体合并（只扩展不覆盖）
│   │   ├── tolerance.py          # D2 每类 sev-1 独立基准 + 稳健σ + 分位数
│   │   ├── deviation.py          # D9 偏离度 d=min(|x-μ|/σ_robust,10)
│   │   └── physics_constraints.py# D4 K2 五类近似关系残差三态
│   ├── reporting/
│   │   ├── graph_visualization.py# C3/C3a 分组 HTML + 信息门控
│   │   ├── run_v23_graphs.py     # 分组/汇总 HTML 执行器
│   │   ├── llm_explanation.py    # D6 模板 rule + 类级/全局汇总（LLM 可选）
│   │   └── report_template.py    # D7 模板兜底
│   └── cli/
│       ├── run_v23_all.py        # 9 步全链编排（单一 run_id）
│       ├── run_v23_end2end.py    # 9 模板双循环（签名+溯源）
│       ├── run_variable_selector.py
│       ├── run_v23_tolerances.py
│       ├── run_v23_llm_explanation.py
│       ├── run_v23_report.py
│       ├── run_v23_validation.py
│       ├── run_v23_validation_set.py
│       └── run_ab_variable_selection.py
├── legacy/
│   └── v21/                      # [v2.1 冻结只读，见 FROZEN.md]
│       ├── FROZEN.md
│       ├── pipeline.py / inversion.py / chains.py / baselines.py / calibration.py
│       ├── ablation_core.py / ablation_symbolic.py / gam_graph.py / cf_validation.py
│       ├── marker_effects.py / lingam_baseline.py / nonlinear_baseline.py
│       ├── data_utils.py / bootstrap.py / consistency.py / method_ensemble.py
│       ├── ablation.py / severity_groups.py / ontology_constraint.py
│       └── run_r0.py / run_r1.py / run_r3.py / run_baselines.py / run_calibration.py
│           / run_cf.py / run_chains.py / run_d0.py / run_gam_graph.py
│           / run_ablation_core.py / run_ablation_symbolic.py
│           / run_llm_explanation.py / run_source_tracing.py
├── rca/
│   └── README.md                 # 已废弃：旧路径 → 新路径对照（无任何 .py）
├── external_knowledge/           # §5.2 L1-L4 YAML（与代码同仓、版本化）
│   ├── variable_dictionary.yaml
│   ├── physics_relations.yaml
│   ├── fault_templates.yaml
│   ├── forbidden_edges.yaml
│   └── tolerance_overrides.yaml
├── tests/                        # v2.3 测试（含 test_architecture.py 守卫）
│   └── legacy/                   # v2.1 冻结测试（9 个，随 legacy 保留可跑）
└── output/
    └── rca_v23/                  # v2.3 唯一产物根（旧 rca_v21/v23_* 冻结为历史）
        ├── manifest.json          # run_id、各产物相对路径/大小/sha256
        ├── registry/
        │   ├── accident_registry.json
        │   ├── split_manifest.json
        │   ├── shared_baseline.json
        │   └── marker_scale.json
        ├── variable_sets/
        │   ├── selection_report_{K}.json   # V0–V7、v3_status（含 pass_with_exemption）、v3_exempt、v5_note
        │   └── variable_selection_summary.json
        ├── audit/data_audit_{K}.json
        ├── audit/z_pathology_audit_{K}.json
        ├── audit/changepoint_report_{K}.json
        ├── tolerances/tolerance_{K}.json
        ├── signatures/signature_{K}_{g}.json
        ├── tracing/root_candidates_{K}_{g}.json
        ├── tracing/topology_{K}_{g}.json
        ├── tracing/clip_sensitivity_{K}_{g}.json
        ├── tracing/skipped_classes.json
        ├── graphs/graph_{K}_{g}.html
        ├── graphs/graph_{K}_summary.html
        ├── graphs/graph_all_summary.html
        ├── reports/summary_{K}.json
        ├── reports/diff_{K}.json
        ├── reports/deviation_{K}_{g}.json
        ├── reports/rca_report_{K}_{g}.json / .txt
        ├── reports/llm_explanation_{K}_{g}.json
        ├── reports/llm_explanation_class_{K}.json
        ├── reports/llm_explanation_global.json
        ├── reports/run_v23_all_summary.json
        ├── knowledge/knowledge_validation.json
        └── ablations/
            ├── frozen_ab_variable_selection_<run_id>.json
            └── ab_variable_selection.json
```

**命名规则**

- 类别 `K` ∈ `{LOCA,LOCAC,SLBIC,SLBOC,SGATR,SGBTR,RW,RI,FLB,MD,LR,LLB}`；组 `g` ∈ `{low,mid,high}`。
- 文件名一律 `{产物类型}_{K}[_{g}].{json|html|txt}`，全大写类别、小写组名。
- JSON 顶层必须含 `schema`、`artifact`、`accident_type`、`group`（或 `null`）、`run_id`、`created_at`、`source`；由 `causalrca/io/artifact.py` 统一注入（旧路径 `rca/v21/artifact.py` 已于批 C 删除），执行器不得手写 json.dump 落盘 v2.3 产物（由 `tests/test_architecture.py` 卡住）。
- 不再出现 `v23_` 前缀子目录、`signatures_strict`、`signatures_no_ontology` 等并列目录；消融产物统一放 `output/rca_v23/ablations/{name}/`。
- 文本/HTML 产物由 `artifact.write_text_artifact` 同时写同名 `.meta.json`（含 sha256 与 run_id）。

## 3. 代码路径对照（旧 → 新，批 A/B/C）

| 旧路径 | 新路径 | 说明 |
|---|---|---|
| `rca/v21/data.py` | `causalrca/data/loader.py` | 逐字搬迁 |
| `rca/v21/data_audit.py` | `causalrca/data/audit.py` | 逐字搬迁 |
| `rca/v21/variable_selector.py` | `causalrca/features/variable_selector.py` | 逐字搬迁 |
| `rca/v21/paper_anchor.py` | `causalrca/features/paper_anchor.py` | 逐字搬迁 |
| `rca/v21/signatures.py` | `causalrca/causal/signatures.py` | 逐字搬迁 |
| `rca/v21/source_tracing.py` | `causalrca/causal/source_tracing.py` | 改为依赖 `causalrca/causal/edges.py` |
| `rca/v21/severity_aggregation.py` | `causalrca/causal/severity_aggregation.py` | 逐字搬迁 |
| `rca/v21/tolerance.py` | `causalrca/evidence/tolerance.py` | 逐字搬迁 |
| `rca/v21/deviation.py` | `causalrca/evidence/deviation.py` | 逐字搬迁 |
| `rca/v21/physics_constraints.py` | `causalrca/evidence/physics_constraints.py` | 逐字搬迁 |
| `rca/v21/knowledge.py` | `causalrca/evidence/knowledge.py` | 逐字搬迁 |
| `rca/v21/external_knowledge.py` | `causalrca/evidence/external_knowledge.py` | 逐字搬迁 |
| `rca/v21/llm_explanation.py` | `causalrca/reporting/llm_explanation.py` | 链文本助手改依赖 `causal/causal/edges.py` |
| `rca/v21/report_template.py` | `causalrca/reporting/report_template.py` | 逐字搬迁 |
| `rca/v21/graph_visualization.py` | `causalrca/reporting/graph_visualization.py` | 逐字搬迁 |
| `rca/v21/run_v23_graphs.py` | `causalrca/reporting/run_v23_graphs.py` | 逐字搬迁 |
| `rca/v21/run_v23_all.py` 等 9 个入口 | `causalrca/cli/` | 逐字搬迁 |
| `rca/v21/accident_config.py` | `causalrca/config/accident_config.py` | 逐字搬迁 |
| `rca/rca_config.CLASS_VARIABLES` | `causalrca/config/class_variables.py` | 原样复制（v2.1 归档前落地） |
| `core/utils.py` 的 save_json/load_json/save_text | `causalrca/io/files.py` | 唯一实现 |
| `rca/v21/{pipeline,inversion,chains,baselines,calibration,ablation_*,gam_graph,cf_validation,marker_effects}`、13 个 `run_r*`/`run_*` 执行器 | `legacy/v21/` | 冻结只读，见 `FROZEN.md` |
| `core/{data_utils,lingam_baseline,nonlinear_baseline,method_ensemble,severity_groups}` 等 v2.1 仍需要的工具 | `legacy/v21/` | 随冻结链一起归位 |
| `accidents/loca/*`、`core/` 其余、`rca/rca_*.py`、`rca/signature*.py`、`run_plan1_pipeline.py`、`config.py` | 已删除 | `git rm`，逐文件见批 C commit 与 `error_tracking.md` |
| `rca/` 全部过渡 shim | 已删除 | 只剩 `rca/README.md` 对照表 |

**shim 策略**：批 A 曾在 `rca/v21/` 保留一组显式 re-export shim；批 C 已全部删除
（所有内部引用与测试均已切到新路径）。若外部脚本仍写旧路径，请按上表或
`rca/README.md` 改 `python -m causalrca.cli.…` / `from causalrca.… import …`。

---

## 4. 与旧产物的对应（收敛映射）

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

## 5. 12 类注册表字段（accident_config.py）

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

## 6. 执行入口（可复现）

```
cd Code
# 全链（验收/发布动作，9 步；需显式声明重跑原因，见下方“运行约定”）
PYTHONPATH=. python -m causalrca.cli.run_v23_all --n-bootstrap 100 --rerun-reason "E-R1 出对外数字：论文表格定稿"
PYTHONPATH=. python -m causalrca.cli.run_v23_all --classes LOCA SGATR FLB --n-bootstrap 100 --rerun-reason "E-R2 ..."
PYTHONPATH=. python -m causalrca.cli.run_v23_all --dry-run          # 只打印 9 步计划（不校验 rerun_reason）
PYTHONPATH=. python -m causalrca.cli.run_v23_all --skip-llm         # 解释后端固定 rule（默认即 rule）
PYTHONPATH=. python -m causalrca.cli.run_v23_all --with-ab ...      # 追加一次性 A/B 消融（默认关闭，10 步）

# 分步重跑（开发默认路径；仍必须传同一 --run-id，否则汇总/报告拒绝混源）
PYTHONPATH=. python -m causalrca.cli.run_variable_selector           # A 阶段：变量选取 + 注册表快照
PYTHONPATH=. python -m causalrca.cli.run_ab_variable_selection       # A/B 一次性消融（不随主链；产物需冻结归档）
PYTHONPATH=. python -m causalrca.data.audit                      # B 阶段：B4/B5/B6
PYTHONPATH=. python -m causalrca.cli.run_v23_tolerances --run-id <id>
PYTHONPATH=. python -m causalrca.cli.run_v23_end2end --n-bootstrap 100
PYTHONPATH=. python -m causalrca.reporting.run_v23_graphs --run-id <id>
PYTHONPATH=. python -m causalrca.causal.severity_aggregation --run-id <id>
PYTHONPATH=. python -m causalrca.cli.run_v23_llm_explanation --run-id <id>   # 模板 rule + 类级/全局（--backend llm 可选）
PYTHONPATH=. python -m causalrca.cli.run_v23_report --run-id <id>
PYTHONPATH=. pytest tests/ -q                                  # 单测 + 回归（144 例，全绿；含 tests/legacy 9 例）
```

> 注：原先 4 例失败在 `tests/test_v23_report_llm.py`，根因是 `output/rca_v23/tracing/` 与
> `signatures/` 来自不同批次（`v23c-*` vs `v23-*`）导致跨阶段拒绝混源；`v23-20261110T134957`
> 统一重算后已 **138 passed / 0 failed**（产物缺失时这 4 例转为 skipped，属设计行为）。
> 登记见 `error_tracking.md` V23-DESIGN-030。`cd Code && pytest tests -q` 亦可
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

## 7. 工作分解（与方案 §6.1 对齐）

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

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
                                                                       output/runs/<run_id>/（只经 artifact 写）
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
   下游只读 v2.3 根；`output/archive/rca_v21_frozen/` 仅作只读回退（`artifact.legacy_input()`），任何执行器不得回写。

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
│   │   ├── injection_truth.py    # 12 类始发（注入）变量真值表（仅 D11 打分，禁用于选变量/建图/排序）
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
│   │   ├── report_template.py    # D7 模板兜底
│   │   └── evaluation.py         # D11 事故内变量级评测（三通道 + 纯偏差基线）
│   └── cli/
│       ├── run_v23_all.py        # 10 步全链编排（单一 run_id）
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
├── experiments/                  # 实验产物（受控诊断/因子实验），`.gitignore` 已忽略；不进 output/
└── output/                       # 布局与保留策略见 output/README.md
    ├── runs/
    │   └── <run_id>/             # v2.3 唯一产物根（路径一律由 artifact 按 run_id 派生）
    │       ├── manifest.json     # run_id、各产物相对本 run 根的路径/大小/sha256/lineage
    │       ├── registry/
    │       │   ├── accident_registry.json
    │       │   ├── split_manifest.json
    │       │   ├── shared_baseline.json
    │       │   └── marker_scale.json
    │       ├── variable_sets/
    │       │   ├── selection_report_{K}.json   # V0–V7、v3_status（含 pass_with_exemption）、v3_exempt、v5_note
    │       │   └── variable_selection_summary.json
    │       ├── audit/data_audit_{K}.json
    │       ├── audit/z_pathology_audit_{K}.json
    │       ├── audit/changepoint_report_{K}.json
    │       ├── tolerances/tolerance_{K}.json
    │       ├── signatures/signature_{K}_{g}.json
    │       ├── tracing/root_candidates_{K}_{g}.json
    │       ├── tracing/topology_{K}_{g}.json
    │       ├── tracing/clip_sensitivity_{K}_{g}.json
    │       ├── tracing/skipped_classes.json
    │       ├── graphs/graph_{K}_{g}.html
    │       ├── graphs/graph_{K}_summary.html
    │       ├── graphs/graph_all_summary.html
    │       ├── metrics/metrics_{K}_{g}.json   # 第 10 步：事故内变量级命中（三通道，含纯偏差基线）
    │       ├── metrics/metrics_summary.json   # 每类/全局汇总 + Wilson 95% + 真值表 + 与老师不可并排
    │       ├── reports/summary_{K}.json
    │       ├── reports/diff_{K}.json
    │       ├── reports/deviation_{K}_{g}.json
    │       ├── reports/rca_report_{K}_{g}.json / .txt
    │       ├── reports/llm_explanation_{K}_{g}.json
    │       ├── reports/llm_explanation_class_{K}.json
    │       ├── reports/llm_explanation_global.json
    │       ├── reports/run_v23_all_summary.json
    │       ├── knowledge/knowledge_validation.json
    │       └── ablations/
    │           ├── frozen_ab_variable_selection_<run_id>.json
    │           └── ab_variable_selection.json
    ├── archive/                  # 只读历史（禁止被 v2.3 引用）
    │   ├── rca_v21_frozen/       # v2.1 冻结产物（见 legacy/v21/FROZEN.md）
    │   ├── rca/  plan1_loca/     # 更早方案产物
    │   └── rca_v23_mixed_<ts>/   # 旧单层根的混源快照
    ├── latest                    # 纯文本 run_id：判断权威结果的唯一入口
    └── README.md                 # 布局说明 + 保留策略 + 权威判定方法
```

**命名规则**

- 类别 `K` ∈ `{LOCA,LOCAC,SLBIC,SLBOC,SGATR,SGBTR,RW,RI,FLB,MD,LR,LLB}`；组 `g` ∈ `{low,mid,high}`。
- 文件名一律 `{产物类型}_{K}[_{g}].{json|html|txt}`，全大写类别、小写组名。
- JSON 顶层必须含 `schema`、`artifact`、`accident_type`、`group`（或 `null`）、`run_id`、`created_at`、`source`；由 `causalrca/io/artifact.py` 统一注入（旧路径 `rca/v21/artifact.py` 已于批 C 删除），执行器不得手写 json.dump 落盘 v2.3 产物（由 `tests/test_architecture.py` 卡住）。
- 不再出现 `v23_` 前缀子目录、`signatures_strict`、`signatures_no_ontology` 等并列目录；消融产物统一放 `<run 根>/ablations/{name}/`。
- 文本/HTML 产物由 `artifact.write_text_artifact` 同时写同名 `.meta.json`（含 sha256 与 run_id）。

**签名产物新增诊断字段（V23-CODE-035，只记录、不参与计算）**

`signatures/signature_{K}_{g}.json` 顶层新增 `redundancy_diagnostics`（可选键，旧 run 无此键即视为未记录）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `available` | bool | 诊断失败时为 `false`（附 `reason`），不影响主产物 |
| `note` | str | 固定声明："仅记录，不参与排序、候选集、稳定边判定或 Top-k" |
| `rule` | obj | 可复核规则：`scope`、`range_tokens`、`min_abs_r=0.95`、`min_abs_w=5.0`、`cancel_ratio=0.10`、`min_targets=2` |
| `n_samples` | int | 相关性计算样本量（训练块池化点数） |
| `n_pairs_scanned` | int | 实际计算 Pearson 的变量对数（全候选对） |
| `n_groups` | int | 同物理量多量程组数 |
| `collinear_pairs` | list | 命中对：`flag="collinear_pair"`、`pair`、`quantity`、`pearson_r`、`targets[{target,w_u,w_v,sum}]`、`n_targets` |
| `zero_variance_variables` | list | `[{variable,flag:"zero_variance",note}]`；**保留不剔**，学图列已被置 0 |
| `variable_flags` | obj | `{变量: ["zero_variance" \| "collinear_pair", ...]}` |

约束：该字段不得被任何排序/候选集/稳定边/Top-k 逻辑读取；新增/修改时必须同步本节。

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
| `output/archive/rca_v21_frozen/v23_signatures/` | `<run 根>/signatures/` | 重算后写入；旧目录不再更新 |
| `output/archive/rca_v21_frozen/v23_tracing/root_candidates_*` | `<run 根>/tracing/` | 重算 |
| `output/archive/rca_v21_frozen/v23_tracing/diff_report_*` | `<run 根>/reports/diff_{K}.json` | 由 `severity_aggregation` 在签名重算后统一重跑 |
| `output/archive/rca_v21_frozen/v23_graphs/graph_{K}_{g}.html` | `<run 根>/graphs/` | 由 `run_v23_graphs.py` 从 v23 签名统一生成 |
| `output/archive/rca_v21_frozen/v23_graphs/graph_{K}_summary.html` | `<run 根>/graphs/` | 同上，与分组图同批次 |
| `output/archive/rca_v21_frozen/v23_llm/` | `<run 根>/reports/llm_explanation_*` | 12/36 份按注册表状态生成 |
| `output/archive/rca_v21_frozen/variable_selection/` | `<run 根>/variable_sets/` | 重算 |
| `output/archive/rca_v21_frozen/tolerances/` | `<run 根>/tolerances/` | 重算 |
| `output/archive/rca_v21_frozen/{signatures,tracing,chains,...}` | 保持不动 | v2.1/v2.2 历史产物，只读 |

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

**环境与依赖（A1）**
- 本机实测 Python **3.14.4**；依赖固定版本见 `Code/requirements.txt`（由 `../.venv/bin/python -m pip freeze` 于 2026-11-14 导出，45 项，含 `lingam==1.8.2`/`pygam==0.12.0`/`graphviz==0.21`）。
- 安装：`cd Code && python -m pip install -r requirements.txt`；运行一律带 `PYTHONPATH=.`。

**R0 前置（A2，强制）**
- 变量选取第一步会把 v2.1 冻结输入复制进 `registry/`：`output/archive/rca_v21_frozen/` 下的
  `split_manifest.json` / `shared_baseline.json` / `marker_scale.json`；**缺失时直接抛 `FileNotFoundError`**
  （`run_variable_selector.py:32-38`），不会自动重算或降级。
- `output/archive/` 已被 `.gitignore` 排除（`.gitignore:11-13`，仅 `output/README.md` 例外）；
  克隆仓库后必须先由数据集侧恢复冻结件，再跑全链。

```
cd Code
# 全链（验收/发布动作，10 步；需显式声明重跑原因，见下方“运行约定”）
PYTHONPATH=. python -m causalrca.cli.run_v23_all --n-bootstrap 100 --rerun-reason "E-R1 出对外数字：论文表格定稿"
PYTHONPATH=. python -m causalrca.cli.run_v23_all --classes LOCA SGATR FLB --n-bootstrap 100 --rerun-reason "E-R2 ..."
PYTHONPATH=. python -m causalrca.cli.run_v23_all --rerun-reason "E-R3 ..."   # 不传 --n-bootstrap 即默认 5（不得据此声称边稳定性）
PYTHONPATH=. python -m causalrca.cli.run_v23_all --dry-run          # 只打印 10 步计划（不校验 rerun_reason）
PYTHONPATH=. python -m causalrca.cli.run_v23_all --skip-llm         # 解释后端固定 rule（默认即 rule）
PYTHONPATH=. python -m causalrca.cli.run_v23_all --role-prior ...   # 角色先验成对输出（默认关闭，见下 B1）
PYTHONPATH=. python -m causalrca.cli.run_v23_all --with-ab ...      # 追加一次性 A/B 消融（默认关闭，11 步）

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
PYTHONPATH=. pytest tests/ -q                                  # 单测 + 回归（175 例，全绿；含 tests/legacy 9 例）
```

> 注：**测试数以本行命令的实际输出为唯一来源**（本机 venv 实测：**175 passed / 0 failed**，2026-11-14）。
> 历史文档里的 161 / 138 是不同批次快照，已被本行取代，不再作为口径。
> 依赖 `output/latest` 或可选库（`pygam`/`graphviz`）的用例在缺环境时应显式 skip，不得计为 passed。
> 历史根因见 `error_tracking.md` V23-DESIGN-030。`cd Code && pytest tests -q` 亦可
> （`pyproject.toml` 仅补 `testpaths`/`pythonpath`，`PYTHONPATH=. python -m` 方式不变）。

**产物根契约（批 E，强制）**
- 唯一根 `output/runs/<run_id>/`，路径只能由 `causalrca/io/artifact.py` 派生：
  `bind_run(run_id)` 绑定、`run_path(run_id)` 纯派生、`subdir_path(name, run_id=None)` 取子目录、
  `build_manifest(run_id)` 收口并刷新 `output/latest`。禁止任何模块拼接绝对产物路径
  （`tests/test_architecture.py::test_no_module_hardcodes_legacy_output_root` 守卫）。
- `output/` 顶层只允许 `runs/ archive/ latest README.md`（+历史 `error_log.jsonl`），
  由 `test_output_has_single_run_root_contract` 守卫。
- **实验产物放 `Code/experiments/`（gitignore），不进 `output/`**：受控诊断、因子实验等非主链产物
  一律写 `Code/experiments/<name>_<run_id>/`，守卫不放宽。
- 权威结果判定：`output/latest` 的 run_id 与 `<该 run>/manifest.json.run_id` 一致。


**运行约定（强制，方案 §6.4）**
- **bootstrap 执行策略：正式口径 100 / 执行默认 5（V23-DESIGN-044，取代 DESIGN-041）**：
  `constants_v23.N_BOOTSTRAP_SPEC=100` 仅为方案正式口径声明（默认不执行）；
  `N_BOOTSTRAP_DEFAULT=5` 是所有执行路径的默认值（`run_v23_all`/`run_v23_end2end`/
  `run_ab_variable_selection`/experiments 脚本），代码里不得散落字面量 5/100。
  100 只能由显式 `--n-bootstrap 100`（或等价显式参数）开启。
  `manifest.json` / `metrics` 继续记 `n_bootstrap`；报告头部必须打印实际次数。
  `run_mode` 字段保留（`spec` ≥100 / `reduced` 否则 / 无记录 `not_recorded`）但**不再承担
  "是否刷新 latest" 的职责**：`build_manifest` 一律正常刷新 `output/latest`（否则工程链不可用）；
  `citable` 不再由次数自动决定，一律显式为 `null`（由引用者逐处标注）。
  `stability_claims_allowed` 仅 ≥100 为 true；未达 100 时：不得声称边稳定性、不得引用
  `consistency_rate` 类指标作结论。兼容别名 `N_BOOTSTRAP_PROD`/`N_BOOTSTRAP_TEST`/
  `RUN_MODE_PROD`/`RUN_MODE_TEST` 保留可用（V23-CODE-032）。
- 默认**禁止**每次改代码就跑全链；先用「受影响单测 + 单阶段重跑（复用上游 run_id）+ 冻结产物比对」验证。
- 仅 E-R1 出对外数字 / E-R2 影响数值 / E-R3 产物结构或契约 / E-R4 跨阶段对不上 四类例外允许全链，且必须传 `--rerun-reason` 并写入 `run_v23_all_summary.json`；未声明原因的重跑不得引用。
- 每轮全链留痕：run_id、rerun_reason、耗时、步数、相对上一轮的变量集/9 组 Top-1/稳定边变化。

`run_v23_all` 结束落盘：
- `<run 根>/manifest.json`：`run_id`、各产物**相对本 run 根**的路径/大小/sha256（六类扩展名全覆盖）；
- `<run 根>/reports/run_v23_all_summary.json`：10 步 `status: ok|skipped|failed`、耗时、产物计数、跳过类、`rerun_reason`、LLM 可用性；
- 任一步失败 → summary 记 `failed` 并非 0 退出，绝不静默跳过。

**第 10 步变量级评测契约（D11，`causalrca/reporting/evaluation.py`；V23-DESIGN-036/038/039）**
- 任务：v2.3 不做事故大类匹配，只做「事故内、变量级根因溯源」。评测即变量级——对每条留出轨迹，
  把该模板的**根因候选变量排序**与**该事故类型的始发（注入）变量真值集**比对；
  **不做事故类型分类、不把 9 个模板当检索候选池、不用关键响应/forced 保护集当判据**（v2.1 范式已弃用）。
- 留出集只取 `registry/split_manifest.json` 的 `test_severities`（每类 20 条，按组 7/7/6）；
  只读本 run 的 `tracing/topology_*`、`tracing/root_candidates_*`、`signatures/signature_*`，
  不重算 bootstrap、不重新学图。
- 真值唯一来源 `causalrca/config/injection_truth.py::INJECTION_TRUTH`（逐条对齐老师项目
  `rca_experiment.py:34-47 GROUND_TRUTH`）。**只剔除数据列中不存在的变量**；真值是否落在候选集
  （模板 15 个节点）内单列 `gt_in_candidate_set` 披露。口径边界：NPPAD 数据只给「类目录 + 文件序号
  （=严重度）」，**没有逐条注入变量字段** → 真值粒度为 `class_level`（同类全部轨迹共用该类的始发
  变量集），产物 `ground_truth.scope` 如实标注，不得当成逐样本真值。
- 四通道同表（同一批轨迹）：`causal` 生产口径（注入点被「故障指示器遮蔽」、
  不进候选）/ `causal_unmasked_for_eval` 评测模式（注入点放回候选，其余同生产口径；用已落盘
  `stable_edges` 重算 T=(I−B)⁻¹，纯矩阵、不改生产输出）/ `deviation_equal_weight` 纯偏差基线
  （与去遮蔽通道同 15 节点）/ `deviation_production_set` 纯偏差基线（与生产通道同 14 节点），
  偏差基线两列只为保证"因果 vs 偏差"各自严格同变量集，不得跨配对比较。
- 指标 Hit@1 / Hit@3 / MRR / P@2 / P@3 / Recall@3 / AP@3 / Jaccard@3（cutoff=3，与老师
  `metrics_for_ranking` 同式），按模板 (K,g) / 按类 / 全局三层汇总；Wilson 95% 只给 Hit@1/Hit@3。
- **B_CLIP 敏感性是一等公民（V23-DESIGN-038）**：总效应是 T=(I−B)⁻¹ 的 |T| 行和，`B_CLIP`
  硬截断会改名次，故每个模板与汇总都必须并列 `clip3 / clip5 / clip10 / clip_off` 四列
  的 Hit@1/Hit@3/MRR/AP@3 与注入点名次中位数，并由 `flips` 自动标出"随 clip 翻转"的模板；
  **只报 clip5 一列不得当结论**（`LOCA_high` clip5=第 1、clip_off=第 3；`FLB_high` clip3=第 2、
  clip5=第 5、clip_off=第 3）。
- **口径定稿为方案 B（V23-DESIGN-039）**：生产口径**维持遮蔽**（`trace_roots` 的 marker 跳过分支不动），
  `causal` 列 0 是候选集定义使然、不是排序失败；去遮蔽列与 `clip_sensitivity` 降级为**可辨识性诊断**，
  不进主表、不作准确率主张；**检索指标已停用**（见 V23-DESIGN-055）。受控实验另存
  `Code/experiments/ranking_diagnosis_v23-20261110T150726/`（非主链产物，`.gitignore` 已忽略）：7 种排序量下 Hit@1 上限
  0.1000，Hit@3 0.3167→0.7000 全来自 `RESPONSE_VARS` 0.30 人工先验，孤立节点殿后使 V2c 0.7000→0.3167。
  **偏差基线两列已修复、可引用（V23-CODE-029 → 030）**：`sigma_robust<=0` 或低于该变量量纲可分辨
  下限时不再产出巨大 `d`，而是标 `d_status=not_comparable` 并退出排序（`tolerance_overrides.yaml`
  补 13 个物理下限 `by_variable`；实测 60 条留出 `n_not_comparable=0`）。修复后 `deviation_equal_weight`
  仍为 Hit@1 0 / Hit@3 0.0167 / MRR 0.1866（修复前 0 / 0 / 0.1746）——**"偏差基线不能定位始发变量"
  的结论不变，且不再由 σ 病态造成**。
- **检索指标已停用（V23-DESIGN-055，2026-11-11）**：`evaluate_template_retrieval`（相对 SEM 残差
  给 9 个模板打分）保留为**手动诊断工具**，`evaluate()` 不再自动调用，流水线不再产出
  `metrics/metrics_template_retrieval.json`；`metrics_summary.template_retrieval` 只剩状态声明。
  历史 run（`v23-20261110T150726`）里的旧 json 与旧数字**不回填**，只能作"实现侧诊断"引用：
  类别级 Hit@1 0.9000 / 模板级 0.3667 不得进主表、结论句或论文数字。
- 落 `metrics/metrics_{K}_{g}.json`（逐例四通道明细 + 模板汇总 + `clip_sensitivity` 四列 + 遮蔽披露）与
  `metrics/metrics_summary.json`（三层汇总 + 真值表 + `clip_sensitivity`/`flips` + `comparability_with_teacher`）。
- **与老师数字不并排**：同任务（事故内变量级 Top-k 命中）但不同协议——我们无人工先验降权、
  切分严格互斥、每 (类,组) 单独建图、另有遮蔽/去遮蔽两列；只能作脚注引用。
- **偏差第三态（V23-CODE-033，2026-11-10）**：`d_status=not_comparable` 除 029 的两种情形外，
  新增 `d_status_reason="degenerate_zero_channel"`——σ 由 fallback/登记下限撑起、正常工况近似恒定
  （MAD=0 或 σ 与 |μ| 同量级且均在噪音量级）、正常工况参考带无展布（IQR=0 或 ≤25% 动态区间）、
  且在本轨迹幅值下实质为 0（`max(|μ|,σ) ≤ 1e-3·max|x|`）四条同时满足。`reports/deviation_{K}_{g}.json`
  增加 `n_not_comparable`（全变量计数）与 `not_comparable`（名单），`deviations` 仍只展示 TopN。
  不新增截断、不改 `d` 定义、不改聚合（`max`）。判据在 `external_knowledge.is_deviation_comparable`
  （`DEGENERATE_ZERO_BAND=0.25`、`DEGENERATE_ZERO_LEVEL=1e-3`）。
- **真值可达性审计（V23-DESIGN-043，B2；只留痕不干预选择）**：`audit/truth_reachability_<CLASS>.json`
  与汇总 `audit/truth_reachability.json`。只读 `variable_sets/selection_report_<CLASS>.json`，逐变量登记
  最终去向七类：`selected` / `stage1_rejected` / `dedup_merged` / `vif_dropped` / `anchor_dropped` /
  `score_rejected` / `not_in_candidates`，回答「门控是否把真值变量挡在门外」。首份结果（现有 3 类）：
  `gate_blocks_truth=false`；LOCA 的 `WBK`/`MBK` 属 `score_rejected`（选股评分淘汰，非 V0–V7 门控）。
- **名次分布标准化（V23-DESIGN-045，B3）**：`metrics_summary.evidence_rank_distribution`
  （逐通道 + global + per_class）给 `n/n_ranked/n_miss/rank_hist/mean_first_rank/median_first_rank/
  mean_normalized_rank/gt_coverage_mean`，避免只看 Hit@1。生产遮蔽通道 `gt_coverage_mean=0.0`
  （注入点不进候选所致），属口径而非排序失败。
- **角色先验（V23-DESIGN-046，B1；默认关闭）**：唯一配置点 `causalrca/config/role_prior.py`
  （名单 41 项、权重 `0.30`、作用范围仅排序乘子——不改 B、不改 T），`--role-prior` 显式开启。
  开启时**必须并列**输出 `causal` 与 `causal_role_prior` 两条通道（`pair_complete=true`），
  任一单独出现即视为不合规；名单缺失时 `resolve` 直接抛错，禁止回退猜测。

**不再使用**：`output/archive/rca_v21_frozen/v23_*` 下的 `tracing_report_v23.txt` 等旧汇总（11-04 产物，已冻结只读）。验收结论以本 run 的 `reports/summary_{K}.json`、`reports/diff_{K}.json` 与 `manifest.json` 为准；权威 run 由 `output/latest` 与 manifest 的 `run_id` 一致确定。

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
| D | D11 变量级评测 | `evaluation.py` | 60 条留出 × 9 模板：变量级 Hit@1/Hit@3/MRR/P@2/P@3/Recall@3/AP@3，四通道（生产/去遮蔽/两条偏差基线）同表、`B_CLIP` 四列并列（diagnostic）；口径定稿方案 B，偏差两列已修复（V23-CODE-030）；检索指标已断开自动调用、不再落盘（V23-DESIGN-055，手动诊断）；与老师 0.4368/0.5800 不并排 |

# CausalTrace 代码与产物结构约定（v2.3）

> 定稿：2026-11-10。本文件是 `Code/` 布局与 `Code/output/` 产物命名/位置的**唯一约定来源**。
> 新增模块、脚本、产物必须先对齐本文件；偏离需在 `Code/error_tracking.md` 登记 DESIGN 条目。

## 1. 分层原则

| 层 | 目录 | 职责 | 允许写入 |
|----|------|------|----------|
| 配置/注册表 | `rca/v21/accident_config.py`（新建） | 12 类事故元数据唯一来源：marker、注入时刻、窗口、分组、触发/关键响应、数据状态 | 只此一处定义事故元数据 |
| 冻结常量 | `rca/rca_config_v21.py` | v2.1 预注册常量；v2.3 新增常量集中在 `V23_*` 段 | 不散落魔数 |
| 数据/审计 | `rca/v21/data.py`、`data_audit.py`（新建） | 加载、划分、B4/B5/B6 审计 | 只读 `nppad_repo/`，只写 `output/rca_v23/` |
| 算法 | `rca/v21/{variable_selector,signatures,source_tracing,tolerance,severity_aggregation,physics_constraints,deviation,knowledge}.py` | 纯算法/纯函数优先，入口脚本不做算法 | 不直接写产物路径 |
| 执行器 | `rca/v21/run_*.py` | 读取上游 JSON、调用算法、落盘、打印摘要 | 一个执行器只写自己约定的子目录 |
| 测试 | `Code/tests/` | 新模块必须配单测；回归脚本走 `run_all_v23.py` | 不写 `output/` |

**单一来源铁律**

1. 事故元数据只来自 `accident_config.py`；`rca_config_v21.ACCIDENT_TYPES` 改为从注册表派生，禁止第二份 12 类列表。
2. 同一产物只有一条生成链：`signatures → tracing → graphs/reports → summary/diff`；下游只读上游 JSON，不重算、不复制。
3. 图、榜单、差异报告、RCA 报告必须来自**同一次**签名/溯源结果（manifest 记录生成批次 `run_id`）。
4. 缺数据的类别只在注册表中标记 `status=data_missing`，执行器跳过并落 `skipped_classes.json`；**禁止生成占位/伪结果**。

## 2. 目标目录结构

```
Code/
├── STRUCTURE.md                  # 本文件
├── error_tracking.md             # 错误跟踪（唯一问题台账）
├── rca/
│   ├── rca_config_v21.py         # v2.1 冻结常量 + V23_* 新增常量
│   └── v21/
│       ├── accident_config.py    # [新] 12 类注册表（元数据唯一来源）
│       ├── data.py               # 加载/划分（按注册表状态门控）
│       ├── data_audit.py         # [新] B4 数据审计 + B5 Z 病态 + B6 变点
│       ├── variable_selector.py   # A1 四阶段漏斗（配置改读 accident_config）
│       ├── knowledge.py          # 96 变量本体 + 12 类 ALLOWED_RELATIONS
│       ├── external_knowledge.py # [新] YAML → 本体合并（只扩展不覆盖）
│       ├── signatures.py         # C1 12类×3组因果签名（含 K2/偏离度挂载点）
│       ├── source_tracing.py     # C2 T=(I-B)^-1 根因排序（含 n_consensus）
│       ├── tolerance.py          # D2 每类 sev-1 独立基准 + 稳健σ + 分位数
│       ├── physics_constraints.py# D4 K2 五类近似关系残差三态
│       ├── deviation.py          # D9 偏离度 d=min(|x-μ|/σ_robust,10)
│       ├── severity_aggregation.py # C3b/C3c 汇总图 + 差异报告
│       ├── graph_visualization.py  # C3/C3a 分组 HTML + 信息门控
│       ├── llm_explanation.py      # D6 LLM 解释 + 忠实性校验
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
        │   └── split_manifest.json         # train/test 分组快照（v2.1 冻结输入留痕）
        ├── variable_sets/
        │   ├── selection_report_{K}.json   # 四阶段漏斗报告（V1–V7 + 选中变量）
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
        ├── reports/rca_report_{K}_{g}.json
        ├── reports/rca_report_{K}_{g}.txt
        ├── reports/llm_explanation_{K}_{g}.json
        ├── knowledge/knowledge_validation.json
        └── ablations/{auto_vs_v21.json,...}        # 消融/对照（唯一允许再分一层的子目录）
```

**命名规则**

- 类别 `K` ∈ `{LOCA,LOCAC,SLBIC,SLBOC,SGATR,SGBTR,RW,RI,FLB,MD,LR,LLB}`；组 `g` ∈ `{low,mid,high}`。
- 文件名一律 `{产物类型}_{K}[_{g}].{json|html|txt}`，全大写类别、小写组名。
- JSON 顶层必须含 `schema`、`artifact`、`accident_type`、`group`（或 `null`）、`run_id`、`created_at`、`source`；由 `rca/v21/artifact.py` 统一注入，执行器不得手写 json.dump 落盘 v2.3 产物。
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
PYTHONPATH=. python -m rca.v21.run_v23_all          # A/B/C/D 全链，单次 run_id
PYTHONPATH=. python -m rca.v21.run_v23_all --stages audit,variables,signatures,tracing,graphs,reports
PYTHONPATH=. python -m rca.v21.run_v23_all --classes LOCA,SGATR,FLB
PYTHONPATH=. pytest tests/ -q                       # 单测 + 回归
```

`run_v23_all` 结束必须打印/落盘：
- `manifest.json`：`run_id`、git commit、常量快照（bootstrap 次数、阈值）、每类 status、产物 sha256；
- `run_report.txt`：每类每阶段成功/跳过/失败与原因；
- C 系列验收由 `output/rca_v23/tracing/tracing_report_v23.txt` 给出 3/3 或 12/12 的显式结论。

## 6. 工作分解（与方案 §6.1 对齐）

| 阶段 | 条目 | 落点 | 验收 |
|------|------|------|------|
| A | A1 变量选取 | 已有 `variable_selector.py`，改为读 `accident_config` | V1–V7 3/3 |
| A | A2 ENTITY_TABLE 96 | 已有 | 选中变量 0 未登记 |
| A | A3 `accident_config.py` 12 类 | 新建 | 12 条记录字段完整、status 明确 |
| A | A4 3 类 A/B 对照 | `ab_comparison/` | 已有，重跑到 `rca_v23` |
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
| D | D6 LLM 解释 | `llm_explanation.py` | 9 份，faithful 校验 + 降级标记 |
| D | D7 模板兜底 | `report_template.py` | 9 份 |
| D | D8 Alg3 | `cf_validation.py` | 复用，不改口径 |
| D | D9 偏离度 | `deviation.py` 接入图/榜单 | 9 份 deviation + 图内着色 |
| D | D10 端到端 | `run_v23_all.py` | 全链一次重算，manifest 可核对 |

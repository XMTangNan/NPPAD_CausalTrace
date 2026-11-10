# output/ 产物目录约定（v2.3，2026-11-10 批 E 起）

**一句话**：v2.3 的产物只有一个根 —— `output/runs/<run_id>/`；其余历史目录一律只读归档在
`output/archive/`。判断"哪份结果是权威"的唯一方法：`output/latest` 指向的 run_id
与 `<该 run>/manifest.json` 里的 `run_id` 一致。

## 目录语义

```
output/
├── runs/
│   └── <run_id>/                 ← 唯一产物根。一个 run_id = 一次端到端计算
│       ├── registry/             12 类注册表 + 冻结输入快照（split_manifest / shared_baseline / marker_scale）
│       ├── variable_sets/        A 阶段变量选取（selection_report_{K}.json、summary）
│       ├── audit/                数据审计（缺失/异常/质量门禁）
│       ├── tolerances/           各类容差（tolerance_{K}.json、skipped_classes.json）
│       ├── signatures/           LiNGAM 签名（signature_{K}_{g}.json）
│       ├── tracing/              溯源排序（root_candidates_{K}_{g}.json）
│       ├── graphs/               传导图 HTML
│       ├── metrics/              评测指标：metrics_{K}_{g}.json × 9 + metrics_summary.json（第 10 步 D11）
│       ├── reports/              汇总/差异/报告/LLM 解释
│       ├── knowledge/            外部知识/物理约束证据
│       ├── ablations/            A/B 消融与冻结件
│       └── manifest.json         全量产物清单（path/size/sha256/run_id/lineage）
├── archive/
│   ├── rca_v21_frozen/           v2.1 冻结产物（只读，见 Code/legacy/v21/FROZEN.md）
│   ├── rca/                      更早的 RCA 方案产物（只读）
│   ├── plan1_loca/               方案一 LOCA 产物（只读）
│   └── rca_v23_mixed_<ts>/       旧单层根 output/rca_v23 的混源快照（只读，不再引用）
├── latest                        纯文本，内容为当前权威 run_id（跨平台可靠，不用软链）
└── README.md                     本文件
```

`error_log.jsonl` 是历史运行日志，保留在顶层，不参与产物契约。

**实验产物不进 `output/`**：只读实验/受控对照的产物放 `Code/experiments/<name>_<run_id>/`
（`.gitignore` 已忽略，不入库、不进 manifest），例如 `ranking_diagnosis_v23-*`、
`protocol_align_v23-*`。`output/` 顶层只允许 `runs/`、`archive/`、`latest`、`README.md`
（+ 历史 `error_log.jsonl`），由 `tests/test_architecture.py::test_output_has_single_run_root_contract`
守卫（V23-CODE-031）。

## 保留策略

| 类别 | 保留 | 清理 |
| --- | --- | --- |
| `runs/` 下被 `latest` 指向的 run | 永久保留（验收证据） | 不得删除 |
| `runs/` 下非 latest 的旧 run | 保留最近若干次（含 134957 等验收批次） | 确认未被文档/报告引用后可整目录删除 |
| `archive/` | 永久只读保留（可复现性证据） | 不删；如确需腾空间，先归档 sha256 清单 |
| `latest` | 每次 `build_manifest` 成功后自动刷新 | 不要手工编辑（除排障） |

## 如何判断"哪份是权威结果"

1. 读 `output/latest` 得到 run_id；
2. 打开 `output/runs/<run_id>/manifest.json`，确认 `run_id` 与之一致；
3. 用 `manifest.json.files[].path`（相对本 run 根）定位具体产物；
4. `manifest.json.lineage.runs` 里若出现其他 run_id，说明该 run 用了上游批次输入（例如未重冻结的
   旧 A/B 冻结件），属**已知混源**，需在报告中说明。

## 迁移记录（批 E，2026-11-10）

- `output/rca_v23/`（148 文件）→ `output/runs/v23-20261110T134957/`：**逐文件 sha256 完全一致**
  （迁移只改位置不改内容）。该 run 的 `manifest.json.root` 仍记录迁移前的旧绝对路径，
  属历史元数据，未改写。
- `output/rca_v21/` → `output/archive/rca_v21_frozen/`（309 文件）；
  `output/rca/` → `output/archive/rca/`（6 文件）；
  `output/plan1_loca/` → `output/archive/plan1_loca/`（22 文件）。
- 空的 `output/results/`、`output/figures/` 已删除。
- `Code/causalrca/config/paths.py` 里历史目录常量（`FIG_DIR`/`RESULT_DIR`/`RCA_OUTPUT_DIR`/
  `PLAN1_OUTPUT_DIR`）已下沉到 `output/archive/`，不再在顶端重建。
- 代码侧唯一根：`causalrca/io/artifact.py` 的 `run_root()/subdir_path()/run_path()`；
  `tests/test_architecture.py` 禁止任何模块再出现旧根字面量。

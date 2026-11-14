# NPPAD-CausalTrace v2.3 评审问题修正清单（待评审稿）

> **文档状态**：待评审修正清单（**非现行版、非定稿**），不构成口径。　**起草**：2026-11-13
> **唯一索引**：`方案/README.md`；**口径效力**：无。方法定义仍以现行版 `NPPAD-CausalTrace-v2.3方案-12类全适配与变量选取机制.md` §0.2/§2.7/§3 为准。
> **状态**：本文件只陈述“问题 + 处置建议 + 验收判据”，**不写验收状态**；落地时在 `Code/error_tracking.md` 登记正式编号。
> **来源**：2026-11-13 全项目只读评审（结构/测试/复现链/台账/文档一致性逐项核过）。条目均给出可复核的文件:行号。
> **读者**：本人 + 导师。**本文件不产生代码改动**。

---

## 0. 三十秒摘要

| # | 问题 | 优先级 | 处置类型 | 阻断评审？ |
|---|---|---|---|---|
| A1 | Code/ 下无任何依赖声明（requirements/lock/environment 均无） | P1 | 补物 | **是**（换机不可复现） |
| A2 | v2.3 A 阶段硬依赖 v2.1 冻结输入 `output/archive/rca_v21_frozen/`，被 .gitignore 排除；主复现命令段未写“先跑 R0” | P1 | 改文（+可选改码） | **是** |
| A3 | `output/` 全部不入库 → 仓库内没有任何可核验数字 | P1 | 补物 | 是 |
| A4 | 测试数文档漂移：STRUCTURE 写“161 例全绿”与“138 passed”，台账最新 175 | P2 | 改文 | 否 |
| A5 | **legacy 冻结代码引用已删除的 `core` 包**（模块级 import 即报错） | P1 | 改码（冻结例外）或改文 | **是** |
| B1–B3 | 方案与实现的归一化/基线口径不一致 3 处（V23-DESIGN-086 自记，未修） | P1 | 改文 **或** 改码（二选一） | **是** |
| C1 | 台账标题状态与正文状态矛盾（CODE-029/CODE-034） | P2 | 改文 | 否 |
| D1 | 主表无任何任务效果数字；`mechanism_consistency` 为空占位 | P2 | 策略裁决 | 是（答辩必答） |
| E1–E2 | SGATR 大系数边、偏差通道历史问题（已登记，未定论） | P3 | 只登记 | 否 |
| F1–F4 | 代码卫生：import 期建目录 / 全局随机种子 / 口径还原无 try-finally / 宽泛 except | P3 | 改码（择机） | 否 |

---

## 1. A 类：可复现性

### A1 无依赖声明（P1）

- **现象**：`Code/` 下无 `requirements*.txt`、无 lock、无 `environment.yml`、无 CI（`.github` 不存在）。仓库里唯一的 `requirements.txt` 在 `nppad_repo/`，是**数据集仓库自带**的（`pandas~=1.4.2`、`pyodbc`、`matplotlib~=3.5.2`、`torch~=1.12+cu113`），**不是本项目依赖**，且与 Python 3.12 不兼容。
- **实测所需**（源码中出现或被动态导入）：`numpy / pandas / scipy / scikit-learn / statsmodels / PyYAML / lingam / pygam / xgboost / requests` + 测试用 `pytest`；其中 `lingam` 另需 `graphviz`、`semopy`、`sympy`，`statsmodels` 需 `patsy`。
- **影响**：干净机器缺 6–9 个包，`pytest` 直接不可用；全链第 4 步（end2end）先后因 `semopy`、`sympy`、`graphviz` 中断（本机实测）。
- **处置**：新增 `Code/requirements.txt`（或 `environment.yml`）并锁版本，写“跑代码机器的实际版本”，不照抄 `nppad_repo/requirements.txt`。
- **验收**：全新 venv 内 `cd Code && pytest tests -q` 与 `run_v23_all --dry-run` 均通过；依赖清单与实际导入零差异。

### A2 “先跑 R0”未写进主复现命令（P1）

- **证据**：`Code/causalrca/cli/run_variable_selector.py:34–40` —— `snapshot_frozen_inputs()` 读 `legacy_input()`，缺失即抛 `FileNotFoundError（“…先跑 R0/A 阶段前置”）`；而 `Code/STRUCTURE.md:285` 起的“执行入口（可复现）”一节只列 v2.3 命令，**无 R0 前置**；该要求只写在 `legacy/v21/FROZEN.md` §3 与运行时报错里。
- **证据补充**：`.gitignore:11–13` 使 `output/archive/rca_v21_frozen/` 不入库，干净 clone 必然缺三份冻结输入（`split_manifest / shared_baseline / marker_scale`）。
- **处置**：在 `STRUCTURE.md` §6 与 `Code/output/README.md` 各加一行前置：“首次复现先跑 `PYTHONPATH=. python -m legacy.v21.run_r0`”；或让 A 阶段缺失时给出可执行提示（改码，低成本）。
- **验收**：干净 clone → R0 → A 阶段 → 全链，一条命令链无隐藏前置。

### A3 产物不入库，数字不可回放（P1）

- **证据**：`.gitignore:11–13`（`**/output/*` 仅放行 `README.md`）；`issues/` 有取证文档，但引用的 run 产物不在库内。
- **处置**：至少提交一次验收 run 的 `manifest.json` + `metrics_summary.json` + 9 份报告纯文本（不含大 HTML/图）；或提供 sha256 清单 + 下载地址。
- **验收**：任取一条对外数字，能沿 `manifest` 找到对应文件与其 hash。

### A4 测试数漂移（P2）

- **证据**：`Code/STRUCTURE.md:308` 写“161 例，全绿”；同文件 `:313` 附近写“138 passed / 0 failed”；`Code/error_tracking.md` 最新记录 175 passed。**实测**（补装依赖后）`pytest tests -q` 收齐 **175** 例：**149 passed / 14 failed / 12 skipped**；14 个失败全为环境或产物缺失（缺 `pygam` 6 例、缺 `graphviz` 2 例、缺 `output/latest` 产物 6 例），**无一条断言失败**。
- **处置**：文档数字改为“以 CI/最近一次全绿 run 为准”，或发布时自动生成；不要方案与结构文档各写一份。
- **验收**：全仓测试例数只剩一处来源。

### A5 【新增】legacy 冻结代码引用已删除的 `core` 包（P1）

- **证据**：`Code/STRUCTURE.md` 明确 `core/` 已删除；但 `legacy/v21/consistency.py:21`、`legacy/v21/ablation.py:23` 在**模块级**写 `from core.total_effect import compute_total_effects`，import 即 `ModuleNotFoundError`；另有 `legacy/v21/lingam_baseline.py:98`、`legacy/v21/method_ensemble.py:141` 为函数内 import（潜在）。链路可达：`legacy/v21/bootstrap.py:14` → `lingam_baseline`。
- **影响**：`FROZEN.md` 声称“9 个老测试随 legacy 保留可跑”，但触到上述模块即失败；`tests/test_architecture.py` 只禁 `legacy→rca`，**未禁 `legacy→core`**，守卫测不出。
- **处置（二选一）**：① 把 `compute_total_effects` 复制进 `legacy/v21/` 并改 import（只改 import 行，不动算法，FROZEN.md 记一行“仅修 import 断链”）；② 在 `FROZEN.md` 列出“不可运行模块清单”，并让守卫测试断言该清单。**建议 ①**，否则“冻结可复现”不成立。
- **验收**：`python -c "import legacy.v21.consistency"` 与 9 个老测试全绿；守卫测试新增“legacy 不得 import 已删除根”的断言。

---

## 2. B 类：方案↔实现口径不一致（P1，方法论级）

> 依据：台账 `V23-DESIGN-086（open）` 自记三条“待修”，证据见 `issues/归一化口径由来-查证-2026-11-11.md`。本清单只做**处置编排**，不重复取证。

| # | 方案写 | 实现是 | 代码位置 | 处置 |
|---|---|---|---|---|
| B1 | 逐类 sev-1 稳健 σ（含 σ_floor） | 统一的训练段**池化 mean/std**（普通 std，无 floor） | `signatures.py:104–119` | 改文（现行默认已是 unified） |
| B2 | 注入前基线 | 注入后恢复段 `[520,2030]` | `loader.py:255–256` | 改文，并说明为何用恢复段 |
| B3 | 逐类基准 | 跨类池化（3 类 × sev-1 × 152 点） | 同上 | 改文 |

- **处置原则**：**改文优先**（实现已验收，改码牵动全部历史数字）；改文须同时更新现行版 §0.2 冲突裁决表与 §2.7。若坚持改码，须新开 DESIGN 条目并重跑 100 档，属下一轮。
- **验收**：方案正文与代码注释不再互斥；`DESIGN-086` 可关闭且给出“改文/改码”结论。

---

## 3. C 类：台账状态矛盾（P2）

| # | 现象 | 证据 | 处置 |
|---|---|---|---|
| C1 | `V23-CODE-029` 标题写 `（open）`，正文已被 `V23-CODE-030（closed）` 修复 | 标题 `error_tracking.md:1208`；正文“状态：🔴 open（未修）”后紧跟 CODE-030“✅ 已解决” | 标题状态由正文单一来源生成，或在标题注明“已被 CODE-030 关闭” |
| C1b | `V23-CODE-034` 标题写 `（open）`，正文写 `✅ closed` | 标题 `:2018`；正文状态 `:2056` 附近 | 同上 |

- **影响**：按标题筛状态会误判未修项，评审/交接最容易被抓。
- **验收**：`grep '（open）'` 结果与正文状态一一对应，零冲突。

---

## 4. D 类：指标主张（P2，需裁决不只改文）

- **证据**：`evaluation.py:99–103` —— `PRIMARY_BLOCKS = (stability, structural_health, mechanism_consistency)`；`mechanism_consistency_block()`（`:835`）当前返回 `status=not_available / violation_rate=None / coverage=None`，即**空占位**；命中类指标全部降为 `diagnostic`。
- **含义**：这是诚实的自我约束（不主张准确率），但意味着**当前没有任何可主张的任务效果数字**，答辩/评审必被追问。
- **两个出口（择一或并行）**：① 把 held-out 注入真值的 diagnostic 结果正式开一个“诊断附录”章（明确“真值=模拟注入变量，非物理根因”）；② 小样本专家标注（v3 方案 C 线：30 条链 + rubric + κ）把 `mechanism_consistency` 从占位变成数值。
- **验收**：任一出口给出“可复核的数字 + 使用边界”，且不违反“不得用真值参与选择/建图/排序”。

---

## 5. E 类：已知未决（P3，仅登记，本轮不处置）

| # | 内容 | 台账 |
|---|---|---|
| E1 | `SGATR_low/mid` 最大 `|B|` 16.75/14.06、`|w|>5` 各 6 条；已取证为共线分裂 + 小 σ 放大，“数值伪影 vs 物理强耦合”未定论 | `V23-DESIGN-088（open）`、`V23-CODE-035（closed）` |
| E2 | 偏差通道 `d` 在 σ=0 处曾爆量，已由物理下限 + 不可比状态修复；“纯偏差不能定位始发变量”的结论仍成立 | `V23-CODE-029/030` |

---

## 6. F 类：代码卫生（P3，择机）

| # | 现象 | 证据 | 建议 |
|---|---|---|---|
| F1 | import 期建目录：任何 `import causalrca.config.paths` 都会 `mkdir` 多个 `output/archive/*`；只读评审机 import 即报错 | `paths.py:23,28,30,34,36,38,42,44` | 惰性化，或收敛到 `artifact` 一处 |
| F2 | 全局随机种子污染：`np.random.seed(random_state)` 改写进程级 RNG | `variable_selector.py:379` | 改 `np.random.default_rng(seed)` 并显式下传 |
| F3 | 口径还原不在 `try/finally`：意外异常会残留进程级归一化口径 | `run_v23_all.py:402` 设置、`:463` 还原 | 包 `try/finally` |
| F4 | 宽泛异常：**实测 0 处 bare `except:`**；10 处 `except Exception`（7 处带 `noqa: BLE001` 说明须降级），集中于 `llm_explanation.py`(4)、`signatures.py`(2)、`artifact.py`(3)、`explanation_verdict.py`(1) | `grep -rn 'except Exception'` | **无须改**；仅建议在 `signatures/artifact` 降级路径补一条 INFO 日志 |

> 说明：本条对“4 处 bare except”的原始表述作了更正——代码里没有 bare except，只有宽泛捕获。

---

## 7. 处置顺序（建议）

| 轮次 | 内容 | 理由 |
|---|---|---|
| 第 1 轮（纯文档，零代码） | A2、A4、B1–B3、C1 + A1 的依赖清单 | 一次清掉“文字与实现打架”，不影响任何数字 |
| 第 2 轮（补物） | A1 提交 requirements/lock、A3 提交验收 manifest + metrics_summary | 换机能跑、数字能回放 |
| 第 3 轮（小改码） | A5 修 legacy 断链、F1/F2/F3 | 只改 import 与生命周期，不动算法口径 |
| 第 4 轮（需裁决） | D 类指标出口 | 涉及对外主张，需导师拍板 |
| 暂不处置 | E 类 | 已有台账，等外部评审证据 |

**红线**：① 不得用 `injection_truth` 参与选择/建图/排序/保护；② 不得回写历史 run 的 `manifest` 与 sha256；③ 不把“改文”和“改码”混在同一轮；④ 冻结代码若因 A5 改动，须在 `FROZEN.md` 记“仅修 import 断链，不改算法”。

---

## 8. 待裁决

1. B 类走“改文”还是“改码”（建议改文：实现已验收，改码需全量重跑）。
2. A5 走“复制函数进 legacy”还是“声明不可运行清单”。
3. D 类指标出口选哪一个，或两者并行。
4. 是否接受“诊断附录”里出现命中率数字（与“不主张准确率”的措辞如何并存）。

---

## 附录：本轮验证方式（可复核）

| 检查 | 命令/方法 | 结果 |
|---|---|---|
| 依赖声明 | `ls Code/{requirements*.txt,*.lock,environment*.yml}; ls .github` | 均不存在 |
| 复现前置 | 读 `run_variable_selector.py:34–40`、`STRUCTURE.md:285–314`、`.gitignore:11–13` | 确认缺 R0 前置文档与冻结输入 |
| 测试实况 | 补装依赖后 `cd Code && pytest tests -q` | **149 passed / 14 failed / 12 skipped**（共 175），失败全为环境/产物缺失 |
| 归一化实现 | 读 `signatures.py:104–119`、`loader.py:255–256` | 与 `DESIGN-086` 三条一致 |
| 主表口径 | 读 `evaluation.py:99–103`、`:835` | `mechanism_consistency` 为空占位 |
| legacy 断链 | `grep -rn 'from core' legacy` | 4 个文件引用已删除的 `core` |
| 异常捕获 | `grep -rn 'except:'` / `'except Exception'` | bare 0 处；宽泛 10 处 |
| 已知未决 | 读 `error_tracking.md:1208,2005,2018` 与 DESIGN-088 | 与台账一致 |

> 证据等级：本清单“实测”均在评审机（无 GPU、只读工作区）取得；涉及 LiNGAM/全链的数字不在本清单内。
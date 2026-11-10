# NPPAD-CausalTrace v2.3 技术方案 — 12类事故全适配与变量选取机制（定稿版 v2.3-final）

> **文档状态**：唯一现行版（定稿，非评审稿）　**版号**：v2.3-final　**定稿日期**：2026-11-10（初稿 2026-11-03）
> **唯一索引**：`方案/README.md`（现行版/并行研究线/归档的判定与版本谱系，以此为准）
> **状态与台账**：验收状态、问题编号、关闭理由一律指向 `Code/error_tracking.md`；目录、命名、入口命令一律指向 `Code/STRUCTURE.md`。本正文**不复制**状态清单。
> **版本定位**：在 v1（LOCA 复现基线）+ v2.1（3类因果签名 RCA）+ v2.2（变量溯源与因果图可视化）基础上，扩展至 NPPAD 全部 12 类严重度事故，核心解决**变量选取量化标准**与**多源知识融合**两大技术问题
> **输入**：① v1/v2.1/v2.2 已实现代码与产物；② 阶段1-5 论文学习笔记（主流水线、三算法、神经符号三层）；③ NPPAD 数据集 17 类事故清单；④ v2.1 实验解读 O1–O12；⑤ AERCA 与 v3（仅作并行研究线引用，不并入正文）
> **读者**：本人 + 导师

---

## 0. 定稿声明与口径总则

### 0.1 唯一现行版

本文档是 NPPAD-CausalTrace 方案体系的**唯一现行版**。其余版本文件（v1 复现方案、v2 跨事故签名、v2.1 方案与实验解读、v2.2 溯源可视化、v2.3 评审、方案关联说明、v3、AERCA、评审 A–D）**全部原地保留为归档**，文首带状态标记，不再更新正文；方法定义、量化标准、阈值与口径一律以本文档为准。判定与谱系见 `方案/README.md`。

### 0.2 冲突裁决表（按事实取值，正文只保留一个口径）

下表逐条消解历史版本文档之间、以及文档与实现之间的冲突。取值以**代码常量与实际产物**为准，凡与代码不一致的旧表述一律作废，不再并存两种口径。

| # | 冲突项 | 历史口径（出处） | 现行取值（依据） | 落点 |
|---|--------|------------------|------------------|------|
| 1 | **VIF 阈值** | 方案 §3.3 原定 `VIF<10`；v2.1 硬编码 20 变量集自身 max VIF 达 9.45e10 | **`VIF_MAX=500`**：仅剔除近完全共线（R²>0.998）病态变量；`N_MIN=15`、`N_MAX=25`（`rca/v21/variable_selector.py:56-58`）。不调阈值掩盖 fail | §3.3 阶段4、§3.5 V3、§7.1 |
| 2 | **严重度分组** | 一处写"由变点检测决定分组边界（如 low 2–86 / sat 87–100）" | **分组仍按预注册固定三段** `low 2–33 / mid 34–66 / high 67–100`（`rca_config_v21.SEVERITY_BANDS`、`accident_config.severity_bands`）。变点检测（CUSUM + DP-L2-BIC）**只作审计与信息门控**，不改分组边界（实测变点：LOCA 7/12/21/43、SGATR 87、FLB 0）。数据驱动分组仍是待裁决项（O4） | §4.2、§4.4.2、§4.6、§9.3 |
| 3 | **变量选取统计单位** | 一处写"按时间点统计"，一处写"按轨迹统计" | **关联统计的单位 = 轨迹**：MI 与跨轨迹区分度在 `groupby(trajectory_id).mean()` 的轨迹均值上与严重度做回归/计算；CV 与非高斯度在合并时间序列上计算（`variable_selector.py:261-288`） | §3.3 阶段2、§3.4 |
| 4 | **基线窗口** | 一处写"LOCA_1 准稳态段 t=520~2030s 作为全局基准"；一处写"注入前 [0,100]s" | **两套并存、各司其职，禁止互相替代**：① 过程本体/公差 μ/σ 基线 = **注入前 [0,100]s 共 11 点**（`tolerance.py`，V23-DATA-004，`baseline_points=11`）；② 数据审计的 Z 病态/变点基线 = **恢复段 [520,2030]s 共 152 点**（`data_audit.py`，取自注册表 `baseline_window`） | §4.4.1、§5.9、§7.4 |
| 5 | **σ_floor 口径** | 一处写"统一取 1%·\|μ\|"；外部材料写"53/93 变量由 σ_floor 兜底"；实现里曾有两份 `sigma_floor_for`（`data_audit` 按本地量纲表、`external_knowledge` 按 YAML） | **单一来源 = `rca/v21/external_knowledge.py::sigma_floor_for` + `external_knowledge/tolerance_overrides.yaml`**（`by_variable` → `by_unit` → `fallback·\|μ\|`）。`σ_robust = max(1.4826·MAD, σ_floor(v))`；`data_audit.sigma_floor_for` 自 V23-DESIGN-029 起只是**保留 dict 契约的兼容包装**，不再持有数值配置（本地 `PHYSICAL_SIGMA_FLOOR` 已删除）。合一后实测**零数值变化**：96 变量 physical 37 / fallback 59，兜底通道数与来源计数与合并前一致。实测由 σ_floor 兜底的通道数 = **87/93（LOCA）、88/93（SGATR）、88/93（FLB）**，其中 fallback 53 + physical 34；"53/93"只是 fallback 来源数 | §4.4.1、§5.9、§7.4 K10 |
| 6 | **稳定边阈值** | 论文 Algorithm 1 原值 `f_e≥0.5 且 s_e/dir≥0.6` | **`f_e≥0.70 且 dir_e≥0.90`**（`STABLE_F=0.70`、`STABLE_DIR=0.90`）。0.5/0.6 只作"论文原值"引用，不用于任何产物。v2.3 为 **LiNGAM 单引擎稳定边**（`consensus_available=false`、`n_consensus=null`），GAM 共识默认未接入，**禁止把稳定边等同共识边** | §2.3、§2.7、§7.3 |
| 7 | **SGATR sev87 归因** | 一处写"严重度分组边界设置不当造成的伪影" | **两条事实分开记**：(a) 变点检测确认 severity=87 处 marker→LSGA 掉档（WTRA 掉档）；(b) sev87+ 的 LSGA 双峰饱和使 GAM 训练中位处局部斜率反转（high 组 C^A=−2693.11，low/mid 为 +23.36/+19.37），R5 方向一致率 0/71 属**已定位的真实数据现象、机制未证实**；不得为改数值调整 `g*` 选择规则或 C^A 口径 | §4.4.2、§7.3、§9.3 |
| 8 | **数据规模** | 一处写"每类 ~100 条轨迹 × 96 变量 × ~4000 时间步"；一处写"23 变量" | **LOCA/SGATR/FLB 各 100 条（sev 1–100）；LOCAC 5 条（partial）；Normal 1 条；其余 8 类无目录**。每类划分 = sev-1 基线 1 条 + 测试 20 条（7/7/6）+ 模板 79 条。每条轨迹 **345–476 个采样点**（10 s/步，≈3440–4750 s 覆盖）；**96 通道**（含 TIME），每类数据审计数值列 **93**、入模 **15**（gate_ratio 0.156）。"~4000 时间步""23 变量"作废 | §1.4、§4.0、§5.1 |
| 9 | **过期引用** | `anomaly.py::build_thresholds_from_loca1_steady()` 被当作"现状"；`anomaly.py` 被列为"待新建"；产物路径仍写 `v23_signatures/` 等 | ① 该函数实际在 **`Code/accidents/loca/anomaly.py`**（v1 LOCA 专用遗留），**不是 v2.3 现状**；v2.3 现状为 **`rca/v21/tolerance.py`**（评审 C 文档"代码中不存在"一句按此更正：函数存在，但不在 `rca/v21`）。② "23 变量"是 v2.0 早期审计起点（`CANDIDATE_UNION` = 18 物理 + 3 marker + 2 安注），现行本体 96。③ v2.3 唯一产物根 = `output/rca_v23/`，`v23_signatures/`、`v23_tracing/`、`v23_graphs/`、`signatures_strict/` 均为**冻结只读历史路径** | §2.4、§5.1、§5.3.1、§6.2、§9.3 |
| 10 | **重复测点去重口径** | 历史实现把 `forced = trigger + key_responses` 直接钉进 `protected`，同一物理量的 A/B 双测点（PSGA/PSGB、WFWA/WFWB、LSGA/LSGB）两路同时入选，VIF≈∞ → V3 恒 fail | **阶段3 去重**（用户裁决方案 (a)）：同一物理量双测点 `\|Pearson r\| ≥ 0.99` 判为精确重复，保留配置顺序里先出现的一路，另一路写 `dropped_key_response`（`pair` / `corr` / `retained` / `stage=stage3`）；被剔除者封死四条回流路径（阶段2 TOP_K、子系统代表、锚定变量、N_MIN 回补），最高分候选恰为重复测点时代表权交给同物理量已入选的另一路。**V3 阈值 500 与 V5 判据均未改动** | §3.3 阶段3、§4.4.1、§9.3 |

### 0.3 状态类信息的唯一来源（不复制进正文）

- **验收状态 / 问题编号 / 关闭理由 / 待裁决项** → `Code/error_tracking.md`（唯一问题台账）。
- **代码目录、产物命名、执行入口命令** → `Code/STRUCTURE.md`（唯一布局约定）。
- 本正文附录 C、附录 D 的两份执行清单为**历史快照**（2026-11-03 / 2026-11-05），保留不删，内容**不作准**，冲突时以台账为准。

### 0.4 并行研究线（引用不并入）

| 文档 | 性质 | 与 v2.3 的边界 |
|------|------|----------------|
| `NPPAD-CausalTrace-后续研究综合方案-v3-2026-11-02.md` | 并行研究线（可辨识性、证据强度、动态因果、risk-coverage） | v2.3 是工程扩展层，不实现 v3 方法；v2.3 产物作为 v3 P0/P1 的输入（附录 B） |
| `AERCA-可利用性专项方案-2026-11-02.md` | 并行研究线（可利用性/解释可用性） | 仅在 §9.1 关系表中引用；不并入 v2.3 正文，不作验收判据 |

---

## 目录

- [0. 定稿声明与口径总则](#0-定稿声明与口径总则)
- [1. 概述与定位](#1-概述与定位)
- [2. 前置校验：论文核心逻辑一致性核对](#2-前置校验论文核心逻辑一致性核对)
- [3. 变量选取机制（核心技术问题）](#3-变量选取机制核心技术问题)
- [4. 12类事故统一适配方案](#4-12类事故统一适配方案)
- [5. 多源知识融合体系](#5-多源知识融合体系)
- [6. 实现路径与代码模块](#6-实现路径与代码模块)
- [7. 验证指标体系](#7-验证指标体系)
- [8. 风险点与预案](#8-风险点与预案)
- [9. 版本关系、吸收清单与待补条目](#9-版本关系吸收清单与待补条目)

---

## 1. 概述与定位

### 1.1 一句话目标

> 在 v2.1/v2.2 已验证的因果发现流水线基础上，将覆盖范围从 3 类事故扩展至 NPPAD 全部 **12 类严重度事故**，通过**量化变量选取机制**替代硬编码变量集，通过**六组件多源知识融合**替代单一模板解释，实现端到端、可复用、可验证的核电事故因果传导分析系统。

### 1.2 与 v1/v2/v2.1/v2.2/v3 的关系

| 版本 | 核心内容 | 状态 | 本版本角色 |
|------|---------|------|-----------|
| v1 复现方案 | 单事故（LOCA）全链复现：数据/窗口/双引擎/共识边/RRF/物理约束/S1–S6 评估 | 已完成，归档 | **复现口径来源**（§6.5 吸收） |
| v2 跨事故方案 | 3→12 类扩展的总体设想与模块规划 | 完成方法设想，归档 | 历史骨架（已被 v2.3 取代） |
| v2.1 | 3类事故（LOCA/SGATR/FLB）因果签名RCA + LiNGAM/GAM-ANM双引擎 | 已完成，归档 | **方法定义来源**（§2.7 吸收）+ 算法层基线 |
| v2.1 实验解读 | 阶段一实验结论与第二阶段方案，O1–O12 待办 | 已完成，归档 | **待办承接来源**（§7.6 吸收） |
| v2.2 | 变量溯源（拓扑根因排名）+ 0依赖HTML+SVG因果图可视化 | 已完成，归档 | **溯源与可视化口径来源**（§4.7 吸收） |
| **v2.3（本方案，定稿）** | **12类全适配 + 变量选取机制 + 六组件知识融合** | **唯一现行版** | **工程扩展层** |
| v3 | 可辨识性研究、证据强度矩阵、动态因果 pilot、risk-coverage | 并行研究线 | 不并入；产物可作其输入 |
| AERCA | 可利用性/解释可用性专项 | 并行研究线 | 不并入；仅 §9.1 引用 |

> 📌 **版本谱系与归档判定**：以 `方案/README.md` 为唯一索引；各版本间的模块复用详表见 [方案关联-版本复用与依赖关系说明.md](./方案关联-版本复用与依赖关系说明.md)（保留为详表，索引效力低于 README）。

### 1.3 项目需求拆解与对应方案

| 项目需求 | 方案落点 | 章节 |
|---------|---------|------|
| 扩展至全部12类数据集 | 12类事故统一适配框架 + 分组针对性规则 | §4 |
| 弱化marker，聚焦变量选取 | 变量选取四阶段漏斗机制（量化标准+算法+校验） | §3 |
| 全类别适配（统一+特殊） | 算法层复用 / 物理层按事故机理分组重建 | §4.3 |
| 多源知识融合 | 六组件融合体系（物理知识/结构知识/本体/公差/LLM/模板） | §5 |
| 方案可落地 | 分步实现路径 + 代码模块映射 + 验证指标 | §6-7 |
| 严重度分组处理决策 | 分级完整学习 + 信息门控 + 跨严重度汇总 | §4.6 |
| 共享基线Z病态处理 | 稳健σ + z截断 + 饱和变量特判（数据审计必做） | §4.4.1, §5.9 |
| 数据偏离度可视化 | 辅助信息层（不替代根因溯源），sev-1基准+4级着色+分位展示 | §5.10 |
| 前置校验 | 论文主流水线/三算法/神经符号表述核对 | §2 |

### 1.4 端到端数据流图

下图展示 v2.3 从原始数据到最终 RCA 报告的完整数据流，涵盖 12 类事故、3 严重度分组、四阶段变量选取、知识融合与严重度汇总全链路：

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                   原始数据层                                          │
│  NPPAD 事故 CSV（LOCA/SGATR/FLB 各100条 × 96通道 × 345–476采样点，10s/步）  │
│  + LOCAC 5条（partial）+ Normal 1条；其余 8 类暂无数据                        │
└──────────────────────────────────┬──────────────────────────────────────────────────┘
                                   │ 按事故类型 k ∈ {12类} 分流
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                              Phase 0：数据审计（每类事故独立）                        │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐                   │
│  │ 列名校验/常量列  │  │ Z病态检测        │  │ 变点检测          │                   │
│  │ 轨迹数统计       │  │ (σ/|μ|<1% 标记)  │  │ (PELT/CUSUM)     │                   │
│  │ 时间窗口配置     │  │ → 病态变量清单    │  │ → 分组边界        │                   │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘                   │
│           │                    │                      │                               │
│           └────────────────────┼──────────────────────┘                               │
│                                ▼                                                       │
│                   审计报告：{k}_audit.json                                              │
└──────────────────────────────────┬──────────────────────────────────────────────────┘
                                   │ 96变量 + 严重度标签
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                         Phase 1：变量选取（四阶段漏斗，§3）                           │
│                                                                                       │
│  96参数 ──► [阶段1 硬过滤] ──► [阶段2 数据驱动评分] ──► [阶段3 物理校验] ──► [阶段4 适配] │
│            (σ<1e-6剔除)    (CV+非高斯+轨迹级MI+区分度)  (子系统覆盖+冗余)    (VIF<500,15-25) │
│                                                                                       │
│  外部知识支撑：本体子系统映射（C3）+ 触发变量/关键响应强制保留                           │
│                                                                                       │
│  输出：V_graph[k]（15-25变量）+ variable_sets_{k}.json                                 │
└──────────────────────────────────┬──────────────────────────────────────────────────┘
                                   │ V_graph[k]
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                    Phase 2：因果发现（算法层复用，按严重度分组 g∈{low,mid,high}）     │
│                                                                                       │
│  ┌──────────┐   ┌──────────────┐   ┌────────────────┐   ┌──────────────────┐        │
│  │ 稳健标准化│──►│ LiNGAM+GAM-ANM│──►│ Bootstrap四指标 │──►│ consensus稳定边   │        │
│  │ (σ_floor) │   │ 双引擎因果发现 │   │ (f_e/s_e/方向/符号)│   │ + RRF融合        │        │
│  └──────────┘   └──────────────┘   └────────────────┘   └────────┬─────────┘        │
│                                                                   │                    │
│  ┌──────────────────────────────────────────────────────────────▼──────────────┐     │
│  │                    符号网络组件（K1约束 + C2结构知识）                         │     │
│  │  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  ┌────────────────┐   │     │
│  │  │ K1禁止边    │  │ K1允许边     │  │ K1竞争边      │  │ C2 Alg2总效应   │   │     │
│  │  │ 删除留痕    │  │ 贴标签       │  │ 双向反馈标记  │  │ T=(I-B)⁻¹      │   │     │
│  │  └─────────────┘  └──────────────┘  └───────────────┘  └────────────────┘   │     │
│  │  配置源：knowledge.py(ALLOWED_RELATIONS/forbidden/competitive)               │     │
│  └─────────────────────────────────────────────────────────────────────────────┘     │
│                                   │                                                    │
│                                   ▼                                                    │
│  ┌─────────────────────────────────────────────────────────────────────────────┐     │
│  │  K2 数值约束（残差三态判定）§5.3.2                                            │     │
│  │  对 consensus 边验证5类近似物理关系（装量-泄漏/SG水位/安注触发/传热管/反应性）  │     │
│  │  三态：违反 / 未违反 / 不可判定 → 边标 physcons_violation                    │     │
│  │  配置源：physics_constraints.py + physics_relations.yaml                     │     │
│  └─────────────────────────────────────────────────────────────────────────────┘     │
│                                                                                       │
│  输出：signature_{k}_{g}.json（稳定边 + 总效应矩阵 T + K1标签 + K2残差判定）          │
└──────────────────────────────────┬──────────────────────────────────────────────────┘
                                   │ 36份 signature（12类×3严重度）
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                    Phase 3：根因溯源 + 严重度分组可视化（§4.6）                       │
│                                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐         │
│  │ Step1：分组溯源（每组独立）                                              │         │
│  │  ┌──────────────────────────────────────────────────────────────┐      │         │
│  │  │ 符号网络组件注入                                              │      │         │
│  │  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐    │      │         │
│  │  │  │ K2异常判定    │ │ K1本体标签    │ │ K3机制模板匹配    │    │      │         │
│  │  │  │ 稳健σ异常加权 │ │ 路径边贴标签  │ │ 故障模板匹配分    │    │      │         │
│  │  │  │ (tolerance)   │ │ (物理一致/竞争)│ │ (fault_templates) │    │      │         │
│  │  │  └──────────────┘ └──────────────┘ └──────────────────┘    │      │         │
│  │  └──────────────────────────────────────────────────────────────┘      │         │
│  │                                                                         │         │
│  │  总因果效应排序(T=(I-B)⁻¹，对效应变量总影响 DESC)                           │         │
│  │  次级键: in_degree ASC + weighted_reach DESC + consensus_rate DESC        │         │
│  │  → root_candidates_{k}_{g}.json                                         │         │
│  │  传导子图(边带K1/K2/K3标签) → graph_{k}_{g}.html                         │         │
│  └───────────────────────────┬─────────────────────────────────────────────┘         │
│                              │                                                          │
│  ┌───────────────────────────▼─────────────────────────────────────────────┐         │
│  │ Step1.5：Algorithm 3 反事实验证（§5.8）                                  │         │
│  │  对 Top-K 根因的关键出边做探索性一致性比较                                │         │
│  │  双引擎预测(LiNGAM cL + GAM-ANM) vs 观测差值 → gCF(PASS/FAIL)            │         │
│  └───────────────────────────┬─────────────────────────────────────────────┘         │
│                              │                                                          │
│  ┌───────────────────────────▼─────────────────────────────────────────────┐         │
│  │ Step2：信息门控                                                          │         │
│  │   if consensus边数 < 5 → 标注"信息不足"，灰色水印                         │         │
│  └───────────────────────────┬─────────────────────────────────────────────┘         │
│                              │                                                          │
│  ┌───────────────────────────▼─────────────────────────────────────────────┐         │
│  │ Step3：跨严重度汇总                                                      │         │
│  │   边并集（标出现组[low/mid/high] + K1/K2标签）+ 根因频率投票              │         │
│  │   → graph_{k}_summary.html                                              │         │
│  │   差异分析（Top1切换/边数差异/机理切换点）→ diff_report_{k}.json           │         │
│  └─────────────────────────────────────────────────────────────────────────┘         │
└──────────────────────────────────┬──────────────────────────────────────────────────┘
                                   │ 根因排名 + 传导路径 + 异常状态
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                    Phase 4：多源知识融合解释（§5 六组件）                             │
│                                                                                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                                   │
│  │ K1 约束通道  │  │ K2 数值通道  │  │ K3 机制通道  │                                   │
│  │ 本体禁止边   │  │ 稳健σ异常判定│  │ LLM+模板解释  │                                   │
│  │ 子系统覆盖   │  │ K2残差三态   │  │ 忠实性校验    │                                   │
│  └─────────────┘  └─────────────┘  └─────────────┘                                   │
│         │                │                │                                            │
│         └────────────────┼────────────────┘                                            │
│                          ▼                                                              │
│              RCA 报告_{k}.json（根因+路径+异常+解释）                                   │
└──────────────────────────────────┬──────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                   最终产物层                                          │
│                                                                                       │
│  每类事故 k 的产物：                                                                   │
│  • variable_sets_{k}.json        变量集（§3）                                          │
│  • {k}_audit.json                数据审计（Z病态+变点）                                 │
│  • tolerance_{k}.json            基准阈值（稳健σ，§5.9）                               │
│  • signature_{k}_{g}.json ×3     因果签名（12类×3组）                                  │
│  • root_candidates_{k}_{g}.json×3 根因排名                                            │
│  • graph_{k}_{g}.html ×3          分组因果图                                           │
│  • graph_{k}_summary.html         跨严重度汇总图                                       │
│  • diff_report_{k}.json           差异分析                                             │
│  • RCA_report_{k}.json/html       融合解释报告                                         │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.5 符号网络完整结构说明

下图展示贯穿 Phase 2-4 的符号网络（神经符号层）完整组件、连接关系与配置项：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    外部约束配置层（知识源）                                │
│                                                                         │
│  knowledge.py                    external_knowledge/                     │
│  ├─ ENTITY_TABLE (96变量)         ├─ variable_dictionary.yaml (L1)       │
│  ├─ ALLOWED_RELATIONS (12类)      ├─ physics_relations.yaml (L2→K2)     │
│  ├─ _forbidden_rules (R1/R2/R3)   ├─ fault_templates.yaml (L3→K3)       │
│  └─ COMPETITIVE_PAIRS             └─ forbidden_edges.yaml (L1→K1)       │
│                                                                         │
│  output/tolerances/                                                      │
│  └─ tolerance_{k}.json (每类sev-1稳健σ基准 → K2异常判定)                 │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │ 加载（Phase 0 完成后注入全流程）
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    符号网络三通道（贯穿 Phase 2-4）                       │
│                                                                         │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐          │
│  │  K1 约束通道    │  │  K2 数值通道    │  │  K3 机制通道    │          │
│  │                 │  │                 │  │                 │          │
│  │ • 禁止边删除    │  │ • 稳健σ异常判定  │  │ • 故障模板匹配  │          │
│  │ • 允许边标签    │  │ • K2残差三态     │  │ • 机理切换检测  │          │
│  │ • 竞争边标记    │  │ • 异常加权       │  │ • 模板覆盖率    │          │
│  │ • 子系统覆盖    │  │ • 物理关系验证   │  │ • 匹配分报告    │          │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘          │
│           │                    │                    │                    │
│  配置源    │ knowledge.py       │ anomaly.py         │ fault_templates   │
│           │ forbidden_edges    │ tolerance_{k}.json │ .yaml             │
│           │ physics_relations  │ physics_constraints│                    │
└───────────┼────────────────────┼────────────────────┼────────────────────┘
           │                    │                    │
           ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    各阶段注入点与连接关系                                 │
│                                                                         │
│  Phase 2 因果发现：                                                      │
│    K1 ──► consensus边生成后：禁止边删除/允许边标签/竞争边标记              │
│    C2 ──► Bootstrap后：总效应矩阵 T=(I-B)⁻¹ 计算（结构知识）              │
│    K2 ──► consensus边后：5类物理关系残差三态判定 → physcons_violation    │
│                                                                         │
│  Phase 3 根因溯源：                                                      │
│    K2 ──► 根因排名：异常状态加权（tolerance_{k}.json 稳健σ）              │
│    K1 ──► 传导路径：边贴本体标签（物理一致/竞争/冲突）                    │
│    K3 ──► 根因路径：与故障模板匹配 → 匹配分                              │
│    C2 ──► 关键边：Algorithm 3 反事实验证（gCF PASS/FAIL）                │
│                                                                         │
│  Phase 4 融合解释：                                                      │
│    K1+K2+K3 ──► RCA报告：根因(带证据向量) + 路径(带物理标签)              │
│                      + 异常(带阈值依据) + LLM解释(忠实性校验)             │
└─────────────────────────────────────────────────────────────────────────┘
```

**组件完整性确认**：

| 通道 | 组件 | Phase 2 | Phase 3 | Phase 4 | 配置源 |
|------|------|---------|---------|---------|--------|
| K1 | 禁止边删除 | ✅ | — | ✅ | knowledge.py, forbidden_edges.yaml |
| K1 | 允许边标签 | ✅ | ✅(路径边) | ✅ | knowledge.py |
| K1 | 竞争边标记 | ✅ | ✅(路径边) | ✅ | knowledge.py(COMPETITIVE_PAIRS) |
| K1 | 子系统覆盖 | — | — | ✅ | ENTITY_TABLE |
| K2 | 稳健σ异常判定 | — | ✅(排名加权) | ✅ | tolerance_{k}.json |
| K2 | K2残差三态 | ✅(边验证) | ✅(路径边) | ✅ | physics_constraints.py |
| K2 | 物理关系验证 | ✅ | — | ✅ | physics_relations.yaml |
| K3 | 故障模板匹配 | — | ✅(路径匹配) | ✅ | fault_templates.yaml |
| C2 | Alg2总效应T | ✅ | — | ✅ | signature JSON |
| C2 | Alg3反事实验证 | — | ✅(关键边) | ✅ | cf_validation.py |

**排查结论**：原数据流图中 Phase 2 缺失 K1竞争边、C2总效应、K2残差；Phase 3 完全缺失符号网络组件。现已全部补全，无遗漏组件。所有配置项均有明确的数据源文件。

---

## 2. 前置校验：论文核心逻辑一致性核对

### 2.1 核对结论

经对阶段1-5学习笔记与论文原文的逐条核对，v2.3方案的所有技术点与论文核心逻辑**保持一致**，个别指标名称差异（如 s_e 为方案层指标，代码实际用 f_e/dir_e/π）已在 §2.3 标注。核对结果如下：

### 2.2 主流水线（论文图2a）

论文原始流水线：`Data Loader → Feature Selector → Causal Discovery Engine → RCA → Causal Evaluator`

v2.3对应实现：
- **Data Loader**：轨迹级数据加载（独立单位=轨迹，非时间点）— 忠实于论文"batch upload of historical sensor data"
- **Feature Selector**：本方案核心新增 §3 变量选取机制 — 对应论文"XGBoost feature selection + domain expert validation"
- **Causal Discovery Engine**：LiNGAM + GAM-ANM 双引擎（忠实于论文 LiNGAM/DiffAN 并列设计）
- **RCA**：总因果效应排序 T=(I-B)⁻¹（论文 Algorithm 2，v2.3 升级）+ 专家公差异常判定
- **Causal Evaluator**：Algorithm 3 反事实验证（降级为探索性一致性比较）

### 2.3 三个核心算法（阶段3笔记核对）

| 算法 | 论文定义 | v2.3实现口径 | 一致性 |
|------|---------|-------------|--------|
| **Algorithm 1** Bootstrap稳定性 | N次重采样，记录每条边强度，s=1/(1+σ)，保留s≥0.6 | 扩展为四指标：f_e（出现频率）+ s_e（边权稳定性）+ 方向一致率 + 符号一致率；**保留条件与代码一致：f_e≥0.70 且 dir_e≥0.90**（`rca_config_v21.py` 冻结 STABLE_F=0.70, STABLE_DIR=0.90）。注：s_e 为方案层指标，代码实际以 f_e/dir_e/π 三指标判定 | ✅ 阈值与代码对齐 |
| **Algorithm 2** 总因果效应 | T=(I-B)⁻¹，Neumann级数 I+B+B²+... | 每次Bootstrap独立求逆 T_k=(I-B_k)⁻¹，汇总中位数/IQR/符号率；禁止先平均B再求逆 | ✅ 逐次求逆是论文 T=(I-B)⁻¹ 在重采样下的正确适配 |
| **Algorithm 3** 反事实验证 | 预测干预效应 vs 留出集观测变化 | 降级为探索性模型-观测一致性比较；观测条件均值差≠干预效应，不作成败标准 | ✅ 降级有声明，不夸大效力 |

### 2.4 神经符号三层（阶段2笔记核对）

| 论文层 | 论文实现 | v2.3适配（代码位置） | 一致性 |
|--------|---------|---------------------|--------|
| 知识图谱 | RDF编码，rdflib查询 | `knowledge.py`：`ENTITY_TABLE`（变量→子系统/含义/单位）+ `ALLOWED_RELATIONS`（按事故类型允许边，L1/L2级别+物理依据）+ `COMPETITIVE_PAIRS`（竞争边）+ `_forbidden_rules`（禁止边R1/R2/R3） | ✅ 轻量替代，不删环节 |
| 过程本体 | Neo4j图数据库，Cypher查询 | `knowledge.py::build_ontology()`：从ENTITY_TABLE+ALLOWED_RELATIONS构造代码本体；`tolerance.py::build_tolerance()`：**每类事故 sev-1 注入前 [0,100]s（11点）**的 μ±kσ 数据驱动阈值（v1 遗留的 `accidents/loca/anomaly.py::build_thresholds_from_loca1_steady()` 仅作历史实现，不再被 v2.3 调用） | ✅ 数据驱动阈值替代专家公差 |
| 因果图提示注入 | 序列化总效应矩阵传入LLM prompt | `_build_llm_prompt_context()`：序列化上游排序/异常状态/稳定边f_e/本体知识 → DeepSeek V4.1-Flash | ✅ 忠实于论文 causal graph prompt injection 机制 |

**可替换性初步评估**（详见§5.1）：现有JSON实现是论文RDF/Neo4j在NPPAD数据规模（现行本体 96 通道、每类审计 93 个数值列，入模 15）下的合理轻量适配，**不建议直接替换技术栈**，升级方向为补全v3三通道（K1约束/K2数值约束/K3机制模板）+ 外部知识接入。

### 2.5 Algorithm 3 反事实验证核对

现有实现 `cf_validation.py::counterfactual_validation()` 已完整落地，与论文Algorithm 3的对应关系（详见§5.8）：
- 论文：对每条边A→B，用总因果效应预测"如果A变了B会变多少"，与留出集观测变化对比
- 实现：5折确定性划分，基线组（x≤中位数）vs 干预组（x>中位数），双引擎预测（LiNGAM线性cL + GAM-ANM），计算方向一致率与量级Spearman
- 降级声明：观测条件均值差≠干预效应，严重度梯度不自动构成准实验，仅作探索性一致性比较

### 2.6 核对中发现的需注意点

1. **f_e 不是置信度**：DirectLiNGAM 对固定数据是确定性算法，每个严重度仅一条轨迹，f_e 的变异来自训练集严重度构成变化。正确读法是"边对严重度构成的敏感度"，非因果置信度。
2. **允许边只标记不添加**：本体只删除禁止边（留痕）、对数据已学到的允许边贴标签，绝不造边。
3. **严重度是遗漏混杂**：文件名即标签但未进建模矩阵；不能作节点（轨迹内常数列）、不能残差化（过度控制上游断点）。

### 2.7 方法定义统一（吸收 v2.1 方案，口径唯一化）

v2.1 方案正文对符号和算法有多处口语化表述，本节把它们收敛为**唯一定义**，后续章节与论文引用一律用本节口径。

#### 2.7.1 符号与对象

| 符号 | 定义 | 现行落点 |
|------|------|----------|
| `s` | 事故严重度，取整 1–100（文件名即标签） | 文件名 |
| `g(s)` | 严重度分组：`low 2–33 / mid 34–66 / high 67–100` | `SEVERITY_BANDS` |
| `D_k` | 第 k 类事故的全部轨迹（本地 100 条） | `data.py::load_class` |
| `T_k` | 学图/模板集：除 sev-1 基线与 20 条测试外的 79 条 | `build_split_manifest` |
| `E_g` | 分组 g 的**稳定边集** | `signatures.py` |
| `f_e / dir_e` | 边 e 在 `LINGAM_N_BOOTSTRAP=100` 次 Bootstrap 中的出现频率 / 方向一致率 | 同上 |
| `s_e` | 边 e 的系数强度中位数 | 同上 |
| `π` | 变量被选入最终集合的概率（跨分组/跨类汇总） | `variable_selector` |

#### 2.7.2 三个算法

- **Algorithm 1（签名）**：对分组 g 的模板集做 `N=100` 次 Bootstrap LiNGAM，保留 **`f_e≥0.70 且 dir_e≥0.90`** 的边为稳定边（`STABLE_F/STABLE_DIR`）；**论文原值 0.5/0.6 只在论文对照中使用，不进入产物**。签名 `c_{k,g}` = 稳定边的「端点 + 方向 + 强度分位」摘要，不存整张图。
- **Algorithm 2（总效应溯源）**：由稳定边构造系数矩阵 `B`，总效应 `T=(I−B)^{-1}`；节点 j 的排名分数 `score(j)=Σ_{s∈S_g}|T_{s,j}|`（`S_g` = 该组症状端集合），次级键 `in_degree ASC → weighted_reach DESC`。**禁止用"路径步数"作主排序键**。
- **Algorithm 3（反事实验证 gCF）**：5 折（`CF_N_FOLDS=5`）、每组至少 5 条（`CF_MIN_GROUP_SIZE=5`）；干预 `g*` 后两组同组重采样；`dr=Q3−Q1` 中位 z_marker `<0.10` 判 `cf_not_applicable`，`|Δobs|<0.1 且 |Δpred|<0.1` 判 `cf_inconclusive`，方向一致率 `<0.50` 判 `gCF=0.5`（fail），否则 `1.0`（pass）。**注意：这是"形式通过"，不等价于反事实可辨识性成立**（v2.1 O8 未闭环，见 §7.6）。

#### 2.7.3 双引擎的真实状态（不得夸大）

- 论文设定为 **LiNGAM + GAM-ANM 双引擎共识边**；**v2.3 现行实现为 LiNGAM 单引擎稳定边**（产物中 `consensus_available=false`、`n_consensus=null`），GAM-ANM 共识仅在显式开启 `--with-gam-consensus` 时可选（`GAM_CONSENSUS_MIN=5/10`）。
- 因此：正文与论文中凡写"共识边"处，若指 v2.3 产物，必须改写为"LiNGAM 稳定边（单引擎）"；写"共识边"时须同时注明是论文设定或已开启 GAM 的可选模式。

#### 2.7.4 评估集与遮蔽口径

- `E1`（全量）/`E2`（遮蔽 marker）/`E3`（遮蔽 marker 且仅保留响应变量）三个评估集的**成员定义保持不变**（v2.1 原义），仅权重与产物路径随 v2.3 对齐。
- **marker 遮蔽 = 从建模矩阵中移除 marker 列后重学图**；不是"学完图再把 marker 节点藏起来"。两者结果不同，不得混用。

---

## 3. 变量选取机制（核心技术问题）

### 3.1 问题定义

**现状**：v2.1 对 3 类事故使用硬编码变量集（LOCA=20变量、SGATR=20变量、FLB=20变量），变量选取依赖人工经验，无量化标准。

**目标**：建立**四阶段漏斗式变量选取机制**，从 NPPAD 的 96 个参数中自动筛选出每类事故的最优变量集，满足：
- 数据质量合格（非常量、有区分度）
- 统计特征适配 LiNGAM（非高斯、低共线性）
- 物理知识覆盖（子系统完整、因果链连续）
- 因果发现可计算（变量数 15-25、VIF<500）

### 3.2 论文锚定 + 四阶段漏斗架构

> **论文依据**：原文 p.2（Fig.2 Feature Selector）明确采用“data-driven feature selection methods or manual selection based on domain expertise”；p.4（Dataset Description）写明 “eight key variables identified through XGBoost feature selection and validated by domain experts”。**原文未给出这 8 个变量的名单**，也未报告 XGBoost 超参、重要性阈值、VIF/降维、变量集敏感性分析（Table 1 的消融是本体/知识图谱消融，不是变量集消融）。因此本方案不替论文补名单，只把“专家验证变量应优先进入候选”落成可执行条款（阶段0）：锚定是**候选优先 + 评分加权**，不是不可剔除的保护集；数据驱动在锚定之外补选，锚定变量与其他变量同等接受数据质量门、去冗余与 VIF 迭代的检验，剔除必须留理由。

```
96 个原始参数
    │
    ▼
┌─────────────────────────────────────┐
│ 阶段0：论文锚定（候选加权，§3.3）      │
│  - 锚定变量进候选优先队列 + 评分加权    │
│    （ANCHOR_SCORE_BONUS=0.15）        │
│  - 不与 forced 合并，不作保护集        │
│  - 原文未给名单 → 早期机理筛选集代理    │
│    （commit 544f3a3；工程假设）        │
│  → 可被去冗余/VIF/裁剪剔除，须留理由    │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 阶段1：硬过滤（数据质量门）            │
│  - 近常量列（原始 σ<1e-6 或唯一值<3）剔除│
│  - 缺失率 > 50% 剔除                   │
│  - 全程零值剔除                        │
│  → 约 40-60 个变量通过                  │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 阶段2：数据驱动排序（统计特征评分）      │
│  - 变异系数 CV（变异程度）权重 0.25     │
│  - 非高斯性（偏度+超额峰度）权重 0.20   │
│  - 严重度效应强度（轨迹级MI）权重 0.30  │
│  - 跨轨迹区分度（轨迹间方差占比）权重0.25│
│  → 综合得分排序，取 Top-30             │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 阶段3：物理知识校验（领域约束）          │
│  - 子系统覆盖度检查（每子系统≥1变量）    │
│  - 因果链完整性检查（关键路径变量必选）  │
│  - 冗余消除（同物理量多选代表性最强的）  │
│  - 触发变量与关键响应变量强制保留        │
│  → 约 20-30 个变量通过                  │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 阶段4：因果发现适配（算法约束）          │
│  - VIF < 500（消除病态共线）          │
│  - 变量数 15-25（LiNGAM方向辨识上限）   │
│  - 包含触发变量 + 关键响应变量          │
│  → 最终变量集（每类事故 15-25 个）       │
└─────────────────────────────────────┘
```

### 3.3 各阶段量化标准

#### 阶段0：论文锚定（候选加权约束，V23-DESIGN-018；返工 V23-DESIGN-023）

**论文事实（只引原文，不补话）**

| 事实 | 原文位置 | 原文要点 |
|------|----------|----------|
| 特征选择方式 | p.2, Fig.2 Feature Selector | “data-driven feature selection methods or manual selection based on domain expertise” |
| 最终变量集 | p.4, Dataset Description | “eight key variables identified through XGBoost feature selection and validated by domain experts” |
| 数据来源 | p.4 | potentiometers / load cells / drive temperatures / robot kinematics |
| 未报告项 | — | 8 个变量的**名单**、XGBoost 超参与重要性阈值、VIF/PCA/降维、变量集敏感性分析（原文未给出） |

**落地条款**

1. 锚定集 = 论文明确使用的变量。因原文未给名单，本方案以 **早期机理筛选集**（`rca.rca_config.CLASS_VARIABLES`：LOCA 14 / SGATR 15 / FLB 14，来源 commit `544f3a3`，2026-10-26 Initial commit）作为可执行代理，**显式标注为工程假设**，不得表述为“论文原始 8 变量”，也不得表述为“v2.1 专家冻结集”——它与 v2.1 硬编码 20 变量集不是同一集合（差异见下）。
2. 锚定变量进入**候选优先队列**并获得评分加权（`ANCHOR_SCORE_BONUS=0.15`，评分为 [0,1] 归一化），但**不是保护集**：阶段3 去冗余、阶段4 VIF 迭代、变量数裁剪均可剔除锚定变量。保护集只保留物理约束要求的 `forced`（触发变量 + 关键响应变量）与在选中的 `subsystem_reps`（子系统代表）。
3. 剔除必须写理由与影响：`dropped_anchor` 记录 `stage`（`stage1_or_missing`/`deduplicate`/`vif_iter`/`n_max_trim`）、`reason`（近常量、缺列、去冗余、VIF 实测值、N_MAX 裁剪）、`z_pathological`、`max_vif_at_drop`、`n_vars_before`；`anchor_impact` 记录锚定声明/保留/剔除数、**剔除前后 `max_vif_all`**、VIF 前后变量数、Top-1 变化指针。做到“剔了谁、为什么、代价是什么”可查。
4. 数据驱动（阶段1/2）在锚定集之外负责补选；阶段1 的数据质量门对锚定变量同样生效，剔除记入 `dropped_anchor(stage=stage1_or_missing)`（与 `dropped_stage1` 同源），不得静默丢弃。
5. 数据集未采集的锚定列记 `missing_in_data`，不报错（锚定自然失效）。

**早期机理筛选集 vs v2.1 硬编码集（实测差异）**

| 事故类 | 早期机理筛选集 | v2.1 `V_graph` | 交集 | 仅早期集有 | 仅 v2.1 有 |
|--------|---------------|----------------|------|-----------|-----------|
| LOCA | 14 | 20 | 13 | WRCA | LSGA, LSGB, PSGB, WFWA, WFWB, WSTA, WSTB |
| SGATR | 15 | 20 | 11 | TF, TSAT, WLR, WRCA | DNBR, LSGA, LSGB, WECS, WFWA, WFWB, WHPI, WSTA, WSTB |
| FLB | 14 | 20 | 12 | VOID, WRCA | LSGA, LSGB, WECS, WFWA, WFWB, WHPI, WSTA, WSTB |

> 是否改用 v2.1 硬编码集**不自行替换**，须人工确认。已知风险：v2.1 的 20 变量集同时包含 PSGA/PSGB、WFWA/WFWB、LSGA/LSGB 等近完全共线测点对（FLB 的 PSGA/PSGB 与 WFWA/WFWB 相关系数 1.000000），直接接入会把 v2.1 的共线性病带回流程。

**冲突裁决（可执行；锚定不享有绝对保护）**

| 冲突情形 | 裁决 | 理由 |
|----------|------|------|
| 锚定变量被阶段1 判为近常量/缺失>50%/全程零值 | **剔除并记录**（`dropped_anchor.stage=stage1_or_missing` + reason） | 无分布的列 LiNGAM 无法建模，强制保留只会产生 NaN/病态解 |
| 锚定变量与同子系统变量高度相关 | **剔除并记录**（`stage=deduplicate`，保留评分更高者） | 冗余测点不增加信息量，锚定加权只保证优先进入候选，不保证最终入选 |
| 锚定变量 Z 病态（σ/\|μ\| < 1%） | **不单独剔除**，仅标记 `z_pathological=true` | Z 病态是数值尺度问题，不是物理无关；若同时 VIF≥500，则在 VIF 迭代中剔除并记录 `z_pathological=true` |
| 锚定变量 VIF ≥ 500 | **剔除并记录**（`stage=vif_iter`，写实测 VIF、`max_vif_at_drop`、`n_vars_before`） | 共线性会破坏 LiNGAM 方向辨识；论文锚定是优先权，不是免剔除权 |
| 变量数 > N_MAX | 按评分裁剪，锚定变量同样可被裁剪（`stage=n_max_trim`） | 算法约束优先，裁剪留痕 |
| 锚定变量在数据集中不存在 | **记录 `missing_in_data`，不报错** | 数据集覆盖问题，不应阻断整类分析 |

> 与漏斗的关系：阶段0 是**候选约束**（优先队列 + 评分加权），不改变阶段1–4 的评分与过滤逻辑，也不豁免任何数据质量门与共线性校验；每一次保留/剔除都落在 `variable_sets/selection_report_{K}.json` 的 `paper_anchor` 块（`anchor_retained`/`dropped_anchor`/`anchor_impact`）。

#### 阶段1：硬过滤

| 指标 | 阈值 | 物理含义 |
|------|------|---------|
| 原始标准差 σ | < 1e-6 剔除 | 变量几乎不变，无因果信息 |
| 唯一值数 | < 3 剔除 | 近似常量列 |
| 缺失率 | > 50% 剔除 | 数据质量不足 |
| 全程零值 | 是则剔除 | 安全系统未动作时无信息 |

> **与Z病态的关系**：阶段1仅按原始σ判据剔除，不使用 CV=σ/|μ|（避免近零均值变量被误剔）。Z病态变量（σ/|μ|<1%）在阶段2评分时通过σ_floor修正，而非在阶段1删除。

#### 阶段2：数据驱动评分

综合得分公式：

```
score(v) = 0.25 × norm(CV(v)) 
         + 0.20 × norm(non_gaussianity(v))
         + 0.30 × norm(effect_strength(v))
         + 0.25 × norm(cross_traj_discrimination(v))
```

其中：
- `norm(x)` = 分位裁剪后的 min-max 归一化到 [0,1]（防单列饱和）
- `CV(v)` = σ/|μ|（变异程度，|μ|→0时改用σ排序）
- `non_gaussianity(v)` = |skewness(v)| + |kurtosis(v)|（**超额峰度**，pandas `.kurt()`正态≈0）
- `effect_strength(v)` = 变量与严重度的**轨迹级**互信息：对每条轨迹取窗口统计量（如均值），再与 severity 计算 MI（避免轨迹内severity为常数导致MI≡0）
- `cross_traj_discrimination(v)` = 轨迹间方差 / 总方差 = `1 − (组内方差均值 / 总方差)`（越大说明跨轨迹区分度越高）

**权重设计依据**：
- 效应强度权重最高（0.30）：与严重度强相关的变量是事故响应的核心指标
- CV 和跨轨迹区分度并列（0.25）：变异程度和区分度同等重要
- 非高斯性（0.20）：LiNGAM 前提，但不做硬门槛

#### 阶段3：物理知识校验

| 校验项 | 规则 | 数据来源 |
|--------|------|---------|
| 子系统覆盖 | 每类事故涉及的子系统至少有 1 个变量入选 | 本体：变量→子系统映射 |
| 因果链完整 | 预定义的关键传导路径上的变量必须入选 | 本体：物理关系清单 |
| 冗余消除 | 同一物理量的多个测点（如 THA/THB）只保留综合得分最高的 | 本体：变量功能分组 |
| 精确重复测点去重 | **先于强制保留执行**：同一物理量的 A/B 双测点若 `\|Pearson r\| ≥ 0.99`（等价 VIF≈50，实测 FLB PSGA/PSGB、WFWA/WFWB；SGATR PSGA/PSGB=0.999916、LSGA/LSGB=0.994073），只保留配置顺序里先出现的一路，另一路写 `dropped_key_response`（`pair`/`corr`/`retained`/`stage=stage3`）。被剔除者不得经阶段2 TOP_K、子系统代表、锚定变量、N_MIN 回补任一途径回流；若某子系统最高分变量恰为被剔除的重复测点，代表权交给同物理量已入选的另一路。**该阈值只决定同一物理量保留哪一路，不参与 V3/V5 判定** | 本体：变量功能分组 + 实测相关系数 |
| 触发变量保留 | 该事故的触发变量（如 LOCA 的 WLR）强制保留 | 事故配置表 |
| 关键响应保留 | 安全系统响应变量（如 WECS/WHPI）强制保留 | 事故配置表 |

#### 阶段4：因果发现适配

| 约束 | 阈值 | 处理方式 |
|------|------|---------|
| 变量数 | 15 ≤ p ≤ 25 | 超出则按综合得分从低到高剔除 |
| VIF（方差膨胀因子） | < 500（仅剔除 R²>0.998 的病态共线） | 迭代剔除 VIF 最高的变量，直到全部 < 500 |
| 触发变量 | 必须包含 | 不参与 VIF 剔除 |
| 关键响应变量 | 必须包含 | 不参与 VIF 剔除 |
| 子系统代表变量 | 必须包含 | 不参与 VIF 剔除 |
| 锚定变量 | 优先进入候选 + 评分加权 | **不在保护表内**：可被 VIF 迭代剔除，落 `dropped_anchor`（见 §3.3） |

### 3.4 变量选取算法伪代码

```python
import numpy as np
from sklearn.feature_selection import mutual_info_regression

def select_variables(accident_type, raw_data, ontology, severity_by_traj, random_state=42):
    """
    四阶段变量选取
    raw_data: DataFrame，含 trajectory_id 列 + 数值变量列
    severity_by_traj: dict {trajectory_id: severity_value}
    返回: selected_vars (list[str]), selection_report (dict)
    """
    np.random.seed(random_state)
    # 仅数值列，排除非物理变量（TIME、trajectory_id、severity 等标识列）
    EXCLUDE = {"TIME", "time", "trajectory_id", "traj_id", "severity", "Severity"}
    num_cols = [c for c in raw_data.select_dtypes(include=[np.number]).columns
                if c not in EXCLUDE]

    # 阶段1：硬过滤（缺失率用原始列，不dropna）
    candidates = []
    for v in num_cols:
        raw_series = raw_data[v]
        missing_rate = raw_series.isna().mean()
        series = raw_series.dropna()
        if len(series) == 0:
            continue
        sigma = series.std()
        if sigma < 1e-6 or series.nunique() < 3:
            continue
        if missing_rate > 0.5:  # 修复：用原始列缺失率，非dropna后
            continue
        if (series == 0).all():
            continue
        candidates.append(v)

    if not candidates:
        return [], {"error": "no candidates after stage 1"}

    # 阶段2：先收集所有候选变量的原始指标，再统一归一
    traj_stats = raw_data.groupby('trajectory_id')[candidates].mean()
    sev_array = np.array([severity_by_traj.get(tid, 0) for tid in traj_stats.index])

    raw_metrics = {"cv": {}, "non_gauss": {}, "mi": {}, "cross_disc": {}}
    for v in candidates:
        series = raw_data[v].dropna()
        mu = series.mean()
        sigma_raw = series.std()
        # σ_floor 修正：防Z病态（暂用 1%·|μ| 兜底，逐变量物理下限待 §5.9 tolerance 配置加载）
        mad = np.median(np.abs(series - mu))
        sigma_robust = max(1.4826 * mad, 0.01 * abs(mu)) if abs(mu) > 1e-9 else sigma_raw
        sigma = max(sigma_raw, sigma_robust)
        raw_metrics["cv"][v] = sigma / abs(mu) if abs(mu) > 1e-9 else sigma
        raw_metrics["non_gauss"][v] = abs(series.skew()) + abs(series.kurt())

        v_traj_mean = traj_stats[v].values
        if len(np.unique(sev_array)) > 1:
            raw_metrics["mi"][v] = mutual_info_regression(
                v_traj_mean.reshape(-1, 1), sev_array, random_state=random_state)[0]
        else:
            raw_metrics["mi"][v] = 0.0

        within_var = raw_data.groupby('trajectory_id')[v].var().mean()
        total_var = series.var()
        cd = 1.0 - (within_var / total_var) if total_var > 1e-12 else 0.0
        raw_metrics["cross_disc"][v] = max(0.0, min(1.0, cd))

    # 统一归一化：分位裁剪(5%-95%) + min-max
    cv_norm = norm_quantile_dict(raw_metrics["cv"])
    ng_norm = norm_quantile_dict(raw_metrics["non_gauss"])
    mi_norm = norm_quantile_dict(raw_metrics["mi"])  # 修复：MI也归一化

    scores = {}
    for v in candidates:
        scores[v] = (0.25 * cv_norm[v]
                    + 0.20 * ng_norm[v]
                    + 0.30 * mi_norm[v]   # 修复：归一化后的MI
                    + 0.25 * raw_metrics["cross_disc"][v])  # cross_disc已在[0,1]

    top30 = sorted(scores, key=scores.get, reverse=True)[:30]

    # 阶段3：物理知识校验
    trigger = ontology.get_trigger_var(accident_type)
    key_responses = ontology.get_key_responses(accident_type)
    forced = [v for v in [trigger] + key_responses if v is not None]
    # 论文锚定（方案 §3.3 阶段0）：候选优先 + 评分加权，不是保护集
    anchor = paper_anchor(accident_type, num_cols)   # 见 rca/v21/paper_anchor.py
    for v in anchor:                 # 命中候选的锚定变量加分并进优先队列
        if v in candidates:
            scores[v] = min(1.0, scores[v] + ANCHOR_SCORE_BONUS)
            if v not in top30:
                top30.append(v)

    for v in forced:
        if v not in top30 and v in candidates:
            top30.append(v)

    required_subsystems = ontology.get_required_subsystems(accident_type)
    for sub in required_subsystems:
        if not any(ontology.get_subsystem(v) == sub for v in top30):
            sub_cands = [v for v in candidates if ontology.get_subsystem(v) == sub]
            if sub_cands:
                best = max(sub_cands, key=lambda v: scores.get(v, 0))
                if best not in top30:
                    top30.append(best)

    top30 = ontology.deduplicate(top30, scores)

    # 阶段4：因果发现适配
    selected = list(top30)

    # 4a. VIF 迭代剔除（锚定变量与普通变量同等参与；上界用仍在集合内的保护变量计数）
    vif_log = []
    while len(selected) > len(set(forced) & set(selected)):
        vifs = compute_vif_safe(raw_data[selected])
        if vifs is None:
            break
        non_forced = [v for v in selected if v not in forced]
        if not non_forced:
            break
        max_vif_var = max(non_forced, key=lambda v: vifs.get(v, 0))
        if vifs.get(max_vif_var, 0) < 500:
            break
        vif_log.append((max_vif_var, vifs[max_vif_var]))
        selected.remove(max_vif_var)

    # 4b. 变量数约束 15-25
    if len(selected) > 25:
        non_forced_sorted = sorted([v for v in selected if v not in forced],
                                    key=lambda v: scores.get(v, 0), reverse=True)
        selected = forced + non_forced_sorted[:25 - len(forced)]
    elif len(selected) < 15:
        # 修复：回补时做VIF复检，不引入高共线变量
        remaining = sorted([v for v in candidates if v not in selected],
                           key=lambda v: scores.get(v, 0), reverse=True)
        for v in remaining:
            if len(selected) >= 15:
                break
            trial = selected + [v]
            vifs = compute_vif_safe(raw_data[trial])
            if vifs is not None and vifs.get(v, 0) < 500:
                selected.append(v)
            # 若VIF超标则跳过该候选，继续下一个

    return selected, {"scores": scores, "vif_log": vif_log,
                      "forced": forced, "n_candidates": len(candidates)}


def norm_quantile_dict(value_dict, lower=0.05, upper=0.95):
    """分位裁剪后 min-max 归一化到 [0,1]"""
    vals = list(value_dict.values())
    lo, hi = np.quantile(vals, [lower, upper])
    if hi - lo < 1e-12:
        return {k: 0.5 for k in value_dict}
    return {k: float(max(0.0, min(1.0, (v - lo) / (hi - lo))))
            for k, v in value_dict.items()}

> **术语防误解**：`V0–V7` 是**变量选取阶段的质量门控编号**（本文 §3.5 定义），与版本号 v2.1 / v2.3 **无关**；`v3_status` 指的是 V3 门控的三态结论，不是『v3 版本』。

### 3.5 校验规则（变量选取质量门控）

每类事故的变量集必须通过以下校验；未通过时**如实记录失败项并标记该类需人工裁决**，不自动回退、不调阈值掩盖。

| 校验项 | 通过条件 | 不通过处置 |
|--------|---------|-----------|
| V0 论文锚定 | 锚定集全体“入选，或有剔除记录”：每个未入选锚定变量都有 `dropped_anchor` 记录（`stage1_or_missing`/`deduplicate`/`vif_iter`/`n_max_trim`） | 记账校验（`V0_anchor_accounted`）；缺记录才是失败，剔除本身不算失败 |
| V1 变量数 | 15 ≤ p ≤ 25 | 按得分补充/剔除 |
| V2 子系统覆盖 | 所有相关子系统 ≥1 变量 | 强制补入该子系统最高得分变量 |
| V3 VIF | **豁免式 pass**：只看**非保护变量**（非 `forced`、非子系统代表）的 `max_vif < 500` → pass；保护变量逐项披露、不计入判定。`v3_status` 四值：`pass`（无豁免项）/ `pass_with_exemption`（有豁免项但非保护变量全达标）/ `fail` / `n/a`（VIF 不可算或 p<3，须在 `v3_note` 写明原因） | fail 时迭代剔除**非保护**变量；保护变量按物理约束保留。豁免项必须逐条写入 `v3_exempt[]`（变量 / VIF / 是否 ≥500 / 豁免原因 forced 或 subsystem_rep），同时报告 `max_vif`（非保护）与 `max_vif_all`（含保护）——**豁免不等于通过，必须逐项披露** |
| V4 触发变量 | 包含该事故触发变量 | 强制保留 |
| V5 物理链完整 | **有效集语义**：关键响应按"同一物理量只要留有另一路代表即算覆盖"判定（阶段3 已对 `\|r\|≥0.99` 的精确重复测点对去重）；被剔除的另一路在 `v5_note` 写明 `pair / 相关系数 / 保留哪一路 / 剔除阶段` | 放宽**仅限重复测点的等价代表**：非重复测点的关键响应缺失仍判 False，并在 `v5_missing` 明列后强制补入 |
| V6 选取客观性 | 综合得分可复现（同数据同结果） | 重跑一致性 |
| V7 非高斯诊断 | ≥60% 变量 \|skewness\| > 0.5 | 仅记录，不阻断（阶段5笔记 Q9） |

**为什么 V3 用"豁免式 pass"而不是把阈值放宽到 5000（即不采用 (b) 口径）**：阶段3 已按 `|r| ≥ 0.99` 剔除精确重复测点，V3 若再放宽 VIF 阈值，等于对同一份共线性做**二次放松**；且放宽阈值会把真正的自动选择缺陷一并掩盖（保护变量之外的变量本可被迭代剔除）。因此阈值 `VIF_MAX=500` 保持不变，改为把"设计上必须保留"的变量（`forced` 触发/关键响应、子系统代表）列入 `v3_exempt` 逐项披露、不参与判定，其余变量仍按 <500 严格判定——**披露换透明度，不换阈值**。

**变量列序口径（V23-CODE-023，2026-11-10 增补）**：进入因果发现前，变量集一律按**字母序（ASCII，变量名全大写）**固定列序，签名、溯源、A/B 对照共用同一顺序。
- 为什么必须固定：DirectLiNGAM 的逐变量回归顺序对输入列序敏感。实测 SGATR 变量集成员完全不变、仅 VOID 由第 5 列移到第 11 列，`SGATR_high` 的 Top1 即由 RM2 变为 RHBR——若不固定，Top1 依赖选择器内部输出顺序这一实现细节，无法通过审稿复现。
- 为什么选字母序而非本体序：本体序会隐含"物理可信度越高越靠前"的偏好，且随本体表增删漂移；字母序与选取得分、数据、本体表完全解耦，第三方可按变量名直接复算列序。
- 影响谁：签名（`signatures/*.json` 的 `variables` 字段）、溯源 Top-1/5、图件节点顺序、A/B 对照共用该顺序；稳定边**集合**不因列序改变（单测 `test_variable_order_permutation_invariance` 守护）。
- 检验方式：`test_bootstrap_fits_on_canonical_order` 断言任何传入列序最终都以字母序喂给 LiNGAM。

---

## 4. 12类事故统一适配方案

### 4.0 数据前提与 Go/No-Go 闸门

> ⚠️ **重要前提**：当前本地仅 **3 类事故（LOCA/SGATR/FLB）** 有完整三分组数据；**LOCAC 有 5 条轨迹**（不足以三分组）；其余 8 类（SLBIC/SLBOC/SGBTR/RW/RI/MD/LR/LLB）的列名/特征需**仿真生成或对接外部数据源**后才能落地。

**数据可用性标注**：

| 状态 | 事故类 | 说明 |
|------|--------|------|
| ✅ 已核实（完整） | LOCA, SGATR, FLB | 本地有数据，列名已确认，可三分组 |
| ⚠️ 部分数据 | LOCAC | 本地有 5 条轨迹，不足以三分组 |
| ⏳ 待核实 | SLBIC, SLBOC, SGBTR, RW, RI, MD, LR, LLB | 需仿真生成或对接，列名/注入时刻/恢复段待确认 |

**Go/No-Go 闸门（开工前必须满足）**：
- Go-1：至少 3 类已核实事故的端到端流程跑通（v2.1 已完成）
- Go-2：变量选取机制在 LOCA/SGATR/FLB 上 V1–V7 校验通过（不通过则回退硬编码，见 §3.5）
- Go-3：其余 9 类事故的数据源到位（仿真或真实），列名经 v2.1 `data_utils.py` 列名白名单校验
- No-Go：若 9 类待核实事故数据 30 天内无法到位，则本方案降级为 3 类适配，9 类配置保留为设计文档不实施

### 4.1 NPPAD 12类严重度事故清单

根据 NPPAD README Table 1，标为 **Severity** 类型的事故共 12 类（均有严重度参数，设计各 99-110 条轨迹；**实际本地数据状态见 §4.0**）：

| 编号 | 事故代码 | 英文名称 | 中文名称 | 严重度含义 | 轨迹数 |
|------|---------|---------|---------|-----------|--------|
| 1 | LOCA | Loss of Coolant Accident (Hot Leg) | 一回路热段失水事故 | % of 100 cm² | 100 |
| 2 | LOCAC | Loss of Coolant Accident (Cold Leg) | 一回路冷段失水事故 | % of 100 cm² | 100 |
| 3 | SLBIC | Steam Line Break Inside Containment | 安全壳内蒸汽管道破裂 | % of 100 cm² | ~100 |
| 4 | SLBOC | Steam Line Break Outside Containment | 安全壳外蒸汽管道破裂 | % of 100 cm² | ~100 |
| 5 | SGATR | Steam Generator A Tube Rupture | 蒸汽发生器A传热管破裂 | % of 1 full tube | ~100 |
| 6 | SGBTR | Steam Generator B Tube Rupture | 蒸汽发生器B传热管破裂 | % of 1 full tube | ~110 |
| 7 | RW | Rod Withdrawal | 提棒（控制棒抽出） | % (+/-) withdrawn | ~100 |
| 8 | RI | Rod Insertion | 插棒（控制棒插入） | % (+/-) insertion | ~100 |
| 9 | FLB | Feedwater Line Break | 给水管道破裂 | % of 100 cm² | ~100 |
| 10 | MD | Moderator Dilution | 慢化剂稀释 | % of unborated injection | ~100 |
| 11 | LR | Load Rejection | 甩负荷 | % of full load rejected | ~100 |
| 12 | LLB | Letdown Line Break | 下泄管线破裂 | % of nominal letdown flow | ~100 |

> **注意**：SP（氢气燃烧）、LACP（失去交流电源）、LOF（失流）、ATWS（未能停堆瞬变）、TT（汽轮机跳闸）为 **Other** 类型，无严重度参数，每类仅单轨迹，**不纳入 v2.3 的 12 类适配**（阶段5笔记：单轨迹事故禁止伪重复式硬做）。

### 4.2 统一框架（算法层事故无关，直接复用）

所有 12 类事故共享同一套算法工程层，直接复用 v2.1/v2.2 已实现代码：

| 模块 | 复用内容 | v2.1已有 | v2.3增量 |
|------|---------|---------|---------|
| 数据加载 | 轨迹级加载、列名校验、常量列审计 | ✅ | 扩展至 12 类路径 |
| 标准化 | 逐类 sev-1 基准的稳健 σ 标准化（μ/σ_robust，含 σ_floor） | ✅ | 替代原全局 Z-score，与 §5.9 一致 |
| 变量选取 | 硬编码 V_graph[k] | ✅ | **§3 四阶段漏斗机制** |
| 因果发现 | LiNGAM + GAM-ANM 双引擎 | ✅ | 无 |
| Bootstrap | 四指标（f_e/s_e/方向/符号一致率） | ✅ | 无 |
| 总因果效应 | T=(I-B)⁻¹ 逐次求逆 | ✅ | 无 |
| 反事实验证 | 探索性一致性比较 | ✅ | 无 |
| 根因溯源 | **总因果效应排序 T=(I-B)⁻¹**（论文 Algorithm 2），拓扑为次级键 | ✅ v2.3 | 无 |
| 可视化 | 0依赖 HTML+SVG | ✅ v2.2 | 12类各出图 |

### 4.3 分组针对性规则（物理层事故相关，按机理重建）

12 类事故按物理机理分为 6 组，每组共享变量集模板与本体规则，但触发变量、关键响应变量、时间窗口各自独立配置。

> **时间窗口体系（继承 v2.1 RCA-030 学图窗修正）**：统一采用相对注入时刻的公式，按类注入时刻校正：
> - **主分析窗** `[t_inject+190, t_inject+1390]s`：用于统计、评分、异常判定（避开注入阶跃）
> - **学图窗** `[t_inject−50, t_inject+1390]s`：用于 LiNGAM/GAM-ANM 学图（含注入前稳态段，避免 marker 平台量伪方向）
> - **组4（反应性引入类）例外**：响应快，主分析窗缩短为 `[t_inject+190, t_inject+890]s`，学图窗 `[t_inject−50, t_inject+890]s`
>
> 各组注入时刻（t_inject）：组1/3/5/6 ≈ 110s，组2 ≈ 110s，组4 ≈ 110s（待数据核实）。
> Phase 0 审计须落盘 `learn_window` 与 `main_window` 字段。

#### 组1：一回路破口类（LOCA / LOCAC）
**机理**：一回路冷却剂丧失 → 压力下降 → 温度上升 → 安全注射
- **触发变量**：LOCA→WLR（泄漏流量），LOCAC→WLR（冷段泄漏）
- **关键响应**：P（压力）、TAVG（平均温度）、VOL（容积）、WECS（安注）、WHPI（高压注水）
- **子系统覆盖**：一回路（P/TAVG/VOL）+ 安全注射（WECS/WHPI）+ 堆芯（DNBR/QMWT）
- **时间窗口**：主分析窗 [300,1500]s，学图窗 [60,1500]s
- **特有规则**：WLR↔P 为双向反馈环，按竞争假设双向报告

#### 组2：蒸汽管道破裂类（SLBIC / SLBOC）
**机理**：二回路蒸汽管道破裂 → 蒸汽流失 → SG 压力下降 → 给水流量变化
- **触发变量**：WSTA（SG-A 蒸汽流量）、WSTB（SG-B 蒸汽流量）（两类共用，A/B 侧与破口位置无关）
- **关键响应**：PSGA/PSGB（SG压力）、WFWA/WFWB（给水流量）、TAVG
- **子系统覆盖**：二回路（PSG/WST/FW）+ 一回路耦合（P/TAVG）
- **时间窗口**：主分析窗 [300,1500]s，学图窗 [60,1500]s（蒸汽泄漏虽响应较快，但注入时刻≈110s，[300,1500] 已避开注入阶跃且覆盖主要动态；统一窗口公式便于跨组比较）
- **SLBIC vs SLBOC 区分**：破口在安全壳内(SLBIC)/外(SLBOC)，靠安全壳压力(PRB)或隔离信号区分，与 SG A/B 侧无关；SLBIC 有安全壳隔离响应，SLBOC 无

#### 组3：SG传热管破裂类（SGATR / SGBTR）
**机理**：SG传热管破裂 → 一回路冷却剂漏入二回路 → SG 压力/水位异常 → 辐射剂量升高
- **触发变量**：WTRA（SG-A 泄漏流量）/ WTRB（SG-B 泄漏流量）
- **关键响应**：PSGA/PSGB、LSGA/LSGB（SG水位）、RM2（辐射剂量）
- **子系统覆盖**：SG-A/SG-B（PSG/LSG/WTR）+ 剂量监测（RM2/STSG）
- **时间窗口**：主分析窗 [300,1500]s，学图窗 [60,1500]s
- **特有规则**：SGATR 与 SGBTR 变量集对称，仅 A/B 侧变量互换

#### 组4：反应性引入类（RW / RI / MD）
**机理**：反应性变化（控制棒移动/硼浓度变化）→ 功率变化 → 温度/压力响应
- **触发变量**：RW/RI→RHRD（反应性），MD→PPM（硼浓度）
- **关键响应**：PWNT（功率）、TAVG、P、QMWT（堆芯热流）
- **子系统覆盖**：反应性控制（RHRD/PPM）+ 堆芯（PWNT/QMWT/DNBR）+ 热工（TAVG/P）
- **时间窗口**：主分析窗 [300,1000]s，学图窗 [60,1000]s（组4缩短窗）
- **特有规则**：RW 与 RI 方向相反（提棒增功率/插棒降功率），变量集共享但响应符号相反

#### 组5：给水/负荷类（FLB / LR）
**机理**：给水系统故障或负荷变化 → 二回路热阱变化 → 一回路温度/压力响应
- **触发变量**：FLB→WFLB（给水流量），LR→TBLD（汽机负荷）
- **关键响应**：WFWA/WFWB、TAVG、PWNT、PSGA/PSGB
- **子系统覆盖**：给水系统（WFW/WFLB）+ 二回路（PSG）+ 一回路（TAVG/P）
- **时间窗口**：主分析窗 [300,1500]s，学图窗 [60,1500]s
- **特有规则**：FLB 是给水丧失（流量骤降），LR 是负荷骤降（功率不匹配）；响应模式不同。注：FLB 的 WFWA 在 sev≈10 处存在断点（机制切换），落在 low 组内部，需在 §4.4.2 变点检测中标注并考虑组内子段处理

#### 组6：下泄管线类（LLB）
**机理**：化学容积控制系统下泄管线破裂 → 冷却剂流失 → 容积/压力变化
- **触发变量**：WLD（下泄流量）
- **关键响应**：VOL（一回路容积）、P、LVPZ（稳压器水位）、WECS
- **子系统覆盖**：化容系统（下泄/上充）+ 一回路（VOL/P/LVPZ）
- **时间窗口**：主分析窗 [300,1500]s，学图窗 [60,1500]s
- **特有规则**：与 LOCA 同属冷却剂丧失，但路径不同（下泄管线 vs 一回路破口）

### 4.4 适配流程（每类事故独立执行）

```
对每个事故类型 k ∈ {12类}：
    │
    ▼
Phase 0：数据审计（事故相关，必须重做）
    - 列名校验、常量列识别、轨迹数统计
    - 时间窗口配置（按 §4.3 分组）
    - 共享基线 Z 病态检测（§4.4.1）← 新增
    - 严重度变点检测（§4.4.2）← 新增
    - 落盘字段：learn_window, main_window, z_pathology, changepoints, n_trajectories
    │
    ▼
Phase 1：变量选取（§3 四阶段漏斗）
    - 输入：该事故 96 个参数 + 严重度标签
    - 输出：V_graph[k]（15-25个变量）+ 选取报告
    │
    ▼
Phase 2：因果发现（算法层复用）
    - 标准化（含 σ_floor 稳健尺度，见 §4.4.1）→ LiNGAM + GAM-ANM → Bootstrap 四指标
    - 共识边 + RRF 融合
    │
    ▼
Phase 3：根因溯源 + 可视化（v2.3总因果效应排序 + §4.6 严重度分组决策）
    - 总因果效应排序(T=(I-B)⁻¹) → 传导子图 → HTML+SVG
    - 分组可视化 + 跨严重度汇总 + 差异分析
    │
    ▼
Phase 4：知识融合解释（§5 六组件）
    - 本体约束 + 公差异常 + LLM + 模板 → RCA报告
```

#### 4.4.1 共享基线 Z 病态检测（数据审计必做项）

**知识来源**：v2.1 实验解读 §1.2(a) — 共享基线 σ 使 z 病态。

**问题定义**：当基线段（sev-1 恢复段）系统接近稳态时，σ 极小（如 WSTA σ=0.0602，σ/|μ|=3.1×10⁻⁵），导致 z=(X−μ)/σ 出现病态值（如 WSTA |z|≈3.1×10⁴）。此时 z 不再是"标准差意义上的偏离"，而是被少数饱和变量支配的**尺度伪影**。

**v2.1 实证数据**：

| 变量 | μ | σ | σ/\|μ\| | 事故主窗最大 \|z\| |
|------|---:|---:|---:|---:|
| P | 156.02 | 0.740 | 4.8e-3 | 156 |
| VOL | 288.34 | 1.764 | 6.1e-3 | 90 |
| LSGA | 11.838 | 0.00384 | 3.2e-4 | 863 |
| WFWA | 1915.51 | 0.512 | 2.7e-4 | 3739 |
| WSTA | 1915.89 | 0.0602 | 3.1e-5 | ≈3.1×10⁴ |

**审计步骤**（在 Phase 0 对每类事故执行）：

```
对每类事故 k：
    1. 加载 sev-1 基线段（按 §5.9 识别的准稳态段）
    2. 对每个变量计算 μ、σ、σ/|μ|
    3. 标记 σ/|μ| < 1% 的变量为"潜在病态变量"
    4. 对潜在病态变量，计算主窗内的 max|z|
    5. 若 max|z| > 100，标记为"Z病态变量"，记录到审计报告
    6. 输出审计结果：病态变量清单 + σ/|μ| 分布
```

**应对措施**（对应 v2.1 O3）：

| 措施 | 实现方式 | 适用范围 |
|------|---------|---------|
| **稳健 σ** | σ = max(1.4826·MAD, σ_floor(v))，其中 **σ_floor(v) 为逐变量物理下限**（按 NPPAD 量纲配置：压力 bar 级取 0.5bar，水位 % 取 0.5%，流量 t/hr 取 5t/hr）；无物理下限配置时回退到 1%·\|μ\| | 所有变量标准化时 |
| **z 截断** | 标准化后 z = clip(z, −10, 10)，并做截断敏感性分析 | 所有涉及 z 的计算 |
| **饱和变量特判** | 对会归零/饱和的变量（WFWA、WSTA、WFLB 等），改用相对变化率 ΔX/\|μ\| 或状态特征（0/1），不参与符号一致率分母 | 变量选取阶段3标记 |

> **σ_floor 敏感性分析**：对 1%·\|μ\| 下限做 0.5%/1%/2% 三档敏感性，报告根因排名变化。若变化>20%，说明 σ_floor 为敏感参数，需逐变量精细化。

**与 v2.3 其他模块的衔接**：
- **§3 变量选取**：Z 病态变量 σ 极小导致 CV=σ/|μ| 偏低（不反映真实变异），阶段2评分前需先用 σ_floor 修正 σ 再算 CV
- **§5.9 基准数据**：sev-1 基准的阈值计算必须使用稳健 σ，不能用原始 σ
- **§5.8 Algorithm 3**：反事实验证的输入 z-score 需使用截断后的稳健 z

#### 4.4.2 严重度变点检测（审计与信息门控，**不改分组边界**）

**知识来源**：v2.1 实验解读 §1.2(c) + O4 — 严重度扫描不是干净剂量阶梯，存在机制断点。

**问题**：v2.1 写死 low/mid/high = [2,33]/[34,66]/[67,100]，但实际数据中 SGATR 在 sev87 处存在 WTRA 掉档（机制切换），LOCA 的 WLR 非单调。固定分组可能导致"分组边界 + 局部斜率 + 病态尺度"的合成伪影。

**现行处理（按事实）**：变点检测**只作审计与信息门控**，分组边界仍为预注册的固定三段（§0.2 第 2 条）。是否改为数据驱动分组属研究裁决（O4，见 §9.3），实现方不擅自改。

**审计步骤**：

```
对每类事故 k：
    1. 提取 marker 对 severity 的中位数曲线
    2. 用 PELT/CUSUM 检测变点（实测：LOCA 7/12/21/43，SGATR 87，FLB 0）
    3. 变点结果写入 changepoint_report_{K}.json，作为"组内机制切换"的披露信息
       —— 不据此移动 low/mid/high 边界（避免按结果分组）
    4. 若 marker 非单调（如 LOCA WLR），标记为"marker不稳定"，并在报告与图上注明
    5. 输出：变点位置 + marker 稳定性评级 + 对现有分组的影响说明
```

**三类 marker 行为参考**（v2.1 实证）：

| 事故 | marker | 行为 | 历史建议（**未采纳**，仅披露） |
|------|--------|------|---------|
| FLB | WFLB | 严格线性，WFWA 在 sev≥10 归零 | 可按三分组，WFWA 在组内特判（已按特判披露，不改边界） |
| SGATR | WTRA | 大体线性，sev87 处断裂 | 曾有"high 组从 sev88 起"的建议，未采纳；现以 sev87 断点作披露 |
| LOCA | WLR | 非单调（sev21 跳变） | marker 不稳定，需辅助变量确认（已在报告中标注） |

### 4.5 marker 弱化策略

**项目需求**：因限定为单一事故内的因果变量传导，无需突出 marker 模块。

**v2.3 处置**（取消 V2.2 的 marker 遮蔽逻辑，采用 FULL/MASKED 双口径）：

**FULL 口径**（含 marker）：
- marker（如 LOCA 的 WLR）作为普通变量参与因果发现和排名
- 用途：展示完整因果图，marker 作为注入点自然出现在图中

**MASKED 口径**（排除 marker 的传导根因）：
- 根因排名时排除 marker，考察"非注入点的传导根因"
- 用途：避免"根因=注入点"的同义反复
- **Q4 验收采用 MASKED 口径**：Top1（排除 marker 后）对效应变量总因果效应最大

**与 V2.2 的差异**：V2.2 实现了 marker 遮蔽（marker 不参与根因排名），V2.3 改为双口径——FULL 保留完整图，MASKED 用于根因排名验收。

**与 §3.3 触发变量强制保留的关系**：marker 即触发变量，在变量选取阶段3 中**强制保留**（非"自然入选"）。两者一致：marker 作为触发变量被强制选入变量集，但根因排名验收时用 MASKED 口径排除 marker。

- 可视化中 marker 不特殊标注（与其他变量同等待遇）

### 4.6 严重度分组处理决策

> **分组策略定位**：v2.3 采用 **low/mid/high 三分组**（沿用 v2.1 既有分组规则，RCA-030 是**学图窗修正**而非冻结分组），作为工程实现方案。v3 方案提出的 **regime map**（数据驱动分段）为后续研究方向，当前 v2.3 不实现 regime map，避免与已定稿流程冲突。§4.4.2 的变点检测结果仅作为分组边界的参考调整，不替代三分组。
>
> **与 PROJECT_DEV_SPEC 的冲突留痕**：PROJECT_DEV_SPEC §4.3 要求"不使用固定三分组"。本方案经用户决策覆盖该条款，三分组为最终方案；dev spec 需同步修改。FLB 的 WFWA 在 sev≈10 处存在机制断点（落在 low 组内部），已在 §4.3 组5标注，需在变点检测中考虑组内子段或分两段报告。

#### 4.6.1 决策背景

v2.2 当前为每类事故的 low/mid/high 三个严重度组分别生成可视化。经实证核查，不同严重度的因果图和根因排名存在**显著差异**：

| 事故 | low Top1 | mid Top1 | high Top1 | low边数 | mid边数 | high边数 |
|------|---------|---------|----------|--------|--------|---------|
| LOCA | LVPZ | LVPZ | LVPZ | 36 | **1** | 38 |
| SGATR | P | P | **PSGA** | 41 | 45 | 25 |
| FLB | P | **PWNT** | PWNT | 7 | 21 | 23 |

**核心发现**：
- **机理切换**：SGATR 的 Top1 从 P（low/mid）变为 PSGA（high），FLB 从 P（low）变为 PWNT（mid/high），说明不同严重度下因果传导机理发生切换
- **信息不足组**：LOCA_mid 仅 1 条 consensus 边、FLB_low 仅 7 条，这些组的根因排名不可靠
- **边数差异**：同类事故不同严重度的稳定边数可相差 30 倍以上

#### 4.6.2 三方案评估

| 方案 | 根因准确性 | 学习效率 | 结果实用性 | 核心问题 |
|------|-----------|---------|-----------|---------|
| ① 三类分别完整学习 | ⭐⭐⭐⭐⭐ 最高 | ⭐⭐ 最低（36份） | ⭐⭐⭐⭐ 高 | mid组可能白跑 |
| ② 选取代表性样本 | ⭐⭐⭐ 中等 | ⭐⭐⭐ 中等 | ⭐⭐⭐ 中等 | 代表性标准难定义，切换点不可预知 |
| ③ 统一合并单一图像 | ⭐ 最低 | ⭐⭐⭐⭐⭐ 最高 | ⭐ 低 | 掩盖机理切换，合并产生伪边 |

#### 4.6.3 最优决策：分级完整学习 + 信息门控 + 汇总补充

**采用方案①为主体**，增加两处优化：

```
对每类事故 k：
    │
    ├─ Step 1：分别对 low/mid/high 跑完整因果发现+溯源+可视化（方案①主体）
    │
    ├─ Step 2：信息门控（新增）
    │    if 某组 consensus 边数 < 阈值（如5条）:
    │        标记该组为"信息不足"，根因排名标注为"不可靠"
    │        该组图像保留但加灰色水印提示
    │
    ├─ Step 3：跨严重度汇总视图（新增，补充）
    │    - 合并三组的稳定边（取并集，保留各边在哪些组出现的标签）
    │    - 根因排名取三组的"频率投票"：某变量在多少组中进入Top5
    │    - 输出汇总图像，边上标注"low/mid/high"出现组
    │
    └─ Step 4：差异分析报告（新增）
         - 列出Top1切换的事故（如SGATR: P→PSGA）
         - 列出边数差异>50%的组
         - 标注机理切换点
```

**不选方案②的理由**：代表性样本无法预先确定切换点。SGATR 切换在 mid→high，FLB 在 low→mid，不同事故切换点不同，无法统一选一个"代表"。

**不选方案③的理由**：合并会掩盖机理切换。SGATR 合并后 P 和 PSGA 的竞争关系会被平均，输出的根因既不是 low 的 P 也不是 high 的 PSGA，物理上无意义。

#### 4.6.4 预期产出

| 产出 | 数量 | 内容 |
|------|------|------|
| 分组因果图 | 36份（12类×3） | 各严重度独立溯源，边数<5的标注"信息不足" |
| 汇总因果图 | 12份（12类×1） | 跨严重度边并集，边标出现组，根因频率投票 |
| 差异分析 | 12份 | 机理切换点、Top1变化、边数差异 |

#### 4.6.5 注意事项

1. **mid组处理**：LOCA_mid 仅 1 条边的情况，不强行排名，标注"该严重度下因果结构不稳定"。物理上可能是系统响应的过渡区。
2. **汇总图边标签**：每条边标注出现的严重度组（如 `[low, high]`），区分跨严重度稳健关系与特定严重度特有关系。
3. **根因频率投票**：汇总图根因排名用"进入Top5的组数"而非得分平均，避免不同组得分尺度不一致。
4. **不降级处理**：保留现有框架，信息不足的组只标注，不删除。

### 4.7 溯源与可视化口径（吸收 v2.2 方案）

v2.2 的溯源/可视化设计已并入 v2.3，但排序键与路径深度按现行实现收敛。以下为唯一口径，v2.2 原文中与之不同的表述一律以本节为准。

| 项 | 现行口径（依据） | 与 v2.2 原文的差异 |
|----|------------------|--------------------|
| 溯源输入 | 该「类 × 严重度组」的签名稳定边 + 症状端集合 | 无 |
| **排名主键** | **总效应矩阵 `T=(I−B)^{-1}`，`score(j)=Σ_{s∈S_g}|T_{s,j}|`**，次级键 `in_degree ASC → weighted_reach DESC`（`source_tracing.py`） | v2.2 原为纯拓扑序（`in_degree↑ → weighted_reach↓ → reach_size↓`）；**拓扑指标降为次级键与展示用** |
| **传导路径深度** | BFS 上限 **3 层（S1/S2/S3）**，`FRONTIER_MAX_DEPTH=3` | v2.2 原文写"最大长度 4"，以 3 为准 |
| **marker 双口径** | 产品同时输出「未遮蔽 / 遮蔽 marker」两套排名，并强制校验 marker 在未遮蔽口径下为 Top1（sanity check，不通过则告警） | v2.2 只描述"双口径"，未规定校验 |
| Top-K | 5（每类每组） | 无 |
| 可视化实现 | **0 依赖 HTML + 内联 SVG**：不引 CDN、不引 JS 框架、不写文件外链；由 `graph_visualization.py` 从 JSON 产物直接渲染 | 无 |
| 忠实性 | 图与排名**只能由产物 JSON 生成**（禁止手改 HTML/SVG）；生成后回读核对节点/边数与 JSON 一致 | 无 |
| 信息门控水印 | 低模态数/高截断比的组在图上打"信息不足"水印，不删图、不改数 | 无 |
| 产物路径 | `output/rca_v23/tracing/root_candidates_{K}_{g}.json`、`output/rca_v23/graphs/graph_{K}_{g}.{html,svg}` | v2.2 的 `v23_tracing/`、`v23_graphs/` 为冻结只读历史路径 |
| A/B 冻结 | 论文用图与报告（A/B 两组）为**一次性冻结件**，不随主链重算；重算需求须走 §6.4 例外 | v2.2 未涉及 |

---

## 5. 多源知识融合体系

### 5.1 现有实现 vs 论文方案：可替换性评估

#### 5.1.1 评估结论

现有 JSON 本体 + 三西格玛阈值 + 代码本体的实现，是论文 RDF 知识图谱 + Neo4j 过程本体在 NPPAD 数据规模（96 通道、每类 93 个数值列、15 条入模变量、26 条物理关系）下的**合理轻量适配**。**不建议直接替换为 RDF/Neo4j 技术栈**，理由如下：

| 维度 | 论文 RDF/Neo4j | 现有 JSON 实现 | 替换必要性 |
|------|---------------|---------------|-----------|
| 数据规模 | 制造业数百传感器 | 核电 96 通道（93 数值列） | ❌ JSON 已能承载 |
| 查询复杂度 | 跨实体多跳 SPARQL | 变量→子系统/关系字典查询 | ❌ 无需图遍历 |
| 运维成本 | 需 Neo4j 数据库服务 | 纯 Python 字典，零依赖 | ❌ 增加不必要复杂度 |
| 关系表达 | RDF 三元组（主-谓-宾） | ALLOWED_RELATIONS 列表（from/to/direction/level/basis） | ❌ 信息等价 |
| 知识纪律 | 允许边只标记不添加 | ✅ 已实现（forbidden删、allowed标） | ✅ 已对齐 |

**升级方向**：不替换技术栈，而是**补全 v3 三通道（K1/K2/K3）** + **外部核物理知识接入** + **基准数据按事故类独立**。

#### 5.1.2 各组件可替换性详评

| 现有组件 | 论文对应 | 可替换性 | 升级策略 |
|---------|---------|---------|---------|
| `ENTITY_TABLE`（96 通道） | RDF 知识图谱 | 低（信息等价） | 已全量内置，按类补外部知识（已实现） |
| `ALLOWED_RELATIONS`（3类） | RDF 关系三元组 | 低（结构等价） | 扩展到 12 类 + K2 数值约束 + K3 机制模板 |
| `_forbidden_rules`（R1/R2/R3） | 本体约束 | 低（逻辑等价） | 保留 + 外部知识补充禁止边 |
| `build_ontology()` | Neo4j 过程本体 | 低（功能等价） | 保留 JSON，新增 RDF 导出接口（可选） |
| `tolerance.py::build_tolerance()` | 专家公差范围 | 中（数据驱动替代） | **已落地：每类事故 sev-1 注入前 [0,100]s 独立基准**（详见 §5.9；`build_thresholds_from_loca1_steady()` 为 v1 遗留，不再调用） |
| `counterfactual_validation()` | Algorithm 3 | 高（已完整实现） | 补充论文对应逻辑说明（详见 §5.8） |

### 5.2 外部核物理知识专家库接入路径

#### 5.2.1 知识来源分层

| 来源 | 内容 | 接入方式 | 优先级 |
|------|------|---------|--------|
| **L1 NPPAD README** | 97 变量字典（名称/单位/物理含义） | 已内置于 ENTITY_TABLE，需补全到 96 变量 | P0（必须） |
| **L2 核电工程手册** | 物理关系（质量守恒、水位动态、安注触发逻辑） | YAML 文件 `physics_relations.yaml`，人工编写 + LLM 辅助 | P1（重要） |
| **L3 FMEA/故障树** | 设备×故障模式本体、预期机制模板 | YAML 文件 `fault_templates.yaml` | P2（增强） |
| **L4 专家公差** | 传感器正常运行范围、报警阈值 | 数据驱动（sev-1 准稳态段）优先，外部标注补充 | P1（重要） |
| **L5 文献/LLM 提取** | 从核电事故分析文献中提取因果关系 | DeepSeek 辅助提取 + 人工审核 | P2（增强） |

#### 5.2.2 接入架构

```
external_knowledge/
├── README.md                    # 知识来源与版本说明
├── variable_dictionary.yaml     # L1: 96变量完整字典（从README提取）
├── physics_relations.yaml       # L2: 近似物理关系（K2数值约束）
├── fault_templates.yaml         # L3: 故障模式机制模板（K3）
├── forbidden_edges.yaml         # 外部补充禁止边
└── tolerance_overrides.yaml     # L4: 外部专家公差覆盖（可选）
```

**加载流程**：
```python
def load_external_knowledge(accident_type):
    """从external_knowledge/加载外部知识，与代码本体合并。"""
    # 1. 加载变量字典 → 补充 ENTITY_TABLE
    var_dict = load_yaml("variable_dictionary.yaml")
    # 2. 加载物理关系 → 补充 ALLOWED_RELATIONS + K2约束
    phys_rel = load_yaml("physics_relations.yaml")
    # 3. 加载故障模板 → K3机制模板
    templates = load_yaml("fault_templates.yaml")
    # 4. 加载外部禁止边 → 补充 _forbidden_rules
    forbidden_ext = load_yaml("forbidden_edges.yaml")
    # 5. 合并：外部知识只扩展，不覆盖代码本体已有定义
    merged = merge_knowledge(code_ontology, external_knowledge)
    return merged
```

**知识纪律**：
- 外部知识只**扩展**代码本体，不**覆盖**已有定义
- 所有外部知识必须标注来源（文件+行号）和置信度
- 禁止边：外部与代码本体取并集
- 允许边：外部只标记，不强制添加（与代码本体纪律一致）
- 冲突处理：外部知识与代码本体冲突时，以代码本体为准（代码本体已审核），外部知识标记为"待审核"

### 5.3 知识图谱 / 过程本体升级改造点

#### 5.3.1 改造清单

| 改造项 | 现有 | 升级后 | 涉及文件 |
|--------|------|--------|---------|
| **变量覆盖** | `CANDIDATE_UNION` 23 变量（v2.0 早期审计起点） | 96 通道全量（`ENTITY_TABLE`，每类数据审计数值列 93） | `knowledge.py` + `variable_dictionary.yaml` |
| **事故覆盖** | ALLOWED_RELATIONS 3 类 | 12 类（按 §4.3 分组配置） | `knowledge.py` |
| **K1 约束** | forbidden(R1/R2/R3) + allowed + competitive | 保留 + 外部禁止边补充 | `knowledge.py` + `forbidden_edges.yaml` |
| **K2 数值约束** | 无 | 5 类近似物理关系（装量-泄漏、SG水位、安注触发、传热管泄漏、反应性-功率） | `physics_constraints.py`（新建）+ `physics_relations.yaml` |
| **K3 机制模板** | 无 | 12 类事故的预期机制模板（边集+方向+时序） | `fault_templates.yaml` |
| **公差基准** | 仅 LOCA_1（v1 遗留，`accidents/loca/anomaly.py`） | **每类事故 sev-1 注入前 [0,100]s 独立基准**（详见 §5.9） | `tolerance.py` |
| **RDF 导出** | 无 | 可选：JSON→RDF 导出接口（兼容论文查询） | `rdf_export.py`（可选，P3） |

#### 5.3.2 K2 数值约束设计（对应 v3 §9.2）

| # | 近似关系 | 变量 | 遗漏项（必须写明） | 适用事故 |
|---|---------|------|-------------------|---------|
| 1 | 一回路装量-泄漏 | WLR ↔ d(VOL)/dt | 安注/补水、密度变化、未测小泄漏 | LOCA/LOCAC/LLB |
| 2 | SG 水位动态 | (WFW − WST) ↔ d(LSG)/dt | 压力/密度/相变、排污、未测流入 | FLB/SGATR/SGBTR |
| 3 | 安注触发 | P 低阈 ↔ WHPI/WECS 阶跃 | 手动/自动逻辑、阈值回差 | 高严重度事故 |
| 4 | 传热管泄漏 | WTR ↔ LSG/PSG | 壳侧排放、控制动作、饱和 | SGATR/SGBTR |
| 5 | 反应性-功率 | RH ↔ d(PWNT)/dt | 温度反馈、氙毒、控制棒动态 | RW/RI/MD |

**实现要求**（对应 v3 §9.2）：
1. 核实变量物理含义与单位
2. 写出完整或近似方程
3. 列出遗漏项与适用工况
4. 阈值来自正常段残差分布、仿真步长或工程容差（逐关系登记，禁止统一魔数）
5. 三态判定（违反/未违反/不可判定）并报告"不可判定"比例
6. 明确"满足约束≠方向证明、违反≠链错（可能遗漏项导致）"

#### 5.3.3 K3 机制模板设计（对应 v3 §9.3）

每类事故一个机制模板，包含：
- **预期边集**：该事故机理下应出现的因果边（from/to/direction/basis）
- **时序约束**：变量异常的预期先后顺序（如 LOCA：WLR↑ → VOL↓ → P↓ → TAVG↑）
- **冲突处理**：模板与数据冲突的边标"冲突"，进入竞争解释，不删除

**知识纪律**（v3 §9.4）：模板必须来自**独立知识来源**（README、工程手册、FMEA），**不得**从训练数据+标签中提取规律再评价测试数据（否则标签泄漏）。

### 5.4 六组件定义

| 组件 | 来源 | 输入 | 输出 | 触发条件 |
|------|------|------|------|---------|
| **C1 物理领域知识** | 核电工程经验 + 论文 + 外部专家库 | 变量集 | 子系统映射、物理关系清单、禁止边/允许边 | 变量选取阶段3、因果发现后约束 |
| **C2 结构知识** | 论文 Algorithm 1/2/3 | 因果图、总效应矩阵 | 稳定边筛选、效应量化、反事实检验 | 因果发现全流程 |
| **C3 知识本体** | JSON 代码本体（v2.1）+ 外部知识扩展 | 变量名 | 变量含义、子系统、单位、物理关系 | 全流程（查询触发） |
| **C4 专家公差** | 每类事故 sev-1 准稳态段 μ±3σ（数据驱动）+ 外部公差覆盖 | 变量时序 | 异常状态（正常/异常/严重异常） | RCA 异常判定阶段 |
| **C5 LLM 能力** | DeepSeek V4.1-Flash | 因果图+异常+本体序列化 prompt | 自然语言根因解释 | 报告生成阶段 |
| **C6 模板解释** | 结构化 RCA 报告模板 | 排序结果+边列表 | 可校验的结构化报告 | LLM 降级或补充 |

**组件 × 通道 MECE 映射矩阵**（消除职责重叠）：

| 组件 \ 通道 | K1 约束 | K2 数值 | K3 机制 | 说明 |
|------------|:-------:|:-------:|:-------:|------|
| C1 物理领域知识 | ✅ | | ✅ | K1提供禁止/允许边；K3提供机制解释 |
| C2 结构知识 | | ✅ | | 总效应矩阵→K2量级；反事实→K2一致性 |
| C3 知识本体 | ✅ | | | 变量子系统/物理关系→K1约束 |
| C4 专家公差 | | ✅ | | μ±3σ→K2异常判定 |
| C5 LLM | | | ✅ | 降频：仅类级 3 份 + 全局 1 份；模板级固定 rule（V23-DESIGN-020） |
| C6 模板 | | | ✅ | 结构化模板→K3兜底 |

> 每个组件只在其主通道起作用，避免多通道重复使用同一信息。C2 是数据驱动产物，归入 K2 数值通道（量级/一致性）。

### 5.5 三通道融合架构（对应 v3 §9）

```
┌─────────────────────────────────────────────────────────┐
│                    因果发现结果（数据驱动）                │
│  稳定边 + 总效应矩阵 + 根因排名 + 传导路径                 │
└──────────────┬──────────────────┬───────────────────────┘
               │                  │
    ┌──────────▼──────┐  ┌────────▼────────┐  ┌─────────────▼────┐
    │  K1 约束通道     │  │  K2 数值通道     │  │  K3 机制通道      │
    │  (C1+C3 本体)    │  │  (C4 公差+物理约束)│ │  (C5 LLM+C6 模板) │
    │                  │  │                  │  │                   │
    │ • 禁止边删除留痕 │  │ • μ±3σ 异常判定  │  │ • 因果图提示注入  │
    │ • 允许边贴标签   │  │ • K2残差三态判定 │  │ • 忠实性校验      │
    │ • 子系统覆盖检查 │  │ • 异常程度量化   │  │ • 方向不确定性标注 │
    │ • 因果链完整性   │  │ • 持续时间统计   │  │ • 三级降级        │
    └──────────────────┘  └──────────────────┘  └───────────────────┘
               │                  │                      │
               └──────────────────┼──────────────────────┘
                                  ▼
                    ┌───────────────────────────┐
                    │   融合输出：RCA 报告       │
                    │  • 根因排名（带证据向量）   │
                    │  • 传导路径（带物理标签）   │
                    │  • 异常状态（带阈值依据）   │
                    │  • 自然语言解释（带校验）   │
                    └───────────────────────────┘
```

### 5.6 各组件交互触发条件

| 交互 | 触发条件 | 数据流 |
|------|---------|--------|
| C3→C1 | 变量选取阶段3 | 本体提供子系统映射，C1 执行覆盖检查 |
| C1→因果发现 | 因果图生成后 | 禁止边删除留痕，允许边贴"本体支持"标签 |
| C4→RCA | 根因排名计算后 | 异常状态加权（探索性消融，不做主排序） |
| K2→因果图 | 因果图生成后 | 残差三态判定，违反约束的边标"physcons_violation" |
| C2+C3+C4→C5 | 报告生成时 | 总效应矩阵+本体+异常序列化为 prompt 注入 LLM |
| C5→C6 | LLM 调用失败/忠实性校验不通过 | 自动降级到模板解释 |
| C6→C5 | 模板解释后可选 | 仅汇总解释可再走 LLM；模板级不再调用（V23-DESIGN-020） |

### 5.7 层级融合方式

**第一层：硬约束（必须遵守）**
- C1 禁止边：与已知物理矛盾的边必须删除并留痕
- C3 子系统覆盖：变量集必须覆盖所有相关子系统
- C4 阈值逻辑：故障指示器（marker）=0 为正常，非零为异常（不使用 μ±3σ）
- K2 守恒关系：严重违反（残差>5σ）的边标记但不删除（可能遗漏项导致）

**第二层：软约束（增强可信度）**
- C1 允许边标签：数据学到的边若本体支持，贴"物理一致"标签
- C4 异常加权：根因排名可选择异常程度加权（消融项）
- C2 稳定性分级：f_e≥0.9 标"高置信"，0.6≤f_e<0.9 标"中等"
- K2 残差判定：违反约束的边标"physcons_violation"，作为负证据

**第三层：解释增强（不影响数据结论）**
- 模板级解释：固定 rule 结构化报告（9 份），**不逐模板调用 LLM**（V23-DESIGN-020 降频）
- C5 LLM 解释：仅用于每类事故 1 份类级汇总 + 1 份全局结论（共 4 份），带同 run_id + 输入哈希缓存
- C6 模板兜底：LLM 失败/无 key 时自动降级为 rule 汇总，流程不中断
- 所有解释经过忠实性校验（正则匹配变量名/数值/模板 id，必须能在产物中找到）

### 5.8 Algorithm 3 反事实验证完整实现逻辑

#### 5.8.1 论文定义（阶段3笔记）

对每条因果边 A→B：
1. 用总因果效应 T[A,B] 预测"如果 A 变化 ΔA，B 会变化多少"：ΔB_pred = T[A,B] × ΔA
2. 在留出集（held-out data）中观测 A 变化时 B 的实际变化 ΔB_obs
3. 比较 ΔB_pred 与 ΔB_obs，验证因果效应是否可靠

#### 5.8.2 现有实现（`cf_validation.py::counterfactual_validation`）

```
输入：
  x: (n,) 训练轨迹主窗 marker z-score 中位数（干预变量）
  Y: (n, J) 训练轨迹主窗症状 z-score 中位数（响应变量）
  symptoms: V_thr 症状变量序列
  cL: 冻结签名线性效应 {var: cL}（不重学）

适用性门（任一不满足则 cf_not_applicable）：
  门1：动态范围 dr = Q3 - Q1 ≥ CF_DYNAMIC_RANGE_MIN
  门2：轨迹数 n ≥ 2 × CF_MIN_GROUP_SIZE
  门3：基线组（x ≤ median）与干预组（x > median）各 ≥ CF_MIN_GROUP_SIZE

核心步骤：
  1. 5 折确定性划分（rng.permutation 后均分）—— **仅对 Δobs 留出，不重学 B/cL**
  2. 逐折计算观测差值 Δobs_fj = median(Y_int, j) - median(Y_base, j)
  3. 双引擎预测（cL 来自冻结签名，不重学）：
     - LiNGAM 线性：Δpred_L = (Q3 - Q1) × cL[v]
     - GAM-ANM：Δpred_A = gam_two_point_delta(x, Y[:,j], Q1, Q3)
  4. inconclusive 判定：|Δobs| < CF_INCONCLUSIVE_DELTA 且 |Δpred| < 阈值
  5. 方向一致率：逐折 sign(Δpred) == sign(Δobs) 的比例
  6. 量级 Spearman：Δpred 向量 vs Δobs 向量的秩相关（仅可判边，n≥3）

边级 gCF 判定（OR 逻辑）：
  - 合并一致率 < CF_FAIL_AGREEMENT（0.5）→ FAIL
  - 任一引擎量级 Spearman < 0 → FAIL
  - 否则 → PASS

gCF 权重（作用于 E3_L 线性效应，与 `inversion.py:55` 一致：wL = π_L · gCF）：
  - PASS → gCF = 1.0（不打折）
  - FAIL → gCF = 0.5（打折）
  - inconclusive/not_applicable → gCF = 1.0
  注：chains.py 仅记录 cf_fail 标记，不做乘性权重；实际乘性权重在 inversion.py 作用于 E3_L

模板级汇总：
  - n_fail / n_judgeable / n_inconclusive
  - gCF_template = PASS 或 FAIL
```

#### 5.8.3 与论文的对应与降级声明

| 论文 Algorithm 3 | 现有实现 | 差异与降级 |
|-----------------|---------|-----------|
| 干预实验 | 观测条件均值差（基线组 vs 干预组） | ⚠️ 观测差异≠干预效应，严重度梯度不构成准实验 |
| 留出集（held-out data） | 仅对 Δobs 做 5 折留出，**不重学 B/cL** | ⚠️ 论文留出集含模型重学，此处只留出观测、模型冻结，降级为观测一致性 |
| 总效应预测 | LiNGAM 线性 cL + GAM-ANM | ✅ 双引擎更稳健 |
| 验证标准 | 预测 vs 观测一致 | ✅ 方向一致率 + 量级 Spearman |
| 用途 | 验证因果边可靠性 | ⚠️ 降级为探索性一致性比较，不作成败标准 |

**降级声明**：由于 NPPAD 数据是观测性仿真数据（无真实干预），Algorithm 3 的结果只能解释为"模型预测与观测变化的一致性描述"，**不能**作为因果效应的严格验证。Δpred≈0 可能是竞争方向（如 P→WLR）的自然结果，不是验证失败。

#### 5.8.4 输出结构

```json
{
  "status": "cf_ok | cf_not_applicable",
  "dynamic_range": 0.5,
  "n_trajectories": 80,
  "edges": {
    "P": {
      "delta_obs": -0.32,
      "delta_pred_L": -0.28,
      "delta_pred_A": -0.31,
      "agree_L": 0.8,
      "agree_A": 0.9,
      "agree": 0.85,
      "gCF": "PASS",
      "flags": []
    }
  },
  "spearman": {"L": {"rho": 0.75, "p": 0.01, "n": 12}, "A": {...}},
  "gCF_template": "PASS",
  "n_fail": 0, "n_judgeable": 12, "n_inconclusive": 2
}
```

### 5.9 基准数据适配性审核与调整方案

#### 5.9.1 现有基准设定审核

> **口径更正（v2.3-final）**：下文"LOCA_1 准稳态段（t=520~2030s）作为全局基准"是 **v2.1 及更早**的实现，现已不是现状。现行实现为 `rca/v21/tolerance.py`：**每类事故各自的 sev-1 轨迹、固定取事故注入前 [0,100]s 共 11 点**估计 μ/σ（V23-DATA-004），不再做准稳态段搜索。准稳态段 [520,2030]s（152 点）现在只用于**数据审计**（Z 病态与变点基线，§4.4.1），两套窗口不得互相替代。本节保留的"审核"文字作为改造动因记录。

**历史设定（v2.1 及更早）**：使用 LOCA_1（LOCA 的 1% 严重度轨迹）的准稳态段（t=520~2030s）作为全局基准，计算所有变量的 μ±3σ 阈值。

**审核结论**：该基准**仅适用于 LOCA**，对其余 11 类事故存在适配性问题：

| 问题 | 说明 | 影响 |
|------|------|------|
| **事故机理不同** | LOCA 是一回路破口，稳态段反映一回路 100% 功率工况；SGATR 是 SG 传热管破裂，RW 是反应性引入，各事故的正常运行参数可能不同 | 用 LOCA 基准判其他事故的异常，可能误报或漏报 |
| **触发变量不同** | LOCA 的 marker 是 WLR，SGATR 是 WTRA，FLB 是 WFLB；各事故的故障指示器不同 | 全局基准无法区分"该事故的正常零值"与"其他事故的异常值" |
| **v3 原则冲突** | v3 §4.3 明确"regime map 必须按事故类独立"，"sev-1 只用于过程本体基线" | 全局 LOCA_1 基准违反"按事故类独立"原则 |
| **Normal 轨迹不可用** | 本地仅 1 条 Normal 轨迹，且是功率漂移（100%→40%），不适合做基准 | 无法用 Normal 做统一基准 |

#### 5.9.2 调整方案：每类事故 sev-1 独立基准

**依据**：v3 §4.3 "sev-1 只用于过程本体基线 μ/σ 估计" + "regime map 必须按事故类独立"。

**方案**：每类事故使用自己的 sev-1（1% 严重度）轨迹的准稳态段作为该事故的基准数据。

```
对每类事故 k ∈ {12类}：
    │
    ▼
Step 1：加载 {k}_1.csv（该事故 1% 严重度轨迹）
    │
    ▼
Step 2：确定基线窗口（现行口径，V23-DATA-004）
    - 固定取事故注入前 [0,100]s（10 s/步 → 11 个采样点），**不做准稳态段搜索**
    - 理由：准稳态段搜索会引入"按结果挑窗"的自由度，且三类事故的恢复段形态差异大；
      固定注入前窗口与物理含义（初始满功率稳态）一致，可复现、可审计
    - 若 sev-1 轨迹缺失或基线段点数 < 3，按 Step 6 回退
    │
    ▼
Step 3：变量筛选
    - 排除故障指示器（marker）：正常应为 0，用"非零即异常"规则
    - 排除安全系统变量（WHPI/WECS 等）：正常应为 0，用"非零即异常"规则
    - 排除 σ < 1e-6 的常量变量
    - 标记 σ/|μ| < 1% 的变量为"潜在Z病态变量"（见 §4.4.1）
    │
    ▼
Step 4：计算 μ±3σ 阈值（含稳健 σ 处理，防 Z 病态）
    - 对剩余变量，在准稳态段计算 μ
    - **稳健 σ**：σ_robust = max(1.4826·MAD, σ_floor(v))
      其中 σ_floor(v) 为逐变量物理下限（与 §4.4.1 一致），无配置时回退 1%·|μ|
      （依据：v2.1 实验解读 O3，防止 σ≈0 导致 z 病态）
    - **z 截断**：异常判定用 |X(t) − μ| / σ_robust，并对 z 做 clip(−10, 10)
    - 异常判定：|X(t) − μ| > 3·σ_robust
    - 对饱和变量（WFWA/WSTA/WFLB 等）：改用相对变化率 ΔX/|μ| 或状态特征
    │
    ▼
Step 5：基准质量校验（现行实现，落 `quality` 字段）
    - baseline_points ≥ 3（实测三类均为 11）
    - 常量列计数 n_constant_pre_inject 单独记录，不阻断（实测 LOCA 2，SGATR/FLB 各 0）
    - Z 病态通道计数 z_pathology_count 单独记录，不阻断（实测 LOCA 1，SGATR/FLB 各 0）
    - 通过则输出 tolerance_{k}.json
    │
    ▼
Step 6：回退策略（若校验不通过）
    - 回退1：使用该事故最低可用严重度（sev-2）的注入前 [0,100]s
    - 回退2：使用 Normal 轨迹（声明降级，仅作 sanity check，**不并入正式公差**）
    - 回退3：标注该类"基准不可用"，跳过该类并记录，不阻断其他类
```

#### 5.9.3 12 类事故基准适配性预估

| 事故 | sev-1 轨迹 | 基线窗口 | 适配性 | 备注 |
|------|-----------|----------|--------|------|
| LOCA | LOCA_1 | 注入前 [0,100]s（11 点） | ✅ 已落地 | 常量列 2、Z 病态 1 |
| LOCAC | LOCAC_1 | 注入前 [0,100]s | ⚠️ 仅 5 条轨迹 | 描述性，不作验收 |
| SLBIC | SLBIC_1 | 注入前 [0,100]s | ❌ 无数据 | 待补数据 |
| SLBOC | SLBOC_1 | 注入前 [0,100]s | ❌ 无数据 | 待补数据 |
| SGATR | SGATR_1 | 注入前 [0,100]s（11 点） | ✅ 已落地 | 常量列 0、Z 病态 0 |
| SGBTR | SGBTR_1 | 注入前 [0,100]s | ❌ 无数据 | 待补数据 |
| RW | RW_1 | 注入前 [0,100]s | ❌ 无数据 | 待补数据 |
| RI | RI_1 | 注入前 [0,100]s | ❌ 无数据 | 待补数据 |
| FLB | FLB_1 | 注入前 [0,100]s（11 点） | ✅ 已落地 | 常量列 0、Z 病态 0 |
| MD | MD_1 | 注入前 [0,100]s | ❌ 无数据 | 待补数据 |
| LR | LR_1 | 注入前 [0,100]s | ❌ 无数据 | 待补数据 |
| LLB | LLB_1 | 注入前 [0,100]s | ❌ 无数据 | 待补数据 |

**结论（按事实）**：12 类中仅 3 类（LOCA/SGATR/FLB）有 sev-1 轨迹并已生成公差；LOCAC 仅 5 条轨迹、只能作描述性；其余 8 类**本地无数据**，不得用预估代替实测。

#### 5.9.4 统一基准数据清单

```
output/rca_v23/tolerances/
├── tolerance_LOCA.json      # LOCA_1 注入前 [0,100]s（11点，V23-DATA-004）
├── tolerance_LOCAC.json     # LOCAC_1 注入前 [0,100]s（无 sev-1 基线时回退）
├── tolerance_SLBIC.json     # SLBIC_1 或回退
├── tolerance_SLBOC.json     # SLBOC_1 或回退
├── tolerance_SGATR.json     # SGATR_1 注入前 [0,100]s
├── tolerance_SGBTR.json     # SGBTR_1 或回退
├── tolerance_RW.json        # RW_1 或回退
├── tolerance_RI.json        # RI_1 或回退
├── tolerance_FLB.json       # FLB_1 注入前 [0,100]s
├── tolerance_MD.json        # MD_1 或回退
├── tolerance_LR.json        # LR_1 准稳态段
└── tolerance_LLB.json       # LLB_1 准稳态段
```

每个 tolerance_{k}.json 包含：
```json
{
  "accident_type": "LOCA",
  "baseline_source": "LOCA_1",
  "baseline_segment": "t=520~2030s",
  "baseline_points": 152,
  "fallback_level": 0,
  "thresholds": {
    "P": {"mu": 155.5, "sigma": 0.72, "method": "mu_3sigma"},
    "TAVG": {"mu": 310.0, "sigma": 0.01, "method": "mu_3sigma"},
    "WLR": {"method": "nonzero_is_anomaly"},
    "WECS": {"method": "nonzero_is_anomaly"}
  }
}
```

### 5.10 数据偏离度计算与可视化（辅助信息层）

> ⚠️ **核心边界声明**：偏离度**仅用于表征数据与正常区间的偏移程度，不直接等同于故障根因**。本功能为辅助信息层，**不替代根因溯源**（总因果效应排序 T=(I-B)⁻¹），不改变根因判定逻辑。

#### 5.10.1 功能定位与必要性

**现状**：当前仅 `signature.py` 有 marker 的 `max_dev`，无全变量偏离度；严重度仅 low/mid/high 三档，粒度较粗。

**必要性**：有条件实现，作为辅助层补充：
- 弥补严重度三档粒度不足，提供连续异常程度度量
- 辅助理解传导路径上的异常分布
- 符合核电运维"参数偏离正常区间"的认知习惯

**不替代**：根因排名仍按总因果效应 T=(I-B)⁻¹（对效应变量总影响 DESC），偏离度仅着色和参考。

#### 5.10.2 偏离度定义

对变量 $v$ 在时刻 $t$ 的取值 $x_{v,t}$，基准为该变量 sev-1 准稳态段的 $\mu_v$ 和稳健 $\sigma_v$：

$$
d_{v,t} = \min\left(\frac{|x_{v,t} - \mu_v|}{\sigma_{v,robust}},\ 10\right)
$$

其中 $\sigma_{v,robust} = \max(1.4826 \cdot \text{MAD}_v,\ 1\% \cdot |\mu_v|)$（复用 §4.4.1 稳健σ，防Z病态）。

> **口径修订（2026-11-10，V23-DATA-004）**：原文“sev-1 准稳态段”实测为全轨迹滑窗（t=0–990），
> 注入后段被计入基线，σ 被系统性放大。现固定为**注入前 [0,100] s 共 11 点**（`t_inject=110` 读注册表），
> 准稳态段搜索加 `t < t_inject` 硬约束、只作追溯记录，不参与 μ/σ；Normal/1.csv 只作正常波动量级对照，不并入。
> 同时 $d$ 拆成两列：$d_{raw}$（不截断，仅用于排序）与 $d=\min(d_{raw},10)$（展示）。
> 影响：σ 回落到 σ_floor 主导后，绝对 $d$ 更饱和（实测 88 个变量中 absolute severe 52–69 个），
> 因此分级必须同时改为 §5.10.4 的“模板内相对分位”，两者不可分开上线。

**边界声明（前端4处强制展示）**：
1. 因果图页面顶部固定横幅
2. Top5排行榜上方提示文字
3. 变量详情弹窗提示图标
4. 导出报告免责声明

> ⚠️ 偏离度 ≠ 故障根因。高偏离度可能是根因、下游受影响变量、或正常操作瞬态。根因判定请参考总因果效应根因排名（T=(I-B)⁻¹）。

#### 5.10.3 基准方案：复用 sev-1 独立基准

**不采用全量建模或分位采样**，理由：
- 全量数据含故障态，会拉宽正常区间，偏离度失效
- 分位采样（sev≥10）已有异常，基准不纯
- sev-1 最接近正常态，与 §5.9 基准完全一致，零额外成本

同一类事故的所有严重度组共享同一个 sev-1 基准，使偏离度可跨组比较。

基准 JSON 扩展字段：
```json
{
  "thresholds": {
    "P": {"mu": 155.5, "sigma_robust": 0.72,
           "quantiles": {"p10": 154.8, "p20": 155.0, "p50": 155.5, "p80": 156.0, "p90": 156.2}}
  }
}
```

#### 5.10.4 偏离度分级与前端展示

**4级着色（2026-11-10 定稿：模板内相对分位，V23-DESIGN-011）**：

| 模板内分位（按 $d_{raw}$） | 等级 | 颜色 | 含义 |
|---------|------|------|------|
| ≤ p50 | 正常 | 🟢 绿 | 本模板内偏移偏小的一半 |
| p50–p70 | 中等 | 🟡 黄 | 本模板内中上 |
| p70–p85 | 较大 | 🟠 橙 | 本模板内前 15%–30% |
| > p85 | 严重 | 🔴 红 | 本模板内前 15%（>p95 另标“极端”） |

> **为什么改**：原方案按绝对 $d<1/3/5$ 分档，实测 8/9 模板 severe 占比 72%–88%（SGATR_low 仅 32%），
> 档位对 NPPAD 阶跃不具区分度。改用模板内相对分位后，档位天然约为 50/20/15/15，
> **只表达“同一模板内的相对偏移排序”，不能当异常强度绝对值**；因此保留 $d$ 与
> `d_abs_level`（旧 3/5/7σ 绝对档）作附注、单列报告，比例只作诊断。
> 该改动与 V23-DATA-004 联动：基线收窄 → σ 回落 → 绝对 $d$ 更饱和，二者必须同批生效（见 §5.10.2 修订说明）。

**前端呈现形式**：

1. **因果图节点增强**：节点背景按偏离度半透明着色，显示"d=X.X 🟡"，不改变拓扑位置
2. **Top5排行榜增加偏离度列**：排名仍按拓扑得分，偏离度仅作参考列
3. **传导路径标注**：`LVPZ(d=2.3🟡) → PSGA(d=4.5🟠) → WSTA(d=8.2🔴)`
4. **变量详情弹窗**：展示基准分布的 p10/p20/p50/p80/p90 分位，当前值落在哪个区间

**10%/20%分位单独展示**：核电参数往往有下限阈值，p10/p20 帮助判断"偏低但未到故障"的预警状态。

#### 5.10.5 饱和变量特判

对会归零/饱和的变量（WFWA、WSTA、WFLB 等），不参与数值偏离度计算，改为状态特征：
- 正常态：显示"未触发"
- 故障态：显示"已触发"（marker 非零）

#### 5.10.6 实现路径（4阶段）

| 阶段 | 内容 | 产出 |
|------|------|------|
| 1. 基准计算 | 复用 §5.9 基准，扩展 quantiles 字段 | `tolerance_{k}.json` 含分位数 |
| 2. 后端 | 新增 `deviation.py`：d = min(\|x-μ\|/σ_robust, 10) | 36份 `deviation_summary_{k}_{g}.json` |
| 3. 前端 | 扩展 `graph_visualization.py`：节点着色+排行榜列+分位弹窗 | 扩展后的 HTML |
| 4. 联调 | Z病态校验、合理性抽检、边界声明检查 | 测试报告 |

**回退**：配置开关 `ENABLE_DEVIATION=false` 关闭，回退到 V2.2 纯拓扑可视化。

### 5.11 可量化提升目标

> **事故覆盖目标**：12/12 类以 Go-3 条件满足为前提；若 No-Go 降级则为 3/3 类。
> **知识融合定位**：v2.3 为**工程布线层**（C3 本体 + C4 公差落地，C1/C5/C6 逐步接入），**不声称知识有效性提升**；有效性验证留待后续版本 A/B 对照。

| 提升维度 | 基线（v2.2） | v2.3 目标 | 度量方式 |
|---------|-------------|----------|---------|
| 变量选取客观性 | 硬编码（0量化） | 四阶段评分（100%量化） | 选取报告完整度 |
| 事故覆盖 | 3/12 类 | 12/12 类（Go-3 前提） | 产物覆盖率 |
| 解释忠实性 | 模板固定 | LLM+忠实性校验 | 校验通过率 ≥ 95% |
| 知识约束工程化 | 无 | 本体约束+禁止边工程落地（不声称有效性） | 禁止边违规数 = 0 |
| 异常判定基准 | 仅 LOCA_1 | 每类独立 sev-1 基准（注入前 [0,100]s） | 基准覆盖率 |
| Algorithm 3 完整度 | 已有实现 | 补充论文对应逻辑+降级声明 | 输出结构完整率 |

### 5.12 事故内变量级评测（第 10 步，D11；V23-DESIGN-036）

> 定位：v2.3 **不做事故大类匹配**，只做「某个事故内的变量级根因溯源」。因此评测也是变量级：
> 对每条留出轨迹，把该模板的**根因候选变量排序**与**该事故类型的始发（注入）变量真值集**比对。
> **不沿用 v2.1 的"事故类型匹配 + 签名反演"范式**：不做事故类型分类、不把 9 个模板当检索池、
> 不用关键响应变量 / forced 保护集作判据（"Top-k 覆盖关键响应"属自证循环代理，已弃用），
> 也不允许出现"事故类型分类准确率"这类 v2.3 根本不做的任务。

**真值（唯一来源）**：`Code/causalrca/config/injection_truth.py::INJECTION_TRUTH`，
逐条对齐老师项目 `故障溯源演示平台/nppad_rca_experiment/rca_experiment.py:34-47 GROUND_TRUTH`
（按 NPPAD 事故表/变量表人工整理的直接始发变量，非穷尽物理根因）。该表**只用于打分**，
禁止参与选变量 / 建图 / 排序 / 解释；未登记类直接 `KeyError`，不许回退成
`key_responses` / `trigger_vars` / forced 保护集。

| 类 | 始发变量真值 | 备注 |
|----|--------------|------|
| LOCA | WLR, WBK, MBK | WBK/MBK 为可疑项（破口流量积分量） |
| LOCAC | WLR, WBK, MBK | 同上；数据 partial |
| SGATR | WTRA | |
| SGBTR | WTRB | 数据缺失 |
| FLB | WFLB | |
| LLB | WLD | 数据缺失 |
| LR | TBLD | 数据缺失 |
| RI | RHRD, RH | 数据缺失 |
| RW | RHRD, RH | 数据缺失 |
| MD | PPM, RHBR, RH | 数据缺失 |
| LOF | WRCA, WRCB, RRCA, RRCB, RRCO | 数据缺失 |
| TT | TBLD | 数据缺失 |

口径边界（必须与数字一起引用）：
- NPPAD 数据只给「类目录 + 文件序号（=严重度）」，**没有逐条注入变量字段** →
  真值粒度是 `class_level`（`ground_truth.scope=class_level`）：同类全部轨迹共用该类的始发变量集。
  因此指标回答的是「**该类的根因变量有没有被排到前面**」，不是「这一条轨迹的注入变量有没有被排到前面」。
- 只剔除「数据列中不存在的变量」；真值是否落在候选集（模板 15 节点）内单列 `gt_in_candidate_set`，
  用于区分"没找对"与"根本不在候选里"。
- LOCA/LOCAC 的 `WBK`/`MBK` 属可疑项（老师项目亦标注），仍计入真值集并单列 `ground_truth.suspect`。

**数据与无泄漏**
- 留出集只取 `registry/split_manifest.json` 的 `test_severities`：每类 20 条（按组 7/7/6），三类共 60 条；
  `train_template_severities` 一律不进评测（与老师"训练/评测文件重叠"的缺陷相反：我们严格互斥）。
- 打分只读本 run 已落盘产物：`tracing/topology_{K}_{g}.json`、`tracing/root_candidates_{K}_{g}.json`、
  `signatures/signature_{K}_{g}.json`、`registry/`（t_inject / main_window）；**不重算 bootstrap、不重新学图**。
- 轨迹只用于计算与类别无关的偏移强度 d(v)=max|x−μ_pre|/max(1.4826·MAD_pre, σ_floor(v))
  （μ_pre/MAD_pre 取注入前 [0, t_inject−10]s；σ_floor 取 `external_knowledge.sigma_floor_for`）。

**四通道同表（偏差基线按配对变量集拆两列，禁止跨配对比较）**
| 通道 | 候选集 | 定义 | 与谁配对 |
|------|--------|------|----------|
| `causal` | 14 节点（排除被遮蔽的注入点） | 生产口径：T=(I−B)⁻¹ 的 \|T\| 行和 DESC → in_degree ASC → weighted_reach DESC → reach_size DESC → consensus_rate DESC，有传导者优先、孤立节点殿后 | `deviation_production_set` |
| `deviation_production_set` | 14 节点 | 同集等权，只按 d(v) DESC（不用因果图） | `causal` |
| `causal_unmasked_for_eval` | 15 节点（含注入点） | 评测模式：注入点放回候选，其余同生产口径；用已落盘 `stable_edges` 重算 T=(I−B)⁻¹（纯矩阵，不改生产输出） | `deviation_equal_weight` |
| `deviation_equal_weight` | 15 节点 | 同集等权，只按 d(v) DESC | `causal_unmasked_for_eval` |

**指标**（cutoff=3，与老师 `metrics_for_ranking` 同式）：Hit@1 / Hit@3 / MRR / P@2 / P@3 /
Recall@3 / AP@3 / Jaccard@3；按模板 (K,g) / 按类（n=20）/ 全局（n=60）三层汇总，
Wilson 95% 只给 Hit@1/Hit@3。落 `metrics/metrics_{K}_{g}.json`（逐例明细 + 模板汇总 + 遮蔽披露）
与 `metrics/metrics_summary.json`（三层汇总 + 真值表 + `comparability_with_teacher`）。

**结果（run `v23-20261110T150726`，60 条留出 × 9 模板；Hit@1 / Hit@3 / MRR）**
| 类 | n | causal（生产，14） | 偏差基线（生产，14） | 去遮蔽（15） | 偏差基线（15） |
|----|---|--------------------|----------------------|--------------|----------------|
| LOCA | 20 | 0 / 0 / 0 | 0 / 0 / 0 | **0.300 / 0.300 / 0.350** | 0 / 0 / 0.174 |
| SGATR | 20 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0.073 | 0 / 0 / 0.175 |
| FLB | 20 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0.191 | 0 / 0 / 0.174 |
| 全局 | 60 | 0 / 0 / 0 | 0 / 0 / 0 | **0.100 / 0.100 / 0.205** | 0 / 0 / 0.175 |

（全局去遮蔽 Hit@1 的 Wilson 95% = [0.0466, 0.2015]；生产通道 [0, 0.0602]）

**B_CLIP 敏感性（四列并列，V23-DESIGN-038；不得只报 clip5）**

总效应 = |T| 行和、`T=(I−B)⁻¹`，`B_CLIP` 硬截断会改变名次，故变量级命中必须四列并列：

| 层级 | clip3 | clip5（生产默认） | clip10 | clip_off |
|------|-------|------------------|--------|----------|
| 全局 Hit@1 | 0.100 | 0.100 | 0.100 | **0.000** |
| 全局 Hit@3 | **0.200** | 0.100 | 0.100 | **0.200** |
| 全局 MRR | 0.234 | 0.205 | 0.199 | 0.141 |
| 全局 AP@3 | 0.083 | 0.033 | 0.033 | 0.044 |
| LOCA Hit@1/Hit@3/MRR | .300/.300/.350 | .300/.300/.350 | .300/.300/.350 | **.000**/.300/.150 |
| SGATR Hit@1/Hit@3/MRR | 0/0/.071 | 0/0/.073 | 0/0/.073 | 0/0/.073 |
| FLB Hit@1/Hit@3/MRR | 0/**.300**/.281 | 0/0/.191 | 0/0/.174 | 0/**.300**/.200 |

逐模板（Hit@1/Hit@3/MRR；括号内为注入点名次）：

| 模板 | clip3 | clip5 | clip10 | clip_off |
|------|-------|-------|--------|----------|
| LOCA_low | 0/0/.071 (14) | 同左 | 同左 | 同左 |
| LOCA_mid | 0/0/.071 (14) | 同左 | 同左 | 同左 |
| **LOCA_high** | 1.0/1.0/1.0 (1) | 1.0/1.0/1.0 (1) | 1.0/1.0/1.0 (1) | **0/1.0/.333 (3)** |
| SGATR_low | 0/0/.071 (14) | 0/0/.077 (13) | 同 clip5 | 同 clip5 |
| SGATR_mid / high | 0/0/.071 (14) | 同左 | 同左 | 同左 |
| FLB_low | 0/0/.125 (8) | 同左 | 同左 | 0/0/.143 (7) |
| FLB_mid | 0/0/.250 (4) | 同左 | 0/0/.200 (5) | 0/0/.143 (7) |
| **FLB_high** | 0/**1.0**/.500 (2) | 0/0/.200 (**5**) | 0/0/.200 (5) | 0/**1.0**/.333 (3) |

**翻转模板（`flips.templates` 自动标出）**：`LOCA_high`（clip5 第 1 → clip_off 第 3）、`FLB_high`
（clip3 第 2 → clip5 第 5 → clip_off 第 3，非单调）、`FLB_mid`（第 4 → 第 5 → 第 7）、`FLB_low`、`SGATR_low`。

**结论**：变量级命中在 clip 轴上不是稳定量——Hit@1 取值 {0, 0.10}、Hit@3 取值 {0.10, 0.20}，
且**唯一的 1.0 命中（LOCA_high）在去截断后归零**。因此它只能作**可辨识性证据**，
不能作"准确率"主张。

| 模板 | n | 去遮蔽 Hit@1 / Hit@3 / MRR | 偏差基线（15）MRR |
|------|---|----------------------------|-------------------|
| LOCA_low | 7 | 0 / 0 / 0.071 | 0.195 |
| LOCA_mid | 7 | 0 / 0 / 0.071 | 0.188 |
| LOCA_high | 6 | **1.000 / 1.000 / 1.000** | 0.134 |
| SGATR_low | 7 | 0 / 0 / 0.077 | 0.192 |
| SGATR_mid | 7 | 0 / 0 / 0.071 | 0.217 |
| SGATR_high | 6 | 0 / 0 / 0.071 | 0.107 |
| FLB_low | 7 | 0 / 0 / 0.125 | 0.135 |
| FLB_mid | 7 | 0 / 0 / 0.250 | 0.167 |
| FLB_high | 6 | 0 / 0 / 0.200 | 0.228 |

**怎么读**
- `causal` 全 0 是**结构性**的，不是"找错了"：注入点 `marker` 在生产链路被「故障指示器遮蔽」
  （`total_effect=None`、`root_candidates.rank=null`），永不进候选；同集的 `deviation_production_set`
  也全 0，说明**在任何方法下生产候选集都无法命中注入变量**——生产榜单回答的是"注入点之外的内部根因源"。
- 去遮蔽列：**clip5 下** LOCA_high 6/6 全中（Hit@1=1.000）；但该命中随截断翻转——
  **`clip_off` 下 WLR 降为第 3、Hit@1 归零**（WLR 48.86 vs WHPI 213.5 / LVPZ 55.4）。
  原来"LOCA_high 1.000"的表述自此**回收**，只能写作"clip5 下 1.000、去截断下第 3"。
  LOCA_low/mid 的 WLR 在任何 clip 下都无出边（名次恒 14）；SGATR 的 WTRA 出度为 0 →
  结构性不可达（名次 13–14）；FLB 的 WFLB 名次在 clip3/5/10/off 下为 2/5/5/3（见四列表）。
- 一句话结论：**这组数字只部分回答"事故内变量级根因找对了没有"，且答案不稳定**——生产口径答不了
  （遮蔽且同集基线也为 0）；去遮蔽口径在 clip5 下 Hit@1=0.10、MRR=0.205，四列区间为
  Hit@1 ∈ {0, 0.10}、Hit@3 ∈ {0.10, 0.20}、MRR ∈ [0.141, 0.234]，故仅作可辨识性证据。

**与 v2.1 口径差异**：v2.1 `baseline_metrics.json` 是 3 类**分类准确率**（B0/B1/B2，FULL/MASKED）；
V23-DESIGN-035 那版"9 模板检索指标"（Hit@1 0.333→0.633 等）**作废，不得引用**。

**与老师项目 accuracy 的关系（不并排）**
同任务（事故内变量级 Top-k 根因命中），但**不同协议**：我们无人工先验降权（老师 `RESPONSE_PRIOR=0.30`）、
切分严格互斥（老师训练/评测文件重叠）、每 (类,组) 单独建图（老师 12 类合图）、另有遮蔽/去遮蔽两列，
且 LOCA 真值含 `MBK/WBK` 而本仓库候选集不含。老师 `results/metrics_summary.csv` 的
causaltrace_lingam Hit@1 0.4368 / Hit@3 0.5800（deviation 0.2322 / 0.5178，1012 例）**只能作脚注**，
不得与其并排放进同一张表（`metrics_summary.json.comparability_with_teacher` 固化为
`same_task=true / comparable_numbers=false`）；要同表必须同一协议复算并说明取舍。

### 5.13 遮蔽口径决策：维持遮蔽（方案 B，2026-11-10 已裁决）

**决策**：维持遮蔽，不动 `source_tracing.trace_roots`（`if v == marker: continue` 原样保留），
`root_candidates.marker_info.rank` 继续为 `null`，生产列 `causal` 仍为 14 节点。
**去遮蔽列（`causal_unmasked_for_eval`，15 节点）仅作可辨识性诊断：不进主表、不参与任何准确率主张。**

**背景事实**（run `v23-20261110T150726`）：注入点即各模板 marker，被 `trace_roots` 从候选剔除，
故 `nodes[marker].total_effect=null`；生产口径变量级 Hit@1/Hit@3/MRR = 0/0/0，属候选集定义的
结构性零点，不是排序失败，也不是参数问题。

**表述边界（定稿）**
- 能写：v2.3 排序的是**注入点之外的内部根因源**（传导链关键节点）；注入点是外生边界条件——
  建图时以它为根（`n_marker_out_edges`/frontier 计入），排名时不出现在候选里。
- 不能写：变量级"命中注入变量"。该口径下命中恒为 0，不得作为准确率主张。
- 代价（每处须披露）：① 与老师项目 accuracy 不同任务（他的答案是注入变量本身）；② 0 易被误读为
  方法失败；③ "图用注入点、排名不用注入点"必须显式说明。

**已评估并否决的两个替代（只留否决理由，不实施）**
- **A 去遮蔽升为正式口径 —— 否决**：诊断列实测 Hit@1=0.10（6/60）、MRR=0.205，全部来自 LOCA_high
  6 条；且 WLR 名次随 `B_CLIP` 翻转（clip5 第 1、去截断第 3，top3 变为 WHPI/LVPZ/WLR）。命中可由
  一个超参翻转，撑不起"定位到注入变量"；marker 效应又取自同一张 B，属"答案解释答案"；改它会使
  "注入点不参与排名"的设计前提消失，A/B 表与写图口径需整体重写。
- **C 折中（marker 走单变量回归挂回） —— 否决**：回归挂边＝用被注入变量解释系统，必然显著，自证
  风险最高；且两类边权重不同源（LiNGAM vs OLS），`B_CLIP` 只对前者有意义，数值不可同尺度排序。

**定稿结论句（论文与报告统一使用）**
> v2.3 做的是注入点之外的内部根因源排序；变量级命中注入变量在此口径下结构性为 0，故不作为准确率
> 主张，只作为"候选集是否包含注入点"的诊断披露。

**与主指标的关系**：主指标为**模板级检索**（事故类 × 严重度组，§7.5 E 系列）；变量级只作诊断。
`B_CLIP` 四列（clip=3/5/10/off）在诊断附录内必须并列，任何单列数值不得单独出现（V23-DESIGN-038）。
模板级主指标的口径与数值**待重算**——035 的旧模板检索数字已作废（见 §7.5 与台账 035），本轮不引用。

### 5.14 两个结构性事实的定位（`WTRA` 出度 0、`WFLB` 第 5）

**WTRA 出度为 0（SGATR 三组均然）——已定位到"因果次序不可辨识"，非数据缺失/窗口/截断**
- 学图窗 `[60,1500]` 含注入点 `t_inject=110`；`WTRA` 窗内 std=0.44/7.46/14.03（low/mid/high），
  min=0、max=2.39/41.1/79.9 → **不是常量、不是没激励**。
- 本体规则只禁 `X→marker`（R3），`WTRA→X` 不在 `removed_forbidden_rates` 里 → **不是本体删边**。
- 100 次 bootstrap 中 `WTRA→X` 全部 `weight=0.0000、f_e=0%、dir_e=0%、count=0` → **不是 B_CLIP 截断**
  （截断只作用于 |w|>5）。
- 反证方向：每次 bootstrap 都估出 `X→WTRA`（rate=1.0）并被 R3 删除 → 估计器把 `WTRA` 排在因果序**末尾**
  （sink），出边因此恒为 0。严重度梯度一致：`LOCA` 的 `WLR` 在 low/mid（std 16.2/209）同样 0 条出边，
  high（std 321）才有 12 条 → 注入信噪比抬升才翻转次序。
- **未定位项**：无法量化"注入幅度 vs 噪声"的比值（注入前 `std=0`、`MAD_pre=0`，只能给 d 值），
  故"幅度太小"只能算与梯度一致的推断，未定位到具体阈值。

**WFLB 排第 5（FLB_high）——一半真实偏弱，一半截断天花板**
- 稳定出边 4 条、权重小（+0.743/−0.469/−0.110/+0.020）；`|T|` 行和 4.160。
- 同模板前四名 `WRCA 5.705 / WCHG 5.121 / WUP 5.001 / PSGA 4.997` 几乎卡在 `B_CLIP=5` 的天花板，
  `WFLB` 恰在其下 → 名次含截断伪影。
- 去截断（clip=1e9）复算：`WRCA 16542 / WCHG 11495 / WFLB 5063` → **升到第 3**；`LOCA_high` 反向变化见 §5.13-A。
  四列名次：**clip3=第 2、clip5=第 5、clip10=第 5、clip_off=第 3（非单调）**。故"第 5"是
  "真实效应弱于 WRCA/WCHG"+"B_CLIP 天花板"的叠加，不能只归因其一；也**不是"调大 clip 就能修好"**
  ——clip10 仍为第 5，说明天花板与真实强度在两处同时起作用。

### 5.15 受控实验：排序量的边际贡献与"孤立节点殿后"的代价（V23-DESIGN-039）

- 产物：`Code/output/experiments/ranking_diagnosis_v23-20261110T150726/`（`REPORT.md`/`results.json`/`diag2.py`，
  只读实验：未改 `causalrca/`、未写 `output/runs/`、未重算 bootstrap；**不属主链产物**）。
- 设置：固定 3 类 × 9 模板 × 60 条互斥留出 × **15 个去遮蔽候选变量**，只换排序量与后处理。
  排序量 7 种：V1 现行总效应；V2a 老师式 `0.80·direct+0.25·out−0.10·in`（无先验）；
  V2b 同 V2a × `role_prior`；V2c 同 V2b 但改用老师式 T（行和 0.95、`inv−I`、无截断）；
  V3 仅偏差 `d`；V4 总效应/偏差名次融合；V5 归一化总效应 × 归一化偏差。
  后处理 2 种：`nogroup`（纯分数）/ `grouped`（生产 `_production_order`，孤立殿后）。
- 一致性校验：`V1|grouped` 复现生产通道 0.1000 / 0.1000 / 0.2049 / 0.0333，逐位一致。

**结论 1（变量级 Top-1 不可辨识）**：14 组（7 排序量 × 2 后处理）里 Hit@1 上限 **0.1000**，唯一
非零来自 V1 + LOCA_high 那 6 条；其余 13 组 ≤ 0.0833。**没有任何排序量能把 Hit@1 抬到 0.3+**。

**结论 2（Hit@3 的提升是名单红利，不是因果能力）**：V1 0.3167 → V2c **0.7000**、MRR 0.2838 →
0.3561、Recall@3 0.1722 → 0.4889；但撤掉 `RESPONSE_VARS` 的 0.30 先验后（V2a）**Hit@3 归零**，
而该先验覆盖 15 个候选中的 **10 个（66.7%，9/9 模板一致）**。真值最好名次只到**第 2**
（LOCA_low/LOCA_mid），**永不进第 1**。→ 增幅全部来自人工名单把 2/3 候选压到 0.3 倍，不能写进方法主张。

**结论 3（"孤立节点殿后"是已知代价，不是待调参项）**：同一排序量 V2c 由 `nogroup` 0.7000 掉到
`grouped` 0.3167，原因是 **7/9 个模板的真值变量 `out_degree=0`**，被"有传导者优先、孤立垫后"
整段推到末尾。该后处理是生产排序的既定组成（保证传导链优先），本轮**不改**，代价登记为已知限制。

**一句结论**：变量级 Top-1 的 0 与差距，主因是**候选集与可辨识性**（候选是否含注入点、注入点在
偏离度谱上的相对位置），不是排序公式。

### 5.16 论文可写 / 不可写清单（变量级命中，定稿）

**A. 能写的最强主张（三句，分层，不得合并成一句）**
1. 主指标层（模板级检索）：按 §5.13 口径写，**数值待重算**，不得沿用 035 旧值。
2. 变量级诊断层（唯一推荐句式）：
   > 在 15 变量去遮蔽候选集、`B_CLIP=5` 下，变量级 Hit@1 上限 0.1000（6/60，Wilson 95% [0.047, 0.202]）、
   > Hit@3 0.1000、MRR 0.2049，6 条命中全部来自 LOCA_high；把排序量换成老师式 + 先验 + 老师 T
   > 只把 Hit@3 抬到 0.7000（撤掉先验即归零），真值最好名次第 2、永不进第 1——该口径下 Top-1 不可辨识。
3. 结构性层：
   > 生产口径 0/0/0 是候选集定义的必然（注入点即 marker，按设计不参与排名），不是排序失败；
   > 与老师同 3 类的 0.3199 之间的差距，主因是候选集是否包含注入点，而非排序公式。

**B. 必须同时披露（与主表同章，不得塞脚注）**
1. `B_CLIP` 四列（Hit@1 0.10/0.10/0.10/0.00、Hit@3 0.20/0.10/0.10/0.20、MRR 0.234/0.205/0.199/0.141）
   与 5 个翻转模板；"1.000"只能写成"clip=5 下 1.000、去截断下第 3"。
2. 生产口径 0 及其原因（候选集不含注入点）——与诊断列**不同口径**，不得互相证明或反驳。
3. 真值口径：class-level 始发变量集；LOCA 的 3 个真值只有 `WLR` 在 15 节点内，Recall@3 上限 1/3。
4. 命中集中度：6/60 全部来自 LOCA_high；LOCA_low/mid、SGATR 三组、FLB 三组均为 0。
5. 受控实验三条（§5.15）：Hit@1 上限 0.1、Hit@3 提升＝先验红利、孤立殿后代价 0.7000→0.3167。
6. 小样本：n=60，Wilson 下界 0.047，未做显著性检验。
7. 结构性限制：`WTRA` 因果次序不可辨识（估计器排其为 sink）、`WFLB` 真实偏弱 + 截断天花板（§5.14）。
8. **偏差基线两列暂停引用**（V23-CODE-029：σ 下限失效使 `d` 爆到 1e4–1e5），修复前不得作任何对照。

**C. 绝对不能进摘要**
1. 「LOCA_high 变量级 Hit@1 = 1.000」（不带 clip 限定语）。
2. 单列 clip=5 的"总体 Hit@1 = 10% / MRR = 0.205"当作性能结论。
3. `causal`(0) 与 `causal_unmasked`(0.10) 并排读作"方法能力"。
4. 0.7000 或"0.3167→0.7000"当作本文方法的改进（含人工先验，撤掉归零）。
5. 与老师 0.4368 / 0.3199 的任何并排、比值、"差距 x%"。
6. AP@3 = 0.0333 单列（clip3 下为 0.0833）。
7. 任何偏差基线数字（σ 缺陷未修，V23-CODE-029）。
8. "变量级 Top-1 达 10%"这类不带模板/截断限定的聚合短语。

**D. 一句话判断**
> 这块能撑的是"候选集决定可辨识性 + 诊断必须报四列"这条方法论主张；撑不起任何"变量级准确率"主张。
> 任何把 0.10 / 1.000 / 0.7000 当性能数字的写法，都会被 clip 翻转或"先验撤掉即归零"当场击穿。

---

## 6. 实现路径与代码模块

### 6.1 分步实现路径（4 个阶段）

#### 阶段 A：变量选取机制落地（核心，约 1 周）

| 步骤 | 内容 | 产出 |
|------|------|------|
| A1 | 新建 `variable_selector.py`：实现 §3 四阶段漏斗 | 模块代码 |
| A2 | 扩展 `ENTITY_TABLE`：补充 12 类事故的子系统映射、触发变量、关键响应变量 | 本体扩展 |
| A3 | 新建 `accident_config.py`：12 类事故配置表（触发变量、时间窗口、子系统列表） | 配置文件 |
| A4 | 在 3 类已有事故上验证：自动选取 vs v2.1 硬编码的一致性 | 验证报告 |

**关键文件**：
- `Code/rca/v21/variable_selector.py`（新建）
- `Code/rca/v21/accident_config.py`（新建）
- `Code/rca/v21/knowledge.py`（扩展 ENTITY_TABLE，原 ontology.py 实际文件名为 knowledge.py）

#### 阶段 B：12类事故数据适配（约 1 周）

| 步骤 | 内容 | 产出 |
|------|------|------|
| B1 | 下载/确认 12 类事故 CSV 数据路径 | 数据清单 |
| B2 | 扩展 `data.py`：支持 12 类事故的轨迹级加载 | 数据加载模块 |
| B3 | 为每类事故生成变量集（运行 variable_selector） | 12 份 variable_sets.json |
| B4 | 数据审计：常量列、轨迹数、时间窗口校验 | 审计报告 |
| B5 | **共享基线 Z 病态检测**（§4.4.1）：σ/μ 比审计 + 病态变量清单 + 稳健 σ 配置 | 12 份 z_pathology_audit.json |
| B6 | **严重度变点检测**（§4.4.2）：PELT/CUSUM 检测 marker 曲线变点，数据驱动分组边界 | 12 份 changepoint_report.json |

#### 阶段 C：因果发现流水线扩展（约 1 周）

| 步骤 | 内容 | 产出 |
|------|------|------|
| C1 | 扩展 `signatures.py`（原 run_signature.py，实际文件名为 signatures.py）：遍历 12 类事故 × 3 严重度组 | 36 份 signature JSON |
| C2 | 扩展 `source_tracing.py`：12 类事故的根因溯源（按严重度分组）；`run_r3.py` 仅做报告生成编排 | 36 份 root_candidates JSON |
| C3 | 扩展 `graph_visualization.py`：分组可视化（§4.6 Step1） | 36 份 HTML 因果图 |
| C3a | **信息门控**（§4.6 Step2）：边数<5的组标注"信息不足" | 水印标注 |
| C3b | **跨严重度汇总视图**（§4.6 Step3）：边并集 + 出现组标签 + 频率投票根因 | 12 份 graph_{k}_summary.html |
| C3c | **差异分析报告**（§4.6 Step4）：机理切换点 + Top1 变化 + 边数差异 | 12 份 diff_report_{k}.json |
| C4 | marker 弱化：确认 marker 不作为特殊变量处理 | 代码确认 |

#### 阶段 D：多源知识融合（约 1.5 周）

| 步骤 | 内容 | 产出 |
|------|------|------|
| D1 | 扩展本体：ENTITY_TABLE 补全到 96 变量 + 12 类 ALLOWED_RELATIONS | `knowledge.py` 扩展 |
| D2 | 实现 C4 专家公差：**每类事故 sev-1 独立基准** + **稳健 σ（MAD×1.4826 + σ_floor）** + z 截断 + **分位数（p10/p20/p50/p80/p90）**（§5.9 + §4.4.1 + §5.10） | 12 份 `tolerance_{k}.json` |
| D3 | 外部知识接入：`external_knowledge/` 目录 + 加载接口（§5.2） | YAML 知识文件 + 加载模块 |
| D4 | 实现 K2 数值约束：5 类近似物理关系 + 残差三态判定（§5.3.2） | `physics_constraints.py` |
| D5 | 实现 K3 机制模板：12 类事故预期机制模板（§5.3.3） | `fault_templates.yaml` |
| D6 | 实现 C5 解释：模板级 rule + 类级/全局汇总（LLM 可选）+ 忠实性校验 | `llm_explanation.py`（原 explanation.py，实际文件名为 llm_explanation.py） |
| D7 | 实现 C6 模板兜底：结构化 RCA 报告模板 | `report_template.py` |
| D8 | Algorithm 3 补充：完善 `counterfactual_validation` 的论文对应逻辑与输出结构（§5.8） | `cf_validation.py` 补充 |
| D9 | **数据偏离度功能**：`deviation.py` + 节点着色 + 排行榜列 + 分位弹窗 + "偏离度≠根因"4处提示（§5.10） | `deviation.py` + 扩展 HTML |
| D10 | 集成测试：12 类事故端到端跑通 | `tracing_report_v23.txt` |

### 6.2 代码模块映射

```
Code/rca/v21/
├── variable_selector.py      [新建] §3 四阶段变量选取
├── accident_config.py        [新建] 12类事故配置表
├── data_audit.py             [新建] §4.4.1 Z病态检测 + §4.4.2 变点检测
├── knowledge.py              [扩展] ENTITY_TABLE(96变量) + 12类ALLOWED_RELATIONS + 禁止边
├── physics_constraints.py    [新建] §5.3.2 K2 数值约束（5类近似物理关系+残差三态判定）
├── external_knowledge.py     [新建] §5.2 外部知识加载接口（YAML→本体合并）
├── tolerance.py              [已落地] §5.9 每类事故 sev-1 注入前 [0,100]s 基准 + 稳健σ(MAD×1.4826+σ_floor) + z截断（v1 的 accidents/loca/anomaly.py 保留不删，仅作历史实现）
├── cf_validation.py          [扩展] §5.8 Algorithm 3 完整逻辑+输出结构
├── deviation.py              [新建] §5.10 偏离度计算（d=min(|x-μ|/σ_robust, 10)）
├── data.py                   [扩展] 12类轨迹级加载
├── signatures.py             [扩展] 12类×3严重度因果签名生成（原run_signature.py）
├── source_tracing.py         [复用] v2.2 根因溯源（marker双口径需调整）
├── graph_visualization.py    [扩展] §4.6 分组可视化 + 信息门控水印
├── severity_aggregation.py   [新建] §4.6 跨严重度汇总 + 差异分析报告
├── llm_explanation.py        [扩展] LLM + 模板解释（原explanation.py）
└── run_source_tracing.py     [扩展] 12类编排

external_knowledge/           [新建] §5.2 外部知识专家库
├── variable_dictionary.yaml  # L1: 96变量完整字典
├── physics_relations.yaml    # L2: 近似物理关系（K2）
├── fault_templates.yaml      # L3: 故障模式机制模板（K3）
├── forbidden_edges.yaml      # 外部补充禁止边
└── tolerance_overrides.yaml  # L4: 外部专家公差覆盖（可选）
```

### 6.3 关键技术选型依据

| 技术点 | 选型 | 依据 |
|--------|------|------|
| 变量选取评分 | 加权综合得分（CV/非高斯/互信息/跨轨迹方差） | 论文 XGBoost+专家验证的可量化替代；四维度覆盖数据质量+统计+物理+稳定性 |
| 共线性处理 | VIF<500 + 保护集豁免（仅 R²>0.998 剔除） | LiNGAM 假设独立噪声，但核电压强耦合；v2.1 硬编码集自身 max VIF 达 1e10–1e13、16–20/20 变量 >10，原 VIF<10 无法执行，放宽为 <500 并保护触发/关键响应/子系统代表变量（2026-11-10 口径定稿） |
| 因果发现 | LiNGAM + GAM-ANM 双引擎 | 忠实于论文 LiNGAM/DiffAN 并列；GAM-ANM 替代完整 DiffAN（p≈15-25 统计匹配） |
| 知识层技术栈 | JSON 代码本体（不替换为 RDF/Neo4j） | §5.1 评估：NPPAD 23变量规模下JSON信息等价，零依赖；升级方向为补全三通道而非换栈 |
| 融合方式 | 三通道（K1约束/K2数值/K3机制） | 对应 v3 §9 三通道设计 + 论文神经符号三层的工程落地 |
| 公差阈值 | **每类事故 sev-1 注入前 [0,100]s（11 点）μ + max(1.4826·MAD, σ_floor)** | v3 §4.3 "sev-1只用于过程本体基线" + "regime map按事故类独立"；V23-DATA-004 修正：原“准稳态段”全轨迹滑窗会把注入后 110–990s 计入基线，已改为注入前固定窗，Normal 只作对照不并入 |
| 外部知识格式 | YAML（非 RDF/Neo4j） | 轻量、版本化、人工可读；与 JSON 本体合并逻辑简单 |
| LLM | DeepSeek V4.1-Flash | 已有实现（v2.1 Phase 5.5），忠实性校验已验证 |

### 6.4 全链重跑准入规定（强制，2026-11-10 生效）

> 条款来源：项目推进效率的明确要求（V23-DESIGN-021）。全链（`run_v23_all`）是**验收/发布动作**，不是开发动作；除本条列出的例外，默认禁止每改一处代码就重跑全链。

**6.4.1 规则本体**

1. 默认禁止全链重跑。任何代码改动先按下述最小集验证，通过后才允许提交。
2. 全链重跑必须事先声明触发原因，并在 `reports/run_v23_all_summary.json` 写入 `rerun_reason`（未填写即视为违规重跑，结果不得引用）。
3. 判断口径：**只有“下游数字可能改变”或“阶段间契约改变”才构成例外**；其余一律走定向验证。

**6.4.2 允许跑全链的例外（四类，须逐条对应）**

| 例外 | 判定标准 | 举例 |
|------|----------|------|
| E-R1 出对外数字 | 需要交付论文/答辩/汇报所用数字，或需要冻结结果 | 论文表格定稿、冻结 Top-1 与稳定边 |
| E-R2 影响数值 | 改动触及变量选取、阈值与常量、排序与权重、数据窗口/基线、bootstrap 次数、变量列序、随机种子 | 论文锚定变量集、VIF 门限、bootstrap 100→50、列序归一 |
| E-R3 影响产物结构/契约 | 改动影响产物 schema、目录结构、步骤序列、阶段间输入输出契约 | A/B 摘出主链（步骤 10→9）、新增 `--with-ab`、产物改名/挪目录 |
| E-R4 跨阶段对不上 | 单阶段复算后仍无法确认上下游是否一致，必须整链重算 | run_id 混源、签名与溯源模板集合不一致 |

**6.4.3 明确禁止触发全链的改动**

注释、日志文案、报错提示、打印格式、文档与台账、纯等价重构、补单测、类型标注、变量重命名（不改语义）、测试夹具调整。此类改动只允许跑受影响单测（必要时加 `--dry-run` 核对步骤计划）。

**6.4.4 每次全链必须留痕**

1. 记录 `run_id`、`rerun_reason`（必须命中 6.4.2 的 E-R1~E-R4 之一并写清具体项）、耗时、步骤数；
2. 记录**相对上一轮**发生变化的数字：变量集差异（新增/剔除/缺失）、9 组 Top-1、稳定边数；无变化也要写“无变化”；
3. 留痕位置：`reports/run_v23_all_summary.json` 的 `rerun_reason` 字段 + `Code/error_tracking.md` 对应用条目；
4. 未声明原因的全链重跑，其产物不得作为验收证据。

**6.4.5 替代验证最小集（开发默认路径）**

| 组合 | 覆盖什么 | 不能覆盖什么 |
|------|----------|--------------|
| 受影响模块单测（`pytest tests/<module> -q`） | 函数级正确性、边界、schema 断言 | 真实数据分布、跨阶段数值联动 |
| 单阶段重跑（`python -m rca.v21.<stage> --run-id <上游run_id>`） | 该阶段在真实产物上的行为与产物结构 | 上游数字变化对该阶段的下游影响 |
| `--dry-run` | 步骤序列、参数装配、步骤数 | 任何数值结果 |
| 与已冻结产物比对（sha256 / 关键指标：变量集、Top-1、稳定边数） | 行为等价性、非预期漂移 | 全新数据或口径变更后的正确性 |

标准组合：**单测 + 单阶段重跑（复用上游 run_id）+ 冻结产物比对**；三者全过即可提交，无需全链。若单阶段产物与冻结产物在关键指标上出现非预期差异，升级为 E-R4 例外。

### 6.5 复现口径（吸收 v1 复现方案）

v1 的复现约定是"单事故、单机、单命令"口径；v2.3 沿用其**可复现原则**并把范围扩到 3 类，命令与产物以 `Code/STRUCTURE.md` §5 为准（本节只定口径，不重复命令清单）。

1. **环境**：Python 3（当前开发机 `python3 --version` = 3.14.4），WSL2/Linux，CPU 可跑，无 GPU 依赖；依赖为已安装的 numpy/pandas/lingam/pygam 栈，不强制虚拟环境，复现以**同机同解释器**为准。`PYTHONPATH=.` 后以 `-m rca.v21.<stage>` 调用。
2. **数据只读**：源数据在 `nppad_repo/Operation_csv_data/{K}/*.csv`，**任何阶段不得写回源目录**；派生数据只落 `output/rca_v23/`。
3. **单一入口**：全链 `python -m rca.v21.run_v23_all --run-id <UTC时间戳> --n-bootstrap 100 --rerun-reason "<E-Rn …>"`；分阶段见 STRUCTURE §5，且分阶段必须 `--run-id <上游 run_id>` 复用，不得自造新 id。
4. **窗口口径（唯一）**：主窗 `[300,1500]`（121 点）/ 学图模板窗 `[60,1500]`（145 点）/ 数据审计基线 `[520,2030]`（152 点）/ 公差基线 `[0,100]`（11 点）；采样 10 s/步。四者用途互不替代（见 §0.2 第 4 条）。
5. **数据划分（唯一）**：每类 `sev-1` 基线 1 条 + 测试 20 条（low/mid/high = 7/7/6）+ 模板 79 条；固定种子 `seed=42`，划分结果由 `build_split_manifest` 落盘。
6. **可复现判定**：同机、同参数、同 run_id 重算 → 产物除 `created_at` 等时间字段外应逐字节一致；跨机允许浮点末位差异，但**关键指标必须一致**：变量集合、Top-1、稳定边数、`max_vif_all` 数量级、降级标记。
7. **产物不可手改**：`output/rca_v23/` 下所有 JSON/HTML/SVG 均由代码生成；论文用 A/B 为一次性冻结件（§6.4 E-R1）。
8. **复现失败排查顺序**：manifest 的 `inputs.sha256` → 列序（readme 顺序）→ 环境/依赖版本 → 种子与 bootstrap 次数 → 阶段间 run_id 是否混源。
9. **v1 遗留边界**：`Code/accidents/loca/*`（含 `anomaly.py`）是 v1 单事故实现，其窗口与阈值**只对 LOCA 历史有效**，不得当作 12 类通用口径。

---

## 7. 验证指标体系

### 7.1 变量选取质量验证（V 系列）

| 指标 | 通过条件 | 度量方式 |
|------|---------|---------|
| V0 论文锚定 | 每个锚定变量要么入选，要么有 `dropped_anchor` 记录（含 stage/reason/VIF/剔除前后 `max_vif_all`） | `selection_report_{K}.json` 的 `paper_anchor` 块（`dropped_anchor`/`anchor_impact`） |
| V1 变量数 | 每类 15 ≤ p ≤ 25 | 计数 |
| V2 子系统覆盖 | 所有相关子系统 ≥1 变量 | 本体查询 |
| V3 VIF | 三态：pass=实际入选集合（含保留锚定变量）全部 VIF<500；fail=存在 ≥500；n/a=VIF 不可算或 p<3（须写原因）。fail 不调阈值掩盖 | statsmodels VIF + `v3_status`/`v3_note`/`max_vif_all` |
| V4 触发变量 | 包含该事故触发变量 | 集合包含检查 |
| V5 物理链完整 | 关键传导路径变量全部入选 | 本体路径检查 |
| V6 选取客观性 | 综合得分可复现（同数据同结果） | 重跑一致性 |
| V7 非高斯诊断 | ≥60% 变量 \|skewness\| > 0.5（仅记录不阻断） | JB检验 |

### 7.2 12类适配完整性验证（C 系列）

> **前提**：以下指标按 Go-3 条件满足后（12 类数据到位）以 12/12 验收；若触发 No-Go 降级为 3 类适配，则对应改为 3/3 验收。

| 指标 | 通过条件 | 度量方式 |
|------|---------|---------|
| C1 数据覆盖 | 12/12 类有 CSV 数据 | 文件存在检查 |
| C2 变量集 | 12/12 类有 variable_sets | JSON 存在 |
| C3 因果图 | 36/36 份 signature（12类×3严重度） | JSON 存在 |
| C4 根因溯源 | 36/36 份 root_candidates | JSON 存在 |
| C5 分组可视化 | 36/36 份 HTML 因果图 | 文件存在 |
| C6 汇总可视化 | 12/12 份跨严重度汇总图 | 文件存在 |
| C7 差异分析 | 12/12 份差异分析报告 | 文件存在 |
| C8 解释报告 | 12/12 类有 RCA 报告 | 文件存在 |
| C9 Z病态审计 | 12/12 类有 z_pathology_audit.json | 文件存在+病态变量清单 |
| C10 变点检测 | 12/12 类有 changepoint_report.json | 文件存在+分组边界 |

### 7.3 因果发现质量验证（Q 系列）

| 指标 | 通过条件 | 度量方式 |
|------|---------|---------|
| Q1 忠实性 | 所有边可追溯到数据（禁止边违规=0） | 本体校验 |
| Q2 稳定边比例 | 稳定边（f_e≥0.70 且 dir_e≥0.90）占候选边 ≥ 50%（不再采用 consensus 占比：单引擎下 consensus 退化） | 稳定边计数 |
| Q3 总效应合理性 | 关键路径总效应符号与物理一致 | 人工抽检 |
| Q4 根因合理性 | Top1（MASKED 口径，排除 marker 后）对效应变量总因果效应最大（T=(I-B)⁻¹） | 总因果效应检查 |
| Q5 信息门控 | 边数<5的组正确标注"信息不足" | 标注检查 |
| Q6 严重度差异检出 | 能检测出Top1切换的事故（如SGATR） | 差异分析报告 |
| **Q7 内容门槛** | **每类事故稳定边数 ≥ 5 且 Top1 可达路径 ≥ 1 条**（无内容则弃权，与 §4.6 信息门控阈值一致；删去 consensus 表述） | 边数+路径数检查 |
| **Q8 弃权合理性** | 弃权事故有明确理由（边数不足/本体冲突），非静默失败 | 弃权报告 |

### 7.4 知识融合验证（K 系列）

| 指标 | 通过条件 | 度量方式 |
|------|---------|---------|
| K1 解释忠实性 | LLM 报告校验通过率 ≥ 95% | 正则匹配变量名+数值 |
| K2 降级可用性 | LLM 不可用时模板报告完整 | 断网测试 |
| K3 禁止边工程约束 | 禁止边违规数 = 0（工程约束，不声称物理有效性） | 本体校验 |
| K4 异常判定 | 触发变量异常检出率 = 100% | 阈值校验 |
| K5 K2数值约束 | 5 类物理关系残差三态判定**输出完整、如实报告**（不按覆盖率达标；不可判定比例单列） | 残差计算+不可判定比例报告 |
| K6 K3机制模板 | 12 类模板**输出完整、如实报告**（不按覆盖率达标；关系库扩充列为后续工作，匹配分仅记录不作有效性结论） | 模板存在性检查 |
| K7 外部知识加载 | external_knowledge/ YAML 全部加载无冲突 | 加载日志+冲突检查 |
| K8 基准数据覆盖 | 12/12 类有独立 tolerance_{k}.json | 文件存在检查 |
| K9 Algorithm 3 | 每类事故 cf_validation 输出结构完整 | JSON schema 校验 |
| K10 稳健σ | 所有 tolerance 使用 σ_robust（MAD×1.4826+σ_floor） | 阈值文件校验 |
| K11 低方差通道控制 | 病态（低方差）变量**全部**走稳健 σ + ±10 截断，原始 max\|z\| 单列报告；病态比例只作诊断（不再要求 <30%） | 审计报告校验 |
| K12 偏离度基准 | 12/12 类 tolerance 含 p10/p20/p50/p80/p90 分位数 | 字段完整性 |
| K13 偏离度边界 | 4处"偏离度≠根因"提示全部展示 | 前端检查 |
| K14 偏离度范围 | 所有 d ≤ 10，无极端值 | 摘要文件校验 |

### 7.5 端到端验证（E 系列）

> **前提**：E1/E2 按 Go-3 条件满足后（12 类数据到位）以 12 类验收；若触发 No-Go 降级为 3 类适配，则 E1/E2 对应改为 3/3 验收（E3/E4 不变）。

| 指标 | 通过条件 |
|------|---------|
| E1 全流程跑通 | 12 类事故端到端无报错（降级时 3/3） |
| E2 产物完整 | 每类事故有：variable_sets/z_audit/changepoint/tolerance + 3×(signature/root_candidates/graph) + summary_graph/diff_report/report |
| E3 不污染原产物 | 仅写入 tracing/ 和 v23/ 子目录 |
| E4 Z病态不泄漏 | 标准化后无 \|z\|>10 的值，无 σ/\|μ\|<0.1% 的阈值 |

---

### 7.6 v2.1 实验解读 O 系列承接状态（吸收，不许静默丢失）

v2.1 实验解读提出的 O1–O12 是本方案的**输入待办**。下表按事实标注承接状态；标"未闭环"的项不得在论文中写成已解决。

| 编号 | 原始问题 | v2.3 承接状态 | 落点 / 说明 |
|------|----------|----------------|-------------|
| O1 | D0（同义反复基线）不能证伪因果增量 | **部分闭环** | 主检验已改为 T 矩阵拓扑/效应通道（§2.7.2、§7.3 Q 系列），但**配对显著性检验未做**，不能说"因果通道显著优于相关通道" |
| O2 | risk-coverage 曲线 | **部分闭环** | `baselines` 输出 coverage 9 点；AURC / matched-coverage 指标未实现（v3 §5 承接） |
| O3 | σ 病态与 z 爆炸 | **已闭环** | 稳健 σ（MAD×1.4826 + σ_floor）+ z 截断 ±10（§4.4.1、§5.9） |
| O4 | 严重度分组是否该由变点决定 | **未闭环（待裁决）** | 变点检测已实现并落盘（LOCA 7/12/21/43、SGATR 87、FLB 0），但**分组边界仍固定 2–33/34–66/67–100**；改与不改属研究决策 |
| O5 | 方向 estimand 未定义 | **部分闭环** | C^A 定义域已写明为"训练中位处的局部斜率"（§2.7）；跨域方向一致性敏感性未做 |
| O6 | 逐轨迹 δ_i 与检验功效 | **未闭环** | v2.3 无 D0 式门控；功效分析缺失 |
| O7 | 弃权的语义与分解 | **未闭环** | 现行"弃权"仅指**数值稳定门控**（cond>1e4 或截断比>0.35），与"评分不确定而弃权"不是同一概念 |
| O8 | 反事实退化为同组重采样 | **未闭环** | Algorithm 3 仍为同组重采样，`gCF=PASS` 只能读作"形式通过"（§2.7.2） |
| O9 | 新任务（侧别/剂量/开集） | **未做** | v3 承接；v2.3 不声称支持 |
| O10 | 扫描数据质量审计缺失 | **已闭环** | `data_audit.py` + `z_pathology_audit` + `changepoint_report` + 恒定列检查（B4/B5/B6） |
| O11 | 产物不可复现 | **已闭环** | `run_v23_all` 一站式 + manifest `inputs.sha256` + 单阶段复用 run_id + 单测 125（§6.5）。**例外：LLM 链路无 key，未实测** |
| O12 | 报告口径（模板/类级/全局） | **部分闭环** | 三层解释 + 忠实性校验已落地；SGATR sev87 的"真实现象非 bug"已降级为"已定位、机制未证实"（§0.2 第 7 条） |

---

## 8. 风险点与预案

### 8.1 变量选取风险

| 风险 | 概率 | 影响 | 预案 |
|------|------|------|------|
| 自动选取变量集与物理直觉偏差大 | 中 | 因果发现结果物理不可解释 | 阶段3物理知识校验强制约束；保留人工指定变量集作为回退 |
| VIF 剔除后变量数不足15 | 低 | 因果发现变量过少 | 放宽 VIF 阈值（现行 500）；或从阶段2 Top-30 补充 |
| 非高斯性不满足 | 中 | LiNGAM 方向辨识不可靠 | 仅记录不阻断（阶段5笔记Q9）；GAM-ANM 不依赖非高斯 |

### 8.2 12类适配风险

| 风险 | 概率 | 影响 | 预案 |
|------|------|------|------|
| 部分事故数据未下载 | 中 | 无法全覆盖 | 优先实现已有数据的事故；缺失的标记为"待数据" |
| 某类事故常量列过多 | 高 | 变量选取后不足15个 | 从其他子系统补充；或接受较少变量并声明 |
| 时间窗口不适配 | 中 | 因果边质量差 | 每类事故 Phase 0 独立审计窗口；不沿用 LOCA 窗口 |
| marker 曲线无变点或多变点 | 中 | 分组边界难以确定 | 无变点则沿用三分组；多变点则取最显著断点并声明 |
| 严重度分组边数差异过大 | 高 | 某组信息不足（如LOCA_mid仅1边） | 信息门控标注"不可靠"，不强行排名 |

### 8.3 基准数据风险

| 风险 | 概率 | 影响 | 预案 |
|------|------|------|------|
| 某类事故 sev-1 无合格准稳态段 | 中 | 该类无独立基准 | 回退1：最低严重度最长稳态段；回退2：初始段t=0~100s；回退3：Normal（声明降级） |
| 基准阈值对该事故误报率高 | 中 | 异常判定不可靠 | 人工审核阈值；外部专家公差覆盖（tolerance_overrides.yaml） |
| 回退到Normal基准降级 | 低 | 基准效力不足 | 声明降级，仅作 sanity check，不作为主异常判定依据 |
| **Z 病态：σ≈0 导致 z 爆炸** | **高** | **相似度度量被饱和变量支配** | **稳健 σ（MAD×1.4826+σ_floor）+ z 截断到±10；饱和变量改用相对变化率** |
| σ_floor 设定过宽掩盖真实异常 | 低 | 异常检出率下降 | 对 1%·|μ| 下限做敏感性分析（0.5%/1%/2%），报告差异 |

### 8.4 知识融合风险

| 风险 | 概率 | 影响 | 预案 |
|------|------|------|------|
| LLM API 不可用 | 中 | 解释生成失败 | C6 模板自动降级（已实现三级降级） |
| 本体知识不完整 | 高 | 禁止边/允许边规则缺失 | 分阶段补充本体；缺失的标记为"无约束" |
| 忠实性校验过严 | 低 | LLM 报告频繁降级 | 调整正则容差；数值校验用 1e-3 浮点容差 |
| K2 物理关系残差不可判定 | 中 | 数值约束无效 | 标记为"不可判定"并报告比例；不阻断主流程 |
| 外部知识与代码本体冲突 | 低 | 知识合并异常 | 以代码本体为准；外部知识标记"待审核" |
| K3 模板与数据冲突 | 中 | 机制匹配分低 | 冲突边标"冲突"进入竞争解释，不删除 |

### 8.5 计算资源风险

| 风险 | 概率 | 影响 | 预案 |
|------|------|------|------|
| 12类 × Bootstrap 100次 × 双引擎 计算量大 | 中 | 运行时间过长 | 并行化（按事故类并行）；Bootstrap 次数可降至 50 |
| 变量选取 VIF 迭代慢 | 低 | 选取耗时 | VIF 计算仅在最终候选集上做，不遍历全量 |

### 8.6 总体回退策略

若某阶段验证不通过，按以下优先级回退：
1. **变量选取失败** → 回退到早期机理筛选集（`paper_anchor` 的锚定代理，14/15/14 条），并声明为降级（不得声称等同 v2.1 硬编码集）
2. **某类事故基准失败** → 回退到该类 sev-2 的注入前 [0,100]s；仍不可用则标"基准不可用"并跳过（Normal 轨迹只作 sanity check，不并入正式公差）
3. **某类事故适配失败** → 跳过该类，标记为"待修复"，不阻断其他类
4. **LLM 解释失败** → 自动降级到模板解释（C6）
5. **K2/K3 知识约束失败** → 降级为 K1-only 模式（仅保留禁止边约束）
6. **知识融合整体失败** → 回退到 v2.2 纯数据驱动模式

---

## 9. 版本关系、吸收清单与待补条目

### 9.1 版本处置（全部原地保留，不删不移）

| 文件 | 原主张 | 与 v2.3 的差异 | 处置 |
|------|--------|----------------|------|
| `NPPAD-CausalTrace实验复现方案.md`（v1） | 单事故 LOCA 全链复现；准稳态段基准；双引擎+共识边+RRF | 只覆盖 1 类；基准窗口已被每类注入前窗口替代 | **归档**；复现原则吸收为 §6.5 |
| `NPPAD-CausalTrace-v2-跨事故因果签名RCA方案.md` | 3→12 类的总体设想、模块规划 | 未量化变量选取、无知识融合细则 | **归档**（历史骨架） |
| `NPPAD-CausalTrace-v2.1方案.md` | 3 类因果签名 RCA、LiNGAM/GAM-ANM、硬编码 20 变量集 | 变量集硬编码；共线性病；未量化 | **归档**；方法定义吸收为 §2.7 |
| `NPPAD-CausalTrace-v2.1实验解读与第二阶段方案-2026-11-02.md` | 阶段一实验结论 + O1–O12 待办 | 仅诊断，不含实现 | **归档**；结论承接为 §7.6 |
| `NPPAD-CausalTrace-v2.2方案-变量溯源与因果图可视化.md` | 拓扑根因排名、0 依赖 HTML/SVG、marker 遮蔽 | 排名键为纯拓扑；路径写 ≤4 层 | **归档**；口径吸收为 §4.7（按现行实现收敛） |
| `NPPAD-CausalTrace-v2.3方案评审-2026-11-03.md` | 对 v2.3 初稿的外部评审意见 | 部分意见已落地，部分不成立（如"函数不存在"） | **归档**（保留可追溯） |
| `方案关联-版本复用与依赖关系说明.md` | 版本间模块复用详表 | 索引地位被 README 取代 | **保留为详表**（索引以 README 为准） |
| `NPPAD-CausalTrace-后续研究综合方案-v3-2026-11-02.md` | 可辨识性、证据强度、动态因果、risk-coverage | 研究方法层 | **并行研究线**，不并入 |
| `AERCA-可利用性专项方案-2026-11-02.md` | 可利用性/解释可用性 | 独立评估线 | **并行研究线**，不并入 |

### 9.2 吸收映射（谁的内容进了哪一节）

| 来源 | 吸收内容 | 落到 |
|------|----------|------|
| v2.1 方案 | 符号体系、Algorithm 1/2/3、Bootstrap 口径、E1/E2/E3、marker 遮蔽定义 | §2.7 |
| v2.2 方案 | 溯源排名、marker 双口径、传导路径、0 依赖可视化、忠实性、跨严重度汇总 | §4.7 |
| v1 复现方案 | 环境/入口/run_id 贯穿/产物只读/比对判定/排查顺序 | §6.5 |
| v2.1 实验解读 | O1–O12 的逐项承接状态 | §7.6 |
| 冲突项（多来源） | 阈值、分组、统计单位、窗口、σ_floor、稳定边、sev87、数据规模、过期引用 | §0.2 |

### 9.3 待补条目（无数据/无凭据，禁止编造）

| # | 待补项 | 现状事实 | 为什么不能自行补齐 |
|---|--------|----------|--------------------|
| 1 | 其余 8 类事故数据（SLBIC/SLBOC/SGBTR/RW/RI/MD/LR/LLB） | 本地无目录 | 无数据不得生成；12 类验收只能按 3/12 + LOCAC 描述性交付 |
| 2 | LOCAC 仅 5 条轨迹 | 有目录但不足 20 条 | 样本量不足以分层，只能描述性 |
| 3 | **V3 三类仍 fail（已按方案 (a) 去重后仍 fail）** | 用户已裁决走 **(a)** 并落地（V23-DESIGN-028）：阶段3 按 `\|Pearson r\| ≥ 0.99` 去重，FLB 剔 `PSGB`/`WFWB`、SGATR 剔 `PSGB`/`LSGB`，LOCA 无重复对（强制集最高相关 0.9106）。去重生效后 V3 **仍 fail**，但 offenders **已不再是重复测点**，全部是受保护变量（forced + 子系统代表）：LOCA `VOL`=2161.6、`RRCA`=1271.0；SGATR `LSGA`=1740.2、`NSGA`=553.1；FLB `QMGB`=2314.6、`PWNT`=1069.9、`TAVG`=760.9、`WCHG`=749.2、`WSTB`=737.7。`max_vif_all` 7.757e9 → 2314.6（FLB）、4.288e5 → 1740.2（SGATR）、2.162e3 → 2161.6（LOCA，未受去重影响） | 保护变量（强制保留 + 每子系统代表）不受 VIF 迭代裁剪，是 V2/V3 的既有设计取舍；放宽 `VIF_MAX=500` 或取消保护都会改方法口径，须另行裁决。**本轮未改阈值、未改 V5 判据** |
| 4 | 严重度分组是否数据驱动（O4） | 变点已检出（SGATR 87 等），分组仍固定 | 改边界会改全部下游数字，须先裁决 |
| 5 | LLM 解释链路实测 | `llm_available=false`，默认 rule 后端 | 无 API key，未实际调用，不得写成"已验证" |
| 6 | `tolerance_LOCA.json` 与台账不符 | **已消除**：全链重算后 `tolerances/tolerance_LOCA.json` 与其余产物同属 `run_id=v23-20261110T134957` | — |
| 7 | `run_v23_all_summary.json` 被测试痕迹覆盖 | **已消除**：当前 `reports/run_v23_all_summary.json` 为 `run_id=v23-20261110T134957`、9/9 步 ok | — |
| 8 | O2/O6/O7/O8/O9 | 未闭环（见 §7.6） | 属方法研究，需 v3 或后续专项 |
| 9 | 12 类 ALLOWED_RELATIONS | 仅 3 类落地 | 其余类缺机理资料与数据，不造边 |
| 10 | 论文锚定 8 变量名单 | 论文原文未给出名单 | 现有锚定是"早期机理筛选集"代理，属工程假设，不可声称复刻论文 |

**第 3 项已裁决并落地：走 (a) 精确重复测点去重**（V23-DESIGN-028，`v23-20261110T134957`）：
- (a) 对"精确重复测点对"做去重（`|Pearson r| ≥ 0.99`，如 PSGA/PSGB、WFWA/WFWB、LSGA/LSGB 仅保留其一，另一路记 `dropped_key_response`）——**已实施**；
- (b) 放宽 V5 为"共线组内选代表"——**未采纳**；
- (c) 接受 V3 fail 并在论文中显式披露——去重后 V3 仍 fail，**仍需在论文中披露 offenders（现为保护变量）**。

---

## 附录 A：12类事故触发变量与关键响应变量速查表

| 事故 | 触发变量 | 关键响应变量 | 涉及子系统 |
|------|---------|-------------|-----------|
| LOCA | WLR | P, TAVG, VOL, WECS, WHPI | 一回路+安注+堆芯 |
| LOCAC | WLR | P, TAVG, VOL, WECS, WHPI | 一回路+安注+堆芯 |
| SLBIC | WSTA, WSTB | PSGA, PSGB, WFWA, WFWB, TAVG | 二回路+一回路耦合 |
| SLBOC | WSTA, WSTB | PSGA, PSGB, WFWA, WFWB, TAVG | 二回路+一回路耦合 |
| SGATR | WTRA | PSGA, LSGA, RM2 | SG-A+剂量 |
| SGBTR | WTRB | PSGB, LSGB, RM2 | SG-B+剂量 |
| RW | RHRD | PWNT, TAVG, P, QMWT | 反应性+堆芯+热工 |
| RI | RHRD | PWNT, TAVG, P, QMWT | 反应性+堆芯+热工 |
| MD | PPM | PWNT, TAVG, P, QMWT | 反应性+堆芯+热工 |
| FLB | WFLB | WFWA, WFWB, TAVG, PWNT, PSGA | 给水+二回路+一回路 |
| LR | TBLD | WFWA, WFWB, TAVG, PWNT, PSGA | 给水+二回路+一回路 |
| LLB | WLD | VOL, P, LVPZ, WECS | 化容+一回路 |

> 注：`rca/v21/variable_selector._V21_KEY_RESPONSE_OVERRIDES` 为不改动已验收的 V1–V7，保留了 v2.1 内联的更全集合（SGATR 额外含 `PSGB`/`LSGB`，FLB 额外含 `PSGB`）。经 V23-DESIGN-028 阶段3 精确重复测点去重后，**有效关键响应与本表一致**：SGATR = `PSGA`/`LSGA`/`RM2`，FLB = `WFWA`/`TAVG`/`PWNT`/`PSGA`。

## 附录 B：与 v3 方案的边界声明

v2.3 是**工程扩展层**，不涉及 v3 的以下研究内容：
- 可辨识性矩阵与 RQ1（v3 §0.2）
- 证据强度矩阵与主张-证据矩阵（v3 §3）
- 动态因果 pilot（VAR-LiNGAM/PCMCI/AERCA，v3 P2-3）
- 开集拒识与工况迁移（v3 RQ3）
- Risk-Coverage/AURC 评价（v3 §5）

v2.3 的产物可直接作为 v3 P0/P1 的输入数据。

---

## 附录 C：执行状态清单（历史快照，2026-11-03 更新）

> **⚠️ 历史快照，以台账为准（2026-11-10 标注）**：本附录为 2026-11-03 的执行快照，路径与状态均已过期，与附录 D 的口径互相矛盾时**两份都不作准**。
> v2.3 的最新执行状态、问题登记与关闭理由**一律以 `Code/error_tracking.md` 为准**；目录与命名以 `Code/STRUCTURE.md` 为准。
> 保留本附录的目的仅为追溯当时的执行判断，**不删除、不更新内容**。
> 收口现状：open 清零，仅 V23-DATA-001/003（等数据）与 RCA-010（v2.1 范围）保持登记。

> **标注规则**：✅ 已执行（产物已落盘并验证）｜⚠️ 部分执行（仅 3 类事故完成，未达 12 类目标）｜❌ 未执行｜🔵 v2.1/v2.2 已有（直接复用，无需重做）
>
> **数据前提**：当前本地仅有 LOCA/SGATR/FLB 三类完整数据，已触发 §4.0 No-Go 降级条件（9 类待核实事故数据未到位），故 Phase B/C/D 的 12 类适配暂按 3 类执行。

### 阶段 A：变量选取机制落地

| 步骤 | 内容 | 状态 | 证据 |
|------|------|:----:|------|
| A1 | 新建 `variable_selector.py`：论文锚定 + 四阶段漏斗 | ✅ | `Code/rca/v21/variable_selector.py` + `paper_anchor.py`；阶段0 锚定（候选优先 + 评分加权，不作保护集；剔除落 `dropped_anchor`）→硬过滤→评分(CV0.25+非高斯0.20+MI0.30+区分度0.25)→强制变量+子系统覆盖+去冗余→VIF迭代(N=15–25)；V3 三态 |
| A2 | 扩展 `ENTITY_TABLE`：12 类子系统映射 | ✅ | `knowledge.py` 现覆盖 NPPAD README Table 2 全部 96 变量（排除 TIME），10 个子系统分类；选中变量 0 未登记 |
| A3 | 新建 `accident_config.py`：12 类配置表 | ⚠️ | 12 类全量配置表待数据到位后建；当前 3 类关键响应已按 §4.3 内联于 `variable_selector.py`（LOCA: P/TAVG/VOL/WECS/WHPI；SGATR: PSGA/PSGB/LSGA/LSGB/RM2；FLB: WFWA/WFWB/TAVG/PWNT/PSGA/PSGB） |
| A4 | 3 类事故验证：论文锚定（候选加权、可剔除留痕）+ 自动补选 | ✅ | 2026-11-10 返工全链 `run_id=v23-20261110T050332`（9/9 ok，291.0s，125 tests passed）：LOCA/SGATR/FLB 各 **15** 变量；`max_vif_all`=2.162e3/4.288e5/7.757e9（非保护 max 490.0/249.9/49.6）；V0/V1/V2/V4/V5/V6/V7 全通过，**V3 三类均 fail**——offenders 全为保护变量（§4.3 关键响应含近完全共线测点对，见台账 V23-DESIGN-023）；锚定 14/8/6、15/7/8、14/6/8（声明/保留/剔除，剔除均含 VIF 与剔除前后 `max_vif_all`）；稳定性 **0/9 降级**（LOCA_mid clip_ratio 0.4516→0.1507）。A/B 对照为**一次性消融**冻结件 `ablations/frozen_ab_variable_selection_v23-20261110T150726.json`（`frozen=true`，`frozen_at`=2026-11-10 16:00，源件 sha256=`9e0c39c5d007ecd0…0f571`，冻结件文件 sha256=`c2c3339f15ab7667…5f9652`），不随主链重算；旧 `…_v23-20261110T034433.json` **作废**（其结论基于旧数据根/混源批次，见 V23-DESIGN-034/036） |

> **VIF 阈值决策留痕（评审 P0-1）**：方案 §3.5 原定 VIF<10，实测核电数据物理强耦合不可行——v2.1 硬编码 20 变量集自身 max VIF=9.45e10（16/20>10）；逐轨迹 VIF 更差（WHPI/WECS 达 1e15）。故放宽至 VIF_MAX=500；**V3 判定只看非保护变量（非 forced、非子系统代表）的 `max_vif<500`，保护变量逐项写入 `v3_exempt[]` 披露、不参与判定**，`v3_status` 四值 `pass / pass_with_exemption / fail / n/a`；同时报告 `max_vif_all` 透明披露含保护变量的真实最大 VIF。**豁免不等于通过**，缺口靠逐项披露而非放宽阈值（不采用"阈值放宽到 5000"的替代口径，避免与阶段3 去重形成双重放松）。指标名已对齐为 `V3_vif_lt_500`。
>
> **信息泄漏修复（评审 P0-3）**：变量选取原用 `range(2,101)` 含 20 条 test 轨迹，已改为仅读 `split_manifest.json` 的 train severities（79 条/类）。

### 阶段 B：12 类事故数据适配

| 步骤 | 内容 | 状态 | 证据 |
|------|------|:----:|------|
| B1 | 确认 12 类事故 CSV 数据路径 | ⚠️ | 仅 LOCA/SGATR/FLB 三类数据已确认；LOCAC 仅 5 条轨迹；其余 8 类待数据 |
| B2 | 扩展 `data.py`：12 类轨迹级加载 | ❌ | `data.py` 仍仅支持 3 类 |
| B3 | 每类事故生成变量集 | ⚠️ | 仅 LOCA/SGATR/FLB 三类（`variable_sets/selection_report_{k}.json`） |
| B4 | 数据审计：常量列/轨迹数/时间窗口 | ❌ | 无 `data_audit.py`，无 12 类审计报告 |
| B5 | 共享基线 Z 病态检测 | ❌ | 无 `z_pathology_audit.json`；稳健 σ 仅在 variable_selector 内用 σ_floor 兜底 |
| B6 | 严重度变点检测 | ❌ | 无 `changepoint_report.json`；沿用 v2.1 固定三分组 |

### 阶段 C：因果发现流水线扩展

| 步骤 | 内容 | 状态 | 证据 |
|------|------|:----:|------|
| C1 | 扩展 `signatures.py`：12类×3组因果签名 | ⚠️ | 3 类 9 份签名已生成（`v23_signatures/`，100 bootstrap）；未达 12 类 36 份 |
| C2 | 扩展 `source_tracing.py`：根因溯源（**T=(I−B)⁻¹ 总因果效应排序**，论文 Algorithm 2） | ⚠️ | 3 类 9 份 root_candidates 已生成（`v23_tracing/`，schema=`NPPAD-CausalTrace-v2.3-tracing`）；排序主键=候选变量对效应变量的总因果效应 \|T\| 之和 DESC，次级键=拓扑指标；V2/V3/V4 校验全通过；详见附录 D |
| C3 | 扩展 `graph_visualization.py`：分组因果图 | ⚠️ | 3 类 9 份 HTML 已生成（`v23_graphs/`，11–14 节点/28–72 边）；consensus 分类从 dir_e 还原 |
| C3a | 信息门控：边数<5 标注"信息不足" | ✅ | `graph_visualization.py` 已加水印 CSS + 排行榜"不可靠"标注；当前 9 组 consensus 边均 ≥36，未触发 |
| C3b | 跨严重度汇总视图 | ✅ | `severity_aggregation.py` 新建；3 份 `graph_{k}_summary.html`（LOCA 126/SGATR 98/FLB 75 条并集边，根因频率投票榜） |
| C3c | 差异分析报告 | ✅ | `severity_aggregation.py`；3 份 `diff_report_{k}.json`：LOCA Top1 切换 1 次+机理切换 2 处；SGATR Top1 切换 1 次；FLB Top1 切换 2 次+机理切换 1 处 |
| C4 | marker 弱化 | ✅ | root_candidates 中 marker 已遮蔽（V4 校验通过） |

### 阶段 D：多源知识融合

| 步骤 | 内容 | 状态 | 证据 |
|------|------|:----:|------|
| D1 | 扩展本体至 96 变量 + 12 类 ALLOWED_RELATIONS | ⚠️ | ENTITY_TABLE 已扩至 96 变量（NPPAD README Table 2），10 子系统分类；ALLOWED_RELATIONS 仍 3 类（待 12 类数据） |
| D2 | C4 专家公差：每类事故 sev-1 独立基准 | ✅ | `tolerance.py` 新建；3 类 `tolerance_{k}.json`：准稳态 CV<5% 检测 + μ + σ_robust=max(1.4826·MAD, 1%·|μ|) + 分位数 p10/p20/p50/p80/p90 + Z 病态标记。LOCA 51 变量/0 病态、SGATR 45/1、FLB 46/1 |
| D3 | 外部知识接入：`external_knowledge/` 目录 | ❌ | 目录未创建，无 YAML 知识文件 |
| D4 | K2 数值约束：5 类近似物理关系 + 残差三态 | ✅ | `physics_constraints.py` 新建；5 类关系（装量-泄漏/SG水位/安注触发/传热管泄漏/反应性-功率），三态判定 violation/not_violated/undecidable；已在 sev-1 上测试 |
| D5 | K3 机制模板：12 类事故预期机制模板 | ❌ | 无 `fault_templates.yaml` |
| D6 | C5 解释（降频）：模板级 9 份 rule 结构化解释 + 3 份类级汇总 + 1 份全局结论（LLM 可选、失败降级 rule、同 run_id+输入哈希缓存、模板 id/数字可追溯） | ✅ | `reports/llm_explanation_{K}_{g}.json` 9 份 + `llm_explanation_class_{K}.json` 3 份 + `llm_explanation_global.json`（run_id=v23-20261110T043640，全部 faithful=True，模板引用 12/12/12/9） |
| D7 | C6 模板兜底：结构化 RCA 报告模板 | ✅ | `report_template.py` 新建；9 份 JSON+文本 RCA 报告（Top1+传导路径+Top5 证据），作为 LLM 不可用时的 C6 兜底 |
| D8 | Algorithm 3 补充：cf_validation 论文对应逻辑 | 🔵 | `cf_validation.py` 已在 v2.1 完整实现（`cf_validation/` 下 9 份 cf 结果已存在），无需重做 |
| D9 | 数据偏离度功能 | ✅ | `deviation.py` 新建；d=min(|x−μ|/σ_robust, 10)，4 级着色；3 类×3 严重度共 9 份 `deviation_{k}_sev{s}.json`；已集成到因果图节点着色+排行榜列+分位弹窗+4 处"偏离度≠根因"提示 |
| D10 | 集成测试：12 类端到端 | ⚠️ | 3 类端到端跑通（`v23_tracing/v23_end2end_summary.json`）；未达 12 类 |

### 验证指标完成度

| 系列 | 指标数 | 已验证 | 未验证 | 说明 |
|------|:------:|:------:|:------:|------|
| V 变量选取 | 7 | 7 (V1–V7) | 0 | 3 类全通过（V6 二次运行实校、V7 回方案口径） |
| C 12类适配 | 10 | 0 | 10 | 9 类数据未到位，全部降级为 3/3 不适用 |
| Q 因果发现质量 | 8 | 5 (Q1/Q2/Q4/Q5/Q7) | 3 (Q3/Q6/Q8) | Q4 已改为 T 矩阵总因果效应校验；Q3 人工抽检未做；Q6 依赖 12 类；Q8 弃权报告未做 |
| K 知识融合 | 14 | 5 (K2/K4/K5/K6/K9) | 9 | K2 数值约束✅、K4 偏离度✅、K5 子系统映射✅、K6 基准✅、K9 cf_validation 已有；其余依赖 12 类数据 |
| E 端到端 | 4 | 3 (E1/E3/E4) | 1 (E2) | E1 仅 3/3；E3 不污染已确认；E4 Z 病态已审计；E2 产物待 12 类 |

### 待执行清单（按优先级）

**P0 — 3 类闭环（当前可做，无需新数据）**
1. ✅ C3a 信息门控：边数<5 的组标注"信息不足"水印
2. ✅ C3b 跨严重度汇总图（3 类）
3. ✅ C3c 差异分析报告（3 类）
4. ✅ D6 解释降频落地：模板级 9 份 rule（凭证 9/9 faithful）+ 3 份类级汇总 + 1 份全局结论（LLM 不可用时降级 rule，全部 faithful）
5. ❌ Q3 总效应合理性人工抽检
6. ✅ E4 Z 病态不泄漏校验：baseline 仅 sev-1、变量选取仅 train 79 条、签名仅 train_template_severities，test 20 条全程未进入训练侧

**P1 — 9 类数据到位后启动**
7. ❌ B1–B6：12 类数据审计 + 变量集生成
8. ❌ C1–C3：12 类签名/溯源/因果图
9. ❌ D1：本体 ALLOWED_RELATIONS 扩展至 12 类（D2 每类 sev-1 独立基准已完成）

**P2 — 知识融合增强**
10. ❌ D3 外部知识接入
11. ✅ D4 K2 数值约束（`physics_constraints.py`，5 类物理关系三态判定）
12. ❌ D5 K3 机制模板
13. ✅ D9 数据偏离度功能（`deviation.py`，节点着色+排行榜+分位弹窗）

**P3 — 可选**
14. ❌ A3 accident_config.py 独立配置表
15. ✅ D7 report_template.py 独立模板模块（9 份 RCA 报告）

---

## 附录 D：V2.3 执行总表 + RCA 算法说明 + 错误记录（历史快照，2026-11-05）

> **⚠️ 历史快照，以台账为准（2026-11-10 标注）**：本附录的"已完成/未完成"总表与错误记录为 2026-11-05 的快照，
> 与附录 C 及当前产物结构（`Code/STRUCTURE.md`、`output/rca_v23/`）均不一致；两者矛盾时**两份都不作准**。
> 最新执行状态、问题编号、口径变更与关闭理由**一律以 `Code/error_tracking.md` 为准**。内容保留不删、不更新。

### D.1 V2.3 已完成 / 未完成总表

| 阶段 | 步骤 | 内容 | 状态 | 产物路径 |
|------|------|------|:----:|---------|
| **A** | A1 | variable_selector.py 论文锚定 + 四阶段漏斗 | ✅ | `Code/rca/v21/variable_selector.py` + `paper_anchor.py` |
| **A** | A2 | ENTITY_TABLE 扩至 96 变量 | ✅ | `knowledge.py`（96 变量，10 子系统） |
| **A** | A3 | accident_config.py 12 类配置表 | ❌ | 未建（3 类配置内联于 variable_selector.py） |
| **A** | A4 | 3 类 A/B 对照验证（一次性消融，产物已冻结） | ✅ | `variable_sets/` + `ablations/frozen_ab_variable_selection_v23-20261110T150726.json`（`frozen=true`，文件 sha256=`c2c3339f…5f9652`，源件 sha256=`9e0c39c5…0f571`，不在主链重算）；`034433` 版作废 |
| **B** | B1–B6 | 12 类数据适配/审计/变点 | ❌ | 9 类数据未到位，跳过 |
| **C** | C1 | 因果签名（100 bootstrap） | ⚠️ 3/12 类 | `v23_signatures/` 9 份 |
| **C** | C2 | 根因溯源（**T=(I−B)⁻¹ 排序**） | ⚠️ 3/12 类 | `v23_tracing/root_candidates_*.json` 9 份 |
| **C** | C3 | 分组因果图 | ⚠️ 3/12 类 | `v23_graphs/graph_*.html` 9 份 |
| **C** | C3a | 信息门控水印 | ✅ | `graph_visualization.py` |
| **C** | C3b | 跨严重度汇总图 | ✅ | `v23_graphs/graph_{k}_summary.html` 3 份 |
| **C** | C3c | 差异分析报告 | ✅ | `v23_tracing/diff_report_{k}.json` 3 份 |
| **C** | C4 | marker 弱化 | ✅ | root_candidates 中 marker 已遮蔽 |
| **D** | D1 | 本体 96 变量 + 12 类关系 | ⚠️ | 变量✅，关系仍 3 类 |
| **D** | D2 | 每类 sev-1 独立基准 | ✅ | `tolerances/tolerance_{k}.json` 3 份 |
| **D** | D3 | 外部知识接入 | ❌ | 未建 |
| **D** | D4 | K2 数值约束 | ✅ | `physics_constraints.py` |
| **D** | D5 | K3 机制模板 | ❌ | 未建 |
| **D** | D6 | 解释（模板 rule + 类级/全局汇总） | ✅ | `reports/llm_explanation_*.json` 13 份 |
| **D** | D7 | 结构化 RCA 报告模板 | ✅ | `report_template.py` + 9 份报告 |
| **D** | D8 | cf_validation | 🔵 v2.1 已有 | `cf_validation.py` |
| **D** | D9 | 数据偏离度 | ✅ | `deviation.py` + 节点着色 |
| **D** | D10 | 端到端 | ⚠️ 3/12 类 | `v23_end2end_summary.json` |

> **统计**：已完成 ✅ 14 项，部分完成 ⚠️ 5 项（均为 3 类已做、12 类未达），未完成 ❌ 7 项，复用 🔵 1 项。

### D.2 RCA 排序算法：必须使用论文 T 矩阵算法（Algorithm 2）

**论文三步算法对应实现**：

| 论文算法 | 内容 | V2.3 实现 |
|---------|------|----------|
| Algorithm 1（图学习） | Bootstrap 边缘稳定性筛选 | `signatures.py`：f_e≥0.70 且 dir_e≥0.90 保留稳定边 |
| **Algorithm 2（量化+RCA）** | **T = (I−B)⁻¹，用总因果效应排序根因** | **`source_tracing.py::build_total_effect_matrix()` + `select_root_candidates()`** |
| Algorithm 3（验证） | 反事实验证 ΔB_pred vs ΔB_obs | `cf_validation.py`（v2.1 已有） |

**RCA 排序公式（必须遵守）**：

```
B[to, from] = weight_median(from→to)          # 直接效应矩阵（bootstrap 中位数）
T = (I − B)⁻¹                                   # 总因果效应矩阵（论文 Algorithm 2）
效应变量 E = { terminal 节点 } ∪ { marker }     # 症状/异常端
根因得分(v) = Σ_{e∈E} |T[e, v]|                 # v 对所有效应变量的总影响
排序键 = (根因得分 DESC, in_degree ASC, weighted_reach DESC, consensus_rate DESC)
```

**代码位置**：[source_tracing.py](file:///home/xczc/study/postgraduate/paperPlanAndCode/CausalTrace/Code/rca/v21/source_tracing.py) 的 `build_total_effect_matrix()` 和 `select_root_candidates()`。

**因果图展示**：排行榜"总因果效应"列直接显示 `total_effect` 字段值，节点弹窗显示 `总因果效应=xx.xxxx`。

### D.3 本次错误记录（已修正）

| # | 错误描述 | 影响 | 修正方式 |
|---|---------|------|---------|
| 1 | **RCA 排序未使用论文 T 矩阵算法，仅用图拓扑指标**（in_degree + weighted_reach + reach_size + consensus_rate） | 根因排名与论文 Algorithm 2 不符；所有 tracing_score=0（拓扑指标未转化为数值得分）；验证集 Top1 命中率仅 22.2% | 新增 `build_total_effect_matrix()` 计算 T=(I−B)⁻¹，根因得分改为候选变量对效应变量的总因果效应之和；拓扑指标降为次级排序键。修正后 Top1 命中率提升至 33.3%，得分有物理意义 |
| 2 | 因果图共识边分类漏传 `label` 字段，仅设 n_same/n_rev | 渲染逻辑按 `label=="consensus"` 过滤，导致全部边被丢弃，图只剩 5 个根节点、0 条边 | 按 `gam_graph.py` 规则补全 label：n_same≥5 且 n_same≥n_rev → consensus |
| 3 | 排行榜得分列全为 0（tracing_score 未正确计算） | 排行榜得分列无参考价值 | 改为显示 total_effect（总因果效应真实值） |
| 4 | 偏离度"严重=红"与 Top1 根因"红框"、"较大=橙"与 Top2-5"橙框"撞色 | 视觉混淆，无法区分偏离度和根因排名 | 根因排名改用**边框粗细**（Top1=粗黑框 4px，Top2-5=中框 2.5px），颜色完全留给偏离度 |
| 5 | 变量选取用了全部 99 条轨迹（含 20 条 test 轨迹），信息泄漏 | 自动变量集被 test 信息污染 | 改为仅读 `split_manifest.json` 的 train severities（79 条/类） |
| 6 | V3 VIF 阈值从方案 §3.5 的 <10 放宽至 500，指标名仍叫 V3_vif_lt_10 | "V1–V7 全通过"名不副实 | 指标名改为 V3_vif_lt_500；**V3 定稿为豁免式 pass**：只看非保护变量的 `max_vif<500`，保护变量逐项落 `v3_exempt[]` 披露（`v3_status` 四值含 `pass_with_exemption`）；`max_vif_all` 继续透明披露含保护变量真实最大 VIF；方案中留痕决策理由（核电数据物理强耦合不可行，且不采用放宽阈值到 5000 的替代口径以免与阶段3 去重双重放松） |
| 7 | V5 恒真：只校验 WHPI/WECS，未按 §4.3 校验各事故关键响应 | LOCA 选中集无 P/TAVG 但 V5 仍 True | `get_key_responses(accident_type)` 按 §4.3 返回各事故关键响应（LOCA: P/TAVG/VOL/WECS/WHPI） |
| 8 | 论文锚定首版把锚定集并入 `forced` 作为**不可剔除的保护集** | `max_vif_all` 回到 2.85e13/8.92e9/1.05e12；SGATR 出现“无非保护变量，V3 按空集视为通过”；LOCA_mid 截断比 45% 触发降级，B_CLIP 成承重墙 | 锚定改**候选优先 + 评分加权**（ANCHOR_SCORE_BONUS=0.15），可被去冗余/VIF/裁剪剔除并落 `dropped_anchor`（含 VIF、剔除前后 `max_vif_all`、变量数）；V3 改作用在实际入选集合上的 pass/N-A/fail 三态；锚定来源改称“早期机理筛选集”（commit 544f3a3），并与 v2.1 硬编码集列差异；详见 `Code/error_tracking.md` V23-DESIGN-023 |

**错误 1 是核心算法错误，必须引以为戒**：V2.3 的 RCA 模块必须使用论文 Algorithm 2 的 T=(I−B)⁻¹ 总因果效应矩阵排序，禁止退化为纯拓扑排序。



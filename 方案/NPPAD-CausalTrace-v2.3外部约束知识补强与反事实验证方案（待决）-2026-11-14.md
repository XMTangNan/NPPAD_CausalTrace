# NPPAD-CausalTrace v2.3 外部约束知识补强与反事实验证方案（提案稿·待决）

> **状态：提案，未实施。** 本文只做设计，不改代码、不改主链、不重跑、不产生任何结果数字；是否实施由用户决定。
> **前置事实**：v2.3 包 `causalrca/` 内**没有**反事实验证实现；同名代码只在 `Code/legacy/v21/cf_validation.py`。
> v2.1 与 v2.3 做的不是同一件事（见 §2.1），**v2.1 的实现与结果都不可复用**。

## 1. 外部约束知识补强

### 1.1 现状（以代码/产物为准）

| 资产 | 位置 | 现状 |
|---|---|---|
| P 系列（正向机理） | `Code/external_knowledge/positive_mechanisms.yaml` | 每条含 `id/variable/assertion/can_be_root/applicable_accidents`；参与 K2/K3 |
| S 系列（否定/响应量） | `Code/external_knowledge/supplementary_mechanisms.yaml` | 5 条，`role_in_accident=response_conduction`、`can_be_root=false` |
| 禁止边 | `Code/external_knowledge/forbidden_edges.yaml` | 只删不造，命中留痕 |
| 本体/K 层 | `causalrca/evidence/knowledge.py`（`build_ontology`、`validate_knowledge_layer`） | K1 标签 / K2 三态 / K3 模板匹配 |
| 故障模板 | `fault_templates` 相关 YAML | K3 覆盖率来源 |

**覆盖实测（2026-11-14 对照表口径）**：LOCA 的 S 条目 5 条；SGATR 的 S 条目 **0** 条；FLB 未取证。

### 1.2 问题

1. **覆盖不均**：S 系列只覆盖 LOCA；SGATR 全无 S 条目，FLB 未取证；12 类里 8 类尚无数据，知识条目也无从校验。
2. **无可追溯来源**：条目没有文献/手册引用，无法判断"这是物理约束还是实现者的直觉"。
3. **无版本与冻结**：YAML 改动不影响 run 留痕，历史 run 与当前知识不可对齐。
4. **口径未写死**：S/P 与 Top-5 的"一致/冲突"没有预注册定义，容易被事后解释。
5. **约束强度混用**：禁止边（硬）与 P/S（软）混在一层，软条目容易被当作调参依据。

### 1.3 建议方案

**A. 条目 schema（每个条目必须有）**

```yaml
id: S6 / P15
class: LOCA
variables: [RHRZ, ...]           # 涉及的变量，必须能在注册表中解析
role: response_conduction        # 或 chain_start / intermediate
assertion: not_start | starts_chain | ambiguous
direction: +1 | -1 | null        # 仅在有明确物理方向时填
source: "教材/手册章节 + 页码"    # 必填
confidence: hard | soft          # hard 只用于禁止边
reviewer: 姓名 + 日期
version: 2026-11-14
```

**B. 分层使用（不得越界）**
- 硬约束（`forbidden_edges`）：只删边、留痕；不造边、不加权。
- 软约束（P/S）：只做**披露与一致性计数**，不进入排序、不进入 K2 的数值项、不参与任何阈值调整。

**C. 覆盖门槛**：每类至少 2 条 S + 3 条 P，不足标 `knowledge_gap=true`，报告中不得据此下结论。
**D. 冻结与留痕**：`knowledge_snapshot.json` + `sha256` 写入 run manifest；知识变更必须新 run。
**E. 评审**：双人签署；有争议的条目标 `disputed`，不参与一致/冲突计数。
**F. 统计口径（预注册，写死）**
- S 条目变量落在 Top-5 → **冲突**；不在 → 不计数；
- P 条目 `assertion=starts_chain` 且落在 Top-5 → **一致**；`not_start` 且落在 Top-5 → **冲突**；
  `ambiguous` 单列，不并入一致/冲突；
- 该计数**只作披露**，不得用于调 Q、阈值、别名判据（与 §3.6 的禁止条款一致）。

### 1.4 工作量（估算）
- schema + 加载/校验改造：小时级（2–4 h，`knowledge.py`、YAML 校验、manifest 冻结）；
- 现有 3 类条目补齐（S 覆盖 FLB/SGATR、补来源）：小时级（每类 1–2 h）；
- 其余 9 类：**须先有数据**，条目编写按每类 0.5–1 天，且只能标 `unverified`。

## 2. 反事实验证（v2.3 重建）

### 2.1 为什么不能复用 v2.1

| 维度 | v2.1 | v2.3 |
|---|---|---|
| 建模对象 | 单类/少类，主窗 z 中位数截面 | 12 类 × 三分组，模板内 100 次 bootstrap |
| 尺度 | 各类各自基线 | `unified`：每模板学图窗 `[60,1500]` 池化 mean/std |
| 图 | 双引擎（LiNGAM + GAM-ANM）共识边 | LiNGAM 单引擎稳定边（`f_e≥0.70`、`dir_e≥0.90`） |
| 判据 | 5 折中位数差 + gCF 阈值 | 总效应 `(I−B)⁻¹` 排序、`B_CLIP=5` |
| 用途 | 关键边探索性比较 | 根因候选排序与机理披露 |

**结论**：v2.1 的 gCF 既不是 v2.3 的证据，也不能当 v2.3 的基线；直接搬用会把两套口径混成一个数字。

### 2.2 目标与不可主张

- **目标**：对**稳定边**给出"方向/量级是否与留出观测一致"的探索性检验，并如实标注不可判。
- **不主张**：不主张干预效应可辨识、不主张因果充分性、不用于选变量或调阈值。
- **措辞边界**：只允许写"与观测不矛盾 / 矛盾 / 不可判"，不得写"验证了因果"。

### 2.3 三档设计（逐档升级，可只做前一档）

**档 A：情形一致性（分钟级数据、0.5 天代码）**
- 对每条稳定边 `i→j`，在留出集按 `i` 的上/下四分位分组，比较 `j` 的中位数差与 `sign(T_ij)`；
- 输出 `consistent / conflict / not_applicable`（附样本量、共同支撑）；
- 纯描述，不做显著性声明。

**档 B：负对照（1–2 天）**
- 随机置换 `i` 的轨迹标签 ≥200 次，构造空分布，报告经验 p；多重比较用 BH；
- 预注册"一致率"与"空分布分位"两个指标，不调阈值。

**档 C：伪干预（3–5 天，需可识别性判定）**
- 在模板内按后门调整集做 g-formula 预测；仅当调整集在 DAG 中局部可识别时输出，
  否则 `not_identifiable`；**不做 do-演算的强主张**。

### 2.4 与现有产物的接口

- 输入：`signature_{k}_{g}.json` 的稳定边、截断后 `B`、留出矩阵；
- 输出：`cf/cf_{k}_{g}.json`（逐边三态 + 空分布 + 披露字段）；
- **不进入** `root_candidates`、不进 K2/K3、不改排序、不刷 `latest`。

### 2.5 验收（预注册，不得事后改）
- 覆盖率 = 可检验边 / 稳定边；
- 一致率与空分布 p 的中位数（按模板）；
- 跨模板方向一致性（仅描述，不做总判）；
- `not_applicable` / `not_identifiable` 必须给出原因分布。
- 失败即报失败，不允许"调阈值直到通过"。

### 2.6 主要风险
1. 严重度梯度不是干预，观测条件差 ≠ 干预效应（v2.1 已踩过）；
2. 每模板留出仅 20 条，统计功效低，`inconclusive` 会占多数；
3. 单轨迹严重度导致共同支撑差，档 B/C 可能大面积 `not_applicable`；
4. 若把结果写进排序或报告结论，会重演"用形式通过冒充可辨识性"。

### 2.7 建议顺序与工作量
| 步 | 内容 | 量级 |
|---|---|---|
| 1 | 知识 schema + 冻结 + 统计口径预注册（§1.3） | 2–4 h |
| 2 | 档 A（情形一致性）接入评测层 | 0.5 天 |
| 3 | 档 B（负对照 + BH） | 1–2 天 |
| 4 | 档 C（可识别性 + g-formula） | 3–5 天，需单独评审 |

### 2.8 待决事项（需用户拍板）
1. 是否做、做到哪一档（A / A+B / A+B+C）；
2. 是否只对 LOCA 试点，还是 3 类全铺；
3. 结果是否允许进入论文（建议：只作附录披露，不进主表）；
4. 是否接受"多数边会是 `inconclusive`"这一预期结果。

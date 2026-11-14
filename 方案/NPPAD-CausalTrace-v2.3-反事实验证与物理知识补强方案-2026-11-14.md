# NPPAD-CausalTrace v2.3 反事实验证与外部物理知识补强方案

- 日期：2026-11-14
- 状态：**方案稿，未实施**。本文件只做设计，不改代码、不改既有方案文档、不跑链路、不推送、不碰 `output/`。
- 适用范围：v2.3 事故内**变量级**溯源（候选集、目标变量、排序对象均按变量定义）。
- 不适用范围：v2.1 的事故大类/模板级任务。

---

## 0. 硬前提：v2.1 的反事实验证不能复用

**v2.1 的 `cf_validation` 不能复用。** 这不是“代码旧了所以不用”，而是**任务对象不同、语义对不上**。

### 0.1 为什么不能复用

1. **任务层级不同。**
   - v2.1 是事故大类/模板级任务：候选集、目标变量、真值口径都按“模板”组织；`cf_validation` 是在同一批观测轨迹内，按变量中位数把样本分成基线组/干预组，做 5 折留出，比较 `Δobs` 与 `Δpred` 的方向一致率和量级相关。
   - v2.3 是事故内变量级溯源：候选集由变量选取阶段产生，目标变量是症状端变量集，排序对象是候选变量到症状端的总效应，真值口径只在 D11 里以“注入变量”形式存在且已撤出结果层。
   - 两者不是同一个统计对象。把 v2.1 的模块接进 v2.3，只会得到一个名字叫“反事实”、实质却对不上的东西。

2. **v2.1 的 `cf_validation` 本身不是严格反事实。**
   - 它的实现是：`x ≤ median` 为基线组、`x > median` 为干预组；冻结模型，只对 `Δobs` 做留出；没有 do 操作、没有同初始条件配对、没有干预分布。
   - 代码位置：[legacy/v21/cf_validation.py](file:////wsl.localhost/Ubuntu/home/xczc/study/postgraduate/paperPlanAndCode/CausalTrace/Code/legacy/v21/cf_validation.py#L80-L120)、[Δobs 分组](file:////wsl.localhost/Ubuntu/home/xczc/study/postgraduate/paperPlanAndCode/CausalTrace/Code/legacy/v21/cf_validation.py#L103-L147)、[Δpred](file:////wsl.localhost/Ubuntu/home/xczc/study/postgraduate/paperPlanAndCode/CausalTrace/Code/legacy/v21/cf_validation.py#L157-L160)。
   - 方案自己已经把 Alg3 标为“形式通过”、O8 未闭环：[§2.7.2](file:////wsl.localhost/Ubuntu/home/xczc/study/postgraduate/paperPlanAndCode/CausalTrace/方案/NPPAD-CausalTrace-v2.3方案-12类全适配与变量选取机制.md#L413-L413)、[§7.6](file:////wsl.localhost/Ubuntu/home/xczc/study/postgraduate/paperPlanAndCode/CausalTrace/方案/NPPAD-CausalTrace-v2.3方案-12类全适配与变量选取机制.md#L2419-L2419)；内部版 §2.5 现已明确写“v2.3 未实现、不可复用”：[§2.5](file:////wsl.localhost/Ubuntu/home/xczc/study/postgraduate/paperPlanAndCode/CausalTrace/方案/NPPAD-CausalTrace-v2.3方案-12类全适配与变量选取机制.md#L368-L376)。

3. **v2.1 与 v2.3 的“反事实”回答的问题不同。**
   - v2.1 问的是“模板级候选变量在某组观测里是否表现出与预测一致的变化”；
   - v2.3 要问的是“在变量级图里，对某个候选变量做干预，症状端会不会变”；
   - 前者是观测一致性，后者是干预/反事实识别。名字可以相同，证据等级不同。

### 0.2 允许借鉴的边界

只允许在**概念层**借鉴“分组比较”这一思路：例如用低/中/高分档、负对照、留出轨迹做一致性检查。但必须在文档、产物和报告里同时标注：

> **这不构成 v2.3 的反事实/干预验证，只是观测数据上的一致性/预测/负对照检查。**

禁止事项：

- 禁止把 `legacy/v21/cf_validation.py` 直接 import 到 v2.3 主链；
- 禁止把它重命名、包装后当作 v2.3 的 Alg3；
- 禁止把它的输出写入 v2.3 的 `cf_validation/` 产物并据此宣称“反事实验证通过”；
- 禁止在论文/对外版中把 v2.1 的 `cf_ok` 当作 v2.3 的证据。

---

## 1. 现实约束（先写清，再谈方案）

1. **无干预数据。** 现有 NPPAD 轨迹是观测/仿真记录，不是“同初始条件、只改一个变量”的配对干预数据。没有干预数据，就不能做真正的 do 效应识别。
2. **无变量级真值。** D11 的“注入变量”只是事故注入通道标记，不是变量级根因标注；相关指标已撤出结果层。不能用它来证明变量级因果。
3. **12 类中 8 类无数据。** 只有 LOCA/SGATR/FLB 三组有可用轨迹；其余 9 个模板处于 `design_pending_data`。任何“全 12 类验证”的说法都不成立。
4. **LLM 只做文本解释。** 第八步的模型判定只读证据包，不改名次、不产生新数据；它是解释一致性审阅，不是因果检验。
5. **结论口径是“根因=传导起点”。** 这是项目自己选的定义，不是“现场确认的唯一物理起因”；对外版已写明是代理度量。
6. **本工作不主张准确率。** 因此所有新增验证都不能用“准确率提升/结果变好”做判据；只能用预先声明的结构、预测、负对照、剂量响应、覆盖率等可反驳判据。

由此得到三条总原则：

- **能做的**：披露、负对照、剂量响应、留出预测、模型内推演、知识约束的预注册与事后校验。
- **不能做的**：把观测一致性包装成干预证据；把模型内 do 当成可证伪证据；用 8 个无数据类做“全类验证”。
- **判据必须先于结果**：每一层的通过/不通过条件在跑之前冻结；跑完后不许改判据、不许换 placebo 集、不许换剂量定义、不许换留出切点。

---

## 2. 证据等级：先定“什么话能说到什么程度”

| 等级 | 名称 | 能支持的典型表述 | 不能支持 |
|---|---|---|---|
| E0 | 观测描述 | “在观测窗口中，X 的偏离度/边权为……” | 因果、干预 |
| E1 | 观测稳定性 | “在重采样下，边 X→Y 的出现比例/方向一致率为……” | 因果置信度、干预效应 |
| E2 | 观测预测 | “用前段轨迹学图，能/不能预测后段下游变量” | 因果方向、反事实 |
| E3 | 模型内推演 | “在拟合的 SCM 内，do(X) 的推演结果是……” | 可证伪证据、现实干预 |
| E4 | 准实验/负对照/剂量 | “负对照未通过/剂量响应单调/不单调” | 真干预、唯一根因 |
| E5 | 真干预/配对仿真 | “在配对仿真中，干预 X 使 Y 变化……” | 超出仿真外部效度的结论 |

**写作规则：** 报告中的每个结论必须绑定一个证据等级；E0–E4 禁止使用“证明”“确认”“导致”“反事实验证通过”等词；只有 E5 才允许写“干预效应估计”。这条规则落在 L0 的 claim ledger 里。

---

## 3. 第一部分：反事实验证与干预检验（L0–L5）

### L0 披露层：明确写“本版未做干预验证”

- **能回答的问题**：读者是否能分清“观测结构排序”与“干预/反事实确认”？报告是否在每一个结论旁标了证据等级？
- **做不到的部分**：不产生任何新证据；不验证任何因果断言；不改变排序。
- **输入**：现有报告模板、对外版、内部版、D11 指标说明。
- **输出**：
  1. 报告固定段落（每个模板、每个组都必须有）；
  2. 对外版/内部版的“解读边界”段落；
  3. `claim_ledger.json`：每条结论的 `claim_id / claim_text / evidence_level / supporting_artifact / allowed_verb / limitations`。
- **产物位置（未来）**：`output/<run_id>/validation/l0_claim_ledger.json`；报告正文固定段落；对外版 §0/§6。
- **判据（预注册，二元）**：
  - 9 个模板报告 100% 含固定段落；
  - 对外版 100% 含“未做干预验证”句；
  - `claim_ledger` 覆盖报告中所有“根因/传导/影响”类结论；
  - E0–E4 结论中不出现“证明/确认/导致/反事实验证通过”；
  - 数值产物与改前逐字节一致（本层只改文字）。
- **成本**：分钟级，纯文档/模板。
- **失败长什么样**：
  - 某个模板报告仍写“根因确认”；
  - 对外版仍暗示“反事实推理已验证”；
  - claim ledger 只覆盖 Top-1，漏掉“传导路径”类结论；
  - 改文字时顺手改了数值/排序（本层不允许）。

**建议固定句（可直接用）：**

> 本版未做干预/反事实验证。文中的“根因”按项目定义指“同类事故内最先动起来、并把影响传下去的变量”，是观测数据上的结构排序与传导起点代理，不是经干预或现场证据确认的唯一物理起因。文中的“反事实”相关能力仅为模型内推演（如启用 L4），不构成可证伪的干预证据。

### L1 负对照／placebo：与事故无关的变量是否也会得到同样“自信”的结论

- **能回答的问题**：当前排序的“自信”是事故信号带来的，还是流程对任何变量都会给出相似的 Top-1/稳定边？
- **做不到的部分**：不能证明真实变量是根因；只能暴露流程的假阳性倾向；不能替代干预。
- **输入**：
  - 9 个模板（LOCA/SGATR/FLB × 低/中/高）；
  - 预先冻结的 placebo 变量清单：事故子系统之外、且不在 K1/K2/K3 中的变量；
  - 预先冻结的随机种子（每个模板 20 次抽取）；
  - 同一套变量选取、LiNGAM、bootstrap、排序参数。
- **输出**：
  - 每次 placebo 运行的 Top-1/Top-3、稳定边数、`f_e` 分布；
  - `placebo_top1_rate`、`placebo_top3_rate`；
  - 真实触发变量与 placebo 的 Top-1 率差；
  - 时间 placebo（把事故前窗口当假事故）作为次要对照。
- **产物位置（未来）**：`output/<run_id>/validation/l1_placebo/`，含 `manifest.json`、`per_template.json`、`summary.json`。
- **判据（预注册）**：
  - 主判据：`placebo_top1_rate ≤ 10%` 且 `placebo_top3_rate ≤ 30%`；
  - 副判据：`real_trigger_top1_rate − placebo_top1_rate ≥ 30pp`；
  - 两者都过才算 `pass`；只过主判据记 `partial`；否则 `fail`。
  - 若某模板 eligible placebo 变量少于 5 个，只跑时间 placebo，并标 `partial`，不得用“变量不够”当通过理由。
- **成本**：天级；复用现有流程，不改核心算法。
- **失败长什么样**：
  - placebo 的 Top-1 率与真实触发变量接近；
  - 流程对随机变量也稳定给出同一子系统；
  - 换一批 placebo 就“通过”（说明在挑样本）。
  - 失败处置：所有“根因”结论降级为“无法与噪声区分”；不得调 placebo 集或阈值重跑。

### L2 剂量-响应：low/mid/high 三档，同一变量效应是否单调

- **能回答的问题**：同一类事故内，随着事故严重度档位升高，同一候选变量到症状端的总效应是否按预注册方向单调变化？
- **做不到的部分**：不能证明因果；剂量档位本身是观测分组，可能受其他因素混杂；单调也不等于干预有效。
- **输入**：
  - 9 个模板的 low/mid/high 分组；
  - 同一候选变量、同一症状端变量集；
  - 主指标：候选变量到症状端的 `|T|`；次指标：稳定边权重；
  - 预注册的期望方向（默认“剂量越高，效应越大”；若机理预期相反，必须提前写明）。
- **输出**：
  - 每个模板 Top-1 候选的三档效应值；
  - 严格单调（3/3 有序）/ 弱单调（Spearman ρ ≥ 0.5 且符号正确）/ 不成立；
  - 三档的置信区间或 bootstrap 分位。
- **产物位置（未来）**：`output/<run_id>/validation/l2_dose_response/`，含 `per_template.json`、`summary.json`。
- **判据（预注册）**：
  - `strong`：三档严格单调且符号与预注册方向一致；
  - `weak`：Spearman ρ ≥ 0.5 且符号正确；
  - `not_established`：其余情况；
  - 只有 `strong/weak` 才允许写“剂量响应一致”；`not_established` 必须写“未建立剂量响应”。
- **成本**：天级；分组已存在，主要是统计与产物。
- **失败长什么样**：
  - 三档效应非单调、或符号翻转；
  - 只有合并三档才显著，分档后消失；
  - 换一个效应指标就反向。
  - 失败处置：记录 `not_established`；不得改剂量定义、不得把三档合并后宣称单调。

### L3 留出轨迹预测核对：前段学图，预测后段下游走向

- **能回答的问题**：用前段轨迹学到的图，能否在后段轨迹上预测下游症状变量的走向？是否优于 persistence / shuffled-graph 基线？
- **做不到的部分**：这是观测层面的预测检验，不是干预；预测好不代表方向对；分布漂移会让预测失败。
- **输入**：
  - 每条轨迹按时间前 60% 训练、后 40% 测试（切点预注册）；
  - 训练段拟合 `B`、`cL`；测试段只用训练段信息做滚动预测；
  - 基线：persistence（用上一时刻值）、shuffled-graph（打乱边）、可选 VAR/线性自回归；
  - 指标：`SS = 1 − MSE_model / MSE_persistence`。
- **输出**：
  - 每个模板/组的 `SS`、MAE/RMSE、方向命中率；
  - 与 shuffled-graph 的对比；
  - 失败组清单。
- **产物位置（未来）**：`output/<run_id>/validation/l3_holdout_prediction/`，含 `per_template.json`、`summary.json`。
- **判据（预注册）**：
  - `pass`：`SS > 0` 在 ≥ 2/3 组成立，且 shuffled-graph 的 `SS < 0`；
  - `partial`：仅 1/3 组成立；
  - `fail`：0 组成立或 shuffled 基线不差于模型。
- **成本**：天级到周级；需要写切分/预测/基线代码。
- **失败长什么样**：
  - 预测不如 persistence；
  - shuffled-graph 与真图差不多；
  - 只有用未来信息才预测得准（泄漏）。
  - 失败处置：标 `predictive_check_failed`；不得用“图仍然可解释”替代预测判据。

### L4 模型内 do 推演：只暴露假设，不当证据

- **能回答的问题**：在拟合的 SCM 内，形式化 `do(X=x)` 会推演出什么？哪些变量是后代？推演排序与观测总效应排序是否一致？对 `B` 的微小扰动是否稳定？
- **做不到的部分**：**不可证伪**；不能当作现实干预证据；不能处理未观测混杂；不能证明方向；不能替代 L5。
- **输入**：
  - 稳定边 `B`、截距 `cL`、结构方程；
  - `do(X=x)` 的截断因子分解；
  - 预注册的扰动网格（如 `B` 的 ±10%）。
- **输出**：
  - `do_effect` 表、后代集、路径符号乘积；
  - 扰动下 Top-1 稳定性；
  - 显式字段：`evidence_level: model_internal`、`falsifiable: false`。
- **产物位置（未来）**：`output/<run_id>/validation/l4_model_do/`，含 `do_effects.json`、`sensitivity.json`、`README`（写明不可证伪）。
- **判据（预注册，只做实现一致性/稳健性）**：
  - 实现一致性：`do` 效应符号必须等于所有有向路径符号乘积之和的符号；不一致即代码 bug；
  - 稳健性：±10% `B` 扰动下 Top-1 不变比例 ≥ 80%；
  - 不满足稳健性记 `model_internal_unstable`，不得用该结果支撑排序。
- **成本**：周级；需要 SCM 推演与敏感性代码。
- **失败长什么样**：
  - `do` 效应与路径符号不一致（实现错）；
  - 轻微扰动 Top-1 翻转；
  - 报告把 `do` 结果写成“验证通过”。
  - 失败处置：修实现或标 `unstable`；禁止把 L4 写进“证据”章节。

### L5 真干预／仿真配对：只有拿到接口才可能

- **能回答的问题**：在可控仿真中，同初始条件、只改一个变量的配对干预，是否会让症状端按预测方向变化？
- **做不到的部分**：没有接口就完全做不到；即使有仿真，仍有 sim-to-real 外部效度问题；不能假设接口存在。
- **输入（先做可行性取证，不假设成立）**：
  1. 能否设置指定变量的值/注入通道？
  2. 能否保持其他变量与初始状态不变？
  3. 能否用同一随机种子重跑基线与干预？
  4. 能否读取干预后的症状端结果？
  5. 能否记录干预变量、剂量、时间、种子与版本？
- **输出**：
  - 先出 `l5_feasibility.md`（5 项逐项 yes/no + 证据）；
  - 只有 5 项全 yes，才出 `intervention/` 配对结果；
  - 否则只出 `not_available`，不得伪造。
- **产物位置（未来）**：`output/<run_id>/validation/l5_feasibility.md`；可行时 `output/<run_id>/validation/l5_intervention/`。
- **判据（预注册）**：
  - 可行性门：5 项全 yes 才进入 L5；任一 no → `not_available`；
  - 若进入：干预效应符号与模型预测一致在 ≥ 2/3 配对中成立，且 95% CI 排除 0；
  - 否则 `fail`，不得用“仿真趋势接近”替代。
- **成本**：不可估；取决于仿真接口与算力。
- **失败长什么样**：
  - 只能改变量、不能固定初始状态；
  - 同种子不可复现；
  - 干预后结果读不到；
  - 用不同初始条件硬凑“配对”。
  - 失败处置：停在 `not_available`；保留 L0 披露；不得用 L4 冒充 L5。

### L0–L5 一览

| 层 | 证据等级 | 核心问题 | 能否现在做 | 主要产物 |
|---|---|---|---|---|
| L0 | E0 | 有没有把“未做干预”说清楚 | 能，分钟级 | 报告固定段 + claim ledger |
| L1 | E4 | 流程对无关变量是否也“自信” | 能，天级 | placebo 率与分布 |
| L2 | E4 | 剂量升高时效应是否单调 | 能，天级 | 三档效应/单调性 |
| L3 | E2 | 前段学图能否预测后段 | 能，天级–周级 | 留出预测 skill score |
| L4 | E3 | 模型内 do 推演是否自洽/稳健 | 能，周级 | do 表 + 敏感性 |
| L5 | E5 | 真干预/配对仿真是否可行 | 取决于接口 | 可行性报告/配对结果 |

---

## 4. 第二部分：外部物理知识补强

### 4.1 三类约束

| 类型 | 作用位置 | 允许做什么 | 禁止做什么 |
|---|---|---|---|
| **硬禁止（hard_forbid）** | 学图前/排序前 | 删除确定不可能的边；排除确定不可能作根因的变量 | 未经专家确认就硬删；看到结果后补禁边 |
| **软先验（soft_prior）** | 边选择/排序 | 用单一全局 λ 加惩罚/奖励；做 λ 敏感性分析 | 按模板调 λ；用先验把结果“调好看” |
| **事后校验（posthoc_check）** | 学图后/报告 | K2 符号、K3 覆盖率、顺序/量级检查 | 把校验结果反馈进排序；只报通过项 |

三类约束的共同要求：**预注册、版本冻结、专家签字、全量报告（含 undecidable/缺失）**。

### 4.2 YAML 字段（建议 schema）

未来新增/迁移到 `Code/external_knowledge/constraints_v1.yaml`（本方案不创建该文件，只定义字段）：

```yaml
schema: causaltrace/knowledge_constraints/v1
constraint_version: v1
frozen_at: "2026-11-XXT00:00:00+08:00"
frozen_by: "maintainer_id"
sha256: "<frozen file hash>"
applies_to: [LOCA, SGATR, FLB]
constraints:
  - id: "K-HARD-LOCA-001"
    type: hard_forbid            # hard_forbid | soft_prior | posthoc_check
    target: edge                 # edge | root_candidate | edge_existence | edge_direction | sign | coverage | order
    scope: {accident_types: [LOCA], groups: [low, mid, high]}
    payload: {from: "P", to: "WLR"}
    rationale: "..."

    source: "FSAR/设计手册/教材页码"
    expert_signoff:
      - {expert_id: "...", role: "nuclear-safety", date: "2026-11-XX", decision: "approve", confidence: "high"}
    status: active               # active | design_only | rejected | deprecated
    falsification: "若 X→Y 在 3 个组均稳定且方向一致，则该禁边与数据冲突，需复核"
```

字段规则：

- `status: active` 才参与计算；`pending_expert` 不得写成 `active`。
- `design_only` 只用于 8 个无数据类，不计入覆盖率，不得对外宣称“已约束”。
- `source` 必须可追溯；不接受“常识”“经验”作为唯一来源。
- `falsification` 必须写“什么情况下这条约束会被数据反驳”，否则不允许 active。
- `expert_signoff` 至少包含一人；硬禁止要求两人（见 4.5）。

### 4.3 代码接口（未来实现，不在本方案实施）

| 模块 | 建议接口 | 约束类型 | 说明 |
|---|---|---|---|
| `external_knowledge.py` | `load_constraints(path) -> list[Constraint]` | 全部 | 只读冻结文件；校验 schema/scope/重复/signoff/hash |
| `external_knowledge.py` | `validate_constraints(list) -> Report` | 全部 | 缺字段、未知变量、scope 冲突、pending 混入 active 均报错 |
| `knowledge.py` | `apply_ontology_constraint(..., constraints)` | hard_forbid | 现有 marker 规则保留为 `builtin`；YAML 规则合并；单一来源 |
| `signatures.py` | `bootstrap_lingam_template(..., soft_prior, lambda_)` | soft_prior | 单一全局 λ；预注册主 λ + 敏感性网格；不按模板调 |
| `source_tracing.py` | `trace_roots(..., root_prior)` | soft_prior / hard_forbid(root) | S 的 `can_be_root=false` 走 `hard_forbid_root`；P 的根候选先验走 soft |
| `physics_constraints.py` | `check_relation(..., expected_sign, scope)` | posthoc_check | K2 扩关系/扩类；仍是事后检查 |
| `external_knowledge.py` | `match_fault_template(..., expected_chain)` | posthoc_check | K3 扩模板；只算 coverage，不硬加边 |
| `run_v23_all.py` | 新可选步骤 `constraints_preflight` | 全部 | 读冻结文件、记 hash、写 `knowledge_status`；失败即中止 |
| `verdict_report.py` / `llm_explanation.py` | 读 `knowledge_status` + L0 段 | 全部 | 报告必须披露知识状态与未做干预 |

接口原则：

- **一个知识源**：不再让 `forbidden_edges.yaml` 与 `knowledge.py` 各存一份规则；YAML 是唯一可编辑源，代码只保留 `builtin` marker 规则并显式标注。
- **一个 λ**：软先验只允许一个全局 λ；禁止按事故类/模板/组分别调。
- **一个方向**：知识只从“约束文件 → 计算”流动；不允许计算完再回写约束。

### 4.4 预注册机制（写死）

**规则：约束必须在跑之前冻结并打版本号；看到结果后调约束，视为不可引用。**

执行细则：

1. **冻结时点**：在 `run_v23_all` 启动前完成；冻结文件只读。
2. **版本号**：`constraint_version` 必须递增；每次改动生成新版本，不覆盖旧版本。
3. **哈希**：`sha256` 写入 `manifest.json`；运行前校验，不一致直接中止。
4. **不可混跑**：旧 run 的结果不能与新版本约束混引；换版本必须全链重跑，并写 `--rerun-reason`。
5. **不可事后加约束**：看到 violation/coverage 后新增、删除、改方向、改 scope，均视为不可引用。
6. **不可按模板调 λ**：主 λ 与敏感性网格在冻结文件中声明；报告必须同时给主 λ 与网格结果。
7. **全量报告**：所有 active 约束都要出现在结果里，包括 `undecidable`、`missing`、`not_applicable`；不得只报通过项。
8. **审计**：每次冻结/解冻/改版都留 `frozen_by`、时间、原因、diff 摘要。

### 4.5 专家确认流程（`pending_expert` 怎么转正）

**状态机：**

`pending_expert → expert_review → approved | rejected | needs_evidence → active | design_only | deprecated`

**转正条件：**

- **硬禁止**：至少 2 名独立领域专家（核电安全/热工水力/仪控）签字；若两人意见冲突，第三人裁决；必须给出可追溯来源（FSAR、设计手册、教材页码、技术规格书）。
- **软先验**：至少 1 名领域专家 + 1 名维护者签字；专家给方向和置信度，维护者确认可实现、scope 正确。
- **事后校验**：至少 1 名领域专家定义期望符号/覆盖率口径；1 名维护者确认实现与口径一致。

**签字字段：**

- `expert_id`（可匿名化编号）、`role`、`date`、`decision`（approve/reject/needs_evidence）、`confidence`（high/medium/low）、`source_ref`、`scope`、`notes`。
- 签字记录写入 YAML 的 `expert_signoff`，并进入只读审计日志（未来放 `Code/external_knowledge/signoff_log.yaml` 或 `方案/专家确认记录/`，本方案不创建）。

**留痕要求：**

- 每条 active 约束都能回答：谁签的、依据哪一页、适用范围、什么情况下会被数据反驳。
- 拒绝的条目保留 `rejected` 状态和理由，不得删除。
- 8 个无数据类只能标 `design_only`，不得计入 active 覆盖率。

### 4.6 与现有 K1/K2/K3 的关系（避免重复建设）

| 现有 | 定位 | 补强方式 | 不要做什么 |
|---|---|---|---|
| K1 禁边 | 已在 `knowledge.py` 硬编码 marker 规则 | 保留为 `builtin`；新增机理禁边进 YAML；`apply_ontology_constraint` 合并 | 不要把同一条规则在 YAML 和代码各写一份 |
| K2 物理关系 | 事后相关符号检查 | 扩关系、扩类、补 expected_sign；仍是 posthoc | 不要把它升级成硬边；不要让它反馈排序 |
| K3 故障模板 | 覆盖率辅助 | 扩 expected_chain、补 coverage 口径；仍是 posthoc | 不要把 expected_chain 当硬边 |
| S1–S5 否定性 | 解释层证据 | 专家确认后，`can_be_root=false` → `hard_forbid_root`；其余保留解释 | 不要未经确认就禁止变量 |
| P1–P14 正向 | 解释层证据 | 有 `asserted_edges` 的转 `soft_prior` 或 `posthoc`；无边的保留解释 | 不要把空边条目伪装成约束 |

原则：**K1 管硬禁止，K2/K3 管事后校验，S/P 转成硬/软约束；四者共用一套 YAML schema 和一个 loader。**

### 4.7 覆盖率门槛与“知识为空时不得假装有约束”

**知识状态：**

| 状态 | 定义 | 报告必须写 |
|---|---|---|
| `empty` | 0 条 active 确认约束 | “本模板无 active 知识约束，知识层未参与” |
| `insufficient` | active 约束 < 2 条，或 K3 期望链覆盖率 < 50% | “知识不足，不能宣称知识约束生效” |
| `partial` | ≥ 2 条 active，覆盖率 50%–80% | “知识部分覆盖，仅作辅助” |
| `adequate` | ≥ 2 条 active，覆盖率 ≥ 80%，且每个模板至少 1 条 hard/soft | “知识约束生效（仍需专家签字与版本号）” |

**门槛建议：**

- 每个有数据模板：至少 1 条 active 硬禁止或 2 条 active 软先验；
- 事后校验：至少覆盖 K3 期望链的 50% 边；
- 8 个无数据类：只允许 `design_only`，覆盖率记 `n/a`。

**空知识处置：**

- 知识为空时，流程照常跑，但 `knowledge_status=empty`，报告不得出现“知识约束/机理约束生效”字样；
- 不得用“默认常识”补空；
- 不得把 `pending_expert` 算作 active；
- 不得在结果不好时临时加约束（见 4.4）。

### 4.8 防作弊清单（每一条都要能审计）

- [ ] 约束文件在跑前冻结，`sha256` 进 manifest；运行前校验。
- [ ] 主 λ、敏感性网格、placebo 集、剂量定义、留出切点、扰动网格全部预注册。
- [ ] 不按模板/组调 λ；不按结果换 placebo；不按结果改剂量。
- [ ] 所有 active 约束都有专家签字与可追溯来源。
- [ ] 所有检查全量报告，含 `undecidable/missing/not_applicable`。
- [ ] 不删除失败模板；不把失败模板从汇总里剔除。
- [ ] 不用 D11 的注入标记当真值去调约束。
- [ ] 不用 L4 的模型内结果冒充 L5 的干预证据。
- [ ] 换约束版本必须全链重跑，旧结果不得与新版本混引。
- [ ] 报告同时给“约束版本 + 知识状态 + 证据等级”。

---

## 5. 第三部分：分期与代价

> 每一期的判据都必须“跑之前可声明、与结果好坏无关”。任何一期都不得用“结果变好”作为通过标准。

### P0：现在能做（文档披露，分钟级）

**内容：**

- L0：报告固定段、对外版/内部版解读边界、claim ledger schema。
- 明确写“v2.1 cf_validation 不可复用”的说明，防止后续误接。

**验收判据：**

- 9 个模板报告 100% 含固定段；
- 对外版 100% 含“未做干预验证”；
- claim ledger 覆盖所有“根因/传导/影响”结论；
- 数值产物逐字节不变。

**不通过怎么办：**

- 阻止报告发布，先修文字；
- 不允许“先发再补”；
- 不接受只改一处模板。

### P1：低风险实验（负对照、剂量-响应，天级）

**内容：**

- L1 placebo：变量 placebo + 时间 placebo，20 次/模板，预注册种子与清单；
- L2 剂量-响应：low/mid/high 三档，主指标 `|T|`，预注册方向。

**验收判据：**

- 预注册 JSON 存在且哈希冻结；
- 9 个模板都有 L1/L2 产物；
- L1 达到 `pass/partial/fail` 的明确判定；
- L2 达到 `strong/weak/not_established` 的明确判定；
- 报告按判定降级措辞。

**不通过怎么办：**

- `fail/not_established` 如实记录，不调阈值、不换样本；
- 若判定为 fail，所有“根因”表述降级为“无法与噪声区分/未建立剂量响应”；
- 只有在发现实现 bug 时才允许重跑，且必须新预注册、写 `--rerun-reason`。

### P2：需专家 + 代码（知识转正、L3/L4，周级）

**内容：**

- 知识 v1：S/P 转正、K2/K3 扩展、约束文件冻结、覆盖率门槛；
- L3 留出预测：前 60%/后 40%，persistence + shuffled 基线；
- L4 模型内 do：截断因子分解 + ±10% 扰动；
- 代码接口按 4.3 落地（本方案不实施）。

**验收判据：**

- 每条 active 约束有签字、来源、falsification；
- `constraint_version` + `sha256` 进 manifest；
- `knowledge_status` 明确为 empty/insufficient/partial/adequate；
- L3 给出 `pass/partial/fail`；
- L4 给出 `consistent/unstable`，并标 `falsifiable: false`；
- 报告不出现“知识约束生效”除非 `adequate`。

**不通过怎么办：**

- 专家拒签/证据不足 → 条目 `rejected/needs_evidence`，不得 active；
- 覆盖率不足 → `insufficient`，报告不得宣称知识约束；
- L3 fail → 标 `predictive_check_failed`，不得用可解释性替代；
- L4 unstable → 标 `model_internal_unstable`，不得用于排序。

### P3：需数据或仿真接口（L5，不可估）

**内容：**

- L5 可行性取证 5 项；
- 只有全 yes，才做配对干预；
- 否则停在 `not_available`。

**验收判据：**

- `l5_feasibility.md` 5 项逐项有证据；
- 可行时：配对协议、种子、干预剂量、结果读取、版本记录齐全；
- 不可行时：明确 `not_available`，不产出伪干预结果。

**不通过怎么办：**

- 任一可行性项 no → 停止 L5；
- 不得用 L4 代替 L5；
- 不得用不同初始条件硬凑配对；
- 保留 L0 披露，等待接口。

---

## 6. 产物与命名约定（未来实施时）

- 统一放在 `output/<run_id>/validation/` 下，不覆盖既有 `aggregation/`、`deviation/`、`evaluation/`。
- 每个产物必须带：
  - `run_id`、`lineage.source_run_id`；
  - `evidence_level`（E0–E5）；
  - `falsifiable`（true/false）；
  - `constraint_version`（如适用）；
  - `knowledge_status`（如适用）；
  - `pre_registration_sha256`。
- 建议命名：
  - `l0_claim_ledger.json`
  - `l1_placebo/{manifest,per_template,summary}.json`
  - `l2_dose_response/{per_template,summary}.json`
  - `l3_holdout_prediction/{per_template,summary}.json`
  - `l4_model_do/{do_effects,sensitivity}.json`
  - `l5_feasibility.md` / `l5_intervention/`
- 报告正文只引用 `validation/` 下的产物，不引用临时脚本输出。

---

## 7. 待登记清单（不占台账编号，由维护者登记）

以下条目需要在 `Code/error_tracking.md` 登记，但**本方案不修改该文件**，只列清单：

- [ ] v2.3 主链无 Alg3；内部版 §2.5 已修正为“v2.3 未实现”，但对外版与报告仍未披露。
- [ ] 对外版全文未披露“未做干预/反事实验证”。
- [ ] 报告局限性未写“未做干预/反事实验证”。
- [ ] `cf_fail_nodes` / `cf_template_gcf` 为无生产者的死引用。
- [ ] v2.1 `cf_validation` 不可复用已在内部版 §2.5 写明，但对外版/报告尚未同步这一边界。
- [ ] S/P 全部 `pending_expert`，无转正流程与签字记录。
- [ ] `forbidden_edges.yaml` 与 `knowledge.py` 双份规则，存在漂移风险。
- [ ] `mechanism_consistency_block()` 永久 `not_available`。
- [ ] K3 有 9 类 `design_pending_data`，覆盖率无法计算。
- [ ] L1–L5 均未实施，且无预注册文件。
- [ ] 知识约束缺少版本冻结/哈希校验/防事后调参机制。

---

## 8. 一句话总结

**先做 L0 把话说清楚；再用 L1/L2 证明流程不是对什么变量都“自信”；L3 只能证明观测预测力；L4 只能暴露模型假设；L5 没有接口就不做。知识补强同理：先冻结、再签字、再接线；没有 active 约束就老实写“知识为空”，不能把解释层的注脚说成因果约束。**

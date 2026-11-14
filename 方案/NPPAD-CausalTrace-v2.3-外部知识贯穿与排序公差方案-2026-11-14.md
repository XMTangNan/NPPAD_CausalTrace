# NPPAD-CausalTrace v2.3 外部知识贯穿与排序公差方案（修订版）

- 日期：2026-11-14
- 状态：**方案稿，未实施**。修订既有文件，不改代码、不改其它方案、不跑链路、不推送、不动 `Code/error_tracking.md`。
- 适用范围：v2.3 事故内变量级溯源（12 类注册表：3 类 `ready`=LOCA/SGATR/FLB；1 类 `partial`=LOCAC 仅 5 条轨迹；8 类 `data_missing`；见 `config/registry.py:105-215`）。
- 与反事实方案的关系：本方案只处理“外部知识/专家先验/公差如何贯穿学图与排序”；Algorithm 3 反事实不在本轮范围，且 Algorithm 3 的 `ϵ` 不是 RCA 的专家传感器公差，不能混用。

---

## 0. 依据标签：三类来源，不许互相冒充

本方案每一条设计只允许挂一个主标签。标签不是修辞，是审计口径。

| 标签 | 只能用于 | 必须附什么 | 禁止什么 |
|---|---|---|---|
| **【论文明确】** | 论文原文直接写到的内容 | 页码/节名 + 英文原句 | 把“论文没写、我们补的”挂成这个标签 |
| **【项目实测】** | 本项目已有代码、已有产物、已有实测数字 | 文件+函数+行号，或 `v23k` 的实测数字 | 把“尚未实现的设想”挂成这个标签 |
| **【本项目新设计】** | 论文没给、项目也没有现成实现，需要我们补的设计 | 明确写“论文未给/项目未实现，这是替代设计” | 把替代设计包装成论文要求或既有实现 |

**最重要的纪律：第三类必须显式说“这是我们的替代”，不能写成“论文要求”。** 如果一句话同时涉及两类，拆成两条，不要混写。

**两个反例检查：**

- “禁边提升为一等约束”是 **N1【本项目新设计】**，不是 P2【论文明确】；论文只写到本体层，没有写禁边如何进入估计。
- “`S_v=E_v=Σ_s|T[s,v]|`，知识只做门控”是 **N6【本项目新设计】**，不是 P1【论文明确】；论文只写“effect + tolerance”的组合，没有给公式，也**没有权重**。

### 0.1 依据台账（本方案共 65 条）

| # | 条目 | 依据 | 证据 |
|---|---|---|---|
| P1 | RCA 排序组合“专家公差 + 拓扑 + 传感器偏差” | 【论文明确】 | p. 40434，RCA 段原句 |
| P2 | 本体编码变量关系/运行约束；图编辑用本体校验；消融提 knowledge-informed edge validation | 【论文明确】 | p. 40434–40436 |
| P3 | 特征选择可由 domain expertise/manual selection，变量经 domain experts 验证 | 【论文明确】 | p. 40433/40435 |
| E1 | K1 禁边表 + `apply_ontology_constraint` 清零语义 | 【项目实测】 | `knowledge.py:361-401, 493-529`；`signatures.py:236` |
| E2 | v23k 注入后每 bootstrap 删边数显著上升 | 【项目实测】 | LOCA 9.47→25.17、9.83→26.41、0.05→12.78 等；`submit/外部知识补强-注入对照-v23k-2026-11-14.md:141-151` |
| E3 | v23k 注入后“不可作根因却 Top-1”明确对撞 6→4（仍有 4 处） | 【项目实测】 | 同上 `:10-11, 180-181, 212` |
| E4 | 当前排序是纯 `T` 列和；`role_prior` 默认关且不进 `root_candidates` | 【项目实测】 | `source_tracing.py:293-296, 268-278`；`run_v23_end2end.py:225`；`evaluation.py:297-301` |
| E5 | 公差只服务偏差通道，不进排序；当前是数据基线不是专家公差 | 【项目实测】 | `tolerance.py:133-233`；`deviation.py:106-166`；`signatures.py:359-362` |
| E6 | `v23k` 冻结基线 32 条/23 有出处/9 无出处；全部 `expert_confirmed=false`；断言边召回低 | 【项目实测】 | 同上 `:44-54, 84-92, 155-163` |
| E7 | `v23k` 第 8 步 `backend=rule`；D6 payload 无 `evidence_pack`/S/P；报告 1–8 节无知识节；“S/P 计数 0”是未接线；**9/9 报告尾句是“本报告由规则模板生成……”，“未经模型判断”0 命中** | 【项目实测】 | `Code/output/runs/v23k-20261114T200109/reports/rca_report_*.{json,txt}`；`submit/外部知识补强-注入对照-v23k-2026-11-14.md:125-135` |
| E8 | 现有路径语义只有三种：禁直接边、禁任何有向路径、约束先后/方向；`prior_knowledge` 只有 0/1/-1，没有跳数语义 | 【项目实测】 | `knowledge.py:361-401, 493-529`；`signatures.py:236`；lingam 1.8.2 `DirectLiNGAM(prior_knowledge=...)` |
| E9 | 当前 47 条知识支撑度：47/47 有角色+方向；25 条边级断言（pos15/neg7/trigger3）；16 条 reverse_forbidden；21 条 forbidden_outgoing；34 sourced/13 unsourced；review 33/12/2；expert_confirmed 0 | 【项目实测】 | `Code/external_knowledge/{positive,supplementary}_mechanisms.yaml`；YAML 统计（本方案 4.2.7） |
| E10 | 代码里没有任何排序权重：`trace_roots` 只按 `total_effect` 排序，无 `rank_v2`/`gate_status`/`tolerance_source`；本文件上一轮曾写“主 λ=0.5”（该文件未被 git 跟踪，**无历史版本可核**） | 【项目实测】 | `source_tracing.py:290-333`；`evaluation.py:297-301`；`git status: ?? 方案/…md` |
| E11 | 现有 D6 解释模板实测：规则稿固定 7 段（结论/证据链/跨组一致性/K2/K3/偏离度/局限），LOCA_low 正文 3174 字符，基本是“字段：数值”罗列，没有机理链叙述；`V23_SYSTEM_PROMPT` 6 条硬约束；`_build_v23_prompt` 只有 3 句指令 + 整份 JSON；模板级 9 份一律 rule，LLM 只用于 3 份类级 + 1 份全局汇总 | 【项目实测】 | `llm_explanation.py:350-361, 813-931, 1004-1012, 1096-1110`；`Code/output/runs/v23k-20261114T200109/reports/rca_report_LOCA_low.txt`（3174 字符） |
| E12 | 现有 D7 模板实测：`VERDICT_SYSTEM_PROMPT` 12 条硬约束 + 固定 JSON schema（verdicts/conclusion）+ 固定 5 节报告；校验器禁止引用 evidence pack 之外的变量/边/数值，机理陈述必须引用 K2 relation_id / K3 模板 id / S-P 条目 id；“对 Top-5 提异议”没有字段可放 | 【项目实测】 | `explanation_verdict.py:47-87, 353-361, 427-513`；`verdict_report.py:34-38`；`Code/tests/test_v23_explanation_verdict.py` |
| E13 | `prior_knowledge=0` 不是“保守删除”：合成数据（A→B，n=5000）上 `pk[B,A]=0` 使 causal_order 由 `C,A,B` 翻成 `C,B,A`，并新造 `B→A (w=+0.597)`；`softly=True` 结果相同；双向 `pk=0` 退化为无边 | 【项目实测】 | lingam 1.8.2；最小脚本见 4.2.8；`.venv/lib/python3.14/site-packages/lingam/direct_lingam.py:115-144` + `base.py:141-154` |
| E14 | 偏差通道对“饱和/缺列”变量静默跳过：`continue` 前不写任何标签；`SATURATED_VARS` 共 8 个；LOCA_low 15 个入模变量中 12 个进偏差，FLB_low 11 个 | 【项目实测】 | `deviation.py:115-120`；`tolerance.py:51`；`signature_LOCA_low.json:9-25`；`deviation_LOCA_low.json` / `deviation_FLB_low.json` 的 `levels` |
| E15 | `V23-DESIGN-084` 仍 open：σ 来源混用、`d_peak/d_p95` 聚合、分档阈值均待定；设计稿明确“不再用模板内分位数” | 【项目实测】 | `Code/error_tracking.md:1972-1982`；`方案/偏差通道统一量纲-设计-2026-11-11.md:8-19, 38-51, 89-90` |
| E16 | `v23k` 9/9 人读报告尾句为“（本报告由规则模板生成，全部数字来自同 run_id 的 v2.3 JSON 产物。）”；全文无“未经模型判断” | 【项目实测】 | `reports/rca_report_*.txt` 尾行；全目录检索 0 命中 |
| E17 | `v23k` 分析侧 S 命中 Top-5 实际为 **0–4**（9 模板：4/3/2/2/0/3/1/1/1 基线；4/4/2/2/2/2/0/0/0 注入） | 【项目实测】 | `submit/外部知识补强-注入对照-v23k-2026-11-14.md:125-135` |
| E18 | 注册表实际构成：3 `ready` + 1 `partial`（LOCAC，5 条轨迹）+ 8 `data_missing` | 【项目实测】 | `config/registry.py:105-215, 212-216` |
| E19 | `v23k` 注入口径只读 `sourced=true`，不读 `expert_confirmed` | 【项目实测】 | `work/knowledge_inject.py:80-99`（`ent.get("sourced") is not True: continue`） |
| E20 | σ 来源审计（3 类 × 15 入模变量 = 45 通道）：27 个物理下限主导、4 个实测 MAD 主导、5 个仅靠 `1%·\|μ\|` 兜底（LOCA.TAVG/VOL、SGATR.LSGA、FLB.QMGB/TAVG）、14 个应判不可评估（5 样本不足 + 8 无公差条目 + 1 饱和）、缺列 0 | 【项目实测】 | `tolerances/tolerance_{LOCA,SGATR,FLB}.json` 的 `thresholds{mu,sigma_robust,sigma_mad,sigma_floor,sigma_floor_source,saturated}`；`signature_*.json` 的 `variables` |
| E21 | **加权分支**离线复算（已被否决）：`λ=0.5` 使 Top-1 在 **7/9** 模板换人，`n_rank_changed` 9–14/15；落盘 `deviation_*.json` 只存 Top-10（覆盖 1–5/15） | 【项目实测】 | 离线脚本 `/tmp/taskB.py`（不入库）；`signatures/`、`reports/deviation_*.json`、`tracing/root_candidates_*.json` |
| E22 | **门控版**离线复算：严格门平均池 **9.89/15（65.9%）**，Top-1 换 **3/9**（SGATR_low、FLB_low、FLB_mid）；宽松门只换 1/9；135 个模板-变量中 `d>1` 只挡掉 46 个，其中 30 个是“不可评估”被连坐，真正数值 ≤1 的仅 16 个（11.9%） | 【项目实测】 | 离线脚本 `/tmp/taskB_gate.py`（不入库） |
| E23 | **LLM 链路可用**：`Code/.env` 的 `DEEPSEEK_API_KEY` 存在且非空（长度 35，只记存在性），变量名与代码期望一致；读取优先级 进程环境 `CAUSALTRACE_LLM_API_KEY` → `DEEPSEEK_API_KEY` → `Code/.env` → 仓库根 `.env`；一次最小请求返回 **HTTP 200**、用量 **8 tokens** | 【项目实测】 | 离线探针 `/tmp/llm_probe.py`（不入库）；`paths.py:85`；`llm_explanation.py:937-992` |
| E24 | **`v23k` 9/9 `backend=rule` 的真实原因**：主流程 `run_v23_all.py:378` 把 `backend="rule"` 硬编码，模板级（`run_v23_all.py:226`、`run_v23_llm_explanation.py:167`）再各写死一次，**从未发起 LLM 请求**；9 份产物 `requested_backend=rule`、`degraded=False` ⇒ 不是 key/网络/异常吞 | 【项目实测】 | `reports/llm_explanation_*.json`；`run_v23_all.py:226,378` |
| N1 | 把禁边提升为一等约束（工程判断） | 【本项目新设计】 | 论文只写到本体层，未写禁边如何进入估计；采用理由见 4.2.1 |
| N2 | 禁单边 vs 禁有向路径的判定规则 | 【本项目新设计】 | 论文未区分；本项目补规则 |
| N3 | 必须边 M1：`prior_knowledge=1` 只约束顺序，不保证直接边 | 【本项目新设计】 | 论文未给必须边方法 |
| N4 | 必须边 M2：约束回归补直接边，权重=bootstrap 中位数，标 `knowledge_imputed` | 【本项目新设计】 | 论文未给权重；这是替代 |
| N5 | 方向约束/软先验的放置：方向走路径约束，软先验只放排序 | 【本项目新设计】 | 论文未给算法 |
| N6 | 主线排序分只有效应分 `S_v=E_v=Σ_s|T[s,v]|`；**不含任何权重** | 【本项目新设计】 | 论文要求组合但未给公式；约束只改 B 与候选池，不改分数 |
| N7 | 门控关闭时 `rank_v2` 逐位复现旧 `rank` 的回归自检；**无 λ 网格** | 【本项目新设计】 | 实现自检，不是论文值 |
| N8 | `tolerance_source=data_proxy` 的诚实标注与专家公差接口 | 【本项目新设计】 | 当前无专家公差表 |
| N9 | 冻结扩展到约束/公差/公式/矩阵，哈希校验 | 【本项目新设计】 | 在 `work/knowledge_freeze.json` 基础上扩展 |
| N10 | D0–D5 分档与验收判据 | 【本项目新设计】 | 项目管理设计 |
| N11 | 失效范围表（`v23-20261112T185258`、`v23k`、`v23w-*`） | 【本项目新设计】 | 基于现有 run 结构的分析 |
| N12 | 收益的可验收形式表（哪张表哪个字段会变） | 【本项目新设计】 | 验收设计 |
| N13 | 禁止按已跑结果增删条目/调门槛；门槛 `g` 属策略文件，不属结果调参 | 【本项目新设计】 | 预注册纪律 |
| N14 | 解释层输入契约：D6 payload 与 D7 evidence pack 统一新增排序/知识/证据字段 | 【本项目新设计】 | 当前两条路径字段不一致；见 4.8 |
| N15 | 解释层禁止表述：data_proxy、禁边、knowledge_imputed、backend 的措辞边界 | 【本项目新设计】 | 防止越权归因；见 4.9 |
| N16 | rule/LLM backend 字段与措辞一致性 | 【本项目新设计】 | 规则稿兜底不得冒充 LLM；见 4.10 |
| N17 | 解释层验收：字段存在性、禁用词扫描为 0、sha256 可复现 | 【本项目新设计】 | 可核查判据，不用“效果更好”；见 4.11 |
| N18 | 多跳是 RCA 默认；边级/变量级知识都不足以逐条路径管理；判定粒度改为逐跳 | 【本项目新设计】 | 论文未给多跳语义；见 4.2.7 |
| N19 | 三层知识本体：变量角色、方向与极性、边级硬禁令 | 【本项目新设计】 | 论文只到本体层；见 4.2.7 |
| N20 | 逐跳检验：角色转移 + 极性一致 + 无禁令；缺失依据标 `unsupported`；无跳数参数 | 【本项目新设计】 | 见 4.2.7 |
| N21 | 路径三分法：`compliant` / `unsupported` / `violating`；`unsupported` 可接受但限制引用 | 【本项目新设计】 | 见 4.2.7 |
| N22 | 后置审计：从 `T` 抽取 Top-N 贡献路径逐跳判定；`path_audit.json`，不回改图 | 【本项目新设计】 | 见 4.2.7 |
| N23 | 主线只做门控：门策略（`gate_policy`）默认生效，决定候选池；门槛 `g` 随策略版本冻结 | 【本项目新设计】 | 见 4.3.2（4）（5） |
| N24 | 门控对照产物 `gate_compare_{K}_{g}.json`：`n_rank_changed`、`top1_before/after`、`top5_set_diff`、`n_excluded`；只用于报告，不改运行口径 | 【本项目新设计】 | 见 4.3.5、4.3.13 |
| N25 | 解释模板重做：Top-5 输入补全（效应/偏差/新排名/知识状态/入出边摘要/K2/K3）+ 通俗六段骨架 + LOCA 占位骨架 | 【本项目新设计】 | 见 4.13 |
| N26 | `llm_disagreement` 独立异议通道：允许对 Top-5 提异议、允许引用库外通用物理与工程常识；只进解释产物，不进排序、不改 `rank` | 【本项目新设计】 | 见 4.13.2 |
| N27 | 断言可追溯：每条断言带 `claim_id` + `evidence_ref`；指不回字段的标 `unsupported_claims`，不得当作结论 | 【本项目新设计】 | 见 4.13.4 |
| N28 | §4.9 拆为两类规则：判断自由度（允许什么）与事实表述约束（禁止什么），不再一刀切禁止 | 【本项目新设计】 | 见 4.9 |
| N29 | “先筛后排”两段式：公差先决定候选池，再对池内排序；必须输出 `candidate_pool.excluded[]`（含 `rank_if_kept`），不得物理删除 | 【本项目新设计】 | 见 4.3.8 |
| N30 | `prior_knowledge` 使用纪律：`0` 会先定序再删边，故只允许用于**预注册的单向禁边**；验收扩为“除预注册外不得有新增/翻转边” | 【本项目新设计】 | 见 4.2.8 |
| N31 | 形式化约定十则：排序量、`V`/`pool` 定义、`D_v` 的 t 窗、`None` 规则、非对称 `[lo,hi]`、同分 tie、单调性、CLI（只留 `--gate-policy`）、pk 环/冲突预检、K3 预检 | 【本项目新设计】 | 见 4.3.10 |
| N32 | 离线预实验可行性：用 `v23-20261112T185258` 的 `signature` + `tolerance` 可离线复算门控版，但需先冻结 3 条约定 | 【本项目新设计】 | 见 4.3.11 |
| N33 | σ 五桶：实测 MAD / 物理下限 / 样本不足 / 饱和 / 缺标定；优先级 `b4>b5>b3>b2>b1`；冲突取更保守桶；不可评估只记录不造数 | 【本项目新设计】 | 见 4.3.12（2） |
| N34 | 逐变量对照表（45 行）+ 分批边界（先 3 类 15 变量，再 96−45 通道）；换尺子 5 个、不可评估 14 个 | 【本项目新设计】 | 见 4.3.12（3）（4） |
| N35 | 加权分支实测（已被否决）：`λ=0.5` 在 7/9 模板换 Top-1，属 σ 副作用；**该结果不能用于评估门控口径** | 【本项目新设计】 | 见 4.3.13（3） |
| N36 | **本项目不存在 `λ`**：主线排序无可调权重；知识只改 `B`（禁边/禁路径）与候选池（公差门） | 【本项目新设计】 | 见 4.3.2（5）、4.3.7 |
| N37 | 门控版实测：严格门（`d>1` 且可评估）平均池 9.89/15，Top-1 换 3/9；旧 Top-1 被直接剔除 3 例，其中 2 例因 marker 无公差条目 | 【本项目新设计】 | 见 4.3.13（4） |
| N38 | LLM 六段正文实施口径：五处方案/代码不一致（模型名未 pin、温度/seed/超时未规定、类级/全局 LLM 产物无消费者、报告尾句无条件写规则稿、txt 正文不拼 LLM 文本）+ 改动估算（不含 D7 约 4 文件、300 行内）+ 成本（13 份/轮约 4–5 万 tokens，真正成本是非确定性） | 【本项目新设计】 | 见 4.13.7 |

**计数：论文明确 3 条（P1–P3）；项目实测 24 条（E1–E24）；本项目新设计 38 条（N1–N38）。**

---

## 1. 结论先说（三层依据各就各位）

1. **【论文明确】论文把外部知识写进了多个环节，但形式化程度不同。**
   - 特征选择允许 domain expertise/manual selection（p. 40433）；
   - 本体编码 variable relationships/operational constraints，图编辑 validated against ontology（p. 40434–40435）；
   - 消融提到 knowledge-informed edge validation（p. 40436）；
   - RCA 明确写“expert-defined, cycle-aware sensor tolerance ranges + learned causal topology”，排序依据 causal path effect strengths + sensor deviation analysis（p. 40434）。
2. **【论文明确】但论文没有把“知识如何进入 LiNGAM/DiffAN 估计”形式化，也没有给“效应 + 公差”的合成公式。** Algorithm 1 输入只有 `D, N, M`，Algorithm 2 输入只有 `B, I`；RCA 只写到组合关系。
3. **【项目实测】项目现成的硬接点只有禁边。** `apply_ontology_constraint` 在每个 bootstrap 后清零 `forbidden` 边；v23k 实测表明它确实能改变数值：LOCA 每 bootstrap 删边 9.47→25.17、9.83→26.41、0.05→12.78；SGATR 8.55–11.33→13.55–23.09；FLB 6.39–7.35→24.79–26.55（极端档 0.86→7.25）。同时，“不可作根因却 Top-1”的明确对撞从 6 处降到 4 处，仍有 4 处。
4. **【本项目新设计】综合口径：** 论文给出的是“知识贯穿”的方向和“效应 + 公差”的组合方式；项目已有实施逻辑给出“禁边”这个现成硬接点及其实测效果。两者不冲突，所以：
   - **学图侧**：沿用并强化禁边，把它提升为一等约束；必须边只做弱约束（路径级 M1，直接边 M2 仅可选且标 `citable=false`）；
   - **排序侧**：按论文把公差/偏差做成**候选池门控**——只决定“谁有资格进池”，不改分数；主线排序分只有效应分 `E_v`。**不做加权合成，论文也没给权重。**
   - **三层依据各就各位**：论文负责方向，项目实测负责硬接点与效果，新设计负责论文没给的落地细节；不互相冒充。

---

## 2. 第一步证据：论文原文到底写了什么【论文明确】

> 页码为 AAAI 正文打印页码（pp. 40432–40439）。本表全部为【论文明确】；不含解释性推断。

| # | 位置 | 英文原句（节选） | 落在哪一步 | 原文性质 |
|---|---|---|---|---|
| 1 | p. 40433，Feature Selector | “the Feature Selector enables the selection of data-driven feature selection methods or **manual selection based on domain expertise**, allowing operators to prioritize variables most relevant to the analysis.” | 特征选择 | 专家判断进入候选变量 |
| 2 | p. 40434，Causal Discovery Engine | “The engine supports … LiNGAM … and … DiffAN to construct DAGs … To enhance reliability, we integrate bootstrap-based edge stability analysis (Algorithm 1) …” | 学图 | 学图主体是 LiNGAM/DiffAN + bootstrap；**没有写知识约束如何进入估计** |
| 3 | p. 40434，Root Cause Analysis | “the RCA module **combines expert-defined, cycle-aware sensor tolerance ranges with the learned causal topology** to generate a ranked list of candidate root causes, based on **causal path effect strengths and sensor deviation analysis**.” | 排序/RCA | **明确支持“效应 + 专家公差/偏差”组合排序**；无公式、无权重 |
| 4 | p. 40434，Neurosymbolic Integration | “A dynamic process ontology … encodes process semantics, **variable relationships, and operational constraints**. Real-time Cypher queries enable on-demand retrieval of explanations, **tolerance ranges**, and sensor-function mappings.” | 知识层 | 本体含变量关系/运行约束/公差范围，但描述为检索/语义层 |
| 5 | p. 40435，Interactive UI | “Users can also interactively modify the discovered causal graph by adding or removing nodes and edges; **all edits are validated against the ontology** to ensure semantic consistency, allowing domain experts to refine and adapt the graph in real time.” | 图编辑/人机回环 | 知识用于**校验人改的图**，不是自动学图约束 |
| 6 | p. 40435，Dataset | “causal discovery was performed … on eight key variables identified through XGBoost feature selection and **validated by domain experts**.” | 特征选择 | 专家确认候选变量 |
| 7 | p. 40435，Counterfactual | “users can select an edge A→B, apply an intervention on A, and compare the predicted change in B (from the learned total effect; Algorithm 3) with observed changes in held-out data.” | 反事实 | 用总效应预测；与排序是两件事 |
| 8 | p. 40436，Algorithm 3 | “Input: Dataset D, total effect matrix τ, **tolerance ϵ**, effect threshold δ … Baseline group: rows where A ≈ a1 within ϵ …” | 反事实 | `ϵ` 是**分组匹配公差**，不是 RCA 的专家传感器公差 |
| 9 | p. 40436，Ablation | “a CausalTrace variant without KG and ontology grounding, which omits semantic tool tips, concept filtering, and **knowledge-informed edge validation**.” | 学图/图校验 | 出现“knowledge-informed edge validation”，但**没有算法、没有权重** |
| 10 | p. 40437，C3AN | “Abstraction: … mapped to high-level concepts in knowledge graph and ontology”；“Grounding: … answers used context from knowledge graph and ontology.” | 解释/评估 | 知识贯穿解释与评估 |

**边界判断（本条为分析，不是论文原句）：**

- “学图阶段用了外部知识约束”——**在系统层/本体层成立**（本体编码 operational constraints；图编辑 validated against ontology；消融提到 knowledge-informed edge validation）；**在 Algorithm 1/2 内部不成立**（原文没有写禁边/必须边/方向先验如何进入估计）。
- “排序 = T 矩阵效应 + 专家公差”——**方向成立**（RCA 原句明确写 combines tolerance ranges with topology，排序依据 causal path effect strengths + sensor deviation）；**公式不成立**（原文没有给合成公式，不能替论文补）。
- Algorithm 3 的 `ϵ` 与 RCA 的 `tolerance` **不是同一个东西**。

---

## 3. 第二步：论文要求 vs 本项目实现【项目实测】

> 本表事实列均为【项目实测】；最后一列“差异”是基于事实的判断，不冒充论文原句。

| 环节 | 论文要求（【论文明确】） | 本项目实现（【项目实测】） | 差异 |
|---|---|---|---|
| 候选变量 | 数据驱动特征选择或 manual selection based on domain expertise；变量 validated by domain experts（p. 40433/40435） | `features/variable_selector.py:568-579` 只强制“触发变量+关键响应”；`:604-605` 子系统覆盖；`ALLOWED_RELATIONS` 只做子系统分组 | 无专家确认环节；知识不决定候选集；只有工程性 forced |
| 学图：禁边 | 本体编码 operational constraints；图编辑 validated against ontology；knowledge-informed edge validation（p. 40434/40435/40436） | `evidence/knowledge.py:361-401` 只有 K1 marker 禁边；`:493-529` `apply_ontology_constraint` 只把 `forbidden` 清零；v23k 运行时追加 YAML 派生禁边 | 只做了禁边；allowed 只贴标签；没有必须边/方向先验 |
| 学图：必须边 | 论文只写“knowledge-informed edge validation”，没有写必须边如何进入估计 | `knowledge.py:429-436` `allowed` 只标记；`external_knowledge.py:391 merge_into_ontology` 只追加 allowed，且无调用方 | 没有作用点；`allowed` 不改变 `B` |
| 学图：方向 | 论文没有写方向先验算法 | `knowledge.py:334-358` 竞争边双向保留；`apply_ontology_constraint` 不纠方向 | 没有方向约束；只有 marker 外的禁边 |
| 排序：效应 | 用 causal path effect strengths；Algorithm 2 定义 `T=(I−B)⁻¹`（p. 40434/40435） | `causal/source_tracing.py:279-296` 算 `T`；`:293-296` `total_effect = Σ|T[effect,v]|`；`:316-323` 按效应 DESC + 拓扑次键 | 效应项一致（列和口径） |
| 排序：公差/偏差 | RCA combines expert-defined tolerance ranges … based on sensor deviation analysis（p. 40434） | `evidence/tolerance.py:133-233` 只做注入前 [0,100]s 基线 μ/σ；`evidence/deviation.py:106-166` 只算 `d_raw`；`causal/signatures.py:359-362` 明确“辅助证据，不改变排名” | 排序里没有公差/偏差项；公差只服务偏差通道 |
| 排序：先验 | 论文未写排序先验；RCA 只写 tolerance + topology | `config/role_prior.py:16-25` 41 项响应变量 ×0.30，默认关；`run_v23_end2end.py:225` 调 `trace_roots` 未传 `role_prior`；`evaluation.py:297-301` 评测侧才可开 | 默认不生效；且不进入 `root_candidates` 产物 |
| 解释/评审 | 知识图谱/本体支撑解释与 grounding（p. 40434/40437） | `reporting/explanation_verdict.py:211-249` S/P 进 evidence pack；`reporting/verdict_report.py:1-14` 第八步判定 | 解释层有知识，但不改排序 |

**代码事实：**

- 排序公式是纯 `T` 列和：`source_tracing.py:293-296`
  ```python
  total_effect = float(np.sum(np.abs(T[effect_idx, j])))
  tracing_score = round(total_effect, 6)
  ```
  排序键是 `(-tracing_score, in_degree, -weighted_reach, -reach_size, -consensus_rate)`（`source_tracing.py:316-323`）。
- 没有公差项：`trace_roots` 不接收 `tolerance`/`deviation`；`role_prior` 是唯一可选乘子，且默认关（`source_tracing.py:268-278`）。
- `root_candidates` 不走 `role_prior`：生产调用 `run_v23_end2end.py:225` 只传 `top_k=5`；`role_prior` 只在评测侧 `evaluation.py:297-301` 打开。
- 公差只服务偏差：`tolerance.py` 输出 μ/σ/分位数；`deviation.py` 用 `σ_robust` 算 `d_raw`；`signatures.py:359-362` 明确 auxiliary 不改排名。
- K2 也不是公差：`physics_constraints.py:142-160` 用相关系数符号做三态判定，不用 tolerance；`tolerance.py` docstring 说“供 K2”是历史口径，代码事实是 K2 不读它。
- 知识只做禁边：v23k 结论“知识只能删错边，不能补缺边”；断言边召回 LOCA 2/7、SGATR 0–1/9、FLB 0–1/5。

**结论：这是实质偏离。** 论文排序是“效应 + 专家公差/偏差”，本项目是“效应 + 拓扑次键”。不是实现细节不同，而是排序输入少了一项。

---

## 4. 第三步：方案

### 4.1 综合口径（先定三层关系）

**【本项目新设计】** 本方案的综合口径是：

- **论文明确**给出“知识贯穿”的方向和“效应 + 公差”的组合方式；
- **项目实测**给出“禁边”这个现成硬接点，以及它确实能改变数值的实测效果；
- **本项目新设计**负责论文没给的落地细节：禁边粒度、必须边权重、排序口径（门控）、冻结机制。

三者不冲突：学图侧沿用并强化禁边；排序侧按论文补公差门（筛选，不加权）；必须边只做弱约束。**不允许把新设计写成论文要求，也不允许把论文方向写成项目已实现。**

### 4.2 学图侧

#### 4.2.1 禁边为什么可以是一等约束

**（a）机制可靠性【项目实测】**

- K1 已经在主链里：`knowledge.py:361-401` 生成 `forbidden`，`signatures.py:236` 每个 bootstrap 调 `apply_ontology_constraint`，`knowledge.py:493-529` 对 `forbidden` 做 `B[i,j]=0`。
- v23k 证明禁边“真的动手”：每 bootstrap 平均删边数
  - LOCA：9.47→25.17、9.83→26.41、0.05→12.78；
  - SGATR：8.55→13.55、8.35→15.35、11.33→23.09；
  - FLB：6.39→26.55、7.35→24.79、0.86→7.25。
  （`submit/外部知识补强-注入对照-v23k-2026-11-14.md:141-151`）
- v23k 也证明禁边有边界：知识判“不可作根因却 Top-1”的明确对撞从 6 处降到 4 处，仍有 4 处；FLB_high 的改善是名义的（WSTB 未被知识覆盖）。这说明禁边能删错方向，但不能把响应量从榜首拿下来（同上 `:10-11, 180-181, 212`）。

**（b）为什么否定性知识可靠【本项目新设计】**

- “这条边不可能存在”是**可判定的领域陈述**：专家能对一条具体边给出是/否，不需要给强度。
- 删边是**保守操作**：不引入新边、不引入新权重、不改变“数据学到的其他边”；它只缩小假设空间。
- 这与“必须存在”相反：必须边要定权重、可能造边、可能把数据没支持的边硬塞进图，所以必须边天然比禁边弱。

**（c）这是工程判断，不是论文算法【本项目新设计】**

- 论文只写到本体层（operational constraints、validated against ontology、knowledge-informed edge validation），**没有写禁边如何进入 LiNGAM/DiffAN 估计**（p. 40434–40436）。
- 但项目已有 `forbidden` 硬接点，且 v23k 实测表明它可运行、可审计、可回退；因此**工程上采用，并把禁边提升为一等约束**。
- **明确：这不是论文 Algorithm 1/2 的复现，是我们的工程选择。**

#### 4.2.2 禁单边 vs 禁有向路径：判定规则【本项目新设计】

论文没有区分这两种粒度；这是本项目必须补的规则。两者语义不同，不能混用：

| 粒度 | 实现 | 语义 | 适用场景 | 风险 |
|---|---|---|---|---|
| **禁单边** | `apply_ontology_constraint` 清零 `B[i,j]` | 只禁止直接边 `i→j`，允许 `i→…→j` 间接路径 | 知识只说“不能直接驱动”，但存在保护/联锁等间接链 | 间接路径仍可能成立；删边不够 |
| **禁有向路径** | `DirectLiNGAM(prior_knowledge=0)` | 禁止 `i` 到 `j` 的任何有向路径 | 知识说“绝不能以任何路径驱动”，如响应量/指示量不能成为物理过程的上游 | 可能过强；与竞争边/反馈环冲突；需一致性检查 |

**判定规则（按顺序问）：**

> **`prior_knowledge` 方向约定（以 lingam 1.8.2 代码为准）：** `pk[target, predictor]`；`pk[i,j]=0` 表示 `j` 不能到 `i`（禁路径），`=1` 表示 `j` 到 `i` 有路径（顺序），`=-1` 未知。对 `A→B`：禁路径写 `pk[B,A]=0`，顺序约束写 `pk[B,A]=1`。docstring 的“i→j”字面容易写反，实现时以代码为准。

1. 领域陈述是“不能直接影响”还是“不能有任何影响”？
   - 只说“不能直接影响” → **禁单边**；
   - 说“不能以任何路径影响” → **禁有向路径**。
2. 是否存在合理的间接链（保护系统、联锁、控制动作）？
   - 有 → 优先**禁单边**，否则会误删间接链；
   - 没有 → 可用**禁有向路径**。
3. 对象是不是 marker/指示量/控制动作？
   - marker 外生性、指示量不得成为物理过程上游 → 优先**禁有向路径**；
   - 只是某条具体管线不能直接相连 → **禁单边**。
4. 路径级约束是否造成环/与 `COMPETITIVE_PAIRS_BY_CLASS` 冲突？
   - 是 → 降级为**禁单边**，并在报告中登记 `downgraded_from_path`；
   - 否 → 保持路径级。

**示例（只示范判定，不代替专家结论）：**

- “响应量不应直接驱动断口” → 若允许经保护系统的间接链，**禁单边**；若专家认为任何路径都不允许，**禁路径**。
- “断口/注入标记不能由下游变量驱动” → **禁路径**（marker 外生性）。
- “指示量不能成为过程变量的上游” → 优先**禁路径**；若存在真实保护联锁，则降级为**禁单边**并登记。

#### 4.2.3 禁边的落法

- **【项目实测】** 现有 K1 保留：marker 禁边继续由 `knowledge.py:361-401` 生成，`apply_ontology_constraint` 清零。
- **【项目实测】** v23k 的 YAML 派生禁边（`work/knowledge_inject.py` 的 `derive_rules`）只处理两类：`reverse_forbidden` 的反向、`forbidden_outgoing` 的出边；它已经是“禁单边”，不是路径级。
- **【本项目新设计】** 把 v23k 的运行时 wrapper 固化为主链可选步骤：
  - 新增 `constraints_v1.yaml` 冻结文件；
  - `build_ontology` 读取 `hard_forbid_edge` 和 `hard_forbid_path` 两类；
  - `hard_forbid_edge` 进 `ontology["forbidden"]`，走 `B[i,j]=0`；
  - `hard_forbid_path` 进 `prior_knowledge=0` 矩阵；对 `A→B` 写 `pk[B,A]=0`（按 lingam 代码 `pk[target,predictor]` 的方向约定）；
  - 两者都在 manifest 记录 `constraint_id`、`constraint_version`、`sha256`。
- **【本项目新设计】** 禁止把 `hard_forbid_edge` 直接写成 `prior_knowledge=0`：那会连间接路径一起禁，语义过强。这是本方案明确禁止的误用。

#### 4.2.4 必须存在的边：M1/M2 两级【本项目新设计】

论文只写“knowledge-informed edge validation”，**没有给必须边的权重、阈值或添加方式**。所以必须边比禁边弱得多：禁边只需“是/否”，必须边要定权重、可能造边。分两级：

- **M1（推荐，低风险）：路径/顺序约束**
  - 对每条 `must_exist` 边 `A→B`，设 `prior_knowledge[dst,src]=1`；
  - **方向约定（以 lingam 1.8.2 代码为准）**：`_estimate_adjacency_matrix` 用 `pk[target, predictor]` 排除 predictor，因此 `pk[i,j]` 表示 `j→i`；A→B 写 `pk[B,A]=1`，禁 A→B 写 `pk[B,A]=0`。不要按 docstring 的“i→j”字面写反；
  - **`0` 不是保守删除（E13/4.2.8）**：它先把 `i` 定在 `j` 之前，再剔除 predictor；方向写错会强制翻序并新造反向边，`softly=True` 也救不回来。
  - 只保证 A 在 B 之前，不保证直接边；
  - 拟合后统计 `present/missing/reversed`；
  - **不添加任何边、不伪造权重**。
  - 判据：`prior_knowledge=None` 时 `B` 与现实现逐元素一致（回归测试）；**除预注册删除外不得出现新增/翻转边**；`present/missing/reversed` 三计数同时报告；不允许为了“命中”而调参。

- **M2（可选，高风险）：直接边补全**
  - 仅当项目明确要求“直接边必须在图中”时启用；
  - 权重来源：对 `A→B` 做**约束回归**（`X_B ~ X_A + Pa(B)`，标准化残差化数据），取 100 次 bootstrap 的系数中位数作为 `B[dst,src]`（本项目 `B[i,j]` 表示 `j→i`）；符号必须与知识一致；
  - 控制变量 `Pa(B)`：优先用当前稳定边里 B 的父节点（排除 A）；若为空，用 `ALLOWED_RELATIONS` 中指向 B 的变量；仍为空则标 `not_estimable`，不补；
  - 冻结：回归公式、`Pa(B)` 选择规则、bootstrap 次数（100）、随机种子、符号判据（≥80% bootstrap 同号）、95% bootstrap CI 排除 0，全部预注册；不允许看到结果后改；
  - 不使用人为“最小效应量”阈值，避免新调参口；
  - 产物标记 `edge_origin=knowledge_imputed`、`citable=false`；
  - 判据：`knowledge_imputed` 边必须在报告中单列，不得混入“数据学出的稳定边”；符号不一致、CI 跨 0 或 `Pa(B)` 不可得时保持 `missing`。
  - **明确：M2 是我们的替代设计，不是论文方法；论文没有给这个权重。**

#### 4.2.5 方向约束与软先验的放置【本项目新设计】

- **方向约束**：对 `A→B`，`prior_knowledge[dst,src]=1`、`prior_knowledge[src,dst]=0`，约束路径/顺序；如果反向直接边仍被学到，再在 `apply_ontology_constraint` 清零反向直接边。论文没有写方向先验算法；这是替代。
- **软先验**：**不采用**。学图侧不做可调 shrink，排序侧也不加任何软分项——否则就是把知识变成调参通道。论文没有写软先验；本项目也不留这个口子。
- **竞争边**：两边都保持 `-1`，不设 `0/1`；与 `COMPETITIVE_PAIRS_BY_CLASS` 冲突的约束跳过并登记。

#### 4.2.6 学图侧验收判据【本项目新设计】

- `prior_knowledge=None` 时，同一 seed/数据下 `B` 与现实现逐元素一致（容差 `1e-12`）。
- `prior_knowledge` 矩阵通过一致性检查（无环、无 1/0 冲突、对角线 0）。
- 硬禁边移除清单与预注册清单逐条一致；无额外删除。
- 必须边只报 `present/missing/reversed`；`knowledge_imputed` 边数量、来源、符号全部可追溯。
- 路径级约束降级为边级时，必须写 `downgraded_from_path` 及原因。
- 任一失败 → 不进入下一步；不得调矩阵/阈值“修好”。

#### 4.2.7 路径语义：多跳是默认，不是例外【本项目新设计】

**先给结论：** RCA 的传播天生就是多跳；不能把多跳当“例外”或“边角情况”。正确的设计不是“给多跳加一个跳数上限”，而是把任意长度路径拆成**逐跳检验**：每一跳都满足“角色允许 + 极性一致 + 无禁令”，整条路径就成立；有跳缺依据就标 `unsupported`；有跳撞禁令/反向驱动就标 `violating`。**不引入跳数参数。**

**依据分布：** E8/E9 是【项目实测】；N18–N22 是【本项目新设计】；论文没有给路径长度/跳数语义。

**（1）为什么边级知识在多跳下不够用【本项目新设计】**

- **【本项目新设计】** 从组合量级看，15 变量图里长度 3–4 的简单路径可能上千条；逐条枚举三跳、四跳不可行。路径数量随图规模组合增长，这是图结构本身的性质。
- **【本项目新设计】** 边级知识能覆盖：这条直接边允不允许、这一跳的局部极性/禁令。边级知识覆盖不了：整条链的角色顺序是否合理、远距离跳是否绕过必经点、某条长路径是否整体合规。
- **【本项目新设计】** 变量级知识能覆盖：变量的角色（驱动/响应/后果/观测）和全局方向倾向；覆盖不了：具体某一跳的极性和禁令。
- **结论：** 边级 + 变量级都不足以“逐条路径”管理多跳。必须把路径拆成跳，逐跳判定，并且只对“贡献大的路径”做审计。**多跳不是例外，多跳是默认，所以判定粒度必须是“跳”，不是“整条路径”。**

**（2）三层知识本体【本项目新设计】**

| 层 | 字段 | 回答什么 | 现有条目实际支撑 | 覆盖不了什么 |
|---|---|---|---|---|
| **变量角色层** | `role`: `driver` / `response` / `consequence` / `observation`；`can_be_root`；`scope` | 这个变量在事故链里是驱动量、响应量、后果量还是观测量？ | 47/47 条有 `role_in_accident`；三类唯一变量覆盖 15/15、15/15、17/17 | 原始角色有 6 种（`injection/response_conduction/response_indicator/response_state/response_control/response`），到 4 类规范角色的映射是**草案**，需专家确认；变量级角色不能替代边级证据 |
| **方向与极性层** | `expected_direction`: `pos/neg/mixed`；`asserted_edges[].sign`: `pos/neg/trigger`；`reverse_forbidden` | 谁驱动谁？同向还是反向？能不能反向驱动？ | 47/47 条有 `expected_direction`（pos 23 / neg 12 / mixed 12）；15 条有 `asserted_edges`，共 25 条边（pos 15 / neg 7 / trigger 3）；16 条边 `reverse_forbidden=true` | 只有 15/47 条有边级 sign；大多数跳没有边级极性证据；`mixed` 不能当同向/反向 |
| **边级硬禁令层** | `forbidden_outgoing`、`asserted_edges[].reverse_forbidden`、K1 marker 规则、`constraints_v1` | 这条直接边/这条有向路径绝对不允许？ | 21/47 条有非空 `forbidden_outgoing`；K1 marker 规则；16 条边 `reverse_forbidden=true` | 不是所有边都有禁令；没有禁令 ≠ 允许，只能算“未禁止/无依据” |

**层内判定规则（草案，需专家确认）：**

- **角色转移矩阵**（`A→B` 是否允许）：
  - `driver → response/consequence`：允许；
  - `response → response/consequence/observation`：允许（响应传导、响应被观测）；
  - `observation → 任何`：**不允许**（观测量不能驱动过程）；
  - `consequence → 任何`：**不允许**（后果量不应再驱动上游）；
  - `response/consequence/observation → driver`：**不允许**（反向驱动）；
  - `driver → driver`：默认不允许（除非专家明确“控制动作驱动另一驱动量”）。
- **极性规则**：
  - 有边级 `sign` 且与两端 `expected_direction` 不冲突：该跳极性通过；
  - 有边级 `sign` 但与 `expected_direction` 冲突：`violating`；
  - 没有边级 `sign`，但两端 `expected_direction` 已知且非 `mixed`：只能算**弱一致**，标 `unsupported`，不能算 `compliant`；
  - `mixed` 或缺失：`unsupported`。
- **禁令规则**：
  - 命中 `forbidden_outgoing`、K1 marker 禁边、`constraints_v1` 的禁边/禁路径：`violating`；
  - 命中 `reverse_forbidden` 且方向相反：`violating`；
  - 没有禁令记录：**不等于允许**，只能算“未禁止”；若角色/极性也缺，则整条路径 `unsupported`。

**（3）多跳约束 = 任意长度路径的逐跳检验，不引入跳数参数【本项目新设计】**

对一条路径 `v0→v1→…→vk`，逐跳检查：

1. **禁令检查**：`vi→vi+1` 是否在禁直接边/禁路径/`reverse_forbidden` 里？命中 → `violating`。
2. **角色检查**：`role(vi) → role(vi+1)` 是否在角色转移矩阵允许？不允许 → `violating`；角色缺失/映射不确定 → `unsupported`。
3. **极性检查**：`sign(vi,vi+1)` 是否与两端 `expected_direction` 一致？冲突 → `violating`；缺失/`mixed` → `unsupported`。

**路径判定：**

- 任意一跳 `violating` → 整条路径 `violating`；
- 没有 `violating`，且每一跳都有角色+极性+禁令依据 → `compliant`；
- 没有 `violating`，但至少一跳缺依据 → `unsupported`。

**关键点：路径长度 `k` 不进入判定规则。** 2 跳和 10 跳用同一套逐跳规则；`k` 只作为输出字段（`length`/`path_length_hist`）和计算上限，不作为“允许/禁止”的参数。因此：
- “三跳以内允许、四跳禁止”仍然**表达不了**，也不需要表达——如果每一跳都合规，10 跳也合规；如果某一跳违规，2 跳也违规。
- 如果业务上确实要“最多三跳”，那是**策略上限/计算上限**，不是物理知识；只能放在后置审计/排序惩罚里，不能伪装成 `prior_knowledge` 的语义。

**（4）路径判定三分法【本项目新设计】**

| 结果 | 条件 | 字段 | 报告怎么写 | 可引用性 |
|---|---|---|---|---|
| **`compliant`** | 每一跳都有角色+极性+禁令依据，且无违反 | `path_audit.paths[].result="compliant"`；`hop_results[].evidence_status="supported"` | “该路径逐跳有知识依据” | 可作为“知识支持路径”引用 |
| **`unsupported`** | 未撞禁令，但至少一跳缺角色/极性/禁令依据 | `result="unsupported"`；`missing_hop_index`；`missing_reason` | “该路径未违反知识，但第 i 跳无依据；按不违反即可接受处理” | **限制引用**：只能说“未被知识反驳”，不能说“知识支持/机理一致” |
| **`violating`** | 撞禁边/禁路径/反向驱动/极性冲突 | `result="violating"`；`violation_type`；`constraint_id`；`source_entry_id` | “知识约束要求 X，图中该路径仍出现 Y” | **不可作为支持**；若它是某根因的 Top 路径，报告 `path_conflict` 并限制该根因的强断言 |

**处理原则：**

- `unsupported` 按“**不违反即可接受**”处理，但必须标记并限制引用；不能把“没被禁止”写成“知识支持”。
- `violating` 不接受为支持；只报冲突，不回改图、不回写 `B`/`T`/`rank`。
- 一条路径的 `result` 与它的 `contribution` 分开：`contribution` 是 `T` 的数值贡献，`result` 是知识判定；两者不能互相替代。

**（5）后置审计：从 `T=(I−B)⁻¹` 抽取贡献最大的路径，逐跳判定【本项目新设计】**

- **【项目实测】** `T=(I−B)⁻¹ = I+B+B²+…`；`T[s,v]` 是所有长度路径贡献的代数和。路径贡献 `C(path)=∏ B_edges`；`T` 是这些 `C(path)` 的和。
- **【本项目新设计】** 图生成后，在 `B` 的支持集上枚举简单路径，按 `|C(path)|` 排序，取 Top-N（建议 20）作为“贡献最大的路径”，再加所有命中硬禁令的路径；对每条路径逐跳做上面的三项检查。
- **枚举上限**：`max_paths`（建议 1000）、`max_hops`（建议 8）只是计算上限，超过写 `truncated=true`；它们**不是**“允许几跳”的物理约束。
- **输出 `path_audit.json`**（放在每个模板的签名目录下）：

| 字段 | 含义 |
|---|---|
| `template` / `group` / `run_id` | 归属 |
| `paths[]` | 被审计的 Top-N 路径 + 违规路径 |
| `paths[].path_id` / `nodes` / `length` | 路径标识、节点序列、跳数 |
| `paths[].contribution` / `abs_contribution` / `contribution_rank` | `T` 的路径贡献、绝对值、贡献排名 |
| `paths[].result` | `compliant` / `unsupported` / `violating` |
| `paths[].hop_results[]` | 每一跳的 `from/to`、`forbidden_check`、`role_check`、`polarity_check`、`evidence_status`、`source_refs`、`reason` |
| `paths[].missing_hop_index` / `missing_reason` | `unsupported` 时缺依据的跳与原因 |
| `paths[].violation_type` / `constraint_id` / `source_entry_id` | `violating` 时对应约束与来源 |
| `counts` | `compliant/unsupported/violating` 计数 |
| `truncated` / `max_paths` / `max_hops` | 是否截断、计算上限 |
| `enumeration_method` | 路径枚举方法/版本 |

**报告呈现：**

- 新增“多跳路径审计”一节：Top 路径表（路径、长度、贡献、结果、原因）、三分法计数、违规清单。
- 违规只报“约束冲突”，不回改图；若某根因的 Top 路径是 `violating`，报告 `path_conflict`，该根因的“知识支持”表述必须降级。
- `unsupported` 路径可以出现在报告里，但必须标“未违反/无依据”，不能写成“知识支持”。

**（6）现状：现有条目到底能支撑到哪一层【项目实测】**

> 以下按当前 HEAD 的 47 条（P14 + S33）计数；`v23k` 冻结基线是 32 条，见 §4.7 的口径说明。

| 项 | 当前数量 | 能支撑什么 | 不能支撑什么 |
|---|---|---|---|
| 总条目 | 47 条（P 14 + S 33） | 变量级角色/方向覆盖 | 不等于边级证据覆盖 |
| 角色 | 47/47 有 `role_in_accident`；三类唯一变量 15/15、15/15、17/17 | 变量角色层 | 6 种原始角色 → 4 类规范角色是草案，需专家确认 |
| 方向 | 47/47 有 `expected_direction`（pos 23 / neg 12 / mixed 12） | 变量级方向倾向 | 变量级方向不是边级极性；`mixed` 不能判定 |
| 边级断言 | 15/47 条有 `asserted_edges`，共 25 条边（LOCA 9 / SGATR 9 / FLB 7）；pos 15 / neg 7 / trigger 3；22/25 有出处 | 部分跳的极性/方向 | 大多数跳没有边级 sign → `unsupported` |
| 反向禁令 | 16 条边 `reverse_forbidden=true` | 部分反向驱动违规 | 不是所有边都有 |
| 出边禁令 | 21/47 条有非空 `forbidden_outgoing` | 硬禁令层的主力 | 不是所有变量/边都有 |
| 审核 | `expert_confirmed=false` 47/47；review accepted 33 / caveat 12 / rejected 2；sourced 34 / unsourced 13 | AI 预审可做 | 不能算专家确认；不能把 AI 预审当 active |

**结论：变量角色层“覆盖得全”，边级极性层“很稀”，硬禁令层“最可用但也不完整”。**

- 设计上可行：逐跳检验不需要新数据接口，只需要把现有 `role_in_accident`、`expected_direction`、`asserted_edges.sign`、`reverse_forbidden`、`forbidden_outgoing` 读出来。
- 条目上做不到“全路径 compliant”：25 条边级断言覆盖不了任意多跳路径；大多数跳会是 `unsupported`。
- 因此，当前只能承诺：
  - 硬禁令/反向驱动可以判 `violating`；
  - 有边级断言的跳可以判 `compliant`；
  - 其余跳必须标 `unsupported`，按“不违反即可接受”处理，但**不能引用为“知识支持”**。
- 如果要把 `unsupported` 降下来，必须补**边级 sign + 角色转移**；只加变量级条目不够。**不要用“设计上可行”掩盖“条目上做不到”。**

**（7）和原有三种语义的关系**

- 原有三种语义仍然有效：禁直接边（`B[i,j]=0`）、禁任何有向路径（`prior_knowledge=0`）、约束先后/方向（`prior_knowledge=1`）；它们都**没有跳数语义**（E8）。
- 本节新增的不是“第四种矩阵语义”，而是**图生成后的逐跳审计**：不改变 `prior_knowledge` 的 0/1/-1，不引入 `max_hops` 约束，只把路径拆成跳来判定。
- “必经中转”仍然可以表达为“禁直接边 + `must_exist_path.via` + 后置路径检查”；它不是跳数，是节点约束。
- 通俗一句话：**不要问“几跳”，要问“这条路径的每一跳，角色、极性、禁令是否都说得清”。说得清 = `compliant`；没说清但没违反 = `unsupported`；撞禁令/反向 = `violating`。多跳是默认，长度只记录，不作为约束参数。**

#### 4.2.8 `prior_knowledge=0` 不是“保守删除”：最小复现【项目实测】

**设置**：合成数据 `n=5000`，真实结构 `A→B`（系数 1.5），`C` 独立；`lingam 1.8.2`；未知项填 `-1`（填 `NaN` 会直接抛 `Input contains NaN`）。

| 先验设置 | `causal_order` | 输出边 |
|---|---|---|
| 无先验 | `C, A, B` | `A→B (+1.507)` |
| `pk[A,B]=0`（禁 `B→A`，方向正确） | `C, A, B` | `A→B (+1.507)`，不变 |
| `pk[B,A]=0`（禁 `A→B`） | **`C, B, A`** | **`B→A (+0.597)`**，翻转并新造反向边 |
| `pk[B,A]=1`（要求 `A→B`） | `C, A, B` | `A→B (+1.507)` |
| 双向 `pk=0` | `C, A, B` | 无边（退化为 mask） |
| `pk[B,A]=0` + `softly=True` | **`C, B, A`** | **`B→A (+0.597)`**，softly 不救 |
| `pk[A,B]=1` 与 `pk[B,A]=0` 同时给 | `C, B, A` | `B→A (+0.599)`，**冲突不报错、静默取胜于 0** |
| `pk[A,B]=1` 与 `pk[B,A]=1` | — | 抛 `ValueError: prior knowledge contains inconsistencies` |

**机制**：`direct_lingam.py:115-144` 把 `pk==0` 与 `pk==1` **一起**抽成 partial order（`no_path_pairs` 也进排序），`base.py:141-154` 只是**定序之后**再把 predictor 从回归里剔除。所以 `0` 的语义是“**i 必须先于 j，且 j 不作 i 的 predictor**”，不是“删掉 j→i、其余不动”。方向一写错，先被强制翻序，再在翻过来的序上回归，于是造出反向边。

**结论（写进纪律，N30）**：

1. `0` 只用于**预注册、单条、方向已人工核对**的禁边；禁止批量生成。
2. `0` 与 `1` 不得指向同一对变量：库不报错，且会静默给出反向边。
3. “双向 `0` 才等价于只删直接边”；单向 `0` **不是**保守操作。
4. 验收从“删除清单一致”扩为：**除预注册删除外，不得出现任何新增边或方向翻转**；`present/missing/reversed` 三个计数必须同时报告。
5. 生成 `pk` 前必须对 K3 `expected_chain` 与 `asserted_edges` 做预检：K3 期望 `A→B` 的边，不得同时写 `pk[B,A]=0`。

### 4.3 排序侧：效应 + 专家公差/偏差

#### 4.3.1 论文口径与项目现状

- **【论文明确】** 论文要求 `ranked list = f(causal path effect strengths, sensor deviation analysis, expert-defined tolerance ranges)`；**没有给 f**（p. 40434）。
- **【项目实测】** 项目现状是 `rank = sort(Σ|T[effect,v]| DESC, 拓扑次键)`；没有 tolerance/deviation 项（`source_tracing.py:293-296, 316-323`）。
- **【本项目新设计】** 因此，下面公式是“论文要求组合、但未给公式”的替代设计，**不是论文公式**。

#### 4.3.2 替代公式（逐项标依据）

对每个模板 `(k,g)`、每个候选变量 `v`：

**（1）效应项【项目实测】**

```
E_v = Σ_{s∈S} |T[s, v]|
```

- `S`：症状端变量集 = 出度为 0 的 terminal 节点 + marker（沿用 `source_tracing.py:283-289`）。
- `T = (I − B)^{-1}`，`B` 为稳定边重建的系数矩阵（沿用 `build_total_effect_matrix`）。
- 数据支撑：有，现成 `total_effect`。

**（2）偏差量 `D_v`：只做门的输入，不做分数【项目实测 + 本项目新设计】**

```
D_v = max_t |x_v(t) − μ_v| / σ_robust(v)
```

- **【项目实测】** `μ_v`：注入前 [0,100]s 共 11 点基线均值（`tolerance.py:45-48`）；`d_raw` 已在 `deviation.py:144-146` 实现；聚合方式是全程 `max_t`（不是 p95）。
- **【项目实测】** 当前分母只能是 `σ_robust(v) = max(1.4826·MAD, σ_floor(v))`（`tolerance.py:168-171`），标 `tolerance_source=data_proxy`；`v23k` 中 87/93 个通道取到了 `σ_floor`（fallback 41、physical 46）。
- **【本项目新设计】** 首选 `tol_expert(v, cycle_state)`（论文口径）；当前没有专家表，不能写成 `expert`。
- **【项目实测】** 不得把 `tolerance_overrides.yaml` 的 `σ_floor` 称作“专家公差”；它只是 σ 下限。
- **【项目实测】** 饱和/缺列通道在 `deviation.py:115-120` 被静默 `continue`，**不写任何标签**；`SATURATED_VARS` 共 8 个（WLR/WECS/WHPI/WFWA/WSTA/WFLB/WTRA/WTRB），LOCA_low 15 个入模变量只有 12 个进偏差、FLB_low 只有 11 个。
- **【本项目新设计】** `D_v=None` 时写 `gate_status=not_evaluable`；**它不参与打分**（主线没有偏差分），只由门控策略决定“进池/不进池”，见（4）。

**（3）不做归一化【本项目新设计】**

主线分数只有 `E_v` 一个量，且比较只在同一模板内进行，所以**不需要百分位、不需要平局秩、不需要量纲统一**；`R_E`/`R_D` 两个符号从主线删除。旧方案里的百分位归一化只保留在附录 B（加权分支）里作为“已被否决的做法”。

**（4）候选池公差门（门控，不是权重）【本项目新设计】**

```
pool = { v ∈ V : gate_status(v) = pass }
gate_status(v) = pass   if D_v 可评估 且 D_v > g
              = fail   if D_v 可评估 且 D_v ≤ g
              = not_evaluable  otherwise
```

- `g` 是**策略参数**（默认 1，随策略版本冻结），它只回答“谁有资格进候选池”，**不改变任何变量的分数**。
- 出处：论文只写 sensor deviation analysis（p.40434），**没有阈值**；`1` 是本项目自定。
- 风险：`d_raw` 到 1.67e5 量级，`g=1` 可能几乎不收缩池子；实际池大小见 4.3.13 门控版。
- `not_evaluable` 的处置必须二选一并冻结：**保守（默认）= 不进池**；宽松 = 进池但标 `gate_status=not_evaluable`。两者都要在报告里列出名单。
- 禁边/禁路径不在这里：它们已经在学图阶段改了 `B`（见 4.2），所以排序看到的 `T` 已经是“约束后的图”。

**（5）主线排序分：只有效应分【本项目新设计】**

```
S_v = E_v = Σ_{s∈S} |T[s, v]|
rank_v2 = sort(S_v DESC,
               in_degree ASC, weighted_reach DESC, reach_size DESC,
               consensus_rate DESC, variable ASC)
```

- **本项目不存在 `λ` 这个参数**；主线排序没有任何权重、没有合成公式、没有可调旋钮。
- 外部知识只通过两条约束起作用：① 禁边/禁路径改 `B`（4.2）；② 公差门改候选池（上一条）。
- 被门控排除的变量**不消失**：仍留在 `root_candidates_all.json` 里，标 `in_pool=false` 与 `gate_status`。
- 有传导能力优先、孤立节点殿后：沿用 `source_tracing.py:325-329`。

**（6）输出字段【本项目新设计】**

- 保留：`total_effect`、`tracing_score`、旧 `rank`（旧口径，供对照）。
- 新增：`rank_v2`、`in_pool`、`gate_status`、`gate_rule_id`、`deviation_raw`、`tolerance_source`、`pool_size`、`n_excluded`。
- **删除**：`lambda`、`ranking_score_v2`、`ranking_mode`、`effect_rank`、`deviation_rank`、`anomaly_gate`、`ranking_compare` 作为运行档——这些属于附录 B 的加权分支，不进主线产物。
- 门控版对照（门控 vs 旧全量）只生成一份 `gate_compare_{K}_{g}.json`，用于报告，不改变运行口径。

#### 4.3.3 收益的可验收形式（哪张表哪个字段会变）【本项目新设计】

| 设计 | 产物/字段 | 接入后应看到什么 | 验收判据 |
|---|---|---|---|
| 禁单边 | `signatures/*.json` 稳定边表 | 每个 bootstrap 的 `forbidden` 清零边数增加；`knowledge_injection_applied.json` 的 `applied_by_class` 增加 | 删边清单与冻结条目逐条一致；无额外删除 |
| 禁路径 | `B`/`T`、`prior_matrix_v1.json` | `B` 中禁止路径对应的系数为 0；`prior_knowledge` 矩阵有 `0` 标记 | 矩阵一致性通过；无环；降级有记录 |
| 必须边 M1 | 新增 `knowledge_prior.json` | 每模板出现 `present/missing/reversed` 计数 | `missing` 全报；无伪造边 |
| 必须边 M2 | `signatures/*.json`、`B` | 新增 `edge_origin=knowledge_imputed` 行；`B[dst,src]` 非零 | 全部单列；`citable=false`；符号/CI 判据通过 |
| 方向约束 | `knowledge_prior.json`、`B` | 反向直接边被清零；`B`/`T` 变化 | 无反向直接边；无 1/0 冲突 |
| 后置路径审计 | `path_audit.json` | 新增 `path_length_hist`/`max_hops`/`truncated`/`violations`；`B`/`T`/`rank` 不被改写 | 字段齐全；违规计数与冻结约束一致；`truncated` 明确 |
| 软先验（未采用） | — | 不进主线；如需研究只在附录 B 离线做 | 主线产物不得出现 `soft_prior_score` |
| 主线排序（纯效应分 + 门控） | `root_candidates/*.json`、`gate_compare_{K}_{g}.json` | 新增 `rank_v2`、`in_pool`、`gate_status`、`gate_rule_id`、`deviation_raw`、`tolerance_source`、`pool_size`、`n_excluded`；**不出现任何权重字段** | `S_v=E_v` 逐变量可复算；`pool` 与 `gate_status` 逐条一致；被剔除者仍在 `root_candidates_all.json` 且可反查；无 `lambda` 字段 |
| 专家公差 | `tolerance_expert_v1.yaml`、`deviation` | `tolerance_source` 从 `data_proxy` 变为 `expert`；门的开合改变 | 15 变量 × 3 类全部有 source+signoff；无 data_proxy 混入 |
| 冻结 | `manifest.json`、`knowledge_freeze.json` | 新增 `constraint_version`、`sha256`、`gate_policy_version` | 运行前哈希校验；不一致中止 |

**注意：** 这些是“字段/表的可观察变化”，不是“结果更好”。判据在跑之前冻结；跑完后不许用“Top-1 变了/更符合物理”作为通过标准。

#### 4.3.4 与现有“列和排序”的差异与可比性

| 项 | 现有【项目实测】 | 新口径【本项目新设计】 |
|---|---|---|
| 图/B/T | 不变 | 不变（排序侧不改图） |
| 主项 | `E_v` 绝对值 | `E_v` 绝对值（**不变**） |
| 公差/偏差 | 无 | 只做候选池门控，**不进分数** |
| 权重 | 无 | **无。本项目不存在 λ** |
| 次键 | 拓扑四键 + 有出度优先 | 同左（逐位一致） |
| 可比性 | `v23-20261112T185258` 基线 | 门控后候选池变化 → Top-5 可能变；图与 T 可复用，排名不可逐位比 |
| 回退 | — | 关闭门控（`gate_policy=off`）时 `rank_v2` 必须逐位复现现有 `rank`（回归自检） |

#### 4.3.5 排序侧验收判据【本项目新设计】

- **默认策略自检**：`gate_policy` 冻结版本生效（默认按超差收缩候选池）；`gate_policy=off` 只用于回归自检，不作为运行默认。
- **实现自检**：`gate_policy=off` 且关闭 `role_prior` 时，`rank_v2` 与现有 `rank` 逐位一致；`S_v=E_v` 与旧 `total_effect` 六位小数一致。
- **公式自检**：`S_v = Σ_s |T[s,v]|` 逐变量可复算；**不存在权重项，因此没有“λ 可复算”这条**。
- **门控自检**：`pool` 与逐变量 `gate_status` 必须一一对应；`n_excluded` 等于 `15 − pool_size`；被剔除者必须能在 `root_candidates_all.json` 反查（`in_pool=false`）。
- **对照自检**（门控 vs 全量）：`gate_compare_{K}_{g}.json` 的 `n_rank_changed`、`top1_before/after`、`top5_set_diff` 可由两套产物复算一致；池子完全相同也要写 0。
- **数据自检**：`tolerance_source` 逐变量标注；`data_proxy` 不得写成 `expert`；`not_evaluable` 的处置必须与冻结策略一致（保守=不进池）。
- **失败处置**：任一自检失败 → 不发布排序；修实现，**不调门槛去凑结果**。

#### 4.3.6 专家公差要补什么【项目实测 + 本项目新设计】

- **【项目实测】** 当前 `tolerance.py` 是数据驱动基线，不是论文的“expert-defined, cycle-aware sensor tolerance”。
- **【本项目新设计】** 要真正对齐论文，需要 `tolerance_expert.yaml`：
  - 逐变量、逐运行状态/循环阶段的 `[lo, hi]` 或 `tol`；
  - 来源：设计手册/FSAR/运行规程页码；
  - 签字：领域专家 + 维护者；
  - 当前 NPPAD 是否有 21 个运行状态标签：**未取证**；若没有，cycle-aware 只能先做“注入前/注入后”两段，不能假装 21 状态。
- 在专家表到位前，公差门只能用 `data_proxy`，报告必须写“公差为数据代理，不是专家公差”。

#### 4.3.7 附录 B：未采用的方案——效应与偏差加权合成【本项目新设计】

> **状态：已否决，仅留档。** 主线排序不存在该公式，也不提供开关。

**为什么不采用（四条）：**

1. **引入可调系数**：`λ` 一旦存在，就成了第四个旋钮（禁边、门槛、σ 之外），排名变化再也说不清是知识带来的还是系数带来的。
2. **增益不可归因**：公差侧仍是 `data_proxy`，偏差排名被 σ 来源主导（4.3.12 已证明 5 个通道的尺子是 `1%·|μ|`），加权只会把噪声放大成“证据”。
3. **论文没给权重**：原文只说“tolerance ranges + topology → ranked list, based on path effect + sensor deviation”（p.40434），没有公式、没有系数；给它补一个 0.5 是我们编的。
4. **筛选口径更可追溯**：门控会留下“谁被剔除、为什么”的清单，加权只能留下一个分差；前者可反查，后者不可。

**已删除的内容**：`S_v=(1−λ)R_E+λR_D·A_v`、λ 网格、主档 0.5、对照档 0、`ranking_mode`、`ranking_score_v2`、`anomaly_gate`、`ranking_compare`（作为运行档）。这些只在 4.3.13 的“加权分支实测”里作为**被否决方案的证据**出现，**不作为可选实施项**。

**一句话**：本项目**不存在 λ 这个参数**；主线排序无可调权重。

#### 4.3.8 主线路线：先筛后排（两段式）【本项目新设计】

**先核论文口径（见 2 节）**：论文只有“tolerance ranges + topology → ranked list，依据 path effect 与 sensor deviation”（p.40434），**没有**“筛”或“加权”的区分；`ϵ` 只在 Algorithm 3 做分组匹配（p.40436）。所以“先筛后排”是我们的实现选择，不能写成论文方法。

**主线定义（已定，不再是 A/B 选项）**：

1. **筛**：逐变量算 `D_v`，按冻结的门策略判定 `gate_status`；`fail` 不进候选池；
2. **排**：池内**只按 `E_v`** 排，次键沿用现有拓扑序；**没有任何权重**；
3. 产物 `candidate_pool`：`n_total/n_kept/n_excluded/policy_version/gate_rule_id/tolerance_source`；`excluded[]` 每条带 `variable`、`gate_status`、`gate_reason`、`d_raw`、`mu`、`sigma_robust`、`sigma_floor_source`、`tolerance_entry_id`、`rank_if_kept`；
4. 全量候选另存 `root_candidates_all.json`，被剔除者 `in_pool=false`，**不物理删除**；报告固定加“候选池与剔除清单”一节。

| 维度 | 主线：先筛后排 | 附录 B：加权（已否决） |
|---|---|---|
| 候选池 | 缩小（15 → M） | 不变（15） |
| 名次 | 被剔除者不可能进 Top-5 | 所有候选仍在，只是顺序变 |
| 可复现性 | 分类型判定，阈值附近会翻转；依赖公差表 + 策略版本 | 连续型，阈值附近平滑；依赖 λ |
| 排除可追溯 | 天然有 `excluded[]`，每条可反查 | 无“排除”概念，只能靠分差倒推 |
| 调参口 | 只有门槛 `g` 与策略版本（可冻结） | 引入 λ（额外旋钮） |
| 与论文关系 | 更贴近“公差是变量取值区间” | 原文无权重，属自补公式 |

**结论**：主线取“先筛后排”。**在专家公差到位前，只能作为带 `tolerance_source=data_proxy` 标记的诊断跑，不能写成“剔除不符合物理规则的对象”。**

**“排序默认生效”的准确含义**：生效的是**门控策略**——默认按超差/知识约束收缩候选池；**不是**“某个系数默认不为零”。门的开合由 `gate_policy_v1`（含 `g`、`not_evaluable` 处置、`V` 定义）决定，随方案冻结哈希；策略文件缺失或版本不符即中止，不出排序。由于本项目不存在 λ，也就不存在“λ 默认值”这件事。

#### 4.3.9 阻断 1/2 的处置（二选一，待拍板）【本项目新设计】

**阻断 1：门的阈值建立在 σ 来源混用之上（E5/E15，084 仍 open）**

- 问题：门槛 `D_v > g` 用的是 `σ_robust = max(1.4826·MAD, σ_floor)`，而 `σ_floor` 混了物理下限、`1%·|μ|` 兜底、无条目三种来源；**同一个 `g` 在不同通道上不是同一把尺子**。
- 选项一（用户已选）：先按 4.3.12 做 σ 五桶，再定 `g`；门槛只在同桶内可比。
- 选项二（已否决）：门槛关闭（`gate_policy=off`），等专家公差到位再开。
- **用户裁决（2026-11-14）：走选项一**——先修尺子，再谈门槛；落地见 4.3.12。

**阻断 2：被排除/无公差条目的通道静默缺席（E14；实测口径见下）**

> **实测更正**：这 8 个通道**不是“缺列”**（97 列轨迹里都有数据），也**不全是“饱和”**（`saturated=True` 只有 FLB.WFWA 一个）；它们是 `excluded_vars` 规则排除的 marker + 安全动作量（LOCA: WECS/WHPI/WLR；SGATR: WECS/WHPI/WTRA；FLB: WHPI/WFLB）。因此本条准确叫法是“**被排除/无公差条目的通道静默缺席**”；“缺列 0”已核实。

- 选项一：显式写 `gate_status=not_evaluable` + `reason`，在门控中按冻结策略处理（保守=不进池），报告单列名单；不改统计口径。
- 选项二：对饱和通道改用别的统计量（相对变化、斜率、到达时间）补算，扩大覆盖。
- **用户裁决（2026-11-14）：本次不动，挂起**——待定（用户 2026-11-14 决定后续再议）。**不按选项一提前实现，也不写选项二**；只保留本节问题描述与 E14 证据，等下一轮裁决。

**这两条阻断在“先筛后排”下的后果（已改为主线，故为现状）：**

- 阻断 1：阈值直接决定**谁被踢出候选池**；σ 混用会导致按噪声下限筛人，且被剔除者不再出现在任何排名里。
- 阻断 2：缺席变量既不在“超差”也不在“正常”；不显式标 `not_evaluable`，策略只能默认保留或默认剔除，两种都会系统性偏置候选池。
- 阻断 3 与筛/排无关：它发生在学图阶段（`prior_knowledge`），任何排序口径都继承。

#### 4.3.10 形式化约定十则（补缺口）【本项目新设计】

| # | 缺口 | 约定 |
|---|---|---|
| 1 | 排序量 | 主线只比 `E_v` 原值，不归一化、不做百分位；`E_v` 相同时才用次键，并输出 `n_ties` |
| 2 | `V` 定义 | `V` = 该模板全部入模变量（15）；`pool ⊆ V`；报告必须同时给 `|V|`、`|pool|`、`n_excluded` |
| 3 | `D_v` 的 t 窗 | `D_v = max_{t∈[0, T_end]} |x_v(t)−μ_v|/σ_robust`，用整条轨迹，不用学习窗；`T_end` 写进产物 |
| 4 | `D_v=None` | 统一 `gate_status=not_evaluable`；保守策略=不进池；**没有“偏差记 0”这条规则** |
| 5 | 非对称 `[lo,hi]` | `d_v=max((x−hi)/σ_up, (lo−x)/σ_dn, 0)`；两侧 σ 来自专家表；无专家表时禁用 |
| 6 | 同分 tie | `E_v` DESC 后依次 `in_degree↑`、`weighted_reach↓`、`reach_size↓`、`consensus_rate↓`、变量名；与旧 `rank` 一致（含“有出度优先”） |
| 7 | 单调性 | 主线 `S_v=E_v`，**对 `E_v` 严格单调（等值除外）是自然性质**，不需要附加条件；门槛只改池子，不改分数 |
| 8 | CLI 与冻结冲突 | 只保留 `--gate-policy <version>`；**没有 `--ranking-mode`、没有 λ 参数**；门槛 `g` 属策略文件，不由命令行临时改 |
| 9 | pk 环/冲突预检 | 生成前查三类冲突：同对 `0/1` 并存、单向 `0` 形成的环、`0` 与 K3 期望边反向；任一命中即中止（E13：库不报错且静默翻序） |
| 10 | K3 预检 | `expected_chain` 的边不得与 `asserted_edges` 反向、不得撞 `forbidden_outgoing`；冲突只报告不自动取舍 |

#### 4.3.11 离线预实验（门控版）是否可行【本项目新设计】

- **可做**：`v23-20261112T185258` 的 `signature_*.json` 有 `variables` + 稳定边 + `weight_median`，可离线重建 `B → T → E_v`；逐变量 `d_raw` 可从原始轨迹按 tolerance 的 μ/σ 补算；旧 Top-5 可校验“门控关闭”时逐位复现。
- **需要先定 3 条约定**：`V`/池子定义（缺口 2）、`D_v` 的 t 窗（缺口 3）、`None` 规则（缺口 4）。
- **无法离线得到**：新 bootstrap 的 `B`（不需要——沿用旧中位数即可）、专家公差表（不存在）。
- **结论**：**可做，成本为一次性只读脚本**；它回答“门控把谁踢出去、Top-1 换没换”，**不能回答“换得对不对”**。结果见 4.3.13。

#### 4.3.12 σ 来源分桶与逐变量对照（用户裁决：先修尺子）【本项目新设计】

> **待批准，未实施。** 本节只给口径与对照表，不改代码、不跑链路。

**（1）为什么要换尺子**

现在 `σ_robust = max(1.4826·MAD, σ_floor)`，而 `σ_floor` 混了三种来源：物理下限、`1%×|μ|` 兜底、没有条目。同一个 `d_raw`，分母却不是一个物理量——084 判“跨变量不可比”，根因就在这里。

**σ 在本方案里只是“门的尺子”**：它决定 `D_v` 是否超过门槛 `g`（谁有资格进池），**不进入任何排序分数**；主线分数只有 `E_v`。所以换尺子改变的是候选池，不是名次算法。

**（2）五个桶与判定条件**

| 桶 | 判定条件 | σ 取法 | 可评估 |
|---|---|---|---|
| b1 实测噪声 | `1.4826·MAD > σ_floor` 且 `MAD>0` | `1.4826·MAD` | 是 |
| b2 物理下限顶住 | `σ_floor(physical) ≥ 1.4826·MAD` | `σ_floor(physical)` | 是（低分辨，标注） |
| b3 样本不足 | `σ_floor` 来自 fallback 且 `1.4826·MAD < σ_floor/10` | **不造数**：`σ=None` | 否 |
| b4 饱和 | `saturated=True` | `σ=None` | 否 |
| b5 缺标定 | 无公差条目（marker / 安全动作量） | `σ=None` | 否 |

- **优先级**：`b4 > b5 > b3 > b2 > b1`（先判不可评估，再判下限，最后才用实测）。
- **冲突择一**：同时满足 b1/b2 → 取 b2（更保守）；同时 b3/b4 → 取 b4，但把两个原因都记进 `reason`。
- **不可评估的记录**：写 `evaluability=not_evaluable` + `reason`（`insufficient_baseline` / `saturated` / `not_calibrated`），**不允许为了把表填满而编一个下限**。

**（3）逐变量对照（15 × 3 类 = 45 行，先做这三类）**

| 类 | 变量 | floor 来源 | 现 σ | 桶 | 改后 σ 取法 | 可评估 | 理由 |
|---|---|---|---|---|---|---|---|
| LOCA | FRCL | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | 零变异本底，MAD=0 不代表可估 |
| LOCA | FRZR | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | 同上 |
| LOCA | LVPZ | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | MAD 仅 0.0098，下限主导 |
| LOCA | P | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | MAD 0.0126 < 0.5 |
| LOCA | PRB | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | 常数本底 |
| LOCA | PSGA | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | MAD 0.0073 |
| LOCA | RHMT | physical 1e-4 | 3.506e-4 | b1 | `1.4826·MAD` | 是 | MAD 已是下限 5.2 倍，现状正确 |
| LOCA | RM1 | physical 1.0 | 1.000 | b2 | 保留 1.0 | 是 | 常数本底 |
| LOCA | RRCA | physical 0.01 | 0.01000 | b2 | 保留 0.01 | 是 | 常数本底 |
| LOCA | TAVG | **fallback** | 3.100 | **b3** | `None` | **否** | floor/MAD≈690，1%·\|μ\| 无物理依据 |
| LOCA | TRB | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | 常数本底 |
| LOCA | VOL | **fallback** | 2.896 | **b3** | `None` | **否** | floor/MAD≈739 |
| LOCA | WECS | 无条目 | — | b5 | 先补条目 | **否** | 安全动作量，被 `excluded_vars` 排除 |
| LOCA | WHPI | 无条目 | — | b5 | 先补条目 | **否** | 同上 |
| LOCA | WLR | 无条目（marker） | — | b5 | marker 单独标定 | **否** | 不作为普通通道 |
| SGATR | HTR | fallback | 1.995 | b1 | `1.4826·MAD`（2.958） | 是 | 名义 fallback，实际 MAD 主导 |
| SGATR | LSGA | fallback | 0.1183 | **b3** | `None` | **否** | floor/MAD≈52 |
| SGATR | LVPZ | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | MAD 0.0083 |
| SGATR | NSGA | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | MAD 0.0215 |
| SGATR | P | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | 同 LOCA.P |
| SGATR | PSGA | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | 同上 |
| SGATR | RH | fallback | 3.399e-5 | b1 | `1.4826·MAD`（5.04e-5） | 是 | MAD 主导 |
| SGATR | RM2 | physical 1.0 | 1.000 | b2 | 保留 1.0 | 是 | 常数本底 |
| SGATR | TKLV | physical 0.1 | 0.1000 | b2 | 保留 0.1 | 是 | 常数本底（`z_pathology`） |
| SGATR | VOID | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | MAD 5.1e-6 |
| SGATR | WRCA | physical 5.0 | 5.000 | b2 | 保留 5.0 | 是 | 常数本底（`z_pathology`） |
| SGATR | WSPY | physical 5.0 | 5.000 | b2 | 保留 5.0 | 是 | μ=0、零变异 |
| SGATR | WECS | 无条目 | — | b5 | 先补条目 | **否** | 被 `excluded_vars` 排除 |
| SGATR | WHPI | 无条目 | — | b5 | 先补条目 | **否** | 同上 |
| SGATR | WTRA | 无条目（marker） | — | b5 | marker 单独标定 | **否** | 同上 |
| FLB | DNBR | physical 0.05 | 0.05000 | b2 | 保留 0.05 | 是 | MAD 0.00129 |
| FLB | PRB | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | 常数本底 |
| FLB | PSGA | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | 同上 |
| FLB | PWNT | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | MAD 0.0536 |
| FLB | QMGB | **fallback** | 19.42 | **b3** | `None` | **否** | floor/MAD≈105 |
| FLB | RBLK | fallback | 59.32 | b1 | `1.4826·MAD`（87.94） | 是 | MAD 主导 |
| FLB | TAVG | **fallback** | 3.100 | **b3** | `None` | **否** | floor/MAD≈820 |
| FLB | TRB | physical 0.5 | 0.5000 | b2 | 保留 0.5 | 是 | 常数本底 |
| FLB | WCHG | physical 5.0 | 5.000 | b2 | 保留 5.0 | 是 | MAD 0.0833 |
| FLB | WFLB | 无条目（marker） | — | b5 | marker 单独标定 | **否** | 同上 |
| FLB | WFWA | physical 5.0 | 5.000 | **b4** | `None` | **否** | 通道饱和，Nσ 无物理含义 |
| FLB | WHPI | 无条目 | — | b5 | 先补条目 | **否** | 被 `excluded_vars` 排除 |
| FLB | WRCA | physical 5.0 | 5.000 | b2 | 保留 5.0 | 是 | 常数本底（`z_pathology`） |
| FLB | WSTB | physical 5.0 | 5.000 | b2 | 保留 5.0 | 是 | MAD 0.00361 |
| FLB | WUP | physical 5.0 | 5.000 | b2 | 保留 5.0 | 是 | μ=0、零变异 |

**分批边界**：本表只覆盖三类 ready 事故的 45 个入模通道；其余 96−45 个通道（含未入模但与 K2/K3 相关的通道）留到第二批，按同一五桶规则补，不得先改口径后补表。

**（4）汇总计数（45 通道）**

| 处置 | 数量 | 明细 |
|---|---|---|
| 保持物理下限（b2） | **27** | 全部 `physical` 且 floor 主导 |
| 已是实测 MAD（b1） | **4** | LOCA.RHMT、SGATR.HTR、SGATR.RH、FLB.RBLK |
| fallback 但 MAD 主导（b1） | 3（HTR/RH/RBLK，已含在上行 4 个内） | 名义 fallback，尺子不变 |
| **真正换尺子**（fallback 独自撑住） | **5** | LOCA.TAVG、LOCA.VOL、SGATR.LSGA、FLB.QMGB、FLB.TAVG |
| **变成不可评估** | **14** | 5（样本不足，MAD ≈ floor/52~820）+ 8（无条目）+ 1（WFWA 饱和） |
| 缺列 | **0** | 45 个变量在 97 列轨迹里都有数据列 |

**（5）“怎么改”的人话版**

- 一个吃 `1%×|μ|` 的通道（例：LOCA.TAVG，μ≈310、σ=3.1）→ 若改成“注入前 11 点实测 MAD”，MAD 只有 0.00449，**等于把尺子缩小约 690 倍**，`d_raw` 会放大 690 倍。所以不能直接换：要么延长基线重估噪声，要么标 `not_evaluable`。
- 饱和通道（FLB.WFWA）→ 标 `gate_status=not_evaluable(reason=saturated)`，按保守策略不进池，不编下限。
- 无条目通道（WECS/WHPI/marker）→ 标 `not_calibrated`；在此之前会被判 `not_evaluable`，严格门下直接进不了池（离线实验已证实，见 4.3.13）。

**（6）字段与冻结口径**

`gate_policy_v1.yaml` 增：`sigma_source`（`measured_mad` / `physical_floor`）、`sigma_value`、`sigma_rule_id`、`evaluability`（`ok` / `not_evaluable`）、`not_evaluable_reason`、`bucket_priority`、`mad_multiplier=1.4826`、`insufficient_ratio=10`。逐变量落到 `tolerance_<K>_v2.json`；报告必须写“多少通道换尺子、多少不可评估”。

**（7）影响与失效范围（D2）**

分母一换，所有含 `d_raw` 的旧结论（偏差档位、任何偏差排名、4.3.13 的离线结果）**一律不可比**；图、`B`、`T` 仍可比。需要重跑：tolerance → deviation →（若上主档）排序 → 报告。旧 run 只能作“旧尺子”对照，不得混表。

#### 4.3.13 离线预实验：三条约定与结果（非 citable）【项目实测】

> 离线复算，**不是新 run**；产物不入库，不得作为 `citable` 结果引用。

**（1）三条约定（推荐 + 备选）**

| 约定 | 推荐 | 备选 | 理由 |
|---|---|---|---|
| `pool` 定义 | `pool={v：`gate_status`=pass}`；`not_evaluable` 保守不进池 | 宽松：`not_evaluable` 进池 | 保守能避免“没测到”被当成“没问题”；宽松会放大缺条目通道 |
| `D_v` 时间窗 | 注入后全程 `max_t` | 注入后固定窗 / p95 | 各模板传播时长不同，固定窗截长尾；p95 会抹掉单点尖峰 |
| 门槛 `g` | 先取 1，随 `gate_policy` 冻结 | 3（更松） | 论文无阈值；`1` 与 `3` 都只是策略值，必须与 σ 尺子成对定标 |

**（2）方法与校验**

`B` 由 `v23-20261112T185258` 的 signature 稳定边 + `weight_median` 重建，`T` 一次求逆，`E_v` 全量重算；`d_raw` 从原始轨迹按 tolerance 的 μ/σ 补算（落盘 `deviation` 只存 Top-10，不够用），并与落盘值逐条核对，差异 0。**门控关闭时与旧排名 9/9 Top-1、Top-5 逐位一致**，`total_effect` 六位小数全等，`cond`/`n_clipped` 一致。

**（3）加权分支实测（已被否决，只作证据）**

> 这一组数字是 `S_v=0.5R_E+0.5R_D·A_v` 跑出来的，**不是主线结论，不能用来评估门控口径**。

| 模板 | Top1 旧 | 加权后 Top1 | n_rank_changed | 偏差覆盖 |
|---|---|---|---|---|
| LOCA_low | LVPZ | **P** | 11 | 12/15 |
| LOCA_mid | TRB | **P** | 9 | 12/15 |
| LOCA_high | LVPZ | LVPZ | 12 | 12/15 |
| SGATR_low | PSGA | **LVPZ** | 14 | 12/15 |
| SGATR_mid | LSGA | **RM2** | 10 | 11/15 |
| SGATR_high | LSGA | **TKLV** | 13 | 10/15 |
| FLB_low | WFLB | **DNBR** | 13 | 12/15 |
| FLB_mid | WFLB | **DNBR** | 13 | 12/15 |
| FLB_high | WRCA | WRCA | 12 | 12/15 |

结论：加权会让 **7/9** 模板换 Top-1、`n_rank_changed` 9–14/15，而且换人主要由“无公差条目通道被 `A_v=0` 压低”驱动——**这正是否决加权的实证之一**。

**（4）门控版实测（主线口径）**

严格门 S = `d_raw>1` 且可评估；宽松门 L = `d_raw>1` 或不可评估；S3 = `d_raw>3` 且可评估。三种门都只决定“谁进池”；**池内一律按效应分 `E_v` 排序**（次键沿用 4.3.2），没有任何权重项。

| 模板 | 剔除数 S/L/S3 | 旧 Top1 | 门控 S | 门控 L | 门控 S3 | S 的 Top-5 进出 |
|---|---|---|---|---|---|---|
| LOCA_low | 7 / 4 / 7 | LVPZ | LVPZ | LVPZ | LVPZ | 进 TRB／出 RM1 |
| LOCA_mid | 7 / 4 / 7 | TRB | TRB | TRB | TRB | 无进出 |
| LOCA_high | 3 / 0 / 5 | LVPZ | LVPZ | LVPZ | LVPZ | 进 FRCL、PRB／出 WLR、WECS |
| SGATR_low | 8 / 5 / 9 | PSGA | **LVPZ** | **LVPZ** | **LVPZ** | 进 HTR、RH／出 PSGA、WECS |
| SGATR_mid | 5 / 1 / 5 | LSGA | LSGA | LSGA | LSGA | 进 PSGA／出 WECS |
| SGATR_high | 6 / 1 / 6 | LSGA | LSGA | LSGA | LSGA | 进 PSGA／出 WHPI |
| FLB_low | 4 / 1 / 4 | WFLB | **DNBR** | WFLB | **DNBR** | 进 WRCA、PSGA／出 WFLB、WHPI |
| FLB_mid | 3 / 0 / 4 | WFLB | **PWNT** | WFLB | **PWNT** | 进 WRCA、TAVG／出 WFLB、WHPI |
| FLB_high | 3 / 0 / 4 | WRCA | WRCA | WRCA | WRCA | 进 PWNT／出 WFLB |

汇总：S 变体 **Top-1 换 3/9**（SGATR_low、FLB_low、FLB_mid）；平均池 **9.89/15（65.9%）**；旧 Top-1 被门控直接剔除 **3 例**——SGATR_low 的 `PSGA`（`d=0.069`），以及 FLB_low/FLB_mid 的 `WFLB`（marker，无公差条目、不可评估）。

**（5）结论（写进诚实边界）**

- 门控版 **并没有比加权版更“稳”**：S 下仍换 3/9，L 下换 1/9；差别在于门控换人**可解释**（谁被剔除、为什么），加权换人只能看分差。
- **`d>1` 在当前 σ 尺子下几乎不收缩池子**：135 个模板-变量里只有 16 个（11.9%）是数值真的 ≤1 被挡；剩下 30 个是“不可评估”被连坐（marker / 安全动作量 / 零方差 / 饱和）。
- **marker 在严格门下必被剔除**（无公差条目），FLB 两例换 Top-1 完全由此产生——**结构效应，不是偏差证据**。
- 因此：门槛 `g` 必须与 σ 尺子（4.3.12）成对定标；在那之前，门控版只能作为带 `tolerance_source=data_proxy` 标记的诊断，不能写成“剔除不符合物理规则的对象”。
- 落盘 `deviation_*.json` 只存 Top-10，按文件口径覆盖率仅 1–5/15，结果会与全量口径不同——**这是产物缺陷，不是结论**。

### 4.4 预注册冻结（硬规则）

**【项目实测】** 项目已有 `work/knowledge_freeze.json` + `work/run_knowledge.py:28-40` 的哈希校验先例；本方案在此基础上扩展。

**【本项目新设计】** 建议冻结文件：

- `Code/external_knowledge/constraints_v1.yaml`：硬禁止/必须边/方向/软先验条目；
- `Code/external_knowledge/tolerance_expert_v1.yaml`：专家公差（若有）；
- `Code/causalrca/config/gate_policy_v1.yaml`：候选池门策略（`g`、`not_evaluable` 处置、`V` 定义、次键、版本号）；冻结 sha256 后即为默认生效策略；
- `Code/causalrca/config/prior_matrix_v1.json`：由约束生成的 `prior_knowledge` 矩阵 + 生成脚本哈希。

冻结内容：

- 文件 `sha256`；
- `constraint_version`；
- `applies_to`（事故类/组/模板）；
- `frozen_at`、`frozen_by`；
- `source`/`expert_signoff`；
- `falsification`（什么情况下这条约束/公式会被数据反驳）。

运行约束：

- `run_v23_all` 启动前校验哈希；不一致直接中止；
- 每个 run 的 `manifest.json` 记录 `constraint_version`、`sha256`、`gate_policy_version`；
- 换版本必须全链重跑，写 `--rerun-reason`；旧 run 不得与新版本混引；
- **不许按 `v23k` 或任何已跑结果增删条目/调门槛 `g`/改公差**；看到结果后改，即视为不可引用；
- 不按模板/组调 `g`；门槛与 `not_evaluable` 处置写进策略文件；**没有 λ、没有权重网格**；策略缺失或版本不符即中止，不出排序；
- 冻结文件里必须写死 `gate_policy` 版本与 `g`，禁止用环境变量/命令行临时改门槛或关掉门控。

### 4.5 分档与代价

| 档 | 做什么 | 依据 | 解释层位次/可见字段 | 改动 | 是否改数值 | 是否全链重跑 | 工作量 | 验收判据 |
|---|---|---|---|---|---|---|---|---|
| **D0 文档/披露** | 写明论文口径 vs 项目现状；写明“当前无专家公差、无必须边、排序是纯效应”；claim ledger | 【本项目新设计】 | 解释层生效起点：固定披露段 + `backend` 真实；`knowledge_status`/`tolerance_source` 可先为 `not_applicable`，但字段必须出现 | 文档/报告模板 | 否 | 否 | 分钟级 | 9 模板 100% 含披露；claim ledger 覆盖所有结论；数值逐字节不变 |
| **D1 知识条目/专家确认** | 补出处、专家签字、冻结 `constraints_v1`；9 条 unsourced 保持 `unsourced` | 【本项目新设计】 | `knowledge_status`、`expert_signoff_status` 进入解释层；未 active 时显示 `empty/insufficient`，不得写“知识生效” | YAML + 签字记录 | 若 active 则改图 | 是 | 小时–天级 | active 条目 100% 有 source + expert_signoff；0 条 unsourced active；覆盖率状态明确 |
| **D2 门控与排序** | 先做 4.3.12 的 σ 五桶与 `tolerance_v2`，再实现候选池门控与 `rank_v2=E_v`；**不含任何权重**；保留旧字段 | 【本项目新设计】 | `sigma_source`/`sigma_value`/`sigma_rule_id`/`evaluability` 进 `tolerance_v2`；`rank_v2`、`in_pool`、`gate_status`、`gate_rule_id`、`pool_size`、`n_excluded` 进 D6/D7；`gate_compare` 的 `n_rank_changed`/`top1_before/after` 进报告；旧 `rank/total_effect` 并列保留 | `tolerance.py` / `source_tracing.py` / `evaluation.py` / 报告 | 是（排名） | 是（9 模板） | 天级 | σ 五桶逐变量可核；换尺子 5 个、不可评估 14 个如实记录；`pool` 与 `gate_status` 一致；门控关闭时逐位复现旧排名；**产物无 `lambda` 字段** |
| **D3 学图路径约束** | `prior_knowledge` 矩阵、硬禁路径、必须边路径、方向约束；M1 只报 missing；含后置 `path_audit.json`（不回改图） | 【本项目新设计】 | `knowledge_prior`（present/missing/reversed）、`forbidden_edges_applied`（edge/path 计数）、`path_violations` 进入解释层 | `signatures.py` / `knowledge.py` / 冻结文件 | 是（图/T/排名） | 是 | 周级 | `prior_knowledge=None` 复现旧 B；矩阵一致性通过；无伪造直接边；missing 全报；`path_audit` 字段齐全 |
| **D4 直接边补全（M2）** | 约束回归补 must-exist 直接边；权重=bootstrap 中位数；标 `knowledge_imputed` | 【本项目新设计】 | `knowledge_imputed_edges`、`edge_origin=knowledge_imputed`、`citable=false` 进入解释层，单列 | 新回归模块 + 报告 | 是（图/T/排名） | 是 | 周级+ | 符号/稳定性判据预注册；全部 `knowledge_imputed` 单列；失败保持 missing |
| **D5 专家公差/cycle-aware** | `tolerance_expert.yaml`、按状态/循环分段；替换 `data_proxy` | 【本项目新设计】 | `tolerance_source=expert` 进入解释层；旧的 `data_proxy` 报告不得再引用为专家公差 | 数据 + 专家 + `tolerance.py` | 是（偏差/排名） | 是 | 周–月级 | 15 变量 × 3 类全部有 source + signoff；`tolerance_source=expert`；无 data_proxy |

**解释层生效位次：** D0 先让字段和披露出现；D1 让知识状态可见；D2 让新排序量可见；D3/D4 让知识约束/补全边可见；D5 让专家公差可见。解释层本身不单独占一个 D 档，它随 D0–D5 的产物逐级生效；任何一档改了字段/数值，报告都必须重新生成并重新跑 4.11 判据。

**优先级：** D0 先做；D2 是核心缺口，但必须与 D1/D5 的 `tolerance_source` 一起做；D3 先做路径约束，不急着做 M2；D1/D5 是瓶颈；D4 最后做且单列。

### 4.6 失效范围：哪些既有数字会不可比

| 改动 | `v23-20261112T185258`（唯一可引用） | `v23-20261112T185258-r8` | `v23k-20261114T200109` | `v23w-*` 宽筛选 | 解释层产物 | 说明 |
|---|---|---|---|---|---|---|
| D0 文档/披露 | 数值可比；报告文字变 | 需重生成 r8 文字 | 文字变；数值可比 | 文字变 | 报告文字变；新增 `backend`/披露字段；数值不变 | 只改文字，不改数字 |
| D1 知识条目 active | **不可比** | 不可比 | 不可比（v23k 已是知识注入变体，再加条目即新变体） | 不可比 | `knowledge_status`/`expert_signoff_status` 变；旧报告不能称知识生效 | 图变 → T/排名/指标全变 |
| D2 门控与排序 | **图/T 可比；候选池与排名不可比** | 不可比（基于旧全量排名） | 不可比 | 不可比 | `rank_v2`/`in_pool`/`gate_status`/`gate_rule_id` 进 D6/D7；旧报告不可混 | 门控本身即换实验条件；只能新建 gate run，不得覆盖 |
| D3 学图路径约束 | **不可比** | 不可比 | 不可比 | 不可比 | `knowledge_prior`/`forbidden_edges_applied`/`path_violations` 进入解释层；旧报告不可混 | 图变 → 全链变 |
| D4 直接边补全 | **不可比** | 不可比 | 不可比 | 不可比 | `knowledge_imputed_edges`/`edge_origin` 进入解释层；旧报告不可混 | 图变 + 新边来源 |
| D5 专家公差 | **偏差/排名/报告不可比；图/T 可比** | 不可比 | 不可比 | 不可比 | `tolerance_source=expert`；旧 `data_proxy` 报告不可再引用 | 公差变 → `D_v`、排名变 |

补充：

- **解释层不单独占 run**：它随 D0–D5 的产物逐级生效；D0 是字段/披露起点，D1 是知识状态，D2 是新排序量，D3/D4 是约束/补全边，D5 是专家公差。任何一档改了字段，报告都要重新生成并重跑 4.11 判据。
- `v23k-20261114T200109` 本身就是 `citable=null` 的对照档；任何新增约束/公式都应新建 run，不要把旧 v23k 当“同一实验”继续比。
- `v23w-20261114T173745`（LOCA wide，p=30）已因 W5 不成立；不要拿它做知识/排序对照。
- 任何 D2–D5 改动都必须写 `--rerun-reason`，并在 manifest 记录 `constraint_version`/`gate_policy_version`；否则新旧混引。

### 4.7 诚实边界：现有知识库意味着什么【项目实测】

> **计数口径说明：** 本节 32 条 / 23 有出处 / 9 无出处指 `v23k` 冻结基线（commit `7c304c6`）。截至本次修订，仓库 HEAD 的 `supplementary_mechanisms.yaml` 已在 commit `ed76001` 扩到 33 条，加上 P14 共 **47 条**（34 有出处 / 13 无出处；review accepted 33 / caveat 12 / rejected 2；`expert_confirmed: true` 仍为 0）；这 47 条不是 v23k 冻结基线，使用当前 HEAD 时必须重新冻结、重新计算覆盖率，不能沿用 32 条的结论。

- **规模与出处**：32 条 = P 14 + S 18；`sourced: true` 23 条，`sourced: false` 9 条；覆盖 LOCA/SGATR/FLB = 10/11/11 条；对入模 15 变量覆盖 = 10/15、11/15、9/15（`submit/外部知识补强-注入对照-v23k-2026-11-14.md:44-54`）。
- **9 条无出处**（S5/S7/S8/P4/P5/P7/P10/P11/P12 等）**不得进入任何约束层**，只能留库备查。
- **23 条有出处**也只到“小节/主题章”级，没有逐页核对；E5–E7 只到主题章级（同上 `:56-68`）。
- **审核**：全部 32 条 `reviewed_by: ai_agent`、`expert_confirmed: false`；AI 代审 accepted 27、accepted_with_caveat 5、rejected 0（同上 `:84-92`）。严格意义上的“专家确认 active 知识”是 **0 条**。
- **断言边召回**：注入档稳定边里，断言边命中 LOCA 2/7、SGATR 0–1/9、FLB 0–1/5（同上 `:155-163`）。当前知识能支持“什么边不可能存在”（禁边，保守、可审计），但**不能支持“必须存在哪些边”**；把低召回知识当 must-exist，会变成“按知识硬掰”。
- **与论文差距**：论文的“expert-defined, cycle-aware sensor tolerance ranges”在本项目**不存在**；当前 `tolerance.py` 是注入前 [0,100]s 数据驱动基线 + 物理 σ_floor，不是专家公差，也不是 cycle-aware。
- **因此最诚实的表述**：
  - 能说：“本项目有一个 AI 代审、部分有出处的知识层，能以禁边形式进入学图；排序目前是纯 T 效应，没有专家公差。”
  - 不能说：“外部知识已按论文贯穿学图与排序”“专家公差已参与根因排序”“知识约束已通过专家确认”。

### 4.8 解释层输入契约【本项目新设计】

**现状【项目实测】：**

- D6 `llm_explanation.build_payload` 当前只下发 `root_candidates` 的 `rank/variable/total_effect/in_degree/out_degree/reach_size/weighted_reach/auxiliary_cL/auxiliary_piL`，加 `k2/k3/deviation/limitations`；没有 `rank_v2`/`gate_status`/`deviation_raw`、`tolerance_source`、`knowledge_status`、禁边/路径信息（[llm_explanation.py](file:////wsl.localhost/Ubuntu/home/xczc/study/postgraduate/paperPlanAndCode/CausalTrace/Code/causalrca/reporting/llm_explanation.py#L661-L673)、[payload 构造](file:////wsl.localhost/Ubuntu/home/xczc/study/postgraduate/paperPlanAndCode/CausalTrace/Code/causalrca/reporting/llm_explanation.py#L760-L798)）。
- D7 `explanation_verdict.build_evidence_pack` 虽然下发 S/P（`knowledge.supplementary`）和 `stable_edges`，但 `candidates` 仍只有 `rank/total_effect/in_degree/out_degree/reach_size/weighted_reach`；没有新排序字段，也没有 `knowledge_prior`/禁边计数（[explanation_verdict.py](file:////wsl.localhost/Ubuntu/home/xczc/study/postgraduate/paperPlanAndCode/CausalTrace/Code/causalrca/reporting/explanation_verdict.py#L259-L271)、[evidence pack](file:////wsl.localhost/Ubuntu/home/xczc/study/postgraduate/paperPlanAndCode/CausalTrace/Code/causalrca/reporting/explanation_verdict.py#L301-L338)）。
- 因此，方案 4.3.2 新增的字段目前只存在于 `root_candidates` 产物，**没有进入 LLM/规则稿的输入**。

**现状实测：现有解释模板到底长什么样【项目实测】**

| 项 | D6 `llm_explanation` | D7 `explanation_verdict` + `verdict_report` |
|---|---|---|
| 是不是模板填空 | 是。`render_rule_explanation` 固定 7 段：结论 / 证据链 / 跨组一致性 / K2 / K3 / 偏离度 / 局限，逐段是“字段：数值” | 是。固定 5 节：图结构 Top-5 / 逐候选四态判定 / 冲突与不确定 / 结论 / 局限 |
| 篇幅 | `LOCA_low` 正文 3174 字符（`v23k` 实测），主要是名次、入出度、可达数、`cL/piL` 与各节计数 | 结论 `conclusion` ≤300 字（prompt 里要求 ≤120 字），`mechanism` ≤60 字/候选 |
| LLM 实际看到什么 | 整份 payload JSON：`root_candidates`（rank/variable/subsystem/role/total_effect/入出度/reach/weighted_reach/auxiliary_cL/piL）+ `cross_group` + `k2` + `k3` + `deviation` + `limitations` | 整份 evidence pack：`nodes`/`candidates`（含 in/out 边摘要）/`stable_edges`/`knowledge`(K2/K3/S-P)/`conflicts`/`graph_diagnosis` |
| prompt 硬约束条数 | 6 条（只许引用输入数字与变量名、两位小数、`n_consensus=null`、不得写“已证实因果”、7 节固定、不得新增数字） | 12 条（不得重排、不得引入包外变量/边/数值、每条机理必须引知识条目 id、K2 `not_violated` 不等于支持、三类证据分开写、不确定判 `undetermined` 等） |
| 调用频次 | 模板级 9 份**一律 rule**；LLM 只写 3 份类级汇总 + 1 份全局结论 | 逐模板可调 LLM，校验不过回落规则稿并标“未经模型判断” |
| 能不能提异议 | **不能**。既没有字段，也在禁止表述范围内 | **形式上不能**。`VERDICT_SYSTEM_PROMPT` 明确“审阅者不是分析者”，校验器拒绝任何重排与包外引用 |

**结论：** 现有两套模板的共同点是“**填空 + 不许越界**”——把人读解释压成了“字段：数值”的清单，既没有逐跳机理链，也没有“为什么排除另外四个”，更不允许模型说“我觉得这个排序有问题”。这正是本节和 4.13 要拆开的两个约束：**判断自由度**要给，**事实表述**要守。

**设计：统一 `explanation_contract_v1`。** D6 与 D7 必须下发同一组字段，缺字段用 `null` + `not_applicable/not_produced`，不能静默省略或显示 0。

| 组 | 字段 | 含义 | 来源 |
|---|---|---|---|
| `ranking` | `gate_policy_version` / `gate_rule_id` | 门策略版本与规则 id | N9 |
| `ranking` | `rank_v2` | 门控后按 `E_v` 的排名；与旧 `rank` 并列，不覆盖 | N6 |
| `ranking` | `in_pool` / `gate_status` / `pool_size` / `n_excluded` | 是否进池、门判定结果、池子大小 | N23 |
| `ranking` | `tolerance_source` | `expert` / `data_proxy` / `not_applicable` | N8 |
| `ranking` | `deviation_raw` | `D_v`，只作门输入，不进分数 | N6 |
| `knowledge` | `knowledge_status` | `empty/insufficient/partial/adequate` | N9 |
| `knowledge` | `constraint_version` / `knowledge_freeze_sha256` | 约束版本与冻结哈希 | N9 |
| `knowledge` | `forbidden_edges_applied` | 边级/路径级计数、`downgraded_from_path` | N1/N2 |
| `knowledge` | `knowledge_prior` | `present/missing/reversed` 计数 | N3/N4 |
| `knowledge` | `knowledge_imputed_edges` / `edge_origin` | 知识补全边及来源；`citable=false` | N4 |
| `knowledge` | `path_audit` / `path_violations` | 后置路径审计：`max_hops`、`truncated`、`forbidden_path_exists`/`must_exist_missing`/`via_missing`/`all_paths_via_violation` 计数 | N21 |
| `knowledge` | `expert_signoff_status` | active/待签/拒绝计数 | N9 |
| `evidence` | `backend` | `llm_verified` / `rule` | N16 |
| `evidence` | `degraded_reason` | 规则稿兜底/字段缺失原因 | N16 |
| `evidence` | `evidence_level` | 每个候选/结论的 E0–E5 | N17 |
| `explanation` | `claims[]` / `unsupported_claims` | 断言 → 字段追溯；指不回字段的断言单列 | N27 |
| `explanation` | `llm_disagreement` | 模型异议通道；`ranking_modified` 恒 `false`，不进排序 | N26 |
| `provenance` | `files` / `sha256` | 新产物来源与哈希 | N9 |

**落地规则：**

- 解释层只读，不重算因果量；`rank_v2`、`gate_status`、`deviation_raw`、`knowledge_prior`、禁边计数都由上游产物传入。
- `candidates` 必须同时保留旧 `rank/total_effect` 和新 `rank_v2/gate_status`，并在报告中说明两者差异；禁止只下发新字段导致旧结论不可追溯。
- `knowledge_status=empty/insufficient` 时，`knowledge` 组仍必须出现，值写 `empty/insufficient`，不能省略。
- `forbidden_edges_applied` 必须区分 `hard_forbid_edge` 与 `hard_forbid_path`；只禁直接边时不得写成“无任何路径”。
- `knowledge_imputed_edges` 必须单列，不得混入 `stable_edges` 的普通行。
- schema 版本升为 `explanation_contract_v1`；D6/D7 的字段集合必须一致。

**验收【本项目新设计】：**

- 同一 `run_id/template`，D6 payload 与 D7 evidence pack 的字段集合完全一致（允许值为 `null`）。
- 9/9 报告都能读到 `gate_policy_version`、`knowledge_status`、`tolerance_source`、`backend`。
- 任一字段缺失时，报告出现 `not_produced`/`not_applicable`，不出现静默 0。
- 解释层产物 sha256 可复现；不重算上游因果量。

### 4.9 解释层规则：判断自由度 vs 事实表述约束【本项目新设计】

**总原则：两类规则必须分开，不能一刀切地禁。**

- **判断自由度**管的是“模型可以想什么、可以不同意什么”——这里要**放开**；
- **事实表述约束**管的是“模型不许把 A 说成 B”——这里要**收紧**。

把它们混成一张“禁止清单”，结果是模型既不敢判断，又会用含糊措辞绕开事实约束（v23k 的规则稿就是前者：只罗列字段，不给判断）。

#### 4.9.1 判断自由度：允许模型做什么【本项目新设计】

1. **允许对 Top-5 提出异议**：可以说“第 2 名比第 1 名更像传导起点”“第 4 名物理上不成立”“排序漏掉了某个变量”，并给出理由。
2. **允许引用知识库以外的通用物理与工程常识**：例如“一回路压力下降会导致饱和温度下降”“停堆后余热仍需排出”。这类常识不必对应现有 47 条条目，**不受知识库覆盖范围限制**。
3. **允许给出“我认为更可能的顺序”**：但只能写进 `llm_disagreement`，**不得改写 `rank`/`rank_v2`、不得改写 `root_candidates` 产物、不得进排序**。
4. **允许说“证据不足、无法判断”**：这比编一个理由更可取。
5. **允许指出知识缺口**：可以说“现有知识没有覆盖这一跳”，这是有价值的输出，不是失败。
6. **允许质疑知识条目本身**：可以说“这条禁令与通用机理不符，建议复核”，但必须标明是建议，不能据此改图。

**边界只有一条：判断自由不等于改数。** 所有异议都落在解释产物里，排序产物与图产物一个字节都不动。

#### 4.9.2 事实表述约束：不许把 A 说成 B【本项目新设计】

解释层必须按字段说话，不能把“知识先验/数据代理/规则稿”升级成“模型确认/专家确认/数据结论”。

| 场景 | 允许说法 | 禁止说法 | 触发字段 |
|---|---|---|---|
| 公差来自 `σ_robust` | “数据代理公差（1 倍稳健 σ）” | “专家公差”“专家定义公差” | `tolerance_source=data_proxy` |
| 边被禁边规则删除 | “知识先验约束删除了该边” | “模型认为该边不存在”“数据表明该边不存在” | `forbidden_edges_applied` |
| 边由 M2 补全 | “知识补全边，`citable=false`” | “数据学出的稳定边”“模型发现该边” | `edge_origin=knowledge_imputed` |
| 只禁直接边 | “禁止了直接边，间接路径仍可能存在” | “不存在任何有向路径” | `hard_forbid_edge` |
| 禁整条路径 | “知识先验禁止该有向路径” | “只禁止了直接边” | `hard_forbid_path` |
| 路径审计违规 | “知识约束要求无路径/必经 M，图中仍存在路径/缺少路径” | “模型认为该路径不存在”“模型判断路径违规” | `path_audit.violations` |
| 知识为空/不足 | “知识未生效/覆盖不足” | “知识验证通过”“机理一致” | `knowledge_status=empty/insufficient` |
| 规则稿兜底 | “未经模型判断（规则稿）” | “LLM 判定”“模型认为” | `backend=rule` |
| 合成排序分（已废弃，见附录 B） | “效应 + 公差的合成排序分” | “因果效应值”“纯 T 效应” | `ranking_score_v2`（禁止出现） |
| 新旧排名并列 | “旧 rank 与新 rank_v2 的差异是……” | 只写“排名第一”不说明口径 | `rank` vs `rank_v2` |
| 知识未覆盖 | “知识未覆盖/缺失” | “知识确认不存在” | `knowledge_prior.missing` |
| 软先验 | “敏感性项，不进入主公式” | “主排序已使用知识先验” | `soft_prior` |

**补充规则：**

- 被禁边删除的边，不能写成“模型认为不存在”；模型/数据并没有做这个判断，是知识先验做的约束。
- `knowledge_imputed` 边不能写成“数据支持”；它是我们的替代设计，必须标 `citable=false`。
- `data_proxy` 公差不能写成“专家公差”；专家表到位前，报告必须保留“数据代理”字样。
- `backend=rule` 时，报告不得出现“LLM 判断/模型认为/模型解释”等措辞；只能写“规则稿兜底”。
- `knowledge_status=empty/insufficient` 时，不得出现“知识贯穿/知识约束生效/机理验证通过”。

#### 4.9.3 两条规则冲突时怎么处理【本项目新设计】

- 模型可以“不同意排序”，但**不同意必须单列**：写进 `llm_disagreement.claims[]`，每条带 `target`（变量 / 路径 / 全局）、`stance`（更可能 / 不成立 / 遗漏）、`basis`（`graph` / `knowledge` / `external_common_sense`）、`text`。
- `basis=external_common_sense` 的异议必须标 `citable=false`、`needs_review=true`：它不来自本项目任何产物，不能当证据引用。
- 事实表述约束只压“**事实性断言**”（谁删了边、公差是什么、谁做的判断），不压“**可能性判断**”（谁更像起点）。一句话里两者都有时，必须拆成两句。
- 若异议与字段冲突（例如“该通道不可能异常”但 `deviation` 显示明显偏离），**以字段为准**；异议保留但必须标注冲突，不得据此抹掉字段。
- 报告呈现顺序固定：先事实（字段），后判断（异议），最后不确定性；**不许把异议写在事实之前充当结论**。

### 4.10 rule backend 与 LLM backend 的一致性【本项目新设计】

- **同一契约**：`llm_explanation` 与 `verdict_report` 必须读同一组 `explanation_contract_v1` 字段；规则稿不是“低配版”，不能少字段。
- **规则稿必须打印**：`backend=rule`、`degraded_reason`、`knowledge_status`、`tolerance_source`、`gate_policy_version`、`knowledge_prior` 计数、`forbidden_edges_applied` 计数。
- **LLM prompt 必须包含**：同一组字段 + 禁止表述清单；prompt 中不得只给 `total_effect` 而不给 `rank_v2`/`gate_status`/`tolerance_source`，否则 LLM 会按旧口径解释。
- **缺失字段处理**：`knowledge` 组缺失时写 `not_applicable`/`not_produced`，不能写 0；`tolerance_source` 缺失时写 `not_applicable`，不能默认 `expert`。
- **一致性测试**：同一模板下，D6 与 D7 的字段集合一致；规则稿与 LLM 稿的 `knowledge_status`/`tolerance_source`/`rank_v2` 必须一致；若 LLM 不可用，规则稿必须显式标 `backend=rule`，不得伪装成 LLM。
- **v23k 教训**：`backend=rule` 时报告没有 S/P 字段，不是因为 S/P 命中 0，而是因为 D6 payload 没接 S/P；D7 的 evidence pack 才接 S/P。两条路径必须先统一，再谈解释质量。另：v23k 的 `backend=rule` 是主流程硬编码（E24），不是缺 key、不是网络、也不是异常降级；**S/P 未接线与 LLM 后端未打开是两件事**。

### 4.11 解释层验收判据【本项目新设计】

以下判据全部可核查，不使用“效果更好/更符合物理”。

1. **字段存在性**：9/9 报告含 `gate_policy_version`、`knowledge_status`、`tolerance_source`、`backend`；每个候选含 `rank_v2`、`gate_status`、`in_pool`、`deviation_raw`；知识组含 `forbidden_edges_applied`、`knowledge_prior`、`knowledge_imputed_edges` 或显式 `not_applicable`。
2. **禁用词扫描**：对 4.9.2 表中“禁止说法”做正则扫描，命中数必须为 **0**；扫描词表与版本号写入产物。
3. **数值忠实性**：沿用现有 `faithful`/`n_numbers_unsupported` 检查，`n_numbers_unsupported` 必须为 **0**；解释中出现的数字必须能在 payload/evidence pack 找到。
4. **backend 真实性**：`backend=rule` 的报告不得出现“LLM 判断”；`backend=llm_verified` 的报告必须带模型版本/调用记录。
5. **字段一致性**：D6/D7 字段集合一致；`rank` 与 `rank_v2` 同时存在；`total_effect` 与 `rank_v2` 不混用（排序分即效应分，不存在合成项）。
6. **哈希可复现**：解释层产物带 `sha256`；同一 run/模板重跑，字段与哈希可复现（LLM 非确定性除外，需记录温度/种子/模型版本）。
7. **知识状态诚实**：`knowledge_status=empty/insufficient` 时，报告不得出现“知识约束生效/知识贯穿/机理验证通过”。
8. **异议隔离**：`llm_disagreement` 出现且 `ranking_modified=false`；`rank`/`rank_v2` 与上游 `root_candidates` 逐位一致；异议不进结论章。
9. **断言可追溯**：`claims[]` 每条 `evidence_ref` 可解析；`unsupported_claims` 在正文有“（无支撑）”标记，且无一条被当作结论句。
10. **门控可见**：报告含 `gate_rule_id`、`pool_size`、`n_excluded`、`n_rank_changed`；被剔除名单可反查；**不得出现任何权重/λ 字段**。
11. **失败处置**：任一判据失败 → 阻断报告发布；修字段/修措辞，不改判据、不调门槛、不删失败模板。

### 4.12 `v23k` 解释层事实核对【项目实测】

**核对对象：** `Code/output/runs/v23k-20261114T200109`。

| 事实 | 证据 | 说明 |
|---|---|---|
| 第 8 步 `backend=rule` | `reports/rca_report_LOCA_low.json` 的 `llm_explanation.backend="rule"`；正文尾句实际是“（本报告由规则模板生成，全部数字来自同 run_id 的 v2.3 JSON 产物。）”——**不是**“未经模型判断” | 不是 LLM 判定；“未经模型判断”只出现在 D7 `verdict_report.py:173/227`，v23k 未走 D7 |
| `llm_explanation_*.json` 是 D6 payload 形状 | 顶层为 `schema/template/group/run_id/payload`；`payload` 只有 `root_candidates/k2/k3/deviation/limitations` | 没有 `evidence_pack` |
| D6 payload 无 S/P | `llm_explanation_LOCA_low.json` 的 `payload` 无 `supplementary`/`positive`/`knowledge` 键；全量 `reports/llm_explanation_*.json` 对 `evidence_pack|supplementary_entries|positive_entries|knowledge_status|forbidden_edges` 检索无匹配 | 报告侧未接 S/P |
| `rca_report` 1–8 节无知识节 | `rca_report_LOCA_low.json` 的 `sections` 为 1–8，无“知识/S/P”节 | 字段未出现，不是命中 0 |
| 分析侧 S 命中 Top-5 为 0–4 | `submit/外部知识补强-注入对照-v23k-2026-11-14.md:125-135`（9 模板：4/3/2/2/0/3/1/1/1 与 4/4/2/2/2/2/0/0/0） | 不要与报告侧“计数 0”混读 |
| D7 才接 S/P | `explanation_verdict.py:211-220` 的 `_supplementary_entries` 把 S/P 放进 `knowledge.supplementary`；`build_evidence_pack` 在 `:319-325` 下发 | 但 D7 `candidates` 仍无新排序字段（`:259-271`） |
| D7 也未接新排序字段 | `explanation_verdict.py:307-313` 的 `algorithm.rank_key` 仍是旧 `(-total_effect, in_degree, ...)`；`candidates` 无 `ranking_score_v2`/`tolerance_source` | 即使走 D7，也看不到新排序量 |

**结论：** `v23k` 里“S/P 计数为 0”的准确含义是 **“报告侧未接线／字段未出现”**，不是“S/P 命中为 0”。分析侧 S 命中 Top-5 是 0–4。D6 没接 S/P，D7 接了 S/P 但没接新排序字段；两条路径都不满足“知识 + 排序 + 约束”三者合一的解释契约。这正是 4.8–4.11 要补的原因。

---

### 4.13 解释模板重做：从字段罗列到通俗因果叙述【本项目新设计】

> **待批准，未实施。** 本节只改解释层（读产物、写文字），不改图、不改 `T`、不改排序。

#### 4.13.1 输入：Top-5 每条候选必须带齐的字段【本项目新设计】

| 组 | 字段 | 用途 |
|---|---|---|
| 身份 | `variable` / `meaning_cn` / `subsystem` | 让人读得懂“这是哪个量” |
| 排名 | `rank`（旧）/ `rank_v2`（门控后）/ `in_pool` / `gate_status` | 讲清“谁进了池、排第几” |
| 效应 | `total_effect`（= 排序分 `E_v`） | 说明排序分数只有效应分，不含任何权重 |
| 偏差 | `deviation_raw` / `gate_status` / `tolerance_source` | 偏差只作门的输入（门的尺子），不进分数；强制标注 `data_proxy` |
| 知识 | `knowledge_status`（`covered`/`partial`/`missing`/`reversed`）/ 命中条目 id 列表 | 讲清“这一跳有没有依据” |
| 边摘要 | `incoming[]` / `outgoing[]`：`from/to`、`weight_median`、`dir_e`、`edge_origin` | 支撑“机理链逐跳” |
| 路径 | `path_audit` 中涉及该变量的 Top 路径 + `result`（`compliant`/`unsupported`/`violating`） | 支撑多跳叙述 |
| 知识命中 | K2 `relation_id` 及三态；K3 命中边/缺失边；S-P 条目 id | 支撑机理与冲突判断 |

全局字段沿用 4.8 的 `explanation_contract_v1`，另加 `gate_compare` 的 `n_rank_changed` / `top1_before` / `top1_after`，让模型知道“新排序到底动了没”。

#### 4.13.2 放权：`llm_disagreement` 独立通道【本项目新设计】

```json
"llm_disagreement": {
  "schema": "causaltrace/llm_disagreement/v1",
  "ranking_modified": false,
  "claims": [
    {"target": "<变量名 | 路径 id | global>",
     "stance": "more_likely | implausible | missing",
     "basis": "graph | knowledge | external_common_sense",
     "text": "<一句话，≤80 字>",
     "citable": true,
     "needs_review": false}
  ]
}
```

规则：

- `ranking_modified` 恒为 `false`；任何情况下不得改动 `rank`/`rank_v2`、不得改 `root_candidates` 产物。
- `basis=external_common_sense` 时必须 `citable=false`、`needs_review=true`。
- 异议**不进结论章**，单独成段；报告里先给算法结论，再给“模型异议”。
- `backend=rule` 时 `llm_disagreement=null`，并写明“规则稿不产生异议”。

#### 4.13.3 通俗化骨架：六段【本项目新设计】

| 段 | 写什么 | 必含字段 | 字数 |
|---|---|---|---|
| 1 现象 | 哪组读数异常、指示器是什么、异常集中在哪些通道 | `deviation`、`marker` | 60–120 |
| 2 最可能原因 | 主档第 1 名是谁、效应与偏差各多少、口径是什么 | `rank_v2`、`total_effect`、`deviation_raw`、`tolerance_source` | 80–150 |
| 3 机理链逐跳 | 从原因到现象，逐跳写“A 变化导致 B 变化”，每跳标注有无知识依据 | `incoming/outgoing`、`knowledge_status`、`path_audit` | 150–300 |
| 4 为什么排除另外几个 | 第 2–5 名各自为什么排在后面（效应小 / 未过公差门 / 撞禁令 / 只是响应量） | `rank_delta`、`gate_status`、`knowledge_status` | 100–200 |
| 5 不确定性与反证 | 知识缺哪几跳、哪些路径 `unsupported`/`violating`、模型异议是什么 | `path_audit.counts`、`llm_disagreement` | 80–150 |
| 6 建议核对项 | 建议人去核对哪张图/哪个通道/哪条知识条目 | `needs_review`、`knowledge_status=missing` | 60–120 |

**禁止**：把第 3 段写成“字段：数值”清单；把第 4 段写成“其余候选效应较小”一句了事；在任一段里出现 4.9.2 表里的禁用说法。

#### 4.13.4 LOCA 占位骨架（不编具体数值）【本项目新设计】

```
【现象】
  {accident_type} / {group} 组异常读数集中在 {deviation_top_channels}；
  指示器 {marker} 触发。
【最可能原因】
  按门控排序（`gate_rule_id`={gate_rule_id}，池 {pool_size}/15），第 1 名是 {v1}（{subsystem_1}）：
  总效应 {E_1}，偏差项 {D_1}（尺子口径 {tolerance_source}）。
  模型判定：{verdict_1}。理由：{reason_1}（引用 {evidence_ref_1}）。
【机理链逐跳】
  {v1} → {v2}：{该跳依据或“无知识依据但未违反禁令”}（引用 {edge_ref}）；
  {v2} → {v3}：{该跳依据或“与禁令冲突，见 {constraint_id}”}；
  ……
  逐跳标注：compliant {n_c} 跳 / unsupported {n_u} 跳 / violating {n_v} 跳。
【为什么排除另外几个】
  第 2 名 {v2}：{理由，引用 rank_delta/gate_status/knowledge_status}；……
【不确定性与反证】
  知识未覆盖 {n_missing} 跳；路径审计 unsupported {n_unsupported} 条、violating {n_violating} 条；
  模型异议：{llm_disagreement.claims 中 needs_review=true 的条目}（未核实，不可引用）。
【建议核对项】
  {needs_review 清单：建议核对 {具体图/通道/知识条目}}
```

骨架只用占位符；实际数值必须来自同 `run_id` 产物，由程序回填或由 LLM 原样引用。

#### 4.13.5 可追溯：每条断言指回字段【本项目新设计】

- `claims[]`：`claim_id`、`section`（1–6）、`text`、`evidence_ref`、`status`（`supported`/`unsupported`）。
- `evidence_ref` 允许的形式：`root_candidates[0].total_effect`、`knowledge.k2.R1_inventory_leak`、`path_audit.paths[3]`、`S12_LOCA_...`、`gate_compare.n_rank_changed`。
- 指不回字段的断言 → 进 `unsupported_claims[]`，正文加“（无支撑）”标记，不得充当结论句。
- 程序校验：`evidence_ref` 必须能解析到实际存在的字段；解析失败即校验失败（沿用 D7 现有的“引用存在性”检查思路）。

#### 4.13.6 验收与代价【本项目新设计】

**验收（可核查）：**

1. 六段齐全；缺段写“未产出 + 原因”，不许静默省略。
2. 每段至少一条 `claims[]` 带可解析的 `evidence_ref`。
3. `unsupported_claims` 全部在正文有“（无支撑）”标记。
4. `llm_disagreement` 出现；`rank`/`rank_v2` 与上游产物逐位一致（校验器比对，不进改写）。
5. 4.9.2 禁用词扫描 0 命中；`n_numbers_unsupported=0`。
6. `backend=rule` 时 `llm_disagreement` 必须为 `null` 且报告写明“规则稿不产生异议”。

**代价（说实的）：**

- **必须真的调 LLM**：六段叙述规则稿写不出来，规则稿只能给第 1、2 段与数值，3–6 段会退化成“未产出”。**key 与端点已验证可用（E23）；当前障碍只是后端硬编码为 rule、开关默认关闭（E24）**——打开 llm 后端并同步重写规则稿即可，不存在“没有 key”这一前提。
- **prompt 变长**：要把每候选的边摘要、路径审计、知识状态都塞进去，token 显著增加，成本上升。
- **非确定性**：需要记录模型名、温度、种子；同一 run 重跑文字会变，只有字段与引用可复现。
- **异议需要人复核**：`external_common_sense` 类异议没有出处，只能进“建议核对项”，不能进结论。
- **不能拿它验证排序**：解释层读的是已有排序，写得再顺也不构成对排序正确性的证明。

#### 4.13.7 LLM 链路诊断与六段正文实施口径【项目实测 + 本项目新设计】

**（1）Key 与端点可用【项目实测】**

- `Code/.env` 中存在 `DEEPSEEK_API_KEY`，非空、长度 **35**（**只记存在性与长度，不记录任何内容**）；仓库根无 `.env`。
- 代码期望的变量名与之一致（`LLM_API_KEY_ENV = "DEEPSEEK_API_KEY"`）；读取优先级：进程环境 `CAUSALTRACE_LLM_API_KEY` → `DEEPSEEK_API_KEY` → `Code/.env` → 仓库根 `.env`。
- 一次最小请求（`deepseek-chat`、`max_tokens=4`、temperature 0）返回 **HTTP 200**，服务端回显 `deepseek-flash`，用量 **8 tokens**。
- 结论：**LLM 链路可用，无阻塞**。这不改变 E7/E12 的“解释层未接线”结论——两者是不同层面的问题。

**（2）`v23k` 9/9 `backend=rule` 的真实原因【项目实测】**

- 主流程 `run_v23_all.py:378` 把 `backend = "rule"` 硬编码；模板级 `run_v23_all.py:226` 与 `run_v23_llm_explanation.py:167` 再各写死一次。
- 因此**从未发起 LLM 请求**。判别证据：九份 `llm_explanation_{K}_{g}.json` 的 `requested_backend` 全为 `rule`、`degraded=False`、`degraded_reason=None`；3 类级 + 1 全局同样 `requested_backend=rule`。若为 key 缺失或调用失败，应为 `requested_backend=llm` + `degraded=True` + 具体原因。
- **明确：不是密钥问题、不是网络问题、不是异常被 `except` 吞掉后静默降级。**
- 与“解释层未接线”的区别：**未接线指没有开关、没有消费者，不是没有能力**。能力（key、端点、调用函数、降级与忠实性校验）都在，缺的是把开关接到主流程、把产物接进报告。

**（3）方案与代码的五处不一致（待改点，本轮不改）**

| # | 不一致 | 方案怎么写 | 代码实际 | 处置建议 |
|---|---|---|---|---|
| 1 | 模型名 | `DeepSeek V4.1-Flash` | v2.3 默认 `deepseek-chat`；旧路径 `LLM_MODEL="deepseek-flash"`；服务端把 `deepseek-chat` 回显为 `deepseek-flash` | **显式 pin** 成方案指定模型名，不依赖别名 |
| 2 | 温度/seed/超时 | 未规定 | `temperature=0.0`、无 seed、`LLM_TIMEOUT_S=30` | 写进方案与冻结配置 |
| 3 | 类级/全局 LLM 产物 | 类级 3 份 + 全局 1 份走 LLM | `llm_explanation_class_*.json` / `_global.json` **无任何消费者** | 明确是进报告还是“仅留档” |
| 4 | 报告尾句 | 按 backend 标注归属 | `report_template.py:398` **无条件**写“本报告由规则模板生成……” | 改为按 `backend` 条件化，否则走 LLM 会误标 |
| 5 | 正文来源 | 六段正文可来自 LLM | txt 正文完全由规则模板生成；JSON 元数据只有 `backend`/`faithful` | 正文优先取六段文本，元数据补模型/温度/seed |

**（4）六段正文落地估算【本项目新设计】**

| 文件 | 改动 | 行数 |
|---|---|---|
| `run_v23_all.py` | `--backend {rule,llm,auto}` 贯通到模板级 | 20–40 |
| `llm_explanation.py` | 六段 prompt + 规则稿重写 + payload 补字段（`rank_v2`/`gate_status`/`in_pool`/`deviation_raw`/知识命中/边摘要） | 200–300 |
| `report_template.py` | 正文优先六段 + 尾句条件化 | 50–80 |
| `constants_v23.py` | 模型/温度/超时常量 | 约 5 |

- 不含 D7：**4 个文件、300 行以内**。
- 成本：13 份全走 LLM，输入约 **88.7k 字符（30–40k tokens）**、输出约 **21k 字符（10–13k tokens）**，合计每轮约 **4–5 万 tokens**。
- **真正的成本是非确定性**：`temperature=0` 也不保证逐字节一致；需记录模型版本、按 `run_id + 输入哈希` 缓存（换 run 即失效）；忠实性校验失败会降级。

**（5）对既有表述的纠正**

- 原“必须真调 LLM，没有 key 就退化回规则稿”改为：**key 与端点已验证可用（E23）；当前是开关默认关闭、后端硬编码为 rule（E24）；六段正文需要打开 llm 后端并对规则稿同步重写**。
- 保留不变：**规则稿写不出六段叙述**（只能给第 1、2 段与数值，3–6 段会退化成“未产出”）。

---

## 5. 与现有 K1/K2/K3 的关系（避免重复建设）

| 现有 | 定位【项目实测】 | 本方案怎么用【本项目新设计】 | 不要做什么 |
|---|---|---|---|
| K1 禁边 | `knowledge.py:361-401` marker 外生性 | 保留为 `builtin`；新增机理禁边进 `constraints_v1.yaml`；统一由 `apply_ontology_constraint` 清零 | 不要在 YAML 和代码各写一份同规则 |
| K2 物理关系 | `physics_constraints.py:97-160` 事后相关符号 | 保留 posthoc；不进入排序；可扩关系/扩类 | 不要把 K2 升级成硬边；不要用它替代 tolerance |
| K3 故障模板 | `external_knowledge.py:334-388` 覆盖率 | 保留 posthoc；不硬加边；可扩 expected_chain | 不要把 K3 当必须边 |
| S/P 条目 | 解释层 + v23k 禁边派生 | **实际口径（E19）**：`sourced=true` 即可派生禁边进 K1（v23k 即如此）；`expert_confirmed=true` 是**尚未满足的加严要求**，不是现有代码的开关；必须边/方向进 `prior_knowledge` 时必须两者同时为真；其余留解释 | 不要把 unsourced 条目变成 active；也不要沿用“`sourced+expert_confirmed` 才能进 K1”的旧写法——按它执行会让约束层恒空（47/47 `expert_confirmed=false`） |
| `role_prior` | 评测侧排序乘子，默认关 | 本方案不混入主公式；作为独立敏感性，两版并列 | 不要把它当“专家公差” |
| `tolerance` | 偏差通道基线 | 排序公式的 `D_v` 代理；专家表到位后替换 | 不要把 `σ_robust` 称作专家公差 |

---

## 6. 待登记清单（不占台账编号，由维护者登记）

- [ ] 论文 RCA 明确写“expert-defined tolerance + topology”，本项目排序无公差项，属实质偏离。
- [ ] 论文 Algorithm 1/2 未形式化知识约束；本项目只有 K1 禁边，`allowed` 不生效。
- [ ] `merge_into_ontology` 无调用方，`allowed` 只标记不添加。
- [ ] `role_prior` 默认关，且不进入 `root_candidates` 产物。
- [ ] `tolerance` 只服务偏差，K2 实际不读 tolerance（docstring 历史口径）。
- [ ] `v23k` 冻结基线 32 条：9 条 unsourced、23 条 sourced 仅小节/主题章级、全部 `expert_confirmed: false`。
- [ ] 断言边召回 LOCA 2/7、SGATR 0–1/9、FLB 0–1/5，不能支持 must-exist。
- [ ] 需要新增 `constraints_v1.yaml` / `tolerance_expert_v1.yaml` / `gate_policy_v1.yaml` 冻结机制。
- [ ] D2–D5 改动会使 `v23-20261112T185258`、`v23k`、`v23w-*` 不可比，需新建 run。
- [ ] `prior_knowledge` 的 `0/1/-1` 语义与直接边/路径的区别需写入实现说明，避免误用 `0` 禁掉间接路径。
- [ ] 解释层 D6/D7 字段不一致：D6 `llm_explanation` 无 S/P、无新排序字段；D7 `explanation_verdict` 有 S/P 但无新排序字段。
- [ ] `v23k` 的“S/P 计数为 0”是报告侧未接线/字段未出现，不是命中 0；分析侧 S 命中 Top-5 为 0–4。
- [ ] `backend=rule` 的报告不得写成 LLM 判断；规则稿必须打印 `knowledge_status`/`tolerance_source`/`gate_policy_version`。
- [ ] `prior_knowledge` 没有跳数语义；“三跳以内允许/最多两跳”不能表达；需在方案/报告中写清，避免误读。
- [ ] 后置路径审计 `path_audit.json` 未实现；当前只有禁边/路径矩阵，没有多跳可达性/路径长度/必经点报告。
- [ ] “允许间接但必经中转”不能用 `prior_knowledge` 单独表达；需 `must_exist_path.via` + 后置路径检查。
- [ ] 排序侧从未做门控：代码里没有 `gate_status`/`in_pool`/`pool_size`；方案的门策略（`gate_policy_v1`）与门控对照（`gate_compare`）均未实现；**代码与方案都不存在 λ**。
- [ ] LLM 后端未贯通：`run_v23_all.py:378` 与两处模板级把 `backend` 硬编码为 `rule`，v23k 从未发起 LLM 请求（E24）；key 与端点可用（E23），缺的是开关与接线，不是能力。
- [ ] 类级 3 份 + 全局 1 份 LLM 产物无消费者；报告 txt 正文不拼 LLM 文本，尾句无条件写“规则稿生成”（N38/4.13.7）。
- [ ] 解释模板是“填空 + 不许越界”：D6 规则稿固定 7 段、LOCA_low 3174 字符；D7 12 条硬约束且禁止重排，无异议字段。
- [ ] `llm_disagreement`、`claims[]`、`unsupported_claims` 三个字段均未实现；解释层目前无法区分“有依据的断言”和“模型自己的判断”。
- [ ] `prior_knowledge=0` 会先定序再删边，方向写错即翻序并新造反向边（E13/4.2.8）；禁边验收须扩为“除预注册外不得有新增/翻转边”。
- [ ] 偏差通道对饱和/缺列变量静默 `continue`、不写标签（E14）；需补 `deviation_status=not_evaluable` 与缺席名单。
- [ ] `V23-DESIGN-084` 仍 open（σ 来源混用、`max_t`/p95 聚合、分档阈值）；在它关闭前，门槛 `g` 不得定档，门控只能作 `data_proxy` 诊断（见 4.3.9 阻断 1）。
- [ ] σ 五桶与逐变量对照（4.3.12）未实现：5 个通道需换尺子、14 个应判不可评估；`tolerance_<K>_v2.json` 与 `sigma_*` 字段待新增。
- [ ] `deviation_*.json` 只落 Top-10（`TOP_N_DEVIATIONS=10`），全量 `d_raw` 不可读；离线复算需从原始轨迹补算，产物口径待补。

---

## 7. 一句话总结

**【论文明确】** 论文确实把专家公差和拓扑一起写进了 RCA 排序，也确实在本体/图校验层用了知识；但论文没有给“知识如何进入 LiNGAM”和“效应+公差如何合成”的公式。**【项目实测】** 本项目现成的硬接点只有禁边，v23k 证明它删得动、也证明它删不掉“响应量当根因”的核心冲突；同时证明 v23k 的解释层是 `backend=rule`，D6 payload 没接 S/P 和新排序字段。**【本项目新设计】** 所以本方案综合两边：学图侧把禁边提升为一等约束（工程判断，不冒充论文），必须边只做弱约束；排序侧**只保留效应分**，外部知识只做“约束”——禁边/禁路径改 `B`，公差门改候选池；**主线没有任何权重、没有 λ**。解释层用 4.8–4.11 的统一契约把新排序量、知识状态、禁边/补全边送进 LLM/规则稿，并用 4.9 的禁用表述防止越权归因。三层依据各就各位，不互相冒充。**本轮改动：**删除加权合成与 λ（降为附录 B，见 4.3.7）；改为“先筛后排”主线（4.3.2、4.3.8）；补门控版离线实测（4.3.13）；解释模板重做（4.13）；均未实施。

**评审轮补充（均未实施）**：`prior_knowledge=0` 会先定序再删边、可翻转方向（最小复现见 4.2.8，E13）；门控版离线实测见 4.3.13（E22）；补形式化十则（4.3.10）。

---

## 8. 裁决留痕表（用户 2026-11-14 已裁决：同意推翻，但必须留痕）

> 本表记录“原来怎么定的、现在改成什么、为什么改、影响哪些旧结果”。方案正文已按新口径写；旧裁决编号保留，不改号、不重排。

| # | 原裁决编号与原内容 | 本次变动性质 | 变动范围 | 生效档位 | 改成了什么 | 影响（哪些旧结果不可比） |
|---|---|---|---|---|---|---|
| 1 | `V23-DESIGN-089`：唯一 100 次档可引用生产 run（`v23-20261112T185258`），明确“不改口径、不改阈值与排序量” | **推翻**（可比性锚点） | §4.3.2、§4.3.8、§8 | D2，先决条件为 4.3.12 | 新口径 = 纯效应分 + 候选池门控（**不含权重、不含 λ**）；该 run 降级为“门控关闭”的对照基线，不再是新口径的可引用生产结果 | 该 run 的候选池/排名/Top-5 不可与新口径混表；图、`B`、`T`、稳定边仍可比 |
| 2 | `V23-DESIGN-099`：否决“强制加边”，注入接法选“排序期禁边” | **缩窄** | §4.2.6 M2 | 默认关闭，仅论文级实验 | M2 保留为“可选、默认关、产物 `citable=false`”，并新增 `prior_knowledge=0` 翻序风险约束（4.2.8） | 不影响旧 run；一旦启用 M2，含补全边的图不可与纯数据图混比 |
| 3 | `V23-DESIGN-098`：主代码一行不改；注入走 `work/`；`citable=null` | **缩窄** | D2（§4.5）、§4.3.7、§4.3.12 | D2 | 098 的“不动主代码”限定在原注入实验；新口径允许在 D2 改 `source_tracing.py`/`evaluation.py`，但必须先 work/ 旁路验证再合入 | 改主代码后所有旧 run 的排序实现基线失效，需新建 run |
| 4 | `V23-DESIGN-068`：取消稳定性一类推断量（跨组 Top-1 一致、Top-5 重叠、名次标准差、单调占比） | **缩窄** | §4.8 输入表、§4.3.2 次键、§4.13 | D3（解释层） | `cross_group` 不再作为 LLM 证据输入，只在附录披露；`consensus_rate` 保留为末位次键（本 run 为 `null`，实际不生效），不再解读为稳定性结论 | 不影响数值结果；旧报告中“跨组一致性”表述不得再引用 |
| 5 | `V23-DESIGN-062`：判定式解释默认关闭；`V23-DESIGN-065`：判定式解释成为逐模板报告正文（默认开启） | **推翻 062 / 扩写 065（名单外新接法）** | §4.13 全节 | D3 | 解释层默认调 LLM 生成六段通俗正文；失败回落规则稿并在抬头标“未经模型判断”；`llm_disagreement` 单列 | 旧报告正文（7 段字段罗列 / 5 节判定稿）不可与新六段正文直接对比 |
| 6 | `V23-DESIGN-065`：报告正文归属（规则稿 vs LLM 判定） | **名单外新接法** | §4.9.2、§4.12、§4.13.2 | D3 | 六段正文仍算“判定式解释正文”，但归属字段扩为 `backend/model/temperature/seed`，并写进正文抬头 | 归属写法变化；旧报告的“本报告由规则模板生成”为旧模板话术，不可沿用 |

- **裁决时间与裁决人**：2026-11-14，用户。
- **台账登记**：`Code/error_tracking.md` 只登记、不改编号、不重排（由维护者按现有编号追加）。
- **σ 与饱和通道**：σ 走 4.3.12（用户裁决 B）；八个饱和通道本条不动，挂起待再议（见 4.3.9）。

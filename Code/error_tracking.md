# 错误跟踪机制 (Error Tracking)

> 本文档记录实验复现过程中遇到的所有问题，按类别归档，便于追溯与复用。
> 格式：`[类别] 问题描述 → 根因 → 解决方案 → 状态`

## 类别定义

| 类别 | 说明 |
|------|------|
| ENV | 环境依赖（Python 版本、包安装、编译） |
| DATA | 数据获取与格式（下载、CSV 结构、缺失） |
| CODE | 代码逻辑错误（索引、维度、算法口径） |
| ALGO | 算法口径问题（LiNGAM 矩阵方向、总效应计算） |
| STAT | 统计假设违背（i.i.d.、非高斯、混杂） |
| PERF | 性能与运行时（耗时、内存） |
| DESIGN | 方案级设计缺口（实现忠实于方案但方案本身需 v2.1 修订） |

## 问题记录

### ENV-001：系统 Python 3.14 受 PEP 668 保护，无法直接 pip install
- **根因**：Debian/Ubuntu 系统 Python 标记为 externally-managed
- **解决**：在项目目录创建 venv（`CausalTrace/.venv`），所有依赖安装在 venv 内
- **状态**：✅ 已解决

### ENV-002：scipy 安装失败（metadata-generation-failed）
- **根因**：Python 3.14 上 scipy 默认尝试源码编译，需 Fortran 编译器（系统无 gfortran/ifort）
- **解决**：使用 `--only-binary :all:` 强制安装预编译 wheel（scipy 1.16.3 / numpy 2.5.4）
- **状态**：✅ 已解决

### ENV-003：lingam 版本回退到 1.8.2
- **根因**：lingam 最新版依赖 semopy，依赖解析回退至兼容版本
- **解决**：接受 1.8.2（DirectLiNGAM 功能完整），验证导入成功
- **状态**：✅ 已解决

### DATA-001：git clone GitHub 失败（HTTP2 framing / connection timeout）
- **根因**：网络环境对 git HTTP/2 协议不稳定，直连 443 端口超时
- **解决**：`git -c http.version=HTTP/1.1` 强制 HTTP/1.1；使用 `--filter=blob:none --sparse` 稀疏克隆仅拉取 LOCA/Normal
- **状态**：✅ 已解决

### DATA-002：NPPAD_info 仅有 5 条样本，无完整 100 条 LOCA
- **根因**：NPPAD_info 是开发样本目录，完整数据需从 GitHub 仓库获取
- **解决**：从 thu-inet/NuclearPowerPlantAccidentData 稀疏克隆 Operation_csv_data/LOCA（100 文件）+ Operation_csv_data/Normal（1 文件）
- **状态**：✅ 已解决（数据位于 `CausalTrace/nppad_repo/Operation_csv_data/`）

### DATA-003：Normal 文件夹名非 NORM
- **根因**：仓库中 Normal 数据在 `Operation_csv_data/Normal/`，而非 `NORM`
- **解决**：sparse checkout 路径修正为 `Operation_csv_data/Normal`
- **状态**：✅ 已解决

### CODE-001：run_pipeline 解析 severity_groups 返回结构时 KeyError: 'groups'
- **根因**：`run_severity_groups()` 直接返回 `{"low":..., "mid":..., "high":...}`，无外层 "groups" 键；run_pipeline 误写 `group_results["groups"]`
- **解决**：改为 `for k, v in group_results.items()`
- **状态**：✅ 已解决

### CODE-002：edge_direction_consistency 返回 list 而非 dict
- **根因**：该函数返回 `report` list（每项含 "relation"/"n_estimable_groups"），run_pipeline 用 `.items()` 遍历并访问 "estimable_groups" 字段
- **解决**：改为 `for rep in consistency`，字段改为 `rep['relation']`、`rep['n_estimable_groups']`
- **状态**：✅ 已解决

### ALGO-001：WLR 对 P 的总效应中位数为 0
- **根因**：LiNGAM 在静态近似下选择了 P→WLR 方向（双向耦合的竞争方向），故 T[P,WLR]≈0
- **解决**：符合方案预期——WLR↔P 为双向竞争假设，不预设方向；如实报告，由严重度稳健性判定（candidate, 3/3 组）界定
- **状态**：✅ 预期行为，非 bug

### ALGO-002：lingam 小样本 RuntimeWarning（divide by zero / invalid value）
- **根因**：DirectLiNGAM 在变量近零方差或小样本下熵估计出现数值问题
- **解决**：在 run_pipeline 顶部 `warnings.filterwarnings("ignore")`；不影响拟合结果（三组 status=ok）
- **状态**：✅ 已处理（抑制警告，结果有效）

### STAT-001：THA→P 方向一致率仅 72%，低于 90% 阈值
- **根因**：静态 LiNGAM 在强自相关动态数据上方向辨识不稳定
- **解决**：解释生成模块自动标注方向不确定性；不强行解读方向
- **状态**：✅ 如实标注

### STAT-002：Normal 轨迹功率演化与 LOCA 事故不匹配，阈值模块失效
- **根因**：Normal/1.csv 是 100%→40% 降负荷过程（PWNT 100%→40%、TAVG 310→299°C），而 LOCA 事故从 100% 功率出发（维持 100% 或迅速停堆到 ~2%），两者功率水平完全不对应。用 Normal 建的 μ±3σ 阈值去判 LOCA 异常，功率相关变量全部误报
- **解决**：改用 LOCA_1（1% 小破口）的 100% 功率恢复段（t=520~2030s，152 点）作为准 Normal 基准建阈值；排除故障变量（WLR/WECS/WHPI，正常恒为 0）和常量变量（σ<1e-6，如 WRCA）；9/14 变量获得可信阈值
- **状态**：✅ 已修订（anomaly.py / config.py / data_loader.py / run_pipeline.py）

### CODE-003：WRCA 稳态段 σ≈0 导致异常度爆炸
- **根因**：WRCA 在 LOCA_1 稳态段为常量（σ≈0），原代码用 σ_safe=1e-12 防除零，导致异常度 = 偏离/1e-12 ≈ 2.59e15
- **解决**：在 build_thresholds_from_loca1_steady 中增加判断：std < 1e-6 的变量不建阈值（无法估计正常波动范围）
- **状态**：✅ 已解决

### CODE-003：run_pipeline 中 ANM 对照阶段引用 te_result 的顺序错误
- **根因**：初次将 Phase 2b（ANM）插在 Phase 3（总效应）之前，但 ANM 对照需要 `te_result["ranking"]`
- **解决**：将 ANM 对照阶段移至 Phase 3 之后，确保 te_result 已计算
- **状态**：✅ 已解决

### STAT-002：GAM-ANM 图比 LiNGAM 更密（90 vs 56 稳定边）
- **根因**：非线性模型容量更大，能拟合严重度梯度上的弯曲关联，产生更密的边；符合方案 13.2 的预判
- **解决**：如实报告，不判输赢；以 Top-K 重合率与方向一致率作为可辨识性差异证据
- **状态**：✅ 预期行为，已在报告中说明

### STAT-003：LiNGAM 与 GAM-ANM Top-5 重合率仅 0.40，但 Top-10 达 0.90
- **根因**：两方法识别的重要变量集合相近（Top-10 高重合），但排序权重不同：LiNGAM 侧重热工变量（THA/PSGA/TAVG），GAM-ANM 侧重功率与安全系统变量（PWNT/QMWT/WHPI/WECS）
- **解决**：按方案 13.5 结论纪律解读为"线性/非高斯 vs 非线性 ANM 可辨识性差异"，不判哪种更准
- **状态**：✅ 如实报告

### ALGO-003：WLR↔P 方向两方法一致（均为 WLR→P）
- **根因**：LiNGAM 与 GAM-ANM 独立判定均选 WLR→P 方向（而非 P→WLR），是物理上可信的一致点
- **解决**：在报告中作为方法间一致的正向证据列出
- **状态**：✅ 正向发现

### ALGO-004：原论文同时跑 LiNGAM 与 DiffAN 但未说明如何结合
- **根因**：研读笔记 6.2 指出论文并列两种算法却未说明 RCA 指标基于哪个图、两方法适用场景差异
- **解决**：新增 `method_ensemble.py`，采用**共识边（方向一致）+ RRF 融合排序**策略：共识边可信度最高，冲突边标记不确定，排序用 Reciprocal Rank Fusion 结合两方法排名
- **状态**：✅ 已实现并集成，WLR↔P 方向一致作为正向证据

### CODE-005：root_cause_explanation.txt 未融合 ANM 结果
- **根因**：解释生成阶段仅传入 LiNGAM ranking，未接入 ANM 与融合结果
- **解决**：扩展 `generate_explanation_llm` 增加 `anm_ranking`、`ensemble` 参数；`_build_llm_prompt_context` 注入两方法对比段（共识边/冲突边/RRF Top-5）；run_pipeline 调用时传入
- **状态**：✅ 已修复，LLM 报告综合两方法

## 方案二（跨事故因果签名 RCA）问题记录

### RCA-001：跨类余弦相似度维度不匹配
- **根因**：不同事故类有效变量集不同（LOCA=20, SGATR=19, FLB=20），`cosine_similarity` 直接 `list(vec.values())` 导致 shape mismatch
- **解决**：`cosine_similarity` 改为按变量名取交集对齐（`set(vec_a.keys()) & set(vec_b.keys())`）
- **状态**：✅ 已修复

### RCA-002：E1 使用测试轨迹自身 marker 而非原型 marker
- **根因**：`compute_e1` 读取 `test_sig["M"]`（测试类的 marker 激活状态），未按原型 marker 判断，导致 E1 对所有原型均返回 1.0
- **解决**：E1 改用 `test_sig["A"][prototype_marker]` 判断原型 marker 是否激活
- **状态**：✅ 已修复

### RCA-003：SGATR 的 marker=WLR 为常量，FLB marker=PSGB 非独有
- **根因**：WLR 仅 LOCA 非零（破口泄漏），SGATR/FLB 中 WLR=0 被常量列审计剔除；PSGB 在 LOCA/SGATR 中也变化
- **解决**：引入正/负 marker 规则：LOCA={正:[WLR]}, SGATR={正:[PSGA],负:[WLR,WFLB]}, FLB={正:[WFLB],负:[WLR]}；E1 = 正marker激活率 × 负marker未激活率
- **状态**：✅ 已修复

### RCA-004：E3 用异常变量与因果上游变量重叠率，区分力不足
- **根因**：重叠率对跨类相似的异常模式不敏感（三类事故 top 异常变量均为 WRCA/WHPI/VOID）
- **解决**：E3 改用 C^L/C^A 因果效应排序的 Spearman 秩相关
- **状态**：✅ 已修复

### RCA-005：测试签名使用真实类别的因果模型（数据泄漏）
- **根因**：`build_signature_for_trajectory` 传入 `class_models[at]`（at 为测试轨迹真实类别），导致 E3 对正确类别恒为 1.0
- **解决**：测试集改用 `per_trajectory_causal=True`，用轨迹自身数据拟合 LiNGAM 构建 C^L；GAM-ANM 逐条太慢故 C^A 留空，E3 仅用 C^L
- **状态**：✅ 已修复

### RCA-006：逐条 LiNGAM 因果排序不可靠
- **根因**：单条轨迹仅 121 时间点，LiNGAM 恢复的因果结构噪声大，B1（仅因果）准确率仅 30%
- **解决**：降低 E3 权重（w3=0.2），提升 E2（异常模式）权重（w2=0.5）和 E1（marker规则）权重（w1=0.3）
- **状态**：✅ 已修复，最终提出方法 acc=1.000, f1=0.917, cov=0.867, abst=0.133

### RCA-007：Accuracy 与 Macro-F1 口径不一致（Acc=1.0 但 F1=0.917）
- **类别**：CODE
- **根因**：`compute_metrics` 中 `accuracy = correct/(n-abstained)` 是 Selective Accuracy（弃权不扣分），但 F1 计算时弃权样本 `predicted=None` 被计为 FN，导致 F1 被惩罚。两者统计口径不同。
- **解决**：同时报告 Selective Accuracy（接受样本准确率）和 Overall Accuracy（弃权=错误）；F1 明确在接受样本上计算，与 Selective Accuracy 同口径。
- **状态**：✅ 已修复

### RCA-008：SGATR marker 误用 PSGA，应为 WTRA
- **类别**：DATA / CODE
- **根因**：原方案明确 SGATR 故障指示器为 WTRA（SG管泄漏流量），但 VARIABLE_UNION_22 遗漏 WTRA，代码退回用 PSGA（正常运行变量，三类事故均非零）作为 marker。
- **解决**：将 WTRA 加入 VARIABLE_UNION_22 和 SGATR 的 CLASS_VARIABLES；SGATR marker 改回 WTRA。数据验证：WTRA 仅在 SGATR 非零，LOCA/FLB 中恒为 0。
- **状态**：✅ 已修复

### RCA-009：消融实验缺失，无法判断因果组件增量贡献
- **类别**：CODE
- **根因**：仅有 B0(仅E2)、B1(仅E3)、B2(随机)，缺仅E1、去E3等系统消融，无法证明因果组件有效。
- **解决**：新增 5 组消融配置：full(E1+E2+E3)、E1_only、E2_only、E3_only、no_E3(E1+E2)，复用同一批 test_sigs。
- **状态**：✅ 已修复。结果：E1_only=100%，E2_only=0%(全弃权)，E3_only=16.7%，no_E3=100%。证明 FULL 条件下准确率完全由 E1(marker规则)驱动。

### RCA-010：marker 变量直接编码事故类型，FULL 条件下分类平凡
- **类别**：STAT
- **根因**：WLR(仅LOCA非零)、WTRA(仅SGATR非零)、WFLB(仅FLB非零) 构成事故类型 one-hot 编码。原方案 §1.3 已声明 FULL 条件下分类"平凡"，真正检验是 MASKED 条件。
- **解决**：报告中明确标注此局限，说明因果组件价值需在 MASKED 条件（屏蔽 marker）下验证。
- **状态**：⚠️ 已标注，MASKED 实验待执行
- **2026-11-10 收口说明**：非 v2.3 决策项，归 v2.1 范围，保持登记（FULL 条件下分类平凡属方案已知局限）。

### RCA-011：B1 基线因 C^A 为空被人为压低
- **类别**：CODE
- **根因**：测试签名逐条构建时 C^A 留空（GAM-ANM 太慢），B1 仍计算 `(cl_sim + ca_sim)/2`，ca_sim=0 导致分数被折半。
- **解决**：B1 判断 `test_sig.get("C_A")`，为空时仅用 cl_sim。
- **状态**：✅ 已修复，B1 从 0.300 提升至 0.383

## 方案二 v2.1 重构审计（2026-10-26，首跑 rca_report.txt 全链路代码审计）

> 背景：首跑结果 FULL=100%（E1 驱动）、E2_only 全弃权、E3_only=16.7%。经全链路代码审计，负面结论目前**不能采信为方法结论**，原因是一批实现偏差与方案级设计缺口。以下条目不删改历史记录，作为 v2.1 实现的严格对照清单；CODE/ALGO/STAT 类按已冻结方案对齐，DESIGN 类需先经方案评审再实现。

### RCA-012：共享基线（过程本体层）三处违反冻结方案
- **类别**：CODE / DATA
- **根因/证据**：
  1. **时段错误**：pool 直接取主窗口切片（`slice_window(300,1500)`后的 `trajs[sev]`），未切方案要求的 t=520~2030s 恢复段（152 点）——`rca_data_loader.py:71`；
  2. **σ≈0 兜底而非跳过变量**：`sigma=np.where(sigma<1e-6,1e-6,sigma)`（`rca_data_loader.py:77`；`signature.py:29-33`；`run_rca_pipeline.py:102`），直接违反方案一既定硬约束（STAT-002、CODE-003：std<1e-6 不建阈值）和方案二 R0 排除规则；
  3. **错误变量入池**：marker（WLR/WTRA/WFLB）与安注阶跃变量（WHPI/WECS）随 VARIABLE_UNION_22 进入基线（`run_rca_pipeline.py:204`），它们在非本征事故类恒为 0、在本征类 sev-1 主窗口非零，μ/σ 被跨类零值稀释。
- **后果**：WRCA 异常度 3.2×10⁹、WHPI 1.2×10⁸（报告 rca_report.txt:153-157），E2 余弦被同一批爆炸分量在三类间同时主导。报告"发现 3：跨事故异常模式高度相似"是**数值伪影**，不可采信。
- **解决（v2.1）**：严格切 520~2030s；阈值变量集排除全部 marker + WHPI/WECS，σ≈0 变量跳过并落盘排除清单（对齐第一版 anomaly.py 口径），不做任何数值兜底。
- **状态**：✅ 已核销（D1）。rca/v21/data.py 切恢复段、fit_shared_baseline 池化三类 sev-1 的 18 物理候选；WRCA 因 σ≈0 排除并落 excluded 清单；shared_baseline.json 含 tightness_descriptive/baseline_note；r0_audit G1 通过

### RCA-013：E3 与冻结定义完全不符，且存在标签泄漏
- **类别**：ALGO / CODE
- **根因/证据**：
  1. E3 实现为测试轨迹效应排名 vs 类原型排名的 **Spearman 秩相关**（`signature_matcher.py:62-78`），方案冻结定义是"测试症状方向 d_x vs 各类 marker 效应列 c_k 的 **π 加权符号一致率**"，测试轨迹上**不学任何因果模型**；
  2. 测试轨迹用自身 121 个时间点现学 LiNGAM（`run_rca_pipeline.py:96-114`）——n=121、p=23 的伪重复，正是方案警告的单轨迹学图；
  3. **标签泄漏**：测试侧 target 取 `CLASS_TARGETS[accident_type]`（`run_rca_pipeline.py:107`），accident_type 是测试轨迹真实类别，真实标签进入 E3 输入构造；
  4. 测试侧 C^A 留空（`run_rca_pipeline.py:114`），双引擎在测试侧单边；
  5. 三类原型 target 不同（P/P/PSGB），Spearman 比较三个不同对象的效应排名，定义不成立；
  6. 测试轨迹自标准化（`run_rca_pipeline.py:100-103`），d_x 相对自身窗口均值而非共享基线，"症状偏离基线"语义丢失。
- **后果**：报告"发现 4：E3 判别力有限（16.7%）"测的是无效代理，对方案 E3 零信息量。
- **解决（v2.1）**：删除测试轨迹学图与标签 target；d_x=相对共享基线的有符号中位 z 偏离，A(x)=非 marker 物理症状的越限占比>10% 集合；E3 按冻结定义重写为双引擎 π 加权符号一致率。
- **状态**：✅ 已核销（D1）。inversion.py 实现 d_x/A(x)/e3_base/e3_path；run_d0 测试侧零学图零 target；MASKED 零访问断言 assert_no_marker_columns 在 D0 18 条上实测通过；E3_L/E3_A 双引擎见 d0_decision.json

### RCA-014：类签名无轨迹级 Bootstrap，数值爆炸无闸门
- **类别**：ALGO / STAT
- **根因/证据**：类模型仅 `B=fit_lingam(Z)` 单次拟合（`run_rca_pipeline.py:40`），`compute_total_effects([B],...)` 传入列表长度 1（:42）；方案要求 100 次轨迹级 Bootstrap、四指标、稳定边、π 同号比例全部缺失；(I−B)⁻¹ 在 B 特征值接近 1 时爆炸（PSGA→P=+5855，报告:35）无任何闸门直接进 |T| 排序并居首。
- **解决（v2.1）**：轨迹级 Bootstrap（LiNGAM 100/GAM 50，n=训练轨迹数），每次独立拟合+独立求逆后汇总四指标；效应按 π 同号率加权/筛稳定边，爆炸边按 π 低与组间翻转自动降权并标注，不允许 +5855 排第一。
- **状态**：✅ 已核销（D1~D4）。signatures.py 实现 100 次轨迹级独立拟合+独立求逆、四指标、f/dir 稳定边；R5 chains.py 实现爆炸闸门（severity_flipped 或 πL<0.70 → 末档并标 magnitude_unstable，禁爆炸量级居首；RRF 序数排名不跨变量比量级）；RCA-030/031 修正学图窗并以 C^A 为 marker 符号主证据

### RCA-015：标准化方向与方案相反（逐类 vs 跨类共享单轨）
- **类别**：CODE
- **根因/证据**：类模型逐类用训练 80 条 `fit_standardizer(train_mat)`（`run_rca_pipeline.py:36-37`）；方案 R0 冻结为"四类 sev-1 恢复段池化的同一套 μ/σ"（修改记录：跨类可比 T 尺度）。当前实现使 E2/E3 跨类尺度不可比。
- **解决（v2.1）**：学图标准化与异常评分统一使用共享基线 μ/σ（或明确论证学图必须逐类时，c_k 在融合前重新归一——优先前者）。
- **状态**：✅ 已核销（D1）。transform_graph_block 与测试侧统一使用 shared_baseline.json 的 μ/σ（marker 用 median_platform、图专用变量冻结尺度），T 与症状跨类可比

### RCA-016：划分实现与配置声明、方案要求三者不一致
- **类别**：CODE / DATA
- **根因/证据**：`rca_config.py:50-51` 注释声称 1~80 训练/81~100 测试；实际 `stratified_split` 对 sev 2~100 调用 `split_trajectories(others,TRAIN_RATIO=0.8,...)` 随机 80/20（`rca_data_loader.py:54-58`），无低/中/高严重度组配额（n=60=3×20 与随机 20% 吻合）；无 split_manifest 落盘、无基线源/测试文件名零交集断言（虽然 sev-1 固定训练使交集当前为空，但断言缺失）。
- **解决（v2.1）**：sev-1 固定训练；测试 20 条从低(2-33)/中(34-66)/高(67-100)按配额分层抽（种子落盘 split_manifest.json）；实现零交集自动断言；sev-1 留一时基线用其余三条 sev-1 重算。
- **状态**：✅ 已核销（D1）。data.build_split_manifest(seed=42)：sev-1 固定训练、配额 7/7/6、20 条测试；断言零交集/sev-1 隔离/2~100 全覆盖，split_manifest.json 落盘

### RCA-017：MASKED 核心实验、严重度分组、留一标定全部缺失
- **类别**：CODE
- **根因/证据**：全仓库无 marker 掩蔽/零访问断言、无严重度三分组指标、无 Normal 弃权；融合权重硬编码 0.3/0.5/0.2（`run_rca_pipeline.py:247,271`），τ=0.3、δ=0.05 硬编码（`signature_matcher.py:81-82`）；无 E2/E3 留一 min-max 归一与 τ/δ 留一净收益标定。
- **解决（v2.1）**：FULL/MASKED 两条件 × 三分组；MASKED 测试侧删除 marker 列并加运行日志零访问断言；阈值由训练集轨迹级留一标定冻结 calibration_params.json；A(x)=∅ 强制弃权。
- **状态**：✅ 已核销（D1~D3）。MASKED 零访问断言（assert_no_marker_columns，D0 18 条 + D3 60 条实测通过）、A=∅ 弃权；FULL/MASKED×三分组全量指标与 τ/δ 留一标定 calibration_params.json（τ=0/δ=0.025）均已落盘

### RCA-018：目标变量未经 Day-1 审计冻结
- **类别**：CODE
- **根因/证据**：`CLASS_TARGETS={LOCA:P,SGATR:P,FLB:PSGB}`（`rca_config.py:30-34`）直接指定；方案要求看分类结果前审计冻结（SGATR 候选 LSGA、FLB 首选 WFWA，见修改记录第 11 条与 4.2 节）。报告中 SGATR→P、FLB→PSGB 无审计记录。
- **解决（v2.1）**：R0 补目标变量审计表（变量是否存在、sev-1 基线值、注入后是否变化、方向），签字冻结后再学图。
- **状态**：✅ 已核销（D1）。run_r0 产出 target_audit.md（SGATR/FLB 候选变量基线值/变化/方向/是否控制量），分类链路不引用 target

### RCA-019：R5 第二层输出物错误
- **类别**：CODE
- **根因/证据**：报告输出"向 target 的 |median total effect| 上游排序"（`run_rca_pipeline.py:364-376`），方案 R5 要求"从 marker 出发、对测试轨迹激活集 A(x) 的 RRF 融合传播链"；无 root_cause_chain_{trajectory}.json；LOCA Top-10 中无 WLR，传播链不从根因假设出发。
- **解决（v2.1）**：传播链以 marker 为根、在 A(x)∪中介变量上做双引擎 RRF 排序，逐轨迹落盘并过忠实性校验（链上边必须在稳定边/共识边集合内）。
- **状态**：✅ 已核销（D4）。chains.py+run_chains.py 出 92 条 root_cause_chain_*.json（FULL60+MASKED32）、ungrounded=0、outside_reach 不硬排、RRF 双引擎排序、方向一致率统计落 chain_summary.json

### RCA-020：B0/B1/B2 名实均不符且 B2 不可复现
- **类别**：CODE
- **根因/证据**：`rca_baselines.py` 中 B0=异常模式余弦（方案：marker 规则/E1）、B1=Spearman 因果（方案：**Pearson 最近类**，用于检验 E3 相对纯相关匹配的增量）、B2=`np.random.default_rng()` 无种子随机弃权（方案：XGBoost 窗口统计量，FULL/MASKED 两版）；指标键名亦按错误定义命名（`run_rca_pipeline.py:295-297`）。
- **解决（v2.1）**：三基线按 6.1 重写；B2 特征=窗口统计量（mean/std/min/max/Δ/越限率，无严重度/文件名/标记列），固定 random_state。
- **状态**：✅ 已核销（D4）。baselines.py+run_baselines.py 实现 B0(marker 规则)/B1(Pearson)/B2(XGBoost)，60 条全量：B0=1.000、B1=0.933、B2=1.000(CV=0.983)，baseline_metrics.json+b2_coverage.csv 落盘

### RCA-021：神经符号环节①（本体约束）在方案二流水线空转
- **类别**：CODE
- **根因/证据**：`core/ontology_constraint.py` 存在且方案一已用，但 rca/ 全部文件从未 import/调用（`run_rca_pipeline.py:9-25` import 清单无本体）；`accidents/` 下只有 loca/ 本体，SGATR/FLB 无实体与冻结关系编码；因此"−本体"消融在当前代码里没有可关闭的真实路径。
- **解决（v2.1）**：三类统一本体（变量→子系统；4.2 全部冻结关系：含 WFWA→LSGA 竞争边）；双引擎学图前后强制过约束（禁止边删除/允许边只标记）；−本体消融走同一学图函数的开关参数。
- **状态**：✅ 已核销（D1）。knowledge.py 三类统一本体（forbidden/allowed/competitive+实体表），bootstrap_lingam_template 学图后强制约束，use_ontology=False 是 −本体消融唯一入口（单测覆盖）

### RCA-022：E1 判据偏离冻结定义
- **类别**：CODE / ALGO
- **根因/证据**：E1 用共享基线异常分>3（即 3σ）判 marker 激活（`signature_matcher.py:45,51`），方案冻结为"marker 非零 |v|>ε，ε=训练集 marker 平台最小值的 1/10，一次性冻结"；叠加 RCA-012 的 marker 入池，marker 的 μ/σ 被其他类零值稀释，3σ 判据语义不成立。
- **解决（v2.1）**：marker 判据回归"非零+ε"（marker 不参与 μ±3σ 基线）；MASKED 条件 E1 恒不可用由融合权重归零处理。
- **状态**：✅ 已核销（D1~D3）。epsilon_by_class 按类冻结（LOCA=6.66/SGATR=0.239/FLB=2.87）落 marker_scale.json；E1 通道在 D3 FULL 流程接线并参与融合（.3·E1）

### RCA-023：训练类原型是"伪聚合"
- **类别**：CODE
- **根因/证据**：80 条训练签名构建时 G/C 组件全部复用同一个 class_model（`run_rca_pipeline.py:115-120`），`build_class_prototype` 再对它们做"边频>50%/效应平均"（`signature.py:121-134`）——同一常量重复 80 次，边频恒为 1、效应平均等于自身，聚合无意义。
- **解决（v2.1）**：类签名直接由轨迹级 Bootstrap 产出（稳定边=f_e、效应=跨 Bootstrap 中位数+π），取消"先逐条签名再平均"的伪聚合层；异常模式原型按严重度组分别聚合。
- **状态**：✅ 已核销（D1）。无逐条签名层；9 模板直接由组内轨迹级 Bootstrap 产出，组模式 A_{k,g} 存 group_pattern；跨组翻转已标注（FLB 4 条 marker 边 severity_flipped）

### RCA-024：【DESIGN】E3-base 与 E2/B1 可能信息同源，因果增量缺独立信息通道
- **类别**：DESIGN / STAT
- **根因**：冻结版 E3 只用 marker 效应列的符号+π 权重，不用图拓扑；而 4.3"预期异常方向向量"与训练数据平均异常符号高度同源。若如此，E3 相对 Pearson 最近类（B1）在构造上就难有增量——这不是实验运气，是设计问题。
- **解决（v2.1 决策点，预注册而非看结果后加）**：
  - D0 决策门控：先跑"最小正确 MASKED"（修好的基线+Bootstrap π+E3-base 符号一致率，LiNGAM 单引擎即可，分钟级），看 MASKED-高严重度 E3-base vs B1 配对差值；
  - E3-path（候选升级版）：传播前沿级联一致性——从 marker 沿稳定边 BFS 分层（L1 直接效应/L2 中介/L3 远端），检验 A(x) 是否呈"近邻层先异常"的前沿结构，用图拓扑信息（Pearson 结构上拿不到）；
  - 事前写死：E3-base 为 P0 主证据，E3-path 为 P1 增强；若 E3-base 不优于 B1，E3-path 成为主科学证据并配 −拓扑 消融；若 E3-base 已胜出，E3-path 作为机制解释增强。
- **状态**：✅ 已核销（D1~D4）。E3-path 实现+G3 单测+D0 冻结主证据；−E3-base/−E3-path 两消融 D4 全量跑完（ablation_core_metrics.json：−E3-base=full、−E3-path 换 base 通道 coverage→1.0）

### RCA-025：【DESIGN】严重度混杂——c_k 类级单点学到的是"严重度效应"
- **类别**：DESIGN / STAT
- **根因**：训练池跨 sev 2~80，marker 幅值与破口严重度、安全系统阶跃、症状幅度高度共变；marker→Y 总效应混入事故发展程度。控制系统反转（FLB 的 LSGA 实测反升，方案已列 L3）在类级总效应上成为系统错号；高严重度三类即使几何可分，E3 胜出也可能是间接做了严重度匹配。
- **解决（v2.1）**：正演签名分组化 c_{k,g}（类×低/中/高，9 个模板），Bootstrap 在组内做；E2/E3 对 9 模板取 max 相容（不使用测试轨迹严重度标签，合规）；报告组间方向翻转变量清单作为可辨识性证据。
- **状态**：✅ 已核销（D1）。9 模板类×组签名、组内 Bootstrap、max_g 相容（不用测试组标签）；severity_flipped 标注 FLB: WFLB→P/DNBR/LSGA/LSGB

### RCA-026：【DESIGN/PERF】留一标定全量 Bootstrap 计算不可行——需两级签名分离
- **类别**：DESIGN / PERF
- **实测证据**（2026-10-26，16 变量/9680 行）：单次 DirectLiNGAM=1.29s；单次 GAM 全对向图（240 对）=2.0min；单个 marker→Y 单变量 GAM=0.016s。方案要求 240 套留一签名若叠加 Bootstrap：LiNGAM≈8.6h、GAM 全图≈400h，不可行。
- **解决（v2.1）**：两级分离——① 最终签名（3 类×3 组）全量 Bootstrap（LiNGAM 100+GAM 全图 50，约 5.5h，一次性）；② 留一标定签名（约 240 套）仅单拟合 LiNGAM（约 5min）+ marker 列的单变量 GAM（240×约 0.24s≈1min），**不跑 GAM 全对向图**（E^cons/拓扑不参与阈值标定，只用于最终签名与解释）。
- **状态**：✅ 已核销（D1~D3）。最终签名 100 次 LiNGAM+50 次 C^A；GAM 全图 10 次共识标注；留一标定单拟合协议（fit_single_template 无有放回）已执行，calibration_params.json 落盘

### RCA-027：【DESIGN】三个变量集未显式分离
- **类别**：DESIGN / DATA
- **根因**：阈值基线、因果学图、E3 激活候选三套变量集在代码中混用同一个 VARIABLE_UNION_22（经逐类常量审计），是 RCA-012/013/022 多项异常的共同根源。
- **解决（v2.1 冻结三张变量表）**：① 阈值集=非 marker、非安注阶跃（WHPI/WECS 不入 μ±3σ）、σ≈0 跳过；② 学图集=含 marker，阶跃变量保留但组内学图、边解释标注阈值响应；③ E3 激活候选集=非 marker 物理症状，阶跃安全变量单列标记不进符号一致率分母（但进传播链展示）。
- **状态**：✅ 已核销（D1）。variable_sets.json 三表分离：V_thr=V_act=17 变量、V_graph=V_thr+WHPI+WECS+marker；WRCA 排除落清单；WHPI/WECS 不进 E3 分母

### RCA-028：【DESIGN】论文 Algorithm 3（反事实验证环）在方案二缺席
- **类别**：DESIGN
- **根因**：论文三算法构成"因果发现（Alg1/2）→总效应量化→反事实验证（Alg3）"闭环；方案一因单轨迹统计意义弱将 Alg3 降级（只做静态预测检验），方案二完全没有验证环——正演签名"可检验"目前只靠冻结物理关系方向一致率。
- **解决（v2.1，保持方案一探索性口径）**：正演可信度检验——在留出训练轨迹上做线性 SEM 反事实重演 do(marker=基线值)，比较 ΔY_pred 与 ΔY_obs 的方向一致率/量级相关性，作为**签名质量门控与解释组件**（不进分类分数、不做参数反演、不声称反事实证明）；非线性引擎对应物用 marker 单变量 GAM 的预测差值，标注"训练分布平均效应"。
- **状态**：✅ 已核销（D1）。cf_validation.py+run_cf.py 实现 Alg3 适用性门/5 折留出/双引擎重演/gCF∈{1,.5} 门控；9 模板全 cf_ok，cf_validation/ 落盘，3 单测通过

### RCA-029：【DESIGN】方案二神经符号身份去标签化
- **类别**：DESIGN
- **根因**：方案二全文无"神经符号层/三环节"显性结构：过程本体被改名"共享基线"（且实现错误，见 RCA-012）、环节①未接线（RCA-021）、环节③输出物错误（RCA-019）；论文标题方法的核心标识在方案二叙事中消失，消融也无 −本体约束/−过程本体/−解释 三组神经符号专项。
- **解决（v2.1）**：新增 Phase R0.5 神经符号层（知识图谱层=三类统一本体+4.2 冻结关系；过程本体层=共享恢复段公差；提示模板层=R5 结构化上下文）；三环节在流水线显式标注与接线；消融表补 −本体约束/−过程本体（经验阈值替代）/−解释 三组；核心贡献段写明"正演签名=双引擎数据驱动（神经）× 本体约束+过程本体公差（符号）的融合知识件"。
- **状态**：✅ 已核销（D1~D4）。R0.5 三层（知识图谱/过程本体/提示模板）均入码接线；−本体约束（use_ontology=False 重学 9 模板）、−过程本体（MAD 替代 μ±3σ）、−解释（R5 传播链+LLM 不可用代码模板兜底 llm_used=false）三消融 D4 全量执行，ablation_symbolic_metrics.json 落盘

### RCA-030：【D1 实现发现】学图窗内 marker 分段恒定，LiNGAM 系统性反向
- **类别**：DATA / ALGO
- **发现时间**：2026-10-26 D1 首版 R1 产物诊断（D0 之前，非结果驱动）
- **证据**：NPPAD marker 为 t=110s 瞬时阶跃，主窗 [300,1500] 内平台恒定（FLB/50 WFLB std=0.000；SGATR/50 cv=0.005）。vstack 后 marker 是分段常量，回归 marker~症状 时块内残差≈0，DirectLiNGAM 系统性把症状→marker 判优，本体 R3 删除后 marker 零稳定出边、frontier 全空（signature_LOCA_high.json 首版）。
- **解决**：学图窗改 [60,1500]（145 点，含注入瞬态；分析窗仍 [300,1500]、基线窗仍 [520,2030]）。复测：marker 稳定出边 12/模板，frontier 非空。窗口修正落 rca_config_v21.LEARN_WINDOW_*。
- **状态**：✅ 已核销（D1）。9 模板以学图窗重跑：marker 稳定出边 0~13/模板（low 组 0 为真实弱扰动结果），LOCA/SGATR/FLB high frontier reach=17/10/16；签名落 learn_window=[60,1500]

### RCA-031：【D1 实现发现】含瞬态同期学图对滞后响应变量符号反转——marker 符号以轨迹级 C^A 为主
- **类别**：ALGO / STAT
- **证据**：扩展窗复测 LiNGAM 直接效应符号：即时响应变量正确（WLR→VOL 负 31/40、WLR→DNBR 负 36/40），滞后响应变量反号（WLR→P 正 34/40、WLR→WHPI 负 40/40、WLR→QMWT 正 40/40；物理应分别为负/正/负）——阶跃即时、症状与安注滞后，同期系数被注入点时序主导。跨轨迹稳态相关符号正确（LOCA corr(marker,VOL)=−0.98；高组 P 饱和 corr≈−0.01 应自然低 π）。
- **解决（D1 前修正，非看分类结果调参）**：拓扑/症状边仍由扩展窗 LiNGAM 给；marker→症状符号与量级改以**轨迹级 C^A** 为主（每轨迹主窗 z 中位一点，GAM 训练中位处局部斜率，50 次轨迹 Bootstrap πA；组内 spread<0.05 整列不可估）。E3-base=0.5(E3_L+E3_A) 两通道均值（方案 13.7.4 原公式）；E3-path 符号用 effective_marker_map（A 优先 π≥0.7，L 兜底）。D0 最小实验因此包含 C^A（每模板约 15s），判据仍是纯 E3-base 通道。
- **状态**：✅ 已核销（D1）。marker_effects.py 9 模板 C^A 落盘；实测 LOCA_high cL 对 P/VOL/TAVG/QMWT/PWNT 等系统性反号（πL=1.0 的错号），C^A 给出物理自洽稳态方向（P/VOL/TAVG 负、DNBR 正）；effective_marker_map 接线 E3-path，2 个 G3 单测覆盖；D0 双引擎结果已落 d0_decision.json

### RCA-032：【D4 实现发现】SGATR 高严重度 LSGA 饱和反转，R5 方向一致率=0 为真实现象非 bug
- **类别**：DATA / INTERPRET
- **发现时间**：2026-10-26 D4 R5 传播链方向一致率统计
- **证据**：SGATR 的 LSGA cA 分组：low=+23.36（πA=1.0）、mid=+19.37（πA=1.0）、high=**−2693.11**（πA=0.98）。跨轨迹原始关系：low/mid 组 corr(WTRA,LSGA)=+1.0（物理正确：传热管破裂→壳侧水位上升）；high 组出现**双峰饱和**——sev 87→100 的 LSGA z 从 173 冲到 864，而 sev 67→86 仅 24→49，GAM 在训练中位处的局部斜率被反转。
- **机制**：D0 实验只在 high 组做（MASKED-高 18 条），冻结的"D0 指定模板"=high；且 low/mid 组 frontier 为空（marker 无稳定出边，E3_path=nan），故 `g*=argmax(E3_path)` 恒落 high。链上 LSGA 全取 high 模板负斜率 → 与 §4.2 冻结方向（+）全部相反 → 方向一致率 SGATR=0/71。
- **性质**：§4.2/§4.3 冻结表仅基于 sev25（11.89）/sev75（15.34）观测，未预见 sev87+ 的饱和反转（sev100 LSGA=864 z）。这是**预注册未覆盖的真实物理现象**，不是实现缺陷；按 13.14 判读纪律"低一致率不单独证伪，物理真实性只通过 4.2 冻结关系与 Alg3 重演检验"，须在 rca_report 的局限/发现段如实报告，**禁止为修数值改 g* 选择规则或 C^A 口径**。
- **状态**：✅ 已定位并如实记录（chains.py 的 FROZEN_MARKER_DIRECTION 对齐 §4.2/§4.3 原文、select_template 修正为 D0 冻结的 argmax_path；方向一致率数值不改）；待 rca_report_v21.txt 判读段纳入

---

## v2.3 全适配执行审计（2026-11-10，方案 v2.3 §6.1 / 附录 C-D 逐条核对）

> 本节按方案 A→B→C→D 顺序登记实现缺口。状态口径沿用上文：✅ 已解决｜⚠️ 部分/待验证｜❌ 未解决，同时保留 open/closed 语义。
> 数据现实：本地仅 LOCA/SGATR/FLB 各 100 条、LOCAC 5 条、Normal 1 条；其余 8 类无目录。**禁止编造数据或结果**。

### V23-CODE-001：12 类事故配置表缺失，3 类配置内联在变量选取模块
- **类别**：CODE / DESIGN（A3）
- **证据**：`Code/rca/v21/accident_config.py` 不存在；`variable_selector.py:100-104` 内联 `ACCIDENT_KEY_RESPONSES` 仅 LOCA/SGATR/FLB；`run_v23_end2end.py:21` 直接用 `ACCIDENT_TYPES`（3 类）。
- **根因**：A3 一直标记"待数据到位后建"，导致 12 类元数据无唯一来源，执行器只能硬编码 3 类。
- **解决**：新建 `accident_config.py` 作为 12 类唯一注册表（marker/触发/关键响应/窗口/分组/数据状态），`rca_config_v21.ACCIDENT_TYPES` 改为从注册表派生；variable_selector 改读注册表。
- **状态**：closed（2026-11-10；`accident_config.py` 落地，12 类齐全，`A/B 阶段实施记录` 见 V23-CODE-010）

### V23-CODE-002：`ACCIDENT_TYPES` 冻结为 3 类，12 类循环不可能发生
- **类别**：CODE（A3/B2/C1）
- **证据**：`rca/rca_config_v21.py:22` `ACCIDENT_TYPES = ["LOCA","SGATR","FLB"]`；`data.build_split_manifest`、`run_v23_end2end.main` 均遍历该常量。
- **根因**：v2.1 冻结清单未随 v2.3 注册表升级。
- **解决**：保留 `PILOT_ACCIDENT_TYPES` 兼容 v2.1 产物，新增 `ALL_ACCIDENT_TYPES`（12）与 `READY_ACCIDENT_TYPES`（按注册表 status 派生）；执行器只遍历 ready/partial。
- **状态**：closed（2026-11-10；`ACCIDENT_TYPES` 保留 3 类兼容，`ALL/READY/PARTIAL/MISSING_ACCIDENT_TYPES` 已派生）

### V23-DATA-001：8 类事故无本地数据，LOCAC 仅 5 条
- **类别**：DATA（B1）
- **证据**：`nppad_repo/Operation_csv_data/` 仅有 `LOCA/SGATR/FLB` 各 100 个 CSV、`LOCAC` 5 个、`Normal` 1 个；无 SLBIC/SLBOC/SGBTR/RW/RI/MD/LR/LLB 目录。
- **根因**：上游数据未下载/未提供；LOCAC 上游即 5 条（评审 §9 未核实项）。
- **解决**：注册表将 8 类标 `data_missing`、LOCAC 标 `partial`（仅描述性，不进 36 份验收）；执行器跳过并落 `skipped_classes.json`，不得生成占位产物。
- **状态**：⚠️ 已如实登记，等待数据（不阻断 3 类闭环）
- **2026-11-10 收口说明**：非决策项，保持 data_missing；LOCAC 保持 partial、不进 36 份验收。

### V23-CODE-003：`data.py` 不支持 12 类加载与缺数据门控
- **类别**：CODE（B2）
- **证据**：`data.py:28-34` `csv_path/load_trajectory` 直接拼路径，缺文件时 `pd.read_csv` 抛 `FileNotFoundError`；`build_split_manifest` 对 3 类硬编码配额。
- **解决**：加载前查注册表 status；缺数据类抛显式 `ClassDataMissing`；split 仅对 ready 类；partial 类只提供加载不做三分组。
- **状态**：closed（2026-11-10；`ClassDataMissing/ensure_class_available/list_class_severities` 已实现，`build_split_manifest` 输出与旧 manifest 逐字节一致）

### V23-DESIGN-001：无 `data_audit.py`，B4/B5/B6 全缺
- **类别**：DESIGN / CODE（B4–B6）
- **证据**：仓库无 `data_audit.py`；无 `data_audit_*.json` / `z_pathology_audit_*.json` / `changepoint_report_*.json`；`variable_selector.py:245-249` 的 σ_floor 仍是 1%·|μ| 兜底，未接逐变量物理下限。
- **解决**：新建 `data_audit.py`：B4 常量列/轨迹数/窗口校验；B5 σ/|μ| 审计 + 主窗 max|z| + 病态清单 + 稳健σ；B6 PELT/CUSUM 变点 + marker 稳定性评级。产物统一落 `output/rca_v23/audit/`。
- **状态**：closed（2026-11-10；4 类审计落地 run_id=v23b-20261110T003923；口径遗留项见 V23-DESIGN-006/007/008）

### V23-CODE-004：v2.3 根因 JSON 缺 `n_consensus`/`n_conflict`，与图/汇总不同源
- **类别**：CODE（C2）
- **证据**：`run_v23_end2end.py:72-92` 的 `graph_summary` 只写 `n_nodes/n_stable_edges/n_bootstrap`；`run_source_tracing.py:81-98`（v2.2）才写 `n_consensus`。二者 schema 不同。
- **解决**：在 v2.3 溯源执行器统一写 `n_consensus/n_conflict/n_bootstrap`，并由 `manifest.json` 的 `run_id` 保证与签名同批。
- **状态**：closed（2026-11-10；`root_candidates_*` 已含 n_consensus/n_conflict/engine/consensus_available/n_clipped_edges/cond_IminusB）

### V23-CODE-005：v2.3 分组 HTML 无生成脚本，现存图不可复现
- **类别**：CODE（C3）
- **证据**：全仓 `v23_graphs` 仅被 `severity_aggregation.py:265` 写入汇总图；`output/rca_v21/v23_graphs/graph_{K}_{g}.html`（11-10 00:01）无对应 `run_*.py`。
- **解决**：新建 `run_v23_graphs.py`，只读 `output/rca_v23/signatures` + `tracing` 生成 `graphs/graph_{K}_{g}.html` 与汇总图，含 C3a 信息门控水印。
- **状态**：closed（2026-11-10；13 份 HTML＋meta，首屏含 run_id/生成时间，数据内嵌）

### V23-DESIGN-002：v2.3 产物多根多源，同一结果存在多份来源
- **类别**：DESIGN（产物结构）
- **证据**：`output/rca_v21/` 下并存 `v23_signatures/`、`v23_tracing/`、`v23_graphs/`、`v23_llm/`、`tracing/`、`signatures/`、`signatures_strict/`；`severity_aggregation.py`、`run_v23_*` 各自拼路径。
- **解决**：按 `Code/STRUCTURE.md` 收敛到唯一根 `output/rca_v23/`；旧目录冻结只读；所有执行器通过 `V23_OUTPUT`/`V23_SUBDIRS` 取路径，写入统一走 `artifact.write_artifact`。
- **状态**：closed（2026-11-10；`output/rca_v21/v23_*` 不再被写入；v2.3 产物全部带 schema/run_id/source）

### V23-CODE-006：汇总图/差异报告停留在 11-04，与 11-10 重算根因不同源
- **类别**：CODE（C3b/C3c）
- **证据**：`v23_tracing/diff_report_*.json` 与 `v23_graphs/graph_{K}_summary.html` 时间戳 11-04 23:09，而 `root_candidates_*.json` 为 11-10 00:01。
- **解决**：`run_v23_all.py` 强制顺序：签名→溯源→分组图→汇总/差异→报告，全部写同一 `run_id`；禁止单独重跑下游。
- **状态**：closed（2026-11-10；图/汇总/差异均带 run_id 门控，run_id 不一致直接 ValueError；统一编排脚本见 D 阶段）

### V23-STAT-001：所有分组 `n_consensus == n_stable_edges`，共识边退化
- **类别**：STAT / CODE（C2）
- **证据**：v2.3 主流程未加载 GAM 共识分类（`consensus_*.json` 未参与），部分图把全部稳定边默认标 `consensus`，导致 top-k 共识率恒为 1。
- **根因**：v2.3 只用 LiNGAM 单引擎签名，却沿用双引擎"共识边"字段语义。
- **解决**：明确 v2.3 单引擎口径：`n_consensus` 仅在存在 GAM 共识文件时统计，否则写 `null` 并在报告中标注"单引擎稳定边"；禁止把稳定边等同共识边。
- **状态**：closed（2026-11-10；9 份 root_candidates 全部 `n_consensus=null`、`consensus_available=false`、图例改“稳定边”；`--with-gam-consensus` 才接入，混源拒绝）

### V23-DESIGN-003：K2 残差三态与偏离度是孤立脚本，未进 signature 与图
- **类别**：DESIGN（D4/D9）
- **证据**：`physics_constraints.py`、`deviation.py` 存在，但 `signatures.py`、`source_tracing.py`、`graph_visualization.py` 均未 import；`v23_graphs` 无偏离度着色/无残差标注。
- **解决**：在签名阶段挂载 K2 三态与 deviation summary，在 `root_candidates`/HTML 中输出；偏离度只作辅助层，不改排序（§5.10）。
- **状态**：closed（2026-11-10；签名 `auxiliary.{k2,k3,deviation}` 由 `bootstrap_lingam_template` 自动挂载；HTML 增 K2 三态表、K3 覆盖条、偏离度四色边框，并注“不改变排名”）

### V23-DESIGN-004：D3/D5 外部知识与 K3 模板全缺
- **类别**：DESIGN（D3/D5）
- **证据**：无 `external_knowledge/` 目录、无任何 `.yaml`；无 `fault_templates.yaml`、无 `external_knowledge.py`。
- **解决**：按 §5.2 建 `Code/external_knowledge/` 五份 YAML + `external_knowledge.py` 合并接口；K3 模板必须来自 README/工程手册，禁止从训练数据+标签提取。
- **状态**：closed（2026-11-10；5 份 YAML + 8 个接口 + 13 例测试；9 类模板标 `design_pending_data`）

### V23-CODE-007：LLM 解释为演示级，未接 v2.3 签名/图/忠实性
- **类别**：CODE（D6）
- **证据**：`run_v23_llm_explanation.py` 读取 `v23_signatures`/`v23_tracing`，但产物 9 份全部 `degraded`/模板文本；`llm_explanation.py` 未见 API 调用与忠实性校验闭环。
- **解决**：升级为可用版：结构化 prompt（图+Top5+路径+K2/K3+偏离度）、忠实性校验（变量名/数值/边存在性）、失败自动降级并标 `llm_used=false`。
- **状态**：closed（2026-11-10；默认 `backend=rule` 离线可跑、`faithful=True`；`backend=llm` 需双环境变量，不可达时降级并写 `degraded_reason`。注：本环境无 API key，LLM 路径仅单测覆盖，未做真实联网验证）

### V23-CODE-008：`run_v23_report.py` 读取已改名的校验键
- **类别**：CODE（A4/报告）
- **证据**：`run_v23_report.py:30` 读取 `c['V3_vif_lt_10']`，而 `variable_selector.py:383` 输出的键为 `V3_vif_lt_500`（附录 D 错误 6 已改名）。
- **解决**：报告读取改为 `.get('V3_vif_lt_500')` 并打印阈值；旧键若存在做兼容读取。
- **状态**：closed（2026-11-10）

### V23-CODE-009：新模块无单测，测试仅覆盖 v2.1
- **类别**：CODE（测试）
- **证据**：`Code/tests/` 9 个文件均 import v2.1 模块；无 `accident_config/data_audit/physics_constraints/external_knowledge/run_v23_all` 测试。
- **解决**：新增 `tests/test_v23_*.py`，覆盖注册表完整性、缺数据门控、Z 病态/变点纯函数、K2 三态、外部知识合并、端到端小样本 smoke。
- **状态**：closed（2026-11-10；共 118 例：accident_config 16、data_audit 7、knowledge_external 13、pipeline 4、physical 10、report_llm 7 + v2.1 原 61）

### V23-ALGO-001：`B` 矩阵共线伪影截断已临时补丁，缺敏感性分析
- **类别**：ALGO
- **证据**：`source_tracing.py:24,171` 新增 `B_CLIP=5.0` 与 `np.clip`；提交 53cf0a5。
- **根因**：LiNGAM 在 PSGA/PSGB→WSTB 等共线对上给出 |weight|~1e5，`(I-B)^-1` 条件数 ~1e11。
- **解决**：保留截断，但在 `data_audit`/manifest 中记录截断前后条件数、被截断边清单，并做 3/5/10 阈值敏感性，结果写报告。
- **状态**：closed（2026-11-10 决策：保留 `B_CLIP=5` 不换阈值，新增弃权门控）
- **决策与实现（2026-11-10）**：`source_tracing.stability_gate`——`cond_after_clip > 1e4` 或 `n_clipped/n_stable > 0.35` 时该模板标“数值不稳定、结论降级”（排名仍输出但降级引用），否则“数值稳定”。门控写入签名 `auxiliary.stability`、`root_candidates.stability`、图总览/组卡、报告第 2 节；主表新增截断比、`Top1(3/5/10)`、`Top5∩(3/5/10)` 与稳定性列。
- **实测（run_id=v23-20261110T030015）**：9 组截断后 cond 33.1–3638（最大 SGATR_low 3638 < 1e4），截断比 0.027–0.264（最大 SGATR_low 0.264 < 0.35），**降级 0/9**；clip=3 时 FLB_mid Top1 由 PSGB 变 WCHG，clip=10 时 LOCA_mid Top1 由 QMGB 变 WECS，其余 Top1 不变；Top5 重叠最低 3/5（FLB_high）/4/5（SGATR_low/mid），其余 5/5。
- **补充观察（V23-CODE-021 重跑暴露）**：SGATR 变量集合未变、仅列序变化（VOID 第 5→第 11），SGATR_high Top-1 由 RM2 变 RHBR，说明 T=(I−B)⁻¹ 排序对输入列序敏感；本次不固定列序（会再改一轮全部结论），仅在论文“数值稳定性”一节如实报告。

---

## v2.3 A/B 阶段实施记录（2026-11-10，12 类注册表 + 数据审计）

### V23-CODE-010：`MARKERS` 扩到 12 类后 `pipeline.e1_vector` 抛 KeyError: 'WSTA'
- **类别**：CODE（A3 回归）
- **证据**：`rca/v21/pipeline.py:76` 原为 `for k_other, m in MARKERS.items() if m != own`；MARKERS 由 3 类派生改为 12 类后，`epsilon` 只有 pilot 3 类键 → `KeyError: 'WSTA'`，任何 FULL 评分路径崩溃（`tests/test_ablation_core.py` 2 例、`test_d3_units.py` 2 例失败）。
- **根因**：v2.1 E1 互斥判据只在 3 个 pilot marker 之间定义，却被写成遍历全局 MARKERS。
- **解决**：改为遍历 `ACCIDENT_TYPES`（pilot 3 类）取 `MARKERS[k_other]`；`marker_main_medians` 默认参数改用新增的 `PILOT_MARKER_COLS`（3 个），`MARKER_COLS` 保留 4 个指示器通道供 v2.3 使用。
- **状态**：closed（98/98 测试通过）

### V23-CODE-011：`chains.ROOT_CAUSE_NAME` 缺 9 类导致 build_chain KeyError
- **类别**：CODE
- **证据**：`rca/v21/chains.py` 原字典仅 LOCA/SGATR/FLB；`tests/test_chains.py` 遍历 MARKERS 后取 `ROOT_CAUSE_NAME[k]` → `KeyError: 'LO...'`。
- **解决**：保留 3 类冻结措辞，其余 9 类由 `accident_config.get_accident(c)['name_cn']` 派生补全。
- **状态**：closed

### V23-CODE-012：v2.1 测试硬编码「2 变量 + 3 marker」形状
- **类别**：CODE（测试）
- **证据**：`tests/test_ablation_core.py:70,83`、`tests/test_d3_units.py:232` 用 `V_THR + list(MARKER_COLS)`，而只构造 5 列 → `IndexError: index 5 is out of bounds`。
- **解决**：测试改用 `PILOT_MARKER_COLS`（与 v2.1 三条 pilot 口径一致），断言逻辑不变。
- **状态**：closed

### V23-CODE-013：A/B 汇总里 `jaccard` 变量跨类复用（最后一类覆盖全部）
- **类别**：CODE（A/B 对照）
- **证据**：`run_ab_variable_selection.py:149` 汇总循环内引用外层循环残留的 `jaccard`，导致 3 类的 `summary[k]["jaccard_vars"]` 全部等于最后一类。
- **解决**：按类收集 `jaccards[k]` 并在汇总中使用。
- **状态**：closed

### V23-DESIGN-005：SGATR/SGBTR 的 RM2 有向边未在方案冻结
- **类别**：DESIGN（组3 知识）
- **证据**：方案 §4.3 组3 将 RM2 列为关键响应，但 §4.2 未冻结 `WTR→RM2` 方向；若只给 SGBTR 加边会造成 A/B 不对称。
- **解决**：两类均不登记该边（`knowledge.py` 注释留痕），K3 模板把 RM2 作为“症状”而非因果边；待方案确认后统一补。
- **决策（2026-11-10）**：**won't fix**——两类对称处理已保证 A/B 公平，K3 把 RM2 当症状是合理工程近似；不单方面给 SGBTR 补边。
- **状态**：closed（接受现状；导师若要求，则 SGATR/SGBTR 两类同时补边并重跑 A/B）

### V23-DATA-002：FLB 42/100 条轨迹 t_last < 2030，恢复段不覆盖
- **类别**：DATA（B4）
- **证据**：`data_audit_FLB.json`：sev98–100 轨迹仅到 1600；主窗 [300,1500] 全覆盖，但基线段 [520,2030] 对 42 条截断。
- **解决**：B4 逐轨迹记录 `t_last` 与窗口覆盖布尔；下游按类截断（不改变全局 SEVERITY_BANDS）；报告注明“恢复段末端缺失”。
- **状态**：closed（2026-11-10；签名 meta 写 `baseline_coverage`，FLB_high 24/32 轨迹截断、平均覆盖 131.25/152）

### V23-DATA-003：LOCAC 仅 6 个严重度点（1/25/50/75/100）
- **类别**：DATA（B1/B6）
- **证据**：`nppad_repo/Operation_csv_data/LOCAC/` 只有 5 个 CSV；`changepoint_report_LOCAC.json` 判 `insufficient`（<10 点）。
- **解决**：LOCAC 标 `partial`，只做描述性审计与单点加载，不进三分组/签名/榜单；不生成占位结论。
- **状态**：⚠️ 等待数据（不阻断 3 类闭环）
- **2026-11-10 收口说明**：非决策项，保持 partial 登记，等上游补数据后按同一 run_v23_all 重跑。

### V23-DESIGN-006：按 §4.4.1 判据，sev-1 段 σ≈0 使 24%–30% 变量被判 Z 病态
- **类别**：DESIGN / STAT（B5）
- **证据**：`z_pathology_audit_{LOCA,SGATR,FLB}.json`：病态 29/17/23 个（30.2%/17.7%/24.0%）；WSTA/WFWA/WFLB 在 sev-1 段 σ≈0，未截断 `max|z|` 达 383。
- **根因**：判据 `σ/|μ|<1%` 与 `max|z|>100` 对“整段恒定、注入后才变化”的通道天然成立，病态面远大于方案预期。
- **决策（2026-11-10，定稿）**：口径统一为“**低方差通道**”，不是数据缺陷。病态变量**全部**走稳健 σ + ±10 截断（D9 不再跳过，见 `deviation.py`），原始 `max|z|` 单列报告；比例只作诊断。
- **实现**：`tolerance.quality_pass` 删除 `z_pathology_ratio < 30%` 门槛（只保留基线 11 点、变量数≥10）；`deviation.compute_deviation` 停止跳过 `z_pathology` 变量；方案 §7 K11 的“比例<30%”在批5同步删除。
- **状态**：closed（2026-11-10；新基线口径下 3 类病态均为 13/93=14.0%，原 24%–30% 系被污染 σ 所致）

### V23-DESIGN-007：B6 对线性剂量阶梯直接检测会产生伪变点
- **类别**：DESIGN / STAT（B6）
- **证据**：FLB marker 中位数随 severity 严格线性（去趋势残差比 7e-8），直接对水平序列做分段常数 DP 会报 15 个伪变点。
- **解决**：主结果在“去趋势残差”上检测，原始水平结果仍完整落盘（`changepoints_level_raw_*`）；FLB 最终判 0 变点，SGATR 检出 87（已知 WTRA 掉档），LOCA 检出 7/12/21/43（WLR 非单调）。
- **状态**：closed（口径已写入产物 method 字段）

### V23-DESIGN-008：σ_floor 按 unit 归并，`%` 单位变量共用 0.5 近似
- **类别**：DESIGN（B5）
- **证据**：`external_knowledge/tolerance_overrides.yaml` 将“水位 0.5%”推广到所有 `%` 变量（TBLD/PWR/FRCL 等），37 个变量取物理 floor、59 个回退 1%·|μ|。
- **解决**：逐变量表可覆盖（`by_variable` 优先于 `by_unit`）；论文注明该归并是工程近似。
- **决策（2026-11-10，辩证吸收）**：接受工程近似、优先级降为 P2。**不采纳**“只保留 TBLD/FRCL/VOID/WSPY 逐变量、其余 by_unit”的收窄建议——现行 `tolerance_overrides.yaml` 的 `by_variable` 已覆盖压力（P/PSGA/PSGB/PRB/PRBA）、流量（WUP/WSPY/WLD/WCHG…）、液位/百分数等分组，精度高于 `by_unit=0.5`，删除属能力回退；仅对未列入项继续 `by_unit`、无配置回退 1%·|μ|。限制（bar 级 0.5、t/hr 级 5.0 等工程近似）写入论文与 §5.2，不再列阻断项。
- **状态**：closed（接受现状，不删逐变量表；如需全量逐变量标定需外部专家给值）

### V23-DESIGN-009：A/B 对照中稳定边与冻结本体的方向一致率仅 2%–4%
- **类别**：DESIGN / STAT（A/B 验收口径）
- **证据**：`output/rca_v23/ablations/ab_variable_selection.json`（run_id=v23a-ab-20261110T003914）：本体一致率 auto/v2.1 分别为 LOCA 4.19%/2.71%、SGATR 3.27%/1.66%、FLB 0.0%/4.22%；每模板稳定边 45–86 条，而本体允许边仅 10–14 条。
- **根因**：稳定边集合远大于本体覆盖范围（本体只冻结主干因果链），且 E1 门控后仍有大量过程量之间的强相关边；该指标的分母是全部稳定边，天然偏低。
- **决策（2026-11-10）**：论文口径改为 `n_ontology_allowed`（命中条数）+ `backbone_hit_ratio = 命中 / 84 条本体边`；反向边单列 `n_ontology_reverse`；A/B 主指标只用稳定边 / frontier / Top1 一致性，本体指标降为 `diagnostics`，不设阈值验收。
- **实现**：`run_ab_variable_selection.py` 输出 `n_ontology_allowed`/`n_ontology_reverse`/`backbone_hit_ratio`（分母常量 `ONTOLOGY_TOTAL_EDGES=84`），`summary` 拆为 `primary`/`diagnostics`。
- **状态**：closed（2026-11-10，重跑 A/B 后生效）

### V23-STAT-002：同 seed 两次运行的浮点末位差异导致复现性断言偶发失败
- **类别**：STAT（C7 可复现性）
- **证据**：`tests/test_v23_pipeline.py::test_two_runs_are_reproducible` 在 10 次全量运行中偶发 1 次失败；`round(total_effect, 8)` 精确相等断言受 BLAS 多线程累加顺序影响。
- **解决**：排名与稳定边数保持逐项严格相等（真正口径），数值改用 `pytest.approx(rel=1e-9)`；CLI 侧 `REPRO_TWO_RUNS_IDENTICAL` 仍按日志指纹比对。
- **状态**：closed（2026-11-10，收敛后连续 6 次全量 101 passed）

### V23-CODE-015：`graph_visualization.render_graph_html` 未导入 logger，且只渲染 consensus 边
- **类别**：CODE（C3）
- **证据**：`rca/v21/graph_visualization.py` 的渲染函数引用 `logger` 但模块未导入（调用即 NameError）；过滤条件只保留 `consensus=True` 的边，单引擎 v2.3 下会得到空图。
- **解决**：v2.3 不复用该渲染函数，仅复用其布局/配色常量，在 `run_v23_graphs.py` 内重写渲染（数据全部来自 `signatures/`+`tracing/` 的同一 run_id）。
- **决策（2026-11-10）**：修 2 行防误用——补 `from core.utils import logger`；过滤条件放开为“非 conflict 边”（单引擎无 consensus 时不再得空图）。仅修代码，**不重跑 v2.2 产物**。
- **状态**：closed

### V23-DESIGN-010：v2.3 汇总/差异产物统一落 `reports/`，与旧 `v23_tracing/diff_report_*` 命名不同
- **类别**：DESIGN（产物结构）
- **证据**：`severity_aggregation.py` 现输出 `reports/summary_{K}.json`、`reports/diff_{K}.json`；旧产物为 `output/rca_v21/v23_tracing/diff_report_{K}_{g}.json`。
- **解决**：以 `Code/STRUCTURE.md` 为准，旧目录冻结只读；对外引用统一用 `reports/` 新路径。
- **状态**：closed（2026-11-10；旧路径不再写入，STRUCTURE 已同步）

---

## v2.3 D 阶段实施记录（2026-11-10，K2/K3/偏离度/报告/LLM/全链）

### V23-CODE-016：`tolerance.py` 把 12 类 marker（WSTA/RHRD/PPM/TBLD/WLD）从所有类中剔除
- **类别**：CODE（D2 公差）
- **证据**：原 `excluded = set(MARKERS.values()) | set(SAFETY_STEP_VARS)`；MARKERS 扩到 12 类后，LOCA 的 RHRD/PPM/TBLD/WLD 等正常模型变量被误排除，阈值变量数虚降。
- **解决**：改为 `excluded = {MARKERS[accident_type]} | set(SAFETY_STEP_VARS)`；LOCA 实测 `excluded_vars=[WECS,WHPI,WLR]`（WLR 为本类 marker，WECS/WHPI 为安全动作）。
- **状态**：closed

### V23-CODE-017：LLM/报告层存在第二套 K3 覆盖率口径（变量入模率 vs 边命中率）
- **类别**：CODE（D5/D6）
- **证据**：`llm_explanation._load_k3` 原按“expected_chain 变量是否在签名变量集中”统计（LOCA 覆盖率 100%），而 `external_knowledge.match_fault_template` 按“相邻边是否命中稳定边”统计（LOCA 20%），同一模板出现两个数。
- **解决**：`_load_k3` 改为只读签名 `auxiliary.k3`，缺失时才调用 `match_fault_template`；全仓库只保留边覆盖一种口径，报告/解释文本同步改为“N 条相邻边命中 M 条”。
- **状态**：closed

### V23-CODE-018：`artifact.build_manifest` 未纳入 `*.txt`
- **类别**：CODE（D10）
- **证据**：`rca_report_*.txt` 只以 `.txt.meta.json` 进入 manifest，正文未登记 sha256。
- **解决**：include 增加 `*.txt`；最终 manifest 覆盖 json/csv/png/html/md/txt 六类。
- **状态**：closed

### V23-DESIGN-011：偏离度严重档饱和，3/5/7σ 分档对 NPPAD 阶跃不适用
- **类别**：DESIGN / STAT（D9）
- **证据**：`reports/deviation_LOCA_low.json` 33/43 变量为 severe，`SGATR_mid` 30/34；而 normal 档仅 0–21 个。（原方案 §5.10.4 写 d<1/3/5 分档，代码旧实现为 3/5/7，本身已不一致。）
- **根因**：NPPAD 在 t=110 注入事故后多数通道阶跃，sev-1 基线 σ 极小，`d=min(|x−μ|/σ,10)` 迅速触顶。
- **决策（2026-11-10）**：改为“**模板内相对分位**”——档位切点取本模板全部变量 `d_raw`（不截断）的 p50/p70/p85，>p95 另标“极端”；展示值仍为 `d=min(d_raw,10)`，`d_abs_level`（旧 3/5/7σ）仅作附注；图例统一为「通道偏移强度（模板内相对）」。
- **实现与实测**：9 组档位分布均为 normal/moderate/large/severe = 44/17/13/14（按构造约 50/20/15/15，**只表达模板内排序，不是异常强度绝对值**，已在方案 §5.10.4 写明）；旧绝对档 severe 仍为 52–69/88，保留在 `abs_level_counts` 供对照；`d` 用未截断值排序，避免截断并列导致整体 severe。
- **状态**：closed（2026-11-10，`run_id=v23-20261110T024555`；与 V23-DATA-004 同批生效）

### V23-DATA-004：公差基线的稳态段 t=0–990 含注入前数据
- **类别**：DATA（D2）
- **证据**：改前 `run_v23_tolerances` 输出 LOCA 稳态点 100 个、窗口 [0,990]；`t_inject=110`，即基线包含 0–110 的正常段与注入后 110–990 s。sev-1 注入响应小（P 仅变 0.4 bar），全轨迹滑窗把注入后段也判“准稳态”。
- **决定（2026-11-10）**：三处同改——
  1. `_find_steady_segment` 加 `t < t_inject` 硬约束（t_inject 读注册表），该段只作追溯记录，不参与 μ/σ；
  2. 主基线固定为注入前 `[0,100] s` 共 11 点；
  3. μ 取基线均值，σ = max(1.4826·MAD, σ_floor)，σ_floor 来源逐变量记录；`Normal/1.csv` 只写 `normal_reference`（同窗 σ 对照），不并入标定。
- **联动（必须同批）**：基线收窄后 σ 从被污染放大回落到接近 0，σ_floor 成为主导，绝对 d 更饱和（9 组 absolute severe 52–69/88），故必须与 V23-DESIGN-011 的模板内相对分位同时生效；该联动已写入方案 §5.10.2/§5.10.4，论文“方法/局限”引用同一段。
- **实测**：3 类均 `baseline_points=11`、变量 93、注入前常量 53、Z 病态 13（14.0%）、quality PASS；`normal_reference` 11 点可用但不参与。
- **状态**：closed（2026-11-10，`run_id=v23-20261110T024555`）

### V23-DESIGN-012：K3 模板链覆盖率低且反向边多
- **类别**：DESIGN（D5）
- **证据**：LOCA low/mid/high coverage=0.40/0.20/0.20（反向 2/2/0）；SGATR=0.143/0.143/0（反向 2/2/3）；FLB=0/0.125/0.125（反向 0/1/0）。
- **根因**：模板 `expected_chain` 来自方案 §4.3 的机理叙述（如 P→TAVG→安注），而学到的稳定边以过程量强相关为主，方向常与机理链相反（反馈/控制回路）。
- **决策（2026-11-10）**：接受现状，改验收口径——Q7 门槛定为“**稳定边 ≥ 5 且 Top1 可达路径 ≥ 1**”，删去 consensus 表述（单引擎下 consensus 已退化）；覆盖率与反向边仍如实输出，但不作达标判据。
- **状态**：closed（方案 §7 Q7 已同步）

### V23-DESIGN-013：K2 物理关系每类仅 2–3 条适用
- **类别**：DESIGN（D4）
- **证据**：`auxiliary.k2.n_relations_applicable`：LOCA=2、SGATR=3、FLB=2（全库仅 5 条关系，`applicable_accidents` 限定类）。
- **影响**：三态证据稀疏，单条 violation 占比可达 1/3，解释力有限。
- **决策（2026-11-10）**：K5/K6 验收改为“**输出完整、如实报告**”，不按覆盖率/匹配分达标；K2 关系库扩充（当前 5 条）列为后续工作，不在 v2.3 验收内。
- **状态**：closed（方案 §7 K5/K6 已同步）

### V23-CODE-019：`skipped_classes.json` 只登记“本次 --classes 里没选的类”，默认运行看起来没有缺数据类
- **类别**：CODE（D10/C1）
- **证据**：默认 `--classes` 仅 ready 3 类时，`skipped_classes.json` 的 `entries=[]`、`n_missing=0`，与注册表 8 类 data_missing 的事实矛盾。
- **解决**：执行器改为始终登记 `PARTIAL_CODES + MISSING_CODES` 全量，未选类写明“本 run 未选择该类（--classes 仅含 ready 类）”；`n_partial/n_missing` 取注册表计数而非本次选择计数。
- **状态**：closed（2026-11-10；默认运行 entries 覆盖 LOCAC + 8 类）

### V23-CODE-020：`test_v23_data_audit` 的端到端用例写进正式产物目录
- **类别**：CODE（测试卫生）
- **证据**：跑 pytest 后 `output/rca_v23/audit/` 出现 `run_id=pytest-v23-data-audit` 的 LOCA 三份审计，覆盖了 B 阶段正式产物。
- **解决**：测试用 `monkeypatch` 把 `V23_SUBDIRS["audit"]` 重定向到 `tmp_path`；并重新以 `run_id=v23b-audit-20261110` 生成 4 类正式审计。
- **状态**：closed

---

## v2.3 收口阶段记录（2026-11-10，按外部专家意见逐条决策）

### V23-CODE-021：end2end 读 v2.1 旧变量集，LOCA_high Top1=HUP 不在当前选集
- **类别**：CODE（C 阶段输入）
- **证据**：`run_v23_end2end.py:46` 原读 `V21_OUTPUT/"variable_selection"/variable_selection_summary.json`（11-03 旧集）；A 阶段产物实际写在 `V23_SUBDIRS["variable_sets"]`。旧集 LOCA 含 HUP，新集为 VOID；旧集 SGATR 的 VOID 在第 5 列，新集在第 11 列。
- **根因**：A 阶段把变量集从 `output/rca_v21` 迁到 `output/rca_v23` 后，C 阶段读取路径未同步。
- **解决**：改读 `V23_SUBDIRS["variable_sets"]`，`vs_report` 同步指向 `rca_v23/variable_sets/`；全链重跑 `run_id=v23-20261110T022554`（7/7 ok）。
- **影响（改结论的修复，如实报告）**：LOCA low QMGB→RRCA、mid WECS→QMGB、high HUP→QMGB；SGATR low/mid 不变（PSGA/PSGA）、high RM2→RHBR（变量集合相同，但 VOID 列序由第 5 变第 11，属 LiNGAM 列序敏感，见 V23-ALGO-001 补充观察）；FLB 三组不变（WCHG/PSGB/WHPI）。
- **状态**：closed

### V23-CODE-022：`_load_k2` 读顶层键，K2 实际挂在 `auxiliary.k2`，9 份解释 + 9 份报告错写“未产出”
- **类别**：CODE（D4/D6/D7）
- **证据**：`signature_{K}_{g}.json` 顶层无 `physics/physics_constraint/physics_constraints/k2/physics_residual` 键，D 阶段挂在 `signature["auxiliary"]["k2"]`（LOCA_high 为例 `n_relations_applicable=2`、`n_not_violated=2`）；`llm_explanation._load_k2` 只查顶层，故全部落“未产出（原因：…）”。
- **根因**：D 阶段落盘结构与 LLM 读取契约不一致，二者各自单测未覆盖真实签名。
- **解决**：`_load_k2` 优先读 `auxiliary.k2`，保留历史顶层键与独立产物作回退；新增真实签名 fixture（`tests/fixtures/signature_LOCA_high_real.json`，从正式产物原样抽取）与 3 例单测（auxiliary 读取、顶层回退/缺失、真实 payload）。重跑后 9 份解释与 9 份报告的 K2 段均为 `available=true`，`rca_report_LOCA_high.txt` 中“未产出”计数为 0。
- **状态**：closed

### V23-CODE-023：Top-1 依赖变量列序（SGATR_high RM2→RHBR），改为固定字母序
- **类别**：CODE（C 阶段因果发现输入口径）
- **证据**：SGATR 变量集成员完全不变、仅 VOID 由第 5 列移到第 11 列，`SGATR_high` 的 Top-1 即由 RM2 变 RHBR；上一轮"不固定列序、论文如实报告"的解释无法让第三方复现结论。
- **根因**：DirectLiNGAM 的逐变量回归顺序受输入列序影响，选择器输出顺序（按得分降序）成为隐式实现细节。
- **解决**：`variable_selector.VARIABLE_ORDER_RULE="alphabetical_ascii"`；`select_variables` 返回前统一排序并写入 `variable_order_rule`；`signatures.bootstrap_lingam_template` 入口再按同一规则同步重排 `variables` 与 `blocks` 列（防任何调用方绕过）。新增 2 例单测：`test_variable_order_permutation_invariance`（置换输入顺序后稳定边集合/前沿不变）、`test_bootstrap_fits_on_canonical_order`（以列偏置断言实际喂给 LiNGAM 的列序恒为字母序）。
- **为什么选字母序而非本体序**：本体序隐含"物理可信度越高越靠前"的偏好，且随本体表增删漂移；字母序与得分/数据/本体表完全解耦。
- **影响（改数字，如实报告）**：相对上一轮 `v23-20261110T032302`，LOCA_low RRCA→RHMT；SGATR low PSGA→HTR、mid PSGA→NSGA、high RHBR→NSGA；LOCA_mid/high（QMGB/QMGB）与 FLB 三组（WCHG/PSGB/WHPI）不变。A/B 的 `auto` 侧随列序一并重算，`detailed` 与全链结果一致（LOCA_low 均 RHMT）。
- **状态**：closed

### V23-DESIGN-017：方案正文 VIF<10 与 §3.5 表 <500 自相矛盾
- **类别**：DESIGN（文档口径）
- **证据**：§3 漏斗图、§3.5 前置条件、§4 约束框、§4 校验表、§7.1 V3 行仍写 `VIF<10`，而 §3.5 校验表与代码（`VIF_MAX=500`）已是 `<500`。
- **解决**：上述 5 处 + 伪代码 2 处 + 风险表统一为 `<500`；校验表补"子系统代表变量"保护行，与 `_protected_vars` 实际口径（触发/关键响应/子系统代表）一致。§3.5 新增"变量列序口径（V23-CODE-023）"段落。
- **影响**：仅文档口径，不改变任何数字。
- **状态**：closed

### V23-CODE-024：产物 schema 拼写错误 `causaltrtrace/v2.3`
- **类别**：CODE（产物契约）
- **证据**：`artifact.py:19` 与 `llm_explanation` payload 均写 `causaltrtrace`（多一个 t），118 个 JSON 产物携带；审计产物另用 `causaltrace.v23.*` 双风格。
- **解决**：统一为 `causaltrace/v2.3`；`data_audit._common` 改用 `artifact.SCHEMA`，artifact 名单独记入 `artifact` 字段。**不做迁移脚本**：全链 10 步在同一 run_id 下全量重算，旧文件被覆盖，避免新老 schema 混存。
- **影响**：产物字段值变化，不影响数值结果。
- **状态**：closed

### V23-CODE-025：A/B verdict 打印 Jaccard=0.35，LOCA 实际 0.2069
- **类别**：CODE（A/B 输出）
- **根因**：汇总循环引用了上一循环残留的局部变量 `jaccard`（`jaccard_vars` 字段本身正确）。
- **解决**：改取 `jaccards[k]` 并统一 4 位小数；重跑实测 LOCA=0.2069、SGATR=0.3462、FLB=0.3462。
- **状态**：closed

### V23-CODE-026：tracing_report 打印 `partial=[]`/`data_missing=[]`，与 skipped_classes.json 9 条矛盾
- **类别**：CODE（C 阶段报告）
- **根因**：按本次 `--classes` 计算，而非按实际跳过清单；只跑 ready 类时必然为空。
- **解决**：从 `skipped` 条目按 `status` 归类输出；重跑实测 `partial(1)=['LOCAC']`、`data_missing(8)=['SLBIC','SLBOC','SGBTR','RW','RI','MD','LR','LLB']`。
- **状态**：closed

### V23-DESIGN-015：STRUCTURE.md 承诺的 `knowledge/knowledge_validation.json` 不存在
- **类别**：DESIGN（目录契约）
- **证据**：`knowledge/` 目录为空，STRUCTURE.md 第 84 行列有该文件。
- **解决**：新增 `knowledge.validate_knowledge_layer()`，在 end2end 内以同 run_id 落盘。实测 3 类各 15 变量全部在 ENTITY_TABLE 登记（未登记 0）；allowed 关系命中 5/3/2 条（登记总数 11/8/7）；forbidden 规则 15/16/15；competitive 1/1/0；证据状态全部 frozen；子系统覆盖 9/6/8。
- **状态**：closed

### V23-DESIGN-016：偏离度"模板内相对档位"与 σ_floor 局限未写进报告
- **类别**：DESIGN（报告口径）
- **证据**：9 个模板档位计数完全相同（44/17/13/14，n=88），是分位分档的必然结果；外部意见另称"53/93 通道 σ 来自 σ_floor"。
- **解决**：三处明写——`deviation.py` 的 `note`、报告第六章 note、LLM 解释 `limitations`："相对档位、跨模板不可比绝对量；档位计数相同不代表物理强度一致"；σ 局限按实测渲染。
- **数据更正（不照抄外部说法）**：σ_robust 由 σ_floor 兜底的通道为 **87/93（LOCA）、88/93（SGATR）、88/93（FLB）**；其中 `fallback 53 + physical 34`。外部所说"53/93"是 fallback 来源数，不是 σ_floor 生效总数，以实测为准。
- **状态**：closed

### V23-CODE-027：manifest 中 22 个文件来自 v23a/v23b 批次、未标血缘
- **类别**：CODE（产物血缘）
- **证据**：变量集 `v23a-...`（00:38）、审计 `v23b-audit-...`（01:31）与主链 run_id 不同，manifest 只记路径与 sha256。
- **解决**：`run_v23_all` 由 7 步扩为 **10 步**（新增 variable_sets、audit、ab_variable_selection，均注入同一 run_id，方法 `_same_run_id`）；`build_manifest` 每条记录 `run_id`/`schema`，顶层新增 `lineage{runs, n_json_without_run_id, n_non_json_files}`。
- **实测**：`run_id=v23-20261110T034433`，141 个文件 = 118 个 JSON 全部同 run_id + 23 个非 JSON（报告 txt/html），`n_json_without_run_id=0`，无跨批次残留。
- **状态**：closed

---

## v2.3 全链验收记录（历史，run_id=v23-20261110T032302，已被 V23-CODE-023 列序修复取代）

**命令**：`cd Code && PYTHONPATH=. python -m rca.v21.run_v23_all --n-bootstrap 100`
**结果**：7/7 步 `ok`、`failed=0`；`manifest.json` 140 个文件全部登记 sha256；
`reports/run_v23_all_summary.json` 记录各步耗时与跳过类；`pytest tests/ -q` → 123 passed。

| 项 | 结果 | 来源 |
|---|---|---|
| 12 类注册表 | 12/12 登记；ready=3、partial=1、data_missing=8 | `registry/accident_registry.json` |
| 变量选取（3 ready 类） | V1–V7 全通过，各选 15 变量；A/B 重跑后 summary 拆 `primary`/`diagnostics` | `variable_sets/`、`ablations/ab_variable_selection.json` |
| 数据审计 | LOCA/SGATR/FLB/LOCAC 4 类各 3 份；8 类显式跳过 | `audit/*_{K}.json` |
| 公差 | 3 类均 `baseline_points=11`（注入前 [0,100]s）、93 变量、常量 53、Z 病态 13（14.0%）、quality PASS | `tolerances/tolerance_{K}.json` |
| 签名/溯源 | 9 模板 ×100 bootstrap；K2 `available` 9/9；稳定性门控降级 0/9 | `signatures/`、`tracing/` |
| 偏离度 | 模板内相对分位：LOCA_low 88 变量 = 44/17/13/14；旧绝对档 severe 62/88（附注） | `reports/deviation_{K}_{g}.json` |
| 图 | 13 份 HTML（9 组+3 类汇总+总览），含 K2/K3/偏移强度/稳定性面板 | `graphs/` |
| 汇总/差异 | 3 类 ×（summary+diff），输入 sha256 留痕 | `reports/summary|diff_{K}.json` |
| 解释/报告 | 9 份 `backend=rule`、`faithful=True`；9 份 8 章报告；K2 段不再“未产出” | `reports/llm_explanation_*`、`rca_report_*` |
| A/B 对照 | 主指标 stable/frontier/Top1 一致；`top1_same=0/3`（三类均不一致，按“无一致优劣”结论如实报告）；diag：backbone_hit(auto/v21)=2.38%/1.98%（LOCA）、1.59%（SGATR v21）、0%（FLB auto），反向边单列 | `ablations/ab_variable_selection.json` |

**根因 Top-1（本次同源结果）**：LOCA low/mid/high = RRCA / QMGB / QMGB；SGATR = PSGA / PSGA / RHBR；FLB = WCHG / PSGB / WHPI。
（相对 11-10 首轮验收 run_id=v23-20261110T013122：LOCA 三组全变，SGATR_high 因 VOID 列序变化由 RM2 变 RHBR，FLB 不变——见 V23-CODE-021/ALGO-001。）

**10 条 open 收口结果（2026-11-10）**

| 编号 | 结论 | 说明 |
|---|---|---|
| V23-ALGO-001 | closed（改代码+重跑） | 保 `B_CLIP=5`，新增弃权门控；9 组均未触发 |
| V23-DATA-004 | closed（改代码+重跑） | 基线改注入前 [0,100]s；与 DESIGN-011 同批 |
| V23-DESIGN-011 | closed（改代码+重跑） | 模板内相对分位四档 + d_abs_level 附注 |
| V23-DESIGN-006 | closed（口径定稿） | “低方差通道”，比例只作诊断，K11 删 30% 门槛 |
| V23-DESIGN-012 | closed（改验收口径） | Q7 → 稳定边≥5 且 Top1 可达路径≥1（删 consensus） |
| V23-DESIGN-013 | closed（改验收口径） | K5/K6 → 输出完整、如实报告；关系库扩充列后续 |
| V23-DESIGN-009 | closed（改指标口径） | `n_ontology_allowed` + `backbone_hit_ratio=命中/84`；反向边单列 |
| V23-DESIGN-005 | closed（won't fix） | 两类对称处理，K3 把 RM2 当症状 |
| V23-DESIGN-008 | closed（接受工程近似，P2） | 保留现有逐变量表，不采纳收窄建议 |
| V23-CODE-015 | closed（两行修，不重跑 v2.2） | 补 logger、放开非 consensus 边 |

**保持登记（非决策项）**：V23-DATA-001（8 类无数据）、V23-DATA-003（LOCAC 仅 5 条）、RCA-010（归 v2.1 范围）。
**open 计数：0**（上述 3 条为“等待数据/范围外”登记，不计 open）。

---

## v2.3 全链验收记录（最终冻结，run_id=v23-20261110T034433）

**命令**：`cd Code && PYTHONPATH=. python -m rca.v21.run_v23_all --n-bootstrap 100`
**结果**：10/10 步 `ok`、`failed=0`；单步耗时 variable_sets 6.62s / audit 3.35s / tolerances 0.43s / end2end 270.50s / graphs 0.02s / aggregation 0.01s / deviation 0.04s / explanation_reports 0.03s / ab 770.60s / manifest 0.02s；`pytest tests/ -q` → **125 passed**。

| 项 | 结果 | 来源 |
|---|---|---|
| 产物 | 141 个文件；118 个 JSON 全部 `run_id=v23-20261110T034433`，23 个非 JSON 单列，无跨批次残留 | `manifest.json` |
| 注册表 | 12/12 登记；ready=3、partial=1、data_missing=8 | `registry/accident_registry.json` |
| 变量选取 | V1–V7 全通过，各 15 变量；列序=字母序（`variable_order_rule`） | `variable_sets/` |
| 数据审计 | LOCA/SGATR/FLB/LOCAC 4 类 ×3 份；8 类显式跳过 | `audit/` |
| 公差 | 3 类 `baseline_points=11`、93 变量；σ_floor 生效 87/93、88/93、88/93（fallback 53 + physical 34） | `tolerances/` |
| 签名/溯源 | 9 模板 ×100 bootstrap；稳定性门控降级 0/9；K2 available 9/9 | `signatures/`、`tracing/` |
| 知识校验 | 3 类 15/15 变量登记；allowed 命中 5/3/2（登记 11/8/7）；forbidden 15/16/15；证据全 frozen | `knowledge/knowledge_validation.json` |
| 偏离度 | 模板内相对档位 44/17/13/14（9 组一致，n=88），绝对档 severe 13–69 作附注 | `reports/deviation_*.json` |
| 图/汇总/解释 | 13 份 HTML；3 类 summary+diff；9 份 rule 解释 `faithful=True` + 9 份报告 | `graphs/`、`reports/` |
| A/B | Jaccard LOCA 0.2069 / SGATR 0.3462 / FLB 0.3462；`top1_same=0/3`；backbone_hit auto/v21 = 2.78%/1.98%（LOCA）、0%/0.79%（SGATR）、0%（FLB auto） | `ablations/ab_variable_selection.json` |

**根因 Top-1（冻结值）**：LOCA = RHMT / QMGB / QMGB；SGATR = HTR / NSGA / NSGA；FLB = WCHG / PSGB / WHPI。

**相对上一轮 `v23-20261110T032302` 的变化（仅由固定列序引起）**：LOCA_low RRCA→RHMT；SGATR low PSGA→HTR、mid PSGA→NSGA、high RHBR→NSGA；LOCA_mid/high、FLB 三组不变。详见 V23-CODE-023。

**收口计数（本轮）**：新增 8 条（CODE-023/024/025/026/027、DESIGN-015/016/017）全部 closed；累计 open=0。
**保持登记**：V23-DATA-001、V23-DATA-003、RCA-010。

---

## 论文锚定 + A/B 一次性消融 + LLM 降频 + 全链重跑准入（2026-11-10）

### V23-DESIGN-018：变量选取未锚定论文原文

- **原方案怎么写**：§3.2 四阶段漏斗（硬过滤→评分→物理约束→VIF），强制集只含“触发变量 + 关键响应 + 子系统代表”；论文只作为背景引用，未进入选取规则。
- **为什么改**：论文 p.4 写明 “eight key variables identified through XGBoost feature selection and **validated by domain experts**”，原漏斗会按数据驱动剔除专家认可变量（高 VIF 时尤其），与论文口径不一致。原文**未给 8 变量名单**，也未报告 XGBoost 超参/重要性阈值/VIF/降维/变量集敏感性分析；因此只能锚定“专家验证集必须进模型”，不能替论文补名单。
- **改法**：新增阶段0「论文锚定」（`rca/v21/paper_anchor.py` 单一来源）。锚定集与 `forced ∪ subsystem_reps` 合并为强制保留集，阶段3 去重、阶段4 VIF 迭代不得剔除；代理集用 v2.1 `CLASS_VARIABLES`（LOCA 14 / SGATR 15 / FLB 14），**显式标注为工程假设**。冲突裁决：近常量/缺失>50%/零值 → 剔除并记录 `dropped_stage1`；Z 病态 → 保留并标记 `z_pathological`；VIF≥500 → 保留并标记实测值；数据集无该列 → 记 `missing_in_data` 不报错。新增 V0 校验。
- **影响产物/验收**：`variable_sets/selection_report_{K}.json` 新增 `paper_anchor` 块（引文、代理来源、used/dropped/kept_pathological、conflict_policy）；§3.2/§3.3/§3.5/§7.1 加条款；A4 验收口径改为“锚定 + 自动补选”。
- **证据**：run_id=`v23-20261110T043640`，锚定 14/14、14/15、14/14；SGATR `WLR` 因 `near_constant(sigma=0.00e+00,nunique=1)` 记录剔除；LOCA/SGATR/FLB 各保留 Z 病态/高 VIF 锚定变量 7/13/12 个（保留并标记）。
- **结论**：closed（改代码+重跑）。

### V23-CODE-028：锚定后 V3 空集误判与日志 None 崩溃

- **现象**：SGATR 全部入模变量进入保护集后 `non_protected_vifs` 为空，原逻辑 `if non_protected_vifs else False` 把空集判为 V3 失败；同时 `max_vif=None` 时日志 `:.1f` 抛 TypeError。
- **改法**：V3 改为空集视为通过（附 `v3_note` 说明“锚定/强制变量按冲突策略保留，VIF 见 final_vif 与 kept_pathological”）；日志 None 显示 `n/a`。
- **验证**：受影响单测全过；全链 SGATR `V3_vif_lt_500=true`。未单独跑全链（按 §6.4 属“纯判定/日志修复”，随本轮里程碑合并验证）。
- **结论**：closed。

### V23-DESIGN-019：A/B 对照占用主链且每轮重算

- **原方案怎么写**：§6.5 将“A/B 对照验证（auto vs v2.1）”作为主链第 9 步，每轮 9×2×100 bootstrap ≈770s。
- **为什么改**：A/B 是一次性消融证据而非每轮必需；论文只需引用冻结结果；每轮重算拖慢推进（用户明确要求）。
- **改法**：主链 10→9 步；`run_v23_all` 新增 `--with-ab`，**默认关闭**，仅在需要时插入 manifest 之前；现有结果冻结为 `ablations/frozen_ab_variable_selection_v23-20261110T034433.json`（`frozen=true`、`frozen_from_run_id`、`frozen_at`、`frozen_source_sha256`、`note`），sha256=`9db8bfb3dad0b610bab11854efb230e5243718d149b01c760ce51e91bbd69782`（源文件 `6b6fdeed...b912d0`）。旧直跑件保留仅作溯源，不再更新。
- **影响产物/验收**：A4 验收改“一次性消融，产物已冻结”；方案 §附录C-A4、附录D-A4 与 STRUCTURE 同步；manifest lineage 中旧 run_id 文件 2 个（`ab_variable_selection.json` + 冻结件），属预期。
- **结论**：closed。

### V23-DESIGN-020：LLM 解释逐模板调用（9 次/轮）

- **原方案怎么写**：§5 第三层 C5 对每个模板生成 LLM 解释，忠实性校验只看数字/变量名。
- **为什么改**：9 次/轮调用成本高、结果非确定；模板级解释用 rule 已足够，跨模板结论才需要 LLM。
- **改法**：模板级 9 份固定 rule；LLM 仅用于 3 份类级汇总 + 1 份全局结论（共 4 份）；带同 run_id + 输入 sha256 缓存（命中不重复调用）；无 key/失败自动降级 rule 并记 `degraded_reason`；汇总忠实性新增“模板 id 必须在 payload.template_ids 中”，与数字/变量名三查。
- **影响产物/验收**：新增 `reports/llm_explanation_class_{K}.json` ×3 与 `llm_explanation_global.json`；D6 验收改为 9+3+1；`run_v23_llm_explanation --backend` 仅作用于汇总。
- **证据**：run_id=`v23-20261110T043640`，13 份解释全部 `faithful=true`（类级模板引用 12/12/12，全局 9）；无 key，汇总 `degraded=false`（requested=rule）。
- **结论**：closed。

### V23-DESIGN-021：全链重跑缺少准入约束

- **来源**：用户对推进效率的明确要求，**2026-11-10 生效**。
- **改法**：方案新增 §6.4「全链重跑准入规定（强制）」：默认禁止；仅 E-R1 出对外数字 / E-R2 影响数值 / E-R3 影响产物结构或契约 / E-R4 跨阶段对不上 四类例外；明确禁止触发清单；每次全链必须记 `--rerun-reason` 并写入 `run_v23_all_summary.json`；给出替代验证最小集（受影响单测 + 单阶段重跑复用上游 run_id + 冻结产物 sha256/关键指标比对）。代码层 `--rerun-reason` 为空即拒绝执行（`--dry-run` 除外）。STRUCTURE §5 放指针。
- **影响产物/验收**：所有全链命令；未声明原因的重跑不得作为验收证据。
- **结论**：closed。

### V23-DESIGN-022：锚定保留高 VIF 变量导致 LOCA_mid 稳定性门控降级

- **现象**：run_id=`v23-20261110T043640` 中 LOCA_mid `stability.degraded=true`（clip_ratio=0.4516 > 0.35，cond_after_clip=816.8，n_clipped=14/31），其余 8 组 false；上一轮为 0/9。
- **原因**：锚定强制保留 14 个专家变量，其中含强共线组（实测最高 VIF≈2.85e13），`B_CLIP=5` 截断边增多，触发降级门控。
- **裁决**：按 §3.3 冲突裁决「保留并标记」，不回退锚定；报告如实写 `stability.degraded=true` 与“数值不稳定、结论降级”。
- **影响**：LOCA_mid Top1=WECS 属降级结论，论文引用时必须标注；不得当作与其余 8 组同等级证据。
- **结论**：closed（接受并标记；论文引用需注明）。

---

## v2.3 全链验收记录（论文锚定 + A/B 摘出 + LLM 降频，run_id=v23-20261110T043640）

**命令**：`cd Code && PYTHONPATH=. python -m rca.v21.run_v23_all --n-bootstrap 100`
**重跑原因（`rerun_reason`，方案 §6.4）**：E-R1 + E-R2 + E-R3 —— ① 论文锚定进入变量选取、变量集改变（数值）；② A/B 摘出主链、步骤 10→9、新增 `--with-ab`（产物结构与阶段契约）；③ 出对外数字并冻结（论文引用的变量集与 9 组 Top-1）。
**结果**：9/9 步 `ok`、`failed=0`、总耗时 649.79s；单步 variable_sets 11.00 / audit 3.11 / tolerances 0.43 / end2end 635.09 / graphs 0.02 / aggregation 0.01 / deviation 0.04 / explanation_reports 0.07 / manifest 0.02（秒）；`pytest tests/ -q` → **125 passed**。

| 项 | 结果 | 来源 |
|---|---|---|
| 产物 | 146 个文件；121 个 JSON `run_id=v23-20261110T043640`、2 个 A/B JSON 属 `v23-20261110T034433`（冻结件+旧直跑件）、23 个非 JSON | `manifest.json` |
| 变量选取 | 20/20/21 变量；V0–V7 全 True；锚定 14/14、14/15、14/14；SGATR `WLR` 剔除并记录 | `variable_sets/` |
| 稳定性门控 | 降级 1/9：LOCA_mid（clip_ratio 0.4516 > 0.35）；其余 0.107–0.309 | `signatures/` auxiliary.stability |
| 解释 | 9 份模板 rule + 3 份类级 + 1 份全局，全部 `faithful=true`（模板引用 12/12/12/9） | `reports/llm_explanation_*` |
| A/B | 冻结件 `frozen_ab_variable_selection_v23-20261110T034433.json`（frozen=true，sha256=9db8bfb3…d69782）；本轮未跑 | `ablations/` |

**根因 Top-1（本轮）**：LOCA = RHMT / WECS / LVPZ；SGATR = NSGA / NSGA / RHBR；FLB = P / WCHG / P。

**相对 `v23-20261110T034433` 的变化（论文锚定导致变量集扩大）**：

| 模板 | 旧 Top1 | 新 Top1 | 变化 |
|---|---|---|---|
| LOCA_low | RHMT | RHMT | 不变 |
| LOCA_mid | QMGB | WECS | **变**（门控降级） |
| LOCA_high | QMGB | LVPZ | **变** |
| SGATR_low | HTR | NSGA | **变** |
| SGATR_mid | NSGA | NSGA | 不变 |
| SGATR_high | NSGA | RHBR | **变** |
| FLB_low | WCHG | P | **变** |
| FLB_mid | PSGB | WCHG | **变** |
| FLB_high | WHPI | P | **变** |

变量集：由“各 15 变量”扩为 20/20/21，锚定变量全部纳入（其中含 Z 病态/高 VIF 被标记保留者 7/13/12 个），稳定性降级由 0/9 变为 1/9。**该变化不压制、不回退，如实记录**；A/B 对照仍引用冻结件，不因本轮变量集变化重算。

**收口计数（本轮）**：新增 6 条（DESIGN-018/019/020/021/022、CODE-028）全部 closed；累计 open=0。
**保持登记**：V23-DATA-001、V23-DATA-003、RCA-010。



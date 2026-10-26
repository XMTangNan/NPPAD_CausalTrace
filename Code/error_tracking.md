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
*（后续问题在此追加）*

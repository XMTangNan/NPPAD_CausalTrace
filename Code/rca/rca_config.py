"""跨事故因果签名 RCA 配置：事故类型、变量并集、逐类目标/marker、路径。"""
from config import REPO_DATA, RCA_RESULT_DIR, RCA_SIGNATURE_DIR
from pathlib import Path

# 参与 RCA 的事故类型（LOCAC 按方案排除）
ACCIDENT_TYPES = ["LOCA", "SGATR", "FLB"]

# 数据目录
ACCIDENT_DIRS = {t: REPO_DATA / t for t in ACCIDENT_TYPES}

# 22+ 变量并集（LOCA 14 + SGATR/FLB 特有）
VARIABLE_UNION_22 = [
    # LOCA 14
    "P", "WLR", "VOL", "LVPZ", "TAVG", "THA", "WRCA", "QMWT",
    "PWNT", "RH", "PSGA", "DNBR", "WHPI", "WECS",
    # SGATR 增量
    "PSGB", "TSAT", "TF", "TCA", "WTRA",
    # FLB 增量
    "VOID", "WFLB", "QRHR", "TPCT",
]

# 逐类变量子集（基于事故机理筛选，含各事故的故障指示器 marker）
CLASS_VARIABLES = {
    "LOCA": ["P","WLR","VOL","LVPZ","TAVG","THA","WRCA","QMWT","PWNT","RH","PSGA","DNBR","WHPI","WECS"],
    "SGATR": ["P","WLR","WTRA","VOL","LVPZ","TAVG","THA","WRCA","QMWT","PWNT","RH","PSGA","PSGB","TSAT","TF"],
    "FLB": ["P","VOL","LVPZ","TAVG","THA","WRCA","QMWT","PWNT","RH","PSGA","PSGB","DNBR","VOID","WFLB"],
}

# 逐类目标变量（事故直接后果变量）
CLASS_TARGETS = {
    "LOCA": "P",      # 一回路压力
    "SGATR": "P",     # 一回路压力（二回路泄漏影响）
    "FLB": "PSGB",    # 蒸汽发生器B压力（主蒸汽管破裂直接后果）
}

# 逐类 marker 规则（正 marker：应激活；负 marker：不应激活）
# 按方案定义：WLR(LOCA) / WTRA(SGATR) / WFLB(FLB) 为各事故故障指示器
CLASS_MARKERS = {
    "LOCA": {"positive": ["WLR"], "negative": []},
    "SGATR": {"positive": ["WTRA"], "negative": ["WLR", "WFLB"]},
    "FLB": {"positive": ["WFLB"], "negative": ["WLR"]},
}

# 严重度分组
SEVERITY_LO = (1, 33)
SEVERITY_MID = (34, 66)
SEVERITY_HI = (67, 100)

# 分层划分：sev-1 固定训练集
TRAIN_SEVERITY_RANGE = (1, 80)   # 1~80 训练
VAL_SEVERITY_RANGE = (81, 100)   # 81~100 验证/测试

# 共享基线：四类 sev-1 池化（本项目三类事故各取 sev-1）
SHARED_BASELINE_SEVERITIES = [1]  # 每类事故取 severity=1 作为准Normal基准

# RCA 输出路径
SIGNATURE_DIR = RCA_SIGNATURE_DIR
RESULT_DIR = RCA_RESULT_DIR

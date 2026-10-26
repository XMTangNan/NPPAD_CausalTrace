"""LOCA 事故特有配置：目标变量、变量集、冻结关系、本体映射、阈值基准。"""
from config import REPO_DATA

LOCA_DIR = REPO_DATA / "LOCA"
TARGET = "P"
PRIOR_VARIABLES = ["P","WLR","VOL","LVPZ","TAVG","THA","WRCA","QMWT","PWNT","RH","PSGA","DNBR","WHPI","WECS"]
LOCA1_STEADY_START = 520.0
LOCA1_STEADY_END = 2030.0
FAULT_SYSTEM_VARS = ["WLR", "WECS", "WHPI"]

FROZEN_RELATIONS = [
    {"from":"WLR","to":"P","category":"core","direction":"bidirectional","basis":"破口动态耦合：P驱动WLR喷放，WLR降低P","evidence":"明确依据"},
    {"from":"P","to":"TAVG","category":"aux","direction":"forward","basis":"压力变化影响饱和温度","evidence":"合理假设"},
    {"from":"VOL","to":"P","category":"aux","direction":"bidirectional","basis":"装量与压力热力学关系","evidence":"合理假设"},
    {"from":"WLR","to":"VOL","category":"aux","direction":"forward","basis":"泄漏减少一回路装量","evidence":"合理假设"},
    {"from":"VOL","to":"LVPZ","category":"aux","direction":"forward","basis":"装量变化反映在稳压器水位","evidence":"合理假设"},
    {"from":"THA","to":"PSGA","category":"allowed","direction":"forward","basis":"一回路热段温度传给蒸发器A压力","evidence":"明确依据"},
    {"from":"RH","to":"PWNT","category":"aux","direction":"forward","basis":"反应性驱动核功率","evidence":"明确依据"},
    {"from":"RH","to":"QMWT","category":"aux","direction":"forward","basis":"反应性影响热功率","evidence":"合理假设"},
    {"from":"P","to":"WRCA","category":"aux","direction":"forward","basis":"压力影响一回路流量","evidence":"合理假设"},
    {"from":"TAVG","to":"DNBR","category":"aux","direction":"forward","basis":"温度升高降低DNBR","evidence":"合理假设"},
]
FORBIDDEN_EDGES = [
    ("RM1","TAVG","辐射监测不驱动一回路温度"),
    ("RM1","P","辐射监测不驱动一回路压力"),
    ("RM2","P","蒸汽管线辐射不驱动压力"),
    ("DTHY","P","甲状腺剂量率不驱动压力"),
    ("DTHY","TAVG","剂量率不驱动温度"),
    ("DWB","P","全身剂量率不驱动压力"),
]
CORE_RELATIONS = [
    {"from":"WLR","to":"P","label":"WLR↔P","type":"bidirectional","basis":"破口动态耦合：P驱动WLR喷放，WLR降低P"},
    {"from":"P","to":"TAVG","label":"P→TAVG","type":"forward","basis":"压力变化影响饱和温度"},
    {"from":"VOL","to":"P","label":"VOL↔P","type":"bidirectional","basis":"装量与压力热力学关系"},
    {"from":"THA","to":"PSGA","label":"THA→PSGA","type":"forward","basis":"一回路热段温度传给蒸发器A压力"},
    {"from":"RH","to":"PWNT","label":"RH→PWNT","type":"forward","basis":"反应性驱动核功率"},
]

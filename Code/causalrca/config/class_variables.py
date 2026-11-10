"""按事故类别的可能变量集合（CLASS_VARIABLES）。

2026-11-10 从 v2.1 `rca/rca_config.py` 原样复制进 causalrca，供 `causalrca.features.paper_anchor` 使用；v2.1 归档后本文件即为其唯一来源。
**只读常量，禁止就地修改**（改动会改变锚定口径）。
"""

# 逐类变量子集（基于事故机理筛选，含各事故的故障指示器 marker）
CLASS_VARIABLES = {
    "LOCA": ["P","WLR","VOL","LVPZ","TAVG","THA","WRCA","QMWT","PWNT","RH","PSGA","DNBR","WHPI","WECS"],
    "SGATR": ["P","WLR","WTRA","VOL","LVPZ","TAVG","THA","WRCA","QMWT","PWNT","RH","PSGA","PSGB","TSAT","TF"],
    "FLB": ["P","VOL","LVPZ","TAVG","THA","WRCA","QMWT","PWNT","RH","PSGA","PSGB","DNBR","VOID","WFLB"],
}

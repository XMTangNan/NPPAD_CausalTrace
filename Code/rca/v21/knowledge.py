"""R0.5 神经符号知识层：三类统一代码本体 + 过程本体（数据驱动公差）。

对应方案 13.4：
1. 知识图谱层（符号）：§4.2 全部冻结关系入码——forbidden / allowed / competitive；
2. 过程本体层（符号×神经）：sev-1 恢复段池化 μ/σ，σ≈0 跳过零兜底；
3. 提示模板层在 R5（D4）实现，本文件不含。

本体纪律（沿用方案一）：forbidden 学图后检出即删；allowed 只标记不添加；
competitive 双向保留、不删不偏向。
"""
from __future__ import annotations

import numpy as np
from typing import Dict, List, Tuple

from rca.rca_config_v21 import (
    MARKERS, MARKER_COLS, SAFETY_STEP_VARS, PHYSICAL_CANDIDATES,
    CONSTANT_SIGMA,
)
from core.utils import logger

# ==================== 实体表：变量 → 子系统 / 物理含义 / 单位 ====================
# 覆盖 NPPAD README Table 2 全部 96 个事故参数变量（排除 TIME）
ENTITY_TABLE: Dict[str, Dict[str, str]] = {
    # —— 一回路热工 ——
    "P":    {"subsystem": "一回路热工", "meaning": "一回路压力", "unit": "bar"},
    "VOL":  {"subsystem": "一回路热工", "meaning": "一回路冷却剂装量", "unit": "m3"},
    "LVPZ": {"subsystem": "一回路热工", "meaning": "稳压器水位", "unit": "%"},
    "VOID": {"subsystem": "一回路热工", "meaning": "一回路空泡份额", "unit": "%"},
    "TAVG": {"subsystem": "一回路热工", "meaning": "一回路平均温度", "unit": "degC"},
    "THA":  {"subsystem": "一回路热工", "meaning": "热段A温度", "unit": "degC"},
    "THB":  {"subsystem": "一回路热工", "meaning": "热段B温度", "unit": "degC"},
    "TCA":  {"subsystem": "一回路热工", "meaning": "冷段A温度", "unit": "degC"},
    "TCB":  {"subsystem": "一回路热工", "meaning": "冷段B温度", "unit": "degC"},
    "TSAT": {"subsystem": "一回路热工", "meaning": "稳压器饱和温度", "unit": "degC"},
    "SCMA": {"subsystem": "一回路热工", "meaning": "A环路过冷裕度", "unit": "degC"},
    "SCMB": {"subsystem": "一回路热工", "meaning": "B环路过冷裕度", "unit": "degC"},
    "HUP":  {"subsystem": "一回路热工", "meaning": "稳压器PORV排汽焓", "unit": "kJ/kg"},
    "HLW":  {"subsystem": "一回路热工", "meaning": "一回路泄漏焓", "unit": "kJ/kg"},
    "WUP":  {"subsystem": "一回路热工", "meaning": "稳压器PORV/安全阀排放流量", "unit": "t/h"},
    "WSPY": {"subsystem": "一回路热工", "meaning": "稳压器喷淋流量", "unit": "t/h"},
    "HTR":  {"subsystem": "一回路热工", "meaning": "稳压器电加热功率", "unit": "KW"},
    "WLD":  {"subsystem": "一回路热工", "meaning": "下泄流量", "unit": "t/h"},
    "WCHG": {"subsystem": "一回路热工", "meaning": "上充流量", "unit": "t/h"},
    "WCFT": {"subsystem": "一回路热工", "meaning": "安注箱流量", "unit": "t/h"},
    "WLPI": {"subsystem": "一回路热工", "meaning": "低压安注(RHR)流量", "unit": "t/h"},
    "PPM":  {"subsystem": "一回路热工", "meaning": "一回路硼浓度", "unit": "ppm"},
    # —— 一回路流量 ——
    "WRCA": {"subsystem": "一回路流量", "meaning": "一回路流量A（主泵）", "unit": "t/h"},
    "WRCB": {"subsystem": "一回路流量", "meaning": "一回路流量B（主泵）", "unit": "t/h"},
    "RRCA": {"subsystem": "一回路流量", "meaning": "A环路流量比", "unit": "-"},
    "RRCB": {"subsystem": "一回路流量", "meaning": "B环路流量比", "unit": "-"},
    "RRCO": {"subsystem": "一回路流量", "meaning": "堆芯流量比", "unit": "-"},
    # —— 反应性与功率 ——
    "QMWT": {"subsystem": "反应性与功率", "meaning": "堆芯热功率", "unit": "MW"},
    "PWNT": {"subsystem": "反应性与功率", "meaning": "核功率", "unit": "%"},
    "PWR":  {"subsystem": "反应性与功率", "meaning": "堆芯热功率份额", "unit": "%"},
    "RH":   {"subsystem": "反应性与功率", "meaning": "总反应性", "unit": "pcm"},
    "RHBR": {"subsystem": "反应性与功率", "meaning": "可溶性硼反应性", "unit": "dk/k"},
    "RHMT": {"subsystem": "反应性与功率", "meaning": "慢化剂温度反应性", "unit": "dk/k"},
    "RHFL": {"subsystem": "反应性与功率", "meaning": "燃料多普勒反应性", "unit": "dk/k"},
    "RHRD": {"subsystem": "反应性与功率", "meaning": "控制棒反应性", "unit": "dk/k"},
    "TFSB": {"subsystem": "反应性与功率", "meaning": "淹没燃料平均温度", "unit": "degC"},
    "TFPK": {"subsystem": "反应性与功率", "meaning": "峰值燃料温度", "unit": "degC"},
    "TF":   {"subsystem": "反应性与功率", "meaning": "平均燃料温度", "unit": "degC"},
    "TPCT": {"subsystem": "反应性与功率", "meaning": "峰值包壳温度", "unit": "degC"},
    # —— 二回路 SG ——
    "PSGA": {"subsystem": "二回路SG", "meaning": "蒸汽发生器A压力", "unit": "bar"},
    "PSGB": {"subsystem": "二回路SG", "meaning": "蒸汽发生器B压力", "unit": "bar"},
    "LSGA": {"subsystem": "二回路SG", "meaning": "SG-A宽量程水位", "unit": "m"},
    "LSGB": {"subsystem": "二回路SG", "meaning": "SG-B宽量程水位", "unit": "m"},
    "QMGA": {"subsystem": "二回路SG", "meaning": "SG-A排热功率", "unit": "MW"},
    "QMGB": {"subsystem": "二回路SG", "meaning": "SG-B排热功率", "unit": "MW"},
    "NSGA": {"subsystem": "二回路SG", "meaning": "SG-A窄量程水位", "unit": "%"},
    "NSGB": {"subsystem": "二回路SG", "meaning": "SG-B窄量程水位", "unit": "%"},
    "WRLA": {"subsystem": "二回路SG", "meaning": "SG-A MSV/ADV流量", "unit": "t/h"},
    "WRLB": {"subsystem": "二回路SG", "meaning": "SG-B MSV/ADV流量", "unit": "t/h"},
    # —— 二回路给水 ——
    "WFWA": {"subsystem": "二回路给水", "meaning": "SG-A给水流量", "unit": "t/h"},
    "WFWB": {"subsystem": "二回路给水", "meaning": "SG-B给水流量", "unit": "t/h"},
    # —— 二回路蒸汽 ——
    "WSTA": {"subsystem": "二回路蒸汽", "meaning": "SG-A蒸汽流量", "unit": "t/h"},
    "WSTB": {"subsystem": "二回路蒸汽", "meaning": "SG-B蒸汽流量", "unit": "t/h"},
    "TBLD": {"subsystem": "二回路蒸汽", "meaning": "汽机负荷", "unit": "%"},
    # —— 安全系统 ——
    "WHPI": {"subsystem": "安全系统", "meaning": "高压安注流量", "unit": "t/h"},
    "WECS": {"subsystem": "安全系统", "meaning": "安全注入总流量", "unit": "t/h"},
    "WCSP": {"subsystem": "安全系统", "meaning": "安全壳喷淋流量", "unit": "t/h"},
    "QRHR": {"subsystem": "安全系统", "meaning": "RHR排热功率", "unit": "MW"},
    "QFCL": {"subsystem": "安全系统", "meaning": "风机冷却器排热功率", "unit": "MW"},
    # —— 安全指标 ——
    "DNBR": {"subsystem": "安全指标",   "meaning": "偏离泡核沸腾比", "unit": "-"},
    "FRCL": {"subsystem": "安全指标",   "meaning": "包壳破损份额", "unit": "%"},
    "FRZR": {"subsystem": "安全指标",   "meaning": "锆氧化份额", "unit": "%"},
    "LVCR": {"subsystem": "安全指标",   "meaning": "堆芯水位", "unit": "m"},
    # —— 安全壳 ——
    "PRB":  {"subsystem": "安全壳", "meaning": "安全壳压力", "unit": "bar"},
    "PRBA": {"subsystem": "安全壳", "meaning": "安全壳局部空气压力", "unit": "bar"},
    "TRB":  {"subsystem": "安全壳", "meaning": "安全壳温度", "unit": "degC"},
    "LWRB": {"subsystem": "安全壳", "meaning": "安全壳地坑水位", "unit": "m"},
    "WBK":  {"subsystem": "安全壳", "meaning": "进入安全壳的破口总流量", "unit": "t/h"},
    "MH2":  {"subsystem": "安全壳", "meaning": "锆水反应产氢量", "unit": "kg"},
    "CNH2": {"subsystem": "安全壳", "meaning": "安全壳氢浓度", "unit": "%"},
    "RBLK": {"subsystem": "安全壳", "meaning": "安全壳总泄漏质量", "unit": "kg"},
    "SGLK": {"subsystem": "安全壳", "meaning": "SG总泄漏质量", "unit": "kg"},
    "MBK":  {"subsystem": "安全壳", "meaning": "破口积分流量", "unit": "kg"},
    "EBK":  {"subsystem": "安全壳", "meaning": "破口积分能量", "unit": "MJ"},
    "TKLV": {"subsystem": "安全壳", "meaning": "换料水箱水位", "unit": "m3"},
    "MDBR": {"subsystem": "安全壳", "meaning": "堆腔熔融物质量", "unit": "kg"},
    "MCRT": {"subsystem": "安全壳", "meaning": "熔融混凝土质量", "unit": "kg"},
    "MGAS": {"subsystem": "安全壳", "meaning": "CCI气体质量", "unit": "kg"},
    "TDBR": {"subsystem": "安全壳", "meaning": "堆腔碎片温度", "unit": "degC"},
    "TSLP": {"subsystem": "安全壳", "meaning": "下腔室碎片温度", "unit": "degC"},
    "TCRT": {"subsystem": "安全壳", "meaning": "熔融混凝土温度", "unit": "degC"},
    # —— 辐射监测 ——
    "RM1":  {"subsystem": "辐射监测", "meaning": "安全壳空气辐射监测", "unit": "CPM"},
    "RM2":  {"subsystem": "辐射监测", "meaning": "蒸汽管线辐射监测", "unit": "CPM"},
    "RM3":  {"subsystem": "辐射监测", "meaning": "凝汽器排气辐射监测", "unit": "CPM"},
    "RM4":  {"subsystem": "辐射监测", "meaning": "辅助厂房空气辐射监测", "unit": "CPM"},
    "RC87": {"subsystem": "辐射监测", "meaning": "一回路冷却剂放射性", "unit": "CPM"},
    "RC131":{"subsystem": "辐射监测", "meaning": "一回路I-131当量浓度", "unit": "GBq/cc"},
    "STRB": {"subsystem": "辐射监测", "meaning": "安全壳辐射释放率", "unit": "GBq/s"},
    "STSG": {"subsystem": "辐射监测", "meaning": "SG阀门辐射释放率", "unit": "GBq/s"},
    "STTB": {"subsystem": "辐射监测", "meaning": "凝汽器排气辐射释放率", "unit": "GBq/s"},
    "DTHY": {"subsystem": "辐射监测", "meaning": "EAB甲状腺剂量率", "unit": "mSv/h"},
    "DWB":  {"subsystem": "辐射监测", "meaning": "EAB全身剂量率", "unit": "mSv/h"},
    # —— 故障指示器（marker，非根因）——
    "WLR":  {"subsystem": "故障指示器", "meaning": "一回路破口泄漏流量（LOCA指示器）", "unit": "t/h"},
    "WTRA": {"subsystem": "故障指示器", "meaning": "SG-A传热管破裂流量（SGATR指示器）", "unit": "t/h"},
    "WTRB": {"subsystem": "故障指示器", "meaning": "SG-B传热管破裂流量（SGBTR指示器）", "unit": "t/h"},
    "WFLB": {"subsystem": "故障指示器", "meaning": "给水/蒸汽管道破裂流量（FLB指示器）", "unit": "t/h"},
}

# ==================== §4.2 冻结关系（allowed，L1/L2 有向；evidence_level 待检验标注）====================
# direction=forward 仅允许 from→to；bidirectional 两个方向都只标记
# 注：allowed 只在数据学出时标记“本体支持”，数据不支持不添加。
ALLOWED_RELATIONS: Dict[str, List[Dict]] = {
    "LOCA": [
        {"from": "WLR", "to": "VOL",  "direction": "forward",       "level": "L1", "basis": "破口泄漏减少一回路装量"},
        {"from": "VOL", "to": "LVPZ", "direction": "forward",       "level": "L1", "basis": "装量下降反映为稳压器水位下降"},
        {"from": "P",   "to": "TAVG", "direction": "forward",       "level": "L2", "basis": "失压导致饱和温度下降"},
        {"from": "P",   "to": "WHPI", "direction": "forward",       "level": "L1", "basis": "低压力触发高压安注（控制逻辑）"},
        {"from": "WHPI","to": "WECS", "direction": "forward",       "level": "L1", "basis": "高压安注汇入安全注入总流量"},
        {"from": "THA", "to": "PSGA", "direction": "forward",       "level": "L2", "basis": "一回路热段温度经U型管传给二回路压力"},
        {"from": "RH",  "to": "PWNT", "direction": "forward",       "level": "L1", "basis": "反应性驱动核功率"},
        {"from": "RH",  "to": "QMWT", "direction": "forward",       "level": "L2", "basis": "反应性影响堆芯热功率"},
        {"from": "VOL", "to": "P",    "direction": "bidirectional", "level": "L2", "basis": "装量与压力的热力学耦合"},
        {"from": "P",   "to": "WRCA", "direction": "forward",       "level": "L2", "basis": "压力影响一回路流量"},
        {"from": "TAVG","to": "DNBR", "direction": "forward",       "level": "L2", "basis": "温度升高降低DNBR"},
    ],
    "SGATR": [
        {"from": "WTRA", "to": "LSGA", "direction": "forward", "level": "L1", "basis": "管侧流体漏入壳侧，SG-A水位上升"},
        {"from": "WTRA", "to": "PSGA", "direction": "forward", "level": "L2", "basis": "二次侧压力微升"},
        {"from": "WTRA", "to": "VOL",  "direction": "forward", "level": "L2", "basis": "传热管破裂造成一回路缓失装量"},
        {"from": "VOL",  "to": "P",    "direction": "forward", "level": "L2", "basis": "装量损失导致压力下降"},
        {"from": "P",    "to": "WHPI", "direction": "forward", "level": "L1", "basis": "仅高严重度：低压力触发安注"},
        {"from": "WHPI", "to": "WECS", "direction": "forward", "level": "L1", "basis": "高压安注汇入安全注入总流量"},
        {"from": "RH",   "to": "PWNT", "direction": "forward", "level": "L1", "basis": "反应性驱动核功率"},
        {"from": "THA",  "to": "PSGA", "direction": "forward", "level": "L2", "basis": "一回路热段温度传给SG-A压力"},
    ],
    "FLB": [
        {"from": "WFLB", "to": "WFWA", "direction": "forward", "level": "L1", "basis": "破口分流，进入SG的给水流量下降"},
        {"from": "WFLB", "to": "WSTA", "direction": "forward", "level": "L2", "basis": "高严重度蒸汽量塌陷（A侧）"},
        {"from": "WFLB", "to": "WSTB", "direction": "forward", "level": "L2", "basis": "高严重度蒸汽量塌陷（B侧）"},
        {"from": "PSGA", "to": "P",    "direction": "forward", "level": "L2", "basis": "二回路失冷最终经传热扰动一回路压力"},
        {"from": "P",    "to": "WHPI", "direction": "forward", "level": "L2", "basis": "高严重度一回路失压触发安注"},
        {"from": "WHPI", "to": "WECS", "direction": "forward", "level": "L1", "basis": "高压安注汇入安全注入总流量"},
        {"from": "RH",   "to": "PWNT", "direction": "forward", "level": "L1", "basis": "反应性驱动核功率"},
    ],
}

# 竞争边：物理反馈环或 L3 方向不预设，双向保留不删不偏向（13.4）
COMPETITIVE_PAIRS: List[Tuple[str, str]] = [
    ("WLR", "P"),       # L1 环存在/L2 方向：P 驱动喷放，WLR 降低 P
    ("WFWA", "LSGA"),   # L3：给水-水位动力学复杂（实测 sev100 LSGA 反升），不预设方向
]


def _forbidden_rules(variables: List[str]) -> List[Dict]:
    """按物理规则生成禁止边（marker 外生性）。

    规则（全部有明确物理依据，basis 落盘）：
      R1 marker→marker：三类 marker 是互斥的外生始发事件，互不因果；
      R2 安注→marker：安全动作不可能引起始发破口；
      R3 任何其他变量→marker：症状/安全动作不“产生”破口流量；
         唯一例外是 P→WLR（破口流量受压差驱动，列入 competitive 保留）。
    """
    rules: List[Dict] = []
    comp_flat = set()
    for a, b in COMPETITIVE_PAIRS:
        comp_flat.add((a, b))
        comp_flat.add((b, a))
    vset = set(variables)

    # R1 marker 互斥（两个方向）
    for i, m1 in enumerate(MARKER_COLS):
        for m2 in MARKER_COLS[i + 1:]:
            for src, dst in ((m1, m2), (m2, m1)):
                if src in vset and dst in vset:
                    rules.append({"from": src, "to": dst,
                                  "basis": "R1 marker为互斥外生始发事件，互不因果"})
    # R2 安全系统 → marker
    for s in SAFETY_STEP_VARS:
        for m in MARKER_COLS:
            if s in vset and m in vset:
                rules.append({"from": s, "to": m,
                              "basis": "R2 安全注入动作不引起始发破口"})
    # R3 其他一切变量 → marker（P→WLR 例外）
    for m in MARKER_COLS:
        if m not in vset:
            continue
        for v in variables:
            if v == m:
                continue
            if (v, m) in comp_flat:
                continue
            rules.append({"from": v, "to": m,
                          "basis": f"R3 下游症状/动作不外生产生破口指示器 {m}（外生故障过程）"})
    return rules


def build_ontology(accident_type: str, variables: List[str]) -> Dict:
    """构建某类模板的代码本体（只含 variables 中存在的变量/关系）。"""
    if accident_type not in ALLOWED_RELATIONS:
        raise ValueError(f"unknown accident type {accident_type}")
    vset = set(variables)
    entities = {v: ENTITY_TABLE.get(v, {"subsystem": "未登记", "meaning": v, "unit": "-"})
                for v in variables}

    allowed, competitive = [], []
    for rel in ALLOWED_RELATIONS[accident_type]:
        if rel["from"] in vset and rel["to"] in vset:
            allowed.append(dict(rel))
    for a, b in COMPETITIVE_PAIRS:
        if a in vset and b in vset:
            competitive.append({"pair": [a, b], "basis": "物理反馈环/L3方向不预设，双向保留"})

    ontology = {
        "accident_type": accident_type,
        "variables": variables,
        "entities": entities,
        "allowed": allowed,
        "competitive": competitive,
        "forbidden": _forbidden_rules(variables),
    }
    return ontology


def _edge_category(ontology: Dict, src: str, dst: str) -> str:
    """返回 forbidden / competitive / allowed / unknown。"""
    for fe in ontology["forbidden"]:
        if fe["from"] == src and fe["to"] == dst:
            return "forbidden"
    for c in ontology["competitive"]:
        a, b = c["pair"]
        if (src == a and dst == b) or (src == b and dst == a):
            return "competitive"
    for rel in ontology["allowed"]:
        if rel["from"] == src and rel["to"] == dst:
            return "allowed"
        if rel["direction"] == "bidirectional" and rel["from"] == dst and rel["to"] == src:
            return "allowed"
    return "unknown"


def apply_ontology_constraint(B: np.ndarray, variables: List[str],
                              ontology: Dict, use_ontology: bool = True
                              ) -> Tuple[np.ndarray, Dict]:
    """对单次学图结果 B（B[i,j]=v_j→v_i）施加本体约束。

    use_ontology=False 时原样返回（−本体约束消融走同一入口，禁止旁路）。
    """
    n = len(variables)
    B_c = B.copy()
    removed, marked, competitive_hits, unknown = [], [], [], []
    for i in range(n):
        for j in range(n):
            if i == j or abs(B[i, j]) < 1e-10:
                continue
            src, dst = variables[j], variables[i]
            if not use_ontology:
                unknown.append({"from": src, "to": dst, "weight": float(B[i, j])})
                continue
            cat = _edge_category(ontology, src, dst)
            edge = {"from": src, "to": dst, "weight": float(B[i, j])}
            if cat == "forbidden":
                basis = next(f["basis"] for f in ontology["forbidden"]
                             if f["from"] == src and f["to"] == dst)
                removed.append({**edge, "basis": basis})
                B_c[i, j] = 0.0
            elif cat == "competitive":
                competitive_hits.append(edge)
            elif cat == "allowed":
                marked.append(edge)
            else:
                unknown.append(edge)
    report = {"use_ontology": use_ontology,
              "n_removed": len(removed), "removed_forbidden": removed,
              "n_marked_allowed": len(marked), "marked_allowed": marked,
              "n_competitive": len(competitive_hits), "competitive_hits": competitive_hits,
              "n_unknown": len(unknown)}
    return B_c, report


# ==================== 过程本体层：共享基线 μ/σ（13.4-2）====================
def fit_process_ontology(pooled: np.ndarray, variables: List[str]
                         ) -> Tuple[Dict[str, float], Dict[str, float], List[Dict]]:
    """从 sev-1 恢复段池化矩阵估计 μ/σ。

    σ < CONSTANT_SIGMA 的变量跳过（返回排除清单），绝不做 1e-6 数值兜底。
    返回 (mu_sigma_keep, excluded)；调用方只对 keep 变量建阈值。
    """
    assert pooled.shape[1] == len(variables)
    mu = pooled.mean(axis=0)
    sigma = pooled.std(axis=0)
    keep_mu, keep_sigma, excluded = {}, {}, []
    for idx, v in enumerate(variables):
        if sigma[idx] < CONSTANT_SIGMA:
            excluded.append({"variable": v, "reason": f"基线窗σ≈0 (σ={sigma[idx]:.3e})，跳过不建阈值"})
            continue
        keep_mu[v] = float(mu[idx])
        keep_sigma[v] = float(sigma[idx])
    logger.info(f"[过程本体] keep={len(keep_mu)} excluded={len(excluded)} "
                f"({[e['variable'] for e in excluded]})")
    return keep_mu, keep_sigma, excluded

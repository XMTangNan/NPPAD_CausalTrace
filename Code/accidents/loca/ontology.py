"""
Phase 0.5：LOCA 事故极简核电本体构建。
三层结构：变量 → 子系统 → 物理关系。
边分类：forbidden（禁止边）、allowed（允许边，仅标记不添加）、unknown（未知边）。
"""
import json
import os
from typing import Dict, List

from accidents.loca.config import FROZEN_RELATIONS, FORBIDDEN_EDGES
from core.utils import logger

# 子系统分类
SUBSYSTEM_MAP = {
    "P": "一回路热工", "WLR": "一回路流量", "VOL": "一回路热工",
    "LVPZ": "一回路热工", "TAVG": "一回路热工", "THA": "一回路热工",
    "WRCA": "一回路流量", "QMWT": "反应性与功率", "PWNT": "反应性与功率",
    "RH": "反应性与功率", "PSGA": "蒸汽发生器", "DNBR": "安全指标",
    "WHPI": "安全系统", "WECS": "安全系统",
}

# 变量角色
ROLE_MAP = {
    "P": "系统状态", "WLR": "故障过程", "VOL": "系统状态", "LVPZ": "系统状态",
    "TAVG": "系统状态", "THA": "系统状态", "WRCA": "过程状态", "QMWT": "过程状态",
    "PWNT": "过程状态", "RH": "过程状态", "PSGA": "二回路状态", "DNBR": "安全指标",
    "WHPI": "控制动作", "WECS": "控制动作",
}

# 中文含义 + 单位
MEANING_MAP = {
    "P": "一回路压力 (bar)", "WLR": "一回路泄漏流量 (t/h)",
    "VOL": "一回路液体体积 (m³)", "LVPZ": "稳压器水位 (%)",
    "TAVG": "一回路平均温度 (°C)", "THA": "热段 A 温度 (°C)",
    "WRCA": "一回路流量 A (t/h)", "QMWT": "堆芯热功率 (MW)",
    "PWNT": "核功率 (%)", "RH": "总反应性",
    "PSGA": "蒸发器 A 压力 (bar)", "DNBR": "偏离泡核沸腾比",
    "WHPI": "高压注入流量 (t/h)", "WECS": "安全注入总流量 (t/h)",
}


def build_ontology(variables: List[str]) -> Dict:
    """构建本体字典：每个变量含 meaning/subsystem/role/relations。"""
    ontology = {"variables": {}, "relations": [], "forbidden_edges": []}

    for var in variables:
        ontology["variables"][var] = {
            "meaning": MEANING_MAP.get(var, var),
            "subsystem": SUBSYSTEM_MAP.get(var, "未知"),
            "role": ROLE_MAP.get(var, "未知"),
        }

    # 物理关系（来自冻结清单）
    for rel in FROZEN_RELATIONS:
        if rel["from"] in variables and rel["to"] in variables:
            ontology["relations"].append({
                "from": rel["from"], "to": rel["to"],
                "category": rel["category"], "direction": rel["direction"],
                "basis": rel["basis"], "evidence": rel["evidence"],
            })

    # 禁止边
    for src, dst, basis in FORBIDDEN_EDGES:
        if src in variables and dst in variables:
            ontology["forbidden_edges"].append({"from": src, "to": dst, "basis": basis})

    return ontology


def get_edge_category(ontology: Dict, src: str, dst: str) -> str:
    """判断一条边的类别：forbidden / allowed / unknown。"""
    for fe in ontology["forbidden_edges"]:
        if fe["from"] == src and fe["to"] == dst:
            return "forbidden"
    for rel in ontology["relations"]:
        if rel["from"] == src and rel["to"] == dst:
            return "allowed"
        if rel["direction"] == "bidirectional" and rel["from"] == dst and rel["to"] == src:
            return "allowed"
    return "unknown"


def serialize_causal_graph(edges: List[Dict], variables: List[str]) -> str:
    """将因果图边列表序列化为文本（供解释生成提示注入）。"""
    lines = ["因果图边列表（序列化）："]
    for e in edges:
        lines.append(f"  {e['from']} → {e['to']}: 总效应 {e.get('total_effect', 'N/A')}, "
                     f"直接效应 {e.get('direct_effect', 'N/A')}, "
                     f"Bootstrap频率 {e.get('f_e', 'N/A')}")
    return "\n".join(lines)


def save_ontology(ontology: Dict, path: str = None):
    from config import RESULT_DIR
    path = path or os.path.join(RESULT_DIR, "ontology.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ontology, f, ensure_ascii=False, indent=2)
    logger.info(f"本体已保存 -> {path}")
    return path


if __name__ == "__main__":
    from accidents.loca.data_loader import load_all_loca
    _, vars_ = load_all_loca()
    ont = build_ontology(vars_)
    save_ontology(ont)
    print(f"本体: {len(ont['variables'])} 变量, {len(ont['relations'])} 关系, "
          f"{len(ont['forbidden_edges'])} 禁止边")

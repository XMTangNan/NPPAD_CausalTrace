"""v2.1 R5 LLM 解释层（LLM 生成 × 符号约束防幻觉，对齐论文 causal graph prompt injection）。

复用方案一 accidents/loca/explanation.py 的 LLM 调用与忠实性校验机制（DeepSeek
Chat Completions + 因果图提示注入 + check_faithfulness + 降级模板），适配 v2.1
跨事故三分类的传播链记录（chain JSON），并新增【通俗解读】段（业外人士可读）。

设计原则（继承方案一，13.10 措辞纪律）：
  1. 所有数值来自 chain 记录，LLM 只能引用注入的因果事实，不得编造变量/边/数值/机制。
  2. 忠实性校验：LLM 输出的变量名必须 ∈ 合法集合；RRF 数值若被引用须与 ranked_nodes 一致。
  3. 降级机制：无 key / 调用失败 / 忠实性校验不过 -> 降级到 chains.render_chain_text 模板。
  4. 密钥经环境变量 DEEPSEEK_API_KEY 或 Code/.env 读取，绝不硬编码。
  5. 新增【通俗解读】：业外人士可读的日常比喻，不含专业变量名，不进忠实性校验。
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Sequence

from config import (
    LLM_API_KEY_ENV, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT,
    LLM_TEMPERATURE,
)
from core.utils import logger
from rca.v21.chains import ROOT_CAUSE_NAME, render_chain_text, _entity, _vname

# 数值校验浮点容差
FLOAT_TOL = 1e-3
# 忠实性校验里排除的非物理变量大写词（术语，不视为幻觉）
_NON_VAR_TERMS = {
    "RRF", "GAM", "ANM", "BFS", "MAD", "LINGAM", "RCA", "LLM", "API",
    "FULL", "MASKED", "Q25", "Q75", "L1", "L2", "L3", "JSON", "DEEPSEEK",
    "LI", "ID", "E1", "E2", "E3", "SGTR", "PWR", "NPP", "PCTRAN",
}
# 变量幻觉检测仅针对 ≥3 字母的传感器式变量名；2 字母领域缩写（SG=蒸汽发生器、
# PC/AC 等）不计入，避免误报。合法变量 RH(2字母)/P(1字母) 本就在合法集合中。
_VAR_RE = re.compile(r"\b[A-Z]{3,}[A-Z0-9]*\b")
_RRF_RE = re.compile(r"[Rr][Rr][Ff]\s*[:=]?\s*(\d+\.\d{2,4})")


def _valid_variables(rec: Dict) -> set:
    """LLM 可合法引用的变量名集合（chain 记录里真实出现的）。"""
    vs: set = set()
    vs.add(rec["template"]["marker"])
    vs.add(rec["pred"])
    vs.add(rec["true"])
    for a in rec.get("A", []):
        vs.add(a["variable"])
    for o in rec.get("outside_reach", []):
        vs.add(o["variable"])
    for n in rec.get("ranked_nodes", []):
        vs.add(n["node"])
        for p in n.get("path", []):
            vs.add(p)
    return vs


# ==================== LLM 调用（迁移方案一） ====================
def _get_api_key() -> str:
    """优先环境变量 DEEPSEEK_API_KEY，其次 Code/.env；不硬编码。"""
    key = os.environ.get(LLM_API_KEY_ENV, "").strip()
    if key:
        return key
    env_paths = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), ".env"),
    ]
    for env_path in env_paths:
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    if k.strip() == LLM_API_KEY_ENV:
                        return v.strip().strip('"').strip("'")
    return ""


def _call_deepseek_api(prompt: str) -> Optional[str]:
    """DeepSeek Chat Completions；失败返回 None。"""
    api_key = _get_api_key()
    if not api_key:
        logger.warning(f"未找到 LLM API key（{LLM_API_KEY_ENV} 或 Code/.env）")
        return None
    import requests
    url = f"{LLM_BASE_URL}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": LLM_TEMPERATURE,
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=LLM_TIMEOUT)
        if resp.status_code != 200:
            logger.error(f"DeepSeek 调用失败 HTTP {resp.status_code}: {resp.text[:300]}")
            return None
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"DeepSeek 调用异常: {e}")
        return None


# ==================== 因果图提示注入（适配 v2.1 传播链） ====================
_SYSTEM_PROMPT = (
    "你是核电事故根因分析专家。你将收到基于因果追踪算法（CausalTrace）计算得到的"
    "因果图事实（跨事故类型：LOCA/SGATR/FLB）。你的任务是生成一份自然语言根因分析报告。"
    "严格遵守以下规则：\n"
    "1. 只能引用提供的因果事实，不得编造任何变量、边、数值或物理机制；\n"
    "2. 引用数值时保留原始精度（如 RRF=0.7764）；\n"
    "3. 方向一致率低于 90% 或标注方向冲突/翻转的边，必须显式标注不确定性；\n"
    "4. 故障指示器（WLR/WTRA/WFLB）是事故发生的信号，不是根因本身，根因是事故工况；\n"
    "5. 结论表述为'模型稳定识别出的有向关系'，非'已证实因果'；\n"
    "6. 使用中文，结构清晰；\n"
    "7. 报告末尾必须有一段【通俗解读】：用业外人士（非核电专业）能理解的日常比喻，"
    "说明这是什么故障、系统里大致发生了什么、算法凭什么判断出故障类型，约 100~150 字，"
    "避免专业术语与变量名。"
)


def _build_prompt_context(rec: Dict) -> str:
    """把 chain 记录序列化为 LLM prompt 上下文（对应论文 causal graph prompt injection）。"""
    tpl = rec["template"]
    k, g, marker = tpl["k"], tpl["g"], tpl["marker"]
    fl = rec["first_layer"]
    lines: List[str] = []
    lines.append("【判定事故类型】")
    lines.append(f"  {ROOT_CAUSE_NAME[k]}（判定模板 {k}_{g}，条件 {rec['condition']}）")
    lines.append(f"  故障指示器：{marker}（{_entity(marker)['meaning']}，"
                 f"{_entity(marker)['subsystem']}）——是事故信号，不是根因。")
    lines.append("")
    lines.append("【第一层证据分】")
    e1_txt = "不适用（MASKED 条件 marker 已掩蔽）" if fl["E1"] is None else f"{fl['E1']:.3f}"
    lines.append(f"  E1={e1_txt}；E2 原始分={fl['E2_raw']:.3f}（归一 {fl['E2_norm']:.3f}）；"
                 f"E3_path 原始分={fl['E3_path_raw']:.3f}（归一 {fl['E3_path_norm']:.3f}）"
                 + ("" if fl["E3_base_raw"] is None else
                    f"；E3_base 原始分={fl['E3_base_raw']:.3f}")
                 + f"；融合分={fl['fusion_score']:.3f}，margin={fl['margin']:.3f}。")
    lines.append("")
    lines.append("【观测异常集 A(x)】")
    if rec["A"]:
        for a in rec["A"]:
            lines.append(f"  {_vname(a['variable'])} 偏离方向={'↑' if a['observed_d']>0 else '↓' if a['observed_d']<0 else '≈0'}")
    else:
        lines.append("  （空）")
    lines.append("")
    lines.append("【marker→症状传播链】（双引擎 RRF 排序，RRF 越大越靠前）")
    nodes = rec["ranked_nodes"]
    if not nodes:
        lines.append("  A(x) 中无变量在 marker 稳定边可达域内，本次不出传播链。")
    for i, n in enumerate(nodes, 1):
        if not all(a["grounded"] for a in n["path_edges"]):
            continue
        chain = " → ".join(n["path"])
        cl = f"|cL|={abs(n['cL']['cL']):.3g}" if n["cL"] else "cL 缺失"
        ca = f"|cA|={abs(n['cA']['cA']):.3g}" if n["cA"] else "cA 不可估"
        tags = _node_tags(n)
        lines.append(f"  {i}) {chain}｜RRF={n['rrf']:.4f}"
                     f"（rank_L={n['rank_L']}, rank_A={n['rank_A']}，{cl}，{ca}）"
                     f"｜子系统：{_entity(n['node'])['subsystem']}"
                     + (f"｜{tags}" if tags else ""))
    lines.append("")
    lines.append("【可达域外异常（未纳入传播链，不硬排）】")
    if rec["outside_reach"]:
        for o in rec["outside_reach"]:
            lines.append(f"  {_vname(o['variable'])}")
    else:
        lines.append("  （无）")
    lines.append("")
    unc = _uncertainty_plain(rec)
    lines.append("【不确定性标注】")
    lines.append(unc if unc else "  （未触发强制标注）")
    return "\n".join(lines)


def _node_tags(n: Dict) -> str:
    tags = []
    if n["magnitude_unstable"]:
        tags.append("量级不稳")
    if n["a_unestimable"]:
        tags.append("cA不可估")
    for a in n["path_edges"]:
        if a["uncertain_direction"]:
            tags.append("方向稳定率<0.90")
        if a["consensus_conflict"]:
            tags.append("两引擎冲突")
        if a["consensus_single"]:
            tags.append("仅LiNGAM单边")
        if a["severity_flipped"]:
            tags.append("跨严重度翻转")
        if a["competitive"]:
            tags.append("竞争边")
    return "、".join(tags)


def _uncertainty_plain(rec: Dict) -> str:
    fl = rec["flags"]
    items = []
    if fl["consensus_conflict_edges"]:
        items.append("两引擎共识冲突边：" + "、".join(fl["consensus_conflict_edges"]))
    if fl["uncertain_direction_edges"]:
        items.append("方向稳定率<0.90 边：" + "、".join(fl["uncertain_direction_edges"]))
    if fl["severity_flipped_edges"]:
        items.append("跨严重度符号翻转边：" + "、".join(fl["severity_flipped_edges"]))
    if fl["competitive_edges"]:
        items.append("竞争边/反馈环：" + "、".join(fl["competitive_edges"]))
    if fl["cf_fail_nodes"]:
        items.append("Alg3 反事实重演未通过(gCF=0.5)变量：" + "、".join(fl["cf_fail_nodes"]))
    if rec.get("cf_template_gcf") == 0.5:
        items.append("模板级 Alg3 重演门控 template_gcf=0.5")
    return "；".join(items)


# ==================== 忠实性校验（适配 v2.1） ====================
def check_faithfulness(text: str, rec: Dict) -> Dict:
    """校验 LLM 输出：变量名必须 ∈ 合法集合；RRF 数值若引用须与 ranked_nodes 一致。

    【通俗解读】段是比喻、不含变量名与数值，自然不命中校验，符合预期。
    """
    mismatches: List[Dict] = []
    valid = _valid_variables(rec)

    # 1) 变量存在性：非术语大写词必须 ∈ 合法集合
    for m in _VAR_RE.finditer(text):
        tok = m.group(0)
        if tok in _NON_VAR_TERMS or tok in valid:
            continue
        mismatches.append({"field": "variable_existence", "found": tok,
                           "expected": "合法变量/术语"})

    # 2) RRF 数值：若 LLM 复述 RRF 数值，须与 ranked_nodes 对拍
    node_rrf = {n["node"]: n["rrf"] for n in rec.get("ranked_nodes", [])}
    # 提取 "变量名 ... RRF=数值" 片段做粗对拍：仅校验数值本身是否来自某节点的 rrf 集合
    all_rrf = {round(n["rrf"], 4) for n in rec.get("ranked_nodes", [])}
    for m in _RRF_RE.finditer(text):
        try:
            found = round(float(m.group(1)), 4)
        except ValueError:
            continue
        if found not in all_rrf:
            mismatches.append({"field": "rrf_value", "found": m.group(1),
                               "expected": "∈ ranked_nodes.rrf"})

    return {"pass": len(mismatches) == 0, "mismatches": mismatches}


# ==================== LLM 解释生成（注入→调用→校验→降级） ====================
def generate_llm_explanation(rec: Dict) -> Dict:
    """对一条 chain 记录生成 LLM 解释；失败/幻觉降级到 render_chain_text 模板。

    返回 {llm_used, llm_explanation, faithful_pass, degraded, degraded_reason}。
    """
    if not _get_api_key():
        return _degrade(rec, "无 API key")
    prompt_context = _build_prompt_context(rec)
    full_prompt = (
        "请基于以下因果分析事实，生成一份结构化的根因分析报告。\n\n"
        f"{prompt_context}\n\n"
        "报告结构：\n"
        "1. 【判定】事故类型与第一层证据概述；\n"
        "2. 【传播链】marker→症状的稳定识别出的有向关系，逐项含 RRF 强度与不确定性；\n"
        "3. 【结论】最可能的故障类型及依据，标注方向可靠性；\n"
        "4. 【通俗解读】业外人士可读的日常比喻。"
    )
    logger.info(f"调用 LLM 生成解释（{rec['condition']}/{rec['true']}/{rec['severity']}，"
                f"模型 {LLM_MODEL}）...")
    llm_output = _call_deepseek_api(full_prompt)
    if not llm_output:
        return _degrade(rec, "LLM 调用失败/无输出")

    faith = check_faithfulness(llm_output, rec)
    if not faith["pass"]:
        logger.warning(f"LLM 解释忠实性校验未通过（{len(faith['mismatches'])} 处），降级模板。")
        for m in faith["mismatches"][:5]:
            logger.warning(f"  不匹配: {m}")
        return _degrade(rec, "忠实性校验未通过")

    return {
        "llm_used": True,
        "llm_explanation": llm_output,
        "faithful_pass": True,
        "faithfulness": faith,
        "degraded": False,
        "degraded_reason": None,
    }


def _degrade(rec: Dict, reason: str) -> Dict:
    return {
        "llm_used": False,
        "llm_explanation": render_chain_text(rec),
        "faithful_pass": None,
        "faithfulness": {"pass": None, "mismatches": []},
        "degraded": True,
        "degraded_reason": reason,
    }


# ==================== 自检（不真实调 LLM） ====================
if __name__ == "__main__":
    import json as _json
    from pathlib import Path
    V21 = Path(__file__).resolve().parents[2] / "output" / "rca_v21"
    chain_dir = V21 / "chains"
    sample = None
    for p in sorted(chain_dir.glob("root_cause_chain_FULL_*.json")):
        sample = p
        break
    if sample is None:
        logger.info("无 chain 产物，无法自检")
        raise SystemExit(0)
    rec = _json.load(open(sample, encoding="utf-8"))
    ctx = _build_prompt_context(rec)
    print("=" * 70)
    print("prompt 上下文（前 2000 字）：")
    print(ctx[:2000])
    print("=" * 70)
    # 模拟一段含幻觉的 LLM 输出，验证 check_faithfulness
    fake = "传播链 WLR→VOL→LVPZ RRF=0.7764，另外 XXYZ 变量也异常。"
    print("check_faithfulness（含幻觉 XXYZ）：",
          check_faithfulness(fake, rec))
    print("API key 存在：", bool(_get_api_key()))

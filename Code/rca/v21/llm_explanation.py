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
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from config import (
    LLM_API_KEY_ENV, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT,
    LLM_TEMPERATURE,
)
from core.utils import logger
from rca.v21 import artifact
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


# ==================================================================
# V2.3 D6：两级后端解释生成器（rule 离线默认 / llm 可选）+ 忠实性校验
# ------------------------------------------------------------------
# 铁律（方案 v2.3 §6.1 D6）：
#   1. 只读 output/rca_v23 下的 tracing/root_candidates_*、signatures/、
#      reports/summary|diff|deviation_*，不重算因果、不读旧 rca_v21/v22 产物；
#   2. rule 后端纯离线、确定性，输出的每个数字与变量名都必须能在 payload 找到；
#   3. llm 后端只在 CAUSALTRACE_LLM_BASE_URL 与 CAUSALTRACE_LLM_API_KEY 同时存在时启用，
#      任何失败都降级为 rule 并写 degraded_reason，绝不静默成功；
#   4. llm 输出不忠实 → backend 仍记 "llm"、faithful=False，并把 unsupported 列表落盘。
# ==================================================================
V23_LLM_BASE_URL_ENV = "CAUSALTRACE_LLM_BASE_URL"
V23_LLM_API_KEY_ENV = "CAUSALTRACE_LLM_API_KEY"
V23_LLM_MODEL_ENV = "CAUSALTRACE_LLM_MODEL"
V23_LLM_MODEL_DEFAULT = "deepseek-chat"
V23_LLM_TIMEOUT_S = 30          # 标准库 urllib，硬超时 30s（测试可 monkeypatch）
FAITHFUL_DECIMALS = 2           # 数值对拍容差：保留 2 位小数后相等
K2_SIGNATURE_KEYS = ("physics", "physics_constraint", "physics_constraints",
                     "k2", "physics_residual")
K2_ARTIFACT_NAMES = ("physics_constraint_{k}_{g}", "k2_{k}_{g}",
                     "physics_residuals_{k}_{g}")
REVERSE_EDGE_NOTE = "反向边=稳定边方向与故障模板 expected_chain 相反"

# 数字：带可选负号；前面不能紧邻字母/数字/下划线/小数点，
# 避免把 RM1（1）、v23c（23）、R1_inventory_leak（1）里的数字当成数值
_NUM_V23_RE = re.compile(r"(?<![A-Za-z0-9_.])(-?\d+(?:\.\d+)?)(%?)")
# 任务规定形态：[A-Z][A-Z0-9_]{1,}
_UPPER_VAR_RE = re.compile(r"[A-Z][A-Z0-9_]{1,}")
# 允许引用但不属于物理变量的领域术语（K2/K3 是方案节号；JSON 是格式名）
_V23_ALLOWED_TERMS = ["K2", "K3", "JSON"]

V23_SYSTEM_PROMPT = (
    "你是核电事故根因分析专家。你将收到一份 JSON 证据（CausalTrace v2.3 单引擎 LiNGAM "
    "稳定边 + 溯源 Top-5 + 跨组汇总/差异 + K2/K3/偏离度）。严格规则：\n"
    "1. 只允许引用输入 JSON 中出现的数字与变量名，禁止编造任何变量、边、数值或机制；\n"
    "2. 百分比请保留 JSON 数值换算后的两位小数，禁止改写或补齐精度；\n"
    "3. 单引擎稳定边不等于多引擎共识，必须标注 n_consensus=null；\n"
    "4. 结论表述为“模型稳定识别出的有向关系”，不得写成“已证实因果”；\n"
    "5. 章节固定：结论与 Top 根因 / 证据链 / 跨组一致性 / 物理一致性 K2 / "
    "故障模板 K3 / 偏离度提示 / 局限；\n"
    "6. 使用中文，禁止输出 JSON 之外的新数字。"
)


# ==================== 忠实性校验 ====================
def _walk_numbers(node: Any, out: set) -> None:
    """递归收集 payload 中可被引用的数值（含字符串里的数字与 <1 比值的百分数）。"""
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        v = float(node)
        out.add(round(v, FAITHFUL_DECIMALS))
        if 0.0 < abs(v) <= 1.0:                      # 比例类允许按百分比引用
            out.add(round(v * 100.0, FAITHFUL_DECIMALS))
        return
    if isinstance(node, str):
        for m in _NUM_V23_RE.finditer(node):
            v = float(m.group(1))
            out.add(round(v, FAITHFUL_DECIMALS))
            if 0.0 < abs(v) <= 1.0:
                out.add(round(v * 100.0, FAITHFUL_DECIMALS))
        return
    if isinstance(node, dict):
        for k, v in node.items():
            _walk_numbers(k, out)
            _walk_numbers(v, out)
        return
    if isinstance(node, (list, tuple, set)):
        for v in node:
            _walk_numbers(v, out)


def _walk_variables(node: Any, out: set) -> None:
    """递归收集 payload 中出现的变量名/UPPER 词（含字符串内嵌的变量。"""
    if isinstance(node, str):
        out.update(_UPPER_VAR_RE.findall(node))
        return
    if isinstance(node, dict):
        for k, v in node.items():
            _walk_variables(k, out)
            _walk_variables(v, out)
        return
    if isinstance(node, (list, tuple, set)):
        for v in node:
            _walk_variables(v, out)


def payload_number_set(payload: Dict) -> set:
    s: set = set()
    _walk_numbers(payload, s)
    return s


def payload_variable_set(payload: Dict) -> set:
    vs: set = set(payload.get("variables") or [])
    vs.update(payload.get("allowed_terms") or [])
    for c in payload.get("root_candidates") or []:
        if isinstance(c, dict) and c.get("variable"):
            vs.add(c["variable"])
    _walk_variables(payload, vs)
    return vs


def verify_faithfulness(text: str, payload: Dict) -> Dict:
    """校验解释文本：数字必须能在 payload 找到，UPPER 变量名必须在 payload 词表内。

    返回 {n_numbers, n_numbers_unsupported, unsupported, n_vars, n_vars_unknown,
          unknown, faithful}；数值容差为保留 2 位小数后相等。
    """
    nums = payload_number_set(payload)
    vars_ok = payload_variable_set(payload)

    unsupported: List[str] = []
    n_numbers = 0
    for m in _NUM_V23_RE.finditer(text):
        n_numbers += 1
        raw = m.group(1)
        try:
            val = float(raw)
        except ValueError:
            continue
        if round(val, FAITHFUL_DECIMALS) not in nums:
            unsupported.append(raw)

    unknown: List[str] = []
    n_vars = 0
    for m in _UPPER_VAR_RE.finditer(text):
        n_vars += 1
        tok = m.group(0)
        if tok not in vars_ok:
            unknown.append(tok)

    return {
        "n_numbers": n_numbers,
        "n_numbers_unsupported": len(unsupported),
        "unsupported": unsupported,
        "n_vars": n_vars,
        "n_vars_unknown": len(unknown),
        "unknown": unknown,
        "faithful": not unsupported and not unknown,
    }


# ==================== 只读输入（不重算因果） ====================
def _read_optional(sub: str, name: str):
    """读 output/rca_v23/<sub>/<name>.json；不存在返回 (None, None)。"""
    path = Path(artifact.V23_SUBDIRS[sub]) / f"{name}.json"
    if not path.exists():
        return None, None
    with open(path, encoding="utf-8") as f:
        return json.load(f), path


def _rel_path(sub: str, name: str) -> str:
    return f"{sub}/{name}.json"


def _num_or_none(x) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _fmt_edge(e) -> str:
    """K3 边条目转文本：{"template","stable"} / {"template","reason"} / 字符串。"""
    if isinstance(e, dict):
        tpl = e.get("template") or e.get("from")
        stable = e.get("stable") or e.get("to")
        if e.get("reason"):
            return f"{tpl}({e['reason']})"
        return f"模板{tpl} 反向为稳定边{stable}" if stable else str(tpl)
    return str(e)


def _normalize_k2(doc: Dict, source: str) -> Dict:
    rels = doc.get("relations") or []
    n_viol = doc.get("n_violation")
    n_not = doc.get("n_not_violated")
    n_und = doc.get("n_undecidable")
    if n_viol is None:
        n_viol = sum(1 for r in rels if r.get("status") == "violation")
    if n_not is None:
        n_not = sum(1 for r in rels if r.get("status") == "not_violated")
    if n_und is None:
        n_und = sum(1 for r in rels if r.get("status") == "undecidable")
    n_rel = doc.get("n_relations_applicable", len(rels))
    ratio = doc.get("undecidable_ratio")
    if ratio is None:
        ratio = round(float(n_und) / max(int(n_rel), 1), 4)
    return {
        "available": True,
        "source": source,
        "n_relations": int(n_rel),
        "n_violation": int(n_viol),
        "n_not_violated": int(n_not),
        "n_undecidable": int(n_und),
        "undecidable_ratio": ratio,
        "relations": [{"relation_id": r.get("relation_id"),
                       "status": r.get("status")} for r in rels],
        "note": doc.get("note", "满足约束≠方向证明、违反≠链错"),
    }


def _load_k2(k: str, g: str, sig: Dict) -> Dict:
    """K2 三态：优先签名 auxiliary.k2，其次历史顶层键/独立产物；缺失如实标记。

    V23-CODE-022：D 阶段把 K2 挂在 `signature["auxiliary"]["k2"]`，本函数原先只查
    顶层 `physics*/k2` 键，导致 9 份解释与 9 份报告把已有证据误写成“未产出”。
    """
    aux = sig.get("auxiliary") or {}
    aux_doc = aux.get("k2")
    if isinstance(aux_doc, dict) and ("relations" in aux_doc or "n_violation" in aux_doc):
        return _normalize_k2(aux_doc, f"signatures/signature_{k}_{g}.json#auxiliary.k2")
    for key in K2_SIGNATURE_KEYS:
        doc = sig.get(key)
        if isinstance(doc, dict) and ("relations" in doc or "n_violation" in doc):
            return _normalize_k2(doc, f"signatures/signature_{k}_{g}.json#{key}")
    for sub in ("tracing", "reports"):
        for tpl in K2_ARTIFACT_NAMES:
            doc, _ = _read_optional(sub, tpl.format(k=k, g=g))
            if doc is not None:
                return _normalize_k2(doc, _rel_path(sub, tpl.format(k=k, g=g)))
    return {"available": False,
            "reason": "physics_constraints 尚未接入签名，output/rca_v23 下无 K2 三态产物"}


def _load_k3(k: str, sig: Dict) -> Dict:
    """K3 故障模板：只走唯一实现 `external_knowledge.match_fault_template`。

    优先读签名里挂载的 `auxiliary.k3`；否则用签名的 stable_edges 现算。
    禁止在本模块另写一套覆盖率口径（历史上曾按“变量是否入模”统计，与边覆盖
    口径不一致，导致同一模板出现两个覆盖率）。
    """
    aux = (sig.get("auxiliary") or {}).get("k3")
    if isinstance(aux, dict) and aux.get("available", True) and "n_matched" in aux:
        doc = dict(aux)
        src = f"signatures/signature_{k}_auxiliary.k3"
    else:
        from rca.v21.external_knowledge import match_fault_template
        try:
            doc = match_fault_template(k, sig.get("stable_edges") or [],
                                       sig.get("variables") or [])
        except Exception as exc:
            return {"available": False,
                    "reason": f"K3 模板匹配失败：{type(exc).__name__}: {exc}"}
        src = "external_knowledge.match_fault_template"
    if not doc.get("available"):
        return {"available": False,
                "reason": doc.get("reason", "K3 模板不可用")}
    chain = list(doc.get("expected_chain") or [])
    return {
        "available": True,
        "source": src,
        "verification": doc.get("verification"),
        "n_expected_vars": len(chain),
        "n_chain_edges": doc.get("n_chain_edges", max(len(chain) - 1, 0)),
        "n_matched": doc.get("n_matched", 0),
        "coverage": doc.get("coverage", 0.0),
        "n_reverse_edges": doc.get("n_reversed", 0),
        "reverse_edges": doc.get("reversed_edges") or [],
        "missing_edges": doc.get("missing_edges") or [],
    }


def _load_deviation(k: str, g: str) -> Dict:
    """偏离度 4 级计数；未产出写原因（不生成空表）。"""
    doc, _path = _read_optional("reports", f"deviation_{k}_{g}")
    if doc is not None:
        return {"available": True, "source": _rel_path("reports", f"deviation_{k}_{g}"),
                "n_variables": doc.get("n_variables"),
                "level_counts": doc.get("level_counts"),
                "note": doc.get("note", "偏离度 ≠ 根因")}
    rep_dir = Path(artifact.V23_SUBDIRS["reports"])
    cands = sorted(rep_dir.glob(f"deviation_{k}_*.json"))
    if cands:
        try:
            with open(cands[0], encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError):
            doc = None
        if doc is not None:
            return {"available": True,
                    "source": f"reports/{cands[0].name}",
                    "n_variables": doc.get("n_variables"),
                    "level_counts": doc.get("level_counts"),
                    "note": "同类其它严重度代表点产物，非本组专用；偏离度 ≠ 根因"}
    return {"available": False,
            "reason": f"reports/ 下无 deviation_{k}_{g}.json（偏离度未接入 v2.3 产物）"}


# ==================== payload 构建（唯一证据入口） ====================
def build_payload(k: str, g: str, run_id: Optional[str] = None) -> Dict:
    """组装 D6/D7 共用证据 payload；只读 v2.3 产物，不重算因果。"""
    rc, rc_path = _read_optional("tracing", f"root_candidates_{k}_{g}")
    if rc is None:
        raise FileNotFoundError(
            f"缺 tracing/root_candidates_{k}_{g}.json（先跑 run_v23_end2end）")
    sig, sig_path = _read_optional("signatures", f"signature_{k}_{g}")
    if sig is None:
        raise FileNotFoundError(
            f"缺 signatures/signature_{k}_{g}.json（先跑 run_v23_end2end）")
    summary, sum_path = _read_optional("reports", f"summary_{k}")
    diff, diff_path = _read_optional("reports", f"diff_{k}")

    owners = [rc.get("run_id"), sig.get("run_id"),
              (summary or {}).get("run_id"), (diff or {}).get("run_id")]
    if run_id is None:
        run_id = rc.get("run_id")
    bad = [r for r in owners if r is not None and r != run_id]
    if bad:
        raise ValueError(f"拒绝混源：{k}_{g} 的输入 run_id={bad} != {run_id}")

    gating = rc.get("gating") or {}
    n_stable = rc.get("n_stable_edges")
    n_clipped = rc.get("n_clipped_edges")
    if n_clipped is None:
        n_clipped = len(rc.get("clipped_edges") or [])

    cL = sig.get("cL") or {}
    roots: List[Dict] = []
    for c in (rc.get("root_candidates") or [])[:5]:
        var = c.get("variable")
        aux = cL.get(var) or {}
        roots.append({
            "rank": c.get("rank"),
            "variable": var,
            "subsystem": (c.get("entity") or {}).get("subsystem"),
            "role": c.get("role"),
            "total_effect": c.get("total_effect"),
            "in_degree": c.get("in_degree"),
            "out_degree": c.get("out_degree"),
            "reach_size": c.get("reach_size"),
            "weighted_reach": c.get("weighted_reach"),
            "auxiliary_cL": aux.get("cL"),
            "auxiliary_piL": aux.get("piL"),
        })

    cross = {"available": summary is not None}
    if summary is not None:
        cg = summary.get("cross_group") or {}
        cross.update({
            "top1_by_group": cg.get("top1_by_group"),
            "top1_stable": cg.get("top1_stable"),
            "mean_pairwise_overlap": cg.get("mean_pairwise_overlap"),
            "n_vars_in_all_groups": cg.get("n_vars_in_all_groups"),
            "pairwise": [{"pair": p.get("pair"), "overlap": p.get("overlap"),
                          "jaccard": p.get("jaccard")}
                         for p in (cg.get("pairwise") or [])],
            "edge_counts": summary.get("edge_counts"),
            "n_bootstrap": summary.get("n_bootstrap"),
        })
    else:
        cross["reason"] = f"跨组汇总未产出（reports/summary_{k}.json 不存在）"

    cross_diff = {"available": diff is not None}
    if diff is not None:
        cross_diff.update({
            "n_top1_switches": len(diff.get("top1_switches") or []),
            "n_mechanism_switches": len(diff.get("mechanism_switches") or []),
            "n_edge_count_diffs": len(diff.get("edge_count_diffs") or []),
            "transitions": [{"from_group": t.get("from_group"),
                             "to_group": t.get("to_group"),
                             "top5_jaccard": t.get("top5_jaccard"),
                             "top1": t.get("top1"),
                             "n_stable_edges": t.get("n_stable_edges")}
                            for t in (diff.get("transitions") or [])],
        })
    else:
        cross_diff["reason"] = f"跨组差异未产出（reports/diff_{k}.json 不存在）"

    k2 = _load_k2(k, g, sig)
    k3 = _load_k3(k, sig)
    deviation = _load_deviation(k, g)

    baseline = rc.get("baseline_coverage_summary") or {}
    limitations: List[str] = []
    if not rc.get("consensus_available"):
        limitations.append(
            f"单引擎口径：{n_stable} 条稳定边来自 LiNGAM 单引擎，"
            f"n_consensus=null，稳定边不等于多引擎共识")
    if int(n_clipped or 0) > 0:
        limitations.append(
            f"B 截断：{n_clipped} 条边在 |B|>{_num_or_none(rc.get('b_clip'))} 处被截断，"
            f"相关量级解释需对照 clip 敏感性")
    if int(baseline.get("n_truncated") or 0) > 0:
        limitations.append(
            f"恢复段不完整：{baseline.get('n_truncated')} 条训练轨迹 t_last 早于基线窗口末端")
    stab = rc.get("stability") or {}
    if stab.get("degraded"):
        limitations.append(
            "数值不稳定、结论降级：" + "；".join(stab.get("reasons") or []) +
            "（V23-ALGO-001 门控，排名仅供参考）")
    if summary is not None and not (summary.get("cross_group") or {}).get("top1_stable"):
        limitations.append("跨组不稳定：三组 Top-1 不一致，单组结论不能外推为类级结论")
    if not k2["available"]:
        limitations.append("物理一致性未被检验：K2 残差三态未产出")
    if k3["available"] and k3["coverage"] < 1.0:
        limitations.append(
            f"故障模板覆盖不全：expected_chain {k3['n_chain_edges']} 条边中仅 "
            f"{k3['n_matched']} 条命中")
    if not deviation["available"]:
        limitations.append("偏离度未产出：无法给出 4 级偏离提示")

    inputs = []
    for sub, path in (("tracing", rc_path), ("signatures", sig_path),
                      ("reports", sum_path), ("reports", diff_path)):
        if path is not None:
            inputs.append({"path": _rel_path(sub, path.stem),
                           "sha256": artifact.sha256_file(path)})

    payload = {
        "schema": "causaltrtrace/v2.3/llm-payload",
        "run_id": run_id,
        "accident_type": k,
        "group": g,
        "template": f"{k}_{g}",
        "marker": (rc.get("template") or {}).get("marker"),
        "engine": rc.get("engine"),
        "n_bootstrap": rc.get("n_bootstrap"),
        "n_variables": rc.get("n_nodes"),
        "n_available_vars": gating.get("n_available_vars"),
        "gate_ratio": gating.get("gate_ratio"),
        "n_stable_edges": n_stable,
        "n_clipped_edges": n_clipped,
        "b_clip": rc.get("b_clip"),
        "cond_IminusB": rc.get("cond_IminusB"),
        "cond_IminusB_after_clip": rc.get("cond_IminusB_after_clip"),
        "stability": rc.get("stability"),
        "consensus_available": bool(rc.get("consensus_available")),
        "n_consensus": rc.get("n_consensus"),
        "baseline_coverage": baseline,
        "top_k": min(3, len(roots)),
        "root_candidates": roots,
        "variables": list(sig.get("variables") or []),
        "auxiliary": {
            "frontier_reach": len((sig.get("frontier") or {}).get("reach") or []),
            "cL": {r["variable"]: {"cL": r["auxiliary_cL"], "piL": r["auxiliary_piL"]}
                   for r in roots},
            "flags": sig.get("flags"),
        },
        "cross_group": cross,
        "cross_group_diff": cross_diff,
        "k2": k2,
        "k3": k3,
        "deviation": deviation,
        "limitations": limitations,
        "allowed_terms": list(_V23_ALLOWED_TERMS),
        "inputs": inputs,
    }
    return payload


# ==================== rule 后端（离线，默认） ====================
def _f(x, digits: int = FAITHFUL_DECIMALS, suffix: str = "") -> str:
    v = _num_or_none(x)
    return "未产出" if v is None else f"{v:.{digits}f}{suffix}"


def _pct(x) -> str:
    v = _num_or_none(x)
    return "未产出" if v is None else f"{v * 100:.{FAITHFUL_DECIMALS}f}%"


def render_rule_explanation(payload: Dict) -> str:
    """基于 payload 数值的结构化中文解释（确定性、可核查、零外部依赖）。"""
    k, g = payload["accident_type"], payload["group"]
    roots = payload["root_candidates"]
    L: List[str] = []
    L.append(f"【{k} / {g} 组 · 根因解释（离线规则后端）】")
    L.append("")

    # ---- 结论 ----
    L.append("一、结论：Top 根因与总效应")
    L.append(f"  本组入模变量 {payload['n_variables']} 个，稳定边 {payload['n_stable_edges']} 条，"
             f"bootstrap {payload['n_bootstrap']} 次（{payload['engine']} 单引擎）。")
    if roots:
        L.append(f"  按总因果效应排序的 Top-{payload['top_k']} 根因：")
        for c in roots[:payload["top_k"]]:
            L.append(
                f"    第{c['rank']}名 {c['variable']}（{c['subsystem']}，总效应 "
                f"{_f(c['total_effect'])}，入度 {c['in_degree']}，出度 {c['out_degree']}，"
                f"可达 {c['reach_size']}）")
        top1 = roots[0]
        L.append(f"  结论：模型稳定识别出的主导上游为 {top1['variable']}，"
                 f"其总效应 {_f(top1['total_effect'])} 高于第 "
                 f"{roots[payload['top_k'] - 1]['rank']} 名。")
    else:
        L.append("  未产出（本组无根因候选）。")
    L.append("")

    # ---- 证据链 ----
    L.append("二、证据链")
    L.append(f"  门控比例 {_pct(payload['gate_ratio'])}（本体 {payload['n_available_vars']} 个变量中"
             f"入模 {payload['n_variables']} 个）。")
    L.append(f"  B 截断阈值 |B|>{_f(payload['b_clip'])}，被截断边 {payload['n_clipped_edges']} 条；"
             f"截断前 cond(I−B)={_f(payload['cond_IminusB'])}，"
             f"截断后 {_f(payload['cond_IminusB_after_clip'])}。")
    if roots:
        L.append("  marker 直接效应（签名 cL，π 同号率 piL）：")
        for c in roots[:payload["top_k"]]:
            L.append(f"    {c['variable']}: cL={_f(c['auxiliary_cL'])}，"
                     f"piL={_f(c['auxiliary_piL'])}")
    L.append("  口径提醒：稳定边为单引擎 bootstrap 频次筛出，非多引擎共识证据。")
    L.append("")

    # ---- 跨组一致性 ----
    L.append("三、跨组一致性")
    cross = payload["cross_group"]
    if cross.get("available"):
        t1 = cross.get("top1_by_group") or {}
        L.append("  三组 Top-1：" + "，".join(f"{gname}={t1.get(gname)}" for gname in t1) + "。")
        L.append(f"  Top-1 是否跨组稳定：{'是' if cross.get('top1_stable') else '否'}；"
                 f"三组共同入榜变量 {cross.get('n_vars_in_all_groups')} 个；"
                 f"相邻组平均 Top-5 重叠 {_f(cross.get('mean_pairwise_overlap'))}。")
        for p in cross.get("pairwise") or []:
            pair = p.get("pair") or ["", ""]
            L.append(f"  组对 {pair[0]}→{pair[1]}：重叠 {p.get('overlap')} 个，"
                     f"Jaccard {_f(p.get('jaccard'))}。")
        d = payload["cross_group_diff"]
        if d.get("available"):
            L.append(f"  差异报告：Top-1 切换 {d['n_top1_switches']} 次，"
                     f"机理切换点 {d['n_mechanism_switches']} 个，"
                     f"边数差异超阈值组对 {d['n_edge_count_diffs']} 个。")
        else:
            L.append(f"  差异报告未产出（原因：{d.get('reason')}）。")
    else:
        L.append(f"  未产出（原因：{cross.get('reason')}）。")
    L.append("")

    # ---- K2 ----
    L.append("四、物理一致性（K2 三态）")
    k2 = payload["k2"]
    if k2.get("available"):
        L.append(f"  适用关系 {k2['n_relations']} 条：违反 {k2['n_violation']}、"
                 f"未违反 {k2['n_not_violated']}、不可判定 {k2['n_undecidable']}"
                 f"（不可判定比例 {_pct(k2['undecidable_ratio'])}）。")
        for r in k2.get("relations") or []:
            L.append(f"    {r['relation_id']}: {r['status']}")
        L.append(f"  来源 {k2['source']}；{k2.get('note')}")
    else:
        L.append(f"  未产出（原因：{k2.get('reason')}）。")
    L.append("")

    # ---- K3 ----
    L.append("五、故障模板覆盖（K3）")
    k3 = payload["k3"]
    if k3.get("available"):
        L.append(f"  模板 {k}（verification={k3.get('verification')}）："
                 f"expected_chain {k3['n_chain_edges']} 条相邻边中命中 {k3['n_matched']} 条，"
                 f"覆盖率 {_pct(k3['coverage'])}。")
        L.append(f"  稳定边中与模板方向相反 {k3['n_reverse_edges']} 条。")
        if k3.get("reverse_edges"):
            L.append("  反向边清单：" + "、".join(_fmt_edge(e) for e in k3["reverse_edges"]))
        if k3.get("missing_edges"):
            L.append("  模板链缺失：" + "、".join(_fmt_edge(e) for e in k3["missing_edges"]))
    else:
        L.append(f"  未产出（原因：{k3.get('reason')}）。")
    L.append("")

    # ---- 偏离度 ----
    L.append("六、偏离度提示")
    dev = payload["deviation"]
    if dev.get("available"):
        lc = dev.get("level_counts") or {}
        L.append(f"  变量 {dev.get('n_variables')} 个的 4 级偏离计数："
                 f"正常 {lc.get('normal')}、中等 {lc.get('moderate')}、"
                 f"较大 {lc.get('large')}、严重 {lc.get('severe')}。")
        L.append(f"  来源 {dev.get('source')}；{dev.get('note')}（偏离度只作辅助层，不改排序）。")
    else:
        L.append(f"  未产出（原因：{dev.get('reason')}）。")
    L.append("")

    # ---- 局限 ----
    L.append("七、局限")
    for item in payload.get("limitations") or []:
        L.append(f"  - {item}")
    L.append("")
    L.append("（本解释由离线规则后端依据 v2.3 产物生成，未调用任何大模型；"
             "所有数字与变量名均来自同 run_id 的 JSON 证据。）")
    return "\n".join(L)


# ==================== llm 后端（可选，OpenAI 兼容） ====================
def llm_unavailable_reason() -> Optional[str]:
    """llm 后端启用条件：BASE_URL 与 API_KEY 同时存在；否则返回原因文本。"""
    missing = [n for n in (V23_LLM_BASE_URL_ENV, V23_LLM_API_KEY_ENV)
               if not os.environ.get(n, "").strip()]
    if missing:
        return f"未设置环境变量 {'/'.join(missing)}，llm 后端不可用"
    return None


def _build_v23_prompt(payload: Dict) -> str:
    return (
        "以下为 CausalTrace v2.3 的单模板证据 JSON（只读产物，未重算因果）。"
        "请生成中文结构化根因解释，章节：结论与 Top 根因 / 证据链 / 跨组一致性 / "
        "物理一致性 K2 / 故障模板 K3 / 偏离度提示 / 局限。\n"
        "只允许引用下面 JSON 中出现的数字与变量名。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _call_chat_completions(prompt: str, timeout: Optional[float] = None) -> str:
    """OpenAI 兼容 chat/completions（标准库 urllib，超时默认 30s，失败抛异常）。"""
    base = os.environ.get(V23_LLM_BASE_URL_ENV, "").strip().rstrip("/")
    key = os.environ.get(V23_LLM_API_KEY_ENV, "").strip()
    if not base or not key:
        raise RuntimeError("llm 后端环境变量缺失")
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    body = json.dumps({
        "model": os.environ.get(V23_LLM_MODEL_ENV, V23_LLM_MODEL_DEFAULT),
        "messages": [
            {"role": "system", "content": V23_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    t = V23_LLM_TIMEOUT_S if timeout is None else timeout
    with urllib.request.urlopen(req, timeout=t) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def generate_explanation(payload: Dict, backend: str = "rule",
                         timeout: Optional[float] = None) -> Dict:
    """生成解释：rule（默认离线）/ llm（可选，失败自动降级并写 degraded_reason）。"""
    requested = backend
    if backend == "llm":
        why = llm_unavailable_reason()
        if why:
            text = render_rule_explanation(payload)
            faith = verify_faithfulness(text, payload)
            logger.warning(f"[D6] llm 后端不可用（{why}），降级 rule")
            return {"backend": "rule", "requested_backend": requested,
                    "degraded": True, "degraded_reason": why,
                    "text": text, "faithfulness": faith}
        try:
            text = _call_chat_completions(_build_v23_prompt(payload), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - 任何失败都必须显式降级
            text = render_rule_explanation(payload)
            faith = verify_faithfulness(text, payload)
            reason = f"llm 调用失败并降级 rule：{type(exc).__name__}: {exc}"
            logger.warning(f"[D6] {reason}")
            return {"backend": "rule", "requested_backend": requested,
                    "degraded": True, "degraded_reason": reason,
                    "text": text, "faithfulness": faith}
        faith = verify_faithfulness(text, payload)
        if not faith["faithful"]:
            logger.warning(f"[D6] llm 输出不忠实：{faith['n_numbers_unsupported']} 个未支撑数字，"
                           f"{faith['n_vars_unknown']} 个未知变量名（原样保留并标记）")
        return {"backend": "llm", "requested_backend": requested,
                "degraded": False, "degraded_reason": None,
                "text": text, "faithfulness": faith}

    text = render_rule_explanation(payload)
    faith = verify_faithfulness(text, payload)
    if not faith["faithful"]:
        raise RuntimeError(f"rule 后端解释未通过忠实性校验（实现缺陷）：{faith}")
    return {"backend": "rule", "requested_backend": requested,
            "degraded": False, "degraded_reason": None,
            "text": text, "faithfulness": faith}


# ==================== 自检（不真实调 LLM） ====================
if __name__ == "__main__":
    payload = build_payload("LOCA", "mid")
    res = generate_explanation(payload, backend="rule")
    print(res["text"])
    print("=" * 70)
    print("backend=", res["backend"], "faithful=", res["faithfulness"]["faithful"])
    print("llm 可用性：", llm_unavailable_reason() or "环境变量齐全")

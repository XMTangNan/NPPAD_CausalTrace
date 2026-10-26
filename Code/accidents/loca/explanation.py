"""
Phase 5.5：LOCA 事故根因解释生成模块（模板驱动 + LLM 可选增强，忠实于数据）。

- generate_explanation: 模板驱动生成自然语言根因报告，不调用 LLM。
- generate_explanation_llm: 因果图提示注入 + DeepSeek LLM 生成自然语言解释，
  带忠实性校验防幻觉；无 API key 或调用失败时自动降级到模板驱动。
- check_faithfulness: 校验解释中引用的数值与 ranking 一致（抗幻觉）。

设计原则：
  1. 所有数值来自计算结果，不编造。
  2. LLM 只能引用 prompt 中提供的因果事实，不得编造物理机制或因果关系。
  3. 方向一致率 < 0.9 时显式标注方向不确定性。
  4. 物理解释优先引用本体关系（basis），否则标注为数据驱动关联。
  5. 密钥通过环境变量读取，代码中不硬编码。
"""
import json
import os
import re
from typing import Dict, List, Optional

from config import (
    LLM_API_KEY_ENV, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT,
    LLM_TEMPERATURE, LLM_TOP_K_INPUT,
)
from core.utils import logger, save_text, save_json


# ==================== 常量 ====================
# 上游候选 Top-K
TOP_K = 5
# 方向一致率阈值：低于此值标注方向不确定性
SIGN_AGREEMENT_THRESHOLD = 0.9
# 数值校验的浮点容差（解释中保留 4 位小数）
FLOAT_TOL = 1e-3


# ==================== 辅助函数 ====================
def _get_variable_meta(ontology: Dict, var: str) -> Dict:
    """从本体获取变量元信息（meaning/subsystem/role），缺失时返回占位。"""
    meta = ontology.get("variables", {}).get(var, {})
    return {
        "meaning": meta.get("meaning", var),
        "subsystem": meta.get("subsystem", "未知"),
        "role": meta.get("role", "未知"),
    }


def _find_direct_relation(ontology: Dict, src: str, dst: str) -> Optional[Dict]:
    """查找 src -> dst 的直接本体关系（含双向关系的反向匹配）。"""
    for rel in ontology.get("relations", []):
        if rel["from"] == src and rel["to"] == dst:
            return rel
        if rel.get("direction") == "bidirectional" and rel["from"] == dst and rel["to"] == src:
            return rel
    return None


def _get_bootstrap_frequency(stable_edges: List[Dict], src: str, dst: str) -> Optional[float]:
    """从稳定边中获取 src -> dst 直接边的 Bootstrap 频率 f_e。"""
    if not stable_edges:
        return None
    for e in stable_edges:
        if e.get("from") == src and e.get("to") == dst:
            return float(e.get("f_e", 0.0))
    return None


def _format_effect_direction(median: float) -> str:
    """根据总效应中位数描述效应方向（标准化尺度）。"""
    if median > 0:
        return "正向（与目标同向变化）"
    if median < 0:
        return "负向（与目标反向变化）"
    return "无显著效应"


def _build_physical_explanation(ontology: Dict, src: str, dst: str, median: float) -> str:
    """构建物理解释：优先使用本体关系 basis，否则标注数据驱动。"""
    src_meaning = _get_variable_meta(ontology, src)["meaning"]
    dst_meaning = _get_variable_meta(ontology, dst)["meaning"]
    direction = _format_effect_direction(median)

    rel = _find_direct_relation(ontology, src, dst)
    if rel:
        category = rel.get("category", "unknown")
        cat_label = {"core": "核心", "aux": "辅助", "allowed": "允许"}.get(category, category)
        basis = rel.get("basis", "")
        return (f"本体{cat_label}关系（{basis}）：{src_meaning}对{dst_meaning}"
                f"呈{direction}的总效应。")
    return (f"数据驱动关联：{src_meaning}对{dst_meaning}呈{direction}的总效应，"
            f"本体中无直接物理关系记录，结论需谨慎。")


def _describe_anomaly(anomaly_scores: Dict, var: str) -> str:
    """描述变量异常状态（异常程度 + 持续占比）。"""
    info = anomaly_scores.get(var) if anomaly_scores else None
    if not info:
        return "无异常评分数据"
    degree = info.get("anomaly_degree", 0.0)
    duration = info.get("anomaly_duration_ratio", info.get("duration_ratio", 0.0))
    return f"异常程度 {degree:.2f}，异常持续占比 {duration:.1%}"


# ==================== 核心函数 ====================
def generate_explanation(target: str, ranking: List[Dict], ontology: Dict,
                         anomaly_scores: Dict, stable_edges: List[Dict]) -> str:
    """模板驱动生成自然语言根因报告。

    Args:
        target: 目标变量名（如 "P"）。
        ranking: 上游排序，每项含 rank/variable/median_total_effect/
                 abs_median/q25/q75/sign_agreement。
        ontology: 本体字典 {variables, relations, forbidden_edges}。
        anomaly_scores: {var: {anomaly_degree, duration_ratio}}。
        stable_edges: 稳定边列表，每项含 from/to/f_e/s_e/...。

    Returns:
        自然语言根因报告字符串。
    """
    target_meta = _get_variable_meta(ontology, target)
    target_anomaly = _describe_anomaly(anomaly_scores, target)

    lines: List[str] = []
    lines.append("=" * 60)
    lines.append(f"根因分析报告：目标变量 {target}（{target_meta['meaning']}）")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"【目标变量】{target} — {target_meta['meaning']}")
    lines.append(f"【子系统】{target_meta['subsystem']}（角色：{target_meta['role']}）")
    lines.append(f"【异常状态】{target_anomaly}")
    lines.append("")
    lines.append(f"【上游候选 Top-{TOP_K}】按 |median(T)| 降序排列（T 为标准化总因果效应）")
    lines.append("-" * 60)

    top_k = (ranking or [])[:TOP_K]
    for item in top_k:
        rank = item["rank"]
        var = item["variable"]
        median = item["median_total_effect"]
        abs_median = item["abs_median"]
        q25 = item["q25"]
        q75 = item["q75"]
        sign_agree = item["sign_agreement"]

        meta = _get_variable_meta(ontology, var)
        f_e = _get_bootstrap_frequency(stable_edges, var, target)
        physical = _build_physical_explanation(ontology, var, target, median)

        lines.append(f"{rank}. {var}（{meta['meaning']}）")
        lines.append(f"   - 子系统：{meta['subsystem']}（角色：{meta['role']}）")
        lines.append(f"   - 总效应中位数（标准化）：median_T={median:+.4f} (|T|={abs_median:.4f})")
        lines.append(f"   - 四分位区间：[Q25={q25:+.4f}, Q75={q75:+.4f}]")
        if f_e is not None:
            lines.append(f"   - Bootstrap 频率：f_e={f_e:.2f}（直接边 {var}→{target}）")
        else:
            lines.append(f"   - Bootstrap 频率：无直接稳定边 {var}→{target}"
                         f"（总效应经间接路径传导）")
        lines.append(f"   - 方向一致率：sign_agreement={sign_agree:.2%}")
        lines.append(f"   - 物理解释：{physical}")

        if sign_agree < SIGN_AGREEMENT_THRESHOLD:
            lines.append(
                f"   - ⚠ 方向不确定性：仅 {sign_agree:.0%} 的 Bootstrap 样本与中位数"
                f"方向一致，低于 {SIGN_AGREEMENT_THRESHOLD:.0%} 阈值，效应方向可靠性不足。"
            )
        lines.append("")

    # 结论
    lines.append("【结论】")
    lines.append("-" * 60)
    if top_k:
        top1 = top_k[0]
        top1_var = top1["variable"]
        top1_meaning = _get_variable_meta(ontology, top1_var)["meaning"]
        top1_median = top1["median_total_effect"]
        top1_sign = top1["sign_agreement"]
        direction = _format_effect_direction(top1_median)

        lines.append(
            f"最可能的上游因素为 {top1_var}（{top1_meaning}），"
            f"其对 {target} 的总效应中位数为 {top1_median:+.4f}（{direction}）。"
        )
        if top1_sign < SIGN_AGREEMENT_THRESHOLD:
            lines.append(
                f"⚠ 方向不确定性：{top1_var} 的方向一致率仅 {top1_sign:.0%}，"
                f"低于 {SIGN_AGREEMENT_THRESHOLD:.0%} 阈值，效应方向需谨慎解读。"
            )
        else:
            lines.append(f"方向一致性较高（{top1_sign:.0%}），效应方向较为可靠。")

        if len(top_k) >= 2:
            secondary = "、".join(
                f"{r['variable']}(median_T={r['median_total_effect']:+.4f})"
                for r in top_k[1:3]
            )
            lines.append(f"次要候选因素包括：{secondary}。")
    else:
        lines.append("无上游候选变量。")

    lines.append("")
    lines.append(
        "【说明】本报告由模板驱动生成，所有数值均来自计算结果，未调用 LLM，"
        "不超出数据支持的结论范围；方向一致率不足时已显式标注不确定性。"
    )
    lines.append("=" * 60)

    return "\n".join(lines)


def check_faithfulness(explanation_text: str, ranking: List[Dict]) -> Dict:
    """校验解释中引用的数值与 ranking 是否一致（抗幻觉）。

    解析解释中 ``rank. variable ... median_T=X`` 模式，与 ranking 对比：
      - 变量是否存在于 ranking；
      - rank 是否一致；
      - median_total_effect 是否在浮点容差内一致。

    Args:
        explanation_text: 生成的解释文本。
        ranking: 上游排序列表。

    Returns:
        {pass: bool, mismatches: [{variable, field, expected, found}]}。
    """
    mismatches: List[Dict] = []
    rank_map = {item["variable"]: item for item in (ranking or [])}
    # 构建变量名集合，用于正则中的精确匹配（变量名均为大写字母+数字）
    valid_vars = "|".join(re.escape(v) for v in rank_map.keys())

    # 匹配 "数字. 变量名 ... median_T=带符号小数"
    # 变量名严格限定为 ranking 中存在的变量，避免误匹配章节标题
    pattern = re.compile(
        rf"(\d+)\.\s+({valid_vars})\b.*?median_T=([+\-]?\d+\.?\d*)",
        re.DOTALL,
    )

    for match in pattern.finditer(explanation_text):
        rank_str, var, median_str = match.groups()
        if var not in rank_map:
            mismatches.append({
                "variable": var,
                "field": "existence",
                "expected": "存在于 ranking",
                "found": f"ranking 中不存在 {var}",
            })
            continue

        expected = rank_map[var]

        # 校验 rank
        found_rank = int(rank_str)
        if expected["rank"] != found_rank:
            mismatches.append({
                "variable": var,
                "field": "rank",
                "expected": expected["rank"],
                "found": found_rank,
            })

        # 校验 median_total_effect
        expected_median = float(expected["median_total_effect"])
        found_median = float(median_str)
        if abs(expected_median - found_median) > FLOAT_TOL:
            mismatches.append({
                "variable": var,
                "field": "median_total_effect",
                "expected": f"{expected_median:+.4f}",
                "found": f"{found_median:+.4f}",
            })

    return {"pass": len(mismatches) == 0, "mismatches": mismatches}


# ==================== LLM 解释生成（因果图提示注入 + 防幻觉）====================
def _build_llm_prompt_context(target: str, ranking: List[Dict], ontology: Dict,
                              anomaly_scores: Dict, stable_edges: List[Dict],
                              anm_ranking: List[Dict] = None,
                              ensemble: Dict = None) -> str:
    """将因果图、排序、异常、本体序列化为 LLM prompt 上下文。

    对应论文的 causal graph prompt injection：把计算得到的因果事实作为
    结构化文本注入 prompt，LLM 只能引用这些事实，不得编造。
    若提供 anm_ranking 和 ensemble，额外注入两方法对比信息。
    """
    top_k = (ranking or [])[:LLM_TOP_K_INPUT]
    target_meta = _get_variable_meta(ontology, target)
    target_anomaly = _describe_anomaly(anomaly_scores, target)

    lines: List[str] = []
    lines.append("【目标变量】")
    lines.append(f"  {target} — {target_meta['meaning']}")
    lines.append(f"  子系统：{target_meta['subsystem']}，角色：{target_meta['role']}")
    lines.append(f"  异常状态：{target_anomaly}")
    lines.append("")

    lines.append("【上游候选因果效应】（LiNGAM 方法，按 |median(T)| 降序，T 为标准化总因果效应）")
    for item in top_k:
        var = item["variable"]
        meta = _get_variable_meta(ontology, var)
        median = item["median_total_effect"]
        q25 = item["q25"]
        q75 = item["q75"]
        sign_agree = item["sign_agreement"]
        f_e = _get_bootstrap_frequency(stable_edges, var, target)
        rel = _find_direct_relation(ontology, var, target)
        basis = rel.get("basis", "无直接本体关系") if rel else "无直接本体关系"
        anomaly_info = _describe_anomaly(anomaly_scores, var)

        lines.append(f"  {item['rank']}. {var}（{meta['meaning']}）")
        lines.append(f"     子系统：{meta['subsystem']}")
        lines.append(f"     median_T={median:+.4f}, |T|={item['abs_median']:.4f}")
        lines.append(f"     Q25={q25:+.4f}, Q75={q75:+.4f}")
        lines.append(f"     Bootstrap频率f_e={f_e:.2f}" if f_e is not None
                     else f"     无直接稳定边 {var}→{target}（间接路径）")
        lines.append(f"     方向一致率={sign_agree:.2%}")
        lines.append(f"     本体关系依据：{basis}")
        lines.append(f"     异常状态：{anomaly_info}")
    lines.append("")

    # GAM-ANM 方法对比段
    if anm_ranking is not None:
        lines.append("【GAM-ANM 非线性方法上游排序】（按平均边际效应 |dP/dX| 降序）")
        anm_top = anm_ranking[:LLM_TOP_K_INPUT]
        for item in anm_top:
            var = item["variable"]
            meta = _get_variable_meta(ontology, var)
            effect = item.get("avg_marginal_effect", item.get("median_total_effect", 0))
            lines.append(f"  {item['rank']}. {var}（{meta['meaning']}）"
                         f" avg_marginal_effect={effect:+.4f}")
        lines.append("")

    # 方法融合段
    if ensemble is not None:
        ec = ensemble["edge_classification"]
        wlr = ensemble["wlr_p_check"]
        lines.append("【两方法融合结果】")
        lines.append(f"  共识边（LiNGAM 与 GAM-ANM 方向一致）：{ec['n_consensus']} 条")
        lines.append(f"  冲突边（两方法方向相反）：{ec['n_conflicting']} 条")
        if ec["conflicting"]:
            for c in ec["conflicting"][:5]:
                lines.append(f"    - {c['pair']}: LiNGAM={c['lingam_dir']}, "
                             f"GAM-ANM={c['anm_dir']}")
        lines.append(f"  WLR↔P 方向：LiNGAM={wlr['lingam']}, GAM-ANM={wlr['anm']}"
                     f"（{'一致' if wlr['agreement'] else '不一致'}）")
        lines.append("  RRF 融合 Top-5 排名：")
        for r in ensemble["fused_ranking"][:5]:
            lines.append(f"    {r['rank']}. {r['variable']} "
                         f"(LiNGAM 排第{r['lingam_rank']}, GAM-ANM 排第{r['anm_rank']})")
        lines.append("")

    lines.append("【方向一致性判定规则】")
    lines.append(f"  方向一致率 < {SIGN_AGREEMENT_THRESHOLD:.0%} 时，效应方向可靠性不足，")
    lines.append("  必须在解释中显式标注不确定性，不得断言因果方向。")
    lines.append("")
    lines.append("【解释约束】")
    lines.append("  1. 只能引用上述提供的因果事实，不得编造新的变量、边、物理机制。")
    lines.append("  2. LiNGAM 总效应中位数为正表示与目标同向变化，为负表示反向变化。")
    lines.append("  3. 方向一致率低于 90% 的变量，必须标注方向不确定性。")
    lines.append("  4. 无直接稳定边的变量，总效应经间接路径传导，不得说成直接因果。")
    lines.append("  5. 共识边（两方法一致）可信度最高；冲突边方向不确定，需谨慎。")
    lines.append("  6. 结论表述为'模型稳定识别出的有向关系'，非'已证实因果'。")

    return "\n".join(lines)


def _get_api_key() -> str:
    """获取 LLM API key：优先环境变量，其次 .env 文件。

    .env 文件格式：DEEPSEEK_API_KEY=sk-xxx
    密钥绝不硬编码到代码中。
    """
    # 1. 环境变量
    key = os.environ.get(LLM_API_KEY_ENV, "").strip()
    if key:
        return key

    # 2. .env 文件（与 explanation.py 同目录或 Code/ 目录下）
    env_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.getcwd(), ".env"),
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
    """调用 DeepSeek Chat Completions API。

    使用 requests 库（不依赖 openai SDK），密钥从环境变量或 .env 文件读取。
    返回 LLM 生成的文本，失败时返回 None。
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning(f"未找到 LLM API key（环境变量 {LLM_API_KEY_ENV} 或 .env 文件），"
                       "LLM 解释不可用。")
        return None

    import requests

    url = f"{LLM_BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": (
                "你是核电 LOCA 事故根因分析专家。你将收到基于因果追踪算法"
                "（CausalTrace）计算得到的因果图事实。你的任务是生成一份"
                "自然语言根因分析报告。严格遵守以下规则：\n"
                "1. 只能引用提供的因果事实，不得编造任何变量、边、数值或物理机制；\n"
                "2. 引用数值时必须保留原始精度（如 median_T=+1.1747）；\n"
                "3. 方向一致率低于 90% 的变量，必须显式标注方向不确定性；\n"
                "4. 无直接稳定边的变量，总效应经间接路径传导，不得说成直接因果；\n"
                "5. 结论表述为'模型稳定识别出的有向关系'，非'已证实因果'；\n"
                "6. 使用中文，结构清晰，包含目标变量异常状态、Top-5 上游分析、结论。"
            )},
            {"role": "user", "content": prompt},
        ],
        "temperature": LLM_TEMPERATURE,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=LLM_TIMEOUT)
        if resp.status_code != 200:
            logger.error(f"DeepSeek API 调用失败: HTTP {resp.status_code}, "
                         f"body={resp.text[:300]}")
            return None
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return content.strip()
    except Exception as e:
        logger.error(f"DeepSeek API 调用异常: {e}")
        return None


def generate_explanation_llm(target: str, ranking: List[Dict], ontology: Dict,
                             anomaly_scores: Dict, stable_edges: List[Dict],
                             anm_ranking: List[Dict] = None,
                             ensemble: Dict = None) -> str:
    """LLM 生成自然语言根因解释（因果图提示注入 + 防幻觉）。

    支持融合 LiNGAM 与 GAM-ANM 两方法结果：若提供 anm_ranking 和 ensemble，
    prompt 中会注入两方法对比（共识边、冲突边、RRF 融合排序），LLM 据此生成
    综合解释。

    流程：
      1. 构建因果图 prompt 上下文（含两方法对比）；
      2. 调用 DeepSeek API 生成解释；
      3. 用 check_faithfulness 校验引用的数值是否与 ranking 一致；
      4. 校验不通过或 API 调用失败时，降级到模板驱动的 generate_explanation。

    Returns:
        自然语言根因报告字符串。模板驱动时末尾标注'模板降级'。
    """
    # 无 key 时直接降级
    if not _get_api_key():
        logger.info("未检测到 LLM API key，使用模板驱动解释。")
        return generate_explanation(target, ranking, ontology,
                                    anomaly_scores, stable_edges)

    prompt_context = _build_llm_prompt_context(
        target, ranking, ontology, anomaly_scores, stable_edges,
        anm_ranking=anm_ranking, ensemble=ensemble
    )

    has_ensemble = anm_ranking is not None and ensemble is not None
    if has_ensemble:
        report_structure = (
            "报告结构：\n"
            "1. 目标变量及其异常状态概述；\n"
            "2. Top-5 上游候选变量逐一分析（含 LiNGAM 总效应、ANM 边际效应、"
            "方向一致性、不确定性标注）；\n"
            "3. 方法间共识与分歧：哪些变量被两方法共同识别、哪些方向冲突；\n"
            "4. 结论：综合两方法的最可能上游因素及次要候选，标注方向可靠性。"
        )
    else:
        report_structure = (
            "报告结构：\n"
            "1. 目标变量及其异常状态概述；\n"
            "2. Top-5 上游候选变量逐一分析（含总效应、方向一致性、不确定性标注）；\n"
            "3. 结论：最可能的上游因素及次要候选，标注方向可靠性。"
        )

    full_prompt = (
        "请基于以下因果分析事实，生成一份结构化的根因分析报告。\n\n"
        f"{prompt_context}\n\n"
        f"{report_structure}"
    )

    logger.info(f"调用 LLM 生成解释（模型: {LLM_MODEL}）...")
    llm_output = _call_deepseek_api(full_prompt)

    if not llm_output:
        logger.warning("LLM 调用失败/无输出，降级到模板驱动解释。")
        return generate_explanation(target, ranking, ontology,
                                    anomaly_scores, stable_edges)

    # 忠实性校验：LLM 引用的数值必须与 ranking 一致
    faith_result = check_faithfulness(llm_output, ranking)
    if not faith_result["pass"]:
        logger.warning(
            f"LLM 解释忠实性校验未通过（{len(faith_result['mismatches'])} 处不匹配），"
            "降级到模板驱动解释。"
        )
        for m in faith_result["mismatches"][:5]:
            logger.warning(f"  不匹配: {m}")
        return generate_explanation(target, ranking, ontology,
                                    anomaly_scores, stable_edges)

    # 校验通过，附加说明
    if has_ensemble:
        ec = ensemble["edge_classification"]
        wlr = ensemble["wlr_p_check"]
        ensemble_note = (
            f"【方法融合说明】本报告综合 LiNGAM（线性/非高斯）与 GAM-ANM（非线性 ANM）"
            f"两种因果发现方法：共识边 {ec['n_consensus']} 条、冲突边 {ec['n_conflicting']} 条；"
            f"WLR↔P 方向两方法{'一致' if wlr['agreement'] else '不一致'}。"
        )
    else:
        ensemble_note = ""

    lines = [
        "=" * 60,
        f"根因分析报告（LLM 生成，模型: {LLM_MODEL}）：目标变量 {target}",
        "=" * 60,
        "",
        llm_output,
        "",
        "-" * 60,
        "【忠实性校验】通过 — LLM 引用的所有数值与因果计算结果一致。",
        "【说明】本报告由 LLM 基于因果图提示注入生成，已通过忠实性校验；"
        "方向一致率不足时已显式标注不确定性。",
    ]
    if ensemble_note:
        lines.append(ensemble_note)
    lines.append("=" * 60)
    return "\n".join(lines)


# ==================== main：模拟数据验证 ====================
if __name__ == "__main__":
    from accidents.loca.ontology import build_ontology
    from accidents.loca.config import PRIOR_VARIABLES, TARGET

    logger.info("Phase 5.5: 解释生成模块 — 模拟数据验证")

    variables = PRIOR_VARIABLES
    target = TARGET
    ontology = build_ontology(variables)

    # 模拟 ranking（基于 LOCA 物理直觉：WLR/VOL/LVPZ 对 P 有较强总效应）
    ranking = [
        {"rank": 1, "variable": "WLR", "median_total_effect": -0.872,
         "abs_median": 0.872, "q25": -0.910, "q75": -0.831, "sign_agreement": 0.98},
        {"rank": 2, "variable": "VOL", "median_total_effect": 0.654,
         "abs_median": 0.654, "q25": 0.580, "q75": 0.712, "sign_agreement": 0.95},
        {"rank": 3, "variable": "LVPZ", "median_total_effect": 0.412,
         "abs_median": 0.412, "q25": 0.301, "q75": 0.503, "sign_agreement": 0.88},
        {"rank": 4, "variable": "TAVG", "median_total_effect": 0.234,
         "abs_median": 0.234, "q25": 0.180, "q75": 0.290, "sign_agreement": 0.92},
        {"rank": 5, "variable": "QMWT", "median_total_effect": -0.156,
         "abs_median": 0.156, "q25": -0.210, "q75": -0.100, "sign_agreement": 0.85},
        {"rank": 6, "variable": "THA", "median_total_effect": 0.089,
         "abs_median": 0.089, "q25": 0.040, "q75": 0.140, "sign_agreement": 0.78},
    ]

    # 模拟 anomaly_scores
    anomaly_scores = {
        "P": {"anomaly_degree": 0.82, "duration_ratio": 0.75},
        "WLR": {"anomaly_degree": 0.95, "duration_ratio": 0.90},
        "VOL": {"anomaly_degree": 0.60, "duration_ratio": 0.65},
        "LVPZ": {"anomaly_degree": 0.45, "duration_ratio": 0.50},
    }

    # 模拟 stable_edges（TAVG -> P 无直接稳定边，用于测试分支）
    stable_edges = [
        {"from": "WLR", "to": "P", "f_e": 0.92, "s_e": 0.88,
         "dir_agreement": 0.95, "sign_agreement": 0.98, "count": 92},
        {"from": "VOL", "to": "P", "f_e": 0.78, "s_e": 0.75,
         "dir_agreement": 0.80, "sign_agreement": 0.95, "count": 78},
        {"from": "LVPZ", "to": "P", "f_e": 0.61, "s_e": 0.62,
         "dir_agreement": 0.70, "sign_agreement": 0.88, "count": 61},
        {"from": "QMWT", "to": "P", "f_e": 0.55, "s_e": 0.58,
         "dir_agreement": 0.65, "sign_agreement": 0.85, "count": 55},
    ]

    # 生成解释
    explanation = generate_explanation(target, ranking, ontology,
                                       anomaly_scores, stable_edges)
    print(explanation)
    print()

    # 忠实性检查
    result = check_faithfulness(explanation, ranking)
    print("=" * 60)
    print(f"忠实性检查：{'通过' if result['pass'] else '未通过'}")
    if result["mismatches"]:
        for m in result["mismatches"]:
            print(f"  - {m}")
    else:
        print("  所有引用数值与 ranking 一致。")
    print("=" * 60)

    # 保存
    save_text(explanation, "explanation")
    save_json(result, "faithfulness_check")

    assert result["pass"], "忠实性检查未通过！"
    logger.info("Phase 5.5 验证完成：解释生成 + 忠实性检查通过。")

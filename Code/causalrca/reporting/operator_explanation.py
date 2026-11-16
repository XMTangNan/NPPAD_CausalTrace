"""V23-DESIGN-118：操作者版解释（知识与判定彻底分离；离线附加产物）。

与既有的 `llm_explanation_{K}_{g}.json` 并行，**不改动、不覆盖**后者，也不参与排序：

    reports/llm_explanation_operator_{K}_{g}.json

口径（2026-11-15 用户拍板，以三份已认可样例为准）
--------------------------------------------------
1. **判定只用数据侧三样**：
   ① 首动时刻（相对图上全部上游变量的越限先后，见 `evidence/temporal.py`）；
   ② 图结构位置（入度/出度、是否纯源点）；
   ③ 偏离幅度与形态（偏差档位、是否阶跃）。
   档位四档：`最早异常` / `同期异常` / `滞后异常` / `未评估`。
   - 最早异常：纯源点且有阶跃；或比图上全部上游**早至少一个采样间隔**且有阶跃；
   - 同期异常：与最早上游差在一个采样间隔以内；早于上游但不足一个间隔按同期处理（取舍）；
   - 滞后异常：晚于最早上游超过一个采样间隔；
   - 未评估：首动算不出（tolerance 未标定 / σ 不可用 / 分析窗内无越阈 / 上游均未评估）。

2. **知识不参与判定**：
   - LLM 输入**不含**知识条目字段（`can_be_root` / `assertion` / `sourced` / 条目 id）；
   - 校验器禁止在档位、依据、指向字段里出现“知识/条目/知识库”或条目 id；
   - 知识只出现在三处描述性位置：`这是什么`（名称/单位/子系统）、
     `事故大概怎么传过来`（K3 机理背景）、`物理背景（仅描述，不参与判定）`。

3. **结论必须指向“该往哪看”**：
   - 数据侧指向 = 不在 Top-5 的纯源点或 Top-5 的图上上游，按首动时刻、纯源点、阶跃占比、
     出度排序；
   - 指明它当前状态（是否在池、出池原因、`rank_if_kept`），措辞是“数据侧指向”，
     不得写成“系统推荐”；
   - 找不到候选时写“无法定位”，不许编。

4. **排序不动**：`ranking_modified=false`；本模块只读既有产物与原始 CSV。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from causalrca.config.constants_v23 import (
    LLM_MODEL_PIN, LLM_TEMPERATURE_PIN, LLM_TIMEOUT_PIN_S,
)
from causalrca.evidence import external_knowledge as EK
from causalrca.evidence import temporal as T
from causalrca.io import artifact
from causalrca.io.logging import logger
from causalrca.reporting import llm_explanation as LE

SCHEMA_OPERATOR = "causaltrace/v2.3/operator-explanation/v1"
ARTIFACT_NAME = "llm_explanation_operator_{k}_{g}"

TIER_EARLIEST = "最早异常"
TIER_SYNC = "同期异常"
TIER_LAG = "滞后异常"
TIER_UNASSESSED = "未评估"
TIERS: Tuple[str, ...] = (TIER_EARLIEST, TIER_SYNC, TIER_LAG, TIER_UNASSESSED)

GROUP_CN = {"low": "低严重度组", "mid": "中严重度组", "high": "高严重度组"}
LEVEL_CN = {"normal": "正常", "moderate": "中等", "large": "较大", "severe": "严重"}
GATE_CN = {"pass": "通过（进入候选池）",
           "excluded_structural": "被安全动作变量规则排除",
           "fail": "未过公差门",
           "not_evaluable": "因注入前基线饱和、偏差不可评估而未进候选池"}


# ==================== 只读输入 ====================
def _read_json(subdir: str, name: str, run_id: str) -> Dict:
    path = artifact.subdir_path(subdir, run_id) / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"缺 {subdir}/{name}.json（run_id={run_id}）")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _r2(x) -> Optional[float]:
    return None if x is None else round(float(x), 2)


def _r3(x) -> Optional[float]:
    return None if x is None else round(float(x), 3)


def _var_meta(idx: Dict[str, Dict], v: str) -> Dict[str, Any]:
    rec = idx.get(v) or {}
    return {"meaning": rec.get("meaning"), "unit": rec.get("unit"),
            "subsystem": rec.get("subsystem")}


def classify_tier(candidate: Dict, indeg: int, outdeg: int, dt: float) -> Tuple[str, str]:
    """按数据侧三样定档，返回 `(档位, 一句话理由)`。

    `candidate` 是 `temporal.compute_temporal` 的单个 candidate 记录。
    """
    cm = candidate.get("candidate") or {}
    if cm.get("status") != T.STATUS_OK:
        return TIER_UNASSESSED, f"时序未评估（{cm.get('status_text') or cm.get('status')}）"

    ct = cm.get("t_first_median")
    step = cm.get("step_frac")
    has_step = step is not None and step >= T.STEP_FRAC_MIN
    ups = [u for u in (candidate.get("upstream") or [])
           if u.get("status") == T.STATUS_OK and u.get("t_first_median") is not None]

    if candidate.get("zero_upstream"):
        if has_step:
            return (TIER_EARLIEST,
                    f"图上无上游（纯源点，入度 {indeg}、出度 {outdeg}）；"
                    f"首动 {ct:.0f}s，阶跃占比 {step:.0%}")
        return (TIER_UNASSESSED,
                f"图上无上游但无阶跃（阶跃占比 {0.0 if step is None else step:.0%}），"
                f"无法按上下游先后定档")

    if not ups:
        return (TIER_UNASSESSED,
                f"上游 {candidate.get('n_upstream')} 个均未评估，无法比较先后")

    ut = min(u["t_first_median"] for u in ups)
    if ct <= ut - dt:
        if has_step:
            return (TIER_EARLIEST,
                    f"早于全部上游（最早上游 {ut:.0f}s）；首动 {ct:.0f}s，"
                    f"阶跃占比 {step:.0%}")
        return (TIER_SYNC,
                f"早于上游 {ut:.0f}s 但不足一个采样间隔或无阶跃"
                f"（阶跃占比 {0.0 if step is None else step:.0%}），按同期处理——这是取舍")
    if ct <= ut + dt:
        return (TIER_SYNC,
                f"与最早上游 {ut:.0f}s 相差不超过一个采样间隔（{dt:.0f}s）；"
                f"首动 {ct:.0f}s")
    return (TIER_LAG,
            f"晚于最早上游 {ut:.0f}s 约 {ct - ut:.0f}s；首动 {ct:.0f}s")


def _pool_status(v: str, allvars: Dict[str, Dict], excluded: Dict[str, Dict]) -> Dict:
    x = allvars.get(v) or {}
    ex = excluded.get(v) or {}
    st = {
        "in_pool": x.get("in_pool"),
        "gate_status": x.get("gate_status"),
        "gate_reason": x.get("gate_reason"),
        "rank": x.get("rank"),
        "rank_v2": x.get("rank_v2"),
        "rank_if_kept": ex.get("rank_if_kept"),
        "exclusion_class": ex.get("exclusion_class"),
    }
    if st["in_pool"] is False:
        st["why_not_top5"] = (f"未进候选池：gate_status={st['gate_status']}，"
                             f"gate_reason={st['gate_reason']}，"
                             f"rank_if_kept={st['rank_if_kept']}")
    elif st.get("rank") and st["rank"] > 5:
        st["why_not_top5"] = f"在候选池但原排序第 {st['rank']}，未进前五"
    else:
        st["why_not_top5"] = f"已在 Top-5（原排序第 {st.get('rank')}）"
    return st


def build_operator_input(accident_type: str, group: str,
                         run_id: Optional[str] = None) -> Dict:
    """组装 LLM 输入（只读数据侧证据，不含知识条目）。"""
    payload = LE.build_payload(accident_type, group, run_id=run_id)
    run_id = payload["run_id"]
    k, g = accident_type, group

    rc = _read_json("tracing", f"root_candidates_{k}_{g}", run_id)
    ca = _read_json("tracing", f"candidates_all_{k}_{g}", run_id)
    sig = _read_json("signatures", f"signature_{k}_{g}", run_id)
    gc = _read_json("reports", f"gate_compare_{k}_{g}", run_id)
    dev = _read_json("reports", f"deviation_{k}_{g}", run_id)
    tempor = T.compute_temporal(k, g, run_id=run_id)

    var_idx = (EK.load_variable_dictionary().get("variables") or {})
    levels = dev.get("levels") or {}
    variables = list(sig.get("variables") or [])
    edges = sig.get("stable_edges") or []
    indeg = {v: sum(1 for e in edges if e.get("to") == v) for v in variables}
    outdeg = {v: sum(1 for e in edges if e.get("from") == v) for v in variables}
    dt = float(tempor["sampling_interval"])

    allvars = {x["variable"]: x for x in (ca.get("variables") or [])}
    excluded = {x["variable"]: x for x in (ca.get("candidate_pool", {}).get("excluded") or [])}
    top5 = [c["variable"] for c in (rc.get("root_candidates") or [])]
    tmap = {x["variable"]: x for x in tempor["candidates"]}

    candidates: List[Dict] = []
    for c in (rc.get("root_candidates") or [])[:5]:
        v = c.get("variable")
        tv = dict(tmap.get(v) or {})
        tier, why = classify_tier(tv, indeg.get(v, 0), outdeg.get(v, 0), dt)
        tv["classification"] = tier
        tv["classification_reason"] = why
        ent = c.get("entity") or _var_meta(var_idx, v)
        inc = sorted([e for e in edges if e.get("to") == v],
                     key=lambda e: abs(e.get("weight_median") or 0.0), reverse=True)
        out = sorted([e for e in edges if e.get("from") == v],
                     key=lambda e: abs(e.get("weight_median") or 0.0), reverse=True)
        candidates.append({
            "rank": c.get("rank"),
            "rank_v2": c.get("rank_v2"),
            "variable": v,
            "name_cn": ent.get("meaning"),
            "unit": ent.get("unit"),
            "subsystem": ent.get("subsystem"),
            "effect_score": _r2(c.get("total_effect")),
            "gate": {"status": c.get("gate_status"), "reason": c.get("gate_reason"),
                     "in_pool": c.get("in_pool")},
            "deviation_level": levels.get(v),
            "deviation_note": "该通道自身相对本模板基线的变化档位；不可跨变量比较，不参与排序",
            "graph": {
                "n_incoming": indeg.get(v, 0),
                "n_outgoing": outdeg.get(v, 0),
                "incoming_top": [{"from": e.get("from"),
                                  "weight": _r3(e.get("weight_median"))}
                                 for e in inc[:5]],
                "outgoing_top": [{"to": e.get("to"),
                                  "weight": _r3(e.get("weight_median"))}
                                 for e in out[:5]],
            },
            "temporal": tv,
            "pool": _pool_status(v, allvars, excluded),
        })

    # 数据侧指向：不在 Top-5 的纯源点 / Top-5 的图上上游，按首动、纯源点、阶跃、出度排序
    upstream_of_top5 = {e.get("from") for e in edges if e.get("to") in top5}
    rows: List[Dict] = []
    for v in variables:
        m = tempor["variables"].get(v) or {}
        if m.get("status") != T.STATUS_OK:
            continue
        pure = indeg.get(v, 0) == 0 and outdeg.get(v, 0) > 0
        up = (v in upstream_of_top5) and (v not in top5)
        if not (pure or up):
            continue
        rows.append({
            "variable": v,
            "name_cn": (var_idx.get(v) or {}).get("meaning"),
            "t_first": m.get("t_first_median"),
            "step_frac": m.get("step_frac"),
            "in_degree": indeg.get(v, 0),
            "out_degree": outdeg.get(v, 0),
            "pure_source": pure,
            "upstream_of_top5": up,
            "in_top5": v in top5,
            "pool": _pool_status(v, allvars, excluded),
        })
    rows.sort(key=lambda r: (r["t_first"],
                             0 if r["pure_source"] else 1,
                             -(r["step_frac"] or 0),
                             -r["out_degree"]))
    primary = rows[0] if rows else None
    also = rows[1:3]

    diag: List[Dict] = []
    directed = {(e.get("from"), e.get("to")) for e in edges}
    for a, b in sorted(directed):
        if (b, a) in directed and (a, b) < (b, a):
            diag.append({"type": "两向都有稳定边", "subject": f"{a}<->{b}",
                         "detail": "若物理上是反馈回路，单向化方向由算法选择，方向需单独复核"})
    for e in (payload.get("k3", {}).get("reverse_edges") or []):
        diag.append({"type": "与故障模板方向相反", "subject": e.get("stable"),
                     "detail": f"模板期望 {e.get('template')}，稳定边为 {e.get('stable')}"})
    for c in candidates:
        if c["graph"]["n_outgoing"] >= 7 and c["graph"]["n_incoming"] <= 2:
            diag.append({"type": "只出不进的源状结构", "subject": c["variable"],
                         "detail": f"出度 {c['graph']['n_outgoing']}、"
                                   f"入度 {c['graph']['n_incoming']}，总效应会被放大"})
    biggest = sorted(edges, key=lambda e: abs(e.get("weight_median") or 0.0),
                     reverse=True)[:3]
    for e in biggest:
        diag.append({"type": "量级最大的边",
                     "subject": f"{e.get('from')}->{e.get('to')}",
                     "detail": f"|系数| = {abs(e.get('weight_median') or 0.0):.3f}，"
                               f"对总效应贡献占主导"})

    ft = EK.load_fault_templates(k)
    slim_vars = {v: {"status": m.get("status"), "status_text": m.get("status_text"),
                     "t_first_median": m.get("t_first_median"),
                     "step_frac": m.get("step_frac"), "n_eval": m.get("n_eval"),
                     "n_group": m.get("n_group"),
                     "sigma_used": m.get("sigma_used"),
                     "sigma_saturated": m.get("sigma_saturated")}
                 for v, m in tempor["variables"].items()}

    return {
        "run_id": run_id,
        "template": f"{k}_{g}",
        "accident": {"code": k, "name_cn": ft.get("name_cn"), "group": g,
                     "group_cn": GROUP_CN.get(g, g),
                     "signal_channel": (rc.get("template") or {}).get("signal_channel")},
        "ranking": {
            "top5_before": gc.get("top5_before"),
            "top5_after": gc.get("top5_after"),
            "top5_set_diff": gc.get("top5_set_diff"),
            "n_rank_changed": gc.get("n_rank_changed"),
            "note": "排序由算法给定，禁止改动；candidates 是原排序前五",
        },
        "candidates": candidates,
        "mechanism_background": {
            "template_id": f"K3:{k}",
            "name_cn": ft.get("name_cn"),
            "trigger": ft.get("trigger"),
            "expected_chain": ft.get("expected_chain"),
            "expected_sign_direction": ft.get("expected_sign_direction"),
            "key_symptoms": ft.get("key_symptoms"),
            "note": "机理背景只用于“事故大概怎么传过来”，不参与档位判定、不参与指向",
        },
        "temporal_evidence": {
            "definition": tempor["definition"],
            "limitations": tempor["limitations"],
            "sampling_interval": dt,
            "n_group": tempor["n_group"],
            "n_trajectories_loaded": tempor["n_trajectories_loaded"],
            "variables": slim_vars,
        },
        "timing": {"t_inject": tempor["t_inject"], "t_end": tempor["t_end"],
                   "n_group": tempor["n_group"], "sampling_interval": dt},
        "variable_dictionary": {v: _var_meta(var_idx, v) for v in variables},
        "graph_diagnosis": diag[:10],
        "deviation_legend": {"levels": LEVEL_CN,
                             "note": "偏差档位是模板内相对分位，不可跨变量比较，不参与排序"},
        "pointing_hint": {
            "rule": "先按首动时刻，其次纯源点（入度 0/出度 >0），再看阶跃占比与出度；"
                    "候选为不在 Top-5 的 Top-5 上游或纯源点",
            "primary": primary,
            "also_check": also,
        },
        "knowledge_policy": {
            "knowledge_in_verdict": False,
            "verdict_inputs": ["首动先后", "图结构（入度/出度/纯源点）", "偏离幅度与阶跃"],
            "knowledge_allowed_in": [
                "what_it_is（名称/单位/子系统）",
                "how_accident_travels（K3 机理背景）",
                "physical_background（仅描述，不参与判定）",
            ],
            "forbidden_in": ["as_start_verdict", "basis", "pointing"],
            "note": "知识条目（can_be_root/assertion/sourced/条目 id）不进入本输入；"
                    "知识库继续用于禁边与极性校验（K1/K2），不用于解释判定",
        },
    }


# ==================== LLM ====================
OPERATOR_SYSTEM_PROMPT = """你是核电厂事故分析的当班技术顾问。本版把“知识”和“判定”彻底分开：

- 判定只用数据侧三样：① 首动时刻（相对图上所有上游变量的越限先后）；② 图结构位置
  （入度/出度、是否纯源点）；③ 偏离幅度与形态（是否阶跃）。
- 本版不向你提供任何知识条目（没有 can_be_root / assertion / sourced / 条目 id）。
  不要引用“知识条目/知识库/条目 id”，不要用物理常识改变档位。
- 输入里的 mechanism_background 只是机理背景，只能用在“事故大概怎么传过来”一句，
  不得用来判定档位、不得用来选指向。
- 知识只由渲染器在正文末尾以“物理背景（仅描述，不参与判定）”出现，不进入你的判断。

档位（必须与输入 candidates[].temporal.classification 完全一致；你负责复核并解释，不改档）：
1. 最早异常：比图上全部上游早至少一个采样间隔，且有阶跃特征；图上无上游的纯源点
   若有阶跃也算（会注明“纯源点”）。这是首要排查对象。
2. 同期异常：与最早上游相差不超过一个采样间隔（输入 timing.sampling_interval）。
   若因“早于上游但不足一个间隔或无阶跃”按同期处理，必须写“这是取舍”。
3. 滞后异常：晚于最早上游超过一个采样间隔，属传导响应。
4. 未评估：时序算不出来（tolerance 未标定 / σ 不可用 / 分析窗内无越阈 / 上游均未评估），
   如实写，不许用知识补。

结论必须指向：
- 用 pointing_hint.primary 作为数据侧首选；写清它的首动/阶跃/图结构依据。
- 写清它当前状态（是否在池内、出池原因、原排序名次）；若不在 Top-5，必须写 why_not_top5。
- 措辞必须是“数据侧指向”，不得写成“系统推荐”。
- 若 pointing_hint.primary 为空，才写 found=false + unable_reason（“无法定位”）。

硬约束：
5. 排序由算法给定。不得新增/删除/替换候选，不得改变名次，不得写“应该排第几”。
   ranking_modified 恒为 false。
6. 只使用随附 JSON 中出现的变量名和数字；数字保留 2 位小数；不得做四则运算造新数字。
7. 给操作者的正文不要堆字段名：不要出现 gate_status、total_effect、in_degree、out_degree、
   path_audit、jaccard、条件数、截断比这类内部术语。需要表达“门控排除”时写
   “未进候选池：注入前基线饱和、偏差不可评估”。
8. “判定依据（仅数据侧）”只能写首动、上游、入度/出度、纯源点、阶跃、偏差档位；
   不得出现“知识/条目/知识库”。
9. 偏差档位只表示该通道自身相对基线的变化幅度，不可跨变量比较，也不参与排序。
   写中文档位（正常/中等/较大/严重），不要写 normal/moderate/large/severe。
10. 计数写整数，时刻写 110s，不要追加 .00；what_it_is 必须包含中文名、单位、所属子系统，
    以及一句物理含义；one_line 用标点分隔各档位，不要连写。
    时间只能写输入中出现的具体时刻（注入时刻、首动时刻、上游首动时刻、分析窗结束），
    或“首动前后一个采样间隔”；不要自造 200s 这类时间窗。
11. action_plan 只要 3-5 条，每条都必须是一个可执行动作；不要写口径说明。
12. 未评估的 basis.text 只写数据原因（标定不足、分析窗内无越限、无首动等），
    不要写“不用知识补”“不靠知识”这类元话。
13. 只输出一个 JSON 对象，严格符合 schema，不要 markdown 代码块，不要前后解释。
"""

OPERATOR_USER_SCHEMA = """{
  "template": "<与输入一致>",
  "one_line": "<=90字：五个分别是什么档位，数据侧首选指向谁",
  "pointing": {
    "found": true,
    "variable": "<pointing_hint.primary.variable>",
    "name_cn": "<中文名>",
    "why": "<=120字，只写数据侧依据：首动/阶跃/纯源点或上游关系",
    "pool_status": "<照抄 pointing_hint.primary.pool.why_not_top5>",
    "why_not_top5": "<它为什么不在 Top-5；不得写成‘系统推荐’>",
    "also_check": [{"variable": "<also_check 中的变量>", "pool_status": "<照抄>", "why": "<数据侧一句>"}],
    "unable_reason": ""
  },
  "candidates": [
    {
      "variable": "<输入 candidates 中的变量，顺序不变>",
      "as_start_verdict": "<必须等于该候选 temporal.classification>",
      "verdict_reason": "<=70字，只写数据侧理由",
      "what_it_is": "这个测点是什么（人话）",
      "why_on_list": "排序为什么把它列出来（排序分数、出入边、门控；不写知识）",
      "how_accident_travels": "这类事故大概怎么传到它（可用 mechanism_background）",
      "first_check": "到现场先查什么（具体到测点、通道或边）",
      "basis": {
        "timing": "<首动时刻与上游比较>",
        "graph": "<入度/出度、是否纯源点>",
        "deviation": "<偏差档位/阶跃占比>",
        "text": "<把三样合成一句；不得出现知识/条目>"
      }
    }
  ],
  "action_plan": ["1. <可执行动作>", "2. <可执行动作>", "3. <可执行动作>（3-5 条）"]
}"""


def build_operator_prompt(operator_input: Dict) -> str:
    return (
        "以下是一份只读数据侧证据包（CausalTrace v2.3，未重算因果）。\n"
        "请按数据侧三样复核档位、写人话解释，并给出数据侧指向。\n"
        "必须严格按 schema 输出一个 JSON 对象；candidates 必须与输入一一对应、顺序不变。\n\n"
        "【输出 schema】\n" + OPERATOR_USER_SCHEMA + "\n\n"
        "【证据包】\n" + json.dumps(operator_input, ensure_ascii=False, indent=2) + "\n"
    )


# ==================== 校验 ====================
_NUM_RE = re.compile(r"(?<![A-Za-z0-9_.])(-?\d+(?:\.\d+)?)(%?)")
_UPPER_RE = re.compile(r"[A-Z][A-Z0-9_]{1,}")
DATA_WORDS = ("首动", "上游", "入度", "出度", "纯源", "阶跃", "偏差",
              "时序", "未评估", "轨迹", "越限", "采样")
KNOW_WORDS = ("知识", "条目", "知识库")
ENTRY_ID_RE = re.compile(r"\b[SP]\d+_[A-Z0-9_]+")


def _walk_numbers(node: Any, out: set) -> None:
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        out.add(round(float(node), 2))
        return
    if isinstance(node, str):
        for m in _NUM_RE.finditer(node):
            try:
                out.add(round(float(m.group(1)), 2))
            except ValueError:
                pass
        return
    if isinstance(node, dict):
        for key, val in node.items():
            _walk_numbers(key, out)
            _walk_numbers(val, out)
        return
    if isinstance(node, (list, tuple, set)):
        for val in node:
            _walk_numbers(val, out)


def _walk_upper(node: Any, out: set) -> None:
    if isinstance(node, str):
        out.update(_UPPER_RE.findall(node))
        return
    if isinstance(node, dict):
        for key, val in node.items():
            _walk_upper(key, out)
            _walk_upper(val, out)
        return
    if isinstance(node, (list, tuple, set)):
        for val in node:
            _walk_upper(val, out)


def _extract_json(text: str) -> Dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("模型输出里找不到 JSON 对象")
    return json.loads(text[start:end + 1])


def _no_knowledge(text: str) -> bool:
    body = text or ""
    return (not any(w in body for w in KNOW_WORDS)) and not ENTRY_ID_RE.search(body)


def validate_operator_output(out: Dict, operator_input: Dict) -> List[str]:
    """判定与指向必须与数据侧一致；判定字段不得出现任何知识措辞。"""
    errs: List[str] = []
    if out.get("template") != operator_input["template"]:
        errs.append(f"template 不一致：{out.get('template')}")

    cands = out.get("candidates")
    if not isinstance(cands, list) or len(cands) != 5:
        errs.append("candidates 数量不是 5")
        cands = cands if isinstance(cands, list) else []
    want = [c["variable"] for c in operator_input["candidates"]]
    got = [c.get("variable") for c in cands]
    if got != want:
        errs.append(f"candidates 变量/顺序不一致：want={want} got={got}")

    imap = {c["variable"]: c for c in operator_input["candidates"]}
    for c in cands:
        v = c.get("variable")
        src = imap.get(v) or {}
        expected = (src.get("temporal") or {}).get("classification")
        if c.get("as_start_verdict") != expected:
            errs.append(f"{v} 档位与数据侧不一致：{c.get('as_start_verdict')} vs {expected}")
        basis = c.get("basis") or {}
        text = basis.get("text") or ""
        if not text.strip():
            errs.append(f"{v} basis.text 为空")
        if not any(w in text for w in DATA_WORDS):
            errs.append(f"{v} basis.text 没有数据侧关键词")
        for key in ("timing", "graph", "deviation"):
            if not (basis.get(key) or "").strip():
                errs.append(f"{v} basis.{key} 为空")
        for key in ("verdict_reason", "why_on_list", "how_accident_travels", "first_check"):
            if not _no_knowledge(c.get(key)):
                errs.append(f"{v} {key} 含知识措辞/条目 id")
        if not _no_knowledge(text):
            errs.append(f"{v} basis.text 含知识措辞/条目 id")

    pt = out.get("pointing") or {}
    hint = (operator_input.get("pointing_hint") or {}).get("primary")
    if hint:
        if not pt.get("found"):
            errs.append("pointing_hint.primary 存在但 found=false")
        if pt.get("variable") != hint.get("variable"):
            errs.append(f"指向变量与数据侧首选不一致：{pt.get('variable')} "
                        f"vs {hint.get('variable')}")
        if not (pt.get("pool_status") or "").strip():
            errs.append("pointing.pool_status 为空")
        if not (pt.get("why_not_top5") or "").strip():
            errs.append("pointing.why_not_top5 为空")
        if not _no_knowledge(pt.get("why")):
            errs.append("pointing.why 含知识措辞/条目 id")
        if "系统推荐" in (pt.get("why_not_top5") or ""):
            errs.append("指向写成了“系统推荐”")
    else:
        if pt.get("found"):
            errs.append("数据侧无首选但 pointing.found=true")
        if not (pt.get("unable_reason") or "").strip():
            errs.append("pointing found=false 但缺 unable_reason")
    return errs


def verify_operator_faithfulness(out: Dict, operator_input: Dict) -> Dict:
    """数字 / 变量名两查：只能引用输入里出现过的。"""
    nums: set = set()
    uppers: set = set()
    _walk_numbers(operator_input, nums)
    _walk_upper(operator_input, uppers)
    for i in range(0, 11):
        nums.add(float(i))
    for n in list(nums):
        if 0.0 <= n <= 1.0:
            nums.add(round(n * 100, 2))

    parts = [out.get("one_line") or ""]
    pt = out.get("pointing") or {}
    parts += [str(pt.get(k) or "") for k in
              ("why", "pool_status", "why_not_top5", "unable_reason")]
    for c in out.get("candidates") or []:
        for key in ("verdict_reason", "what_it_is", "why_on_list",
                    "how_accident_travels", "first_check"):
            parts.append(str(c.get(key) or ""))
        basis = c.get("basis") or {}
        parts += [str(basis.get(k) or "") for k in
                  ("timing", "graph", "deviation", "text")]
    for item in out.get("action_plan") or []:
        parts.append(re.sub(r"^\s*\d+\s*[.、)]\s*", "", str(item)))
    text = "\n".join(parts)

    bad_nums: List[str] = []
    bad_vars: List[str] = []
    for m in _NUM_RE.finditer(text):
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        if not any(abs(val - n) <= 0.006 for n in nums):
            bad_nums.append(m.group(0))
    for tok in _UPPER_RE.findall(text):
        if tok in uppers:
            continue
        m = re.match(r"^([A-Z][A-Z0-9_]*?)\d+$", tok)
        if m and m.group(1) in uppers:
            continue
        bad_vars.append(tok)
    return {"n_numbers": len(_NUM_RE.findall(text)),
            "unsupported_numbers": sorted(set(bad_nums)),
            "unknown_tokens": sorted(set(bad_vars)),
            "faithful": not bad_nums and not bad_vars}


# ==================== 渲染（布局固定，不由 LLM 决定） ====================
def _gate_cn(c: Dict) -> str:
    gate = c.get("gate") or {}
    status = gate.get("status")
    if status == "excluded_structural" and gate.get("reason") == "safety_action_variable":
        return "被安全动作变量规则排除"
    return GATE_CN.get(status, str(status))


def _temporal_line(c: Dict) -> str:
    t = c.get("temporal") or {}
    cm = t.get("candidate") or {}
    if cm.get("status") != T.STATUS_OK:
        return "时序：未评估（" + str(cm.get("status_text") or "无数据") + "）"
    s = (f"时序：首动 {cm['t_first_median']:.0f}s"
         f"（注入后 {cm['delay_vs_inject_median']:+.0f}s；"
         f"{cm.get('n_eval')}/{cm.get('n_group')} 条轨迹；"
         f"阶跃 {cm.get('step_frac') or 0:.0%}）")
    ups = t.get("upstream") or []
    if t.get("zero_upstream"):
        s += "；图上无上游（纯源点），无上游可比"
    else:
        rows = [f"{u['variable']} {u['t_first_median']:.0f}s"
                if u.get("status") == T.STATUS_OK else f"{u['variable']} 未评估"
                for u in ups]
        s += "；上游 " + "、".join(rows)
        if t.get("temporal_strong_reverse"):
            s += " → 上游更早"
        elif t.get("temporal_supports_start"):
            s += " → 早于全部上游"
        else:
            s += " → 未早于全部上游"
    return s


def clean_basis(text: Optional[str]) -> str:
    """只保留描述性物理含义，剔除“是不是起点/根因”一类判断句。"""
    if not text:
        return ""
    drop = ("不是起点", "不是根因", "不能作为起点", "根因候选", "不能作为根",
            "不是给水破口的原因", "不构成单向驱动", "不能作为强支持链",
            "而非驱动", "不是驱动", "不驱动")
    keep = [p.strip() for p in re.split(r"[；;。]", str(text))
            if p.strip() and not any(d in p for d in drop)]
    return "；".join(keep)


def load_background(accident_type: str) -> Dict[str, List[Dict]]:
    """知识条目按变量归组；**仅供渲染层物理背景**，不参与判定。"""
    out: Dict[str, List[Dict]] = {}
    for doc in (EK.load_supplementary_mechanisms(), EK.load_positive_mechanisms()):
        for e in (doc.get("entries") or []):
            if accident_type in (e.get("applicable_accidents") or []):
                out.setdefault(e.get("variable"), []).append(e)
    return out


def _physical_background(v: str, meta: Dict, bg: Dict[str, List[Dict]]) -> str:
    base = f"{meta.get('meaning')}（{meta.get('unit')}，{meta.get('subsystem')}）"
    entries = bg.get(v) or []
    if not entries:
        return base + "。本模板知识库无条目。"
    e = entries[0]
    src = "有外部出处" if e.get("sourced") else "无外部出处（仅 AI 代审留库）"
    desc = clean_basis(e.get("basis"))
    s = base + "。"
    if desc:
        s += f"物理含义：{desc}；"
    s += f"期望方向 {e.get('expected_direction')}；{src}。"
    return s


def _background_conflict(v: str, verdict: str, bg: Dict[str, List[Dict]]) -> str:
    """知识与数据档位不一致时，只作背景提示，不改档。"""
    if verdict not in (TIER_EARLIEST, TIER_SYNC):
        return ""
    if any(e.get("can_be_root") is False for e in (bg.get(v) or [])):
        return ("（背景提示：知识条目把该点归为响应量；与数据侧档位不一致；"
                "仅背景说明，不参与判定。）")
    return ""


def render_operator_text(out: Dict, operator_input: Dict,
                         bg: Optional[Dict[str, List[Dict]]] = None) -> str:
    """固定四块版面：一句话结论 / 逐个说人话 / 判定与依据 / 先查什么 + 口径。"""
    bg = bg or {}
    lines: List[str] = []
    a = operator_input["accident"]
    cs = out.get("candidates") or []
    counts = {tier: sum(1 for c in cs if c.get("as_start_verdict") == tier)
              for tier in TIERS}
    lines.append(f"【{a['code']} / {a['group']} · Top-5 解释（操作者版）】")
    lines.append(f"事故类型：{a['name_cn']}（{a['group_cn']}）｜"
                 f"信号通道：{a['signal_channel']}｜模板：{operator_input['template']}")
    lines.append(f"判定分布：最早异常 {counts[TIER_EARLIEST]} 条 / "
                 f"同期异常 {counts[TIER_SYNC]} 条 / "
                 f"滞后异常 {counts[TIER_LAG]} 条 / 未评估 {counts[TIER_UNASSESSED]} 条")
    lines.append("")
    lines.append("一句话结论")
    lines.append("  " + (out.get("one_line") or "").strip())
    lines.append("")
    pt = out.get("pointing") or {}
    hint = (operator_input.get("pointing_hint") or {}).get("primary")
    lines.append("指向：数据侧首选")
    if pt.get("found") and hint:
        lines.append(f"  {hint.get('variable')}｜{hint.get('name_cn')}")
        lines.append(f"  依据：{pt.get('why')}")
        lines.append(f"  当前状态：{hint.get('pool', {}).get('why_not_top5')}")
        if not hint.get("in_top5"):
            lines.append(f"  为什么不在 Top-5：{hint.get('pool', {}).get('why_not_top5')}")
        for x in ((operator_input.get("pointing_hint") or {}).get("also_check") or []):
            tag = "纯源点" if x.get("pure_source") else "Top-5 上游"
            lines.append(
                f"  同时核对：{x.get('variable')}——"
                f"{x.get('pool', {}).get('why_not_top5')}；数据侧："
                f"首动 {x.get('t_first'):.0f}s，阶跃 {x.get('step_frac') or 0:.0%}，"
                f"{tag}，入度 {x.get('in_degree')}/出度 {x.get('out_degree')}。")
        lines.append("  说明：这是数据侧指向，不是系统推荐，也没有改排序。")
    else:
        lines.append(f"  无法定位：{pt.get('unable_reason') or '时序与结构均不支持定位'}")
    lines.append("")
    lines.append("一、Top-5 逐个说明")
    by_var = {c.get("variable"): c for c in cs}
    for i, c in enumerate(operator_input["candidates"], 1):
        o = by_var.get(c["variable"]) or {}
        rank_txt = (f"原排序第 {c.get('rank')} 名"
                    + (f"（门控后第 {c['rank_v2']} 名）" if c.get("rank_v2")
                       else "（未进入门控后排序）"))
        score = "—" if c.get("effect_score") is None else f"{c['effect_score']:.2f}"
        lvl = LEVEL_CN.get(c.get("deviation_level"), "未产出")
        lines.append(f"{i}. {c['variable']}｜{c.get('name_cn')}｜{rank_txt}｜"
                     f"排序分数 {score}｜{_gate_cn(c)}｜偏差档位：{lvl}")
        lines.append(f"   判定（数据侧）：{o.get('as_start_verdict')}——"
                     f"{o.get('verdict_reason')}")
        what = o.get("what_it_is") or ""
        if c.get("unit") and str(c["unit"]) not in what:
            what = f"{what}（{c['unit']}，{c.get('subsystem')}）"
        lines.append(f"   这是什么：{what}")
        lines.append(f"   为什么会被列出来：{o.get('why_on_list')}")
        lines.append(f"   事故大概怎么传过来：{o.get('how_accident_travels')}")
        lines.append(f"   先查什么：{o.get('first_check')}")
        lines.append("   " + _temporal_line(c))
        basis = o.get("basis") or {}
        lines.append(f"   判定依据（仅数据侧）：{basis.get('text')}")
        meta = operator_input["variable_dictionary"].get(c["variable"]) or {}
        lines.append(f"   物理背景（仅描述，不参与判定）："
                     f"{_physical_background(c['variable'], meta, bg)}"
                     f"{_background_conflict(c['variable'], o.get('as_start_verdict'), bg)}")
        lines.append("")
    lines.append("二、判定与依据（按档位）")
    for tier in TIERS:
        rows = [c for c in cs if c.get("as_start_verdict") == tier]
        lines.append(f"  【{tier}】{len(rows)} 条")
        if not rows:
            lines.append("    - 无")
        for c in rows:
            lines.append(f"    - {c.get('variable')}：{(c.get('basis') or {}).get('text')}")
    lines.append("")
    lines.append("三、先查什么（按优先级）")
    for i, item in enumerate(out.get("action_plan") or [], 1):
        txt = re.sub(r"^\s*\d+\s*[.、)]\s*", "", str(item))
        lines.append(f"  {i}. {txt}")
    lines.append("")
    lines.extend(_boundary_lines(operator_input))
    text = "\n".join(lines)
    # 版面修正：变量名与紧跟的时刻之间补空格（如 LVPZ110s -> LVPZ 110s）
    names = sorted((operator_input.get("variable_dictionary") or {}).keys(),
                   key=len, reverse=True)
    if names:
        pat = r"\b(" + "|".join(re.escape(v) for v in names) + r")(\d+)(?![\d_])"
        text = re.sub(pat, r"\1 \2", text)
    return text


def _boundary_lines(operator_input: Dict) -> List[str]:
    ranking = operator_input["ranking"]
    td = operator_input["temporal_evidence"]["definition"]
    dt = operator_input["timing"]["sampling_interval"]
    return [
        "四、口径与边界（由产物事实生成，不由 LLM 改写）",
        f"  - 排序未被改动：rank/rank_v2 原样保留，ranking_modified=false；"
        f"原排序前五 {ranking['top5_before']}，门控后前五 {ranking['top5_after']}。",
        f"  - 时序证据：首动口径={td['first_motion']}；阶跃口径={td['step']}；"
        f"基线={td['baseline']}；{td['provenance']}。",
        f"  - 档位只用数据侧三样：首动先后、图结构（入度/出度/纯源点）、偏离幅度与阶跃；"
        f"采样间隔 {dt:.0f}s；早于上游但不足一个采样间隔按同期处理（取舍）。",
        f"  - 饱和通道（注入前恒为 0、σ 由物理下限撑起）的首动仍按 3σ 下限计算，"
        f"该 3σ 不是实测噪声的 3 倍，结论需现场复核；逐项见产物 temporal_evidence。",
        f"  - 知识不参与判定：LLM 输入不含知识条目（can_be_root/assertion/sourced/条目 id）；"
        f"知识只出现在‘这是什么’（名称/单位/子系统）、‘事故大概怎么传过来’（K3 机理背景）、"
        f"‘物理背景（仅描述，不参与判定）’三处；知识库继续用于禁边与极性校验（K1/K2）。",
        f"  - 起点性条目只有 3 条（WLR/WTRA/WFLB），且三条全部在候选池外"
        f"（注入前基线饱和、偏差不可评估）；这是已知结构性缺口，本轮不补条目。",
        f"  - 单引擎口径：LiNGAM 单引擎、bootstrap 5 次，n_consensus=null，"
        f"稳定边不等于多引擎共识。",
        f"  - 偏差档位只表示该通道自身相对基线（模板内分位），不可跨变量比较，不参与排序。",
        f"  - 运行号：{operator_input['run_id']}；本产物是附加件，不覆盖既有 "
        f"llm_explanation_*.json。",
        "",
        "（正文由 LLM 依据同 run_id 的只读数据侧证据生成；物理背景由渲染器从知识库补入，"
        "标注为仅描述；渲染布局由脚本固定。）",
    ]


# ==================== 规则稿（LLM 不可用/输出不合格时兜底） ====================
def render_rule_output(operator_input: Dict) -> Dict:
    """确定性规则稿：字段全部由输入拼装，不调用 LLM。"""
    chain = (operator_input.get("mechanism_background") or {}).get("expected_chain") or []
    chain_txt = "→".join(chain) if chain else "无"
    cands: List[Dict] = []
    for c in operator_input["candidates"]:
        t = c["temporal"]
        cm = t.get("candidate") or {}
        graph = c.get("graph") or {}
        step = cm.get("step_frac")
        verdict = t["classification"]
        timing = (f"首动 {cm['t_first_median']:.0f}s，注入后 "
                  f"{cm['delay_vs_inject_median']:+.0f}s；上游更早"
                  if cm.get("status") == T.STATUS_OK else
                  f"未评估（{cm.get('status_text')}）")
        deviation = (f"偏差档位 {LEVEL_CN.get(c.get('deviation_level'), '未产出')}；"
                     f"阶跃占比 {0.0 if step is None else step:.0%}")
        basis_text = (f"{t['classification_reason']}；入度 {graph.get('n_incoming')}、"
                      f"出度 {graph.get('n_outgoing')}；{deviation}")
        cands.append({
            "variable": c["variable"],
            "as_start_verdict": verdict,
            "verdict_reason": t["classification_reason"],
            "what_it_is": f"{c.get('name_cn')}，单位 {c.get('unit')}，"
                          f"所属{c.get('subsystem')}。",
            "why_on_list": f"原排序第 {c.get('rank')} 名，排序分数 {c.get('effect_score')}；"
                           f"入度 {graph.get('n_incoming')}、出度 {graph.get('n_outgoing')}；"
                           f"{_gate_cn(c)}。",
            "how_accident_travels": f"{operator_input['accident']['name_cn']}的机理链路为 "
                                    f"{chain_txt}。",
            "first_check": f"核对 {c['variable']} 通道及主要入出边，"
                           f"按首动前后一个采样间隔比对。",
            "basis": {"timing": timing,
                      "graph": f"入度 {graph.get('n_incoming')}、"
                               f"出度 {graph.get('n_outgoing')}"
                               + ("，纯源点" if graph.get("n_incoming") == 0 else ""),
                      "deviation": deviation,
                      "text": basis_text},
        })
    hint = (operator_input.get("pointing_hint") or {}).get("primary")
    if hint:
        pointing = {
            "found": True, "variable": hint["variable"],
            "name_cn": hint.get("name_cn"),
            "why": f"数据侧：首动 {hint['t_first']:.0f}s，"
                   f"阶跃 {hint.get('step_frac') or 0:.0%}；"
                   f"入度 {hint.get('in_degree')}/出度 {hint.get('out_degree')}，"
                   + ("纯源点" if hint.get("pure_source") else "Top-5 图上上游"),
            "pool_status": hint["pool"]["why_not_top5"],
            "why_not_top5": hint["pool"]["why_not_top5"],
            "also_check": [{"variable": x["variable"],
                            "pool_status": x["pool"]["why_not_top5"],
                            "why": f"首动 {x['t_first']:.0f}s，"
                                   f"阶跃 {x.get('step_frac') or 0:.0%}"}
                           for x in ((operator_input.get("pointing_hint") or {})
                                     .get("also_check") or [])],
            "unable_reason": "",
        }
    else:
        pointing = {"found": False, "variable": None, "name_cn": None, "why": "",
                    "pool_status": "", "why_not_top5": "", "also_check": [],
                    "unable_reason": "时序与结构均不支持定位"}
    one_line = "、".join(f"{c['variable']}{c['as_start_verdict']}" for c in cands)
    one_line += f"；数据侧首选指向{pointing['variable']}" if pointing["found"] else "；无法定位"
    plan: List[str] = []
    if pointing["found"]:
        plan.append(f"1. 核对 {pointing['variable']} 通道的首动与阶跃，确认其是否先于 Top-5 异常。")
    for c in sorted(cands, key=lambda x: TIERS.index(x["as_start_verdict"]))[:3]:
        plan.append(f"{len(plan) + 1}. 核对 {c['variable']} 通道及其主要入出边，"
                    f"按首动前后一个采样间隔比对。")
    plan.append(f"{len(plan) + 1}. 对未评估通道补做阈值标定与注入前基线检查。")
    return {"template": operator_input["template"], "one_line": one_line,
            "pointing": pointing, "candidates": cands, "action_plan": plan}


# ==================== 生成与落盘 ====================
def _merge_candidates(operator_input: Dict, out: Dict) -> List[Dict]:
    by_var = {c.get("variable"): c for c in (out.get("candidates") or [])}
    merged: List[Dict] = []
    for c in operator_input["candidates"]:
        merged.append({
            "variable": c["variable"],
            "rank": c.get("rank"),
            "rank_v2": c.get("rank_v2"),
            "effect_score": c.get("effect_score"),
            "gate_status": (c.get("gate") or {}).get("status"),
            "gate_reason": (c.get("gate") or {}).get("reason"),
            "in_pool": (c.get("gate") or {}).get("in_pool"),
            "deviation_level": c.get("deviation_level"),
            "graph": c.get("graph"),
            "temporal": c.get("temporal"),
            "pool": c.get("pool"),
            "llm": by_var.get(c["variable"]) or {},
        })
    return merged


def explain_operator(accident_type: str, group: str, backend: str = "auto",
                     run_id: Optional[str] = None,
                     write: bool = True) -> Dict:
    """生成操作者版解释（默认 llm；失败降级规则稿并写明原因）。"""
    operator_input = build_operator_input(accident_type, group, run_id=run_id)
    requested = backend
    if backend == "auto":
        backend = "llm" if LE.llm_unavailable_reason() is None else "rule"

    raw_text: Optional[str] = None
    validation: List[str] = []
    degraded = False
    degraded_reason: Optional[str] = None

    if backend == "llm":
        why = LE.llm_unavailable_reason()
        if why:
            degraded, degraded_reason = True, f"llm 不可用，降级规则稿：{why}"
        else:
            try:
                prompt = build_operator_prompt(operator_input)
                raw_text = LE._call_chat_completions(
                    prompt, timeout=max(float(LLM_TIMEOUT_PIN_S), 180.0),
                    system_prompt=OPERATOR_SYSTEM_PROMPT)
                out = _extract_json(raw_text)
                validation = validate_operator_output(out, operator_input)
                if validation:
                    degraded = True
                    degraded_reason = ("模型输出未通过判定/指向校验，降级规则稿："
                                       + "；".join(validation[:5]))
            except Exception as exc:              # noqa: BLE001 - 附加路径不得中断主链
                logger.warning(f"[D6-operator] {accident_type}_{group} 生成失败："
                               f"{type(exc).__name__}: {exc}")
                degraded, degraded_reason = True, f"生成失败，降级规则稿：" \
                                                  f"{type(exc).__name__}: {exc}"

    if backend != "llm" or degraded:
        out = render_rule_output(operator_input)
        backend_used = "rule"
    else:
        backend_used = "llm"

    bg = load_background(accident_type)
    text = render_operator_text(out, operator_input, bg)
    faith = (verify_operator_faithfulness(out, operator_input) if backend_used == "llm"
             else {"faithful": True, "n_numbers": None,
                   "unsupported_numbers": [], "unknown_tokens": [],
                   "note": "规则稿由脚本按产物事实拼装，不做模型忠实性校验"})
    counts = {tier: sum(1 for c in (out.get("candidates") or [])
                        if c.get("as_start_verdict") == tier) for tier in TIERS}
    pointing_llm = out.get("pointing") or {}

    doc = {
        "template": operator_input["template"],
        "accident": operator_input["accident"],
        "signal_channel": operator_input["accident"]["signal_channel"],
        "requested_backend": requested,
        "backend": backend_used,
        "degraded": degraded,
        "degraded_reason": degraded_reason,
        "model": {"name": LLM_MODEL_PIN, "temperature": LLM_TEMPERATURE_PIN,
                  "timeout_s": max(float(LLM_TIMEOUT_PIN_S), 180.0)},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ranking_modified": False,
        "knowledge_in_verdict": False,
        "knowledge_policy": operator_input["knowledge_policy"],
        "verdict_rule": {
            "inputs": ["首动先后", "图结构（入度/出度/纯源点）", "偏离幅度与阶跃"],
            "tiers": {
                TIER_EARLIEST: "比图上全部上游早至少一个采样间隔且有阶跃；"
                               "纯源点有阶跃亦算",
                TIER_SYNC: "与最早上游相差不超过一个采样间隔；"
                           "早于上游但不足一个间隔按同期处理（取舍）",
                TIER_LAG: "晚于最早上游超过一个采样间隔，属传导响应",
                TIER_UNASSESSED: "首动算不出（未标定 / sigma 不可用 / 无越阈 / 上游均未评估），"
                                 "不得用知识补",
            },
        },
        "verdict_counts": counts,
        "pointing": {
            "rule": (operator_input.get("pointing_hint") or {}).get("rule"),
            "data_side_primary": (operator_input.get("pointing_hint") or {}).get("primary"),
            "data_side_also_check": (operator_input.get("pointing_hint") or {}).get("also_check"),
            "llm": pointing_llm,
            "note": "数据侧指向，不是系统推荐；排序未被改动",
        },
        "candidates": _merge_candidates(operator_input, out),
        "temporal_evidence": operator_input["temporal_evidence"],
        "timing": operator_input["timing"],
        "mechanism_background": operator_input["mechanism_background"],
        "graph_diagnosis": operator_input["graph_diagnosis"],
        "ranking": operator_input["ranking"],
        "text": text,
        "validation": {"errors": validation, "passed": not validation},
        "faithfulness": faith,
        "inputs": [{"path": "tracing/root_candidates", "run_id": operator_input["run_id"]},
                   {"path": "signatures/signature", "run_id": operator_input["run_id"]},
                   {"path": "reports/gate_compare", "run_id": operator_input["run_id"]},
                   {"path": "reports/deviation", "run_id": operator_input["run_id"]},
                   {"path": "tolerances/tolerance", "run_id": operator_input["run_id"]},
                   {"path": "registry.data_dir（原始 CSV）", "run_id": operator_input["run_id"]}],
        "note": "附加产物：不覆盖既有 llm_explanation_*.json；排序、学图、门控未改动",
    }
    if raw_text is not None:
        doc["llm_raw"] = raw_text

    if write:
        path = artifact.write_artifact(
            ARTIFACT_NAME.format(k=accident_type, g=group), doc, "reports",
            run_id=operator_input["run_id"], accident_type=accident_type, group=group,
            source="tracing/root_candidates;signatures/signature;reports/gate_compare;"
                   "reports/deviation;tolerances/tolerance;registry.data_dir")
        logger.info(f"[D6-operator] 已落盘：{path}")
    return doc

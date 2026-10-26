"""v2.1 R5 LLM 解释执行器：对已有 chain JSON 增量生成 LLM 解释（不重跑 R0~R5 数据）。

- 读 output/rca_v21/chains/root_cause_chain_{cond}_{type}_{sev}.json（92 条）；
- 对每条调用 llm_explanation.generate_llm_explanation（LLM 主路径，失败/幻觉降级模板）；
- 写回原 chain JSON 的 "llm" 字段（llm_used/llm_explanation/faithful_pass/degraded）；
- 另生成 chains_llm/root_cause_chain_{cond}_{type}_{sev}.md（Markdown 可读解释）。

用法（cd Code）：
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_llm_explanation --smoke   # 每类 1 条 FULL 验证
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_llm_explanation --limit 5 # 只跑前 5 条
  PYTHONPATH=. ../.venv/bin/python -m rca.v21.run_llm_explanation            # 全量 92 条
  --force 覆盖已有 llm 结果（默认跳过已生成且未降级的记录）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

from rca.rca_config_v21 import V21_OUTPUT
from rca.v21 import llm_explanation as LE
from core.utils import logger

CHAIN_DIR = V21_OUTPUT / "chains"
LLM_DIR = V21_OUTPUT / "chains_llm"


def _load_chains() -> List[Path]:
    return sorted(CHAIN_DIR.glob("root_cause_chain_*.json"))


def _write_back(rec: Dict, llm: Dict, chain_path: Path) -> None:
    rec["llm"] = llm
    with open(chain_path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2, default=str)


def _write_markdown(rec: Dict, llm: Dict, chain_path: Path) -> None:
    LLM_DIR.mkdir(parents=True, exist_ok=True)
    tpl = rec["template"]
    md_name = chain_path.name.replace(".json", ".md")
    md_path = LLM_DIR / md_name
    header = (
        f"# 根因分析报告（v2.1 R5 两层解释）\n\n"
        f"- 事故工况：{tpl['k']}（判定模板 {tpl['g']}）\n"
        f"- 条件：{rec['condition']}；轨迹：{rec['true']}/sev{rec['severity']}\n"
        f"- 故障指示器：{tpl['marker']}（事故信号，非根因）\n"
        f"- 生成方式：{'LLM（' + LE.LLM_MODEL + '）' if llm['llm_used'] else '代码模板降级（' + str(llm['degraded_reason']) + '）'}\n\n"
        "---\n\n"
    )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(header + llm["llm_explanation"] + "\n")


def _already_done(rec: Dict, force: bool) -> bool:
    if force:
        return False
    llm = rec.get("llm")
    return bool(llm and llm.get("llm_used"))


def run(smoke: bool = False, limit: Optional[int] = None, force: bool = False) -> Dict:
    paths = _load_chains()
    if smoke:
        # 每类取一条 FULL（low→high 组序内第一）
        # 文件名 root_cause_chain_{cond}_{true}_{sev}.json -> split("_")[4]=true
        picked: Dict[str, Path] = {}
        for p in paths:
            if "_FULL_" not in p.name:
                continue
            k = p.name.split("_")[4]
            picked.setdefault(k, p)
        paths = [picked[k] for k in ("LOCA", "SGATR", "FLB") if k in picked]
    if limit is not None:
        paths = paths[:limit]

    n_llm = n_degraded = n_skipped = 0
    for p in paths:
        rec = json.load(open(p, encoding="utf-8"))
        if _already_done(rec, force):
            n_skipped += 1
            continue
        llm = LE.generate_llm_explanation(rec)
        _write_back(rec, llm, p)
        _write_markdown(rec, llm, p)
        if llm["llm_used"]:
            n_llm += 1
        else:
            n_degraded += 1
            logger.warning(f"降级: {p.name} <- {llm['degraded_reason']}")

    summary = {
        "schema": "NPPAD-CausalTrace-v2.1-R5-llm",
        "model": LE.LLM_MODEL,
        "n_total": len(paths),
        "n_llm": n_llm,
        "n_degraded": n_degraded,
        "n_skipped": n_skipped,
        "api_key_present": bool(LE._get_api_key()),
    }
    logger.info(f"LLM 解释完成: {summary}")
    out = V21_OUTPUT / ("llm_explanation_summary_smoke.json" if smoke
                        else "llm_explanation_summary.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def main():
    ap = argparse.ArgumentParser(description="v2.1 R5 LLM 解释生成")
    ap.add_argument("--smoke", action="store_true", help="每类 1 条 FULL 验证")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 条")
    ap.add_argument("--force", action="store_true", help="覆盖已有 llm 结果")
    args = ap.parse_args()
    run(smoke=args.smoke, limit=args.limit, force=args.force)


if __name__ == "__main__":
    main()

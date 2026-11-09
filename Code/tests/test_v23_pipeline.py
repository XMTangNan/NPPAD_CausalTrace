"""v2.3 C 阶段流水线单测（小样本，产物落 tmp_path，不污染正式产物）。

覆盖验收点：
- C2：签名与溯源同 run_id；V23-STAT-001 单引擎下 n_consensus/n_conflict 为 None，
  consensus_available=false，共识来源写 "none:..."；
- C3：分组图 / 单类汇总图 / 全类总览生成，首屏含 run_id，内嵌边 consensus=null，
  图例为"稳定边（LiNGAM bootstrap ...）"且不出现"共识边"字样；
- C4：summary/diff 带 run_id 与输入 sha256；--run-id 不匹配直接报错；
- C5/C6：clip_sensitivity 产物存在，签名 meta 含 baseline_coverage；
- C7：同一命令跑两次（n_bootstrap=3）排名与稳定边数逐项一致。
"""
import json

import pytest

from rca.v21 import artifact
from rca.v21 import run_v23_end2end as E2E
from rca.v21 import run_v23_graphs as GR
from rca.v21 import severity_aggregation as SA


def _patch_output(monkeypatch, root):
    """把 artifact 的唯一产物根整体指向 tmp 目录（保持同名子目录）。"""
    root.mkdir(parents=True, exist_ok=True)
    subs = {}
    for name in list(artifact.V23_SUBDIRS):
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        subs[name] = d
    monkeypatch.setattr(artifact, "V23_SUBDIRS", subs)
    monkeypatch.setattr(artifact, "V23_OUTPUT", root)
    return subs


def _read(sub_dir, name):
    with open(sub_dir / name, encoding="utf-8") as f:
        return json.load(f)


def test_single_engine_fields_and_same_run_id(tmp_path, monkeypatch):
    subs = _patch_output(monkeypatch, tmp_path / "c1")
    run_id = E2E.main(["--classes", "LOCA", "--groups", "low", "--n-bootstrap", "5"])

    rc = _read(subs["tracing"], "root_candidates_LOCA_low.json")
    sig = _read(subs["signatures"], "signature_LOCA_low.json")

    # C2 同源：签名 / 溯源 / 拓扑 / 敏感性共用同一 run_id
    assert run_id.startswith("v23c-")
    assert rc["run_id"] == sig["run_id"] == run_id
    assert _read(subs["tracing"], "topology_LOCA_low.json")["run_id"] == run_id
    assert _read(subs["tracing"],
                 "clip_sensitivity_LOCA_low.json")["run_id"] == run_id

    # V23-STAT-001：单引擎共识字段必须为 null，不能是 0 或稳定边数
    assert rc["n_consensus"] is None
    assert rc["n_conflict"] is None
    assert rc["consensus_available"] is False
    assert rc["consensus_source"].startswith("none:")
    assert "不等同共识边" in rc["consensus_note"]
    assert rc["engine"] == "lingam"
    assert rc["n_bootstrap"] == 5

    # C5：截断证据（默认阈值不变）+ 条件数
    assert rc["b_clip"] == 5.0
    assert isinstance(rc["n_clipped_edges"], int)
    assert rc["n_clipped_edges"] == len(rc["clipped_edges"])
    assert rc["cond_IminusB"] is None or rc["cond_IminusB"] >= 1.0
    sens = _read(subs["tracing"], "clip_sensitivity_LOCA_low.json")
    assert [v["clip"] for v in sens["variants"]] == [3.0, 5.0, 10.0]
    assert all(0 <= v["rank_overlap_at_5"] <= 5 for v in sens["variants"])
    assert isinstance(sens["top1_stable"], bool)

    # 排名连续性 & 规模
    ranks = [c["rank"] for c in rc["root_candidates"]]
    assert ranks == list(range(1, len(ranks) + 1))
    assert 1 <= len(ranks) <= 5
    assert rc["n_nodes"] == len(sig["variables"]) == rc["gating"]["n_in_model"]

    # C6：baseline_coverage（实际/期望点数，按 t_last 截断）
    cov = sig["baseline_coverage"]
    assert cov["expected_points"] == 152
    assert cov["n_trajectories"] == len(cov["detail"]) >= 1
    assert all(r["n_points"] <= cov["expected_points"] for r in cov["detail"])

    # 缺数据/未选类：注册表全量如实登记，但绝不生成占位签名/榜单
    skipped = _read(subs["tracing"], "skipped_classes.json")
    codes = {e["code"]: e for e in skipped["entries"]}
    assert skipped["n_missing"] == 8
    assert "LOCAC" in codes and codes["LOCAC"]["status"] == "partial"
    assert codes["SLBIC"]["status"] == "data_missing"
    assert all("未选择" in codes[c]["reason"] for c in ("LOCAC", "SLBIC"))
    import glob
    assert not glob.glob(str(subs["signatures"] / "signature_LOCAC_*.json"))


def test_graphs_summary_and_run_id_gate(tmp_path, monkeypatch):
    subs = _patch_output(monkeypatch, tmp_path / "c2")
    run_id = E2E.main(["--classes", "LOCA", "--groups", "low,mid,high",
                       "--n-bootstrap", "3"])

    GR.main(["--run-id", run_id, "--classes", "LOCA"])
    for name in ("graph_LOCA_low.html", "graph_LOCA_mid.html", "graph_LOCA_high.html",
                 "graph_LOCA_summary.html", "graph_all_summary.html"):
        assert (subs["graphs"] / name).exists(), name

    txt = (subs["graphs"] / "graph_LOCA_low.html").read_text(encoding="utf-8")
    assert run_id in txt                     # 首屏 run_id
    assert "生成时间=" in txt
    assert "稳定边（LiNGAM bootstrap" in txt  # 单引擎图例口径
    assert "共识边" not in txt                # 禁止把稳定边称共识边
    assert "C3a 信息门控" in txt
    assert "96 变量" in txt

    blob = txt.split('<script id="graph-data" type="application/json">')[1]
    blob = blob.split("</script>")[0]
    data = json.loads(blob)
    assert data["run_id"] == run_id
    assert data["edges"], "传导子图不应为空"
    assert all(e["consensus"] is None for e in data["edges"])
    assert data["nodes"] and all(n["id"] for n in data["nodes"])

    # C4：汇总/差异同源 + 输入 sha256
    SA.main(["--run-id", run_id, "--classes", "LOCA"])
    summary = _read(subs["reports"], "summary_LOCA.json")
    diff = _read(subs["reports"], "diff_LOCA.json")
    assert summary["run_id"] == diff["run_id"] == run_id
    assert summary["cross_group"]["pairwise"]
    assert len(summary["inputs"]) == 6 and all("sha256" in i for i in summary["inputs"])
    assert len(diff["transitions"]) == 2
    assert diff["consensus_available"] is False

    # run_id 不一致必须报错（禁止混源）
    with pytest.raises(ValueError):
        SA.main(["--run-id", "v23c-19700101T000000", "--classes", "LOCA"])
    with pytest.raises(ValueError):
        GR.main(["--run-id", "v23c-19700101T000000", "--classes", "LOCA"])


def test_two_runs_are_reproducible(tmp_path, monkeypatch):
    def run_once(root):
        subs = _patch_output(monkeypatch, tmp_path / root)
        rid = E2E.main(["--classes", "LOCA", "--groups", "low",
                        "--n-bootstrap", "3", "--no-repro-check"])
        rc = _read(subs["tracing"], "root_candidates_LOCA_low.json")
        return rid, rc

    rid1, rc1 = run_once("r1")
    rid2, rc2 = run_once("r2")
    assert rid1 != rid2
    assert rc1["n_stable_edges"] == rc2["n_stable_edges"]
    assert ([c["variable"] for c in rc1["root_candidates"]]
            == [c["variable"] for c in rc2["root_candidates"]])
    # BLAS 多线程可能带来末位浮点差异：排名必须逐项一致，数值用相对容差比对
    assert ([c["total_effect"] for c in rc1["root_candidates"]]
            == pytest.approx([c["total_effect"] for c in rc2["root_candidates"]],
                             rel=1e-9, abs=1e-12))
    assert rc1["cond_IminusB"] == pytest.approx(rc2["cond_IminusB"], rel=1e-9)

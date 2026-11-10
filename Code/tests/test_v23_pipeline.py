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
import shutil
from pathlib import Path

import pytest

from causalrca.io import artifact
from causalrca.cli import run_v23_end2end as E2E
from causalrca.reporting import run_v23_graphs as GR
from causalrca.causal import severity_aggregation as SA


class _RunDirs:
    """延迟解析 run 根下的子目录（run 根 = <tmp>/<当前绑定 run_id>）。"""

    def __init__(self, root):
        self.root = Path(root)

    def __getitem__(self, name):
        d = Path(artifact.run_root()) / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def values(self):
        return [self[n] for n in artifact.V23_SUBDIR_NAMES]


def _patch_output(monkeypatch, root, run_id=None):
    """把唯一产物根指向 tmp（run 根 = <tmp>/<run_id>），run_id 固定便于预置输入。

    A 阶段产物（variable_sets/）从 latest run 只读拷贝，避免单测依赖真实 output 的写入痕迹。
    """
    latest = artifact.latest_run()
    src = artifact.run_path(latest) / "variable_sets" if latest else None
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    run_id = run_id or f"v23c-{root.name}"
    monkeypatch.setattr(artifact, "V23_RUNS", root)
    monkeypatch.setattr(artifact, "V23_LATEST", root / "latest")
    monkeypatch.setattr(artifact, "new_run_id",
                        lambda prefix="v23c": f"{prefix}-{root.name}")
    monkeypatch.setattr(artifact, "_RUN_ID", run_id)
    if src and src.exists():
        dst = root / run_id / "variable_sets"
        if True:
            dst.mkdir(parents=True, exist_ok=True)
            for f in sorted(src.glob("*.json")):
                shutil.copy2(f, dst / f.name)
    return _RunDirs(root)


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

    # V23-ALGO-001：弃权门控（默认 B_CLIP=5 不变，越界才降级）
    stab = rc["stability"]
    assert stab["thresholds"] == {"cond_after_clip_max": 1e4, "clip_ratio_max": 0.35}
    assert stab["label"] in ("数值稳定", "数值不稳定、结论降级")
    assert sig["auxiliary"]["stability"]["label"] == stab["label"]
    assert 0.0 <= stab["clip_ratio"] <= 1.0
    assert stab["n_clipped"] == rc["n_clipped_edges"]

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
    # 混源门控：把本 run 产物原样复制到另一个 run 目录（文件名相同、内容 run_id 不同）
    decoy = "v23c-19700101T000000"
    for sub in ("signatures", "tracing"):
        d = artifact.run_path(decoy) / sub
        d.mkdir(parents=True, exist_ok=True)
        for f in subs[sub].glob("*.json"):
            shutil.copy2(f, d / f.name)
    with pytest.raises(ValueError):
        SA.main(["--run-id", decoy, "--classes", "LOCA"])
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


def test_stability_gate_thresholds():
    """V23-ALGO-001：越界才降级，B_CLIP 不改。"""
    from causalrca.causal import source_tracing as ST

    ok = ST.stability_gate({"cond_IminusB_after_clip": 3643.0,
                            "n_clipped_edges": 26}, 91)
    assert ok["degraded"] is False and ok["label"] == "数值稳定"
    assert ok["clip_ratio"] == pytest.approx(26 / 91, abs=1e-4)

    bad_cond = ST.stability_gate({"cond_IminusB_after_clip": 10001.0,
                                  "n_clipped_edges": 0}, 91)
    assert bad_cond["degraded"] is True and bad_cond["reasons"]

    bad_ratio = ST.stability_gate({"cond_IminusB_after_clip": 10.0,
                                   "n_clipped_edges": 33}, 91)
    assert bad_ratio["degraded"] is True
    assert "0.363" in bad_ratio["reasons"][0]


def test_variable_order_permutation_invariance():
    """V23-CODE-023：置换输入变量顺序后稳定边集合与前沿不变。"""
    import numpy as np
    from causalrca.causal.signatures import bootstrap_lingam_template

    rng = np.random.default_rng(7)
    n = 600
    a = rng.normal(size=n)
    b = 0.9 * a + rng.normal(scale=0.6, size=n)
    c = 0.8 * b + rng.normal(scale=0.6, size=n)
    d = 0.7 * c + rng.normal(scale=0.6, size=n)
    X = np.column_stack([a, b, c, d])
    blocks = [X[:300], X[300:]]
    variables = ["A", "B", "C", "D"]
    kw = dict(marker="A",
              ontology={"forbidden": [], "competitive": [], "allowed": []},
              mu={v: 0.0 for v in variables},
              sigma={v: 1.0 for v in variables},
              median_platform=1.0, n_bootstrap=10, seed=11,
              use_ontology=False)

    base = bootstrap_lingam_template(blocks, variables, **kw)
    perm = [3, 1, 0, 2]
    other = bootstrap_lingam_template(
        [blk[:, perm] for blk in blocks],
        [variables[i] for i in perm], **kw)

    def edge_set(sig):
        return {(e["from"], e["to"]) for e in sig["stable_edges"]}

    assert len(base["stable_edges"]) > 0
    assert base["variables"] == other["variables"] == variables
    assert edge_set(base) == edge_set(other)
    assert base["frontier"] == other["frontier"]


def test_bootstrap_fits_on_canonical_order(monkeypatch):
    """V23-CODE-023：无论调用方传入何种列序，实际喂给 LiNGAM 的矩阵列序一致。"""
    import numpy as np
    import lingam
    from causalrca.causal.signatures import bootstrap_lingam_template

    seen = []
    Real = lingam.DirectLiNGAM

    class Spy(Real):
        def fit(self, X):
            seen.append(np.array(X, copy=True))
            return super().fit(X)

    monkeypatch.setattr(lingam, "DirectLiNGAM", Spy)

    rng = np.random.default_rng(3)
    n = 200
    # 每列加不同偏置，便于核对列序（mu=0/sigma=1 时偏置原样保留）
    X = np.column_stack([rng.normal(size=n) + 10 * k for k in range(4)])
    variables = ["VOID", "P", "TAVG", "WLD"]  # 字母序 = P, TAVG, VOID, WLD
    kw = dict(marker="P", ontology={"forbidden": [], "competitive": [], "allowed": []},
              mu={v: 0.0 for v in variables}, sigma={v: 1.0 for v in variables},
              median_platform=1.0, n_bootstrap=2, seed=5, use_ontology=False)

    bootstrap_lingam_template([X[:100], X[100:]], variables, **kw)
    perm = [2, 0, 3, 1]
    bootstrap_lingam_template([X[:100][:, perm], X[100:][:, perm]],
                              [variables[i] for i in perm], **kw)

    canonical = [variables.index(v) for v in sorted(variables)]
    for z in seen:  # 两次调用的列序都等于字母序（按列偏置核对）
        assert z[:, 0].mean() == pytest.approx(10 * canonical[0], abs=0.5)
        assert z[:, 3].mean() == pytest.approx(10 * canonical[3], abs=0.5)


# ==================== S1：单产物根（v2.3 registry 优先，v2.1 只读回退）====================
def test_stage_a_snapshots_three_frozen_inputs(tmp_path, monkeypatch):
    """A 阶段把 split_manifest / shared_baseline / marker_scale 写进 v2.3 registry。"""
    from causalrca.cli import run_variable_selector as VS

    subs = _patch_output(monkeypatch, tmp_path / "s1a")
    VS.snapshot_frozen_inputs("v23a-testrun")

    for name in ("split_manifest", "shared_baseline", "marker_scale"):
        assert (subs["registry"] / f"{name}.json").exists(), name
    baseline = _read(subs["registry"], "shared_baseline.json")
    assert baseline["run_id"] == "v23a-testrun"
    assert baseline["mu"] and baseline["sigma"]
    assert baseline["schema"] == artifact.SCHEMA
    # 取值零变化：与 v2.1 冻结文件逐项一致
    v21 = E2E.D.load_json("shared_baseline.json")
    assert baseline["mu"] == v21["mu"] and baseline["sigma"] == v21["sigma"]
    scale = _read(subs["registry"], "marker_scale.json")
    assert scale["median_platform"] == E2E.D.load_json("marker_scale.json")["median_platform"]


def test_load_inputs_prefers_v23_registry(tmp_path, monkeypatch):
    """C 阶段读 v2.3 registry（不再回读 v2.1 根）。"""
    subs = _patch_output(monkeypatch, tmp_path / "s1b")
    subs["registry"].joinpath("split_manifest.json").write_text(
        json.dumps({"classes": {"LOCA": {"groups": {"low": {
            "train_template_severities": [99]}}}}}), encoding="utf-8")
    subs["registry"].joinpath("shared_baseline.json").write_text(
        json.dumps({"mu": {"X": 1.0}, "sigma": {"X": 2.0}}), encoding="utf-8")
    subs["registry"].joinpath("marker_scale.json").write_text(
        json.dumps({"median_platform": {"LOCA": 2.0}}), encoding="utf-8")

    inputs = E2E.load_inputs()
    assert inputs["manifest"]["classes"]["LOCA"]["groups"]["low"][
        "train_template_severities"] == [99]
    assert inputs["baseline"]["mu"] == {"X": 1.0}
    assert inputs["scale"]["median_platform"]["LOCA"] == 2.0


def test_load_inputs_falls_back_to_v21(tmp_path, monkeypatch):
    """v2.3 registry 缺文件时才回退 v2.1 只读输入（旧产物兼容）。"""
    _patch_output(monkeypatch, tmp_path / "s1c")
    inputs = E2E.load_inputs()
    assert "classes" in inputs["manifest"]
    assert inputs["baseline"]["mu"] and inputs["baseline"]["sigma"]
    assert "median_platform" in inputs["scale"]

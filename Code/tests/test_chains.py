"""13.10 Phase R5 单测：合成签名/合成观测，不依赖数据文件（方向统计引真实 knowledge 表）。

覆盖冻结规格：
1) BFS 最短路径 + grounded 独立校验（注入坏边 -> ungrounded 且不进 chain_text）；
2) RRF 数值/排序/并列稳定；
3) 爆炸闸门（severity_flipped 或 πL<0.70 不得居 rank_L 首位）；
4) outside_reach 不进链；
5) 文本纪律：禁“根因是 WLR/WTRA/WFLB”、禁“已证实因果”；
另含 §4.2 方向一致率统计与 cf_fail 标注。

可直接 python tests/test_chains.py 运行（不依赖 pytest）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rca.rca_config_v21 import MARKERS, GROUP_ORDER
from rca.v21 import chains as C
from rca.v21.pipeline import fit_minmax


# ==================== 合成夹具 ====================
def _edge(u, v, f=1.0, d=1.0):
    return {"from": u, "to": v, "f_e": f, "dir_e": d, "weight_median": 0.1}


def _calibration(k):
    return {"tau": 0.5, "delta": 0.1,
            "minmax": {"E2": {k: fit_minmax([0.0, 1.0])},
                       "E3_path": {k: fit_minmax([0.0, 1.0])}}}


def _res(k, A, condition="FULL", e3p=0.7, e2=0.8):
    per_tpl, class_raw = {}, {}
    for g in GROUP_ORDER:
        per_tpl[f"{k}_{g}"] = {"e2": e2, "e3_base": 0.6, "e3_path": e3p}
    class_raw[k] = {"e2": e2, "e3_base": 0.6, "e3_path": e3p}
    return {
        "A": list(A), "n_activation": len(A),
        "per_template": per_tpl, "class_raw": class_raw,
        "class_norm": {"e2": {k: e2}, "e3_path": {k: e3p}},
        "e1": ({k: 1.0} if condition == "FULL" else None),
        "fusion": {k: 0.9}, "ranking": [[k, 0.9]],
        "pred": k, "argmax_class": k,
        "s1": 0.9, "s2": 0.2, "margin": 0.7,
        "accepted": True, "abstain_reason": None,
    }


def _loca_synthetic_sig():
    """WLR→VOL→LVPZ；P 不在 reach（测 outside_reach）；consensus 带 conflict。"""
    return {
        "marker": "WLR",
        "stable_edges": [_edge("WLR", "VOL"), _edge("VOL", "LVPZ")],
        "cL": {"VOL": {"cL": -4.0, "piL": 1.0},
               "LVPZ": {"cL": 0.2, "piL": 1.0},
               "P": {"cL": 6.0, "piL": 1.0}},
        "cA": {"VOL": {"cA": -50.0, "piA": 1.0, "estimable": True},
               "LVPZ": {"cA": -1.0, "piA": 0.9, "estimable": True},
               "P": {"cA": -10.0, "piA": 0.86, "estimable": True}},
        "flags": {"severity_flipped_marker_edges": []},
    }


# ==================== 1. BFS 最短路径 + grounded ====================
def test_bfs_shortest_path_and_grounding():
    edges = [_edge("M", "A"), _edge("M", "B"), _edge("A", "C"),
             _edge("B", "C"), _edge("C", "D")]
    idx = C.stable_edge_index(edges)
    bfs = C.bfs_shortest_paths(edges, "M")
    # 等长最短路径按变量名稳定取 A 侧
    assert C.shortest_path(bfs, "C") == ["M", "A", "C"]
    assert C.shortest_path(bfs, "D") == ["M", "A", "C", "D"]
    assert C.shortest_path(bfs, "ZZ") is None

    # 独立忠实性校验：BFS 路径全 grounded；注入坏边被检出
    ann_ok = C.validate_path_edges(["M", "A", "C"], idx)
    assert all(a["grounded"] for a in ann_ok)
    bad = C.validate_path_edges(["M", "A", "BAD"], idx)
    assert bad[-1]["grounded"] is False and bad[-1]["dir_e"] is None

    # dir_e<0.90 标 uncertain_direction（stable 表外的防御性校验）
    weak = C.stable_edge_index([_edge("M", "A", d=0.85)])
    assert C.validate_path_edges(["M", "A"], weak)[0]["uncertain_direction"]


def test_ungrounded_edge_excluded_from_text():
    sig = _loca_synthetic_sig()
    res = _res("LOCA", ["VOL", "LVPZ", "P"])
    d_map = {"VOL": -20.0, "LVPZ": -2.0, "P": -5.0}
    rec = C.build_chain(condition="FULL", true_k="LOCA", severity=70,
                        pred="LOCA", accepted=True, res=res, d_map=d_map,
                        sig=sig, g="high",
                        cf_obj={"edges": {}, "template_gcf": 1.0},
                        consensus_obj={"classification": {}},
                        calibration=_calibration("LOCA"))
    # 正常 BFS 路径无 ungrounded
    assert rec["flags"]["ungrounded_edges"] == []
    node = next(n for n in rec["ranked_nodes"] if n["node"] == "LVPZ")
    # 注入坏边（模拟路径来源被污染）：独立校验必须捕获
    idx = C.stable_edge_index(sig["stable_edges"])
    node["path"] = ["WLR", "VOL", "BAD", "LVPZ"]
    node["path_edges"] = C.annotate_path_edges(node["path"], idx)
    node["grounded"] = all(a["grounded"] for a in node["path_edges"])
    rec["flags"] = C._aggregate_flags(rec["ranked_nodes"], rec["outside_reach"],
                                      "WLR")
    text = C.render_chain_text(rec)
    assert any(a["edge"] == "VOL->BAD" and not a["grounded"]
               for a in node["path_edges"])
    assert "BAD" not in text and "VOL->BAD" not in text  # 坏边不进正文
    assert "未落地边" in text                            # 有显式留痕说明


# ==================== 2. RRF 数值 / 排序 / 并列 ====================
def test_rrf_numeric_and_ties():
    assert abs(C.rrf_score(1, 1) - (1 / 61 + 1 / 61)) < 1e-12
    assert abs(C.rrf_score(2, 3) - (1 / 62 + 1 / 63)) < 1e-12
    cL = {"A": {"cL": 1.0, "piL": 1.0}, "B": {"cL": 2.0, "piL": 1.0},
          "X": {"cL": 100.0, "piL": 0.5}}           # X 低 π -> rank_L 末档
    cA = {"A": {"cA": 10.0, "piA": 1.0, "estimable": True},
          "B": {"cA": 5.0, "piA": 1.0, "estimable": True},
          "X": {"cA": 1000.0, "piA": 1.0, "estimable": True}}
    r = C.rank_candidates(["A", "B", "X"], cL, cA, "M")
    assert r["B"]["rank_L"] == 1 and r["A"]["rank_L"] == 2
    assert r["X"]["rank_L"] == 3 and r["X"]["magnitude_unstable"]
    assert r["X"]["rank_A"] == 1                      # 爆炸闸门只作用于 rank_L
    assert r["A"]["rank_A"] == 2 and r["B"]["rank_A"] == 3
    for y in ("A", "B", "X"):
        assert abs(C.rrf_score(r[y]["rank_L"], r[y]["rank_A"]) - 0) >= 0
        assert abs(C.rrf_score(r[y]["rank_L"], r[y]["rank_A"])
                   - (1 / (60 + r[y]["rank_L"]) + 1 / (60 + r[y]["rank_A"]))) < 1e-12

    # 并列：|cL| 相同 -> 变量名升序，确定性
    tie = C.rank_candidates(
        ["Z", "Y"], {"Z": {"cL": 1.0, "piL": 1.0}, "Y": {"cL": 1.0, "piL": 1.0}},
        {"Z": {"cA": 1.0, "piA": 1.0, "estimable": True},
         "Y": {"cA": 1.0, "piA": 1.0, "estimable": True}}, "M")
    assert tie["Y"]["rank_L"] == 1 and tie["Z"]["rank_L"] == 2
    assert tie["Y"]["rank_A"] == 1 and tie["Z"]["rank_A"] == 2


# ==================== 3. 爆炸闸门 ====================
def test_explosion_gate_flip_and_low_pi():
    marker = "WLR"
    cL = {"VOL": {"cL": -4.0, "piL": 1.0},      # 正常
          "P": {"cL": 5000.0, "piL": 1.0},      # 跨严重度翻转
          "TAVG": {"cL": -3.0, "piL": 0.6}}     # 低 π
    cA = {v: {"cA": -1.0, "piA": 0.9, "estimable": True}
          for v in ("VOL", "P", "TAVG")}
    r = C.rank_candidates(["VOL", "P", "TAVG"], cL, cA, marker,
                          flipped_edges=["WLR->P"])
    # 5000 的爆炸量级与低 π 都不得居 rank_L 首位
    assert r["P"]["rank_L"] != 1 and r["P"]["magnitude_unstable"]
    assert r["TAVG"]["rank_L"] != 1 and r["TAVG"]["magnitude_unstable"]
    assert r["VOL"]["rank_L"] == 1 and not r["VOL"]["magnitude_unstable"]
    assert {r["P"]["rank_L"], r["TAVG"]["rank_L"]} == {2, 3}

    # 端到端：翻转节点的链文本必须带量级不稳标注
    edges = [_edge("WLR", "VOL"), _edge("WLR", "P")]
    sig = {"marker": "WLR", "stable_edges": edges, "cL": cL, "cA": cA,
           "flags": {"severity_flipped_marker_edges": ["WLR->P"]}}
    res = _res("LOCA", ["VOL", "P"])
    rec = C.build_chain(condition="FULL", true_k="LOCA", severity=100,
                        pred="LOCA", accepted=True, res=res,
                        d_map={"VOL": -1.0, "P": -3.0}, sig=sig, g="high",
                        cf_obj={"edges": {}, "template_gcf": 1.0},
                        consensus_obj={"classification": {}},
                        calibration=_calibration("LOCA"))
    assert "P" in rec["flags"]["magnitude_unstable_nodes"]
    assert "量级不稳" in rec["chain_text"]


# ==================== 4. outside_reach 不进链 ====================
def test_outside_reach_not_in_chain():
    sig = _loca_synthetic_sig()
    res = _res("LOCA", ["VOL", "LVPZ", "P"])
    rec = C.build_chain(condition="FULL", true_k="LOCA", severity=70,
                        pred="LOCA", accepted=True, res=res,
                        d_map={"VOL": -20.0, "LVPZ": -2.0, "P": -5.0},
                        sig=sig, g="high",
                        cf_obj={"edges": {}, "template_gcf": 1.0},
                        consensus_obj={"classification": {}},
                        calibration=_calibration("LOCA"))
    ranked = {n["node"] for n in rec["ranked_nodes"]}
    assert ranked == {"VOL", "LVPZ"}
    assert [o["variable"] for o in rec["outside_reach"]] == ["P"]
    assert rec["outside_reach"][0]["entity"]["subsystem"] == "一回路热工"
    assert "P" in rec["chain_text"]  # 出现在不确定性段
    # P 不作为传播链路径节点（路径只有 WLR/VOL/LVPZ）
    chain_section = rec["chain_text"].split("【不确定性】")[0]
    assert "WLR → P" not in chain_section
    # 链按 RRF 降序（VOL 两引擎都强）
    assert rec["ranked_nodes"][0]["node"] == "VOL"


# ==================== 5. 文本纪律（三 marker 全名断言）====================
def test_text_discipline_all_markers():
    for k, marker in MARKERS.items():
        sig = {"marker": marker,
               "stable_edges": [_edge(marker, "VOL")],
               "cL": {"VOL": {"cL": -4.0, "piL": 1.0}},
               "cA": {"VOL": {"cA": -40.0, "piA": 1.0, "estimable": True}},
               "flags": {"severity_flipped_marker_edges": []}}
        res = _res(k, ["VOL"])
        rec = C.build_chain(condition="FULL", true_k=k, severity=50, pred=k,
                            accepted=True, res=res, d_map={"VOL": -10.0},
                            sig=sig, g="high",
                            cf_obj={"edges": {}, "template_gcf": 1.0},
                            consensus_obj={"classification": {}},
                            calibration=_calibration(k))
        text = rec["chain_text"]
        for bad in (f"根因是{marker}", f"根因是 {marker}"):
            assert bad not in text, (k, bad)
        assert "已证实因果" not in text
        assert f"根因：{C.ROOT_CAUSE_NAME[k]}" in text
        assert "传播链" in text and "稳定识别出的有向关系" in text
        assert rec["llm_used"] is False and rec["code_template_fallback"] is True

    # MASKED 文本：E1 不适用，不报 marker 激活为证据外的违规措辞
    sig = _loca_synthetic_sig()
    res = _res("LOCA", ["VOL"], condition="MASKED")
    rec = C.build_chain(condition="MASKED", true_k="LOCA", severity=70,
                        pred="LOCA", accepted=True, res=res,
                        d_map={"VOL": -20.0}, sig=sig, g="high",
                        cf_obj={"edges": {}, "template_gcf": 1.0},
                        consensus_obj={"classification": {}},
                        calibration=_calibration("LOCA"))
    assert "E1=不适用（MASKED 条件 marker 已掩蔽）" in rec["chain_text"]


# ==================== 6. §4.2 冻结关系方向一致率 + 竞争边豁免 ====================
def test_frozen_direction_consistency():
    sig = _loca_synthetic_sig()
    # VOL cA 负（与冻结 WLR→VOL↓ 一致）；LVPZ 人为造正（与冻结 ↓ 不一致）
    sig["cA"]["LVPZ"] = {"cA": 2.0, "piA": 0.9, "estimable": True}
    res = _res("LOCA", ["VOL", "LVPZ", "P"])
    rec = C.build_chain(condition="FULL", true_k="LOCA", severity=70,
                        pred="LOCA", accepted=True, res=res,
                        d_map={"VOL": -20.0, "LVPZ": 2.0, "P": -5.0},
                        sig=sig, g="high",
                        cf_obj={"edges": {}, "template_gcf": 1.0},
                        consensus_obj={"classification": {}},
                        calibration=_calibration("LOCA"))
    stats = C.direction_consistency([rec])["LOCA"]
    assert stats["n"] == 2 and stats["n_consistent"] == 1
    assert stats["n_inconsistent"] == 1 and abs(stats["rate"] - 0.5) < 1e-12
    case = stats["inconsistent_cases"][0]
    assert case["variable"] == "LVPZ" and case["chain_sign"] == +1
    # P 是 WLR↔P 双向竞争：outside_reach 且不在冻结表 -> 不计不一致
    assert stats["n_noncomparable"] == 0

    # cA/cL 都不可判 -> noncomparable，不进 n
    sig2 = {"marker": "WLR",
            "stable_edges": [_edge("WLR", "VOL")],
            "cL": {"VOL": {"cL": 0.0, "piL": 0.0}},
            "cA": {"VOL": {"cA": 0.0, "piA": 0.0, "estimable": False}},
            "flags": {}}
    res2 = _res("LOCA", ["VOL"])
    rec2 = C.build_chain(condition="MASKED", true_k="LOCA", severity=5,
                         pred="LOCA", accepted=True, res=res2,
                         d_map={"VOL": 0.0}, sig=sig2, g="low",
                         cf_obj={"edges": {}, "template_gcf": 1.0},
                         consensus_obj={"classification": {}},
                         calibration=_calibration("LOCA"))
    stats2 = C.direction_consistency([rec2])["LOCA"]
    assert stats2["n"] == 0 and stats2["rate"] is None
    assert stats2["n_noncomparable"] == 1


# ==================== 7. 边标注：conflict/single/flipped/competitive + cf_fail ====================
def test_edge_and_cf_annotations():
    edges = [_edge("WLR", "VOL"), _edge("WLR", "P")]  # WLR->P=双向竞争边
    sig = {"marker": "WLR", "stable_edges": edges,
           "cL": {"VOL": {"cL": -4.0, "piL": 1.0},
                  "P": {"cL": 6.0, "piL": 1.0}},
           "cA": {"VOL": {"cA": -40.0, "piA": 1.0, "estimable": True},
                  "P": {"cA": -10.0, "piA": 0.86, "estimable": True}},
           "flags": {"severity_flipped_marker_edges": []}}
    consensus = {"classification": {
        "WLR->VOL": {"label": "consensus", "n_same": 10, "n_rev": 0},
        "WLR->P": {"label": "conflict", "n_same": 2, "n_rev": 8}}}
    cf = {"template_gcf": 1.0, "edges": {
        "VOL": {"agree": 0.5, "gCF": 0.5, "flags": ["cf_fail"]}}}
    res = _res("LOCA", ["VOL", "P"])
    rec = C.build_chain(condition="FULL", true_k="LOCA", severity=90,
                        pred="LOCA", accepted=True, res=res,
                        d_map={"VOL": -20.0, "P": -6.0}, sig=sig, g="high",
                        cf_obj=cf, consensus_obj=consensus,
                        calibration=_calibration("LOCA"))
    assert "WLR->P" in rec["flags"]["consensus_conflict_edges"]
    assert "WLR->P" in rec["flags"]["competitive_edges"]
    assert rec["flags"]["cf_fail_nodes"] == ["VOL"]
    vol = next(n for n in rec["ranked_nodes"] if n["node"] == "VOL")
    assert vol["cf_support"]["gCF"] == 0.5 and vol["cf_support"]["agree"] == 0.5
    text = rec["chain_text"]
    assert "两引擎方向冲突" in text and "竞争边" in text
    assert "Alg3 反事实重演未通过" in text

    # single_lingam 标注
    consensus2 = {"classification": {"WLR->VOL": {"label": "single_lingam"}}}
    ann = C.annotate_path_edges(["WLR", "VOL"], C.stable_edge_index(edges),
                                consensus2["classification"], [])
    assert ann[0]["consensus_single"] and not ann[0]["consensus_conflict"]


# ==================== 8. 汇总计数 ====================
def test_summary_counts():
    sig = _loca_synthetic_sig()
    res = _res("LOCA", ["VOL", "LVPZ", "P"])
    rec = C.build_chain(condition="FULL", true_k="LOCA", severity=70,
                        pred="LOCA", accepted=True, res=res,
                        d_map={"VOL": -20.0, "LVPZ": -2.0, "P": -5.0},
                        sig=sig, g="high",
                        cf_obj={"edges": {}, "template_gcf": 1.0},
                        consensus_obj={"classification": {}},
                        calibration=_calibration("LOCA"))
    s = C.summarize_chains([rec], {"FULL": 1, "MASKED": 0})
    assert s["ungrounded_edges_total"] == 0
    assert s["chains_emitted"]["FULL"] == 1
    assert s["outside_reach_frequency"].get("P") == 1
    assert s["frozen_relation_direction_rate_LOCA"] is not None
    assert s["n_LOCA"] >= 1


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n全部 {len(fns)} 个 13.10 chains 单测通过")


if __name__ == "__main__":
    _run_all()

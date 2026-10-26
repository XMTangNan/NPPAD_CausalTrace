"""
Phase 1：LiNGAM 基线 + 矩阵索引方向核对。
关键：确认 B[i,j] = x_j → x_i 的直接效应，T=(I-B)^{-1}[i,j] = x_j 对 x_i 的总效应。
"""
import numpy as np
from typing import Dict, List

from core.utils import logger, log_error


def verify_matrix_index() -> Dict:
    """两变量 + 链式合成数据核对矩阵索引方向。
    返回核对结果字典。"""
    rng = np.random.default_rng(123)
    results = {}

    # ---- 两变量：X → Y，Y = 0.8*X + noise ----
    n = 2000
    X = rng.laplace(0, 1, n)
    Y = 0.8 * X + rng.laplace(0, 0.5, n)
    data = np.column_stack([X, Y])

    from lingam import DirectLiNGAM
    model = DirectLiNGAM()
    model.fit(data)
    B = model.adjacency_matrix_  # B[i,j] = effect of x_j on x_i

    # X 是 index 0, Y 是 index 1
    # 预期 B[1, 0] ≈ 0.8 (X→Y), B[0, 1] ≈ 0
    results["two_var"] = {
        "B": B.tolist(),
        "B[Y,X] (X→Y direct)": float(B[1, 0]),
        "B[X,Y] (Y→X direct)": float(B[0, 1]),
        "expected_XtoY": 0.8,
        "pass_direction": abs(B[1, 0]) > abs(B[0, 1]) and B[1, 0] > 0.5,
    }

    # 总效应 T = (I - B)^{-1}
    T = np.linalg.inv(np.eye(2) - B)
    results["two_var"]["T[Y,X] (X→Y total)"] = float(T[1, 0])
    results["two_var"]["T_total_equals_direct"] = np.isclose(T[1, 0], B[1, 0], atol=0.1)

    # ---- 链式：X → Z → Y ----
    # Y = 0.5*Z + ny, Z = 0.7*X + nz
    X = rng.laplace(0, 1, n)
    Z = 0.7 * X + rng.laplace(0, 0.5, n)
    Y = 0.5 * Z + rng.laplace(0, 0.5, n)
    data3 = np.column_stack([X, Z, Y])

    model3 = DirectLiNGAM()
    model3.fit(data3)
    B3 = model3.adjacency_matrix_
    T3 = np.linalg.inv(np.eye(3) - B3)

    # X=0, Z=1, Y=2
    # 总效应 X→Y 应≈ 0.7*0.5 = 0.35 (间接路径)
    indirect_expected = 0.7 * 0.5
    results["chain"] = {
        "B": B3.tolist(),
        "B[Z,X] (X→Z)": float(B3[1, 0]),
        "B[Y,Z] (Z→Y)": float(B3[2, 1]),
        "B[Y,X] (direct X→Y)": float(B3[2, 0]),
        "T[Y,X] (total X→Y)": float(T3[2, 0]),
        "expected_total_XtoY": indirect_expected,
        "pass_total_equals_indirect": abs(T3[2, 0] - indirect_expected) < 0.15,
    }

    results["summary"] = (
        f"两变量方向核对: {'通过' if results['two_var']['pass_direction'] else '失败'}; "
        f"总效应=直接效: {'通过' if results['two_var']['T_total_equals_direct'] else '失败'}; "
        f"链式总效应=间接积: {'通过' if results['chain']['pass_total_equals_indirect'] else '失败'}"
    )
    logger.info(results["summary"])
    return results


def fit_lingam(data: np.ndarray) -> np.ndarray:
    """拟合 DirectLiNGAM，返回邻接矩阵 B（B[i,j] = x_j→x_i 直接效应）。"""
    from lingam import DirectLiNGAM
    model = DirectLiNGAM()
    model.fit(data)
    return model.adjacency_matrix_


def adjacency_to_edges(B: np.ndarray, variables) -> list:
    """将邻接矩阵转为边列表 [{from, to, weight}]，仅非零边。"""
    edges = []
    n = len(variables)
    for i in range(n):
        for j in range(n):
            if i != j and abs(B[i, j]) > 1e-10:
                edges.append({"from": variables[j], "to": variables[i], "weight": float(B[i, j])})
    return edges


if __name__ == "__main__":
    res = verify_matrix_index()
    from core.utils import save_json
    save_json(res, "matrix_index_verification")
    print(res["summary"])

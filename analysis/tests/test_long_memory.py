"""驗證 analysis/long_memory.py —— 全部用已知性質的合成序列。"""

import numpy as np
import pytest

from analysis import long_memory as lm


def test_random_walk_has_vr_near_one():
    """隨機漫步的 VR(q) 應接近 1,且檢定不應顯著。"""
    rng = np.random.default_rng(42)
    r = rng.normal(0, 0.02, 4000)
    for q in (5, 10, 20):
        res = lm.variance_ratio_test(r, q)
        assert abs(res["vr"] - 1.0) < 0.12, f"q={q} VR={res['vr']:.3f}"
        assert res["p_value"] > 0.05, f"q={q} 對隨機漫步誤判為顯著"


def test_positively_autocorrelated_series_gives_vr_above_one():
    """AR(1) 正係數 → 動能 → VR > 1 且顯著。"""
    rng = np.random.default_rng(1)
    n, phi = 4000, 0.25
    e = rng.normal(0, 0.02, n)
    r = np.zeros(n)
    for t in range(1, n):
        r[t] = phi * r[t - 1] + e[t]
    res = lm.variance_ratio_test(r, 10)
    assert res["vr"] > 1.0 and res["p_value"] < 0.01


def test_mean_reverting_series_gives_vr_below_one():
    """AR(1) 負係數 → 均值回歸 → VR < 1 且顯著。"""
    rng = np.random.default_rng(2)
    n, phi = 4000, -0.25
    e = rng.normal(0, 0.02, n)
    r = np.zeros(n)
    for t in range(1, n):
        r[t] = phi * r[t - 1] + e[t]
    res = lm.variance_ratio_test(r, 10)
    assert res["vr"] < 1.0 and res["p_value"] < 0.01


def test_robust_test_does_not_flag_pure_volatility_clustering():
    """
    ⛔ 這是本模組存在的關鍵理由:GARCH 型波動度叢聚**沒有**報酬自相關,
    但同質變異數版本的 VR 檢定會把它誤判為顯著。穩健版本不該誤報。
    """
    rng = np.random.default_rng(5)
    n = 6000
    vol = np.zeros(n)
    vol[0] = 0.02
    r = np.zeros(n)
    for t in range(1, n):
        vol[t] = np.sqrt(1e-6 + 0.1 * r[t - 1] ** 2 + 0.85 * vol[t - 1] ** 2)
        r[t] = rng.normal(0, vol[t])
    res = lm.variance_ratio_test(r, 10)
    assert res["p_value"] > 0.01, f"波動度叢聚被誤判為動能(p={res['p_value']:.4f})"


def test_hurst_near_half_for_random_walk():
    rng = np.random.default_rng(3)
    x = np.cumsum(rng.normal(0, 1, 20000))
    assert abs(lm.hurst_exponent(x) - 0.5) < 0.06


def test_degenerate_inputs_return_nan_not_garbage():
    assert np.isnan(lm.variance_ratio(np.array([1.0, 2.0]), 10))
    assert np.isnan(lm.hurst_exponent(np.array([1.0, 2.0])))

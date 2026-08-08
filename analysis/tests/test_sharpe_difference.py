"""驗證 analysis/sharpe_difference.py —— 合成序列,已知答案。"""

import numpy as np
import pytest

from analysis import sharpe_difference as sd


def _garch(n, rng, omega=2e-6, alpha=0.10, beta=0.86, nu=4):
    """肥尾 + 波動叢聚的合成序列,近似 BTC 的實測性質(峰度約 19)。"""
    r = np.zeros(n)
    h = omega / (1 - alpha - beta)
    for t in range(n):
        h = omega + alpha * r[t - 1] ** 2 + beta * h if t else h
        r[t] = np.sqrt(h) * rng.standard_t(nu) / np.sqrt(nu / (nu - 2))
    return r


def test_identical_series_gives_zero_difference():
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.02, 2000)
    res = sd.sharpe_difference_test(r, r.copy(), n_boot=300)
    assert abs(res["delta"]) < 1e-12
    assert res["p_boot"] > 0.5


def test_gradient_matches_numerical_derivative():
    """delta method 的解析梯度必須與數值微分一致 —— 這是推導正確性的直接檢查。"""
    rng = np.random.default_rng(3)
    r1 = rng.normal(0.001, 0.02, 5000)
    r2 = rng.normal(0.0005, 0.025, 5000)

    m1, m2 = r1.mean(), r2.mean()
    g1, g2 = (r1**2).mean(), (r2**2).mean()

    def delta_of(v):
        a, b, c, d = v
        return a / np.sqrt(c - a**2) - b / np.sqrt(d - b**2)

    v0 = np.array([m1, m2, g1, g2])
    num = np.empty(4)
    for i in range(4):
        h = 1e-8 * max(abs(v0[i]), 1e-8)
        vp, vm = v0.copy(), v0.copy()
        vp[i] += h
        vm[i] -= h
        num[i] = (delta_of(vp) - delta_of(vm)) / (2 * h)

    ana = sd._gradient(r1, r2)
    assert np.allclose(ana, num, rtol=1e-4), f"解析 {ana} vs 數值 {num}"


def test_detects_genuinely_different_sharpe():
    rng = np.random.default_rng(1)
    n = 3000
    common = rng.normal(0, 0.02, n)
    r1 = 0.0020 + common          # 明顯較高的 Sharpe
    r2 = 0.0002 + common
    res = sd.sharpe_difference_test(r1, r2, n_boot=500)
    assert res["delta"] > 0 and res["p_boot"] < 0.01


def test_no_false_positive_on_fat_tailed_clustered_returns():
    """
    ⛔ 本模組存在的核心理由:兩個真實 Sharpe 相同、但肥尾且波動叢聚的序列,
    不該被判定為顯著不同。常態假設的檢定在這裡會誤報。
    """
    rng = np.random.default_rng(11)
    n = 3000
    c = _garch(n, rng)
    r1 = c + rng.normal(0, 0.005, n)
    r2 = c + rng.normal(0, 0.005, n)
    res = sd.sharpe_difference_test(r1, r2, n_boot=400)
    assert res["p_boot"] > 0.05, f"對真實相同的 Sharpe 誤報顯著(p={res['p_boot']:.4f})"


def test_hac_se_exceeds_iid_se_under_positive_autocorrelation():
    """
    報酬正自相關時,均值的長期變異數大於短期,HAC 標準誤必須大於樸素估計。

    ⚠️ 注意:單純的「波動叢聚」**不保證**這件事 —— HAC 修正的是梯度加權後的
    動差向量 (r, r²) 的自相關,不是波動度本身。實測 GARCH 序列兩者幾乎相等
    (0.01074 vs 0.01079),所以這裡用報酬自相關來測,那才是 HAC 明確該抓到的。
    """
    rng = np.random.default_rng(5)
    n, phi = 4000, 0.3
    e1, e2 = rng.normal(0, 0.02, n), rng.normal(0, 0.02, n)
    r1, r2 = np.zeros(n), np.zeros(n)
    for t in range(1, n):
        r1[t] = phi * r1[t - 1] + e1[t]
        r2[t] = phi * r2[t - 1] + e2[t]
    assert sd.sharpe_difference_se(r1, r2) > sd.sharpe_difference_se(r1, r2, lag=0)


def test_mismatched_lengths_rejected():
    with pytest.raises(ValueError):
        sd.sharpe_difference_test(np.zeros(100), np.zeros(90))


def test_minimum_detectable_difference_grows_with_trials():
    se = 0.02
    vals = [sd.minimum_detectable_difference(se, n) for n in (1, 5, 10, 50)]
    assert vals == sorted(vals)

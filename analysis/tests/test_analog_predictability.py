"""驗證 analysis/analog_predictability.py —— 合成序列,已知答案。"""

import numpy as np

from analysis import analog_predictability as ap


def test_random_walk_gives_ic_near_zero():
    """隨機漫步沒有類比結構,IC 應接近 0 且不顯著。"""
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.02, 4000)
    res = ap.analog_information_coefficient(r)
    assert abs(res["ic"]) < 0.08, f"隨機漫步卻得到 IC={res['ic']:.3f}"


def test_detects_genuine_analog_structure():
    """
    構造一個視窗尺度的動能結構:過去 20 日累積報酬會延續到未來 ——
    相似狀態確實導向相似結果,IC 應顯著為正。
    """
    rng = np.random.default_rng(1)
    n = 6000
    r = np.zeros(n)
    for t in range(20, n):
        state = r[t - 20 : t].sum()
        r[t] = 0.06 * state + rng.normal(0, 0.02)
    res = ap.analog_information_coefficient(r)
    assert res["ic"] > 0.1 and res["p_value"] < 0.01, f"未偵測到真實結構:{res}"


def test_embargo_prevents_self_prediction():
    """
    ⛔ 關鍵測試:不做 embargo 時,重疊視窗會讓樣本點用自己預測自己,
    IC 被虛增。embargo 應顯著降低這個虛假訊號。

    用隨機漫步:真值為 0,任何正 IC 都是重疊造成的假象。
    """
    rng = np.random.default_rng(7)
    r = rng.normal(0, 0.02, 4000)

    leaky = ap.analog_information_coefficient(r, window=20, embargo=0)
    clean = ap.analog_information_coefficient(r, window=20, embargo=20)
    assert abs(clean["ic"]) <= abs(leaky["ic"]) + 0.02, (
        f"embargo 未降低虛假訊號:leaky={leaky['ic']:.3f} clean={clean['ic']:.3f}"
    )


def test_grinold_law():
    assert abs(ap.implied_information_ratio(0.10, 100) - 1.0) < 1e-12
    assert ap.implied_information_ratio(-0.10, 100) == ap.implied_information_ratio(0.10, 100)
    assert np.isnan(ap.implied_information_ratio(0.1, 0))


def test_short_series_returns_nan_not_garbage():
    res = ap.analog_information_coefficient(np.random.default_rng(0).normal(0, 0.02, 100))
    assert np.isnan(res["ic"])

"""
驗證 analysis/trend_filter.py。

⚠️ **全部使用合成資料。** 本模組是為策略二假說準備的,依 CP-003 與
docs/proposals/exit-mechanism-hypothesis.md 的預先登錄紀律,在假說正式核准之前
不得對真實 BTC/ETH 資料評估其績效 —— 那會讓「先登錄、後檢驗」的順序失效。
這裡測的是**濾波器數學正確性**,不是策略績效。
"""

import numpy as np
import pytest

from analysis import trend_filter as tf


def test_lambda_cutoff_relation_is_monotonic_and_invertible():
    """截止週期越長 → 趨勢越平滑 → λ 越大、q 越小。"""
    lams = [tf.lambda_from_cutoff_period(p) for p in (30, 45, 60, 90)]
    assert lams == sorted(lams)
    for p in (30, 45, 60, 90):
        assert abs(tf.signal_to_noise_from_cutoff(p) * tf.lambda_from_cutoff_period(p) - 1) < 1e-12


def test_lambda_matches_closed_form_at_45_days():
    """p=45(取自 risk-policy.md 2.3 節的 time-stop)對應的 λ 固定下來,避免日後被無聲改動。"""
    assert abs(tf.lambda_from_cutoff_period(45) - 2639.6) < 0.5


def test_cutoff_period_below_nyquist_rejected():
    with pytest.raises(ValueError):
        tf.lambda_from_cutoff_period(2)


def test_recovers_known_constant_slope():
    """
    對一條「固定斜率 + 觀測雜訊」的序列,濾波後的斜率應收斂到真值。
    這是濾波器實作正確性的基本檢查。
    """
    rng = np.random.default_rng(0)
    n, true_slope = 600, 0.003
    y = true_slope * np.arange(n) + rng.normal(0, 0.02, n)

    out = tf.filter_trend(y, tf.signal_to_noise_from_cutoff(45))
    tail = out["slope"][-200:]
    assert abs(tail.mean() - true_slope) < 5e-4, f"斜率估計 {tail.mean():.5f} vs 真值 {true_slope}"


def test_detects_trend_reversal_within_reasonable_lag():
    """
    上升趨勢後轉為下降,斜率應在合理延遲內轉負。

    延遲是這個設計的**已知代價**:用整條路徑做推論換來的是更穩健的判定,
    但必然比「碰到回撤就出場」慢。這個測試把延遲的量級固定下來。
    """
    n_up, n_down = 400, 200
    y = np.concatenate([
        0.004 * np.arange(n_up),
        0.004 * n_up - 0.006 * np.arange(1, n_down + 1),
    ])

    sig = tf.trend_exit_signal(y, cutoff_period_days=45)
    assert not sig[:n_up].any(), "上升段不應出現出場訊號"

    first_exit = int(np.argmax(sig[n_up:]))
    assert sig[n_up:].any(), "下降段應偵測到趨勢反轉"
    assert first_exit < 60, f"反轉後 {first_exit} 根才偵測到,延遲過長"


def test_no_exit_signal_during_sustained_uptrend_with_noise():
    """
    帶雜訊的持續上升趨勢中不應頻繁誤觸發 —— 這正是固定 ATR 停損做不到的事
    (它只看回撤,分不出「趨勢中的正常震盪」與「趨勢結束」)。
    """
    rng = np.random.default_rng(7)
    n = 500
    y = 0.004 * np.arange(n) + rng.normal(0, 0.04, n)

    sig = tf.trend_exit_signal(y, cutoff_period_days=45)
    # 前 60 根濾波器仍在收斂,不列入計算
    assert sig[60:].mean() < 0.05, f"誤觸發率 {sig[60:].mean():.1%} 過高"


def test_filter_uses_no_future_information():
    """
    ⛔ 最關鍵的一項:時刻 t 的輸出只能依賴 y_1..y_t。

    驗證方式:把序列尾端整段換掉,前段每一點的輸出都不得改變。
    若誤用 Kalman smoother(RTS)而非 filter,這個測試會失敗 ——
    而那正是最容易發生、也最致命的前視偏誤來源。
    """
    rng = np.random.default_rng(11)
    n, cut = 400, 250
    y = 0.003 * np.arange(n) + rng.normal(0, 0.03, n)

    q = tf.signal_to_noise_from_cutoff(45)
    full = tf.filter_trend(y, q)["slope"][:cut]
    truncated = tf.filter_trend(y[:cut], q)["slope"]

    assert np.allclose(full, truncated, atol=1e-10), "尾端資料影響了前段輸出 → 有前視偏誤"


def test_exit_rule_is_scale_invariant():
    """
    θ=0.5 的判準化簡成 μ̂<0,不依賴 σ_v 的估計。
    對數價格整體平移(等價於改變計價單位)不應改變任何出場訊號。
    """
    rng = np.random.default_rng(3)
    y = 0.003 * np.arange(300) + rng.normal(0, 0.03, 300)

    a = tf.trend_exit_signal(y, 45)
    b = tf.trend_exit_signal(y + np.log(1234.5), 45)
    assert np.array_equal(a, b)


def test_empty_input_does_not_crash():
    out = tf.filter_trend(np.array([]), 1e-4)
    assert len(out["slope"]) == 0

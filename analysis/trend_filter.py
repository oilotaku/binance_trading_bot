"""
平滑趨勢狀態空間模型(local linear trend,σ_L²=0)的 Kalman 濾波,
用於「以趨勢自身的統計訊號判定趨勢結束」。

⚠️ 這是為**策略五假說**準備的工具,不是策略一的修補。策略一已依
docs/pass-b-results.md 判定未通過,且依 CP-003 §4.0.4 不得再調整後重測。

---
數學動機(完整推導見 docs/change-proposals/strategy-5-exit-mechanism-hypothesis.md):

固定 k×ATR 移動停損的出場決策**只用到「距峰值的回撤」這一個統計量**。
對於「趨勢是否仍在持續」這個推論問題,回撤不是充分統計量 —— 兩條回撤幅度相同
但路徑平滑度不同的價格序列,對趨勢持續性提供的證據強度完全不同,而固定停損
無法區分它們。

本模組改用整條路徑:把對數價格建模為

    y_t = L_t + ε_t,           ε_t ~ N(0, σ_v²)      觀測
    L_t = L_{t-1} + μ_{t-1}                          水準
    μ_t = μ_{t-1} + η_t,       η_t ~ N(0, σ_μ²)      斜率(趨勢)

Kalman 濾波對這個模型是**最適線性濾波器**,它給出斜率的後驗 μ̂_t 與其變異數。
出場條件因此有統計意義(「不再有證據支持趨勢向上」),而不是一個任意的波動度倍數。

只有一個結構參數:訊噪比 q = σ_μ²/σ_v²。已知平滑趨勢模型等價於 HP 濾波且
λ = 1/q,而 HP 增益 1/2 的截止週期 p 滿足 λ = 1/(16 sin⁴(π/p)) —— 因此
q 可以由「趨勢的時間尺度 p」這個有經濟意義的量直接指定,不需要從資料估計。
"""

from __future__ import annotations

import numpy as np


def lambda_from_cutoff_period(p_days: float) -> float:
    """
    HP 濾波的 λ 與增益 1/2 之截止週期 p 的關係:

        λ = 1 / (16 · sin⁴(π/p))

    :param p_days: 截止週期(天)。週期短於 p 的波動被視為雜訊,長於 p 的視為趨勢。
    """
    if p_days <= 2:
        raise ValueError("截止週期必須大於 2(Nyquist)")
    return float(1.0 / (16.0 * np.sin(np.pi / p_days) ** 4))


def signal_to_noise_from_cutoff(p_days: float) -> float:
    """q = σ_μ²/σ_v² = 1/λ。"""
    return 1.0 / lambda_from_cutoff_period(p_days)


def filter_trend(y: np.ndarray, q: float) -> dict:
    """
    平滑趨勢模型的 Kalman 濾波(σ_L² = 0,只有斜率有過程噪音)。

    觀測噪音固定為 1 —— 只有比值 q 影響濾波增益,故此正規化不失一般性;
    斜率後驗 μ̂ 的單位與 y 相同(每期),變異數則以 σ_v² 為單位。

    ⚠️ **這是濾波(filtering)而非平滑(smoothing)。** 時刻 t 的輸出只用到
    y_1..y_t,不含任何未來資訊 —— 這是它能當交易訊號的前提,也有測試把關
    (test_filter_uses_no_future_information)。若誤用 Kalman **smoother**
    (RTS),每一點都會用到全序列,回測會產生嚴重的前視偏誤。

    :param y: 觀測序列(對數價格)
    :param q: 訊噪比 σ_μ²/σ_v²
    :return: dict(level, slope, slope_var) —— 各為長度 n 的陣列
    """
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n == 0:
        return {"level": np.array([]), "slope": np.array([]), "slope_var": np.array([])}

    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    Q = np.array([[0.0, 0.0], [0.0, q]])
    H = np.array([[1.0, 0.0]])
    R = 1.0

    # 擴散式初始化:對初始狀態幾乎無資訊
    x = np.array([y[0], 0.0])
    P = np.eye(2) * 1e6

    level = np.empty(n)
    slope = np.empty(n)
    slope_var = np.empty(n)

    for t in range(n):
        # 預測
        x = F @ x
        P = F @ P @ F.T + Q

        # 更新
        S = float((H @ P @ H.T)[0, 0]) + R
        K = (P @ H.T).ravel() / S
        resid = y[t] - float((H @ x)[0])
        x = x + K * resid
        P = (np.eye(2) - np.outer(K, H)) @ P

        level[t] = x[0]
        slope[t] = x[1]
        slope_var[t] = P[1, 1]

    return {"level": level, "slope": slope, "slope_var": slope_var}


def trend_exit_signal(
    log_price: np.ndarray, cutoff_period_days: float = 45.0
) -> np.ndarray:
    """
    出場訊號:當濾波後的趨勢斜率轉負時出場。

    對應「斜率後驗機率 P(μ_t > 0 | y_1..y_t) < 0.5」—— 取 θ=0.5 有兩個好處:

    1. **沒有額外的自由參數。** 任何 θ≠0.5 都需要再挑一個數字,而我們沒有
       可辯護的先驗能決定它該是 0.4 還是 0.3。
    2. **尺度不變。** θ=0.5 的判準化簡成 μ̂_t < 0,完全不依賴 σ_v 的估計 ——
       少一個要從資料估計的量,就少一份過擬合的可能。

    因此整個出場機制的自由參數只有 `cutoff_period_days` 一個,而它取自
    risk-policy.md 2.3 節早已定案的 45 天 time-stop(遠早於看到任何資料)。

    :return: 布林陣列,True 表示該根 K 棒收盤後應出場
    """
    q = signal_to_noise_from_cutoff(cutoff_period_days)
    out = filter_trend(np.asarray(log_price, dtype=float), q)
    return out["slope"] < 0.0

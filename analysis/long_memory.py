"""
檢驗「趨勢延續」這個經濟前提本身是否成立 —— Lo–MacKinlay 變異數比檢定與 Hurst 指數。

為什麼這是**前提檢驗**而不是策略搜尋:
    docs/strategy-hypothesis.md 策略一的經濟假說是「加密貨幣價格存在時間序列動能,
    突破後趨勢會延續」。這個假說可以**在不碰任何策略參數的情況下直接檢定** ——
    若報酬序列在相關時間尺度上與隨機漫步無法區分,那麼任何趨勢跟隨策略都沒有
    可利用的結構,策略一的失敗就不是「參數沒調好」,而是前提不成立。

    這裡計算的是**市場的性質**,不是某個策略的績效,因此不消耗參數搜尋的 N 額度。
    但它也不能反過來證明任何策略有效 —— 見 docs/ 對應文件的說明。

Lo, A. W. & MacKinlay, A. C. (1988), "Stock Market Prices Do Not Follow Random Walks:
Evidence from a Simple Specification Test", Review of Financial Studies 1(1), 41–66.
⚠️ 原文我方未取得,實作依公式結構(見 reading-list.md 第 8 節的一貫揭露)。
"""

from __future__ import annotations

import numpy as np
from scipy import stats


def variance_ratio(returns: np.ndarray, q: int) -> float:
    """
    VR(q) = Var(q 期加總報酬) / (q · Var(1 期報酬))

    隨機漫步下 VR(q) = 1;
    VR > 1 表示正自相關(動能/趨勢延續),VR < 1 表示均值回歸。

    使用重疊視窗並套用 Lo–MacKinlay 的無偏修正。
    """
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if q < 2 or n < q + 1:
        return float("nan")

    mu = r.mean()
    # 1 期變異數(無偏)
    var_1 = np.sum((r - mu) ** 2) / (n - 1)
    if var_1 <= 0:
        return float("nan")

    # q 期重疊加總報酬,Lo–MacKinlay 的自由度修正
    rolled = np.convolve(r, np.ones(q), mode="valid")  # 長度 n-q+1
    # ⚠️ 分母 m 已含 q,故 var_q 是**每期**變異數,不是 q 期變異數 ——
    # 不可再除一次 q(除兩次會讓 VR 恆等於 1/q,而且看起來像「顯著均值回歸」,
    # 是個會安靜產生錯誤結論的 bug,已由 test_random_walk_has_vr_near_one 把關)。
    m = q * (n - q + 1) * (1 - q / n)
    var_q = np.sum((rolled - q * mu) ** 2) / m

    return float(var_q / var_1)


def variance_ratio_test(returns: np.ndarray, q: int) -> dict:
    """
    Lo–MacKinlay 異質變異數穩健(heteroskedasticity-robust)的 VR 檢定。

    金融報酬有明顯的波動度叢聚,**必須**用穩健版本;同質變異數版本會嚴重
    低估標準誤、把雜訊誤判為顯著的動能。這在加密貨幣上尤其重要。

        z*(q) = (VR(q) − 1) / sqrt(θ*(q))
        θ*(q) = Σ_{j=1}^{q−1} [2(q−j)/q]² · δ_j
        δ_j   = Σ_t (r_t−μ)²(r_{t−j}−μ)² / [Σ_t (r_t−μ)²]²

    :return: dict(vr, z_stat, p_value, interpretation)
    """
    r = np.asarray(returns, dtype=float)
    n = len(r)
    vr = variance_ratio(r, q)
    if not np.isfinite(vr):
        return {"q": q, "vr": float("nan"), "z_stat": float("nan"), "p_value": float("nan")}

    mu = r.mean()
    d = (r - mu) ** 2
    denom = d.sum() ** 2

    theta = 0.0
    for j in range(1, q):
        delta_j = float(np.sum(d[j:] * d[:-j]) / denom)
        theta += ((2.0 * (q - j) / q) ** 2) * delta_j

    if theta <= 0:
        return {"q": q, "vr": vr, "z_stat": float("nan"), "p_value": float("nan")}

    z = (vr - 1.0) / np.sqrt(theta)
    p = float(2 * (1 - stats.norm.cdf(abs(z))))

    if p >= 0.05:
        interp = "與隨機漫步無法區分"
    elif vr > 1:
        interp = "顯著正自相關(動能)"
    else:
        interp = "顯著均值回歸"

    return {"q": q, "vr": vr, "z_stat": float(z), "p_value": p, "interpretation": interp}


def hurst_exponent(series: np.ndarray, min_lag: int = 2, max_lag: int = 100) -> float:
    """
    以「不同 lag 下的結構函數」估計 Hurst 指數:

        E[|X_{t+τ} − X_t|²] ∝ τ^{2H}

    對 log τ 與 log(變異數) 做線性迴歸,斜率的一半即 H。

    H = 0.5 隨機漫步;H > 0.5 持續性(趨勢);H < 0.5 反持續性(均值回歸)。

    ⚠️ Hurst 指數**沒有現成的顯著性檢定**,樣本有限時估計偏誤可觀。
    它只適合當描述性指標,正式的統計判定請用 variance_ratio_test()。
    """
    x = np.asarray(series, dtype=float)
    lags = np.arange(min_lag, min(max_lag, len(x) // 2))
    if len(lags) < 3:
        return float("nan")

    tau = [np.var(x[lag:] - x[:-lag]) for lag in lags]
    tau = np.asarray(tau)
    ok = tau > 0
    if ok.sum() < 3:
        return float("nan")

    slope = np.polyfit(np.log(lags[ok]), np.log(tau[ok]), 1)[0]
    return float(slope / 2.0)

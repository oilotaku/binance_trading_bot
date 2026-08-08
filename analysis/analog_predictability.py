"""
「類比可預測性」診斷:歷史上相似的市場狀態,是否導向相似的後續報酬?

這是 k-NN 的核心假設,也是所有「從歷史相似情境學習」方法(case-based reasoning、
最近鄰、乃至於樹模型的分割邏輯)共同的前提。與 analysis/long_memory.py 一樣,
它測的是**市場的性質**,用來在投入模型搜尋之前先判斷「這裡有沒有東西可學」。

⚠️ 但與 long_memory.py 不同,本模組**不是零 N 成本**:它需要指定特徵、鄰居數、
   預測期與 embargo。每跑一個配置就消耗一次參數搜尋額度
   (見 docs/proposals/ml-and-exogenous-data-assessment.md 第 1 節的 N 預算)。
   **請事前指定配置再跑,不要掃描。**

三個必須做對、少一個結果就沒有意義的地方:

1. **只用過去的鄰居。** 時刻 i 的預測只能在 i 之前的樣本裡搜尋。
2. **embargo。** 特徵用 W 期視窗時,時間距離 < W 的「鄰居」與自己共用大部分視窗 ——
   那不是「歷史上的相似情境」,是同一段歷史被重複數了一次。實測 BTC/USDT 在
   W=20 時,**約 50% 的最近鄰落在 ±20 天內**,不做 embargo 等於自己預測自己。
3. **標準化也只能用過去。** 用全樣本的均值/標準差做標準化是一個容易被忽略的前視洩漏。
"""

from __future__ import annotations

import numpy as np
from scipy import stats


def window_features(log_returns: np.ndarray, window: int) -> np.ndarray:
    """
    每個時點以過去 `window` 期的報酬構造三個特徵:累積報酬、波動度、上漲期比例。

    刻意維持在 3 維:k-NN 在高維會失去局部性(維度詛咒)——要讓鄰域涵蓋 1% 的資料,
    d=10 時每個維度需涵蓋 63% 的範圍,「最近鄰」實際橫跨大半個特徵空間。
    """
    r = np.asarray(log_returns, dtype=float)
    n = len(r)
    if n <= window:
        return np.empty((0, 3))
    return np.array(
        [[r[t - window : t].sum(), r[t - window : t].std(), np.mean(r[t - window : t] > 0)]
         for t in range(window, n)]
    )


def analog_information_coefficient(
    log_returns: np.ndarray,
    window: int = 20,
    k: int = 10,
    horizon: int = 20,
    embargo: int | None = None,
    warmup: int = 300,
) -> dict:
    """
    以嚴格因果的 k-NN 計算資訊係數(IC):鄰居的平均後續報酬 vs 實際後續報酬的相關係數。

    :param embargo: 排除時間距離小於此值的鄰居;預設等於 `window`(視窗重疊的長度)
    :return: dict(ic, p_value, n, hit_rate)

    IC 的量級可用 Grinold 基本定律換算成可達成的資訊比率:`IR ≈ IC · √廣度`,
    廣度為每年的獨立下注數。這個換算是判斷「這個 IC 值不值得追」的關鍵一步 ——
    一個統計顯著但量級微小的 IC,可能連門檻都摸不到。
    """
    r = np.asarray(log_returns, dtype=float)
    emb = window if embargo is None else embargo

    feats = window_features(r, window)
    n_feat = len(feats)
    usable = n_feat - horizon
    if usable <= warmup + k + emb:
        return {"ic": float("nan"), "p_value": float("nan"), "n": 0, "hit_rate": float("nan")}

    X = feats[:usable]
    fwd = np.array([r[window + t : window + t + horizon].sum() for t in range(usable)])

    preds, acts = [], []
    for i in range(warmup, usable):
        hi = i - emb
        if hi < k:
            continue
        pool = X[:hi]
        # 標準化只用 pool —— 全樣本標準化是前視洩漏
        mu = pool.mean(axis=0)
        sd = pool.std(axis=0)
        sd[sd == 0] = 1.0
        d = np.linalg.norm((pool - mu) / sd - (X[i] - mu) / sd, axis=1)
        nb = np.argsort(d)[:k]
        preds.append(fwd[nb].mean())
        acts.append(fwd[i])

    preds, acts = np.asarray(preds), np.asarray(acts)
    if len(preds) < 30:
        return {"ic": float("nan"), "p_value": float("nan"), "n": len(preds), "hit_rate": float("nan")}

    ic, p = stats.pearsonr(preds, acts)
    return {
        "ic": float(ic),
        "p_value": float(p),
        "n": int(len(preds)),
        "hit_rate": float(np.mean(np.sign(preds) == np.sign(acts))),
    }


def implied_information_ratio(ic: float, breadth: float) -> float:
    """
    Grinold 基本定律:`IR ≈ IC · √廣度`。

    廣度是**每年的獨立下注數** —— 「獨立」很關鍵:兩個相關係數 0.8 的標的
    不算兩個下注(見 ml-and-exogenous-data-assessment.md 第 4 節的 K_eff)。

    ⚠️ 這是上界近似,假設下注彼此獨立且訊號被最適地轉換成部位。實際 IR 通常更低。
    """
    if breadth <= 0:
        return float("nan")
    return float(abs(ic) * np.sqrt(breadth))

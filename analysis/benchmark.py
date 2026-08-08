"""
CP-004 第二層的比較基準:**每日再平衡等權 50/50 BTC/ETH 買進持有**。

為什麼基準必須寫成程式碼而不是文件裡的一個數字:
    CP-004 的判定是「策略 Sharpe 是否顯著高於基準」。基準若只存在於文件,
    日後每次重算都可能因為定義的細微差異(再平衡頻率、對數 vs 簡單報酬、
    起訖日)得到不同的數字,而那個差異會直接改變通過與否。
    **基準是治理參數,和門檻同級。**

為什麼是「每日再平衡等權」而不是「買進持有不再平衡」:
    不再平衡的 50/50 其權重會隨兩個資產的相對表現漂移,**結果因此依賴起始日**,
    同一個策略換個回測起點就對應到不同的基準。每日再平衡是無參數且唯一的。

    (實測差異:再平衡 Sharpe 0.3405 / MDD 88.32%;不再平衡 0.3650 / 87.02%。
     不再平衡的版本對策略比較**有利**,這也是不選它的理由之一 ——
     基準的選擇不應該偏向讓策略好看。)

為什麼是 50/50 而不是 BTC 單一:
    scope.md 定義的標的池就是 BTC + ETH。用 BTC 單一當基準,等於把
    「BTC 與 ETH 之間怎麼配置」這個決策的功過也算進策略頭上,而那不是
    策略要回答的問題。**兩標的策略應對照兩標的基準。**

    ⚠️ 誠實揭露:50/50(Sharpe 0.3405)也**確實比 BTC 單一(0.44)更容易超越**。
    此選擇由專案負責人於 2026-08-08 定案,**時點早於任何策略二接觸資料**,
    符合預先登錄紀律。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BENCHMARK_PAIRS = ("BTC/USDT", "ETH/USDT")
BENCHMARK_WEIGHTS = (0.5, 0.5)
BENCHMARK_TIMEFRAME = "1d"

# CP-004 第 4 節定案:回撤上限
MAX_DRAWDOWN_CAP = 0.30


def benchmark_log_returns(
    datadir: Path, pairs: tuple[str, ...] = BENCHMARK_PAIRS,
    weights: tuple[float, ...] = BENCHMARK_WEIGHTS,
) -> pd.Series:
    """
    每日再平衡等權組合的對數報酬序列。

    每日再平衡下,組合的**對數**報酬是各標的對數報酬的加權和的一階近似。
    此處直接採用該定義(而非先合成價格再取對數),因為它讓「每日再平衡」
    這件事在程式碼裡是顯式的、無歧義的。
    """
    from freqtrade.data.history import load_pair_history

    if len(pairs) != len(weights):
        raise ValueError("pairs 與 weights 長度必須相同")
    if abs(sum(weights) - 1.0) > 1e-9:
        raise ValueError(f"weights 必須加總為 1,實得 {sum(weights)}")

    series = []
    for p in pairs:
        df = load_pair_history(p, BENCHMARK_TIMEFRAME, datadir)
        if df.empty:
            raise ValueError(f"{p} 無資料")
        s = pd.Series(
            np.diff(np.log(df["close"].to_numpy())), index=df["date"].iloc[1:].to_numpy()
        )
        series.append(s)

    aligned = pd.concat(series, axis=1).dropna()
    if aligned.empty:
        raise ValueError("各標的沒有共同的日期區間")
    return (aligned * np.asarray(weights)).sum(axis=1)


def performance_summary(log_returns: np.ndarray | pd.Series, periods_per_year: int = 365) -> dict:
    """
    年化報酬、年化 Sharpe、最大回撤 —— 三者用同一段序列、同一套定義算出,
    避免不同來源的數字被混用比較(這是本專案反覆遇到的問題類型)。
    """
    r = np.asarray(log_returns, dtype=float)
    n = len(r)
    if n < 2:
        return {k: float("nan") for k in ("cagr", "sharpe", "max_drawdown", "n")}

    eq = np.cumsum(r)
    running = np.maximum.accumulate(eq)
    mdd = float((1 - np.exp(eq - running)).max())
    sd = r.std()

    return {
        "n": n,
        "cagr": float(np.exp(r.sum() * periods_per_year / n) - 1),
        "sharpe": float(r.mean() / sd * np.sqrt(periods_per_year)) if sd > 0 else float("nan"),
        "max_drawdown": mdd,
    }


def zero_skill_baseline(benchmark_mdd: float, mdd_cap: float = MAX_DRAWDOWN_CAP) -> dict:
    """
    CP-004 第一層的「零技巧基準」。

    把基準曝險縮到 `w = mdd_cap / benchmark_mdd` 倍,回撤與報酬**同比例**縮小,
    而 Sharpe 完全不變 —— 這是機械後果,不需要任何 edge。

    因此「在 MDD ≤ cap 之下保留的報酬比例」若只等於 `w`,代表策略沒有比
    單純降低曝險做得更好。**這是第一層的及格線,不是成就。**
    """
    if benchmark_mdd <= 0:
        return {"exposure": float("nan"), "required_retention": float("nan")}
    w = min(1.0, mdd_cap / benchmark_mdd)
    return {"exposure": w, "required_retention": w}

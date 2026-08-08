"""
⚠️ 合成資料產生器 —— 僅供「管線整合測試」使用,絕不可用於任何策略績效判斷。

存在理由:docs/backtest-procedure.md 第 4 節的完整驗證流程是 9 個 fold ×
(hyperopt 200 epochs + OOS backtest),在真實環境要跑數小時。若管線本身有整合錯誤
(欄位名稱對不上、自訂 loss function 在真實 hyperopt 迴圈裡拋例外、report.py 吃不下
Freqtrade 實際匯出格式),用真實資料跑到一半才發現是很昂貴的失敗。

本工具產生「格式與真實資料完全相同、但內容是程式產生」的 OHLCV,讓整條管線能先被
端到端執行一次,證明機械環節接得起來。

⚠️ 這絕對不驗證策略有沒有 edge —— 合成價格序列跑出來的 Sharpe / DSR / 回撤機率
在統計上毫無意義,任何情況下都不得寫進 docs/ 的正式報告,也不得作為
docs/go-no-go-checklist.md 任何一項的通過依據。資料一律寫入獨立的
user_data/data_synthetic/ 目錄,不污染真實資料目錄。

用法:
    .venv/bin/python analysis/tools/make_synthetic_data.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

SYNTHETIC_DATADIR = _REPO_ROOT / "user_data" / "data_synthetic" / "binance"
START = datetime(2019, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 8, 7, tzinfo=timezone.utc)


def _generate_regime_switching_series(
    n_days: int, start_price: float, seed: int
) -> np.ndarray:
    """
    產生帶 regime 切換的價格序列(趨勢期 / 盤整期交替)。

    刻意做出 regime 結構而非單純的隨機漫步,理由:策略一是 Donchian 突破,
    在純隨機漫步上幾乎不會產生任何有意義的訊號數量,管線會因為「零筆交易」
    而走不到後面的統計計算,測不出整合問題。有 regime 結構才能確保產生足夠
    的交易筆數讓整條管線跑起來 —— 這是為了「測試管線」而設計的資料特性,
    不是對真實市場的模擬主張。
    """
    rng = np.random.default_rng(seed)
    log_prices = np.zeros(n_days)
    log_prices[0] = np.log(start_price)

    day = 1
    while day < n_days:
        # 隨機決定這一段是趨勢期還是盤整期。
        # 趨勢期比例刻意偏高(0.55)、區段偏短,是為了讓 Donchian 突破訊號密度足夠,
        # 使整條統計管線(需要足夠交易筆數才算得出標準差/自相關)能被真正壓測到。
        # 這是「為了測試管線」而選的資料特性,不是對真實市場頻率的主張。
        is_trending = rng.random() < 0.55
        segment_len = int(rng.integers(25, 90))
        if is_trending:
            drift = rng.choice([1, -1]) * rng.uniform(0.003, 0.008)
            vol = rng.uniform(0.02, 0.04)
        else:
            drift = 0.0
            vol = rng.uniform(0.015, 0.03)

        for _ in range(segment_len):
            if day >= n_days:
                break
            log_prices[day] = log_prices[day - 1] + drift + rng.normal(0, vol)
            day += 1

    return np.exp(log_prices)


def build_ohlcv(pair_seed: int, start_price: float) -> pd.DataFrame:
    """由收盤價序列反推出合理的 OHLCV(high >= max(open,close),low <= min(open,close))。"""
    dates = pd.date_range(START, END, freq="1D", tz="UTC")
    n = len(dates)
    close = _generate_regime_switching_series(n, start_price, pair_seed)

    rng = np.random.default_rng(pair_seed + 1000)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]  # 日 K 開盤 = 前一日收盤(現貨 24/7 無跳空)

    intraday_range = np.abs(rng.normal(0, 0.015, size=n)) + 0.005
    high = np.maximum(open_, close) * (1 + intraday_range)
    low = np.minimum(open_, close) * (1 - intraday_range)

    # 成交量:與當日「絕對報酬」正相關(真實市場的量價關係 —— 大幅波動日通常伴隨放量)。
    # 用日報酬而非單純的日內振幅,是為了讓「突破日」確實傾向放量,策略的成交量確認條件
    # 才會與突破訊號有實質關聯,而不是兩個互相獨立的隨機條件(否則同時成立的機率過低,
    # 產生的交易筆數不足以壓測統計管線)。
    daily_return = np.abs(np.concatenate([[0.0], np.diff(np.log(close))]))
    base_volume = rng.lognormal(mean=10, sigma=0.35, size=n)
    volume = base_volume * (1 + 8 * daily_return + 2 * intraday_range)

    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def main() -> None:
    from freqtrade.data.history import get_datahandler
    from freqtrade.enums import CandleType

    SYNTHETIC_DATADIR.mkdir(parents=True, exist_ok=True)
    handler = get_datahandler(SYNTHETIC_DATADIR, data_format="feather")

    for pair, seed, start_price in [("BTC/USDT", 20240101, 10000.0), ("ETH/USDT", 77, 200.0)]:
        df = build_ohlcv(seed, start_price)
        handler.ohlcv_store(pair, "1d", df, CandleType.SPOT)
        print(
            f"[SYNTHETIC] {pair}: {len(df)} 根日K, "
            f"{df['date'].min():%Y-%m-%d} ~ {df['date'].max():%Y-%m-%d}, "
            f"收盤 {df['close'].iloc[0]:,.0f} -> {df['close'].iloc[-1]:,.0f}"
        )

    print(f"\n寫入: {SYNTHETIC_DATADIR}")
    print("⚠️  合成資料,僅供管線整合測試,不得用於任何策略績效判斷。")


if __name__ == "__main__":
    main()

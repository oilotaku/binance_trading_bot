"""
docs/backtest-procedure.md 4.3 節「關於步驟 1 為什麼用自訂 loss function」:
  1. Purge 必須在 hyperopt 每次評估、計算 loss 之前生效,不能事後補救。
  2. 明確對齊 statistical-methodology.md 第 1 節定義的 SR_trade
     (逐筆交易報酬計算,不年化),不依賴 Freqtrade 內建 SharpeHyperOptLoss/
     SharpeHyperOptLossDaily 的確切計算基礎(兩者計算基礎可能隨版本演進而不同,
     實測已確認內建 calculate_sharpe() 是 calendar-day 加權平均 / per-trade 標準差的
     混合公式,不等同本專案定義的 SR_trade,見程式碼註解)。

embargo_days / IS 視窗結束日期(is_end)透過環境變數傳入 —— walk-forward 每個 fold
是獨立的 `freqtrade hyperopt` CLI 呼叫(見 analysis/report.py 的編排邏輯),沒有原生
機制把「目前是第幾個 fold」傳給 loss function,環境變數是最不需要修改 Freqtrade
本身、又能讓每個 fold 帶入正確 embargo 邊界的做法。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from pandas import DataFrame

from freqtrade.optimize.hyperopt import IHyperOptLoss

# analysis/ 在 repo 根目錄,不在 user_data/ 底下(architecture-spec.md 2.2 節)。
# Freqtrade 執行時的 cwd 慣例是 repo 根目錄,但不保證一定在 sys.path 上,
# 明確加入以避免 "ModuleNotFoundError: No module named 'analysis'"。
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class PurgedTradeSharpeLoss(IHyperOptLoss):
    """
    statistical-methodology.md 第 1 節定義的 SR_trade,加上 purge 過濾。
    loss = -SR_trade(hyperopt 找最小值,SR_trade 越高越好,故取負號)。
    """

    @staticmethod
    def hyperopt_loss_function(
        *,
        results: DataFrame,
        trade_count: int,
        min_date: datetime,
        max_date: datetime,
        config,
        processed: dict,
        backtest_stats: dict,
        starting_balance: float,
        **kwargs,
    ) -> float:
        MAX_LOSS = 100_000.0

        if results is None or len(results) == 0:
            return MAX_LOSS

        embargo_days = int(os.environ.get("ANALYSIS_EMBARGO_DAYS", "30"))
        is_end_str = os.environ.get("ANALYSIS_IS_END")
        is_end = (
            datetime.fromisoformat(is_end_str) if is_end_str else max_date
        )
        if is_end.tzinfo is None:
            is_end = is_end.replace(tzinfo=timezone.utc)

        from analysis.walk_forward import purge_is_trades

        purged = purge_is_trades(results, is_end, embargo_days, open_date_col="open_date")

        if len(purged) < 2:
            # 樣本太少無法算標準差,依 statistical-methodology.md 精神:
            # 資訊量不足不應該被判定為「好結果」,回傳最大 loss。
            return MAX_LOSS

        returns = purged["profit_ratio"].to_numpy(dtype=float)
        std = returns.std(ddof=1)
        if std <= 0:
            return MAX_LOSS

        sr_trade = returns.mean() / std  # 不年化,statistical-methodology.md 第1節明訂
        return float(-sr_trade)

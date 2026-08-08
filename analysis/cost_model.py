"""
docs/backtest-procedure.md 第 2.2 節:滑價 haircut(Freqtrade 回測預設零滑價,過度樂觀)。
"""

from __future__ import annotations

import pandas as pd

SLIPPAGE_BPS_PER_SIDE = 5  # backtest-procedure.md 2.2 節:每邊 5 bps,來回合計 10 bps,刻意保守假設
FEE_PCT_PER_SIDE = 0.001  # backtest-procedure.md 2.1 節:Binance 現貨 VIP0,未計 BNB 折扣


def apply_slippage_haircut(
    trades: pd.DataFrame, slippage_bps_per_side: float = SLIPPAGE_BPS_PER_SIDE
) -> pd.DataFrame:
    """
    backtest-procedure.md 2.2 節具體做法:
        進場價格上調 slippage、出場價格下調 slippage(方向皆對策略不利),重算 profit_ratio/profit_abs。

    這是後處理階段的獨立調整,不修改 Freqtrade 的 --fee 參數本身
    (那會讓 --fee 這個數字失去單一意義,難以拆解 debug)。
    """
    if trades.empty:
        return trades

    adjusted = trades.copy()
    haircut = slippage_bps_per_side / 10_000

    adjusted["open_rate_adj"] = adjusted["open_rate"] * (1 + haircut)
    adjusted["close_rate_adj"] = adjusted["close_rate"] * (1 - haircut)

    is_short = adjusted["is_short"] if "is_short" in adjusted.columns else False

    long_ratio = adjusted["close_rate_adj"] / adjusted["open_rate_adj"] - 1
    short_ratio = 1 - adjusted["close_rate_adj"] / adjusted["open_rate_adj"]
    adjusted["profit_ratio_adj"] = long_ratio.where(~is_short, short_ratio) if isinstance(is_short, pd.Series) else long_ratio

    fee_haircut = 2 * FEE_PCT_PER_SIDE
    adjusted["profit_ratio_adj"] = adjusted["profit_ratio_adj"] - fee_haircut + (
        # 若原始 profit_ratio 已含 Freqtrade 自己的 --fee 假設,這裡只補上滑價差額,
        # 避免手續費被重複扣兩次。呼叫端(report.py)必須明確標註 trades 是否已含手續費。
        0
    )

    adjusted["profit_abs_adj"] = adjusted["profit_ratio_adj"] * adjusted["stake_amount"]

    return adjusted


def net_return_series(trades: pd.DataFrame, use_adjusted: bool = True) -> pd.Series:
    """回傳供 analysis/ 其餘模組(sample_size/significance/drawdown_mc)使用的淨報酬序列。"""
    col = "profit_ratio_adj" if use_adjusted else "profit_ratio"
    if col not in trades.columns:
        raise ValueError(
            f"trades 缺少欄位 {col} —— 是否忘記先呼叫 apply_slippage_haircut()?"
        )
    return trades[col]

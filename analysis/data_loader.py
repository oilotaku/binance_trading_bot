"""
docs/backtest-procedure.md 第 8 節 data_loader.py:讀取 Freqtrade 原始匯出檔,標準化成 DataFrame。

刻意重用 Freqtrade 官方提供的 load_backtest_data / HyperoptTools.load_filtered_results,
不重新實作 zip/json 解析邏輯 —— 呼應 tech-stack-decision.md「不重新發明輪子」的既有原則,
這兩個函式是 Freqtrade 本身供外部工具讀取匯出結果用的公開介面(freqtrade.data.btanalysis)。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_trades(backtest_result_dir: Path | str, strategy: str | None = None) -> pd.DataFrame:
    """
    讀取 user_data/backtest_results/ 下最新一次(或指定)回測的交易明細。
    欄位對照 Freqtrade BT_DATA_COLUMNS(見 freqtrade.data.btanalysis.bt_fileutils):
    pair, stake_amount, open_date, close_date, open_rate, close_rate, profit_ratio,
    profit_abs, exit_reason, trade_duration(分鐘), is_short 等。
    """
    from freqtrade.data.btanalysis import load_backtest_data

    return load_backtest_data(Path(backtest_result_dir), strategy=strategy)


def load_hyperopt_epochs(results_file: Path | str, config: dict) -> tuple[pd.DataFrame, int]:
    """
    讀取 user_data/hyperopt_results/ 下的完整 epoch 紀錄。
    backtest-procedure.md 4.3 節步驟 2:必須匯出全部 epoch(不是只匯出最佳那組),
    因為 sigma_SR 需要全部 trial 的 Sharpe 分布才能算。

    回傳的 DataFrame 每列一個 epoch,欄位:
        epoch_id, loss(= -SR_trade,見 hyperopt_loss.PurgedTradeSharpeLoss), sr_trade(= -loss),
        trade_count, params_dict(原始 dict,供後續依需要展開個別參數欄位)
    """
    from freqtrade.optimize.hyperopt_tools import HyperoptTools

    epochs, total = HyperoptTools.load_filtered_results(Path(results_file), config)

    rows = []
    for i, epoch in enumerate(epochs):
        rows.append(
            {
                "epoch_id": i,
                "loss": epoch.get("loss"),
                "sr_trade": -epoch.get("loss", 0.0) if epoch.get("loss") is not None else None,
                "trade_count": epoch.get("results_metrics", {}).get("total_trades"),
                "params_dict": epoch.get("params_dict"),
                "is_best": epoch.get("is_best", False),
            }
        )

    return pd.DataFrame(rows), total


def load_ohlcv(datadir: Path | str, pair: str, timeframe: str = "1d") -> pd.DataFrame:
    """
    讀取 user_data/data/binance/ 下已下載的 feather 格式 K 線資料,供事後分析
    (如 backtest-procedure.md 6.1 節「事後檢視 time-stop 出場後 10 個交易日走勢」)使用。
    不用於任何 Freqtrade 執行時的訊號判斷,純離線診斷用途。
    """
    from freqtrade.data.history import load_pair_history

    return load_pair_history(pair=pair, timeframe=timeframe, datadir=Path(datadir))

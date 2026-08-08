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


# Freqtrade 在「交易筆數低於 hyperopt_min_trades」時,不呼叫 loss function,
# 直接指派這個哨兵值(freqtrade/optimize/hyperopt/hyperopt_optimizer.py: MAX_LOSS = 100000)。
# 這種 epoch 沒有真實的 Sharpe,必須排除在 sigma_SR 計算之外 —— 見下方說明。
FREQTRADE_MAX_LOSS_SENTINEL = 100_000


def load_hyperopt_epochs(results_file: Path | str, config: dict) -> tuple[pd.DataFrame, int]:
    """
    讀取 user_data/hyperopt_results/ 下的完整 epoch 紀錄。
    backtest-procedure.md 4.3 節步驟 2:必須匯出全部 epoch(不是只匯出最佳那組),
    因為 sigma_SR 需要全部 trial 的 Sharpe 分布才能算。

    回傳的 DataFrame 每列一個 epoch,欄位:
        epoch_id, loss, sr_trade(= -loss,哨兵值 epoch 為 NaN), trade_count,
        params_dict, is_best, is_sentinel

    ⚠️ 哨兵值處理(整合測試實測踩到的坑,若不處理會靜默產生錯誤的 DSR):
        交易筆數不足的 epoch,其 loss 是 MAX_LOSS=100000 而非真實的 -SR_trade。
        若照單全收算成 sr_trade=-100000,sigma_SR 會從正常的 0.x 量級暴衝到數萬,
        連帶讓 statistical-methodology.md 2.2 節的 SR0 = sigma_SR × C(N) 變成十萬量級,
        DSR 因此恆為 0 —— 任何策略都必定「不通過」,且完全不會報錯。
        實測案例:20 個 epoch 中 2 個是哨兵值,sigma_SR 從正確的 0.1970 變成 30779.4845。
    """
    from freqtrade.optimize.hyperopt_tools import HyperoptTools

    epochs, total = HyperoptTools.load_filtered_results(Path(results_file), config)

    rows = []
    for i, epoch in enumerate(epochs):
        loss = epoch.get("loss")
        is_sentinel = loss is None or loss >= FREQTRADE_MAX_LOSS_SENTINEL
        rows.append(
            {
                "epoch_id": i,
                "loss": loss,
                # 哨兵值 epoch 沒有真實 Sharpe,一律設為 NaN,讓下游 .dropna() 自然排除
                "sr_trade": float("nan") if is_sentinel else -loss,
                "trade_count": epoch.get("results_metrics", {}).get("total_trades"),
                "params_dict": epoch.get("params_dict"),
                "is_best": epoch.get("is_best", False),
                "is_sentinel": is_sentinel,
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

"""
Phase 6 統計驗證管線(docs/backtest-procedure.md 第 8 節模組地圖的實作)。

這些模組獨立於 Freqtrade 執行時期,離線讀取 user_data/backtest_results/、
user_data/hyperopt_results/ 的匯出檔案做批次分析,不在 Freqtrade 主迴圈內執行。

資料流(docs/backtest-procedure.md 第 8 節):
    data_loader -> cost_model -> walk_forward (驅動 hyperopt_loss 的 purge 邏輯)
    -> 逐 fold: sample_size + significance
    -> 全部 fold 完成後串接: sample_size (串接序列) -> significance (次檢定)
    -> position_sizing_check -> drawdown_mc -> risk_policy_validation -> report

任何一個模組單獨拿掉,下游模組都拿不到合法輸入
(docs/statistical-methodology.md 第 0 節「單一驗證管線」原則的程式碼對應)。
"""

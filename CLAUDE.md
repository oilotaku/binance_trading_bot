# 專案慣例 / Project Conventions

## 語言

- **Commit message:一律用中文。**
- **`docs/` 文件、程式碼註解與 docstring:一律用中文。**
- 程式碼識別字(函式、變數、類別名)維持英文,對齊 Freqtrade 與 Python 生態慣例。
- 對話回覆用中文。

> 2026-08-08 之前的 commit message 是英文(`f461fe7`~`0a8d049`),尚未改寫。

## 方法論紀律

這個專案的核心是**驗證方法論**,不是交易程式。動到以下任何一項之前,先讀 [`docs/`](./docs/):

- **通過門檻**(`DSR ≥ 0.95`、`n_eff ≥ 30`)與**核心治理數字**(`risk_fraction` 上限、Kelly `c`、kill switch、每日熔斷、ATR `k` 的搜尋邊界)不得直接修改,必須走 [`docs/risk-policy.md`](./docs/risk-policy.md) 第 7 節的變更提案流程(範例見 [`docs/change-proposals/`](./docs/change-proposals/))。
- **預先登錄原則:** 驗證方法論的任何變更必須在看到回測結果**之前**決定。看到結果才回頭改方法,即使技術論證正確也視為不正當。
- **參數搜尋次數 `N` 包含研究者自由度**,不只是 hyperopt 的 epoch 數。換過的策略型態、試過又放棄的參數值,全部都算。

## 資料

- 資料檔不進 git,但 `user_data/data/checksums/` 的校驗和進版控 —— 容器是暫時的,靠校驗和重現。
- 取得與匯入只有一條路徑:`analysis/tools/download_binance_vision.py` → `analysis/tools/ingest_market_data.py`。後者強制執行 [`docs/backtest-procedure.md`](./docs/backtest-procedure.md) 1.4 節的品質檢查。
- **不得** forward-fill、插值或以任何方式修補 K 棒缺漏。`analysis/data_quality.py` 刻意不提供這些能力,並有測試把關。

## 測試

```bash
cd /workspace/binance_trading_bot && .venv/bin/python -m pytest analysis/tests/ -q
```

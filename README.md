# binance_trading_bot

幣安現貨量化交易機器人。以 [Freqtrade](https://github.com/freqtrade/freqtrade) 為執行框架,自建策略邏輯與一套完整的統計驗證管線。

A Binance spot quantitative trading bot, built on [Freqtrade](https://github.com/freqtrade/freqtrade) with a custom strategy and a full statistical validation pipeline.

> **⚠️ 目前狀態:尚未投入任何真實資金,策略也尚未經過真實資料驗證。**
> 規劃階段(Phase 0–9)已完成並全數確認,程式碼骨架已實作並通過整合測試,但**策略是否真的有 edge 完全未知** —— 這需要跑完 9-fold walk-forward 驗證,而那需要真實歷史資料。見下方「目前進度」。

---

## 這個專案的特色:先規劃、後寫程式

本專案刻意採用「所有設計文件完成並確認後,才寫第一行程式碼」的流程。`docs/` 下的 10 份文件不是事後補的說明,而是**開發的依據**——每個具體數字(單筆風險 1.5%、kill switch −15%、ATR 停損倍數搜尋邊界 2.0–4.0)都在文件中有推導過程,程式碼只是把它們落實。

This project deliberately front-loads design: every document in `docs/` was written and signed off *before* any code existed. Each concrete number (1.5% per-trade risk, −15% kill switch, ATR multiplier bounds) has a documented derivation; the code implements those decisions rather than inventing them.

| Phase | 文件 | 內容 |
|---|---|---|
| 0 | [`scope.md`](docs/scope.md) | 範圍界定:現貨、波段、模擬資金起步、治理拍板權 |
| 1 | [`strategy-hypothesis.md`](docs/strategy-hypothesis.md) | 策略假說與經濟邏輯(v1 聚焦動能突破) |
| 2 | [`statistical-methodology.md`](docs/statistical-methodology.md) | DSR 顯著性檢定、walk-forward、Fractional Kelly、蒙地卡羅回撤 |
| 3 | [`architecture-spec.md`](docs/architecture-spec.md) | Freqtrade 模組對應、kill switch 落地、環境分離 |
| 4 | [`risk-policy.md`](docs/risk-policy.md) | 所有風控數字定案 + 推導過程 |
| 5 | [`security-policy.md`](docs/security-policy.md) | 金鑰管理、日誌脫敏、啟動確認、事故應變 |
| 6 | [`backtest-procedure.md`](docs/backtest-procedure.md) | 回測與驗證管線的可執行流程 |
| 7 | [`execution-spec.md`](docs/execution-spec.md) | 執行層規格(冪等性、對帳、錯誤處理) |
| 8 | [`tech-stack-decision.md`](docs/tech-stack-decision.md) | 技術選型:為何建構於 Freqtrade 之上 |
| 9 | [`go-no-go-checklist.md`](docs/go-no-go-checklist.md) | 開發前最終把關 |
| — | [`pipeline-findings.md`](docs/pipeline-findings.md) | **整合測試找到的 2 個靜默失敗缺陷及修正** |
| — | [`development-plan.md`](docs/development-plan.md) | 整體流程與目前進度 |

---

## 策略概要 / Strategy

**Regime-Filtered Momentum Breakout**(BTC/USDT、ETH/USDT 現貨,日 K)

- **進場**:收盤突破 N 日 Donchian 上軌,且成交量 ≥ M 日均量 × X
- **出場**(三條路徑,任一觸發即出場):ATR × k 移動停損 / 跌破 Donchian 下軌 / 45 天 time-stop
- **不設固定停利** —— 策略的 edge 依賴少數大趨勢單的右尾報酬,固定停利會系統性砍掉它
- **倉位**:Fractional Kelly(c=0.25)換算,再取 `min()` 於 1.5% 硬上限

經濟假說、失效情境與信心評估見 [`strategy-hypothesis.md`](docs/strategy-hypothesis.md)。

## 風控:五層防線 / Risk Controls

| 防線 | 門檻 | 觸發行為 |
|---|---|---|
| 每日虧損熔斷 | −4% | 暫停新倉 3 天 |
| StoplossGuard | 10 天內 2 筆停損 | 暫停新倉 5 天 |
| CooldownPeriod | 每次平倉後 | 該交易對暫停 2 天 |
| 月回撤熔斷 | −8%(滾動 30 天) | 暫停新倉 + ≥24h 冷靜期 + 人工覆核 |
| **Kill switch** | **−15% 帳戶回撤** | 強制平倉 + 停機 + 冷靜期 |

除每日熔斷外,其餘皆由 Freqtrade 的 Protections 在**框架層級**強制執行,策略程式碼無法繞過。完整推導見 [`risk-policy.md`](docs/risk-policy.md)。

---

## 目錄結構 / Repository Layout

```
├── docs/                          規劃文件(開發依據,非事後說明)
├── user_data/
│   ├── strategies/                策略實作
│   ├── hyperopts/                 自訂 hyperopt loss(含 purge 過濾)
│   └── configs/                   分層設定:common / testnet / live + secrets
├── analysis/                      統計驗證管線(離線,獨立於 Freqtrade 執行時期)
│   ├── significance.py            PSR / DSR / Reality Check
│   ├── sample_size.py             Newey-West 有效樣本數 / block bootstrap
│   ├── position_sizing_check.py   Fractional Kelly
│   ├── drawdown_mc.py             蒙地卡羅回撤模擬
│   ├── walk_forward.py            fold 邊界 / purge / CUSUM
│   ├── report.py                  端到端編排與通過判定
│   ├── tools/                     合成資料產生器、整合測試、walk-forward 執行器
│   └── tests/                     31 個測試(含文件數值示例的精確重現)
```

## 快速開始 / Getting Started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install "freqtrade[hyperopt]" -r analysis/requirements.txt

# 1. 下載歷史資料(公開端點,不需 API 金鑰)
freqtrade download-data \
  -c user_data/configs/config-common.json -c user_data/configs/config-testnet.json \
  --pairs BTC/USDT ETH/USDT --timeframe 1d --timerange 20190901-

# 2. 前置檢查:確認策略沒有 lookahead bias
freqtrade lookahead-analysis \
  -c user_data/configs/config-common.json -c user_data/configs/config-testnet.json \
  --strategy RegimeFilteredMomentumBreakout --timerange 20200101-20211231

# 3. Pass A:embargo 校準輪
python analysis/tools/run_walk_forward.py --pass a --epochs 200

# 4. Pass B:正式輪(用 Pass A 印出的校準值;epochs 同為 200,見 CP-001)
python analysis/tools/run_walk_forward.py --pass b --epochs 200 --embargo-days <校準值>
```

Pass B 完成後會直接印出通過/不通過總表,完整報告寫入 `analysis/artifacts/pass_b/final_report.json`。

執行測試:`python -m pytest analysis/tests/ -v`

---

## 目前進度 / Current Status

**已完成**

- Phase 0–9 全部規劃文件,並逐份確認
- 策略實作:訊號邏輯、ATR 移動停損、Kelly 倉位、time-stop、每日熔斷、四層 Protections
- 統計驗證管線 9 個模組 + 31 個測試(含 `statistical-methodology.md` 數值示例的精確重現)
- 管線整合測試:端到端跑過真實 Freqtrade backtesting + hyperopt,**找出並修正 2 個會靜默產生錯誤結論的缺陷**(見 [`pipeline-findings.md`](docs/pipeline-findings.md))
- walk-forward 執行器,關鍵約束寫進程式結構並有迴歸測試

**唯一阻塞項**

- ⛔ **完整 9-fold walk-forward 尚未執行** —— 需要真實歷史資料與數小時運算。在此之前,**策略是否有 edge 完全未知**,`risk-policy.md` 中用理論推導得出的數字(45 天 time-stop、ATR 邊界、風險上限)也都尚未經真實資料檢驗。

**明確尚未驗證的事**

程式碼跑得起來 ≠ 策略會賺錢。目前所有測試驗證的都是**機械正確性**(公式算對、格式相容、約束不被繞過),沒有任何一項驗證策略的獲利能力。

---

## ⚠️ 安全注意事項 / Security Notes

- **絕不將 API Key / Secret 提交到 git**。金鑰放 `user_data/configs/secrets-*.json`(已在 `.gitignore`,glob 規則經實測驗證),範本見 `secrets-*.example.json`。
- **API 金鑰務必關閉提幣權限**,並搭配 IP 白名單。這是「金鑰外洩最大損失上限」唯一有效的結構性控制。
- **預設一律 dry-run**。`config-live.json` 的 `dry_run` 刻意保持 `true`,切換為 `false` 是需要二次確認的獨立動作。
- 完整規範(含金鑰輪替週期、日誌脫敏規則、金鑰外洩應變 runbook)見 [`security-policy.md`](docs/security-policy.md)。

## 免責聲明 / Disclaimer

本專案為個人研究用途。加密貨幣交易具高風險,可能損失全部本金。本專案的任何內容都不構成投資建議,作者不對使用本程式造成的任何損失負責。**請務必先在模擬環境充分驗證。**

For personal research only. Cryptocurrency trading carries substantial risk of total loss. Nothing here constitutes financial advice. Validate thoroughly in simulation before risking real capital.

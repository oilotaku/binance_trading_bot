# binance_trading_bot

幣安現貨量化交易機器人。以 [Freqtrade](https://github.com/freqtrade/freqtrade) 為執行框架,自建策略邏輯與一套完整的統計驗證管線。

A Binance spot quantitative trading bot, built on [Freqtrade](https://github.com/freqtrade/freqtrade) with a custom strategy and a full statistical validation pipeline.

> **⚠️ 目前狀態:已對真實資料完成三個策略的正式驗證,皆未達可投入真實資金的標準。尚未投入任何真實資金。**
> 策略一(動能突破)未通過顯著性檢定;策略四(波動度目標化)、策略五(趨勢濾波出場)第一層(回撤控制)通過,但均無統計顯著的超額報酬可主張。詳見下方「驗證結果」。

---

## 這個專案的特色:先規劃、後寫程式,而且規劃會被資料修正

本專案採用「設計文件先確認、才寫程式碼」的流程起步,但更重要的紀律是:**任何方法論或目標的修改,都必須在對應的策略碰到真實資料之前完成並 commit**——這條規則被反覆執行了八次(`CP-001`–`CP-008`),包括在策略一未通過之後發現並修正自己的統計方法論錯誤、策略四三度修正 `σ_target` 的推導,以及策略五為新出場機制重新校準風控核心數字。

This project front-loaded design, but the more important discipline running through it is pre-registration: any change to methodology or targets must be finalized and committed *before* the corresponding strategy touches real data. That rule was exercised eight times (`CP-001`–`CP-008`), including catching and fixing our own statistical methodology errors after strategy one failed, three rounds of correcting strategy four's `σ_target` derivation, and recalibrating core risk parameters for strategy five's new exit mechanism.

| Phase | 文件 | 內容 |
|---|---|---|
| 0 | [`scope.md`](docs/scope.md) | 範圍界定:現貨、波段、模擬資金起步、治理拍板權 |
| 1 | [`strategy-hypothesis.md`](docs/strategy-hypothesis.md) | 策略假說與經濟邏輯(v1 動能突破;策略二/三/五為 backlog) |
| 2 | [`statistical-methodology.md`](docs/statistical-methodology.md) | DSR 顯著性檢定、walk-forward、Fractional Kelly、蒙地卡羅回撤 |
| 3 | [`architecture-spec.md`](docs/architecture-spec.md) | Freqtrade 模組對應、kill switch 落地、環境分離 |
| 4 | [`risk-policy.md`](docs/risk-policy.md) | 策略一風控數字定案 + 推導過程 |
| 5 | [`security-policy.md`](docs/security-policy.md) | 金鑰管理、日誌脫敏、啟動確認、事故應變 |
| 6 | [`backtest-procedure.md`](docs/backtest-procedure.md) | 回測與驗證管線(已依 CP-003 更新為事前指定參數流程) |
| 7 | [`execution-spec.md`](docs/execution-spec.md) | 執行層規格(冪等性、對帳、錯誤處理) |
| 8 | [`tech-stack-decision.md`](docs/tech-stack-decision.md) | 技術選型:為何建構於 Freqtrade 之上 |
| 9 | [`go-no-go-checklist.md`](docs/go-no-go-checklist.md) | 開發前最終把關 |

**驗證後的變更提案(`docs/change-proposals/`)——每一份都在碰資料之前 commit:**

| 提案 | 內容 |
|---|---|
| [`CP-001`](docs/change-proposals/CP-001-hyperopt-epochs.md) | hyperopt epochs 1,000 → 200 |
| [`CP-002`](docs/change-proposals/CP-002-dsr-time-unit.md) | 修正 DSR 門檻推導混用年化/交易單位 Sharpe 的錯誤 |
| [`CP-003`](docs/change-proposals/CP-003-fixed-parameters.md) | 放棄 hyperopt 搜尋,4 個參數事前固定 + 1 個掃描,`N=5` |
| [`CP-004`](docs/change-proposals/CP-004-revised-targets.md) | 目標重訂為兩層制(回撤控制 + 可選超額報酬),基準改為 50/50 再平衡組合 |
| [`CP-005`](docs/change-proposals/CP-005-risk-policy-for-always-in-market.md) | 為「永遠在市」型策略重新設計風控(策略一的事件驅動機制不適用) |
| [`CP-007`](docs/change-proposals/CP-007-sigma-target-convexity-correction.md) | 修正 `σ_target` 推導漏掉的 Jensen 不等式凸性偏誤,改用不重疊的時間切分校準 |
| [`CP-008`](docs/change-proposals/CP-008-strategy-5-backstop-and-sizing.md) | 策略五用真實資料重新校準災難後備停損(`-25%→-22%`)與 position sizing 係數(`k=3.0→k'=5.0`) |
| [`target-reassessment.md`](docs/change-proposals/target-reassessment.md) | 證明原始 20–30% 報酬 + 1.0–1.5 Sharpe + 15–20% 回撤三個目標互相矛盾 |

---

## 驗證結果 / Validation Results

### 策略一:Regime-Filtered Momentum Breakout — ❌ 未通過

**BTC/USDT、ETH/USDT 現貨,日 K**。突破 N 日 Donchian 上軌 + 成交量確認進場,ATR 移動停損 / Donchian 下軌 / 45 天 time-stop 三選一出場。

用 2017-08-17 至 2026-07-31 的真實資料(9.0 年,SHA256 校驗)執行 [`CP-003`](docs/change-proposals/CP-003-fixed-parameters.md) 的事前指定參數掃描,**Deflated Sharpe Ratio = 0.90,低於 0.95 門檻,未通過**。診斷詳見 [`pass-b-results.md`](docs/pass-b-results.md) 與 [`post-mortem-strategy-1.md`](docs/post-mortem-strategy-1.md)——核心問題是出場機制太保守,系統性砍掉策略賴以獲利的右尾大趨勢單。

### 策略四:波動度目標化(Volatility Targeting) — ⚠️ 混合結果

**永遠在市**,無方向判斷,只依已實現波動與組合回撤動態調整曝險(`w = min(0.8, σ_target/σ̂) × 回撤斜坡`)。經濟假說:方向不可預測,但風險可預測。

第一次正式判定(CP-006 版本,`n=3270`,全樣本)發現 `σ_target` 的推導漏了 Jensen 不等式造成的凸性偏誤,MDD 31.37% 超出 30% 上限 1.37pp。[`CP-007`](docs/change-proposals/CP-007-sigma-target-convexity-correction.md) 用一段與判定樣本**不重疊**的時間切分(校準期 2017-08~2020-07,評估期 2020-08~2026-07)重新校準,重跑結果:

- **假說的可證偽預測成立**:相同平均曝險下,最大回撤比零技巧基準低
- **✅ 絕對回撤目標通過**:MDD 25.13%,低於 30% 上限
- **⚠️ 但通過不代表曝險精準對齊設計目標**:實現平均曝險仍超出設計值 `w0` 38%(與修正前的 39% 幾乎相同),原因是校準期與評估期的波動水位本身不平穩,是時間切分校準的方法論固有限制,細節見 [`strategy-4-cp007-results.md`](docs/strategy-4-cp007-results.md) 第 3 節
- **無統計顯著的超額報酬**:與基準的 Sharpe 差距 `Δ = +0.013`,遠低於 `N=15` 下 `0.6146` 的顯著性門檻

完整判定見 [`strategy-4-cp007-results.md`](docs/strategy-4-cp007-results.md)(CP-007 最新結果)與 [`strategy-4-results.md`](docs/strategy-4-results.md)(CP-006 版本,歷史記錄);機制已寫成真正的 Freqtrade `IStrategy`(`user_data/strategies/VolatilityTargeting.py`)並通過框架內技術驗證,見 [`strategy-4-freqtrade-technical-demo.md`](docs/strategy-4-freqtrade-technical-demo.md)(**該文件是技術驗證,不是新的統計判定**)。

### 策略五:以 Kalman 濾波趨勢斜率取代固定停損的出場機制 — ⚠️ 混合結果

進場邏輯與策略一完全相同(Donchian 突破 + 成交量確認),**只換出場機制**:用平滑趨勢狀態空間模型的 Kalman 濾波,以斜率後驗的符號(`μ̂_t<0`)判定出場,取代策略一的固定 `k×ATR` 移動停損。經濟假說:回撤不是判斷「趨勢是否仍在持續」的充分統計量,整條路徑的濾波後斜率才是。

- **✅ 第一層通過**:MDD 8.18%(基準 88.32%),保留報酬比 18.2% vs 零技巧基準所需 16.6%
- **❌ 第二層不通過**:`Δ=+0.52`,遠低於 `N=10` 門檻 `1.13`
- **❌ 核心因果檢定(配對比較 vs 策略一本身)不通過**:`delta=0.020`,只有門檻 `0.304` 的 6.7%
- **⚠️ 全樣本可證偽預測(持倉天數/賺賠比/`SR_trade`)字面上全部成立,但這個結論被進場點分岔(兩策略出場時間不同,實際進場點集合分岔達 25%)嚴重削弱**——控制進場點組成後(配對比較),效應的統計顯著性消失,這才是更可信的答案

完整判定見 [`strategy-5-results.md`](docs/strategy-5-results.md);風控核心數字(災難後備停損、position sizing)的重新校準見 [`CP-008`](docs/change-proposals/CP-008-strategy-5-backstop-and-sizing.md);機制已寫成真正的 Freqtrade `IStrategy`(`user_data/strategies/TrendFilterExit.py`)。

### Backlog

策略二(流動性衝擊均值回歸)、策略三(跨資產相對強度輪動)尚未評估,見 [`strategy-hypothesis.md`](docs/strategy-hypothesis.md)。

---

## 風控 / Risk Controls

**策略一(事件驅動,擇時型)——見 [`risk-policy.md`](docs/risk-policy.md) 完整推導:**

| 防線 | 門檻 | 觸發行為 |
|---|---|---|
| 每日虧損熔斷 | −4% | 暫停新倉 3 天 |
| StoplossGuard | 10 天內 2 筆停損 | 暫停新倉 5 天 |
| CooldownPeriod | 每次平倉後 | 該交易對暫停 2 天 |
| 月回撤熔斷 | −8%(滾動 30 天) | 暫停新倉 + ≥24h 冷靜期 + 人工覆核 |
| **Kill switch** | **−15% 帳戶回撤** | 強制平倉 + 停機 + 冷靜期 |

**策略四(狀態驅動,永遠在市)——上述機制大多不適用,見 [`CP-005`](docs/change-proposals/CP-005-risk-policy-for-always-in-market.md) 為何與如何重新設計:**

| 防線 | 門檻 | 機制 |
|---|---|---|
| 曝險上限 | 80% 權益 | 硬性上限,不加槓桿 |
| 回撤斜坡 | 30% → 40% | 曝險隨組合回撤線性降至 0(取代事件驅動熔斷) |
| Kill switch | 40% 帳戶回撤(滾動 365 天) | 斜坡終點自然歸零,非額外機制 |
| 再平衡帶 | 20% | 目標曝險偏離現況未達門檻不動,抑制換手成本 |

除每日熔斷外,策略一的其餘防線皆由 Freqtrade 的 Protections 在框架層級強制執行;策略四的曝險控制則是策略程式碼內的連續函數,因為 Protections 只能鎖倉、無法連續調整目標曝險(理由見 CP-005 第 2 節)。

---

## 目錄結構 / Repository Layout

```
├── docs/                          規劃文件、驗證結果、變更提案(change-proposals/)
├── user_data/
│   ├── strategies/
│   │   ├── RegimeFilteredMomentumBreakout.py   策略一(已測試,未通過)
│   │   └── VolatilityTargeting.py              策略四(已測試,混合結果)
│   └── configs/                   分層設定:common / testnet / live + secrets
├── analysis/                      統計驗證管線(離線,獨立於 Freqtrade 執行時期)
│   ├── significance.py            PSR / DSR / MinTRL / Reality Check
│   ├── sample_size.py             Newey-West 有效樣本數 / block bootstrap
│   ├── sharpe_difference.py       Ledoit-Wolf 穩健配對 Sharpe 差檢定(CP-004 第二層)
│   ├── benchmark.py               CP-004 比較基準(每日再平衡 50/50)
│   ├── vol_target.py              策略四核心邏輯(離線純函式版本)
│   ├── position_sizing_check.py   Fractional Kelly
│   ├── drawdown_mc.py             蒙地卡羅回撤模擬
│   ├── walk_forward.py            fold 邊界 / purge / CUSUM
│   ├── data_quality.py            K 棒品質檢查(缺漏/異常值,見 data-requirements.md)
│   ├── long_memory.py             變異數比檢定(市場前提診斷)
│   ├── analog_predictability.py   k-NN 類比可預測性診斷
│   ├── offline_exchange.py        離線交易所規格注入(Freqtrade 需要但本環境連不上即時 API)
│   ├── report.py                  端到端編排與通過判定
│   ├── tools/                     資料下載/匯入、參數掃描、策略四評估腳本
│   └── tests/                     127 個測試
```

## 快速開始 / Getting Started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install "freqtrade[hyperopt]" -r analysis/requirements.txt

# 1. 下載歷史資料(data.binance.vision 月封存,逐檔 SHA256 驗證;
#    api.binance.com 的即時 REST/WS 在部分網路環境有地區限制,見 data-requirements.md)
python analysis/tools/download_binance_vision.py --pair BTCUSDT --start 2017-08 --end 2026-07 \
  --out /tmp/BTCUSDT-1d.csv
python analysis/tools/ingest_market_data.py --pair BTC/USDT --input /tmp/BTCUSDT-1d.csv \
  --expected-start 2017-08-17 --expected-end 2026-07-31
# ETH/USDT 同上

# 2. 前置檢查:確認策略沒有 lookahead bias
freqtrade lookahead-analysis \
  -c user_data/configs/config-common.json \
  --strategy RegimeFilteredMomentumBreakout --timerange 20200101-20211231

# 3. 策略一:CP-003 事前指定參數掃描(取代 hyperopt)
python analysis/tools/run_parameter_scan.py

# 4. 策略四:波動度目標化評估
python analysis/tools/run_strategy4_evaluation.py
```

執行測試:`python -m pytest analysis/tests/ -v`

---

## 目前進度 / Current Status

**已完成**

- Phase 0–9 全部規劃文件並逐份確認;後續發現的方法論問題透過 `CP-001`–`CP-008` 八次預先登錄的變更提案修正
- 真實歷史資料已取得並通過品質檢查(9.0 年,SHA256 逐月驗證,見 [`data-requirements.md`](docs/data-requirements.md))
- 策略一:完整參數掃描 + DSR 顯著性檢定,**結果:未通過**(見 [`pass-b-results.md`](docs/pass-b-results.md))
- 策略四:假說設計、離線模擬驗證、Freqtrade 框架內技術驗證,CP-007 修正後**第一層通過、第二層(超額報酬)不通過**(見 [`strategy-4-cp007-results.md`](docs/strategy-4-cp007-results.md))
- 策略五:假說設計、風控重新校準(CP-008)、Freqtrade 實作、對真實資料正式判定,**第一層通過、第二層與核心因果檢定(配對比較)均不通過**(見 [`strategy-5-results.md`](docs/strategy-5-results.md))
- 統計驗證管線 18 個模組 + 137 個測試

**明確尚未完成的事**

- 策略二、三仍在 backlog,未進入驗證
- 三個已測試策略均未達到可投入真實資金(即使是模擬資金的 paper trading 正式階段)的標準,見 [`backtest-procedure.md`](docs/backtest-procedure.md) 7.1 節的資格條件
- 即時 dry-run / paper trading 尚未執行——本環境的出口網路對 Binance 即時 API(含 Testnet)有地區限制,技術驗證改用 Freqtrade backtesting 引擎完成(見 [`strategy-4-freqtrade-technical-demo.md`](docs/strategy-4-freqtrade-technical-demo.md))

---

## ⚠️ 安全注意事項 / Security Notes

- **絕不將 API Key / Secret 提交到 git**。金鑰放 `user_data/configs/secrets-*.json`(已在 `.gitignore`,glob 規則經實測驗證),範本見 `secrets-*.example.json`。
- **API 金鑰務必關閉提幣權限**,並搭配 IP 白名單。這是「金鑰外洩最大損失上限」唯一有效的結構性控制。
- **預設一律 dry-run**。`config-live.json` 的 `dry_run` 刻意保持 `true`,切換為 `false` 是需要二次確認的獨立動作。
- 完整規範(含金鑰輪替週期、日誌脫敏規則、金鑰外洩應變 runbook)見 [`security-policy.md`](docs/security-policy.md)。

## 免責聲明 / Disclaimer

本專案為個人研究用途。加密貨幣交易具高風險,可能損失全部本金。本專案的任何內容都不構成投資建議,作者不對使用本程式造成的任何損失負責。**目前兩個已測試策略均未通過驗證,不具備投入真實資金的依據。**

For personal research only. Cryptocurrency trading carries substantial risk of total loss. Nothing here constitutes financial advice. **Both strategies tested so far failed validation and provide no basis for risking real capital.**

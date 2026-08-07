# Phase 3 — 系統架構規格 / Architecture Specification

> 狀態:**🟡 草稿完成,待專案負責人審閱後確認 / DRAFTED — pending owner review.**
> 負責角色:system-architect
> 範圍限制(承 [`tech-stack-decision.md`](./tech-stack-decision.md) 的決定):本文件**不是**從零設計一套新交易系統的架構圖,而是定義**如何在 Freqtrade 的框架邊界內配置、客製化**——這是 Phase 8 提前拍板後,對 Phase 3 範圍的明確重新定義(見該文件「對後續 Phase 的影響」一節)。任何 Freqtrade 已經處理好的問題(執行引擎、回測引擎、交易所串接、訂單狀態機底層實作),本文件只說明「如何配置」與「驗證它確實滿足我們的需求」,不重新發明。
> 前提假設(承 [`scope.md`](./scope.md)):現貨、波段頻率、單人專案、負責人本人持有 kill switch 拍板權、金鑰僅交易不可提幣。
> 前提假設(承 [`strategy-hypothesis.md`](./strategy-hypothesis.md)):v1 僅實作策略一(Regime-Filtered Momentum Breakout:Donchian 突破 + 成交量確認 + ATR 移動停損),BTC/ETH 現貨。
> 前提假設(承 [`statistical-methodology.md`](./statistical-methodology.md)):本文件定義的資料輸出(交易紀錄、hyperopt epoch 紀錄)必須是 Phase 2 統計驗證管線可直接消費的格式,不得另起爐灶。

---

## 0. 文件定位與方法論

### 0.1 這不是一份「系統設計文件」

依 [`tech-stack-decision.md`](./tech-stack-decision.md) 的決定,Phase 3 的產出從「設計新系統架構」改為「定義如何配置/客製化 Freqtrade」。具體而言:

- **模組劃分、訂單狀態機、崩潰恢復、對帳邏輯的底層實作**——Freqtrade 已經做了 8 年、48K 星使用者驗證過,本文件的工作是**確認**它符合我們的需求、**指出**哪些地方仍需我們自己補齊,而不是重新畫一張新的架構圖。
- **本文件真正需要做設計決策的地方**,是 Freqtrade 刻意留給使用者決定的介面:策略類別怎麼寫、Protections 參數怎麼設、設定檔怎麼分環境、目錄怎麼組織、kill switch 的人為治理規則怎麼對應到 Freqtrade 的機制上。這些才是本文件的主體。

### 0.2 資料來源與查證範圍

本文件撰寫時實際查閱了以下 Freqtrade 官方文件(2026-08-07):

- [`bot-basics.md`](https://github.com/freqtrade/freqtrade/blob/develop/docs/bot-basics.md)——機器人生命週期、主迴圈
- [`configuration.md`](https://github.com/freqtrade/freqtrade/blob/develop/docs/configuration.md)——設定檔結構、多檔案合併、環境變數覆寫
- [`telegram-usage.md`](https://github.com/freqtrade/freqtrade/blob/develop/docs/telegram-usage.md)——Telegram 指令行為
- [`rest-api.md`](https://github.com/freqtrade/freqtrade/blob/develop/docs/rest-api.md)——REST API 端點與認證
- [`includes/protections.md`](https://github.com/freqtrade/freqtrade/blob/develop/docs/includes/protections.md)——Protections 設定方式與參數

**查證範圍之外、仰賴既有知識的部分**(已在文中對應段落標記,實作前建議依當時版本文件重新核實):`user_data/` 目錄由 `freqtrade create-userdir` 產生的標準子目錄清單、`custom_stoploss`/`custom_stake_amount`/`confirm_trade_entry` 等 callback 的確切簽章、`IntParameter`/`DecimalParameter` 等 hyperopt 參數宣告方式、Binance testnet 在 ccxt/Freqtrade 設定中的端點覆寫寫法、`/locks` 指令與端點的確切行為。這些屬於「確定存在、但精確語法可能隨版本變動」的細節,標記為 Phase 7(執行層規格)或實作階段核實項目,不影響本文件的架構決策本身。

### 0.3 三種執行模式共用同一套介面

呼應 [`development-plan.md`](./development-plan.md) Phase 3 的離開條件之一(「Paper/Backtest/Live 共用同一套執行介面」):Freqtrade 的 `IStrategy` 介面(`populate_indicators`/`populate_entry_trend`/`populate_exit_trend`/`custom_stoploss` 等 callback)在 **backtesting、hyperopt、dry-run(paper trading)、live** 四種模式下執行**完全相同的策略程式碼**,差異只在框架外層「怎麼處理訂單」(backtesting 用歷史資料模擬撮合;dry-run 接真實行情但不真的下單;live 透過 ccxt 送出真實訂單)。這件事**不需要我們設計**,是 Freqtrade 架構本身的既有保證,也是 [`reference-research.md`](./reference-research.md) 當初評估 Freqtrade 的核心理由之一。

本文件唯一需要決策的相關問題是:**四種模式之間唯一應該不同的東西是設定檔**(`dry_run` 開關、資料庫路徑、交易所端點)——策略程式碼本身、Protections 設定、Kelly 倉位公式,**一律不應該因為換模式而改程式碼**。這條原則直接決定第 6 節(環境設定分離)的設計。

---

## 1. 模組邊界與介面總覽 / Module Boundaries

呼應 [`development-plan.md`](./development-plan.md) Phase 3 要求的「模組劃分圖」,以下是傳統五層(資料/策略/風控/執行/監控)對應到 Freqtrade 內部組件的位置,以及各層由誰負責:

| 層 | 由誰負責 | 我們要做的事 |
|---|---|---|
| **資料層** | Freqtrade 核心(透過 ccxt 抓取 OHLCV、管理 pairlist、feather/parquet 快取) | 不重新設計;僅在設定檔指定 pairlist(BTC/USDT、ETH/USDT)、timeframe、資料下載範圍(Phase 6 定案) |
| **策略層(訊號)** | 我們的 `IStrategy` 子類別(`user_data/strategies/`) | **本文件第 3 節**——唯一需要我們寫程式碼邏輯的層 |
| **風控層** | Freqtrade Protections 外掛(`ProtectionManager`,框架層級)+ 策略內的 `custom_stoploss`/`custom_stake_amount` | **本文件第 4 節**——參數由我們定,但強制執行邏輯是框架的,不是我們寫的 |
| **執行層** | Freqtrade 核心(訂單送出、ccxt 抽象化、訂單狀態追蹤) | 不重新設計;Phase 7(`execution-spec.md`)只處理連線相關參數,不重新設計冪等下單/重連邏輯(見 [`tech-stack-decision.md`](./tech-stack-decision.md)) |
| **監控層** | Freqtrade 內建日誌 + SQLite 交易紀錄 + 選配的 Telegram/REST API | **本文件第 5 節**——哪些要客製化接線 |

**訂單狀態機**(development-plan 要求的另一項):Freqtrade 的 `Trade`/`Order` 資料模型已定義掛單/成交/取消/拒絕的完整狀態轉換(對應 ccxt 標準訂單狀態),並且**不是只在啟動時對帳一次**——每個主迴圈迭代都會對「有未結訂單的持倉」呼叫更新邏輯(`update_trade_state`),持續向交易所查詢實際訂單狀態並同步回本地資料庫。這代表本地帳本與交易所之間的對帳是**迴圈內建的常態行為**,不是額外要接的功能。我們不重新設計這套狀態機;第 6 節只處理「進程重啟後」這個更嚴苛的特例。

---

## 2. 目錄結構 / Repository & Directory Structure

### 2.1 整體結構

```
binance_trading_bot/                         (repo root)
├── docs/                                     # 規劃文件(已存在)
│   ├── scope.md                              # Phase 0 ✅
│   ├── strategy-hypothesis.md                # Phase 1 ✅
│   ├── statistical-methodology.md            # Phase 2 🟡
│   ├── architecture-spec.md                  # Phase 3(本文件)
│   ├── risk-policy.md                        # Phase 4(尚未撰寫)
│   ├── security-policy.md                    # Phase 5(尚未撰寫)
│   ├── backtest-procedure.md                 # Phase 6(尚未撰寫)
│   ├── execution-spec.md                     # Phase 7(尚未撰寫)
│   ├── tech-stack-decision.md                # Phase 8 ✅
│   ├── go-no-go-checklist.md                 # Phase 9(尚未撰寫)
│   ├── development-plan.md
│   ├── stable-profitability-roadmap.md
│   └── reference-research.md
│
├── user_data/                                # Freqtrade 標準根目錄(由 `freqtrade create-userdir` 建立)
│   ├── strategies/
│   │   └── RegimeFilteredMomentumBreakout.py # Phase 1 策略,Phase 10+ 才實作
│   ├── configs/                              # 我們的命名慣例(見第 6 節,取代 Freqtrade 預設單一 config.json)
│   │   ├── config-common.json                # 共用、不含機密
│   │   ├── config-testnet.json                # Testnet/dry-run 環境覆寫
│   │   ├── config-live.json                  # 未來實盤環境覆寫(Phase 9 Go/No-Go 前不啟用)
│   │   ├── secrets-testnet.example.json      # 佔位範例,結構同 .env.example 的角色
│   │   └── secrets-live.example.json         # 佔位範例
│   ├── hyperopts/                            # 自訂 hyperopt loss function(若 Phase 6 判定需要 DSR 導向的目標函數)
│   ├── notebooks/                            # 臨時分析用(選用)
│   ├── data/                                 # OHLCV 快取(已在 .gitignore)
│   ├── backtest_results/                     # 回測輸出(已在 .gitignore)
│   ├── hyperopt_results/                     # hyperopt epoch 紀錄(**需新增到 .gitignore**,見 2.3)
│   └── logs/                                 # 日誌(已在 .gitignore)
│
├── analysis/                                 # Phase 2 統計後處理腳本的落腳目錄(NEW,Phase 6 backtest-analyst 填入內容)
│                                              # 消費 user_data/backtest_results/ 與 hyperopt_results/ 的輸出,
│                                              # 實作 DSR / Newey-West / block bootstrap / Monte Carlo(statistical-methodology.md)
│
├── .env / .env.example                       # 見第 6 節,建議調整變數命名
├── .gitignore                                # 見 2.3 需新增項目
└── README.md
```

### 2.2 關鍵目錄決策說明

| 決策 | 理由 |
|---|---|
| 策略檔案放 `user_data/strategies/`,檔名與類別名一致(PascalCase) | Freqtrade 標準慣例(`freqtrade new-strategy` 產生的結構),`--strategy` 參數直接對應類別名,維持與框架工具鏈(`freqtrade list-strategies`、hyperopt CLI)相容,不需要自訂載入邏輯 |
| **自訂 Protections 沒有獨立設定檔**——宣告在策略類別內的 `protections` property | 依 Freqtrade 現行文件([`includes/protections.md`](https://github.com/freqtrade/freqtrade/blob/develop/docs/includes/protections.md)),Protections 是策略類別的一個 property,回傳一組設定字典的清單,而非 `config.json` 的頂層鍵值。這個放置位置**不是我們可以自由選的**,是 Freqtrade 現行版本的既定介面——但這個限制反而對第 4 節的治理需求有利處,見該節說明。 |
| 新增 `user_data/configs/`(而非用 Freqtrade 預設的單一 `user_data/config.json`) | 支撐第 6 節的環境分離設計;Freqtrade 原生支援多個 `--config` 疊加合併(後者覆寫前者),我們用這個機制做 common/env/secrets 三層疊加,而不是維護兩份幾乎重複的完整設定檔 |
| 新增 `analysis/` 目錄於 repo 根目錄,不放在 `user_data/` 底下 | Phase 2 的後處理腳本是**獨立於 Freqtrade 執行時期**的離線分析工具(讀取 Freqtrade 產出的靜態檔案),概念上不屬於 Freqtrade 的 `user_data` 命名空間;放在根目錄下也讓它不會被誤認為是 Freqtrade 認得的特殊目錄 |
| `user_data/hyperopts/` 目錄先保留、不先寫內容 | 是否需要自訂 hyperopt loss function(讓優化目標貼合 DSR 精神,而非單純最大化樣本內 Sharpe)是 Phase 6 的方法論決策,本文件只保留標準目錄位置,不越權決定內容(避免過度工程——沒確定需要就不先設計) |

### 2.3 需要對現有檔案做的更動(留待實作階段執行,本文件僅記錄需求)

- `.gitignore` 需新增 `user_data/hyperopt_results/`、`user_data/configs/secrets-*.json`(目前的 `secrets.json` 是精確檔名比對,不會擋到我們新命名的 `secrets-testnet.json`/`secrets-live.json`,需要用 glob pattern 明確涵蓋)。
- `.env.example` 的變數命名建議依第 6 節調整為環境明確區分的形式(現行的 `BINANCE_API_KEY`/`BINANCE_USE_TESTNET` 沒有結構性地防止「忘記切換 flag 導致金鑰用錯環境」)。

---

## 3. 策略類別結構 / Strategy Class Structure

本節把 [`strategy-hypothesis.md`](./strategy-hypothesis.md) 的策略一(Donchian 突破 + 成交量確認 + ATR 移動停損)對應到 Freqtrade `IStrategy` 的介面上。**這是介面/結構決策,不是實作**——具體公式、參數搜尋範圍屬於 Phase 6([`backtest-procedure.md`](./backtest-procedure.md)),精確數字屬於 Phase 4([`risk-policy.md`](./risk-policy.md))。

### 3.1 各 callback 對應表

| Freqtrade 介面點 | 對應 Phase 1 概念 | 結構決策 |
|---|---|---|
| `populate_indicators(dataframe, metadata)` | 計算 Donchian 上下軌、成交量均量、ATR | 三組指標都是對整個 dataframe 做向量化計算(不用逐列迴圈,呼應 [`reference-research.md`](./reference-research.md) 提到的 Freqtrade 慣例)。**Donchian 上軌必須用 `.shift(1)`**(或等價做法),確保「今天的突破」比較的是「不含今天在內的過去 N 日最高價」——這不是效能考量,是 [`strategy-hypothesis.md`](./strategy-hypothesis.md) 明訂的「強制隔根進場,不可用未收盤 K 棒判斷」規則在指標層級的落實。 |
| `populate_entry_trend(dataframe, metadata)` | 進場訊號:收盤突破 Donchian 上軌 **且** 成交量 ≥ M 日均量 × X | 純訊號產生,設定 `enter_long` 欄位,不涉及部位大小或風險計算(那是 `custom_stake_amount` 的職責,見下)。搭配 `process_only_new_candles: true`(設定檔層級)確保只在收盤 K 棒上評估訊號,而非每次 tick 都重算——這是 Freqtrade 內建、只需開啟的選項,不需要自己實作防護。 |
| `populate_exit_trend(dataframe, metadata)` | 「軟」出場訊號:跌破 Donchian 下軌 | 只處理**基於指標門檻**的出場路徑。**ATR 移動停損不放在這裡**——原因見下一列。 |
| `custom_stoploss(pair, trade, current_time, current_rate, current_profit, ...)` | 「硬」出場路徑:ATR × k 移動停損 | ATR 移動停損是**路徑相依**的(需要知道進場價、目前最高價、目前 ATR 值),`populate_exit_trend` 只能對整個 dataframe 做一次性運算,拿不到單筆交易的狀態(`Trade` 物件),所以必須用 `custom_stoploss`,它明確拿得到 `trade`(含進場價、進場時間)並可透過 `self.dp.get_analyzed_dataframe()` 取回當下 ATR 值,回傳結果化的停損距離。需設定 `use_custom_stoploss = True` 才會啟用此 callback,`stoploss` 類別屬性仍須保留一個保守的硬底線值(Freqtrade 要求即使用 custom stoploss 也要有這個 fallback)。 |
| `custom_stake_amount(pair, current_time, current_rate, proposed_stake, min_stake, max_stake, ...)` | [`statistical-methodology.md`](./statistical-methodology.md) 第 5 節的 `position_size = (risk_fraction × Equity) / (k × ATR)` 公式 | 這是 Kelly 分析與硬上限公式在 Freqtrade 介面上的**唯一合理接線點**——不應該用靜態的 `stake_amount` 設定值,因為部位大小必須依當下 ATR 動態換算。具體公式與 `risk_fraction` 數字由 Phase 4 定案,這裡只確認「有這個 callback、它是對的接線點」。 |

### 3.2 Hyperopt 可調參數的介面分類

[`statistical-methodology.md`](./statistical-methodology.md) 第 2 節的 DSR 計算依賴「hyperopt 實際試過幾組參數」,因此哪些參數屬於 hyperopt 搜尋空間、哪些不是,是一個**必須在架構層級先分類清楚**的問題:

| 參數 | 屬於策略訊號邏輯 | Hyperopt 可調? | 理由 |
|---|---|---|---|
| Donchian 週期 N | 是 | **是** | Phase 1 明訂待驗證參數,用 `IntParameter` 宣告,納入 `buy` space |
| 成交量倍數 X | 是 | **是** | 同上 |
| 成交量均量週期 M | 是 | **是** | 同上 |
| ATR 週期 | 是 | **是** | 同上,通常較窄範圍 |
| ATR 停損倍數 k | 是 | **是** | 同上,納入 `sell`/`protection` space(依 Freqtrade hyperopt space 分類慣例) |
| Kelly 分數 c、硬上限 risk_fraction | **否**——這是風控參數,不是訊號參數 | **否,絕對不可放進 hyperopt 搜尋空間** | [`statistical-methodology.md`](./statistical-methodology.md) 5.3 節明訂 `c ≤ 0.5` 是治理硬上限,若讓 hyperopt 自由搜尋 c,優化器會把 c 推到能「最大化回測績效」的數值,直接架空風控上限的意義——這是一個容易被忽略但後果嚴重的架構陷阱,必須在介面設計階段就排除,不能寄望之後靠人工檢查參數搜尋結果來補救。`custom_stake_amount` 內讀取的 `risk_fraction` 應該來自**寫死的設定值**(對接 Phase 4 `risk-policy.md` 定案的數字),而不是 `self.` 開頭的 hyperoptable parameter。 |
| Protections 的 `stop_duration`、`trade_limit` 等 | 否,風控參數 | **否** | 同上理由,且 Freqtrade 目前介面本身也不支援對 `protections` property 內的參數做 hyperopt(它是靜態回傳的清單,不是 hyperoptable parameter),這點與我們的治理需求恰好一致 |

### 3.3 其他結構性要求

- **`startup_candle_count`**:必須設為至少 `max(N, M, ATR週期) + 緩衝`,否則回測/實盤起始的一段資料會因指標尚未穩定(NaN)而產生錯誤訊號。這與 [`statistical-methodology.md`](./statistical-methodology.md) 3.3 節的 embargo 概念**是兩件不同的事**(一個是指標暖機、一個是防資訊洩漏),容易混淆,實作時需注意分開處理,不要誤以為設定其中一個就滿足另一個的需求。
- **`order_types.stoploss_on_exchange: true`(建議)**:讓 ATR 停損不只是 Freqtrade 進程內的邏輯判斷,而是同時在交易所掛一張真實的停損掛單。這件事的價值不在策略邏輯本身,而在**系統韌性**——見第 6 節與第 5 節對「進程崩潰」失效模式的討論,這裡先point 出這是策略結構決策的一部分(需要在 `populate_entry_trend` 產生訊號並成交後,交由 Freqtrade 的下單邏輯自動代掛,不需要我們手動送出額外的停損單)。

---

## 4. Kill Switch / 治理落地 / Kill-Switch & Governance Embodiment

### 4.1 回顧 Phase 0 的治理規則

[`scope.md`](./scope.md) 第 5 節已確認:

1. 風控參數(停損%、每日虧損上限等)不得在虧損當下臨時調鬆,調整須走正式變更流程。
2. **Kill switch 觸發後必須強制冷靜期**(如 24 小時),不可觸發後立刻重啟繼續交易。
3. API 金鑰僅交易、禁止提幣。

本節任務是把這三條規則**具體對應到 Freqtrade 的實際機制上**,並誠實標出 Freqtrade 沒有提供、需要我們自己補的部分。

### 4.2 Freqtrade 提供的機制清單

| 機制 | 觸發方式 | 行為 | 對應治理規則 |
|---|---|---|---|
| **Protections(`StoplossGuard`/`MaxDrawdown`/`CooldownPeriod`/`LowProfitPairs`)** | 框架自動監測交易結果,達門檻自動觸發 | 鎖定新進場(依設定為單一交易對或全域),持續 `stop_duration` | 對應「自動熔斷」情境,是規則 2 中「自動觸發」的那一半 |
| **Telegram `/stopentry`(暫停)** | 人工下指令 | 阻止新進場,**既有持倉持續依原本的 ROI/出場訊號/停損邏輯正常管理** | 對應「暫停開新倉但不放棄已有部位風控」的中間狀態,適合「懷疑資料品質」而非「緊急停損」的情境 |
| **Telegram `/stop` 或 REST `POST /stop`** | 人工下指令或程式化呼叫 | 依 Freqtrade 文件為「完全停止交易器運作」——**需在實作前(Phase 7)核實此指令是否仍持續管理已開倉部位的動態出場邏輯**(不同版本文件對此描述細節可能有出入,見 4.4 的保守設計如何不依賴此細節) | 對應規則 2 的「人工緊急停止」 |
| **Telegram `/forceexit <id\|all>` 或 REST `POST /forceexit`** | 人工下指令 | 立即以市價強制平掉指定或全部持倉,略過 ROI 判斷 | Kill switch 的「立即清空曝險」動作 |
| **REST API 認證(Basic Auth / JWT)+ 預設綁定 `127.0.0.1`** | 設定檔啟用 `api_server.enabled` | 未經授權者無法遠端觸發任何啟停動作 | 支撐「拍板權明確、不可被他人誤觸」的前提 |

### 4.3 確認:Protections 在架構上不可被策略程式碼繞過

這是本節最重要的架構確認,任務要求明確驗證:**Protections 由框架層級的 `ProtectionManager` 執行,運作在策略程式碼之外**。具體而言:

- Protections 的**判斷邏輯**(是否已達 `trade_limit`、是否仍在 `stop_duration` 鎖定期內)是 Freqtrade 核心的外掛程式碼(位於框架本身,不是我們寫的策略檔案),在**進場流程判斷是否要真的送出訂單之前**被呼叫。
- 策略類別的 `populate_entry_trend`、`confirm_trade_entry` 等 callback **無法得知、也無法覆寫**一個已生效的 Protection 鎖定——即使 `populate_entry_trend` 產生了進場訊號、`confirm_trade_entry` 回傳 `True`,只要對應的交易對(或全域)正處於鎖定狀態,框架會在這些 callback 之後、實際下單之前擋下,策略程式碼完全沒有可以介入的接口。
- 這與策略程式碼本身完全解耦,滿足「不可被策略程式碼繞過」的要求,**不需要我們額外做任何事去保證這件事**——這是 Freqtrade 架構的既有性質,本文件的工作只是確認並記錄下來,而非設計。

**但要注意一個容易被忽略的細節**:Protections 的**參數**(`stop_duration`、`trade_limit` 等)雖然強制執行的邏輯在框架層,宣告的位置卻是策略類別檔案內的 `protections` property(見 2.2 節)。這代表:**改動熔斷參數,技術上等於改動策略程式碼、需要重新部署**。我們把這件事**視為優點而非缺點**採用:

> 修改風控熔斷參數需要走「修改程式碼 → commit(附修改原因與日期)→ 重新部署」的完整流程,天然比修改一個執行中程序讀取的 JSON 設定檔更慢、更留痕——這正好呼應 [`scope.md`](./scope.md) 規則 1「不可在虧損當下憑情緒現場修改」的精神。Git commit history 本身就是「正式變更流程」要求的書面紀錄,不需要另外設計一套變更審批系統。

### 4.4 人工緊急停止的具體流程(Runbook 設計,非依賴 `/stop` 確切語意)

因為 `/stop` 是否仍持續管理已開倉部位這個細節,不同 Freqtrade 版本的文件描述可能有出入(見 4.2 表格備註),本文件設計一套**不依賴這個細節、任何情況下都安全**的人工緊急停止順序:

1. **先 `/forceexit all`(或 REST 對等端點)**——立即以市價清空所有持倉。這一步执行後,不論 `/stop` 之後是否還會管理持倉,都已經沒有持倉需要管理。
2. **再 `/stop`**——完全停止交易迴圈,避免任何新訊號被評估。
3. 若第 3 節建議的 `stoploss_on_exchange: true` 有啟用,即使步驟 1、2 之間出現任何延遲或失敗,交易所上仍有掛著的停損單作為最後一層防護——這是 3.3 節的策略結構決策直接服務本節治理需求的地方。

這個順序刻意設計成**不論 `/stop` 的確切行為為何都成立**——先清空部位再停機,永遠比先停機、再擔心停機後持倉是否還受保護要安全。

### 4.5 強制冷靜期:Freqtrade 不提供,必須靠程序落實

**這是本文件必須誠實承認 Freqtrade 做不到的地方**:Freqtrade 沒有任何機制能阻止一個人在 `/stop` 之後一分鐘就手動再打一次 `/start`。24 小時冷靜期是**人為紀律要求**,不是技術限制,因此不能只靠技術手段解決,但技術可以提供恰到好處的輔助摩擦力:

**自動觸發情境(部分技術可強制)**:對於 `MaxDrawdown` Protection 自動觸發的情況,可以把它的 `stop_duration` 直接設為與 Phase 0 的 24 小時冷靜期數字一致(換算為對應的分鐘或 K 棒數,依 Freqtrade 該參數的實際計時單位,Phase 4 定案時核實)。這樣一來,**即使負責人在自動熔斷後立刻想重啟**,框架本身仍會在 24 小時內拒絕開新倉——這是自動觸發情境下,技術層面能提供的實質強制力,不完全只靠自制力。

**人工觸發情境(純程序性,技術上不強制)**:對於負責人主動判斷要按下 kill switch(例如發現程式邏輯有 bug、對盤面不信任等,不是由 `MaxDrawdown` 自動偵測到的情境),Freqtrade 完全沒有對應機制。設計為:

1. **程序要求**:觸發後,在 [`risk-policy.md`](./risk-policy.md)(Phase 4)將定義的事件紀錄機制中,寫下觸發時間、原因;24 小時後**重新覆核觸發原因是否已排除**,才手動執行 `/start`,並同樣記錄覆核時間與結論。這是規則 2 要求的「覆核觸發原因」在流程上的具體落實。
2. **刻意不做的事(避免過度工程)**:本文件**不**建議在 v1 建置一套自動化的「技術性攔截重啟」機制(例如一個外部看門狗程序,物理上讓 `freqtrade trade` 在 24 小時內無法啟動)。理由:(a) 這是單人專案,真正的風險是衝動下的自我決策,不是惡意繞過保護機制的對抗性場景,加裝對抗性防護是在解決一個不存在的威脅模型;(b) 這類機制本身會增加系統複雜度與新的失效模式(例如看門狗程序自己掛掉導致真正需要重啟時被卡住),不符合「設計現在需要的東西,而非投機性基礎設施」的原則。若未來實務上發現純程序性紀律不可靠(例如真的發生了衝動重啟),再回頭考慮加裝輕量級的技術摩擦(例如一個啟動前檢查時間戳的小腳本,而非完整看門狗服務),不在本階段預先建置。

### 4.6 API 金鑰禁止提幣

這是 Phase 5([`security-policy.md`](./security-policy.md))的權責範圍,本文件僅將其列為**架構前提假設**:本文件所有設計(尤其是第 6 節的環境分離)都假設交易所端已經把金鑰設定為不可提幣,若這個前提不成立,本文件的其餘設計(尤其是把「金鑰誤用於錯誤環境」的爆炸半徑控制在「用錯環境下單」而非「資產被轉走」)也需要重新評估。

---

## 5. 觀測性與日誌 / Observability & Logging

### 5.1 Freqtrade 預設提供的部分

| 項目 | 預設行為 |
|---|---|
| **SQLite 交易資料庫** | 每一筆交易的進出場時間、價格、`profit_ratio`、`exit_reason`、手續費、`stake_amount` 等欄位自動記錄,是**權威的結構化交易紀錄**,不需要另外設計 schema |
| **Backtest/Hyperopt 匯出的交易明細(JSON)** | `freqtrade backtesting --export trades` 匯出的欄位(`pair`, `open_date`, `close_date`, `open_rate`, `close_rate`, `profit_ratio`, `profit_abs`, `exit_reason`, `stake_amount` 等)與 SQLite 記錄的欄位高度一致,**這就是 [`statistical-methodology.md`](./statistical-methodology.md) 全篇要求的「交易報酬序列」原始資料來源**,不需要另外設計一套匯出格式 |
| **主控台/日誌檔輸出** | 生命週期事件、下單、成交、錯誤堆疊皆有記錄,層級可調(`INFO`/`DEBUG`) |
| **Protection 鎖定狀態** | 透過 Telegram `/locks` 指令與對等的 REST 端點可查詢目前生效中的鎖定(哪個交易對、何時解除)——這是風控否決決策的可追溯管道 |
| **Telegram 通知(需先設定 token/chat_id 才會啟用)** | 進場、出場、錯誤/警告等事件推播 |

### 5.2 需要客製化接線的部分

| 項目 | 為什麼需要客製化 | 建議做法 |
|---|---|---|
| **Telegram bot 啟用** | 預設關閉,需自行申請 bot token 並填入設定檔 | Testnet 環境即可先接上,習慣這個管道;正式上線前確認 chat_id 僅限負責人本人可見 |
| **REST API server 啟用與加固** | 預設關閉;若啟用必須強密碼 + 隨機 JWT secret,且**只綁定 `127.0.0.1`**,不對外開放 | 若需要遠端操作(如手機下 `/stop`),官方建議走 SSH tunnel 或 VPN,而不是把 `api_server` 直接暴露在公網——這條建議直接沿用官方文件的安全建議,不需要另外設計 |
| **Phase 2 統計管線的輸入介面** | Phase 2 需要「校正過自相關的交易報酬序列」等後處理輸入,但**原始資料**(交易明細、hyperopt epoch 紀錄)已由 Freqtrade 提供,不需要重新設計格式 | `analysis/` 目錄下的 Phase 6 腳本直接讀取 `user_data/backtest_results/` 與 `user_data/hyperopt_results/` 的既有匯出檔案,批次離線處理即可 |
| **日誌等級與輪替(rotation)** | Freqtrade 本身不做日誌輪替;長時間常駐運行若不處理,日誌檔會無限成長 | 交由部署層(OS 的 `logrotate` 或 Docker 的 log driver 設定)處理,不在 Freqtrade 應用層解決,避免在應用程式裡重造一個日誌管理系統 |
| **日誌脫敏** | Phase 5([`security-policy.md`](./security-policy.md))權責,本文件僅標記需求存在 | 待 Phase 5 定案哪些欄位需遮蔽;架構層面的建議是預設用 `INFO` 而非 `DEBUG` 等級運行常駐服務,降低意外把請求細節寫進日誌的機率 |

### 5.3 刻意不做的事(避免過度工程)

不建置自訂的事件匯流排(event bus)或 webhook 接收服務去「即時」串接 Phase 2 的統計分析。Phase 2 的驗證管線([`statistical-methodology.md`](./statistical-methodology.md))本質上是**離線批次分析**(walk-forward、DSR、蒙地卡羅模擬都需要一段完整的歷史/樣本外資料,不是逐筆交易即時觸發的計算),用 Freqtrade 既有的檔案匯出 + 批次讀取就足夠,建置即時事件管線是在解決一個目前不存在的需求(v1 沒有需要「秒級反應」的下游系統)。Freqtrade 確實也提供通用 `webhook` 設定(可對任意 URL 推播事件),若未來真的需要即時儀表板,屆時再評估啟用,不在本階段預先建置接收端。

---

## 6. 狀態對帳與崩潰復原 / State Reconciliation & Crash Recovery

### 6.1 Freqtrade 框架處理的部分(呼應 [`stable-profitability-roadmap.md`](./stable-profitability-roadmap.md) 的 state desync 疑慮)

- **SQLite 交易資料庫是本地帳本的唯一真實來源(source of truth)**。進程重啟時,Freqtrade 會讀出所有 `is_open=True` 的交易紀錄,並針對每一筆去查詢交易所上對應訂單的實際狀態(成交、部分成交、掛單中、已取消),依查詢結果更新本地紀錄——這正是 `stable-profitability-roadmap.md` 中 system-architect/execution-engineer 角色提出的「本地帳本與交易所對帳」需求,**已經被框架處理**,不需要我們自己實作。
- 這個對帳邏輯不只在啟動時執行——如第 1 節所述,只要有未結訂單的持倉,每個主迴圈迭代都會持續更新訂單狀態,是常態行為而非崩潰後才觸發的特殊流程。
- **結論:「進程崩潰後重啟,本地狀態與交易所狀態不一致」這個失效模式,已被 Freqtrade 的架構設計覆蓋,不需要本文件重新設計一套對帳機制。**

### 6.2 仍需我們自己注意的部分

- **前提假設:交易用的 API 金鑰帳戶不應同時有人工在同一帳戶手動下單、入金、出金。** Freqtrade 的對帳邏輯是針對「Freqtrade 自己下的訂單」設計的;若帳戶上出現 Freqtrade 完全不知情的人工操作(例如用同一組帳戶在 Binance 網頁介面手動賣出部位),會產生框架對帳邏輯處理範圍之外的落差。這不是程式碼缺陷,是操作面的紀律要求,建議明訂為「此帳戶僅供本機器人使用」的硬性規則(可併入 Phase 5 `security-policy.md`)。
- **進程崩潰的自動重啟**:Freqtrade 本身不會在自己崩潰後自我重啟,需要外層的行程監督機制。建議用 systemd service(`Restart=on-failure`)或 Docker 的 restart policy(`unless-stopped`/`on-failure`)——這是部署層的標準做法,不需要在應用程式內建置自訂的監督邏輯。搭配 6.1 節的對帳行為,「行程監督自動重啟 + 啟動時自動對帳」兩者相加,就是對「進程崩潰」這個失效模式的完整回應。
- **資料源中斷(data feed drops)**:短暫的行情資料抓取失敗,Freqtrade 內部(ccxt 層 + Freqtrade 自身的錯誤處理)會重試,若某次迭代資料無法取得,該次迭代對受影響的交易對**跳過訊號評估**而非崩潰——這是合理的預設行為,不需要我們額外處理。**持續性、長時間的資料源中斷**(例如交易所 API 中斷數小時)則屬於「進程仍活著但實質失能」的情境,Freqtrade 預設不會主動告警;本文件將此列為 Phase 7([`execution-spec.md`](./execution-spec.md))待補的輕量級存活監控需求(例如外部排程定期呼叫 API 的健康檢查端點),**不在本文件展開設計**——這是有意識地把一個明確但範圍尚未確定的問題留給更合適的階段,而非現在就設計一整套監控基礎設施。
- **崩潰到重啟之間的保護空窗期**:進程down 掉的期間,若沒有 `stoploss_on_exchange: true`(第 3 節建議),已開倉部位在這段期間完全沒有任何停損保護(因為停損判斷邏輯是進程內計算的)。這是第 3 節建議啟用 `stoploss_on_exchange` 的核心理由——它讓保護機制的存續不依賴 Freqtrade 進程本身是否存活。

---

## 7. Testnet / Live 設定分離 / Environment Config Separation

### 7.1 設計目標

呼應 `README.md`、[`scope.md`](./scope.md)、`stable-profitability-roadmap.md` 已反覆提及的「Testnet/Mainnet 混淆」疑慮:本節的目標是讓「目前是哪個環境」在架構層級**結構性地不可能搞混**,而不是靠人工每次啟動時自己小心檢查。Phase 5([`security-policy.md`](./security-policy.md))之後會在此基礎上再加一層「啟動時強制顯示環境並要求二次確認」的程序性防護——本節提供的是它可以疊加上去的結構基礎。

### 7.2 設定檔分層設計

依 Freqtrade 官方文件確認的機制(多個 `--config` 依序合併,後者覆寫前者;可用獨立檔案存放機密):

| 檔案 | 內容 | 是否進 git |
|---|---|---|
| `user_data/configs/config-common.json` | 環境無關的共用設定:`stake_currency`、`pairlist`(BTC/USDT, ETH/USDT)、`timeframe`、`order_types`、日誌格式等 | 是 |
| `user_data/configs/config-testnet.json` | `dry_run`(或 testnet 對應設定)、`db_url` 指向 `tradesv3-testnet.sqlite`、`logfile` 指向獨立路徑、交易所端點若需指向 Binance Spot Testnet(見 7.4 備註) | 是 |
| `user_data/configs/config-live.json` | 對應的 live 版本設定(`db_url` 指向 `tradesv3-live.sqlite` 等)——**在 Phase 9 Go/No-Go 通過前,此檔案即使存在也不應被任何啟動指令引用** | 是 |
| `user_data/configs/secrets-testnet.json` | Testnet API key/secret | **否,加入 .gitignore** |
| `user_data/configs/secrets-live.json` | Live API key/secret | **否,加入 .gitignore** |

啟動時依環境組合對應的 `--config` 順序(範例,概念示意而非實際部署腳本):Testnet 環境依序疊加 common → testnet → secrets-testnet;Live 環境依序疊加 common → live → secrets-live。**兩個環境的啟動指令在檔名層級就完全不重疊**——不存在「同一份指令改一個布林值切換環境」的模式,降低「以為在測試網、其實在改的是實盤設定檔」的可能性。

### 7.3 環境變數命名建議

現行 `.env.example` 的 `BINANCE_API_KEY`/`BINANCE_API_SECRET`/`BINANCE_USE_TESTNET` 命名方式,結構上允許「金鑰是同一組,靠一個布林 flag 切換」——一旦 flag 不小心設反,金鑰仍然「有效」,只是打到錯的環境,這正是我們要結構性避免的模式。建議調整為環境各自獨立命名(**本文件僅提出目標命名方向供 Phase 5/實作階段採用,不在此變更 `.env.example` 本身**):

- 各環境各自獨立的金鑰變數名(而非共用一組變數 + 一個切換 flag),搭配 Freqtrade 已確認支援的 `FREQTRADE__EXCHANGE__KEY` / `FREQTRADE__EXCHANGE__SECRET` 環境變數覆寫機制,分別對應到啟動 testnet/live 時各自載入的 `.env` 內容
- 好處:即使啟動腳本疊加了錯誤的 `--config` 檔案組合,只要對應的環境變數集合沒被載入,金鑰本身就是「不存在/不匹配」而非「用錯環境但仍然有效」——把「設定檔用錯」與「金鑰用錯」兩個獨立故障模式都納入防護,而不是只防其中一個

### 7.4 已知待查證項目

Binance Spot Testnet 在 ccxt/Freqtrade 設定中的端點覆寫(例如透過 `exchange.ccxt_config` 覆寫 API URL),精確的設定鍵值會隨 Freqtrade/ccxt 版本演進,本文件不假設特定語法,留給 Phase 7 或實作階段依當時版本的官方文件核實。本節的架構決策(獨立設定檔、獨立資料庫、獨立金鑰變數)**不依賴這個細節的具體寫法**,只依賴「testnet 與 live 用完全不同的設定檔案 + 完全不同的金鑰來源」這個結構性原則,原則本身不會因為 ccxt 版本更新而改變。

---

## 8. Fork vs. Pip 依賴決策 / Fork vs. Pip Dependency Decision

[`tech-stack-decision.md`](./tech-stack-decision.md) 留下的未決問題,本文件在此定案。

### 8.1 決定:以 pip 套件(或官方 Docker image)方式引用 Freqtrade,搭配獨立的 `user_data/`;**不 fork**

### 8.2 理由

1. **v1 範圍不需要修改框架核心行為**。策略邏輯、ATR 移動停損、Kelly 倉位公式、Protections 參數,全部都透過 `IStrategy` 已公開的介面(`populate_*`、`custom_stoploss`、`custom_stake_amount`、`protections` property)完成,沒有任何一項需要碰觸 Freqtrade 內部的訂單執行、交易所串接、或核心迴圈邏輯。這正是 [`reference-research.md`](./reference-research.md) 當初評估「選項 B」的前提,實際盤點過本文件第 3 節的所有介面點後,這個前提成立。
2. **Fork 的維護成本沒有對應的效益**。Fork 之後,上游每一次安全性修補、交易所 API 相容性更新,都需要我們自己手動 merge/rebase——對單人專案而言這是持續的負擔,而我們目前完全沒有需要偏離上游行為的理由去承擔這個負擔。
3. **釘選版本(pin version)已經能達到「可重現、可控」的目標,不需要靠 fork**。在依賴宣告(如 `requirements.txt` 或 Docker image tag)中鎖定明確的 Freqtrade 版本號,升級版本變成一個刻意、可測試、有紀錄的決策(呼應第 4 節「變更需要走正式流程」的精神,延伸到依賴管理),而不是無法控制的隨上游浮動。
4. **保留未來遷移的彈性**:若 v2(例如納入 [`strategy-hypothesis.md`](./strategy-hypothesis.md) 提到的策略二/三、或永續合約)真的遇到 Freqtrade 架構上無法客製化的限制,屆時再評估是否需要 fork 或部分自建——現在沒有證據顯示會遇到這個限制,提前 fork 是解決一個尚未出現的問題。
5. **部署層面同步強化這條邊界**:採用官方 Docker image(釘選特定 tag),把 `user_data/` 以 volume/bind mount 的方式掛載進去。這讓「框架」與「我們的程式碼」在部署層級也是物理分離的兩個東西,不只是原始碼倉庫層級的約定——與 [`tech-stack-decision.md`](./tech-stack-decision.md) 已定案的「Freqtrade 官方 Docker image,單機/單一 VPS 常駐」完全一致。

---

## 9. 已知限制與交棒事項

- **Protections 的確切設定機制**(策略 property vs. 是否仍有 config 層級的替代寫法)以本文件撰寫時查閱的官方文件為準;實作前(Phase 6/10+)應核對當時的 Freqtrade 版本文件,若機制已變更,第 4.3 節「不可被策略程式碼繞過」的架構性結論預期仍然成立(這是框架設計哲學層面的保證,不只是特定版本的實作細節),但參數宣告的確切位置可能需要調整。
- **`/stop` 指令對已開倉部位的確切管理行為**,不同版本文件描述細節可能有出入,4.4 節的 runbook 設計刻意不依賴這個細節,但仍建議 Phase 7 實作時明確核實並記錄下來,作為 SOP 的一部分。
- **MaxDrawdown Protection 的 `stop_duration` 計時單位**(分鐘 vs. K 棒數)需要在 Phase 4 定案 24 小時冷靜期的技術對應值時,依當時版本文件核實精確單位換算。
- **Binance Spot Testnet 的 ccxt/Freqtrade 端點設定語法**,如 7.4 節所述,留給 Phase 7 或實作階段核實。
- **結構化交易紀錄的確切欄位清單**,本文件依既有知識列出常見欄位(`pair`, `open_date`, `close_date`, `profit_ratio`, `exit_reason` 等),Phase 6 backtest-analyst 實際跑出第一批 Freqtrade 匯出檔案後,應該對照確認欄位是否齊全,若統計方法論需要額外欄位(例如逐筆交易的 ATR 值以驗證 `risk_fraction` 計算是否正確執行),屆時可能需要透過 `custom_exit`/自訂欄位或額外 side-channel logging 補齊——本文件不預先假設會需要,留待 Phase 6 實測後判斷。
- **持續性資料源中斷的存活監控**,本文件在 6.2 節僅指出需求存在並列為 Phase 7 待補項目,未展開具體設計(輪詢頻率、告警管道等),避免在尚未確定需求規模前預先建置監控基礎設施。
- 本文件所有目錄樹狀結構、Freqtrade callback 簽章細節,是規格層級的描述,實作時(Phase 10+)以當時 Freqtrade 官方文件與 `freqtrade new-strategy`/`freqtrade create-userdir` 等鷹架工具產生的實際結構為準,本文件若與工具產生的預設值有出入,以確保「不重新發明輪子」的原則為優先,調整本文件而非強行讓實作偏離框架慣例。

**下一步:** 本文件經專案負責人審閱確認後,Phase 3 結案,可交棒 Phase 4(risk-manager,`risk-policy.md`——定案本文件第 3.2、4.5 節多處留白的具體數字)與 Phase 5(trading-security-reviewer,`security-policy.md`——在本文件第 6、7 節的結構基礎上,定案程序性的環境二次確認與金鑰管理細節)。

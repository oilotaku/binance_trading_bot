# Phase 7 — 執行層技術規格 / Execution Layer Specification

> 狀態:**🟡 草稿完成,待專案負責人審閱後確認 / DRAFTED — pending owner review.**
> 負責角色:execution-engineer
> 範圍限制(承 [`tech-stack-decision.md`](./tech-stack-decision.md) 與 [`architecture-spec.md`](./architecture-spec.md) 第 1 節模組表的明確定義):本文件**不**重新設計冪等下單、WebSocket 斷線重連、訂單狀態機這些邏輯——[`architecture-spec.md`](./architecture-spec.md) 第 1 節模組表已明訂執行層「不重新設計;Phase 7(`execution-spec.md`)只處理連線相關參數,不重新設計冪等下單/重連邏輯」。本文件的工作是三件事:(a) **確認** Freqtrade/ccxt 實際提供的保證是什麼、附上查證依據,不是重述 `tech-stack-decision.md`/`architecture-spec.md` 已經斷言過的結論;(b) **定義**讓這些保證在本專案實際生效所需的具體設定值;(c) **解決**其他 Phase 文件明確交棒給 Phase 7 的待查證項目([`architecture-spec.md`](./architecture-spec.md) 7.4/9 節、[`security-policy.md`](./security-policy.md) 5.3/7 節)。
> 前提假設(承 [`scope.md`](./scope.md)):現貨、BTC/ETH、波段頻率、單人專案。
> 前提假設(承 [`tech-stack-decision.md`](./tech-stack-decision.md)):Freqtrade + ccxt 為執行引擎,dry-run 對應 Testnet/模擬資金,官方 Docker image 部署於單一 VPS。
> 前提假設(承 [`architecture-spec.md`](./architecture-spec.md)):第 1 節已將執行層工作範圍限定如上;第 3.3 節已建議 `stoploss_on_exchange: true`,本文件需給出具體 `order_types` 設定;第 6.1 節已描述 Freqtrade `Trade`/`Order` 模型的對帳邏輯(每主迴圈迭代持續執行,非僅重啟時觸發),本文件不重複推導,只在第 4 節指出這個既有機制的邊界;第 6.2 節已把「持續性資料源中斷的存活監控」列為 Phase 7 待補項目;第 7.4 節把 Binance Spot Testnet 端點覆寫留給本文件核實。
> 前提假設(承 [`risk-policy.md`](./risk-policy.md)):第 0 節已定案 timeframe = 1d;3.4 節已指出每日虧損熔斷的自訂邏輯需要 balance re-query,且不受框架層級保證涵蓋。
> 前提假設(承 [`security-policy.md`](./security-policy.md)):2.1 節已定案 `secrets-*.json` 為 Freqtrade 讀取機密的權威來源、`deploy/*.env` 為 Docker 注入手段,但 7 節明確標記兩者覆寫優先序留給本文件核實;5.3 節已定下政策約束——任何自訂重試必須有界、fail closed、且不可疊加在 ccxt/Freqtrade 已處理的重試之上,本文件第 9 節需在此約束下給出具體參數。

本文件對應 [`development-plan.md`](./development-plan.md) Phase 7 要求清單,但如 [`tech-stack-decision.md`](./tech-stack-decision.md) 已明確指出:該清單原始描述(client order ID 產生規則、退避演算法、斷線重連步驟、對帳觸發時機)假設的是「從零設計一套執行引擎」,這個假設已被 Phase 8 的決策取代。本文件把清單的**意圖**(冪等性、rate limit 韌性、斷線後的一致性、對帳、錯誤處理)保留下來,但**作法**改為「查證 Freqtrade/ccxt 已經如何做到 + 我們需要配置什麼」。

---

## 0. 文件定位與方法論

### 0.1 這不是要重新設計執行引擎

呼應 [`architecture-spec.md`](./architecture-spec.md) 0.1 節的既有立場:本文件不畫新的訂單狀態機圖、不設計新的重連演算法。凡是 Freqtrade/ccxt 已經處理好的部分,本文件的動作是「查證它確實如此、引用依據」,不是「假設它如此、照抄一句話帶過」——這是本文件與 `architecture-spec.md`/`tech-stack-decision.md` 已有斷言之間最主要的差異:那兩份文件在提前拍板技術選型時,對 Freqtrade 的能力做了**方向性**的斷言(「ccxt 已經處理 rate limit/WebSocket/REST 抽象化」);本文件的職責是把這些方向性斷言**逐項核實到可以寫進實作規格書的精確度**,並誠實標出核實不到的地方。

### 0.2 查證方法與資料來源

本文件撰寫時(2026-08-07)採用兩層查證,層級不同、可信度不同,以下逐節會明確標示每個結論屬於哪一層:

**第一層:官方文件(權威性較高,但可能與實際版本行為有落差)**——實際 WebFetch 查閱了以下 Freqtrade 官方文件(`develop` 分支):
- [`exchanges.md`](https://github.com/freqtrade/freqtrade/blob/develop/docs/exchanges.md)——交易所特定設定、`ccxt_config`/`ccxt_async_config` 結構
- [`configuration.md`](https://github.com/freqtrade/freqtrade/blob/develop/docs/configuration.md)——`order_types`、環境變數覆寫機制、`internals.process_throttle_secs`、多檔設定合併規則
- [`rest-api.md`](https://github.com/freqtrade/freqtrade/blob/develop/docs/rest-api.md)——REST API 端點、認證機制、預設綁定

**第二層:Freqtrade 原始碼(精確度最高,但不是公開文件承諾的穩定契約,只是特定 commit 觀察到的實作細節)**——本文件在官方文件無法完全回答第 1、2、3 節的具體機制問題時(例如「client order ID 究竟是怎麼運作的」這類文件沒有明講的實作細節),直接讀取了 `develop` 分支以下原始碼檔案以取得可驗證的答案,而非憑既有知識猜測:`freqtrade/exchange/exchange.py`(`create_order`/`create_stoploss`/`_get_stop_order_type` 等下單邏輯)、`freqtrade/exchange/common.py`(`@retrier`/`@retrier_async` 重試裝飾器)、`freqtrade/exchange/binance.py`(Binance 專屬的 `_ft_has` 設定,包含 `stoploss_order_types`)、`freqtrade/freqtradebot.py`(`execute_entry`/`enter_positions`/`handle_similar_open_order`/`manage_open_orders`)。**這一層查證的結果比官方文件更精確,但穩定性保證更低**——原始碼行為隨版本改動的機率高於文件承諾的公開介面,本文件每一處引用原始碼觀察到的行為,都會明確標記為「依 `develop` 分支 2026-08-07 觀察到的實作細節,非官方文件明文保證的公開契約,實作前應對照當時實際 pin 住的 Freqtrade 版本原始碼重新核實」,不會包裝成與官方文件同等級的確定性。

**查證範圍之外、無法完全確認的部分**:會在各節與第 10 節明確列出,不假裝已核實。

### 0.3 快速參考:本文件的設定值總覽(供實作階段直接查閱)

| 類別 | 設定 | 值/結論 | 對應章節 |
|---|---|---|---|
| Client order ID / 冪等性 | Freqtrade 是否對下單呼叫設定 clientOrderId 供交易所去重 | **否**(原始碼查證,見第 1 節)——真正的機制是「下單呼叫本身不被框架自動重試」,不是「重試但去重」 | 第 1 節 |
| 下單重試 | `create_order`/`create_stoploss` 是否套用 Freqtrade 自己的 `@retrier` | **否**(`create_order` 完全無裝飾器;`create_stoploss` 明確 `@retrier(retries=0)`) | 第 1 節 |
| Rate limit | `exchange.ccxt_config.enableRateLimit` | 沿用預設 `true`,不客製化 | 第 2 節 |
| Rate limit | `internals.process_throttle_secs` | 沿用預設 5 秒,不因低頻策略而更動 | 第 2 節 |
| WebSocket(市場資料) | `exchange.enable_ws` | **建議明確設為 `false`**(1d 策略用不到 OHLCV 低延遲優化,且此設定與訂單/對帳無關) | 第 3 節 |
| 訂單追蹤 | 是否經由 WebSocket | 否,一律 REST 輪詢(原始碼查證) | 第 3 節 |
| 對帳補強 | 定期原始餘額比對 | 建議每日一次獨立於 Freqtrade 主邏輯的 `/balance` vs `/status` 交叉核對 | 第 4 節 |
| `order_types.entry` / `.exit` | `"limit"` | 第 5 節 |
| `order_types.stoploss` | `"limit"`(Binance Spot 僅支援 `stop_loss_limit`,原始碼查證) | 第 5 節 |
| `order_types.stoploss_on_exchange` | `true` | 第 5 節 |
| `order_types.stoploss_on_exchange_interval` | `60`(秒,沿用預設) | 第 5 節 |
| `order_types.stoploss_on_exchange_limit_ratio` | `0.99`(沿用預設,本文件不擅自調整) | 第 5 節 |
| `order_types.emergency_exit` | `"market"` | 第 5 節 |
| Testnet 端點 | `exchange.ccxt_config.sandbox: true` | 官方文件記載機制,但**已知對 Binance 曾因端點變更而失效**,列為實作時必須實測驗證項目,不保證單獨這樣設定就會生效 | 第 6 節 |
| 環境變數覆寫優先序 | `FREQTRADE__*` vs `--config` 疊加鏈 | **環境變數優先序高於設定檔**(官方文件明文) | 第 7 節 |
| 每日虧損熔斷 balance re-query 重試 | 上限次數/總耗時 | 3 次重試(共至多 4 次嘗試)/ 總耗時上限 10 秒 / fail closed | 第 9 節 |

---

## 1. 冪等下單確認 / Idempotent Order Confirmation

### 1.1 任務性質:這是查證題,不是設計題

[`architecture-spec.md`](./architecture-spec.md) 第 1 節已斷言執行層「不重新設計冪等下單」邏輯,言下之意是 Freqtrade 已經處理好。[`security-policy.md`](./security-policy.md) 5.3 節則進一步明確要求本文件「確認冪等性(`client_order_id` 設計)是否真的對齊」。本節的任務因此是**驗證**這個既有假設,而不是提出新設計——但驗證的結果,比「Freqtrade 已經處理好」這句話所暗示的更細緻,必須誠實呈現。

### 1.2 查證發現一:Freqtrade 下單時並未主動設定 client order ID

依原始碼查證(`freqtrade/exchange/exchange.py` 的 `create_order()`、`freqtrade/exchange/binance.py`):Freqtrade 呼叫 ccxt 的 `self._api.create_order(pair, ordertype, side, amount, rate_for_order, params)` 時,`params` 字典由 `_get_params()` 組成,內容僅包含 `timeInForce`、`reduceOnly` 等欄位——**沒有主動設定 `clientOrderId`/`newClientOrderId`**。`binance.py` 也沒有覆寫 `create_order`/`_get_params` 去補上這個欄位。這代表:**若真的送出兩次同樣的下單呼叫,Binance 會各自配發不同的 auto-generated `clientOrderId`,兩筆都會被接受為獨立訂單**——client order ID 在這裡不是 Freqtrade 主動設計的去重鍵。

這與 [`architecture-spec.md`](./architecture-spec.md)/[`tech-stack-decision.md`](./tech-stack-decision.md) 原本方向性斷言的畫面(隱含「Freqtrade 用 client order ID 做冪等」)有落差,本文件在此誠實訂正:**Freqtrade 達成「重試不會重複下單」這個結果的機制,不是「用固定 client order ID 讓交易所擋掉重複請求」,而是下一節說明的「下單呼叫本身根本不會被自動重試」。**

### 1.3 查證發現二:真正的機制是「下單呼叫沒有自動重試」,而非「重試但去重」

依原始碼查證(`freqtrade/exchange/common.py` 的 `@retrier` 裝飾器 + `freqtrade/exchange/exchange.py`):

- `create_order()` 函式**完全沒有套用 `@retrier` 裝飾器**——若 ccxt 呼叫失敗拋出例外(`ccxt.InsufficientFunds`、`ccxt.InvalidOrder`、`ccxt.DDoSProtection`、`ccxt.OperationFailed`/`ccxt.ExchangeError` 等),`create_order()` 會把它們分別包成 Freqtrade 自己的例外型別(`InsufficientFundsError`/`InvalidOrderException`/`DDosProtection`/`TemporaryError`/`OperationalException`)直接往上拋,**不會在這個函式內部自動重新呼叫一次下單**。
- `create_stoploss()`(交易所端停損單建立,對應第 5 節 `stoploss_on_exchange`)明確標註 `@retrier(retries=0)`——注意這不是「沒有裝飾器」,是**明確**把可設定的重試次數鎖定為 0。這代表 Freqtrade 開發者刻意選擇讓這個裝飾器語法上存在(保留未來若需要調整重試次數的彈性),但目前的值就是「不重試」,不是遺漏。
- 相對地,`@retrier`(預設 `retries=API_RETRY_COUNT=4`)確實用在其他呼叫上,例如 `cancel_order`、`fetch_order` 相關的查詢/取消類操作——這些是**冪等的讀取/取消動作**(重複呼叫 `cancel_order` 兩次,第二次頂多得到「訂單已不存在」的錯誤,不會產生任何副作用),與「建立新訂單」這種**非冪等的寫入動作**性質不同。

**結論(這是本文件對 [`security-policy.md`](./security-policy.md) 5.3 節查證要求的直接回答)**:Freqtrade 防止「自己的重試邏輯造成重複下單」的方式,是**不讓下單呼叫進入任何重試迴圈**,而不是「允許重試、但靠 client order ID 讓交易所端去重」。這個結論比原先文件假設的機制更保守、也更簡單——保守到「下單呼叫失敗就是失敗,例外直接往上拋、這次訊號被跳過」,沒有任何一層會嘗試「重新送一次剛才失敗的那個下單請求」。這與 [`security-policy.md`](./security-policy.md) 5.3 節「不要在 ccxt 已處理的重試上再包一層」的精神完全一致,只是精確機制是「該重試的地方本來就沒有重試」,而非「有重試但已經去重」。

### 1.4 查證發現三:訊號層級的重複下單防護——`enter_positions` 與 `handle_similar_open_order`

除了「下單呼叫本身不重試」之外,原始碼(`freqtrade/freqtradebot.py`)還顯示兩層額外的防護,值得記錄:

- **`enter_positions()`**:在建立新單前,會把任何已經有 `is_open=True` 之 `Trade` 記錄的交易對從候選白名單移除——不會對同一個交易對同時開兩筆獨立新倉。這個保護依賴本地 `Trade` 記錄**已經存在**才能生效(見 1.5 節的殘留缺口)。
- **`handle_similar_open_order()`**(position-adjustment 情境):若某筆 `Trade` 已經有一張掛單,且新要送出的訂單與既有掛單「同金額、同方向」,直接視為已存在、**不重複送單**;若參數不同,先取消既有掛單再補新單,而非兩張並存。
- **`unfilledtimeout` 機制**(`configuration.md` 查證):逾時未成交的掛單會被取消、並在訊號仍然有效時**以當下新價格重新送出**——這是一個**設計內的合法重送**(cancel-then-replace),不是意外重複下單,實作與 code review 時應與「未預期的重複訂單」(第 8 節錯誤分類的一環)明確區分,不要誤把這個正常機制當成 bug。

### 1.5 誠實揭露:仍然存在、無法用「Freqtrade 已處理好」一句話打發的殘留缺口

**模糊結果窗口(ambiguous outcome window)**:若 `create_order()` 對 ccxt 的 HTTP 呼叫已經送達 Binance 並實際執行,但 Freqtrade 在等待回應時發生逾時或連線中斷,导致這次呼叫在 Freqtrade 這一側被視為「失敗」而拋出例外——此時**尚未建立任何 `Order`/`Trade` 資料庫記錄**,1.4 節的兩層訊號級防護(`enter_positions` 排除已有 `Trade` 的 pair、`handle_similar_open_order`)都依賴本地已有記錄才能運作,**對這個情境完全沒有涵蓋**;[`architecture-spec.md`](./architecture-spec.md) 6.1 節描述的常態對帳邏輯(針對 `is_open=True` 的既有交易查詢對應訂單狀態)同樣要有一筆本地記錄才有東西可查,**同樣不在涵蓋範圍內**——這是本文件在第 4 節會回頭處理的具體缺口案例之一。

**這個殘留風險在本專案情境下的緩解因子(緩解,不是消除)**:

1. `timeframe=1d` + `process_only_new_candles: true`([`architecture-spec.md`](./architecture-spec.md) 3.1 節既有設定):這個模糊窗口若真的發生,下一次策略評估同一個訊號至少要等到下一根日 K,而非幾秒後立即重試——屆時 Donchian 突破條件很可能已不再成立,即使不成立而策略仍判斷要進場,那也會被記錄為一次獨立的新訊號評估,而非對同一個突破事件的重複下單。
2. 這個窗口本質上是「网络请求已送出、回應遺失」的經典分散式系統問題,不是 Freqtrade 架構設計上的疏漏——任何交易系统(不論自建或用框架)都存在這個理論窗口,差別只在於**多久會被人為發現、發現後的補救成本**,這正是第 4 節建議補強的定期原始餘額對帳要負責的部分。

**明確聲明**:本節的結論**不能**寫成「Freqtrade 已透過 client order ID 完整保證冪等性」——查證結果顯示這句話不準確。準確的表述是:「Freqtrade 透過『下單呼叫本身不自動重試』+『訊號層級的既有持倉檢查』,把最常見的重複下單成因(自己的重試機制)排除;殘留的『網路層級模糊結果』窗口沒有被消除,只能靠低頻策略的時間尺度緩解 + 第 4 節的定期對帳事後發現。」

### 1.6 對實作階段的具體要求

1. 不需要、也不應該自行對 `create_order`/`create_stoploss` 外層再包一層重試邏輯——1.3 節已確認框架本身刻意選擇不重試,策略 callback(例如 `confirm_trade_entry`)或部署腳本若因為「下單失敗好像很可惜」而手動 catch 例外後重新呼叫一次進場邏輯,等同重新引入 Freqtrade 刻意避免的重複下單風險,**Phase 10 code review 應把這一點列為明確檢查項**。
2. 若未來 Freqtrade 版本新增了主動設定 `clientOrderId` 的能力或設定項,應在該版本升級時重新核對本節結論是否仍然成立,而非假設本文件的結論會永遠有效。
3. 交棒 Phase 10:實作 `confirm_trade_entry`/自訂邏輯時,不應假設「這次呼叫失敗,代表交易所端一定沒有這筆單」——第 8 節的錯誤處理分類與第 4 節的對帳補強,都是為了不讓這個假設在無人發現的情況下悄悄成立。

---

## 2. Rate Limit 處理 / Rate Limit Handling

[`tech-stack-decision.md`](./tech-stack-decision.md) 已斷言 ccxt(Freqtrade 內建)已處理交易所層級的 rate limit。依 `exchanges.md` 查證,機制是:

- `exchange.ccxt_config.enableRateLimit`(預設 `true`)——ccxt 內建的**節流**(在每次呼叫之間依交易所公告的限制主動間隔請求,不是「打到限制後才反應」的被動重試)。
- `exchange.ccxt_async_config.rateLimit`(毫秒)——若需要針對特定交易所調整節流間隔的可選設定,官方範例顯示可設定如 `3100`(毫秒)這類數值,但文件本身也明講「最佳設定值依交易所與 pairlist 大小而異」,沒有給出 Binance 的建議值。
- 依 1.3 節原始碼查證,Freqtrade 自己的 `@retrier`/`@retrier_async` 裝飾器(`freqtrade/exchange/common.py`)會捕捉 `ccxt.DDoSProtection`(對應 HTTP 418/429)並套用退避:`calculate_backoff(retrycount, max_retries) = (max_retries - retrycount) ** 2 + 1` 秒,預設 `API_RETRY_COUNT=4` 次重試(對讀取類呼叫,例如 `fetch_order`/`fetch_balance`;不含第 1 節已確認不重試的下單類呼叫)。這個退避**沒有內建 jitter**——與第 9 節本文件自己要設計的重試邏輯(需要 jitter)是不同層級的機制,不要混為一談。

**本專案的具體設定建議(維持精簡,理由如下)**:

- `exchange.ccxt_config.enableRateLimit`:沿用預設 `true`,不關閉。
- `internals.process_throttle_secs`:沿用預設 5 秒。這個值控制的是 Freqtrade **主迴圈**的節流(多久跑一次迭代),不是交易所呼叫的節流,兩者是不同層級的設定,不應該因為策略是 1d timeframe 就誤以為要把它拉長到接近一天——拉長主迴圈間隔會拖慢 Telegram/REST 控制指令(`/forceexit`、`/stop`)的反應速度與 `manage_open_orders` 對未成交掛單的處理頻率([`architecture-spec.md`](./architecture-spec.md) 4.4 節的緊急停止 runbook 依賴主迴圈仍在正常頻率運作),得不償失。
- `exchange.ccxt_async_config.rateLimit`:**不客製化,沿用 ccxt 針對 Binance 的內建預設值**。理由:v1 僅 BTC/ETH 兩個交易對、`timeframe=1d`([`risk-policy.md`](./risk-policy.md) 第 0 節)、策略訊號本就稀疏([`risk-policy.md`](./risk-policy.md) 6.2 節引用「一個乾淨趨勢市場一年可能才個位數到十幾次有效突破訊號」)——這個組合對 Binance API 呼叫量的壓力遠低於 rate limit 門檻,沒有需要客製化調整的實際壓力來源。若未來 v2 納入更高頻策略或更多交易對([`strategy-hypothesis.md`](./strategy-hypothesis.md) 已列為 backlog 的策略二/三),應重新評估此節,而非現在預先調參一個目前用不到的旋鈕。

本節依任務要求維持簡短,理由正是上述最後一點:1d timeframe 對 rate limit 而言本質上是低壓力情境,沒有值得展開的複雜度。

---

## 3. WebSocket 斷線重連與重同步 / WebSocket Reconnection & Resync

### 3.1 先回答一個前提問題:本專案的訂單/帳戶追蹤有沒有用到 WebSocket?

依原始碼查證(`freqtrade/exchange/exchange.py` 的 `create_order`/`cancel_order`/`fetch_order` 等呼叫路徑),**下單、查詢訂單狀態、查詢餘額,一律是同步 REST 呼叫**,沒有任何 `ccxt.pro`/`watch_*` 呼叫涉入其中。這代表 [`development-plan.md`](./development-plan.md) 原始 Phase 7 清單要求的「WebSocket 斷線重連與狀態重同步的具體步驟」,對 Freqtrade 目前架構而言,答案是:**訂單/帳戶狀態的重同步,從來就不依賴一條會斷線的長連線是否重連——它是每次主迴圈的 REST 輪詢自然達成的**,沒有 WebSocket 連線存在,自然也沒有「斷線重連」這個步驟需要設計。[`architecture-spec.md`](./architecture-spec.md) 6.1 節已完整描述這個 REST 輪詢對帳的行為(每主迴圈迭代持續執行,不只在重啟時),本文件在此不重複推導,只補上這個「WebSocket 在這裡根本不參與」的釐清。

### 3.2 Freqtrade 確實有 WebSocket 使用場景,但範圍僅限市場資料、與訂單追蹤無關

依 WebFetch/搜尋查證(`exchanges.md` 未直接提及,額外查證到 Freqtrade 透過 `ccxt.pro` 提供的 OHLCV 資料串流功能):`exchange.enable_ws`(預設 `true`)控制是否用 WebSocket 訂閱**K 棒(OHLCV)資料**;目前僅少數交易所測試過(含 Binance Spot),失敗或停用時**自動 fallback 回 REST**。這個機制與訂單/帳戶追蹤是兩件不相干的事——它只影響「Freqtrade 多快拿到最新一根還在走的 K 棒」,不影響下單、對帳、或任何本文件其他章節討論的保證。

另外,[`architecture-spec.md`](./architecture-spec.md) 5.2 節已提及的 REST API server 自帶的 `/api/v1/message/ws` 端點(依 `rest-api.md` 查證,需 `ws_token` 查詢參數),是給外部監控/FreqUI 訂閱**機器人事件**用的單向推播,同樣與交易所連線無關,不在本節討論範圍內。

### 3.3 本專案的建議:明確關閉市場資料 WebSocket

**建議 `exchange.enable_ws: false`。** 理由:

1. `timeframe=1d` 代表新的一根候選 K 棒一天才收一次,WebSocket 相對 REST 輪詢帶來的延遲優化(通常是為了日內/高頻策略搶那幾百毫秒到幾秒的資訊優勢)對本策略沒有實質效益——[`scope.md`](./scope.md) 已明確排除日內高頻定位,這個優化解決的正是本專案刻意不做的那個問題。
2. 少一條長連線(額外執行緒、額外連線狀態、額外的「連線是否還活著」這個新故障模式)換不到對應的效益,不是穩健的取捨——這與 [`architecture-spec.md`](./architecture-spec.md) 4.5 節「不建置目前不需要的基礎設施」的既有原則一致,是同一種判斷邏輯的延伸應用。
3. 明確關閉(而非讓它保持預設 `true` 但實際上用不到)的價值在於:未來若有人在 log 或連線狀態監控中看到一條 WebSocket 連線,會直接知道那是刻意啟用的(例如未來若真的需要日內功能才手動開啟),而不是一個「不確定為什麼存在、不確定能不能關」的模糊狀態。

### 3.4 若未來版本演進,WebSocket 用途擴大到訂單追蹤怎麼辦

本節的結論建立在 2026-08-07 查證到的 Freqtrade 架構之上——若未來版本把 WebSocket 用途從「僅市場資料」擴大到「訂單/帳戶事件推播」(這在其他框架與交易所整合中並不罕見),3.1 節「訂單狀態靠 REST 輪詢達成重同步」這個結論需要重新核實,不能假設本文件的結論會一直成立。這屬於實作時應對照當時 Freqtrade 版本 changelog 重新確認的項目,列入第 10 節。

---

## 4. 對帳 / Reconciliation

### 4.1 [`architecture-spec.md`](./architecture-spec.md) 6.1 節已涵蓋的部分(不重複推導)

SQLite 交易資料庫是本地帳本的唯一真實來源;每個主迴圈迭代都會對有未結訂單的持倉查詢交易所實際訂單狀態並同步回本地——這是 Freqtrade 架構既有保證,已由 `architecture-spec.md` 完整描述並附上依據,本文件承接此結論,不重新展開。

### 4.2 本文件新增的部分一:框架自動對帳「不足夠」的具體情境

[`architecture-spec.md`](./architecture-spec.md) 6.2 節已標記一個既有缺口(帳外人工手動操作,不在 Freqtrade 對帳邏輯涵蓋範圍)。本文件在此補上第二個具體情境,是第 1.5 節查證發現的直接延伸:

**框架對帳邏輯的運作前提是「本地已有一筆 `Order`/`Trade` 記錄可以拿去跟交易所核對」。若第 1.5 節描述的模糊結果窗口發生(下單呼叫在 Freqtrade 這一側被判定失敗、因此從未寫入任何本地記錄,但該筆訂單實際上已經在交易所端建立/成交),常態對帳邏輯完全不知道要去查什麼,因為它沒有任何本地記錄可以作為查詢的起點。** 這不是 Freqtrade 對帳邏輯設計不良——任何「以本地帳本為起點去核對交易所」的對帳設計,本質上都無法涵蓋「本地帳本裡完全不存在的東西」,這是這種對帳方法論的**結構性**盲區,不是可以靠改進 Freqtrade 對帳頻率或演算法解決的問題。

### 4.3 本文件新增的部分二:建議的補強機制——定期原始餘額比對

**設計:獨立於 Freqtrade `Trade`/`Order` 模型的一次性原始查詢,直接比對「Freqtrade 回報的持倉」與「交易所帳戶實際餘額」,不透過 Freqtrade 自己的對帳邏輯。**

- **觸發頻率**:建議**每日一次**,對齊 `timeframe=1d` 的自然節奏(不需要比策略本身的判斷頻率更密集,策略一天才產生一次新訊號,更頻繁的比對不會提早發現任何額外問題,只會增加不必要的 API 呼叫)。
- **具體比對方式**:呼叫 Freqtrade REST API 的 `/status`(依 `rest-api.md` 查證,列出所有 open trades)與 `/balance`(帳戶餘額),與**直接對交易所發出的原始餘額查詢**(即繞過 Freqtrade、用 ccxt 或 Binance API 直接查 `GET /api/v3/account` 等等)進行交叉比對——核心邏輯:「Freqtrade 認為自己持有多少 BTC/ETH」是否約等於「帳戶上實際可歸因於這些持倉的資產數量」(需扣除可能存在的手續費幣種餘額如 BNB、非本策略交易的餘額,這是為什麼比對邏輯不能只是簡單的等式,細節留給實作階段依實際帳戶結構調整)。
- **不一致時的行為(fail closed,呼應第 9 節與 [`security-policy.md`](./security-policy.md) 5.3 節的一致原則)**:偵測到不一致時,**優先觸發告警並暫停新倉**,不自動嘗試「修復」本地帳本(例如自動幫 Freqtrade 建立一筆補登的 `Trade` 記錄)——自動修復本身可能基於錯誤的假設(例如誤判哪一筆才是「多出來」的持倉),比維持現狀、等待人工判斷更危險。這與 [`architecture-spec.md`](./architecture-spec.md) 6.2 節「此帳戶僅供本機器人使用」的既有前提假設一致:一旦這個前提被違反(不論是人工手動操作,還是第 1.5 節的模糊結果窗口),正確反應是**發現並停下來**,不是自動接管、猜測正確狀態。
- **接線建議**:可以是獨立的 systemd timer/cron job(部署層,與 [`architecture-spec.md`](./architecture-spec.md) 5.2 節「日誌輪替交由部署層處理」同樣的分工邏輯,不在 Freqtrade 應用層內建置),呼叫上述兩個 REST 端點與一次獨立的餘額查詢腳本;具體實作(語言、確切比對容差)留給 Phase 10,本文件只定義行為規格。

### 4.4 與 [`architecture-spec.md`](./architecture-spec.md) 6.2 節「存活監控」待補項目的關係

`architecture-spec.md` 6.2 節已把「持續性資料源中斷的存活監控」列為 Phase 7 待補項目,但特意不展開設計(避免預先建置範圍未定的監控基礎設施)。本節的每日餘額比對機制**附帶**提供部分這類存活訊號的價值(若比對腳本本身連續多日無法完成——不論是因為 Freqtrade REST API 無回應、還是交易所 API 本身不可達——這本身就是一個「機器人可能實質失能」的訊號),但**不等於**完整回答該待補項目——本文件的立場與 `architecture-spec.md` 一致:不在此預先設計一整套存活監控系統,只指出本節設計的比對機制可以順帶覆蓋其中一部分,完整的存活監控需求若未來明確化,應另外評估。

---

## 5. `stoploss_on_exchange` 具體配置 / Concrete Configuration

### 5.1 回顧既有前提

[`architecture-spec.md`](./architecture-spec.md) 3.3 節已建議啟用 `stoploss_on_exchange: true`,理由是讓 ATR 停損的保護不依賴 Freqtrade 進程是否存活([`architecture-spec.md`](./architecture-spec.md) 6.2 節「崩潰到重啟之間的保護空窗期」)。[`risk-policy.md`](./risk-policy.md) 對這個機制的依賴體現在:ATR 停損倍數 `k` 的搜尋邊界(2.0–4.0)、`custom_stoploss` fallback 值(-25%)都假設有 `stoploss_on_exchange` 作為進程外的第二層保護。本節任務是給出**具體** `order_types` 設定,把這個建議落地成可以直接寫進 `config-common.json` 的值。

### 5.2 具體 `order_types` 設定建議

```
order_types:
  entry:                              "limit"
  exit:                               "limit"
  stoploss:                           "limit"
  stoploss_on_exchange:               true
  stoploss_on_exchange_interval:      60      (秒,沿用 Freqtrade 預設)
  stoploss_on_exchange_limit_ratio:   0.99    (沿用 Freqtrade 預設,理由見 5.3 節)
  emergency_exit:                     "market"
```

**逐項理由:**

- **`entry`/`exit`:`"limit"`**——波段頻率(非搶當下成交速度的日內策略)下,限價單可控滑價的價值高於市價單的立即成交保證;搭配 `unfilledtimeout`(第 1.4 節已提及)確保限價單不會無限期掛著不成交,逾時後在訊號仍有效時以新價重掛,是 Freqtrade 內建、不需要我們自己實作的機制。
- **`stoploss`:`"limit"`**——這不是任意選擇,是**依原始碼查證的硬性事實**:`freqtrade/exchange/binance.py` 的 `_ft_has` 字典明確定義 `"stoploss_order_types": {"limit": "stop_loss_limit"}`——**Binance Spot 在 Freqtrade 目前架構下,`stoploss_on_exchange` 只支援對應到 `"limit"` 這一個鍵**(沒有 `"market"` 對應項)。若誤設為 `"market"`,依 `_get_stop_order_type()` 的既有邏輯(找不到指定鍵時「otherwise pick only one available」),實際仍會落回 `stop_loss_limit`,但明確設為 `"limit"` 比依賴這個隱性 fallback 更清楚、更不容易在未來版本行為變動時產生意外落差。**這個發現直接呼應 [`risk-policy.md`](./risk-policy.md) 5.2 節/[`security-policy.md`](./security-policy.md) 提到的「Binance spot stop-limit order mechanics」——Binance Spot 沒有給 Freqtrade 一個「純市價停損」的交易所端選項可用,停損單本質上就是一張限價單,5.3 節的 tradeoff 討論即建立在這個事實之上。**
- **`stoploss_on_exchange`:`true`**——落實 [`architecture-spec.md`](./architecture-spec.md) 3.3 節的既有建議。
- **`stoploss_on_exchange_interval`:`60`**——這個值控制 Freqtrade 多久去比對/更新交易所上停損掛單的觸發價一次(讓交易所端的單能跟上 `custom_stoploss` 算出的新 ATR 移動停損位置)。沿用 Freqtrade 預設值,理由:本策略是 1d timeframe,ATR 本身逐日變動、不是逐秒變動,60 秒的更新間隔遠快於「需要跟上的變化速度」,沒有調快的實質需求;也不建議調慢,60 秒對应交易所端 API 呼叫量而言仍是低頻(1 天 1440 次,遠低於 rate limit 門檻,對照第 2 節的低壓力結論一致),沒有調慢换取節省呼叫量的必要。
- **`stoploss_on_exchange_limit_ratio`:`0.99`**——沿用 Freqtrade 預設值,**本文件不擅自調整**。這個比例決定停損限價單的限價相對觸發價的偏移量(偏移越大,越不容易因為價格瞬間跳空穿越限價而掛單未成交,但越大也代表允許更多的滑價)。本文件在 5.3 節說明這是一個未來可能需要依實測數據重新檢視的旋鈕,但依 [`risk-policy.md`](./risk-policy.md) 第 7 節的參數變更流程,任何調整都需要具體依據(例如 Phase 6 回測或 paper trading 觀察到的實際觸發-未成交比例)並走正式變更流程記錄理由,本文件現階段不預先猜測一個「更好」的數字。
- **`emergency_exit`:`"market"`**——依 `configuration.md` 查證,這是「交易所端停損單建立失敗時」的後備出場路徑。此時已經沒有「掛一張精確限價停損單」的選項可用(建立本身就失敗了),用市價確保部位真的離場,優先於再嘗試一次可能同樣失敗的限價單。

### 5.3 Tradeoff 討論:交易所端 stop-limit 機制 vs. bot 自身輪詢 `custom_stoploss`

這是 [`risk-policy.md`](./risk-policy.md)/[`security-policy.md`](./security-policy.md) 都指名要求本文件正面處理的已知取捨,不能只寫「啟用就好」帶過:

**交易所端 `stop_loss_limit` 的滑價風險是真實存在、無法消除,只能調參權衡的**:依 5.2 節查證,Binance Spot 的交易所端停損機制本質上是「觸發價(`stopPrice`)到達後,轉為一張限價單(`limit_price = stop_price × stoploss_on_exchange_limit_ratio`)掛到市場上」——這代表若價格瞬間跳空穿越限價(例如急殺行情中出現價格缺口),這張限價單可能完全不成交或只部分成交,部位因此暴露在比預期更差的價位、甚至完全沒有離場。`stoploss_on_exchange_limit_ratio` 就是在「限價設得離觸發價越遠,越不容易發生未成交」與「限價設得離觸發價越近,萬一真的成交,滑價損失越小」之間的取捨旋鈕,沒有能同時消除兩種風險的設定值。

**bot 自身輪詢 `custom_stoploss` 完全不受這個限價滑價 tradeoff 影響**——因為那條路徑一旦判斷該出場,走的是 Freqtrade 自己下的 `exit` 訂單(依 5.2 節設為 `"limit"`;若進一步考慮,`custom_stoploss` 觸發的出場理論上也可能希望用市價以確保成交,但這已經是 `order_types.exit` 的職責範圍,不是 `stoploss_on_exchange` 的職責範圍,兩者不應混淆)——但這條路徑的前提是 **Freqtrade 進程必須存活、主迴圈必須正常運作、`custom_stoploss` 的計算邏輯必須正確產出結果**。這正是 [`architecture-spec.md`](./architecture-spec.md) 3.3/6.2 節已論證的「進程崩潰時完全沒有保護」風險——bot 自身輪詢的優勢(不受交易所端限價滑價影響)剛好對應到它的弱點(進程不在時完全失效)。

**結論(承接 `architecture-spec.md` 既有決定,本節只是把它具體化):兩條路徑並存,不是二選一。** `stoploss_on_exchange: true` 建立的交易所端限價停損單,角色是「進程不在時的保險」——它有滑價風險,但「有一張掛著、可能有滑價的保護單」永遠優於「進程掛了、完全沒有任何保護」;`custom_stoploss` 的進程內輪詢,角色是「進程活著時更精準的主要出場邏輯」——它能算出比交易所端訂單物理上能表達的更複雜的移動停損距離(交易所端的停損單本質上只是一個固定觸發價,每 `stoploss_on_exchange_interval` 秒由 Freqtrade 主動去更新,不是自己動態計算),進程活著時應該以它為主,交易所端的單只是持續被更新去追蹤這個主要邏輯算出的最新位置。

---

## 6. Testnet 端點設定 / Testnet Endpoint Configuration

> 本節解決 [`architecture-spec.md`](./architecture-spec.md) 7.4 節明確交棒的待查證項目:「Binance Spot Testnet 在 ccxt/Freqtrade 設定中的端點覆寫...留給 Phase 7 或實作階段依當時版本核實」。

### 6.1 官方文件記載的機制

依查證(ccxt/Freqtrade 官方文件慣例):在 `exchange.ccxt_config` 內加入 `"sandbox": true`,ccxt 會嘗試將 API 呼叫導向該交易所在 ccxt 內部登記的 `test`/sandbox URL(若該交易所有註冊)。這是**文件層級記載的標準機制**,不是本文件發明的寫法。

### 6.2 已知的實際脆弱點(明確標記為實作時必須驗證,不是理論疑慮)

透過額外查證找到具體、可佐證的案例(非本文件臆測):**Binance Spot Testnet 的實際端點曾經變更過**(相關 ccxt/Freqtrade issue 討論指出,舊端點 `testnet.binance.vision` 在某個時間點之後對已簽名請求不再有效,新端點與此不同)。這代表 ccxt 內建、寫死在 ccxt 函式庫裡的 `sandbox: true` 對應 URL,**曾經因為 Binance 官方端點變更而在一段時間內失效**,直到 ccxt 該版本更新登記的 URL 為止。這正是 [`architecture-spec.md`](./architecture-spec.md) 7.4 節所擔心的「精確設定鍵值會隨版本演進」的具體實例,不是憑空的謹慎——本文件也實際查證到 Freqtrade 官方文件過去一個專門講 sandbox testing 的頁面,在目前的 `develop` 分支文件樹中已經找不到(回傳 404),這本身也是「這塊設定的說明位置與寫法仍在變動」的一個佐證。

### 6.3 建議做法(依查證所能到的最佳依據,明確保留待驗證項)

**步驟一(優先嘗試,標準寫法)**:`user_data/configs/config-testnet.json` 的 `exchange` 區塊內設定 `"ccxt_config": {"sandbox": true}`。實作時第一步**必須**用當時實際 pin 住的 ccxt/Freqtrade 版本,實測一次 testnet 連線 + 送出一筆最小測試單,確認請求真的打到 testnet 而非不小心打到 mainnet(不能只看設定值「看起來」正確就視為完成)——這與 [`security-policy.md`](./security-policy.md) 1.1 節「用一次實際 API 呼叫驗證比只信任畫面勾選更可靠」是同一種驗證態度的延伸應用,不是本文件新發明的原則。

**步驟二(備援,若步驟一因版本落差而失敗)**:若當時 pin 住的 ccxt 版本尚未跟上 Binance testnet 端點變更(依 6.2 節,這是有實際先例的失效模式),備援做法是在 `ccxt_config` 內直接手動覆寫 API base URL(概念上對應 ccxt 交易所物件的 `urls` 屬性結構,常見形式類似 `ccxt_config.urls.api.public`/`.private` 之類的巢狀鍵),指向當時 Binance 官方公告的實際 Testnet REST 端點。**本文件不假設、不寫死具體鍵名或網址**——不只是因為端點網址本身會隨時間變(這是意料中的),更因為 ccxt 該版本原始碼內 `binance.js`(或對應語言的等價檔案)的 `urls` 巢狀結構本身,才是決定「該用什麼鍵名覆寫」的唯一權威依據,本文件查證時點的結構未必等於實作時點的結構。

**步驟三(不論步驟一或二生效,都要做的驗證)**:啟動後透過一個唯讀端點(例如查帳戶餘額)確認回傳的是 testnet 的模擬餘額特徵(例如 [`scope.md`](./scope.md) 已定案的虛擬起始餘額量級),而非意外連上正式帳戶——這是本文件對 6.3 節「不能只信設定生效」原則的具體落地動作。

### 6.4 結構性防護不依賴這個細節(重申 `architecture-spec.md` 7.4 節既有立場)

不論步驟一或步驟二哪個最終在實作當下生效,本專案 testnet/live 環境分離的**主要防線**從來不是端點覆寫語法本身——[`architecture-spec.md`](./architecture-spec.md) 7.2/7.3 節已確立的「完全不同的設定檔 + 完全不同的金鑰來源 + 完全不同的資料庫路徑」這個結構性設計,以及 [`security-policy.md`](./security-policy.md) 第 4 節的啟動時強制環境確認機制,才是防止「環境搞混」的主要防線。端點覆寫解決的是一個**功能性**問題(「testnet 環境的請求真的有打到 testnet 伺服器嗎」),不是一個**安全性**問題(「這次啟動是不是不小心用錯了環境」)——兩者職責不同,不應該混淆,本節的查證結果不影響、也不應該被拿來替代第 4 節已有的防護設計。

---

## 7. 環境變數覆寫優先序 / Environment Variable Override Precedence

> 本節解決 [`security-policy.md`](./security-policy.md) 明確標記的開放項目:`FREQTRADE__EXCHANGE__KEY`/`FREQTRADE__EXCHANGE__SECRET` 環境變數覆寫,相對於 `--config` 疊加鏈中 `secrets-*.json` 的值,何者覆寫何者——「確切生效優先序...留給 Phase 7/實作階段依當時官方文件核實」,並要求本文件確認兩個機制不會在同一次啟動中互相打架。

### 7.1 查證結果

依 `configuration.md` 官方文件查證(用詞明確,非本文件推論):Freqtrade 設定生效的優先序,由高到低為:

```
CLI 參數  >  環境變數(FREQTRADE__* )  >  設定檔(--config 疊加鏈,依疊加順序,後面的檔案覆寫前面的)  >  策略類別內建設定
```

文件對多檔案合併規則的原文表述是「若同一個鍵出現在超過一份設定中,最後指定的設定生效」("last specified configuration wins"),這條規則適用於 `--config` 疊加鏈**內部**彼此之間的順序;環境變數則是**獨立於這個疊加鏈之上的更高一層**,不論疊加鏈內部順序如何,環境變數一律覆寫疊加合併後的最終結果。

### 7.2 這對 [`security-policy.md`](./security-policy.md) 2.1 節分工設計的具體意義

[`security-policy.md`](./security-policy.md) 2.1 節的既有設計是:`secrets-testnet.json`/`secrets-live.json` 是「Freqtrade 實際讀取的機密來源(唯一權威來源)」,`deploy/testnet.env`/`deploy/live.env`(經 `FREQTRADE__EXCHANGE__KEY`/`SECRET`)是「Docker 部署時秘密進到容器裡的手段」。7.1 節的查證結果代表:

- **`deploy/*.env` 經環境變數注入的金鑰值,優先序高於 `secrets-*.json`**——若兩者同時存在且不一致,**環境變數會贏**,`secrets-*.json` 裡的值會被覆寫、實際不生效。
- 這直接回答 [`security-policy.md`](./security-policy.md) 提出的具體疑慮:「`deploy/live.env` 的變數會不會被一份殘留的舊版 `secrets-testnet.json` 覆寫?」**答案是不會**——覆寫方向與這個疑慮設想的方向相反,環境變數的優先序結構性地高於任何 `--config` 疊加鏈內的 json 檔案,不論該 json 檔案疊加順序落在哪裡。
- **但這也代表真正該擔心的情境,是原本疑慮的鏡像版本**:若啟動 live 環境時,`deploy/testnet.env` 因為某種操作疏失(shell 環境變數殘留未清除、docker-compose 的 `env_file` 清單設定寫錯而同時列出兩個環境的 env file)也一併被載入,**環境變數的高優先序會讓它蓋掉 `secrets-live.json` 裡的值**,實際生效的會是 testnet 的金鑰,而非預期的 live 金鑰——這在 dry_run 已經是 live 模式(`false`)的情況下,是一個危險的組合(用 live 的 `dry_run=false` 搭配 testnet 金鑰,會導致所有交易所呼叫因認證失敗而報錯,是相對可自我發現的失效模式;但若順序反過來——testnet 環境意外載入了 live 金鑰——則是更危險的方向,可能導致 testnet 環境下發生真實資金的交易所呼叫)。

### 7.3 具體建議:結構性隔離,而非只依賴「不要犯這種疏失」的自制力

**兩個機制不會互相打架**(7.1/7.2 節已確認優先序明確、單向,沒有循環覆寫或不確定的合併順序);但「不打架」不等於「不會被錯誤地同時載入」——後者是操作面的風險,需要結構性防護,不能只靠精確理解優先序規則來避免:

1. **啟動 testnet/live 時,只注入對應單一環境的 env file,不應該讓兩個 `.env` 檔案在同一次 `docker run`/`docker-compose` 啟動中同時被載入。** 最安全的做法是 testnet 與 live **各自獨立的 docker-compose service 定義**,每個 service 的 `env_file` 清單只包含該環境自己的檔案——不共用同一個 container/service 定義、靠參數切換,這與 [`architecture-spec.md`](./architecture-spec.md) 7.2 節「兩個環境的啟動指令在檔名層級就完全不重疊」的既有原則完全一致,本節只是把這個原則延伸到 env file 的層級。
2. **[`security-policy.md`](./security-policy.md) 第 4 節的啟動前檢查腳本(pre-flight check)應該把「目前生效的環境變數是否確實對應到預期環境」也納入交叉驗證範圍**,不只檢查 `--config` 疊加鏈與 `dry_run` 是否一致(`security-policy.md` 4.1 節原設計)。具體做法:檢查腳本可以確認目前 process 環境中 `FREQTRADE__EXCHANGE__KEY` 是否存在,並且(**不印出金鑰內容本身**,遵守 [`security-policy.md`](./security-policy.md) 第 3 節脫敏規則)透過呼叫一次唯讀端點(例如帳戶資訊或餘額查詢)確認實際生效的金鑰屬於預期的帳戶類型/餘額量級(呼應 [`security-policy.md`](./security-policy.md) 1.1 節「用一次實際 API 呼叫驗證比只信任畫面勾選更可靠」的同一態度,也呼應第 6.3 節本文件對 testnet 端點驗證提出的同一原則)——**這是本文件對 `security-policy.md` 第 4 節 pre-flight check 腳本的具體補充建議,不是取代它。**

### 7.4 誠實揭露查證的邊界

7.1 節的優先序結論依 `configuration.md` 官方文件明確用詞得出("last specified configuration wins"、環境變數層級高於設定檔案),信心程度較高。但以下細節本文件**未**逐一窮舉驗證,實作階段部署前應額外用一次實測確認:

- 「策略類別內建設定」這個最低優先級層級,與 `secrets-*.json`/`config-*.json` 之間互動的邊界案例(例如策略類別內若意外也定義了 `exchange` 相關設定,是否確實一律敗給設定檔與環境變數)。
- 是否所有巢狀鍵都同等支援環境變數覆寫,或存在任何例外——官方文件的範例聚焦在 `exchange.key`/`exchange.secret`/`stake_amount` 這類常見鍵,未必窮盡所有可能被覆寫的路徑。
- **建議的實測方式**:部署前刻意在 `secrets-testnet.json` 與對應的 `deploy/testnet.env` 中設一組明顯衝突、容易辨識的測試值(例如刻意錯誤的 key 片段,不涉及真實金鑰),啟動後透過 Freqtrade REST API 或啟動 log 確認實際生效的是環境變數版本——用一次實測驗證本節結論在當時實際部署環境中成立,而非只依賴文件描述。

---

## 8. API 錯誤處理 / API Error Handling

依原始碼查證(`freqtrade/exchange/exchange.py` 的 `create_order()` 例外處理分支)與官方文件,整理與本策略(BTC/ETH 現貨、1d、低頻)相關的具體錯誤情境:

| 情境 | ccxt/Binance 例外類型 | Freqtrade 預設處理 | 是否需要客製化告警 |
|---|---|---|---|
| **餘額不足** | `ccxt.InsufficientFunds` | 包成 `InsufficientFundsError` 往上拋;該次進場訊號被跳過,不建立 `Trade` | **需要**——正常運作下,[`risk-policy.md`](./risk-policy.md) 的部位大小公式應該不會算出超過實際可用餘額的下單量;若真的觸發,代表資金分配計算可能有誤,或帳戶餘額被意外動用(呼應 [`architecture-spec.md`](./architecture-spec.md) 4.6 節「此帳戶僅供機器人使用」前提可能已被打破),兩種可能都值得操作者知道 |
| **數量/價格精度錯誤** | `ccxt.InvalidOrder` | 包成 `InvalidOrderException` | **需要**——Freqtrade 送出訂單前已透過 `amount_to_precision`/`price_to_precision` 依交易所市場定義做過捨入,理論上不該常態發生;若仍發生,較可能的原因是市場定義快取過期(交易所異動了精度規則但 Freqtrade 尚未 `reload_markets`),需要告警並由操作者判斷是否要手動觸發市場資訊刷新 |
| **市場閉市/交易對暫停交易** | `ccxt.ExchangeNotAvailable`/`ccxt.OnMaintenance`/market not found 類錯誤 | 依例外類型歸入 `TemporaryError`/`OperationalException`;該次迭代對受影響交易對跳過訊號評估([`architecture-spec.md`](./architecture-spec.md) 6.2 節已述行為) | **需要**——BTC/ETH 是 Binance 上流動性最高的兩個交易對,若出現這類錯誤,通常代表交易所級事故而非個別幣種流動性問題,嚴重程度高於一般的暫時性錯誤,應視同 [`risk-policy.md`](./risk-policy.md) 5.2 節「即時主動推播」要求的等級 |
| **訂單被拒絕/逾時失效(`rejected`/`expired`)** | 訂單狀態欄位(非例外) | 依 `execute_entry()` 既有邏輯:若 `filled == 0`,視為進場失敗、不建立 `Trade`,回傳 `False`;若部分成交,依實際成交量繼續處理為部分持倉 | **需要**——對突破策略而言,一筆進場訊號沒有真正成交,代表可能錯過這次趨勢起漲的進場時機,即使不是系統錯誤,也是操作者應該知道的事件 |
| **Rate limit / DDoS protection(HTTP 418/429)** | `ccxt.DDoSProtection` | 依第 2 節,Freqtrade 自己的 `@retrier`/`@retrier_async` 自動退避重試(讀取類呼叫);依第 1 節,下單類呼叫不重試、直接失敗 | 通常**不需要**告警(屬於框架已處理的暫時性狀況);**唯一例外**是重試次數耗盡後仍然失敗——此時已經不是「暫時性」問題,應比照上面「市場閉市」等級告警 |
| **部分成交(partial fill)** | 無例外,由 `Order`/`Trade` 的 `filled`/`remaining` 欄位追蹤 | `manage_open_orders`/`update_trade_state`(依 [`architecture-spec.md`](./architecture-spec.md) 6.1 節)持續處理未成交剩餘量,依 `unfilledtimeout` 設定決定何時取消剩餘掛單 | 不需要額外告警(框架既有保證範圍內的正常行為);本文件在此列出只是為了讓錯誤分類表完整,方便對照,不代表這是一個需要人為介入的情境 |

### 8.1 Telegram 通知接線要求

呼應 [`architecture-spec.md`](./architecture-spec.md) 5.2 節與 [`risk-policy.md`](./risk-policy.md) 5.2 節已要求的即時主動推播:Freqtrade 內建的通知分類已經涵蓋上表大多數情境(進場/出場確認、進場/出場成交、錯誤等級事件),但**不是全部預設開啟**,需要在設定檔的 `telegram.notification_settings` 逐項核對啟用。本文件的政策要求是:**正式環境部署前,`notification_settings` 至少需要涵蓋以下事件類別**——entry/exit 確認、entry/exit 實際成交(含部分成交)、`strategy_msg`(供 `custom_stoploss`/第 9 節自訂邏輯內主動送出的訊息使用,例如第 9 節重試耗盡的告警)、error 等級的所有事件。具體設定鍵名清單(Freqtrade 各版本的 `notification_settings` 巢狀結構可能調整)留待 Phase 10 對照當時 `config-common.json` schema 核實,本文件在此只定義「需要涵蓋的事件類別」這個政策要求,不假設固定的鍵名寫法。

---

## 9. 每日虧損熔斷自訂邏輯的重試政策 / Retry Policy for the Daily-Loss Circuit Breaker's Balance Re-query

> 本節回應 [`security-policy.md`](./security-policy.md) 5.3 節明確交棒的政策約束——任何自訂重試須有界、須指數退避 + jitter、須 fail closed、且不可疊加在 ccxt/Freqtrade 已處理的重試之上——並針對 [`risk-policy.md`](./risk-policy.md) 3.4 節指名的具體場景(`confirm_trade_entry` 內的每日損益自訂檢查需要 balance re-query)給出**具體參數**。

### 9.1 情境回顧

[`risk-policy.md`](./risk-policy.md) 3.4 節已誠實揭露:每日虧損熔斷(-4%)沒有對應的原生 Freqtrade Protection,必須實作為策略程式碼內的自訂邏輯(`confirm_trade_entry` 內查詢當日已實現+未實現損益、與開盤權益比較)——這是本文件所有防線中**唯一沒有框架層級雙重保證**的一層,需要單元測試覆蓋邊界情況。這個自訂邏輯需要查詢當下 `Equity`,若這次查詢本身失敗(暫時性網路問題、交易所端暫時性錯誤),需要一個有界、安全的重試策略——這正是 [`security-policy.md`](./security-policy.md) 5.3 節政策約束鎖定的具體場景。

### 9.2 具體重試參數

| 參數 | 值 | 理由 |
|---|---|---|
| 最大重試次數 | **3 次**(不含首次呼叫,共至多 4 次嘗試) | 與第 2 節查證到的 Freqtrade 自己 `API_RETRY_COUNT=4` 次同一量級,不刻意設得更保守或更寬鬆,維持與框架既有慣例一致的直覺 |
| Backoff base | **1 秒** | 每日損益檢查發生在 `confirm_trade_entry`——訊號評估的關鍵路徑上,不應該讓一次查詢重試佔用過長時間拖慢整個主迴圈迭代 |
| Backoff 公式 | `base × 2^attempt + jitter`,`jitter` 為 `[0, base]` 秒內的均勻隨機值 | 依 [`security-policy.md`](./security-policy.md) 5.3 節明確要求的「指數退避 + jitter」;之所以需要 jitter(而第 2 節查證到 Freqtrade 自己的 `@retrier` 反而沒有內建 jitter),是因為若未來策略規模擴大到多個交易對同時觸發重試,沒有 jitter 的重試可能在同一時間點集中打向交易所,jitter 是本文件對政策約束的具體落實,不是照抄 Freqtrade 既有機制 |
| 各次嘗試的預期延遲(範例,僅供實作理解量級,實際依隨機 jitter 浮動) | attempt 0(第 1 次重試前):約 0–1 秒;attempt 1:約 1–3 秒;attempt 2:約 3–5 秒 | 呈現整體重試不會拖太久,同時仍保有指數遞增的退避效果 |
| **總耗時上限(max total duration)** | **10 秒** | 遠低於 `internals.process_throttle_secs` 一次主迴圈週期(第 2 節已確認沿用預設 5 秒——注意:10 秒總耗時上限本身會讓單次主迴圈迭代的實際耗時有機會超過 5 秒的 throttle 目標值,這是刻意接受的權衡,理由是「查清楚當日損益再決定要不要進場」優先於「嚴格準時完成每次迭代」,但 10 秒是這個權衡的明確上限,不允許無界拖長);同時遠低於 1d timeframe 策略對即時性的容忍度,不影響策略本質 |
| **Fail closed 行為** | 重試耗盡後,`confirm_trade_entry` **回傳 `False`**(拒絕本次進場訊號),不下單;同時記錄並告警(依第 8.1 節 Telegram 接線要求) | 直接落實 [`security-policy.md`](./security-policy.md) 5.3 節「重試耗盡後的行為必須是『跳過此次訊號、不下單』,而非『假設一個預設值後繼續』」——**明確禁止**任何形式的「沿用上一次快取的 `Equity` 值繼續計算」這類降級路徑,那正是 [`security-policy.md`](./security-policy.md) 5.2 節第 3 點已經明確禁止的「用猜測值繼續計算」模式在這個場景的翻版 |

### 9.3 為什麼這個重試邏輯不與 ccxt/Freqtrade 已有的 `@retrier` 衝突、不重複疊加

這是 [`security-policy.md`](./security-policy.md) 5.3 節政策約束中最容易被實作階段忽略的一點,本文件在此明確要求一個**實作前的檢查步驟**,而非只是原則性提醒:

- 本節要重試的對象,是「我們自己在 `confirm_trade_entry` 內主動呼叫的一次餘額查詢」。這個查詢若透過 Freqtrade 已暴露的介面呼叫(例如透過 `self.wallets` 或直接呼叫 `self.exchange` 上對應的方法),**這條呼叫路徑本身有可能已經被 Freqtrade 自己的 `@retrier` 裝飾器包住**(依第 2 節查證,讀取類呼叫如 `fetch_balance` 屬於會被 `@retrier` 保護的類別,不同於第 1 節已確認不重試的下單類呼叫)。
- **若我們自己在這一層再疊加一個獨立的 retry loop,會造成「雙層重試」**:Freqtrade 內部已經重試至多 4 次(每次退避可達數秒到十幾秒),我們外層再重試 3 次,最壞情境下單次餘額查詢卡住的合計時間可能遠超過 9.2 節設計的 10 秒總耗時上限預期,實質上架空了這個上限的意義。
- **要求(實作前必須執行的檢查,不是選擇性建議)**:實作時必須先依當時 pin 住的 Freqtrade 版本原始碼,確認我們呼叫的餘額查詢方法本身是否已經被 `@retrier`/`@retrier_async` 包住:
  - **若已經包住**:我們自己這一層的「重試」應退化為單純的「呼叫一次、失敗就直接視為重試耗盡、進入 fail closed」——不再疊加 9.2 節的次數重試,只保留「總耗時上限」與「fail closed」這兩項政策要求(用一個計時器包住整個呼叫,超過總耗時上限視同失敗)。
  - **若確認未被包住**(例如透過一個未經 `@retrier` 保護的路徑直接查詢):9.2 節的完整重試參數才適用。
- 這正是 [`security-policy.md`](./security-policy.md) 5.3 節「不要在 ccxt 已處理的重試上再包一層」的精神,具體落實到這個「政策要求需要重試,但重試對象可能已經被框架保護」的實際場景——本文件不假裝這個判斷可以一次性寫死在規格書裡,因為它依賴「我們選擇用哪一個 Freqtrade 內部介面去查餘額」這個 Phase 10 才會決定的實作細節,但本文件把**判斷方法**與**判斷後兩種情況各自該怎麼做**都定義清楚,不留給實作階段自己摸索。

### 9.4 正常路徑不受影響

本節的重試/fail-closed 邏輯**只在餘額查詢本身失敗時才觸發**——絕大多數情況下第一次查詢就會成功,重試機制完全不介入(dormant),不會因為加了這一層保護就變相拉長每次進場判斷的正常延遲。這與第 2 節「1d 策略對 rate limit 而言是低壓力情境」的結論一致:重試機制的存在是為了處理少數異常情況,不是常態路徑的一部分。

---

## 10. 已知限制與交棒事項 / Known Limitations & Handoff

本文件依 [`architecture-spec.md`](./architecture-spec.md)/[`security-policy.md`](./security-policy.md) 已建立的誠實揭露慣例,逐項列出查證邊界:

- **第 0.2 節已說明的查證層級差異**:本文件第 1、3、5 節多處結論依賴直接讀取 Freqtrade `develop` 分支原始碼(而非官方文件明文承諾),精確度較高但穩定性保證低於公開文件契約——**這是本文件與 `architecture-spec.md`/`security-policy.md` 主要依賴官方文件查證的一個方法論差異,實作前(Phase 10)必須對照當時實際 pin 住的 Freqtrade 版本原始碼重新核實這些細節是否仍然成立**,不能假設 2026-08-07 觀察到的行為會在任意未來版本上保持不變。受此影響的具體結論包含:`create_order`/`create_stoploss` 是否仍然不套用自動重試(第 1.3 節)、`enter_positions`/`handle_similar_open_order` 的訊號級防護邏輯是否仍然存在(第 1.4 節)、Binance Spot 的 `stoploss_order_types` 映射是否仍然只有 `"limit"`(第 5.2 節)。
- **第 1.5 節已誠實揭露的模糊結果窗口**(下單呼叫因網路逾時被判定失敗、但交易所端可能已實際執行)**未被消除,只有緩解**——這是本文件對「冪等下單已經完整解決」這個原始假設最重要的一次修正,不應該在後續文件或實作階段被簡化回「Freqtrade 已經處理好冪等性」這句過度簡化的表述。
- **第 6 節 Testnet 端點覆寫的具體語法(尤其步驟二的備援寫法)本文件刻意不假設固定鍵名**——這不是本文件偷懶,是因為查證過程本身就發現這塊設定在 Freqtrade 文件樹裡的說明位置與具體寫法仍在變動(舊的 sandbox-testing 頁面已經找不到),寫死一個可能已經過時的語法,風險高於誠實留白。
- **第 7 節環境變數覆寫優先序的結論信心程度較高(官方文件明文),但未窮舉驗證所有巢狀鍵與策略類別內建設定的邊界互動**——7.4 節已給出具體的實測驗證建議,實作階段應該執行,不是紙上作業就視為完成。
- **第 4 節建議的定期原始餘額比對機制,具體比對容差與帳戶結構的細節處理(例如如何排除手續費幣種餘額)留給 Phase 10 依實際帳戶組成決定**,本文件只定義行為規格與 fail-closed 原則。
- **第 9.3 節要求的「Freqtrade 內部呼叫路徑是否已被 `@retrier` 包住」判斷,依賴 Phase 10 才會決定的具體介面呼叫方式**,本文件已把判斷方法與兩種結果各自的因應寫清楚,但無法在規格書層級預先鎖定答案本身。
- **本文件所有 `order_types`/`ccxt_config` 等設定鍵名與巢狀結構,以規格撰寫時查閱的 Freqtrade 版本為準**——依 [`architecture-spec.md`](./architecture-spec.md) 9 節已確立的既有原則,若實作時發現與當時 Freqtrade 官方文件或 `freqtrade new-config` 等鷹架工具產生的預設值有出入,以「不重新發明輪子、貼齊框架慣例」為優先,調整本文件而非強行讓實作偏離框架版本的實際結構。

**下一步:** 本文件經專案負責人審閱確認後,Phase 7 結案。依 [`development-plan.md`](./development-plan.md) 規劃順序,可與 Phase 6([`backtest-procedure.md`](./backtest-procedure.md),若尚未結案)一併交棒 Phase 9([`go-no-go-checklist.md`](./go-no-go-checklist.md)),逐項確認「執行層規格涵蓋冪等性、重連、對帳」這條離開條件是否已被本文件滿足——依本文件的查證結果,滿足的方式是「確認 Freqtrade 已提供什麼 + 誠實標出殘留缺口 + 定義補強機制」,而非「重新設計一套執行引擎」,這與 Phase 9 檢查清單原始描述的字面用詞(「涵蓋冪等性、重連、對帳」)在精神上一致,但完成的路徑因 Phase 8 的技術選型決策而不同,審閱時應以此為準。

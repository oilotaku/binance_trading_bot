# Phase 6 — 回測框架與驗證程序 / Backtest Framework & Validation Procedure

> 狀態:**🟡 草稿完成,待專案負責人審閱後確認 / DRAFTED — pending owner review.**
> 負責角色:backtest-analyst
> 範圍限制(承 [`strategy-hypothesis.md`](./strategy-hypothesis.md) 的決定):僅針對 **策略一:Regime-Filtered Momentum Breakout**(Donchian 突破 + 成交量確認 + ATR 移動停損),BTC/ETH 現貨、波段頻率,不處理策略二、三(backlog)。
> 前提假設(承 [`statistical-methodology.md`](./statistical-methodology.md)):該文件已定案 DSR/PSR 公式與門檻、walk-forward 切分規則、Newey-West/block bootstrap、Kelly 倉位公式、蒙地卡羅回撤估計,**本文件不重新定義任何公式或門檻數字**,只負責把它們接上 Freqtrade 的實際輸出、操作化成可執行步驟。第 7 節「Phase 2 通過門檻總表」是本文件執行結果的終審依據,本文件第 5 節原樣照抄該表並補上「如何算出表中每一格數字」與「不通過怎麼辦」。
> 前提假設(承 [`architecture-spec.md`](./architecture-spec.md)):Freqtrade backtesting/hyperopt 輸出落在 `user_data/backtest_results/`、`user_data/hyperopt_results/`;`analysis/` 目錄(repo 根目錄)已保留、由本文件填入內容(第 8 節);策略類別的 callback 對應表(3.1 節)與 hyperopt 可調參數分類表(3.2 節,N/X/M/ATR 週期/k 可調,`risk_fraction`/Kelly `c` 絕不可調)已定案,本文件據此設計 hyperopt 的 `--spaces`,不重新分類。
> 前提假設(承 [`risk-policy.md`](./risk-policy.md)):`timeframe=1d`(6.1 節)、ATR 停損倍數 `k` 搜尋邊界 `[2.0, 4.0]`(2.1 節)、`minimal_roi` 必須明確停用為 `{"0": 10}` 一類的值(2.2 節)、45 天 time-stop(2.3 節)、`risk_fraction` 硬上限 1.5%/合併 2.5%(第 1、4 節)。本文件的任務之一是**用真實資料驗證這些數字是否成立**(第 6 節),而非重新拍板——重新拍板須走該文件第 7 節的變更流程。
> 前提假設(承 [`tech-stack-decision.md`](./tech-stack-decision.md)):以 Freqtrade 的 `download-data`/`backtesting`/`hyperopt`/`lookahead-analysis` 為工具鏈,本文件不重新發明資料下載、回測撮合或參數搜尋引擎。
> 對應 [`development-plan.md`](./development-plan.md) Phase 6 要求清單(歷史資料/成本模型/績效指標/通過門檻/paper trading 銜接);該計畫文件本節描述較簡略,**以 [`statistical-methodology.md`](./statistical-methodology.md) 已定案的更細緻方法論為準**,本文件依後者操作化。

---

## 0. 文件定位與方法論

### 0.1 這份文件在做什麼、不做什麼

[`statistical-methodology.md`](./statistical-methodology.md) 開宗明義說「Freqtrade 本身不內建 DSR、block bootstrap、Newey-West」,這些需要在 Freqtrade 的 `backtesting`/`hyperopt` 輸出之上用獨立 Python 後處理腳本實作,並明確把「怎麼落地」交棒給本文件,同時警告「公式與門檻在此定案,Phase 6 不重新發明」。因此本文件的產出是:

1. 一份**可以直接照著做的 runbook**——從 `freqtrade download-data` 開始,到最終產出 [`statistical-methodology.md`](./statistical-methodology.md) 第 7 節總表每一格數字的具體步驟順序、CLI 指令、以及每一步驟串接下一步驟的資料格式。
2. 一份**`analysis/` 目錄的模組地圖**(第 8 節),讓 Phase 10 實作者不需要重新設計就知道要寫哪些檔案、每個檔案吃什麼吐什麼。
3. 明確標記所有 [`statistical-methodology.md`](./statistical-methodology.md)、[`risk-policy.md`](./risk-policy.md) 已留給「Phase 6 用真實資料校準」的項目,並給出具體校準程序(尤其是 embargo_days 的雞生蛋問題,見 4.1 節)。

**不做的事:** 不重新推導 DSR/PSR/Kelly/蒙地卡羅公式(那是 [`statistical-methodology.md`](./statistical-methodology.md) 的內容,本文件只引用);不重新決定風控硬上限數字(那是 [`risk-policy.md`](./risk-policy.md) 的內容,本文件只驗證);不撰寫實際程式碼(那是 Phase 10)。

### 0.2 執行參數總覽表(供之後直接查閱)

| 類別 | 參數 | 數值 |
|---|---|---|
| 交易所/資料源 | exchange | Binance,`trading-mode=spot` |
| 標的 | pairs | `BTC/USDT`、`ETH/USDT` |
| K 棒週期 | timeframe | `1d`(承 [`risk-policy.md`](./risk-policy.md) 6.1 節既有決定) |
| 原始資料下載起點 | download start | **2019-09-01**(見 1.2 節,含指標暖機緩衝) |
| Walk-forward 第一個 fold IS 起點 | anchor | **2020-01-01**(見 1.2 節,刻意避開 2017–2019 舊 regime) |
| Walk-forward 切分 | IS/OOS/步進 | **24 個月(滾動)/ 6 個月 / 6 個月**(承 [`statistical-methodology.md`](./statistical-methodology.md) 3.2 節,不重新定案) |
| Embargo(初始值,待校準) | embargo_days | **30 天(下限,Pass A 用此值,Pass B 依實測持倉分布校準,見 4.1 節)** |
| 目前可用完整 fold 數(以今日 2026-08-07 為基準) | fold 數 | **9 個**(落在 [`statistical-methodology.md`](./statistical-methodology.md) 目標 8–10 個區間內,見 1.3 節) |
| Hyperopt 每 fold trial 數 | epochs | **1,000**(見 4.3 節理由) |
| Hyperopt 搜尋空間 | `--spaces` | `buy sell`(不含 `roi`/`trailing`/`protection`,理由見 4.3 節) |
| 手續費假設 | fee | **0.10% / 0.10%**(taker/maker,Binance 現貨 VIP0、未計 BNB 折扣,見 2.1 節,**待實作時核實實際帳戶費率**) |
| 滑價假設(post-processing haircut) | slippage | **每邊 0.05%(5 bps)、來回合計 0.10%**(見 2.2 節,**待 paper trading 驗證**,見第 7 節) |
| 資金費率成本 | funding rate | **不適用**(現貨,見 2.3 節) |
| Sharpe 年化 | annualization | **`× √365`,絕不可用 `× √252`**(承 [`statistical-methodology.md`](./statistical-methodology.md) 第 1 節明訂) |

以下各節逐一給出推導過程與具體步驟,不是憑空列表。

---

## 1. 歷史資料 / Historical Data

### 1.1 資料來源

用 Freqtrade 內建的 `download-data` 子指令,對接 Binance 現貨 REST API(經 ccxt),下載 OHLCV K 線資料,指令層級參數概念如下:

| 參數 | 值 | 說明 |
|---|---|---|
| `--exchange` | `binance` | 承 [`tech-stack-decision.md`](./tech-stack-decision.md) |
| `--trading-mode` | `spot` | 承 [`scope.md`](./scope.md) 第一版只做現貨的決定 |
| `--pairs` | `BTC/USDT ETH/USDT` | 承 [`strategy-hypothesis.md`](./strategy-hypothesis.md)、[`scope.md`](./scope.md) |
| `--timeframe` | `1d` | 承 [`risk-policy.md`](./risk-policy.md) 6.1 節 |
| `--timerange` | `20190901-`(至下載當下最新完整日 K,見 1.2 節) | 見下 |
| `--data-format-ohlcv` | `feather`(Freqtrade 現行預設) | 沿用框架預設,不自訂格式 |

資料存放於 `user_data/data/binance/`,已在 `.gitignore`(承 [`architecture-spec.md`](./architecture-spec.md) 2.1 節目錄結構)。

### 1.2 時間範圍:為什麼是 2019-09 起下載、2020-01 起才當作有效訓練資料

**兩個不同的日期,不能混為一談:**

- **原始資料下載起點:2019-09-01。** 這只是給指標暖機用的緩衝——`startup_candle_count` 需要 `max(N, M, ATR 週期) + 緩衝`([`architecture-spec.md`](./architecture-spec.md) 3.3 節),N、M 常見範圍約 20–55 天、ATR 週期約 14 天([`statistical-methodology.md`](./statistical-methodology.md) 3.3 節既有估計),抓 4 個月(約 120 天)緩衝遠超過這個量級所需,留有餘裕。這段資料**不會被當成任何 fold 的 IS 訓練資料**,純粹讓第一個 fold 開始時指標已經穩定,不產生 NaN 訊號。
- **第一個 fold 的 IS 視窗起點:2020-01-01。** 這才是實際影響驗證結果的日期,理由見下。

**為什麼不把 IS 起點往前推到 2017–2018 年(增加更多歷史/更多 fold)?**

[`statistical-methodology.md`](./statistical-methodology.md) 3.2 節已明確標記:「2017-2019 年的舊資料...那個時期的市場結構(機構化程度、流動性)與現在已有實質差異」,這是該文件選擇**滾動窗**而非**擴張窗**的理由之一——但滾動窗解決的是「舊資料權重不會被稀釋掉」的問題,不解決「要不要一開始就把這段被明確標記為結構不同的期間納入任何一個 fold 的訓練資料」的問題。若把 IS 起點推到 2018 年,會有 1–2 個早期 fold 的 IS 視窗完全落在這段被自己的方法論文件明確警告「市場結構已與現在有實質差異」的期間內,這些 fold 的 hyperopt 選出的參數用來評估「策略一在接近現在的市場結構下是否有效」的參考價值可疑,反而稀釋整體驗證的可信度。**2020-01-01 作為 IS 起點,完整避開這段被明確標記的期間**,同時：

- BTC/USDT、ETH/USDT 在 Binance 現貨自 2017 年即已上市,資料可得性不是限制因素,是**刻意選擇不用**這段資料,不是拿不到。
- 2020 年也大致對應加密市場機構化程度顯著提升的轉折點(COVID 後機構資金大量進場),與 2017-2019 年的市場結構差異在直覺上也站得住腳。

### 1.3 為什麼 2020-01-01 這個起點能產生落在目標區間的 fold 數

以 [`statistical-methodology.md`](./statistical-methodology.md) 3.2 節定案的 **24 個月 IS(滾動)/ 6 個月 OOS / 6 個月步進**、3.3 節的 embargo 下限 30 天,從 IS 起點 2020-01-01 往前推算,截至今日(2026-08-07)可以取得的**完整**(OOS 視窗已完全落在今日之前)fold 如下:

| Fold | IS 視窗(24 個月) | Embargo(30 天) | OOS 視窗(6 個月) |
|---|---|---|---|
| 1 | 2020-01-01 ~ 2021-12-31 | 2022-01-01 ~ 2022-01-30 | 2022-01-31 ~ 2022-07-30 |
| 2 | 2020-07-01 ~ 2022-06-30 | 2022-07-01 ~ 2022-07-30 | 2022-07-31 ~ 2023-01-30 |
| 3 | 2021-01-01 ~ 2022-12-31 | 2023-01-01 ~ 2023-01-30 | 2023-01-31 ~ 2023-07-30 |
| 4 | 2021-07-01 ~ 2023-06-30 | 2023-07-01 ~ 2023-07-30 | 2023-07-31 ~ 2024-01-30 |
| 5 | 2022-01-01 ~ 2023-12-31 | 2024-01-01 ~ 2024-01-30 | 2024-01-31 ~ 2024-07-30 |
| 6 | 2022-07-01 ~ 2024-06-30 | 2024-07-01 ~ 2024-07-30 | 2024-07-31 ~ 2025-01-30 |
| 7 | 2023-01-01 ~ 2024-12-31 | 2025-01-01 ~ 2025-01-30 | 2025-01-31 ~ 2025-07-30 |
| 8 | 2023-07-01 ~ 2025-06-30 | 2025-07-01 ~ 2025-07-30 | 2025-07-31 ~ 2026-01-30 |
| 9 | 2024-01-01 ~ 2025-12-31 | 2026-01-01 ~ 2026-01-30 | 2026-01-31 ~ 2026-07-30 |

**9 個完整 fold**,落在 [`statistical-methodology.md`](./statistical-methodology.md) 3.5 節「最少 5 個、目標 8–10 個」的目標區間內,不需要為了湊 fold 數而侵入被 1.2 節排除的 2017-2019 期間。第 10 個 fold 的 OOS 會落在 2026-07-31 ~ 2027-01-30,尚未完整發生,**不納入本輪驗證**;每隔 6 個月(下一次 OOS 視窗結束時)可以自然新增一個 fold,重跑本文件第 4 節流程時應重新產生這張表,不是一次性寫死。

此表的 embargo 欄位使用 3.3 節既有下限 30 天,是 **Pass A(校準輪)** 用的暫定值,真正拿來產出第 5 節通過/不通過判定的是 **Pass B(正式輪)**,embargo 校準後可能改變確切日期,見 4.1 節。

### 1.4 K 棒/資料品質與缺漏處理規則

[`architecture-spec.md`](./architecture-spec.md) 已標記「結構化交易紀錄的確切欄位清單」待 Phase 6 實測後確認,但**資料層本身的缺漏/異常處理規則**目前完全沒有文件定義過——這是本文件必須補上的空白。BTC/USDT、ETH/USDT 是 Binance 現貨流動性最深的兩個交易對,實務上完整缺漏日 K 的機率很低,但規則必須先定義,不能等真的遇到才臨時決定:

**規則 1 — 缺漏 K 棒偵測(下載後、進入任何 fold 之前的強制步驟):**

對每個 pair,以下載範圍(1.2 節)產生期望的完整 UTC 日曆日期索引,與實際下載回來的 K 棒時間戳做差集比對。任何缺漏都必須先被列出來,不能靜默略過。

**規則 2 — 缺漏的處理方式(依缺漏規模分級,不是統一規則):**

| 缺漏規模 | 處理方式 | 理由 |
|---|---|---|
| **單根、孤立的缺漏**(例如下載當下 API 短暫異常漏抓一根) | 先用 `download-data --erase` 重新完整下載該 pair 一次;若重下後仍缺漏,**不得**用前值填補(forward-fill)或插值生成一根「合成」K 棒 | Forward-fill 會捏造一根從未真實存在過的價格,若這根合成 K 棒剛好落在某個 fold 的訊號判斷窗附近,等於用假資料產生真訊號——這是本文件必須主動防範的資料層面 lookahead/資料捏造風險,不是效能或方便考量能凌駕的事 |
| **重下後仍持續缺漏**(懷疑 Binance 本身該日期資料有問題,而非我方下載失敗) | 該筆缺漏日期記錄在案,若落在任一 fold 的 IS 或 OOS 視窗內,**該 fold 標記為「資料缺口警示」**,執行到第 4 節步驟時人工檢查缺口是否影響到指標計算窗或任一筆交易的進出場判斷;若有實質影響,該 fold 從第 5 節「≥70% fold OOS Sharpe>0」等統計量計算中剔除,並在最終報告中揭露剔除原因,不得悄悄跳過不提 | 呼應本文件開頭「backtest-analyst 誠實報告」的角色要求——資料缺口若真的影響到某個 fold 的訊號,把它悄悄留在樣本裡計算平均值,是另一種形式的資料品質問題被統計平均掩蓋 |
| **連續多日/長時間缺漏**(懷疑交易所停機或資料源中斷) | 整段缺漏期間視為「資料不可用期間」,任何跨越這段期間進場的交易一律標記為「資料完整性存疑」,不計入任何統計量;若缺漏期間落在某 fold 的 embargo/OOS 窗口內,該 fold 需要延後(整組往後平移到資料完整為止),不得為了維持 fold 數硬跑不完整資料 | 長時間缺漏通常代表真實的市場事件(交易所暫停服務),用有缺口的資料硬算出的回測結果不具參考價值,寧可少一個 fold 也不要用假設出來的價格路徑污染統計檢定 |

**規則 3 — 異常值(非缺漏,而是「有資料但看起來不對」)的處理:**

對每根 K 棒計算 `(high - low) / ATR`(用當時已知的 ATR,不用未來值),若某根 K 棒的振幅相對其鄰近 K 棒的 ATR 出現數量級異常(例如單根振幅 > 過去 20 日 ATR 的 10 倍,且成交量並未同步異常放大——量價背離的價格尖刺,常見於資料源記錄到極短暫的異常成交或錯誤 tick 被誤記為日 K),標記為「疑似異常值」,**人工檢視該根 K 棒是否落在其他資料源(如 Binance 官網歷史 K 線圖)也能複現**;若無法複現(疑似資料源本身錯誤),按規則 2「重下後仍持續缺漏」的處理精神處理(不憑空修正,寧可標記剔除也不臆測正確值)。此規則屬於保守的資料品質防線,預期在 BTC/USDT、ETH/USDT 這兩個高流動性標的上觸發頻率極低,但規則必須存在,不能假設「不會發生」。

---

## 2. 交易成本模型 / Cost Model

### 2.1 手續費 / Fees

Binance 現貨標準費率(VIP 0 級距,未套用 BNB 折抵)為 **maker 0.10% / taker 0.10%**(對稱費率,現貨無 maker rebate);若帳戶啟用 BNB 折抵可降至約 0.075%/0.075%(25% 折扣)。

**本文件採用 0.10%/0.10%(不假設 BNB 折扣)作為回測 `--fee` 參數**,理由:

- BNB 折扣依賴帳戶內持續持有足額 BNB 餘額,這是一個**營運假設**(需要額外資金配置、且該功能可能隨 Binance 政策調整而變動),不應該被寫進回測的成本模型裡當作既定事實——用不打折的費率是保守方向的假設,若實際帳戶有折扣,真實表現只會比回測**更好**,不會製造虛假樂觀。
- Freqtrade `backtesting --fee 0.001` 的語法把費率套用在進場與出場兩端(對應 0.10% × 2 = 0.20% 來回),Freqtrade 官方文件已確認「custom fee applies twice, once at entry once at exit」,不需要自己在成本計算時再乘以 2。

**這是一個待驗證假設,非最終數字。** Binance 費率表會隨帳戶 30 天交易量、VIP 等級、平台政策調整,**Phase 10 實作/Phase 11 Testnet 驗證階段,必須對照當時帳戶實際生效的費率表重新確認這個數字**,若與 0.10%/0.10% 有出入,依 [`risk-policy.md`](./risk-policy.md) 精神(數字要有依據,不能憑感覺)重新設定 `--fee` 並重跑受影響的回測。

### 2.2 滑價模型 / Slippage

**已知問題:Freqtrade 回測預設對「有沒有滑價」過度樂觀。** 依 Freqtrade 官方 backtesting 文件:「All orders are filled at the requested price (no slippage) as long as the price is within the candle's high/low range」——只要價格落在該根 K 棒的高低範圍內,回測就假設**完全按照期望價格成交、零滑價**。對日內高頻策略而言,這個假設的落差可能相對有限(訂單簿深度變化快、但持倉時間也短);但對本策略而言,**這個假設的方向性風險其實較小但不是零**:策略一是波段突破策略,訊號產生後**強制隔根進場**([`strategy-hypothesis.md`](./strategy-hypothesis.md) 已定案),進場時機是下一根日 K 開盤,以 Freqtrade 預設用開盤價撮合——真實下單時,從「訊號成立」到「機器人真的送出市價單」之間有秒級到分鐘級的延遲,且 Donchian 突破訊號本質上是「價格剛突破新高/新低」的時刻,正是流動性提供者可能暫時撤單、價差擴大的時刻,不能假設零滑價。

**Freqtrade 沒有原生的「隨機滑價模型」參數可以直接開啟**(這不同於「調整費率」那麼簡單),因此本文件的做法是:**不修改 Freqtrade 的費率參數去偷渡滑價成本(那會讓 `--fee` 這個數字失去單一意義、之後很難拆解debug),而是在 `analysis/` 後處理階段對匯出的交易明細做一次獨立、透明的滑價 haircut。**

**具體做法(對接第 8 節 `analysis/cost_model.py`):**

1. Freqtrade 匯出的每筆交易含 `open_rate`(進場成交價)、`close_rate`(出場成交價)。
2. 對每筆交易,**進場價格上調 5 bps(0.05%)、出場價格下調 5 bps**(方向皆對策略不利,模擬「買貴賣便宜」的滑價),重新計算 `profit_ratio`/`profit_abs`。
3. 用調整後的交易明細,而非 Freqtrade 原始輸出,餵進第 3 節之後所有績效指標與統計檢定的計算。

**5 bps/邊、來回 10 bps 的理由與其保守程度:** BTC/USDT、ETH/USDT 是 Binance 現貨深度最好的兩個交易對,以 [`scope.md`](./scope.md) 已定案的資金規模量級(模擬起始 1,000–5,000 USDT,對應 [`risk-policy.md`](./risk-policy.md) 單筆名目部位上限 ≤50% 權益,實際單筆下單金額量級落在數百到約 2,500 USDT),相對這兩個標的的訂單簿深度而言是極小單,理論上實際滑價應遠低於 5 bps。選擇 5 bps 是**刻意保守**的假設,不是精確估計——**這個數字的真正驗證來源是第 7 節 paper trading 階段的實際成交價比對,不是理論推算**,回測階段只需要一個方向正確、量級合理的保守假設,不需要假裝能精確預測未來滑價。

**成本假設的合計示意(算術示範,非真實回測結果):**

```
單筆來回總成本假設 = 手續費(0.10%×2 = 0.20%) + 滑價(0.05%×2 = 0.10%) ≈ 0.30%(占名目部位)
```

這個 0.30% 是後續判讀所有績效指標時的背景認知——任何一筆交易的毛利若小於這個量級,實際上是虧損交易,`analysis/` 的所有統計量都必須用扣除成本後的淨報酬計算,不得用毛報酬。

### 2.3 資金費率 / Funding Rate:不適用,確認非模型缺口

[`strategy-hypothesis.md`](./strategy-hypothesis.md) 已明確把「資金費率情緒濾網」列為**輔助訊號、非策略一的一部分**,且狀態為 backlog;[`scope.md`](./scope.md) 已確認第一版僅現貨、不交易永續合約。資金費率(funding rate)是永續合約特有的持倉成本機制,現貨部位不涉及資金費率的收付。**本文件確認這不是成本模型的缺口,而是範圍本身就不適用**——不需要在 `--fee` 或滑價 haircut 之外額外建模一項「資金費率成本」,因為策略一從頭到尾沒有任何一筆交易會產生這項費用。若未來 v2 依 [`strategy-hypothesis.md`](./strategy-hypothesis.md) 已標記的方向納入永續合約或啟用資金費率濾網,屆時本節需要整段重寫,不是加一行補充。

---

## 3. 必須輸出的績效指標 / Required Output Metrics

承 [`stable-profitability-roadmap.md`](./stable-profitability-roadmap.md) 中 backtest-analyst 角色原本提出的「完整績效指標儀表板」與 [`statistical-methodology.md`](./statistical-methodology.md) 全篇要求,任何一次回測/OOS 結果報告都**必須**完整輸出以下指標,**不得只挑好看的幾項報告**——這是本文件對「誠實報告全部指標,不是挑著報告」這個角色紀律的具體落實:

| 指標 | 定義/計算基礎 | 來源 | 特別注意事項 |
|---|---|---|---|
| 總報酬(Total Return) | 期末權益 / 期初權益 - 1 | Freqtrade backtest report 原生輸出 | 用第 2.2 節滑價調整後的交易明細重算,不用 Freqtrade 原生零滑價數字 |
| 年化報酬 | 依實際回測期間長度年化 | 同上 | — |
| **`SR_daily`** | 帳戶每日 mark-to-market 權益曲線報酬,年化**乘 `√365`** | Freqtrade backtest report,或 `analysis/` 重算 | **絕對不可用 `√252`**(承 [`statistical-methodology.md`](./statistical-methodology.md) 第 1 節明訂的常見錯誤警告)。**必須在報告產生時明確核對** Freqtrade 當時版本 backtest report 與 `SharpeHyperOptLossDaily` 使用的年化天數是否確實為 365;若版本預設值不同或不透明,一律在 `analysis/` 後處理階段用調整後的日報酬序列自行以 `× √365` 重新計算,不得直接沿用 Freqtrade 報告數字 |
| **`SR_trade`** | 逐筆交易報酬計算,**不年化** | `analysis/significance.py` | 用於第 1/3/4 節的統計檢定與 Kelly 公式輸入,與 `SR_daily` 是兩個不同用途的數字,報告中必須並列標示清楚是哪一種,不可混用 |
| Sortino Ratio | 僅用下方波動度(downside deviation)為分母的風險調整報酬 | Freqtrade backtest report(若版本原生支援)或 `analysis/` 補算 | 年化基準同樣是 `√365`,理由同上 |
| MDD(最大回撤) | 歷史單一路徑的最大回撤 | Freqtrade backtest report 原生輸出 | 這只是**單一歷史路徑**的數字,不能取代第 6 節蒙地卡羅的機率分布,兩者都要報告,不能只報告較好看的那個 |
| 勝率(Win Rate) | 獲利交易數 / 總交易數 | Freqtrade backtest report 原生輸出 | 承 [`risk-policy.md`](./risk-policy.md) 1.2 節既有預期(右偏肥尾分布下勝率常低於 50%,常見趨勢突破系統落在 35–45%),若回測勝率明顯偏離此區間(過高或過低),應在報告中額外標註並檢查是否有資料/邏輯問題,而非直接視為好消息照單全收 |
| 平均獲利/平均虧損(Avg Win / Avg Loss) | 獲利交易平均報酬、虧損交易平均報酬,兩者分別報告且比值明確標出 | Freqtrade backtest report 原生輸出 | 這組數字直接反映策略「右偏肥尾」的假說是否在真實資料中成立——若平均獲利遠大於平均虧損的形狀沒有出現,代表策略一的核心經濟假說([`strategy-hypothesis.md`](./strategy-hypothesis.md))可能不成立,這比單純看總報酬更早發現問題 |
| Profit Factor | 總獲利金額 / 總虧損金額(絕對值) | Freqtrade backtest report 原生輸出 | — |
| 交易次數(Trade Count) | 原始交易筆數 `n`(不分 IS/OOS/purge 前後,各版本都要報告) | Freqtrade backtest report 原生輸出 | 這是 [`statistical-methodology.md`](./statistical-methodology.md) 第 1 節明訂「一律以交易筆數為單位,絕不可用日曆天數替代」的 `n` 本尊,必須明確報告 purge 前、purge 後、以及 `n_eff` 三個數字,不能只報告其中一個讓讀者誤以為它們是同一件事 |
| **`n_eff`** | Newey-West 校正自相關後的有效樣本數 | `analysis/sample_size.py` | 承 [`statistical-methodology.md`](./statistical-methodology.md) 4.4 節,**`n_eff ≥ 30` 為硬性下限**,低於此值該筆結果自動不通過,不論其他數字多好看,見第 5、6 節 |
| **`IF`**(inflation factor) | `n / n_eff` | `analysis/sample_size.py` | **`IF > 3` 須額外標記「高度聚類風險」**(承 4.4 節),即使統計檢定通過也要在報告中揭露 |

**Freqtrade 原生輸出不含的欄位(`n_eff`、`IF`、DSR、PSR、Reality Check p-value、蒙地卡羅機率)一律標記「來源:`analysis/`」**,讓讀者清楚分辨哪些數字是框架直接算出來的、哪些是本專案自己實作的後處理——這個區分本身就是誠實揭露方法論的一部分,不是文書排版問題。

---

## 4. 驗證管線:具體執行步驟 / The Validation Pipeline

### 4.1 兩輪執行:Pass A(embargo 校準)→ Pass B(正式輪)

[`statistical-methodology.md`](./statistical-methodology.md) 3.3 節的 embargo 公式本身有一個雞生蛋問題:`embargo_days = max(N, M, ATR 週期) + 持倉天數的 95th 百分位`,但「持倉天數的 95th 百分位」是**回測跑出真實交易後才知道的輸出**,不是可以事先代入的輸入。該節已預見這個問題並給出處理原則:「`embargo_days` 建議下限抓 30 天,實際數字待 Phase 6 用真實回測的持倉時間分布重新校準,但不得低於 30 天」。本文件把這個原則具體化為兩輪執行:

- **Pass A(校準輪):** 用 `embargo_days = 30`(下限值)完整跑一次第 4.3 節的 9-fold 流程(可以用較少 hyperopt epochs,例如 200,加速這一輪,因為 Pass A 的參數本身不是最終要用的結果,只是為了取得持倉天數分布)。
- **校準:** 取 Pass A 所有 fold OOS 交易的**持倉天數分布**,計算其 95th 百分位,重新代入 `embargo_days = max(N, M, ATR 週期) + 該 95th 百分位`(`N`、`M`、ATR 週期取各 fold hyperopt 選出值的中位數或聯集上界,若各 fold 差異大則額外檢查第 4.4 節的參數穩定性)。若重新算出的 `embargo_days` 仍是 30 天量級,Pass A 的 fold 表(1.3 節)不需要調整,直接進入 Pass B;若明顯大於 30 天(例如超過 45 天,已經逼近 [`risk-policy.md`](./risk-policy.md) 2.3 節的 time-stop 本身),需要重新產生 1.3 節的 fold 日期表(embargo 變長會壓縮可用 OOS 天數,fold 數可能因此略降,若降到 5 個以下,依 [`statistical-methodology.md`](./statistical-methodology.md) 3.5 節視為方法論本身無效,需要往前檢討是否要延伸資料起點或放寬 embargo 精神性下限)。
- **Pass B(正式輪):** 用校準後的 `embargo_days` 與完整 1,000 epochs 重跑第 4.3 節全部流程,**這一輪的結果才是餵進第 5 節通過/不通過判定的正式數字**,Pass A 的結果不得混入最終報告的統計量計算,只作為校準過程留痕。

### 4.2 前置檢查(Step -1,任何 fold 開始前必須先做一次)

在花費運算資源跑 9 個 fold 的 hyperopt 之前,先用 Freqtrade 內建的 `lookahead-analysis` 工具檢查策略程式碼本身:

```
freqtrade lookahead-analysis --strategy RegimeFilteredMomentumBreakout --timerange <涵蓋至少一個完整 fold 的區間>
```

這個工具會自動偵測 `.shift(-N)`、不當的 `.iloc[]`、未 roll 的聚合函式等常見 lookahead 寫法,直接對應 [`architecture-spec.md`](./architecture-spec.md) 3.1 節「Donchian 上軌必須用 `.shift(1)`」這條結構性要求是否真的被正確實作。**若此步驟發現任何被標記為 biased 的訊號,必須先修正策略程式碼、重新走完 Phase 10 的 code review,才能進入第 4.3 節**——在明知策略程式碼可能有 lookahead bias 的狀態下跑 9 個 fold 的完整驗證管線,是浪費運算資源在一個已知會被推翻的結果上。

### 4.3 逐 fold 步驟(對每個 fold,依 1.3 節表格的 9 個 fold 各執行一次)

| 步驟 | 動作 | 具體做法 | 輸出 |
|---|---|---|---|
| 1 | IS 資料上跑 hyperopt | `freqtrade hyperopt --strategy RegimeFilteredMomentumBreakout --timerange <該 fold IS 起訖> --hyperopt-loss PurgedTradeSharpeLoss --spaces buy sell -e 1000 --random-state <固定值,確保可重現>`。`--spaces` 只含 `buy`(N、X、M、ATR 週期)與 `sell`(k),**不含 `roi`/`trailing`/`protection`**——`minimal_roi` 已依 [`risk-policy.md`](./risk-policy.md) 2.2 節寫死為停用值、Protections 參數依 3.2 節絕不可調,讓 hyperopt 去搜尋這些空間會直接違反已定案的治理決策。loss function 用第 8 節 `analysis/` 提供的自訂 `PurgedTradeSharpeLoss`(見下方說明),不用 Freqtrade 內建的 `SharpeHyperOptLoss`/`SharpeHyperOptLossDaily`,理由見下 | `user_data/hyperopt_results/` 下該 fold 的完整 epoch 紀錄(1,000 筆,含每組參數與對應 loss/Sharpe) |
| 2 | 匯出 hyperopt 完整 epoch 紀錄 | `freqtrade hyperopt-list`/`hyperopt-show --print-json` 匯出**全部** 1,000 個 epoch(不是只匯出最佳那組)——`σ_SR` 需要全部 trial 的 Sharpe 分布才能算,只留最佳結果會讓 DSR 公式沒有輸入資料 | 該 fold 完整 epoch 的 `(params, SR_trade)` 表 |
| 3 | `analysis/sample_size.py` 計算 `n_eff` | 對該 fold **hyperopt 選出的最佳參數組、purge 後(已在 loss function 內排除)的 IS 交易報酬序列**做 Newey-West 校正,依 [`statistical-methodology.md`](./statistical-methodology.md) 4.2 節公式算出 `n_eff`、`IF`;並行跑 block bootstrap(4.3 節,`arch.bootstrap.StationaryBootstrap`,`B=10,000`)交叉驗證 | 該 fold 的 `(n_eff, IF, bootstrap 95% CI)` |
| 4 | `analysis/significance.py` 計算該 fold DSR | 用步驟 2 全部 epoch 的 `SR_trade` 樣本標準差當 `σ_SR`,依 [`statistical-methodology.md`](./statistical-methodology.md) 2.2 節公式先算 `N`(或依 2.3 節對高相關 trial 分群後的有效 `N'`,見下方說明)、再算 `SR0`,最後代入 PSR 公式得該 fold 的 DSR。**每個 fold 都算一次 DSR 並全部保留**,不是只算最後一個 fold——理由見 4.4 節 | 9 個 fold 各自的 DSR 值 |
| 5 | OOS 回測(套用該 fold hyperopt 選出的凍結參數) | `freqtrade backtesting --strategy RegimeFilteredMomentumBreakout --timerange <該 fold OOS 起訖>`,策略讀取步驟 1 選出的最佳參數(依 Freqtrade 慣例讀取隨策略存放的 `<StrategyName>.json` 參數覆寫檔,精確檔名/載入旗標依當時版本核實)。**這個步驟的參數是凍結的,不得在 OOS 視窗內重新 hyperopt**,否則就不是樣本外驗證 | 該 fold OOS 交易明細(JSON) |
| 6 | 套用第 2.2 節滑價 haircut | `analysis/cost_model.py` 對步驟 5 匯出的 OOS 交易明細做進場 +5bps / 出場 -5bps 調整 | 調整後 OOS 交易明細 |
| 7 | 記錄該 fold 的 `SR_trade`(OOS)、持倉天數分布、hyperopt 選出的 N/X/M/ATR 週期/k | 供 4.4 節做「≥70% fold OOS Sharpe>0」「參數穩定性 CV<0.5」判定,以及第 6 節 risk-policy.md 假設驗證 | 該 fold 摘要列(供彙總表) |

**關於步驟 1 為什麼用自訂 loss function `PurgedTradeSharpeLoss` 而非內建 `SharpeHyperOptLoss`:**

1. **Purge 的實作接線點就在這裡。** [`statistical-methodology.md`](./statistical-methodology.md) 3.3 節的 purge 規則(「任何在 IS 視窗最後 `embargo_days` 天內進場的交易,一律從 IS 的 hyperopt 目標函數計算中剔除」)必須在 hyperopt 每次評估一組參數、計算 loss 之前生效,而不是在 hyperopt 跑完之後才補救——用一個自訂 `IHyperOptLoss` 子類別,在計算 Sharpe 之前先過濾掉 `open_date` 落在 `(IS_end - embargo_days, IS_end]` 區間內的交易,是唯一能讓 purge 規則真正影響 hyperopt 選擇結果的做法。這正好對應 [`architecture-spec.md`](./architecture-spec.md) 2.2 節保留、但刻意不先填內容的 `user_data/hyperopts/` 目錄——本文件在此明確填入這個需求。
2. **明確對齊 `SR_trade` 定義,不依賴內建 loss function 的確切計算基礎。** Freqtrade 內建同時提供 `SharpeHyperOptLoss` 與 `SharpeHyperOptLossDaily` 兩種版本,暗示兩者計算基礎不同(逐筆交易 vs. 先重採樣成日報酬)。[`statistical-methodology.md`](./statistical-methodology.md) 第 1 節明確要求 DSR/PSR 用的 `SR_trade` 是「逐筆交易報酬計算,未按日曆年化」的版本——與其信任某個版本的內建 loss function 名稱暗示的行為(可能隨版本演進而變),不如在自訂 loss function 裡明確寫死計算基礎為逐筆交易報酬,消除這個依賴 Freqtrade 版本細節的不確定性。**若 Phase 10 實作時發現當時版本的 `SharpeHyperOptLoss` 確實就是逐筆交易基礎、行為與自訂版本一致,可以考慮改用內建版本簡化維護,但仍需保留 purge 過濾邏輯,不能因此放棄第 1 點的需求。**

**關於 hyperopt trial 相關性分群(對應 [`statistical-methodology.md`](./statistical-methodology.md) 2.3 節):** 理想做法是對每個 epoch 的**每日報酬序列**兩兩計算相關係數,>0.9 視為同群。若當時 Freqtrade 版本的 hyperopt 輸出不含足夠細節重建每個 epoch 的完整日報酬序列(僅有摘要統計量),退而求其次的做法是:只對 hyperopt 選出的**前 20–50 名候選**(而非全部 1,000 個 epoch)重新個別執行一次完整 backtest 取得其日報酬序列,做相關性分群後得到有效群數 `N'`,並在報告中明確標註使用的是這個近似做法而非對全部 1,000 個 epoch 分群——**誠實標註方法上的近似,好過假裝做了完整分群**。全部 1,000 個 epoch 各自的原始 loss 值(而非日報酬序列)無論如何都會被完整保留,`σ_SR` 的計算不受此近似影響。

### 4.4 全部 9 個 fold 完成後:串接與最終檢定

依 [`statistical-methodology.md`](./statistical-methodology.md) 3.2 節設計,9 個 fold 的 OOS 視窗**首尾相接、互不重疊**,可以直接串接成一條連續的模擬實盤報酬序列。以下是 [`statistical-methodology.md`](./statistical-methodology.md) 第 7 節總表本身沒有明講、但作為「操作化」文件本文件必須明確定義的執行細節——**這些是本文件在既有方法論框架下做的具體執行決定,不是重新定義門檻本身**:

| 統計量 | 用哪個 fold / 哪段資料 | 理由 |
|---|---|---|
| **主檢定 DSR** | **最終 fold(第 9 個,即最近一期)的 IS 資料、該 fold 的 `N`/`σ_SR`** | 第 9 個 fold 的 hyperopt 選出的參數,是實務上即將進入第 7 節 paper trading 階段的那組參數——DSR 要回答的問題是「這組即將被採用的參數,考慮到試出它的過程本身,有多大機率不是純粹運氣」,問的是「最終要用的這組」而非歷史上任何一組。**每個 fold 仍個別算一次 DSR 並在報告中全部列出**,作為穩健性診斷:若早期 fold 的 DSR 明顯低於或高於最終 fold,需在報告中標記可能的 regime 漂移或參數不穩定,呼應第 6 節的檢查精神,但不影響第 5 節正式判定,正式判定只看第 9 個 fold |
| **次檢定 OOS `PSR(SR*=0)`** | **9 個 fold 串接後的完整 OOS 序列** | 這正是 [`statistical-methodology.md`](./statistical-methodology.md) 2.5 節要求的「從未進入過 hyperopt 優化過程的樣本外交易報酬序列」——串接後樣本數遠大於任何單一 fold,是這條驗證管線裡樣本量最大、也最乾淨(完全沒有被任何一次參數搜尋碰過)的序列,理應是統計檢定力最強的一段,用它做次檢定合理 |
| **Reality Check / SPA 交叉檢查** | 取**第 9 個 fold** hyperopt 的前 K 名候選(建議 K=10)參數組,在**第 9 個 fold 的 OOS 視窗**上重新逐一 backtest,取得各自日報酬序列,套用 [`statistical-methodology.md`](./statistical-methodology.md) 2.4 節的 stationary bootstrap 流程 | 與 DSR 主檢定使用同一個 fold,確保這是「即將被採用的那組參數,相對於同一批候選,是否真的最好」的檢驗,語意上與主檢定的問題一致 |
| **CUSUM 結構性斷點檢查** | 9 個 fold 串接後的完整 OOS 序列 | [`statistical-methodology.md`](./statistical-methodology.md) 3.4 節原文即以「把每個 walk-forward fold 的 OOS 交易報酬,依時間順序串接成一條序列」描述,與此處設計一致 |
| **Kelly 倉位公式輸入(`SR_1`、`σ_1`)** | 9 個 fold 串接後的完整 OOS 序列,取 block bootstrap 信賴區間**下界**(悲觀估計) | 承 [`statistical-methodology.md`](./statistical-methodology.md) 5.3 節「必須使用第 2 節 walk-forward 的樣本外估計值...不得使用 hyperopt 優化過的樣本內數字」的既有硬性規定,串接後的完整 OOS 序列同樣是這條管線裡樣本數最大的乾淨 OOS 來源 |
| **蒙地卡羅回撤模擬** | 9 個 fold 串接後的完整 OOS 序列(含持倉天數)+ 上一列算出的最終 `risk_fraction` | 承 [`statistical-methodology.md`](./statistical-methodology.md) 6.2 節既有規定 |

**已知風險,誠實揭露:本策略交易頻率可能讓 `n_eff` 系統性貼近甚至低於 30 的硬性下限。** [`statistical-methodology.md`](./statistical-methodology.md) 3.1 節已預告「一個乾淨的趨勢市場一年可能才有個位數到十幾次有效突破訊號(單一標的)」,24 個月 IS 視窗、BTC+ETH 兩個標的、purge 後,單一 fold 的 IS 交易筆數落在數十筆量級是合理預期,Newey-West 校正後(若 `IF` 落在 1.5–2 的量級,參考 [`statistical-methodology.md`](./statistical-methodology.md) 4.2 節示例)`n_eff` 可能非常接近 30 的下限,甚至部分 fold 落到下限以下。**若第 9 個 fold(主檢定 DSR 使用的那個)的 `n_eff < 30`,依 [`statistical-methodology.md`](./statistical-methodology.md) 4.4 節明訂規則,DSR 檢定直接自動不通過,不論點估計數字多漂亮,不得靠提高信心水準彌補。** 這個風險是串接後 OOS 序列(次檢定用)樣本量遠大於任何單一 fold 的另一個重要原因——即使主檢定因單一 fold 樣本不足而不通過,次檢定仍能在較大樣本上給出獨立判斷,但**兩者都要通過**是 2.5 節的既有規定,樣本不足不能靠「用另一個檢定的較大樣本結果」互相掩護。若這個風險真的發生(多數/全部 fold 的 `n_eff` 貼近或低於 30),應在報告中明確標記為系統性樣本量不足,並依 [`statistical-methodology.md`](./statistical-methodology.md) 4.4 節既有建議,評估延長歷史區間或(在不違反 [`strategy-hypothesis.md`](./strategy-hypothesis.md) 「避免山寨幣 beta 污染」前提下)謹慎納入更多高流動性標的,而不是想辦法讓數字「看起來」通過。

### 4.5 參數穩定性與一致性檢查(對應第 7 節總表第 2 項)

- **一致性:** 統計 9 個 fold 中 OOS `SR_trade > 0` 的比例,須 `≥ 70%`(至少 7/9 個 fold)。
- **參數穩定性:** 9 個 fold hyperopt 選出的 N、X(以及 M、ATR 週期、k)分別計算變異係數 `CV = std/mean`,須 `< 0.5`。若某個 fold 明顯偏離其他 fold,在報告中標記並排查(是否對應到 CUSUM 偵測到的結構性斷點附近)。
- **Embargo 合規:** Pass B 使用的 `embargo_days` 須 `≥ 30` 且滿足 4.1 節校準後的公式值,這是方法論硬性規定,不通過視為整套驗證程序本身無效,不論績效數字多好看。

---

## 5. 通過/不通過判定 / Pass/Fail Determination

本節原樣照抄 [`statistical-methodology.md`](./statistical-methodology.md) 第 7 節「Phase 2 通過門檻總表」,並在每一項後面補上「本文件第幾節提供這一格的具體算法」,作為本文件執行結果的**唯一**終審依據——**這張表的門檻數字本身不可修改**,修改門檻本身屬於 [`risk-policy.md`](./risk-policy.md) 第 7 節「核心治理數字」變更範疇,須重新走 [`statistical-methodology.md`](./statistical-methodology.md) 相應章節的正式流程,不是本文件或任何一次回測報告可以自行調整的。

| # | 項目 | 通過門檻(照抄 statistical-methodology.md 第 7 節,不可修改) | 本文件對應算法出處 |
|---|---|---|---|
| 1 | 統計顯著性 | `DSR ≥ 0.95` **且** OOS `PSR(SR*=0) ≥ 0.95` **且** Reality Check p < 0.05,三者缺一不可 | 第 4.3 節步驟 4(DSR)、第 4.4 節表(次檢定、Reality Check 資料來源) |
| 2 | Walk-forward 切分 | 最少 5 個 fold(目標 8–10 個);≥70% fold 的 OOS Sharpe>0;參數穩定性 CV<0.5;embargo/purge 規則合規;CUSUM 偵測到斷點時斷點後子區間須獨立達標 | 第 1.3 節(9 個 fold)、第 4.1 節(embargo 校準)、第 4.3 節步驟 1(purge)、第 4.5 節(一致性/穩定性)、第 4.4 節(CUSUM) |
| 3 | 有效樣本數校正 | `n_eff ≥ 30`(硬性下限);`IF>3` 須標記聚類風險;block bootstrap 95% CI 下界 `SR_trade > 0` | 第 4.3 節步驟 3;第 4.4 節「已知風險」段落已預先揭露本策略在此項可能吃緊 |
| 4 | 部位大小 | `c ≤ 0.5`、預設 `c=0.25`;`risk_fraction = min(quarter-Kelly 換算值, Phase 4 硬上限)`;Full Kelly 一律不通過;`μ` 輸入須用 OOS + bootstrap 悲觀下界 | 第 4.4 節表(Kelly 輸入資料來源);硬上限數字本身取自 [`risk-policy.md`](./risk-policy.md) 1.5%/2.5%,本文件第 6 節驗證此上限是否仍是實際生效的約束 |
| 5 | 回撤機率估計 | `P(全年MDD>20%) ≤5%`;`P(全年MDD>15%) ≤20–25%`;預期月回撤暫停頻率 `≤2–3次/年`;`B≥5,000`(目標 10,000);須揭露信賴區間 | 第 4.4 節表(蒙地卡羅資料來源);具體模組見第 8 節 `analysis/drawdown_mc.py` |

**整體通過條件(照抄 [`statistical-methodology.md`](./statistical-methodology.md) 原文,不重寫):上述 5 項全部通過,backtest-analyst(本 Phase)才能簽核可以朝實盤方向推進;任一項不通過,策略退回參數重新設計或宣告本輪 v1 驗證失敗,不得跳過任一項直接進入後續實作。**

### 5.1 不通過時的具體處理路徑

依上述「不通過」的兩種結局,本文件給出更具體的判斷依據(仍是操作化,不是重新定義):

| 情境 | 判斷依據 | 處理路徑 |
|---|---|---|
| 只有 1–2 項未過,且未過的原因可歸因於**方法論執行細節**(例如某 fold 的資料缺口導致該 fold 被剔除、使 fold 數勉強低於門檻;或 embargo 校準後 fold 數略降) | 問題出在「這次跑法」而非「策略本身沒有 edge」 | 修正該執行細節(延長資料範圍、調整 embargo 校準邏輯)後,依第 4 節流程重新執行 Pass B,不需要回頭修改策略邏輯本身 |
| 多項未過,且未過的原因指向**策略經濟假說本身**(例如 OOS Sharpe 持續為負、勝率/賺賠比形狀與 [`strategy-hypothesis.md`](./strategy-hypothesis.md) 右偏肥尾假說明顯不符、`n_eff` 系統性低於下限反映交易頻率不足以支撐任何統計判斷) | 問題可能出在策略本身或標的範圍不足以產生可驗證的 edge | 退回 [`strategy-hypothesis.md`](./strategy-hypothesis.md)(Phase 1),依該文件既有的「backlog 候選策略」(均值回歸、輪動)重新評估,或依 [`statistical-methodology.md`](./statistical-methodology.md) 4.4 節建議謹慎評估納入更多高流動性標的;**不得在未回到 Phase 1 重新論證經濟假說的情況下,直接調整策略一的參數搜尋範圍試圖「調到通過」**——這正是 [`strategy-hypothesis.md`](./strategy-hypothesis.md) 已列為已知風險的過擬合模式 |
| `DSR` 通過但 `Reality Check` 未過,兩者結論矛盾 | 依 [`statistical-methodology.md`](./statistical-methodology.md) 2.5 節「以較保守者(Reality Check)為準」 | 視為未通過,並回頭檢查 hyperopt trial 是否過度相關(4.3 節的分群近似是否失真) |
| 全部 5 項通過 | — | 進入第 7 節 paper trading 銜接流程,**不代表直接進入實盤**,實盤資格是 Phase 9 `go-no-go-checklist.md` 的職責 |

**本文件明確拒絕的做法:** 任何形式的「調整參數搜尋範圍、更換標的、放寬統計門檻本身以求通過」都不是合法的處理路徑。若團隊(即專案負責人本人)判斷門檻本身不合理,必須走 [`statistical-methodology.md`](./statistical-methodology.md) 對應章節或 [`risk-policy.md`](./risk-policy.md) 第 7 節的正式變更流程,留下書面理由與日期,而不是在一次回測報告裡悄悄調整。

---

## 6. 驗證 risk-policy.md 假設的檢查清單 / Validating risk-policy.md's Assumptions Against Real Data

[`risk-policy.md`](./risk-policy.md) 第 8 節已明確承認:45 天 time-stop、ATR `k` 搜尋邊界 `[2.0, 4.0]`、第 1.2 節的連續虧損確定性推導表,**都是在真實回測資料出現前用理論/確定性推導得出的數字**,並明確要求 Phase 6(本文件)用真實資料驗證、若不成立則依該文件第 7 節變更流程處理。以下是具體檢查項目,**每一項都要有明確的「不match長什麼樣子」定義,不能只寫「檢查看看」**:

### 6.1 檢查一:45 天 time-stop 是否砍在不該砍的地方

**具體做法:** 用第 4 節 9 個 fold 串接後的完整 OOS 交易明細,計算:

1. 全部交易依出場原因(`exit_reason`)分類統計佔比:`stoploss`(ATR 停損)/ `exit_signal`(Donchian 下軌)/ `force_exit` 或等價的 time-stop 標記。
2. 對被 time-stop 出場的交易子集,額外檢查:出場當下的 `profit_ratio` 是正是負;若為正,進一步取出場後 **10 個交易日**(不影響回測本身,只是額外用同一份已下載的價格資料做事後檢視)的價格走勢,計算「若沒有 time-stop、假設繼續持有到下一個停損/Donchian 訊號」的**虛擬延伸報酬**(僅供診斷用,不得混入第 5 節任何正式統計量)。

**「不 match」長什麼樣子(具體判準):**

- 若 time-stop 出場筆數佔全部交易 **> 15–20%**,且其中相當比例(例如 > 50%)在出場當下仍是獲利狀態、且事後檢視延伸報酬顯示繼續上漲的比例明顯偏高——這代表 45 天正在**系統性**砍掉右尾獲利,與 [`risk-policy.md`](./risk-policy.md) 2.3 節自己承認的「刻意選擇的設計取捨」規模不符(該文件預期這是少數尾端情況,不是常態)。
- 若持倉天數的 95th 百分位(第 4.1 節校準已經算出這個數字)本身就已經逼近或超過 45 天,代表 45 天這個「1.5 倍緩衝」的假設本身站不住腳(緩衝空間被壓縮到接近 0)。

**若不 match:** 依 [`risk-policy.md`](./risk-policy.md) 第 7 節變更流程處理——time-stop 屬於該文件明確列出的「搜尋空間內的操作調參」還是「核心治理數字」需要先判斷:45 天本身不在 hyperopt 搜尋空間內(是寫死值),依該文件分類邏輯應視為需要走正式變更流程的參數,而非常態調參。

### 6.2 檢查二:ATR 停損倍數 `k` 是否頻繁貼著 `[2.0, 4.0]` 邊界

**具體做法:** 統計 9 個 fold 各自 hyperopt 選出的最佳 `k` 值,計算有多少比例的 fold,選出的 `k` 落在邊界附近(定義「附近」為距邊界 `≤ 0.2`,即 `k ≤ 2.2` 或 `k ≥ 3.8`)。

**「不 match」長什麼樣子(具體判準):** 若 **≥ 30%**(9 個 fold 中 3 個以上)的 fold 選出的 `k` 落在邊界附近,這是 [`risk-policy.md`](./risk-policy.md) 2.1 節自己已經預告的訊號:「若 Phase 6 backtest 用真實資料驗證發現...`k` 在邊界附近被 hyperopt 頻繁選中(代表邊界可能設得過緊)」。

**若不 match:** 依 [`risk-policy.md`](./risk-policy.md) 第 7 節,`k` 的搜尋邊界本身屬於「核心治理數字」(該文件第 7 節第 3 點明確列出「ATR `k` 的搜尋邊界本身」需要重新走驗證流程,區別於「邊界內由 hyperopt 選出的實際運行值」不需要),重新評估邊界寬窄需要重新跑受影響的第 5 節相關項目(至少是重新做一次 Pass B 的第 4 節流程,搭配調整後的邊界)。

### 6.3 檢查三:確定性連續虧損表 vs. 蒙地卡羅模擬,是否描繪出同一個風險輪廓

[`risk-policy.md`](./risk-policy.md) 1.2 節的連續虧損表(用 `risk_fraction=1.5%` 算出「6 筆連續虧損觸發月回撤熔斷、11 筆觸發 kill switch、15 筆觸發回撤硬上限」)是一個**刻意保守、忽略自相關的確定性模型**,該文件自己在 1.3 節已明確聲明「這不是對『實際會發生什麼』的機率估計」,真正的機率估計是第 4 節第 4.4 節餵給 `analysis/drawdown_mc.py` 的蒙地卡羅模擬(第 5 節第 5 項)。

**具體比對做法:**

1. 從蒙地卡羅模擬輸出中,取 `P(全年MDD>15%)` 與 `P(全年MDD>20%)` 的點估計與信賴區間(承第 5 節第 5 項)。
2. 額外分析蒙地卡羅模擬的 `B` 條偽路徑中,觸及 15%/20% 回撤的路徑,其回撤是由**幾筆連續虧損**造成的(取觸及回撤當下往前回溯的連續虧損筆數分布),與確定性表的「11 筆」「15 筆」直接比較。

**「不 match」長什麼樣子(具體判準):**

- 若蒙地卡羅模擬顯示的 `P(全年MDD>15%)` 或 `P(全年MDD>20%)` 已經超過第 5 節門檻(`≤20–25%`/`≤5%`)——這本身已經是第 5 節第 5 項的正式不通過,不需要额外比對就已經觸發第 5 節的處理路徑。
- **即使蒙地卡羅模擬在門檻內通過,但觸及回撤的路徑普遍只需要 3–5 筆連續虧損(遠少於確定性表暗示的 11–15 筆)**——這代表真實資料中的自相關(承 [`statistical-methodology.md`](./statistical-methodology.md) 4.1 節「虧損有群聚傾向」的既有警示)遠比確定性表假設的「獨立虧損」情境更嚴重,確定性表低估了聚類風險。這是一個**即使正式門檻通過、仍然值得標記**的落差,因為它代表 1.5% 這個硬上限實際承受的壓力比原本推導時想像的更集中。

**若不 match:** 依 [`risk-policy.md`](./risk-policy.md) 第 7 節、8 節既有指示——「Phase 6 的 Block Bootstrap 蒙地卡羅模擬結果出爐後,應與本文件的邊界互相對照:若模擬顯示這些邊界明顯過於保守或明顯不足,應依第 7 節流程調整,並在調整時同步更新第 1.2 節的推導表」。本文件在此把這句話具體化:**「明顯不足」的判準就是上面「3–5 筆而非 11–15 筆」這類具體落差**,不是憑印象判斷。

---

## 7. Paper Trading 銜接規則 / Handoff to Paper Trading

承 [`scope.md`](./scope.md) 第 2 節已定案「先用模擬資金(Testnet/paper trading),尚不投入真實資金」——本節定義「回測/walk-forward 通過」到「可以開始 Freqtrade dry-run(接真實行情、模擬下單)」之間的具體門檻,這是 Phase 9 `go-no-go-checklist.md`(真實資金)之前的**必要但不充分**中繼站。

### 7.1 進入 dry-run 的資格條件

1. **第 5 節「Phase 2 通過門檻總表」5 項全部通過**,且 Pass B(非 Pass A 校準輪)的結果,不得用校準輪的數字充數。
2. **第 6 節三項檢查清單全部執行完畢並有明確結論**(通過或已依 [`risk-policy.md`](./risk-policy.md) 第 7 節流程完成對應調整),不得帶著已知的假設落差直接跳過驗證進入下一階段。
3. `analysis/` 產出的完整報告(含第 3 節全部指標、第 5 節總表逐項數字、第 6 節檢查結論)已存在於 repo 中(留痕,呼應 [`risk-policy.md`](./risk-policy.md) 第 7 節「寫下來」的一貫紀律),供之後 Phase 9 稽核回顧。

### 7.2 Dry-run 觀察期長度與資料量門檻

**取「時間長度」與「累積交易筆數」兩個門檻,以先達成者較晚的為準(即兩者都要滿足)**:

| 門檻類型 | 具體數值 | 理由 |
|---|---|---|
| 最短時間 | **至少 3 個完整日曆月** | 承 [`risk-policy.md`](./risk-policy.md) 2.3 節 45 天 time-stop 的既有設計,3 個月足以讓多數已開倉部位走完至少一次完整的「進場→(停損/Donchian/time-stop)出場」週期,不會在觀察期還沒看到任何一筆完整交易生命週期時就倉促下結論 |
| 最少累積交易筆數(BTC+ETH 合計) | **至少 15 筆** | 呼應第 4.4 節已誠實揭露的「本策略交易頻率低、小樣本」風險——3 個月若剛好遇到盤整期完全沒有觸發訊號,時間到了但沒有任何交易可供比對,此時**不能**視為滿足資格,須延長觀察期直到累積到有意義的交易筆數,不能用「時間到了」單獨當作充分條件 |

### 7.3 需要比對的具體項目:paper trading 實際滑點/成交行為 vs. 回測成本模型假設

這是本節最核心的產出——第 2.2 節的 5 bps 滑價假設是**理論保守假設**,dry-run 是第一個能用真實市場資料驗證它的機會(Freqtrade dry-run 接真實行情報價,但不真的下單,仍能記錄「若真的下單,依當時真實訂單簿/報價,預期成交價會是多少」的估計,依 Freqtrade 版本能提供的精細度而定,若當時版本 dry-run 對滑價的模擬比 backtest 更貼近真實訂單簿,精確機制留待 Phase 10 依當時版本核實)。

**具體比對方法:**

1. 對 dry-run 期間每一筆(模擬)成交,計算實際記錄的成交價與訊號當根 K 棒的預期價格(通常是下一根開盤價,依策略隔根進場設計)之間的差距,換算成 bps。
2. 彙總這批「實際滑點」樣本,與回測階段假設的固定 5 bps/邊做比較(分布的中位數、以及尾端的極端值)。

**判準(具體數字,不模糊):**

- 若 dry-run 實際滑點的**中位數超過假設值的 2 倍(即 > 10 bps/邊)**,或**任一筆單邊滑點超過 30 bps**(代表偶發的極端流動性事件),視為第 2.2 節的成本模型假設不成立。
- **若不成立:回頭修正第 2.2 節的滑價假設值,用 dry-run 實測的分布重新設定 haircut 數字,並重跑一次第 4 節受影響的統計量計算(至少是第 5 節第 4、5 項,因為成本假設直接影響淨報酬進而影響 Kelly 輸入與蒙地卡羅模擬)**,才能進入 Phase 9 的 go/no-go 檢查——**不得帶著已知失真的成本模型直接進入真實資金決策**,這正是 [`stable-profitability-roadmap.md`](./stable-profitability-roadmap.md) 中 backtest-analyst 角色「Paper Trading 銜接層」原本提出需求的具體落實。

3. **額外的訊號一致性檢查(抓實作 bug,不只是成本模型落差):** 對 dry-run 期間產生的每一筆訊號,事後用同一段時間的歷史資料重跑一次 backtest,確認 dry-run 實際觸發的訊號(進場日期、方向)與「若當初直接用這段期間做 backtest」會產生的訊號完全一致。若出現不一致(例如 dry-run 少觸發或多觸發了某次訊號),這通常代表策略程式碼在 live/dry-run 執行路徑與 backtest 執行路徑之間有實作差異(儘管 [`architecture-spec.md`](./architecture-spec.md) 0.3 節已確認 Freqtrade 架構上三種模式共用同一套 `IStrategy` 介面,理論上不應該發生,但這是**驗證**這個架構保證在本專案實際程式碼中確實成立的唯一方法),須視為 Phase 10 程式碼層級的 bug 立即修正,不歸類為「成本模型」問題。

### 7.4 Dry-run 通過後的下一步

Dry-run 觀察期滿足 7.2 節門檻、7.3 節比對無重大落差(或已修正並重新驗證)後,才具備進入 [`go-no-go-checklist.md`](./go-no-go-checklist.md)(Phase 9)最終真實資金決策的資格。**本文件不決定 Phase 9 的其餘檢查項目**(那是全體角色會審的職責),只確認「回測/walk-forward → dry-run → 可以開始評估 Phase 9」這條鏈路上,backtest-analyst 這一段的具體離開條件。

---

## 8. `analysis/` 目錄的模組結構 / Module Map

[`architecture-spec.md`](./architecture-spec.md) 2.2 節已保留 repo 根目錄下的 `analysis/` 目錄,說明它是「獨立於 Freqtrade 執行時期的離線分析工具,讀取 Freqtrade 產出的靜態檔案」,但未填入內容——本節提供 Phase 10 實作者可以直接依循的模組地圖。**這是規格層級的描述,不是程式碼**,每個模組列出職責、輸入、輸出、對應 [`statistical-methodology.md`](./statistical-methodology.md) 的章節、以及建議使用的 Python 套件(承 [`statistical-methodology.md`](./statistical-methodology.md) 第 0 節已建議的工具鏈:`numpy`/`pandas`/`scipy.stats`/`statsmodels`/`arch.bootstrap`)。

| 模組 | 職責 | 消費(輸入) | 產出(輸出) | 對應章節 | 主要套件 |
|---|---|---|---|---|---|
| `analysis/data_loader.py` | 讀取 Freqtrade 原始匯出檔,標準化成統一格式的 `pandas.DataFrame` | `user_data/backtest_results/*.json`、`user_data/hyperopt_results/*` | 標準化交易明細 DataFrame(`pair`、`open_date`、`close_date`、`open_rate`、`close_rate`、`profit_ratio`、`profit_abs`、`exit_reason`、`stake_amount`);標準化 epoch 紀錄 DataFrame(`epoch_id`、參數欄位、`SR_trade`、`loss`) | 全篇的資料入口 | `pandas`、`pyarrow`(讀 feather 格式的原始 K 線,若後續模組需要重建帶日曆刻度的權益曲線) |
| `analysis/cost_model.py` | 套用第 2.2 節滑價 haircut,產出淨報酬版本的交易明細 | `data_loader.py` 輸出的交易明細 | 調整後交易明細(`open_rate`/`close_rate`/`profit_ratio` 已扣除滑價) | 第 2.2 節 | `pandas`、`numpy` |
| `analysis/walk_forward.py` | 產生 fold 邊界(1.3 節公式)、實作 purge 過濾邏輯(供 hyperopt 自訂 loss function 呼叫)、CUSUM 結構性斷點檢定(3.4 節) | 資料下載範圍、`embargo_days`(來自 4.1 節校準結果) | fold 邊界表(IS/embargo/OOS 起訖日期)、purge 後的 IS 交易子集、CUSUM 統計量與斷點位置 | 第 1.3 節、第 4.1 節、[`statistical-methodology.md`](./statistical-methodology.md) 3.3/3.4 節 | `numpy`、`pandas` |
| `analysis/sample_size.py` | Newey-West HAC 校正、`n_eff`/`IF` 計算、block bootstrap 交叉驗證 | 交易報酬序列(可為單一 fold IS,或串接後 OOS) | `n_eff`、`IF`、ACF/lag 選擇診斷、block bootstrap 95% CI | [`statistical-methodology.md`](./statistical-methodology.md) 第 4 節 | `numpy`、`statsmodels`(`acf`/自行實作 Newey-West 估計式)、`arch.bootstrap.StationaryBootstrap` |
| `analysis/significance.py` | PSR/DSR 公式(2.2 節)、`SR0` 極值分布公式、trial 相關性分群(2.3 節)、White's Reality Check / SPA(2.4 節) | `SR_hat`、`n_eff`、`γ3`/`γ4`(由交易報酬序列算出)、epoch 紀錄的 `N`/`σ_SR`、top-K 候選的日報酬序列 | `DSR`、OOS `PSR(SR*=0)`、Reality Check p-value、分群後有效 `N'` | [`statistical-methodology.md`](./statistical-methodology.md) 第 2 節 | `scipy.stats`(`norm.ppf`/`norm.cdf`/`skew`/`kurtosis`,**不可用手算近似值,statistical-methodology.md 2.2 節已明訂**)、`numpy` |
| `analysis/position_sizing_check.py` | Fractional Kelly `f*`/`c·f*` 計算(5.2 節),與 [`risk-policy.md`](./risk-policy.md) 硬上限比較、確認 `min()` 邏輯與最終生效值 | 串接 OOS 序列的 `SR_1`/`σ_1`(bootstrap 悲觀下界)、`k`、`ATR%`、[`risk-policy.md`](./risk-policy.md) 硬上限常數 | `f*`、`c·f*`、換算後 `risk_fraction`、與硬上限比較後的最終生效值,四個數字並列(承 5.3 節「任何一步的取捨都要看得見計算過程」要求) | [`statistical-methodology.md`](./statistical-methodology.md) 第 5 節、[`risk-policy.md`](./risk-policy.md) 第 1 節 | `numpy` |
| `analysis/drawdown_mc.py` | Block bootstrap 蒙地卡羅回撤模擬(6.2 節)、帶日曆刻度權益曲線重建、`P(MDD>X%)` 與信賴區間 | 串接 OOS 序列(報酬 + 持倉天數)、`position_sizing_check.py` 算出的最終 `risk_fraction` | MDD/月度回撤分布、`P(全年MDD>15%/20%)` 點估計與信賴區間、預期月回撤暫停頻率 | [`statistical-methodology.md`](./statistical-methodology.md) 第 6 節 | `arch.bootstrap.StationaryBootstrap`、`numpy`(向量化 running max drawdown) |
| `analysis/risk_policy_validation.py` | 第 6 節三項檢查清單(45 天 time-stop、`k` 邊界、確定性表 vs. 蒙地卡羅比對) | 串接 OOS 交易明細、9 個 fold 的 `k` 選擇紀錄、`drawdown_mc.py` 輸出 | 三項檢查各自的判準結果(通過/標記/建議走 [`risk-policy.md`](./risk-policy.md) 第 7 節變更流程) | 第 6 節 | `pandas`、`numpy` |
| `analysis/hyperopt_loss.py`(對應 `user_data/hyperopts/`) | 自訂 `PurgedTradeSharpeLoss`(`IHyperOptLoss` 子類別),purge 過濾 + 逐筆交易 Sharpe 計算 | hyperopt 每次評估的 backtest 結果 DataFrame、`embargo_days`、IS 視窗結束日期 | 供 `freqtrade hyperopt --hyperopt-loss` 直接引用的 loss 值 | 第 4.3 節 | Freqtrade `IHyperOptLoss` 介面、`numpy` |
| `analysis/report.py` | 端到端編排前述所有模組,依第 4 節步驟順序跑完整條管線(單一 fold 或全部 9 個 fold),產出第 5 節總表填好數字的最終報告 | 前述所有模組輸出 | 結構化報告(建議 Markdown 或 HTML,含第 3 節全部指標、第 5 節總表逐項數字與通過/不通過標記、第 6 節檢查結論) | 全篇 | 前述全部套件;報告排版可選配 `matplotlib`/`plotly`(僅供視覺化,非統計核心) |

**模組間的資料流,呼應 0.1 節「單一驗證管線」的定位:** `data_loader.py` → `cost_model.py` → `walk_forward.py`(產生 fold 邊界、驅動 `hyperopt_loss.py` 的 purge 邏輯)→ 逐 fold 呼叫 `sample_size.py` + `significance.py` → 全部 fold 完成後串接,再次呼叫 `sample_size.py`(串接序列)→ `significance.py`(次檢定)→ `position_sizing_check.py` → `drawdown_mc.py` → `risk_policy_validation.py` → `report.py` 彙總輸出。任何一個模組單獨拿掉,下游模組都拿不到合法輸入——這與 [`statistical-methodology.md`](./statistical-methodology.md) 第 0 節「任何一節單獨拿掉,其他節的結論都會失真」的既有警告完全對應到程式碼模組層級。

---

## 9. 已知限制與交棒事項 / Known Limitations & Handoff

- **本文件的日期表(1.3 節)會隨時間過期。** 每次重跑第 4 節流程前,必須重新產生 fold 邊界表(依 1.2 節固定的 anchor 2020-01-01、24mo/6mo/6mo 規則,往前推算到當下可用的最新完整 OOS 視窗),不能沿用本文件寫作當下(2026-08-07)算出的固定表格,那只是首次執行時的具體示範。
- **Embargo 校準(4.1 節)的兩輪設計,是本文件對 [`statistical-methodology.md`](./statistical-methodology.md) 既有雞生蛋問題給出的具體解法**,但 Pass A 用 200 epochs 這個縮減值只是加速校準輪的建議,不是嚴謹推導出的數字,若 Phase 10 實作時發現 200 epochs 選出的持倉時間分布與完整 1,000 epochs 跑出的分布有系統性差異,應該提高 Pass A 的 epochs 數,不要為了省時間犧牲校準品質。
- **hyperopt trial 相關性分群(4.3 節)在 Freqtrade 版本不支援重建每個 epoch 完整日報酬序列時的近似做法**(只對前 20–50 名候選重新 backtest),是本文件在方法論與工程可行性之間的具體取捨,[`statistical-methodology.md`](./statistical-methodology.md) 8 節已預告這是「最需要 Phase 6 落地時特別小心處理的一步」——若 Phase 10 實作時發現 Freqtrade 版本其實可以完整取得全部 1,000 個 epoch 的日報酬序列,應該優先採用完整分群,不要預設一定要走近似路徑。
- **本文件第 2.2 節的 5 bps 滑價假設,其存在理由是「有一個保守但誠實標註為假設的數字」,不是精確估計**——第 7 節已經設計了用 dry-run 實測校正的機制,但在那之前(即整個第 4、5、6 節的回測/walk-forward 階段),所有績效指標都是在這個假設之下算出的,報告時必須明確標註這一點,不能讓讀者誤以為第 5 節通過的數字已經是「真實成本下」的最終結果——**真正的驗證要等第 7 節 dry-run 階段才完成**。
- **`analysis/hyperopt_loss.py` 的自訂 loss function 設計(4.3 節)预设了 Freqtrade 的 `IHyperOptLoss` 介面允許存取足夠的 trade-level 資訊做 purge 過濾**——這個介面的確切簽章(能拿到哪些欄位、`min_date`/`max_date` 参数語意)依 [`architecture-spec.md`](./architecture-spec.md) 0.2 節既有原則,屬於「確定存在、但精確語法可能隨版本變動」的細節,留給 Phase 10 依當時版本文件核實,不影響本文件的設計邏輯本身。
- **第 6 節三項檢查清單的判準數字(15–20% 出場佔比、30% fold 貼邊界、3–5 筆 vs. 11–15 筆連續虧損)是本文件為了讓「不 match 長什麼樣子」具體可執行而給出的操作性定義**,不是 [`risk-policy.md`](./risk-policy.md) 或 [`statistical-methodology.md`](./statistical-methodology.md) 已經明文規定的門檻——若專案負責人審閱後認為這些具體數字需要調整,屬於本文件內部的操作細節修訂,不需要動用 [`risk-policy.md`](./risk-policy.md) 第 7 節「核心治理數字」等級的正式變更流程,但建議修訂時仍留下理由紀錄。

**下一步:** 本文件經專案負責人審閱確認後,Phase 6 結案。可交棒 Phase 7([`execution-spec.md`](./execution-spec.md),尚未撰寫——execution-engineer 職責,定案冪等下單、rate limit 退避、WebSocket 重連等執行層規格,與本文件的回測驗證管線平行、不互相依賴)。本文件第 4–8 節定義的完整流程,是 Phase 9([`go-no-go-checklist.md`](./go-no-go-checklist.md))檢查清單中「回測框架的成本模型與通過門檻已定案」這一項的具體實作依據;第 6 節的驗證結論若導致 [`risk-policy.md`](./risk-policy.md) 任何數字變更,須在進入 Phase 9 之前完成該文件第 7 節的正式變更流程並更新該文件本身,不得讓 Phase 9 的檢查清單對照到一份已經過時的風控政策文件。

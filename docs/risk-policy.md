# Phase 4 — 風控政策 / Risk Policy

> 狀態:**🟡 草稿完成,待專案負責人審閱後確認 / DRAFTED — pending owner review.**
> 負責角色:risk-manager
> 範圍限制(承 [`strategy-hypothesis.md`](./strategy-hypothesis.md) 的決定):僅針對 **策略一:Regime-Filtered Momentum Breakout**(Donchian 突破 + 成交量確認 + ATR 移動停損),BTC/ETH 現貨、波段頻率。
> 前提假設(承 [`scope.md`](./scope.md)):最大回撤上限 15–20%、月回撤 8% 觸發強制停機複查、單人專案且負責人本人持有 kill switch 拍板權、觸發後強制 ~24 小時冷靜期、風控參數不得在虧損當下臨時調整。
> 前提假設(承 [`statistical-methodology.md`](./statistical-methodology.md) 第 5 節):部位大小公式已定案為 `position_size = (risk_fraction × Equity) / (k × ATR)`,`risk_fraction = min(quarter-Kelly 換算值, Phase 4 硬上限)`,Kelly 分數 `c = 0.25`(預設)、`c ≤ 0.5`(絕對上限)已由 Phase 2 拍板,**本文件不重新決定 `c`,只決定 Phase 4 的硬上限數字**。
> 前提假設(承 [`architecture-spec.md`](./architecture-spec.md)):以 Freqtrade Protections(`StoplossGuard`/`MaxDrawdown`/`CooldownPeriod`)作為框架層級、不可被策略程式碼繞過的熔斷機制;`custom_stoploss`/`custom_stake_amount` 是 ATR 停損與 Kelly 倉位公式的接線點;Kelly 分數 `c` 與本文件的 `risk_fraction` 硬上限**絕不可作為 hyperopt 可調參數**,一律寫死於程式碼並經 git commit 留痕;`MaxDrawdown` Protection 的 `stop_duration` 須對齊 Phase 0 的 24 小時冷靜期,精確計時單位待本文件核實。

本文件是 [`architecture-spec.md`](./architecture-spec.md) 第 3.2、4.5 節與 [`development-plan.md`](./development-plan.md) Phase 4 要求清單留白的具體數字定案,也是 [`statistical-methodology.md`](./statistical-methodology.md) 第 5 節明確交棒給 risk-manager 的決定(該節已算出 quarter-Kelly 換算出的單筆風險約 7%,並言明「Phase 4 risk-manager 仍握有最終決定更保守數字的權力」)。**本文件產出後,任何後續程式碼撰寫階段都不應該再回頭問「這個數字應該是多少」——這是規格書,不是討論稿。**

---

## 0. 數字總覽表(供實作階段直接查閱)

| 類別 | 參數 | 數值 |
|---|---|---|
| 統一 K 棒單位 | timeframe | **1d(日 K)**,呼應 [`strategy-hypothesis.md`](./strategy-hypothesis.md)「日線收盤價突破」的既有決定,本文件僅將其明確化為所有風控參數的計時單位 |
| 單筆風險上限 | `risk_fraction` 硬上限 | **1.5%**(權益);最終取 `min(0.25 × f* × k × ATR%, 1.5%)` |
| ATR 停損倍數 | `k` 的 hyperopt 搜尋邊界 | **2.0 – 4.0**(預設起點 3.0) |
| 停利 | 固定 take-profit | **不設**,唯一出場路徑為 ATR 移動停損 / Donchian 下軌 / time-stop |
| 最大持倉時間 | time-stop | **45 個日曆天** |
| `custom_stoploss` 失效後備值 | Freqtrade `stoploss` class attribute | **-25%**(fallback,非正常路徑) |
| 每日虧損熔斷 | 門檻 / 行為 | **-4%**(當日,UTC 曆日,已實現+未實現)→ 僅暫停新倉 3 天,既有部位不強制平倉 |
| 月回撤熔斷(Phase 0 既有規則) | `max_allowed_drawdown` / 行為 | **8%**,`lookback_period_candles=30`、`trade_limit=2`、`stop_duration ≥24h` → 暫停新倉 + 強制 ≥24h 冷靜期 + 人工覆核後才可恢復,既有部位不強制平倉 |
| Kill switch | 帳戶回撤門檻 | **-15%**(Phase 0 15–20% 區間下緣) |
| Kill switch | `max_allowed_drawdown` / lookback / trade_limit / stop_duration | `0.15` / `lookback_period_candles=365`(或版本上限)/ `2` / `≥24h` |
| Kill switch | 觸發行為 | 自動鎖新倉(框架層)+ **人工執行 forceexit-all → stop runbook 強制平倉**+ 強制 ≥24h 冷靜期 + 人工覆核 |
| StoplossGuard | `trade_limit` / `lookback_period_candles` / `stop_duration_candles` | `2` / `10` / `5`(皆以 1d K棒計) |
| CooldownPeriod | `stop_duration_candles` | `2`(1d K棒) |
| 總曝險 | 單筆 / 合併 `risk_fraction` 上限 | 單筆 1.5% / **合併(BTC+ETH 同時持倉)2.5%**(相對 naive 加總 3.0% 打折約 17%) |
| 總曝險 | 單筆 / 合併名目部位(notional)上限 | 單筆 **≤50% 權益** / 合併 **≤80% 權益**(保留 ≥20% 現金緩衝) |

以下各節逐一給出推導過程與理由,不是憑空拍板。

---

## 1. 單筆交易風險上限 / Per-Trade Risk Cap

### 1.1 張力的來源

[`statistical-methodology.md`](./statistical-methodology.md) 第 5.2 節用一組寫實假設(`SR_1=1.1`、`σ_1=35%`、`k=3`、`ATR%=3%`)算出:即使套用 quarter-Kelly(`c=0.25`),換算出的單筆風險 `risk_fraction ≈ 7.07%`。該文件明確指出這「遠高於一般建議的 1–2%」,並將**設定實際硬上限**的責任交給本文件。而我在 [`stable-profitability-roadmap.md`](./stable-profitability-roadmap.md) 中原本的立場是「每筆風險固定為淨值 1–2%」。這兩個數字差距近 5 倍,不能都用,必須有一個實際生效的決定。

### 1.2 推導:硬上限與 Phase 0 回撤上限的關係

**方法:** 用一個刻意保守、不依賴機率分布假設的模型——「連續 N 筆交易都恰好虧損 `risk_fraction`(等於觸及停損),不考慮期間任何獲利交易的緩衝」——反推需要幾次連續虧損才會撞上 Phase 0 的三條回撤紅線(月回撤熔斷 8%、kill switch 下緣 15%、kill switch 上緣/硬上限 20%)。由於部位大小是依「當下權益」動態換算(呼應 [`statistical-methodology.md`](./statistical-methodology.md) 的 `position_size` 公式),連續虧損是**複利式**縮水,即權益倍數 `= (1 - risk_fraction)^N`,反推:

```
N = ceil( ln(1 - D) / ln(1 - risk_fraction) )
```

`D` 為回撤幅度,`N` 為所需連續虧損筆數。代入不同 `risk_fraction` 候選值:

| 單筆風險 `risk_fraction` | 連續虧損 N 筆撞上月回撤熔斷(8%) | 連續虧損 N 筆撞上 kill switch 下緣(15%) | 連續虧損 N 筆撞上回撤硬上限(20%) |
|---|---|---|---|
| 1.0% | 9 筆 | 17 筆 | 23 筆 |
| **1.5%** | **6 筆** | **11 筆** | **15 筆** |
| 2.0% | 5 筆 | 9 筆 | 12 筆 |
| 3.0% | 3 筆 | 6 筆 | 8 筆 |
| 7.0%(quarter-Kelly 原始換算值) | 2 筆 | 3 筆 | 4 筆 |

**這張表本身就是拒絕 7% 的理由:** 在 `risk_fraction=7%` 時,只要連續 4 筆停損就會撞穿 Phase 0 的回撤硬上限(20%),連續 2 筆就觸發月回撤熔斷。策略一是趨勢突破,已知失效場景是「橫盤震盪期假突破率高」([`strategy-hypothesis.md`](./strategy-hypothesis.md) 已明確標記),連續 2–4 筆假突破停損在盤整市完全是正常事件,不是黑天鵝——用 7% 下注等於把「策略正常會遇到的失效場景」直接兌換成「觸發 kill switch」,這不是穩健的風控設計。

**為什麼不是更低的 1%:** 突破策略的報酬分布右偏、肥尾(多數小額停損 + 少數大額趨勢單,[`statistical-methodology.md`](./statistical-methodology.md) 2.2 節已定性),意味著**勝率通常低於 50%**(常見趨勢突破系統勝率落在 35–45% 區間)。用 35% 勝率粗略估計(忽略第 4 節已指出的正自相關,實際連續虧損機率只會更高不會更低,因為虧損有群聚傾向):`P(連續 6 筆虧損) ≈ 0.65^6 ≈ 7.5%`——這不是需要上百年才會遇到一次的尾端事件,是策略生命週期中大概率會遇到的正常波動。若把 `risk_fraction` 訂在 1%,`N=9` 才觸發月回撤熔斷,會讓月回撤熔斷這個 Phase 0 既有機制在實務上很難被觸發到(等同於把它架空),也代表在真正需要及早停下來複查的時候,系統會讓虧損跑得比應有的更久。

### 1.3 決定:`risk_fraction` 硬上限 = **1.5%**

這個數字**維持在我原本 1–2% 的立場範圍內**,落在區間偏上緣,而非另立新數字——上表顯示 1.5% 讓「6 筆連續虧損觸發月回撤熔斷」「11 筆觸發 kill switch 下緣」「15 筆觸發回撤硬上限」,三個數字都對應到「明顯異常的連續失效」而非「策略設計本身就預期會發生的正常變動」,同時月回撤熔斷仍然是一個會被實際觸發到、發揮作用的機制(不像 1% 那樣形同虛設)。

**明確拒絕 quarter-Kelly 換算值(≈7%)作為實際採用值**,理由已在 1.2 節說明:那個數字是用「edge 已知」的假設反推出的理論上界,不是可以直接拿來下注的操作數字——[`statistical-methodology.md`](./statistical-methodology.md) 自己也用「主觀上以為在用已驗證的正 edge 下注,數學上實際卻在虧錢的區間裡加碼」描述了這個陷阱,套用到本策略「多數小額停損」的已知特性,問題會更明顯。

**重要聲明:上述 N 筆連續虧損模型是刻意保守的確定性推導(每筆恰好虧損 `risk_fraction`、不考慮任何獲利交易的緩衝、不假設任何機率分布),目的是在 Phase 6 有真實回測數據之前提供一個可推導、可辯護的邊界,不是對「實際會發生什麼」的機率估計。** 真正的機率估計是 [`statistical-methodology.md`](./statistical-methodology.md) 第 6 節的 Block Bootstrap 蒙地卡羅模擬(`P(全年MDD>15%) ≤20–25%` 等門檻),兩者互補而非互相替代——本節提供「現在就能拍板的保守邊界」,第 6 節模擬留待 Phase 6 用真實交易數據驗證這個邊界是否足夠寬鬆到不會讓策略窒息、也是否足夠保守到符合機率門檻。

### 1.4 最終公式(唯一生效版本)

```
risk_fraction = min( 0.25 × f* × k × ATR%,  1.5% )

其中:
  f* = SR_1 / σ_1     （SR_1、σ_1 須用 OOS 估計值 + block bootstrap 悲觀下界,不得用 in-sample 優化值 —— statistical-methodology.md 5.3 節既有規定)
  k  = 本文件第 2.1 節 hyperopt 搜尋邊界內的實際 k 值
  ATR% = ATR_in_quote_currency / entry_price

position_size(base currency) = (risk_fraction × Equity) / (k × ATR_in_quote_currency)
```

以 [`statistical-methodology.md`](./statistical-methodology.md) 5.2 節的示範數字重算:`0.25 × 3.14 × 3 × 3% ≈ 7.07%`,`min(7.07%, 1.5%) = 1.5%`——**在絕大多數可預期的參數情境下,1.5% 硬上限才是實際生效的約束**,quarter-Kelly 換算值的角色降級為「上界檢查」,與 [`statistical-methodology.md`](./statistical-methodology.md) 5.2 節末段的定位完全一致。

`risk_fraction = 1.5%` 與 Kelly 分數 `c=0.25`、`c≤0.5` 一樣,依 [`architecture-spec.md`](./architecture-spec.md) 3.2 節的既有規定,**必須寫死在策略程式碼中,絕不可作為 hyperopt 可調參數**。

---

## 2. v1 策略(策略一)的強制出場規則 / Mandatory Exit Rules

呼應 [`stable-profitability-roadmap.md`](./stable-profitability-roadmap.md) 中我原本的立場:「沒有第 3、5 點(每日熔斷、kill switch),其他都只是裝飾——沒有停損機制的策略遲早會遇到黑天鵝把獲利全部吐回去」,以及「每個策略必須有完整出場路徑(停損+停利+time-stop)」。本節逐項定案策略一的三個出場路徑,並在第 2.2 節說明為何本策略最終**不採用**固定停利。

### 2.1 停損:ATR 移動停損倍數 `k`

[`architecture-spec.md`](./architecture-spec.md) 3.2 節已把 `k`(ATR 停損倍數)分類為**訊號邏輯參數、hyperopt 可調**(不同於 `risk_fraction`/Kelly `c` 這類治理硬上限)。本文件的職責因此不是給出單一 `k` 值,而是**框定 Phase 6 hyperopt 搜尋 `k` 的合理邊界**,避免搜尋跑出風控上不合理的區間:

- **下界 2.0**:`k` 小於 2 倍 ATR,停損距離窄於一般日內波動的正常擺盪,對趨勢跟隨策略而言容易被正常雜訊洗出場(whipsaw),讓策略在真正的趨勢啟動前就被震盪停損出局——這是把「風控」做過頭、反而侵蝕策略本身經濟假說(捕捉趨勢延續)的情況。
- **上界 4.0**:`k` 大於 4 倍 ATR,單筆停損距離過寬,除了讓策略在趨勢已經反轉後才出場(回吐過多既有獲利)之外,也讓 `stoploss_on_exchange`([`architecture-spec.md`](./architecture-spec.md) 3.3 節建議啟用)掛出的停損單距離現價過遠,實質上長時間處於「近乎無停損保護」的狀態,與 `populate_exit_trend` 既有的 Donchian 下軌「軟」出場訊號重疊度也會降低,削弱雙重出場路徑的保護效果。
- **建議起點 3.0**:對齊 [`statistical-methodology.md`](./statistical-methodology.md) 5.2 節數值示範採用的 `k=3`,作為 Phase 6 hyperopt 的中心起點,非強制預設值。

**重要澄清:放寬或收窄 `k` 不會改變單筆實際承擔的風險(`risk_fraction`)**,因為 `position_size = (risk_fraction × Equity) / (k × ATR)` 的設計就是讓 `k` 變動時用調整下單量來抵銷,使 `$` 風險維持在 `risk_fraction` 訂下的水準不變——`k` 影響的是**停損距離**與**部位名目大小**,不是風險上限本身。這正是為何 `k` 可以放給 hyperopt 在合理邊界內自由搜尋,而 `risk_fraction`/`c` 不行:前者是「用什麼距離抓出場點」的訊號選擇問題,後者是「願意承擔多少 `$` 風險」的治理決策,兩者性質不同。

**`custom_stoploss` 失效後備值:** [`architecture-spec.md`](./architecture-spec.md) 3.1 節已指出即使使用 `custom_stoploss`,Freqtrade 仍要求策略類別保留一個保守的 `stoploss` class attribute 作為最後防線(當 `custom_stoploss` 因程式錯誤或資料缺失而未能正常回傳時的 fallback)。本文件訂定此值為 **`-25%`**——這個數字**不是**正常出場路徑會用到的數字(正常路徑下 `k×ATR%` 在典型波動度下遠小於 25%),而是一個「理論上不該被觸及,但萬一 `custom_stoploss` 完全失效時,仍能把單筆最大損失鎖在可接受範圍內」的災難後備值,寬鬆到不會與正常的 ATR 停損邏輯打架,但不寬鬆到讓部位在邏輯真的失效時毫無下界。

### 2.2 停利:v1 不設固定 take-profit

**決定:不設固定百分比停利,唯一的獲利了結路徑是 ATR 移動停損本身(價格反轉時停損位置逼近成交價、自然鎖利)+ Donchian 下軌「軟」出場訊號。**

**理由:**

1. [`statistical-methodology.md`](./statistical-methodology.md) 2.2 節已定性,本策略的交易報酬分布**右偏、肥尾**——「多數小額停損 + 少數大額趨勢單」。策略的整體 edge 高度依賴那少數幾筆能捕捉到完整大趨勢的交易。固定停利(例如「獲利達 X% 即出場」)會**系統性地砍掉右尾**,正好切掉這個策略賴以獲利的那一小部分交易——用固定停利等於主動放棄策略的經濟假說。
2. [`strategy-hypothesis.md`](./strategy-hypothesis.md) 的經濟機制論證是「趨勢一旦形成常因跟風與敘事發酵而延續」,這個機制本身就是在說「不要提早獲利了結」,固定停利與策略的經濟邏輯直接矛盾。
3. 趨勢跟隨系統用移動停損取代固定停利是業界標準做法(讓停損距離跟著有利價格移動、逐步鎖利,同時保留繼續參與趨勢的空間),不是本文件發明的新概念,是把既有共識具體落地到本策略。

**Freqtrade 特有的實作陷阱(需明確記錄,避免實作階段誤踩):** Freqtrade 設定檔的 `minimal_roi` 若沿用官方範例的預設值(常見範例如 `{"0": 0.04, ...}`,即「開倉後立即獲利 4% 就出場」),會在我們完全沒有察覺的情況下**變相植入一個固定停利**,直接推翻本節的決定。本文件要求:**`minimal_roi` 必須明確設為實質停用**(例如 `{"0": 10}`,即需要 1000% 獲利才會觸發,等同於永遠不會由 ROI 機制主動出場),把出場路徑完全交給 `custom_stoploss`(ATR 移動停損)、`populate_exit_trend`(Donchian 下軌)、與第 2.3 節的 time-stop 三者,不讓一個常被忽略的預設設定值悄悄違反本節的策略性決定。

### 2.3 最大持倉時間(Time-Stop)

**決定:45 個日曆天,無條件強制平倉(不論當下損益)。**

**理由:**

- [`statistical-methodology.md`](./statistical-methodology.md) 3.3 節(purge/embargo 規則的推導)已給出本策略持倉時間的既有估計:「持倉中位數約 1–3 週,但尾部可能拉長到 1 個月」,並以此設定 `embargo_days` 下限為 30 天。Time-stop 的角色是「防止部位橫盤漂移、資金被無限期占用」的最後防線,理應設在比這個既有 95th 百分位估計(約 30 天)更寬鬆的位置,才不會誤傷仍在正常延續、只是還沒被停損或 Donchian 下軌打到的合理趨勢單——45 天約為 30 天估計值的 1.5 倍,提供合理緩衝但仍是一條明確、有限的界線,而非「TBD」。
- 45 天大幅寬於 Phase 0 對「波段」的原始描述(數小時至數日),但這是**刻意選擇更精確、專屬於本策略的既有估計**([`statistical-methodology.md`](./statistical-methodology.md) 已在 Phase 2 用更細緻的分析給出持倉週期數字),優先於 Phase 0 較粗略的分類標籤——Phase 0 的「波段」一詞本身涵蓋數小時到數週的範圍,用來與日內高頻、長期投資做區隔,不是對本策略持倉天數的精確約束。
- **設計取捨的誠實揭露:** 一個真正強勁、持續創新高的趨勢理論上可能被 45 天的 time-stop 提前腰斬,犧牲部分右尾報酬。但既有估計顯示這類案例是尾端情況(95th 百分位約 30 天),用 45 天(明顯寬於此)出場的情況預期是少數;若 Phase 6 backtest 用真實資料驗證發現 time-stop 系統性地打斷仍在獲利延續的趨勢單(例如相當比例被 time-stop 出場的交易在出場當下仍是獲利狀態且持續一段時間後仍在上漲),應依第 7 節的變更流程重新評估這個數字,而非現在就先放寬到可能讓部位無限期占用資金的程度。

實作接線點:`custom_exit`(或等價的 `custom_stoploss`/`populate_exit_trend` 組合)判斷 `current_time - trade.open_date_utc ≥ 45 天` 時無條件出場,與停損/Donchian 出場路徑並存,任一先觸發即出場。

---

## 3. 每日虧損熔斷 / Daily Loss Circuit Breaker

### 3.1 門檻推導

v1 僅交易 BTC/ETH 兩個現貨對,單筆風險上限已定為 1.5%(第 1 節)。**最直接可預期的「壞日子」情境是:BTC 與 ETH 在同一天都觸發進場訊號、且同一天都被停損出場**——[`strategy-hypothesis.md`](./strategy-hypothesis.md) 已明確標記三個候選策略「都高度依賴 BTC 大盤 regime」,BTC/ETH 同期觸發訊號、同期被同一波逆勢行情打到停損,不是小機率的巧合,是這個策略結構本身決定的相關性風險。這個情境下的當日損失:`1.5% + 1.5% = 3.0%`。

**門檻訂在 -4%**,比這個「兩筆部位同日正常停損」的建模基準(3.0%)高出約 33% 的緩衝——用意是讓每日熔斷**不會**在策略正常運作、風控機制正常發揮作用的情況下就自動觸發(那樣熔斷會變成毫無資訊量的例行事件),而是保留給真正超出設計預期的異常日子:例如滑點顯著超出 ATR 停損距離所隱含的名目值、同一交易對當日停損後又重新觸發進場並二度停損、或任何導致當日實際虧損超出「兩筆部位各自正常停損」這個基準情境的狀況。

### 3.2 觸發行為:僅暫停新倉,既有部位不強制平倉

**行為:觸發後暫停開新倉 3 個曆日(不含觸發當日),既有持倉持續由第 2 節既定的出場路徑(ATR 移動停損 / Donchian 下軌 / time-stop)正常管理,不強制平倉。**

**理由:**

- 既有部位的出場邏輯已經是**經過統計驗證、有明確依據**的機制([`statistical-methodology.md`](./statistical-methodology.md) 全篇),用一個當日虧損門檻去強制平倉,等同用一個未經驗證、臨時起意的規則去覆蓋已定案的出場邏輯,可能在比原本停損更差的價位平倉,反而增加損失。
- 這與 [`architecture-spec.md`](./architecture-spec.md) 4.2 節描述的 Telegram `/stopentry` 行為(「阻止新進場,既有持倉持續依原本的 ROI/出場訊號/停損邏輯正常管理」)在精神上完全一致——每日熔斷是這個「暫停開新倉但不放棄既有部位風控」中間狀態的自動化版本,適合「當天狀況不對勁,先別開新倉」的情境,而非「帳戶級別出現結構性問題,必須立即清空曝險」的情境(那是第 5 節 kill switch 的職責)。
- 3 天的鎖定期選擇:短到不會把單一壞日子放大成長期停機(避免過度懲罰一次正常範圍內的波動),但長到足以避免隔天立刻在同樣不利的市場條件下重新進場——不需要走到 Phase 0 規定的完整 24 小時冷靜期 + 人工覆核流程(那個流程的重量級設計是留給第 5、6 節的帳戶級/月度事件,詳見 3.3 節的區分)。

### 3.3 與 Phase 0「月回撤 8%」熔斷的關係:不重複,互補

[`scope.md`](./scope.md) 已確認「月回撤達 8% 即觸發強制停機複查」。每日 -4% 熔斷與這個既有規則**監測的是不同時間粒度的風險形狀,任一個都不能取代另一個**:

- **每日熔斷抓的是「單日急殺」**:一天之內損失集中超出設計基準,可能是單一異常事件(例如當日行情劇烈波動、執行滑點異常)造成,需要快速反應但不必然代表策略本身有系統性問題。
- **月回撤 8% 抓的是「溫水煮青蛙」**:例如一個月內連續出現四個各約 -2% 的普通日子(每一天都遠低於 -4% 的每日熔斷門檻,完全不會觸發每日熔斷),但累積起來已達 -8%,代表策略在這段期間可能處於系統性失效的 regime(例如 [`strategy-hypothesis.md`](./strategy-hypothesis.md) 標記的橫盤震盪假突破環境),這正是只看單日數字會完全錯過、必須用累積窗口才能抓到的風險形狀。

兩者互補,不是「兩層一樣的保險」——這也是為什麼第 1 節的連續虧損推導表要同時列出 8%、15%、20% 三個層級:每一層對應到治理反應的嚴重程度不同(見 3.4 與第 6.5 節總覽表)。

### 3.4 技術實作缺口與其架構性侷限(誠實揭露)

Freqtrade 原生 Protections(`StoplossGuard`/`MaxDrawdown`/`CooldownPeriod`/`LowProfitPairs`)**沒有一個是直接對應「當日 % 損益」的機制**——`StoplossGuard` 計的是停損筆數,`MaxDrawdown` 計的是相對歷史峰值的回撤幅度(第 5、6 節會用它,但語意上不是「今天」這個曆日概念)。因此**每日熔斷無法用一個原生 Protection 直接表達**,必須實作為策略程式碼內的自訂檢查(例如在 `confirm_trade_entry` 內,即時查詢當日已實現+未實現損益、與開盤時的權益比較,低於 -4% 即拒絕新進場)。

**這帶來一個必須被記錄下來的架構性侷限,呼應 [`architecture-spec.md`](./architecture-spec.md) 4.3 節對「框架強制 vs. 策略程式碼」差異的既有討論:** `StoplossGuard`/`CooldownPeriod`/`MaxDrawdown` 由 `ProtectionManager` 在框架層強制執行,**策略程式碼完全無法繞過或誤判**;而每日虧損熔斷因為活在 `confirm_trade_entry` 這類策略 callback 內,**技術上仍屬於「策略程式碼」的一部分**——若這段自訂邏輯本身有 bug(例如日期邊界算錯、權益快照抓錯時間點),它不像原生 Protection 那樣有框架層的雙重保險。

**因應建議(政策層級要求,非程式碼):** 這段自訂每日損益檢查函式應被視為與其他安全關鍵路徑同等重要,在實作與 code review 階段(Phase 10+)需要有明確的單元測試覆蓋當日損益計算的邊界情況(例如跨日時區、部分成交、未平倉部位的即時估值),因為它是本文件所有防線中**唯一沒有 Freqtrade 框架層級保證的一層**。若 Phase 7/10 發現 Freqtrade 版本已提供更接近原生的每日損益 Protection 或等價機制,應優先改用該機制,降低這個已知的架構侷限。

---

## 4. 總曝險上限與相關性上限 / Total Exposure & Correlation Cap

### 4.1 為什麼「兩個獨立 1.5%」不等於安全

v1 只交易 BTC、ETH 兩個現貨對,兩者皆為長倉。表面上「各自風險上限 1.5%」看起來已經是保守設計,但 [`strategy-hypothesis.md`](./strategy-hypothesis.md) 已反覆標記:BTC、ETH 高度依賴同一個大盤 regime,「risk-off 時往往同步下跌(correlation clustering),分散配置在最需要它的時候可能失效」。加密現貨市場中 BTC/ETH 日報酬相關係數常態性地落在高位區間(0.7 以上並不罕見)。這代表**同時持有兩筆「各自獨立 1.5%」的部位,實際承擔的共同曝險遠高於兩個真正互不相關資產的組合**——本質上更接近「一個放大過的單一大盤方向性賭注」,而非兩個分散的獨立部位。

### 4.2 單筆與合併 `risk_fraction` 上限

- **單筆 `risk_fraction` 上限:1.5%(不變,第 1 節)。** 兩個標的各自觸發訊號時仍可各自使用到 1.5%,因為 BTC/ETH 同時進場、同時參與同一波大趨勢,正是這個策略選擇這兩個標的的設計初衷([`strategy-hypothesis.md`](./strategy-hypothesis.md):「BTC/ETH 等高流動性現貨」),過度限制同時持倉會直接削弱策略本身的意圖。
- **合併 `risk_fraction` 上限:2.5%**(BTC、ETH 同時有部位時,兩筆 `risk_fraction` 加總不得超過此值),相對 naive 加總(`1.5%+1.5%=3.0%`)打折約 17%,反映兩者高相關、不能視為獨立風險加總的事實。

**實作規則(供 Phase 7/10 落地,非程式碼)**:先觸發進場的部位可使用完整 1.5%;第二個部位觸發進場時,其 `risk_fraction` 取 `min(1.5%, 2.5% - 已持有部位的實際 risk_fraction)`——若第一筆已用滿 1.5%,第二筆最多只能再用 1.0%,而不是各自獨立取滿 1.5%(合計 3.0%,超出合併上限)。

### 4.3 名目部位(Notional)上限:第二層防護,獨立於 ATR 公式

`position_size = (risk_fraction × Equity) / (k × ATR)` 這個公式有一個容易被忽略的邊界案例:**當 `ATR%` 異常小(低波動盤整期,恰好也常是突破訊號醞釀前的典型狀態)時,分母趨近於零,同樣的 `risk_fraction` 會換算出異常巨大的名目部位**。用 [`statistical-methodology.md`](./statistical-methodology.md) 的範例參數延伸驗證:

```
情境 A(典型波動度,k=3, ATR%=3%):
  notional = 1.5% / (3 × 3%) = 1.5% / 9% ≈ 16.7% 權益 → 合理

情境 B(極端低波動,k=3, ATR%=0.5%):
  notional = 1.5% / (3 × 0.5%) = 1.5% / 1.5% = 100% 權益 → 單筆部位吃掉全部本金
```

現貨無槓桿,理論上限本來就是 100%,但「用全部本金押單一部位」本身就不是可接受的風控狀態,不能因為公式沒有明確違反「無槓桿」的物理限制就視為合規。因此本文件訂定**獨立於 ATR 公式之外的名目部位上限**,作為 `min()` 的一部分:

- **單筆名目部位上限:≤ 50% 權益**——不論 ATR 公式算出多少,任何單一部位都不得超過此值。無論兩個標的是否同時觸發,單一交易不應該有能力主張超過半數本金。
- **合併名目部位上限(BTC+ETH 同時持倉):≤ 80% 權益**——保留 ≥20% 現金緩衝,除了進一步限制相關性風險的實際曝險金額(不只是「風險」在統計意義上的曝險,也包括「萬一模型完全失準」時實際能虧損的本金上限),也讓帳戶隨時保有應付手續費、滑價、以及未來調整所需的操作彈性。

最終部位大小取三者的最小值:`min(ATR 公式換算值, 單筆 notional 上限 × Equity / entry_price, 依 4.2 節合併 risk_fraction 上限反推的可用配額)`。

---

## 5. Kill Switch 帳戶回撤門檻 / Kill-Switch Drawdown Threshold

### 5.1 確認門檻:-15%

**確認採用 -15%**,即 Phase 0 最大回撤上限區間(15–20%)的**下緣**,而非上緣(20%)。這個數字與我在 [`stable-profitability-roadmap.md`](./stable-profitability-roadmap.md) 原本提出的「-15% kill switch」立場一致,也與 [`statistical-methodology.md`](./statistical-methodology.md) 6.4 節已經直接引用的表述一致(該節明確寫道:「15% 是 `stable-profitability-roadmap.md` 中 risk-manager 提出的 kill switch 觸發線下緣」)——**這不是本文件新提出的數字,是把兩份既有文件已經共同指向的數字正式拍板**。

**為什麼是下緣而非上緣:** Kill switch 是全自動、無需人工判斷的最後防線,而自動監測本身存在偵測延遲(每個主迴圈迭代才評估一次,不是連續監控)與執行延遲(偵測到閾值突破,到 Protection 實際鎖倉之間,價格仍可能繼續移動)。把自動觸發點設在 15%,刻意在 15%–20% 之間保留 5 個百分點的緩衝空間,讓即使有現實中的執行延遲與滑點,實際觸及的回撤也很難真正突破 Phase 0 的 20% 硬上限——這是工程上常見的安全邊際做法,不是把 15%、20% 兩個數字混為一談。

### 5.2 觸發行為:兩層執行(自動 + 人工),誠實對齊 Freqtrade 的既有限制

[`architecture-spec.md`](./architecture-spec.md) 4.3 節已經明確驗證:**Freqtrade 的 Protections 只會鎖定新進場,不會自動強制平倉既有部位**。這代表「kill switch = 強制全部平倉 + 停機」這個完整行為,**不能只靠 `MaxDrawdown` Protection 自動達成**——本文件必須誠實面對這個既有限制,而不是假裝一個 Protection 設定就能滿足「force-close everything」的要求。實際設計分兩層:

1. **自動層(框架強制,立即生效):** `MaxDrawdown` Protection 偵測到回撤達 15% 門檻時,立即鎖定所有新進場,持續 `stop_duration`(見 5.4 節)。這一層**不需要人為介入**,是 [`architecture-spec.md`](./architecture-spec.md) 4.3 節已驗證「不可被策略程式碼繞過」的框架層機制。
2. **人工層(必要,無法自動化):** 既有部位的強制平倉,需要負責人(唯一持有 kill switch 拍板權者,[`scope.md`](./scope.md) 第 5 節)執行 [`architecture-spec.md`](./architecture-spec.md) 4.4 節已定案的 runbook:先 `/forceexit all` 清空所有持倉,再 `/stop` 完全停止交易迴圈。

**前提要求(本文件新增,填補這個兩層設計的可靠性缺口):** 若負責人沒有即時得知第 1 層已經觸發,第 2 層的人工動作就無從發生。因此本文件**要求** Telegram 通知(或等價的告警管道,[`architecture-spec.md`](./architecture-spec.md) 5.2 節已列為需客製化接線的項目)必須設定為:`MaxDrawdown` Protection 觸發鎖倉、每日虧損熔斷觸發、`StoplossGuard`/`CooldownPeriod` 觸發時**即時主動推播**,不可依賴負責人自行定期登入查詢——這是把「自動偵測」與「人工強制平倉」這兩層真正串起來的必要條件,否則第 1 層的自動鎖倉會讓人誤以為「系統已經處理好了」,而實際上曝險部位仍然掛在那裡毫無防護地等待市場進一步惡化。

### 5.3 對應到 Freqtrade `MaxDrawdown` Protection 的具體參數(kill switch 層級)

| 參數 | 數值 | 理由 |
|---|---|---|
| `max_allowed_drawdown` | `0.15` | 第 5.1 節已確認 |
| `lookback_period_candles` | `365`(1d K棒,即約 1 年;若版本設有更小的技術上限,取該版本允許的最大值,並在 Phase 7 記錄實際核實結果) | Kill switch 的目的是限制**帳戶自成立以來**的最大累積虧損,不是只看近期窗口——若用短窗口(例如比照 5.4 節月度層級的 30 天),一個已經發生超過該窗口、帳戶至今仍未創新高的深度回撤會被「滑出視窗」而偵測不到,這正好是 kill switch 最不能漏接的情境。 |
| `trade_limit` | `2` | 避免窗口內交易筆數過少(例如僅 1 筆)時,單筆極端結果被誤判為「回撤破新高峰值的 100%」這類失真訊號;策略一交易頻率本就低,`trade_limit` 不宜設太高,否則熔斷機制早期會長期處於「尚未累積足夠交易、無法生效」的空窗期。 |
| `stop_duration` | `≥ 24 小時`(精確單位換算見 5.4 節) | 對齊 [`scope.md`](./scope.md) 第 5 節「kill switch 觸發後必須強制冷靜期(如 24 小時)」 |

### 5.4 `stop_duration` 的計時單位:寧多勿少

[`architecture-spec.md`](./architecture-spec.md) 已在第 9 節列為已知待查證項目:「`MaxDrawdown` Protection 的 `stop_duration` 計時單位(分鐘 vs. K棒數)需要在 Phase 4 定案 24 小時冷靜期的技術對應值時,依當時版本文件核實精確單位換算。」本文件在此定案換算原則,精確語法留給 Phase 7/實作階段依當時版本核實:

- 若該版本以**分鐘**為單位(常見參數名如 `stop_duration`):設為 **1440 分鐘**,精確對應 24 小時。
- 若該版本以 **K棒數**為單位(參數名如 `stop_duration_candles`):在本文件第 6.1 節已確立的 **1d 時間框架**下,`1` 根日 K 名義上等於 24 小時,但實際鎖定時長會依觸發時間點落在該根 K 棒的哪個位置而有所浮動(可能略短於或略長於嚴格 24 小時,取決於框架如何計算 K 棒邊界)。

**核心原則:[`scope.md`](./scope.md) 訂的是「至少 24 小時」的下限要求,不是精確 24 小時 00 分的上限要求。** 若 K棒計時方式導致實際鎖定時間略微超過 24 小時(例如因為 K棒邊界對齊而拉長到略多於 24 小時),這完全符合、甚至更保守地滿足 Phase 0 的精神;唯一不可接受的情況是**任何可能導致鎖定時間短於 24 小時的設定**。因此若 Phase 7 發現 `stop_duration_candles=1` 在實務上可能因邊界效應而不足 24 小時,應直接調升為 `2`(寧可鎖定接近 48 小時,也不可低於 24 小時下限)。

---

## 6. StoplossGuard / CooldownPeriod / 月回撤 8% 的技術對應

### 6.1 統一 K 棒單位:1 日 K(1d)

[`architecture-spec.md`](./architecture-spec.md) 的 `config-common.json` 只列出 `timeframe` 這個設定鍵,未定案具體數值。本文件在此明確採用 **1d(日 K)**作為所有本文件風控參數(StoplossGuard、CooldownPeriod、MaxDrawdown 的 candle 版本參數)的統一計時單位——這不是本文件新做的架構決策,而是把 [`strategy-hypothesis.md`](./strategy-hypothesis.md) 早已確認的「日線收盤價突破」訊號邏輯,明確落實為一個具體的 `timeframe` 數值,因為第 6 節要求的具體 Protection 參數(以「candles」為單位)若不先固定計時單位就無法給出具體數字。與波段持倉週期(數天到數週)的時間尺度也自然吻合,不需要日內級別的細粒度。

### 6.2 StoplossGuard 具體參數

| 參數 | 數值 | 理由 |
|---|---|---|
| `trade_limit` | `2` | 策略一交易頻率本就低([`statistical-methodology.md`](./statistical-methodology.md) 3.1 節:「一個乾淨的趨勢市場一年可能才有個位數到十幾次有效突破訊號(單一標的)」)。在整體交易本就稀少的基準下,短窗口內連續出現 2 筆停損已經是明顯的異常聚集訊號,不需要等到 3 筆以上才反應。 |
| `lookback_period_candles` | `10`(10 個日 K,即 10 天) | 窗口需短到能捕捉「短期內密集發生」這個異常特徵(而非把整個交易史都算進來稀釋掉聚集效應),但也要長到超過本策略單筆平均持倉幾天的量級,才有意義地涵蓋「連續幾筆交易」而不是「同一筆交易內的雜訊」。 |
| `stop_duration_candles` | `5`(5 天) | 短於第 5 節 kill switch 層級的 ≥24 小時要求數倍,因為 StoplossGuard 抓到的是「頻率異常」而非「幅度異常」,嚴重程度低於帳戶級回撤事件,鎖倉 5 天讓短期内不穩定的訊號環境(例如盤整期假突破)有時間沉澱,同時不會像月回撤熔斷或 kill switch 那樣觸發完整的人工覆核流程。 |

### 6.3 CooldownPeriod 具體參數

| 參數 | 數值 | 理由 |
|---|---|---|
| `stop_duration_candles` | `2`(2 天) | 任何一筆交易平倉後(不論輸贏),同一交易對 2 天內不得重新進場。目的是避免在同一個尚未確認的突破位附近反覆進出造成的手續費/滑價侵蝕與情緒化交易傾向,由於本策略訊號本身已是週級別的稀疏事件,2 天的冷卻期相對整體訊號頻率而言影響甚微,不會實質壓抑合理的下一次進場機會。 |

### 6.4 月回撤 8% 對應到 `MaxDrawdown` Protection 的第二實例

[`scope.md`](./scope.md) 第 5 節提到的「熔斷條件觸發自動停機後必須強制冷靜期」是一般性表述,不限定只適用於 kill switch(第 5 節)——本文件將其**一致地套用到月回撤 8% 這個 Phase 0 既有觸發條件上**,理由是「一旦達到某個回撤閾值就自動暫停」本質上就是一種熔斷,不應該因為閾值較淺就免除冷靜期與覆核要求,否則會製造一個可以被規避冷靜期規則的漏洞(例如反覆讓回撤壓在 8%–15% 之間、每次觸發後立刻重啟,實質上規避了 kill switch 層級才有的紀律要求)。

| 參數 | 數值 | 理由 |
|---|---|---|
| `max_allowed_drawdown` | `0.08` | [`scope.md`](./scope.md) 已確認的既有數字 |
| `lookback_period_candles` | `30`(30 個日 K,約 1 個月) | 對應「月」這個時間尺度,採**滾動 30 天**而非嚴格日曆月邊界——呼應 [`statistical-methodology.md`](./statistical-methodology.md) 3.2 節「採滾動窗而非擴張窗」的既有偏好,避免日曆月邊界造成的失真(例如月初一筆大虧損,到下個月 1 號就被歸零重置,即使實際只過了 1 天) |
| `trade_limit` | `2` | 與 6.2 節理由相同 |
| `stop_duration` | `≥ 24 小時` | 對齊上述「熔斷即需冷靜期」的一致性原則,單位換算方式同第 5.4 節 |

**觸發行為(與 kill switch 的關鍵差異):** 暫停新倉 + 強制 ≥24 小時冷靜期 + 人工覆核觸發原因後才可手動恢復新倉——**但既有部位不強制平倉**,持續由第 2 節既定出場路徑管理。這是與第 5 節 kill switch 最核心的行為差異:月回撤 8% 是「先暫停下來看清楚狀況」的治理動作,回撤幅度尚未深到需要不計代價立即清空所有曝險;kill switch 的 15% 才是「不計較是否砍在好價位,先把曝險歸零」的最後手段。若把兩者都設計成強制平倉,8% 這個較淺的門檻反而會讓系統過早、過於頻繁地用市價強制平倉方式離場,增加不必要的交易成本與滑價損失,也讓 15% 這個原本該有的「更嚴重」層級失去區分度。

### 6.5 四層防線總覽(展示彼此互補、非重複)

| 防線 | 監測對象 | 監測窗口 | 門檻 | 觸發後行為 | 是否強制平倉 | 恢復方式 |
|---|---|---|---|---|---|---|
| 每日虧損熔斷(第 3 節) | 單一曆日已實現+未實現損益 | 1 天 | -4% | 暫停新倉 3 天 | 否 | 到期自動恢復,無需人工覆核 |
| StoplossGuard(6.2) | 短窗口內停損筆數 | 10 天 | 2 筆停損 | 暫停新倉 5 天 | 否 | 到期自動恢復,無需人工覆核 |
| CooldownPeriod(6.3) | 單一交易對剛平倉 | 單筆交易後 | 任何一次平倉 | 該交易對暫停新倉 2 天 | 否 | 到期自動恢復 |
| 月回撤熔斷(6.4) | 帳戶回撤(滾動窗口) | 30 天 | -8% | 暫停新倉 + ≥24h 冷靜期 | 否(既有部位正常出場) | 人工覆核觸發原因後手動恢復 |
| Kill switch(第 5 節) | 帳戶回撤(長窗口/近全歷史) | 365 天 | -15% | 暫停新倉(自動)+ **強制平倉全部(人工執行 runbook)**+ ≥24h 冷靜期 | **是** | 人工覆核觸發原因後手動恢復 |

五層防線監測的訊號(單日幅度、短窗口頻率、單一交易對狀態、月度累積、帳戶級深度回撤)彼此不重疊,任一層觸發都不代表其他層必然也會觸發——這是刻意設計成的縱深防禦(defense in depth),不是同一個風險用五種方式重複計算。

---

## 7. 風控參數變更流程 / Change Procedure

落實 [`scope.md`](./scope.md) 第 5 節「風控參數一旦在 Phase 4 定案並上線,非緊急狀況不得臨時調鬆;要調整必須走 Phase 4 文件的正式變更流程(寫下修改原因與日期),不可在虧損當下憑情緒現場修改」,以及 [`architecture-spec.md`](./architecture-spec.md) 4.3 節「Git commit history 本身就是正式變更流程要求的書面紀錄」的既有設計,具體化為以下checklist:

1. **禁止時機:** 任一防線(第 3、5、6 節)處於觸發鎖定狀態、或觸發後的強制冷靜期尚未結束時,**不得**提出或執行任何風控參數變更——這是 [`scope.md`](./scope.md) 「不可在虧損當下憑情緒現場修改」的直接落實,禁止事項本身也不允許在冷靜期內被「臨時解除」。
2. **變更提案須包含:** 變更的參數名稱、現值 → 提議新值、變更理由(須有具體依據——例如 Phase 6 回測結果、一段時間 paper trading/live 的實際數據,不能只是主觀感覺)、提出日期。
3. **依變更影響區分處理路徑:**
   - **核心治理數字**(`risk_fraction` 硬上限、Kelly `c`、kill switch/月回撤門檻、每日熔斷門檻、ATR `k` 的搜尋邊界本身):任何調整**必須**重新走過 [`statistical-methodology.md`](./statistical-methodology.md) 第 7 節的通過門檻總表相關項目(至少是受影響的那一項),不得只憑文字論證就直接改動生效值——這些數字上一次生效前就是靠完整驗證流程定案的,調整同樣需要對應等級的驗證,不能無成本地繞過。
   - **搜尋空間內的操作調參**(例如 hyperopt 在既有 `k∈[2.0,4.0]` 邊界內選出的實際運行值):屬於 Phase 6 常態流程的一部分,不需要重新走本節流程——本節流程只約束**邊界本身**的變動,不約束邊界內由 hyperopt 依既定方法論選出的值。
4. **落地方式:** 依 [`architecture-spec.md`](./architecture-spec.md) 2.2 節既有設計,風控參數(`protections` property 內容、`risk_fraction`/`k` 邊界等寫死值)只能透過修改策略程式碼、git commit(**commit message 需包含變更原因與日期**)、重新部署三個步驟完成,**不允許**對執行中程序做任何形式的即時熱編輯(live patch)。
5. **部署時機:** 僅在排定的、非緊急的維護窗口執行重新部署,不得在盤中因單一交易結果不如預期而臨時觸發部署——這與第 1 點「冷靜期內禁止變更」的精神一致,但範圍更廣:即使不在冷靜期內,也不應該用「馬上部署新參數」來對沖當下的情緒化決策衝動。
6. **紀錄留存:** 除 git commit history 外,建議在本文件(或後續建立的獨立 changelog)維護一份簡短的參數變更歷程表(欄位:日期、參數、舊值、新值、理由、對應 commit hash),讓「這個數字上一次是什麼時候、為什麼改的」可以不必翻 git log 就先有個概覽——這是輔助性的可讀性措施,不是取代 git commit 作為正式紀錄的地位。

---

## 8. 已知限制與交棒事項

- **每日虧損熔斷缺乏框架層級保證**(第 3.4 節已詳述):這是本文件所有防線中唯一必須活在策略程式碼(而非 Freqtrade `ProtectionManager`)內的一層,實作與測試階段需要用同等於安全關鍵路徑的嚴謹度對待,若未來 Freqtrade 版本原生支援等價機制應優先改用。
- **`stop_duration` 精確計時單位**(分鐘 vs. candles)延續 [`architecture-spec.md`](./architecture-spec.md) 第 9 節已標記的待查證項目,本文件已給出換算原則(第 5.4 節:寧可鎖定時間偏長也不可偏短),但精確參數名稱與數值需 Phase 7/實作階段依當時 Freqtrade 版本文件核實。
- **`MaxDrawdown` Protection 能否在單一策略類別的 `protections` property 中並存兩個不同閾值/窗口的實例**(第 5 節 kill switch 層級與第 6.4 節月回撤層級各是獨立設定)技術上待 Phase 7/實作階段依當時 Freqtrade 版本核實是否支援清單內多個同類型 Protection 並存;若不支援,月回撤 8% 這一層應改用與每日熔斷相同的自訂邏輯路徑實作(`confirm_trade_entry` 內的滾動 30 天損益檢查),並同樣適用第 3.4 節「非框架層保證,需加強測試覆蓋」的要求。
- **Time-stop(45 天)、ATR 倍數搜尋邊界(2.0–4.0)是用 [`statistical-methodology.md`](./statistical-methodology.md) 既有的持倉時間估計值(尚屬方法論階段的既有數字,非 Phase 6 真實回測結果)推導**,Phase 6 backtest-analyst 取得真實交易數據後,若發現實際持倉時間分布、或 `k` 在邊界附近被 hyperopt 頻繁選中(代表邊界可能設得過緊),應依第 7 節流程重新評估,而非本文件視為一次性定案、永不調整的數字。
- **每筆 `risk_fraction` 上限(1.5%)、合併曝險上限(2.5%/80%)是用確定性的「連續虧損」推導,不是機率模型**——這是刻意的簡化以便在真實回測數據出現前先給出可執行的邊界(第 1.3 節已聲明),Phase 6 的 Block Bootstrap 蒙地卡羅模擬結果出爐後,應與本文件的邊界互相對照:若模擬顯示這些邊界明顯過於保守或明顯不足,應依第 7 節流程調整,並在調整時同步更新第 1.2 節的推導表。

**下一步:** 本文件經專案負責人審閱確認後,Phase 4 結案,可交棒 Phase 5(trading-security-reviewer,`security-policy.md`——金鑰管理、Testnet/Live 二次確認機制,建立在本文件與 [`architecture-spec.md`](./architecture-spec.md) 已定的治理基礎上)與 Phase 6(backtest-analyst,`backtest-procedure.md`——用真實歷史資料驗證本文件所有邊界數字是否成立,尤其是第 1.3 節、第 2.3 節、第 8 節已明確標記「待真實數據驗證」的項目)。

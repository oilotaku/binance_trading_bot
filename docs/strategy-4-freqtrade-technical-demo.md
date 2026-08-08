# 策略四技術驗證:機制在 Freqtrade 框架內正確運作

> 狀態:**✅ 技術驗證完成(2026-08-08)。**
> **⚠️ 本文件不是新的統計判定,不取代 [`strategy-4-results.md`](./strategy-4-results.md)。**
> 目的:確認 CP-004/CP-005/CP-006 已核准的機制能在 Freqtrade 框架內正確下單、
> 調整部位、觸發風控——而不只是在 `analysis/vol_target.py` 的離線模擬裡成立。

---

## 0. 為什麼跑這個、為什麼不是正式驗證

即時 dry-run(Binance Testnet / mainnet)在本環境無法連線——實測 `testnet.binance.vision` 同樣回應 **451**(地區限制),與正式資料下載時遇到的 `api.binance.com` 限制同一類,且**不是白名單能解的問題**(見 [`data-requirements.md`](./data-requirements.md) §1.1;即時報價 API 是 Binance 自己的地區封鎖,不是代理層政策)。

因此改用 Freqtrade 的 `backtesting` 引擎,搭配已有的歷史資料 + `analysis/offline_exchange` 市場規格 stub,跑一次真正的策略類別(`user_data/strategies/VolatilityTargeting.py`)。**這驗證的是「機制在框架內能不能正確運作」,不產生新的統計結論** ——正式判定仍以 `analysis/vol_target.py` + [`strategy-4-results.md`](./strategy-4-results.md) 為準。

---

## 1. 新增檔案

`user_data/strategies/VolatilityTargeting.py` —— 策略四第一次以真正的 Freqtrade `IStrategy` 存在(先前只有離線純函式版本)。參數全部從 `analysis/vol_target.py` 匯入,不重複定義。

關鍵設計對應(見 [`CP-005`](./change-proposals/CP-005-risk-policy-for-always-in-market.md)):

| CP-005 決策 | 實作位置 |
|---|---|
| 排除 ATR 停損/time-stop/CooldownPeriod/StoplossGuard | 全部不繼承、`protections = []` |
| 曝險由 `adjust_trade_position` 管理 | `custom_stake_amount`(進場)+ `adjust_trade_position`(再平衡) |
| 回撤斜坡(滾動 365 天視窗) | `_record_and_get_drawdown()`,對齊 `vt.simulate()` 的滾動視窗邏輯 |
| 再平衡帶 20% | `adjust_trade_position` 內的 `rel_dev < vt.REBALANCE_BAND` 檢查 |
| 目標曝險趨近零時完整出場 | `custom_exit`(Freqtrade 的 `adjust_trade_position` 無法把部位精確減到 0) |

---

## 2. 驗證結果

### 2.1 短窗口(2022 全年,含熊市)

- 2 個標的各觸發**多次**進場/減碼(訂單明細顯示全年持續依波動與回撤調整部位,buy/sell 交替、`partial_exit` 標籤)
- `min_rate`/`max_rate` 與真實 2022 BTC 價格區間吻合
- 回撤高峰落在 2022-01-03 → 2022-05-28(權益基礎 18.05% underwater),量級與 [`analysis/vol_target.py`](../analysis/vol_target.py) 同窗口離線模擬(MDD 20.17%)一致

### 2.2 中窗口(2020–2022 中,含 312 崩盤、2021 ATH)

- 權益曲線高低點日期與真實市場史吻合:**峰值落在 2021-11-09**(BTC 歷史高點正是 2021-11-10 附近,與 [`data-requirements.md`](./data-requirements.md) §0 的地標比對一致)
- 回撤區間 2021-11-09 → 2022-05-28,權益基礎 21.39% underwater(未觸及 30% 斜坡門檻,符合預期)

### 2.3 完整 9 年窗口(2017-08-17 ~ 2026-07-31)

| | Freqtrade 回測(本次) | `analysis/vol_target.py` 離線判定([`strategy-4-results.md`](./strategy-4-results.md)) |
|---|---|---|
| 總報酬 | +228.33% | (未直接比較總報酬,判定用年化 CAGR) |
| **最大回撤(權益基礎)** | **30.92%** | **31.37%** |
| BTC 進場/減碼次數 | 140 / 110 | — |
| ETH 進場/減碼次數 | 133 / 102 | — |

**兩套獨立實作的 MDD 相差不到 0.5 個百分點。** 這是一個強的交叉驗證:離線純函式模擬(`analysis/vol_target.py`)與真正的 Freqtrade 執行引擎(含撮合、部位精度、force_exit 邊界)算出高度一致的風險輪廓,說明 [`strategy-4-results.md`](./strategy-4-results.md) 的判定不是模擬邏輯的產物,而是機制本身的真實行為。

---

## 3. 一個 Freqtrade 報表的已知怪異之處(記錄以免日後誤讀)

Freqtrade 內建的「Max % of account underwater(closed trades)」欄位在本策略的回測中固定顯示 **0.00%**。這不是 bug,是**指標定義問題**:該欄位只統計「已平倉交易之間」的權益回撤,而策略四的兩筆交易(BTC、ETH)一路持有到回測結束才 `force_exit`,期間從未真正平倉,因此這個欄位無法捕捉到任何東西。

**正確的指標是「Wallet based Metrics」區塊的 `Max % of account underwater (balance)`**——它逐日 mark-to-market 整個錢包餘額,這才是本專案定義的 MDD(對齊 [`analysis/benchmark.py`](../analysis/benchmark.py) 的 `performance_summary()`)。第 2 節的數字全部取自這個欄位。

---

## 4. 誠實揭露

- **本次驗證只確認機制「能跑」,不重新判定「該不該用」。** 第一層/第二層的正式結論見 [`strategy-4-results.md`](./strategy-4-results.md),未因本次技術驗證而改變。
- **9 年回測與離線模擬的 MDD 相差 0.5pp**,差異來源包含:交易所最小下單量截斷、`force_exit` 邊界效應(回測結束時的強制平倉不代表策略行為)、Freqtrade 的手續費/滑價撮合細節。這個差距在可接受範圍內,但不是「完全相同的兩個數字」。
- **`custom_exit` 觸發後的「re-entry」邏輯依賴 `populate_entry_trend` 的訊號在下一根 K 棒重新評估**——這在本次測試窗口沒有被實際觸發到(目標曝險從未真正跌破 `_ZERO_EXPOSURE_EPSILON = 0.01`),因此「出場後能否正確重新進場」這條路徑**尚未被實測**,只在程式邏輯上是自洽的。
- **`adjust_trade_position` 減碼分支的下界保護**(不減到 `min_stake` 以下)同樣未被本次測試明確觸發驗證,只在程式碼審閱層級確認邏輯正確。

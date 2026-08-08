# 管線整合測試發現的問題 / Pipeline Integration Test Findings

> 狀態:**已修正並加上迴歸測試 / FIXED, regression-tested.**
> 日期:2026-08-08
> 來源:`analysis/tools/pipeline_integration_test.py`(合成資料端到端執行)

## 為什麼需要這份文件

`docs/backtest-procedure.md` 第 4 節的完整驗證流程是 9 個 fold × (hyperopt 1,000 epochs + OOS backtest),在真實環境要跑數小時。本次用**合成資料**把整條管線端到端跑過一次,目的不是驗證策略有沒有 edge(合成資料的績效數字毫無意義),而是**在花費真實運算資源之前,先找出管線本身的整合錯誤**。

結果找到 4 個問題,其中 **2 個是會靜默產生錯誤結果、不會報錯的真實缺陷**。若沒有這次測試,它們會在真實資料上悄悄汙染最終結論。

---

## 🔴 發現一(嚴重):MAX_LOSS 哨兵值汙染 `sigma_SR`,使 DSR 恆為 0

**症狀:** `sigma_SR = 30779.4845`(正常應為 0.x 量級)。

**根因:** Freqtrade 對「交易筆數低於 `hyperopt_min_trades`」的 epoch **不呼叫 loss function**,直接指派哨兵值 `MAX_LOSS = 100000`(見 `freqtrade/optimize/hyperopt/hyperopt_optimizer.py`)。`analysis/data_loader.py` 原本無條件用 `sr_trade = -loss` 換算,把這些 epoch 算成 `SR_trade = -100000`。

**後果(這才是真正可怕的地方):** `sigma_SR` 是 [`statistical-methodology.md`](./statistical-methodology.md) 2.2 節 `SR0 = sigma_SR × C(N)` 的關鍵輸入。被汙染後 `SR0` 會暴衝到十萬量級,**DSR 因此恆為 0 —— 不論策略實際多好都必定判定「不通過」,而且整個過程不會拋出任何錯誤或警告。**

實測數據:20 個 epoch 中 2 個是哨兵值(交易數 0 和 1),`sigma_SR` 從正確的 **0.1970** 變成 **30779.4845**。

**修正:** `analysis/data_loader.py` 將哨兵值 epoch 的 `sr_trade` 設為 `NaN` 並標記 `is_sentinel`,下游 `.dropna()` 自然排除。同時 `analysis/report.py` 的 DSR `n_trials` 改用「有效 epoch 數」而非總數(產生零筆交易的 epoch 從來沒機會成為極值分布的最大值,不應計入 N)。

**迴歸測試:** `analysis/tests/test_statistical_modules.py::test_hyperopt_max_loss_sentinel_excluded_from_sigma_sr`

---

## 🔴 發現二(嚴重,操作陷阱):hyperopt 參數檔是共用可變狀態,會讓 walk-forward 靜默失效

**觀察:** hyperopt 執行完會把選出的參數寫進 `user_data/strategies/RegimeFilteredMomentumBreakout.json`,策略下次啟動時自動載入(`IntParameter(..., load=True)` 的既定行為)。

**好消息:** 這**證實了** [`backtest-procedure.md`](./backtest-procedure.md) 4.3 節步驟 5 的既有假設正確 —— OOS 回測確實會自動吃到該 fold hyperopt 選出的凍結參數,不需要額外接線。

**壞消息(必須寫進 runner 腳本的硬性約束):這個檔案是全域共用、會被覆寫的單一狀態。** 若 runner 腳本天真地寫成:

```
for fold in 1..9:  跑 hyperopt          # 每次都覆寫同一個 json
for fold in 1..9:  跑 OOS backtest      # ← 全部都讀到 fold 9 的參數!
```

則**九個 fold 的 OOS 回測會全部使用最後一個 fold 的參數**,整個 walk-forward 樣本外驗證完全失效 —— 而且回測會正常跑完、產出漂亮的報告,不會有任何錯誤訊息。這正是 [`statistical-methodology.md`](./statistical-methodology.md) 反覆警告的「看起來像驗證、其實什麼都沒驗證」的具體實例。

**強制要求(runner 腳本必須遵守):**

```
for fold in 1..9:
    跑該 fold 的 hyperopt        # 寫入 params json
    立刻跑該 fold 的 OOS backtest # 讀取剛寫入的 params
    備份該 fold 的 params json    # 留痕,供事後稽核每個 fold 實際用了什麼參數
```

hyperopt 與 OOS backtest **必須成對、緊鄰執行**,中間不得插入其他 fold 的 hyperopt。

---

## 🟡 發現三(測試 harness 錯誤,非產品缺陷):Binance 用 TICK_SIZE precision 模式

**症狀:** 回測產生 0 筆交易,`confirm_trade_entry` 從未被呼叫。

**根因:** 測試用的 market stub 把 `precision` 寫成 `{"amount": 5, "price": 2}`(小數位數的直覺寫法)。但 Binance 在 ccxt 的 `precisionMode` 是 **`TICK_SIZE`(=4)**,這些值被解讀為「最小跳動單位」—— 即「下單量必須是 5 顆 BTC 的倍數」。實際下單量 0.0055 BTC 被 `amount_to_contract_precision()` 截斷成 0,交易在 `confirm_trade_entry` 之前就被靜默丟棄。

**影響範圍:** 僅影響本測試 harness 的離線 stub。正式執行時 market 資料由交易所提供,不會有此問題。已在 stub 加上註解說明。

---

## 🟡 發現四(測試 harness 錯誤):程式化呼叫 hyperopt 必須明確指定 `RunMode.HYPEROPT`

**症狀:** hyperopt 產生了 20 組不同參數,但每個 epoch 的 loss 與交易數完全相同(`sigma_SR = 0`),參數顯示為 `value loaded from strategy`。

**根因:** `IStrategy.__init__` 執行 `ft_load_hyper_params(config["runmode"] == RunMode.HYPEROPT)`,這個布林值決定每個參數的 `in_space`。若 runmode 不是 `HYPEROPT`,`in_space` 全為 `False`,`hyperopt_optimizer` 的 `if attr.in_space and attr.optimize: attr.value = ...` 就不會生效 —— optimizer 照常搜尋,但策略永遠使用預設值。

**影響範圍:** 僅影響像本測試這樣以程式方式呼叫 Hyperopt 的情境。用 CLI `freqtrade hyperopt` 時 runmode 由框架自動設定。

---

## ✅ 同時獲得的正面確認

以下是這次執行**確認可用**的部分,不需要再懷疑:

- `analysis/data_loader.py` 讀得懂 Freqtrade 實際匯出的 zip/JSON 格式,欄位名稱與 `BT_DATA_COLUMNS` 相符
- 自訂的 `PurgedTradeSharpeLoss` 能在真實 hyperopt 迴圈中正確執行,成功呼叫 `analysis.walk_forward.purge_is_trades` 並回傳合法 loss
- 策略的 `custom_stake_amount` 在真實回測中回傳合理數值(實測 275 / 324 / 366 USDT,對應 3,000 USDT 權益與 1.5% 風險上限)
- `custom_stoploss`、`custom_exit`(time-stop)、四層 Protections 在真實回測迴圈中皆未拋出例外
- 完整鏈路 `backtesting → 匯出 → data_loader → cost_model → sample_size → significance` 格式相容、可執行

---

## ⚠️ 這次測試**沒有**驗證的事

必須明確劃清界線,避免日後誤讀:

- **策略有沒有 edge —— 完全沒有驗證。** 所有績效數字來自 `analysis/tools/make_synthetic_data.py` 產生的合成價格序列,統計上毫無意義。
- **`risk-policy.md` 的任何假設(45 天 time-stop、`k` 邊界、`risk_fraction` 上限)—— 完全沒有驗證。** 那需要真實市場資料,見 `backtest-procedure.md` 第 6 節。
- **`go-no-go-checklist.md` 的任何一項 —— 都不因本次測試而通過。**

本次測試唯一的主張是:**當真實資料到位時,這條管線在機械上跑得起來,而且上述 4 個會導致錯誤結論的缺陷已經被排除。**

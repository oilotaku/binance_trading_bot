# 驗證資料:現況、需求規格與取得方式

> 狀態:**🔴 阻塞中 / BLOCKED — 本環境無法取得任何市場資料。**
> 日期:2026-08-08
> 目的:把「驗證需要什麼資料」講到可以直接照著準備的程度,並記錄阻塞的確切原因。

---

## 1. 現況:一根 K 棒都沒有

`user_data/data/binance/` 是空的。截至今日,**本專案從未取得任何真實市場資料**,所有跑過的東西(整合測試、`σ_SR` 實測值)都是合成資料。

### 1.1 實測結果:全部來源被閘道拒絕

2026-08-08 重新實測 10 個來源,全部失敗:

| 來源 | 結果 |
|---|---|
| `api.binance.com` / `data.binance.vision` / `api1.binance.com` | ❌ |
| `api.kraken.com` / `api.exchange.coinbase.com` | ❌ |
| `api.coingecko.com` / `min-api.cryptocompare.com` | ❌ |
| `query1.finance.yahoo.com`(Yahoo Finance) | ❌ |
| `api.bitfinex.com` / `api.gemini.com` / `www.bitstamp.net` | ❌ |

代理狀態端點的診斷明確指出原因:

```
"kind": "connect_rejected",
"detail": "gateway answered 403 to CONNECT (policy denial or upstream failure)"
```

**這不是憑證問題、也不是設定錯誤,是這個執行環境的網路政策。** 白名單只放行 GitHub、PyPI、npm 等套件來源。`freqtrade download-data` 在此環境無法使用。

### 1.2 這不是可以繞過的東西

環境的 README 明確要求不得繞過網路政策。GitHub 是通的,理論上可以把資料塞進 repo 再拉下來——**但那是把 GitHub 當成資料走私通道,規避使用者為這個環境選定的政策**,不會這樣做。

同時這也違反本專案自己的規則:[`backtest-procedure.md`](./backtest-procedure.md) 1.4 節要求資料必須有可驗證的來源。**來路不明的資料跑出來的 DSR,數字再漂亮也不具意義。**

---

## 2. 需求規格

以 [`feasibility-paths.md`](./feasibility-paths.md) 建議的**方案 F**(`N=5` + 資料延長到約 8.7 年)為準。

| 項目 | 規格 |
|---|---|
| 交易對 | `BTC/USDT`、`ETH/USDT`(幣安**現貨**,承 [`scope.md`](./scope.md)) |
| 週期 | `1d`(日 K) |
| 起始 | 各交易對在幣安的**上市首日**(BTC/USDT 約 2017-11,ETH/USDT 約 2017-08)⚠️ 見 4.2 |
| 結束 | `2026-07-31` |
| 時區 | **UTC**,且時間戳需帶時區資訊 |
| 筆數 | 每個交易對約 3,200 根,兩個共約 6,400 根 |
| 檔案大小 | CSV 約 0.5 MB,壓縮後更小 |

**資料量非常小。** 阻塞的原因純粹是網路政策,不是規模或成本。

### 2.1 欄位

`date, open, high, low, close, volume` —— 標準 OHLCV。`date` 可以是 ISO 字串或 epoch 時間戳(秒/毫秒皆可,匯入工具會依數量級自動判斷單位)。

### 2.2 為什麼起點要往前推到上市首日,而不是沿用 2020-01

不是為了「資料多一點比較好」,而是 [`feasibility-paths.md`](./feasibility-paths.md) 第 2.1 節算出的具體差額:在 `N=5` 之下,6.6 年的門檻是 1.37、8.7 年是 1.20。**多出來的 2 年把 `σ_SR` 估計誤差的容錯從「高估 20% 就失守」推到「高估 50% 仍守得住」。**

⚠️ 但這也意味著必須納入 2017 泡沫與 2018 熊市。這兩段的市場結構與現在差異極大,**同質性假設存疑**——這個代價已記錄在 [`feasibility-paths.md`](./feasibility-paths.md) 第 3.3 節,不是被忽略。

### 2.3 指標暖身期

策略的 `startup_candle_count = 100`(Donchian 週期上界 55、ATR 上界 21,加緩衝)。Freqtrade 會自動從資料起點取用暖身 K 棒,**因此第一個 fold 的實際可用起點會比資料起點晚約 100 天**。規格中的「上市首日」已把這件事考慮進去——直接從最早可得處開始即可,不需額外往前推。

---

## 3. 資料到手之後怎麼進來

匯入路徑已經建好,而且**只有這一條路**:

```bash
.venv/bin/python analysis/tools/ingest_market_data.py \
    --pair BTC/USDT --input /path/to/BTCUSDT-1d.csv \
    --expected-start 2017-11-01 --expected-end 2026-07-31 \
    --note "來源說明"
```

工具會在寫入之前強制跑完 [`backtest-procedure.md`](./backtest-procedure.md) 1.4 節的規則 1–3(`analysis/data_quality.py`),並把來源記錄進 `user_data/data/binance/PROVENANCE.md`。

### 3.1 閘門的行為

| 情況 | 行為 |
|---|---|
| 結構性錯誤(欄位缺失、`high < max(open,close)`、日期無時區、重複/未排序、非正價格) | ❌ 擋下 |
| 日期不合理(早於比特幣創世、或在未來) | ❌ 擋下 —— 這是 epoch 單位判斷錯誤的防線,見 3.2 |
| 連續 ≥3 日缺漏(規則 2 第三級) | ❌ 擋下 |
| 孤立缺漏(規則 2 第一級) | ⚠️ 擋下並要求先重下;確實重下過仍缺漏才用 `--already-redownloaded` 放行並記錄為「資料缺口警示」 |
| 量價背離的價格尖刺(規則 3) | ⚠️ 警示,需人工比對其他資料源是否能複現 |

**沒有 `--force`,也沒有 forward-fill / 插值的能力**——不是沒做,是刻意不提供。1.4 節規則 2 明訂 forward-fill 會捏造一根從未存在的價格;能力不存在,才不會在趕時間時被說服使用。這一點有測試把關(`test_module_provides_no_data_repair_functions`)。

### 3.2 建這條路時抓到的一個真實 bug

冒煙測試時發現:餵進**以秒為單位**的 epoch 時間戳,工具會當成毫秒解析,所有日期塌縮到 1970 年。真正危險的不是日期錯,而是**日期一旦全部塌縮到同一天,規則 1 的缺漏偵測就變成空操作**(期望範圍只剩一天,差集必為空)——整個品質閘門會在毫無徵兆的情況下失效,而且畫面上還是印「✅ 通過」。

已修:匯入端依數量級自動判斷單位(秒/毫秒/微秒/奈秒),檢查端另外獨立擋下不合理日期。**兩層防線各有測試**,不依賴其中任何一層。

> 這類「錯得很安靜」的失效,與 [`pipeline-findings.md`](./pipeline-findings.md) 記錄的 `MAX_LOSS` 哨兵值汙染是同一種——都是不會報錯、但會讓整個統計結論失去意義的問題。

---

## 4. 取得資料的可行途徑

### 4.1 建議:調整環境網路政策

最直接的做法是把 `data.binance.vision` 加進環境的網路白名單。這是使用者可以自行變更的環境設定,見 [Claude Code on the web 文件](https://code.claude.com/docs/en/claude-code-on-the-web)。

`data.binance.vision` 是幣安官方的歷史資料公開站,提供逐月 ZIP 的 K 線封存,**不需要 API key,也不會碰到帳戶或下單權限**——就取得歷史 K 棒而言,它比開放 `api.binance.com` 的暴露面更小。放行之後 `freqtrade download-data` 可直接使用,不需要本文件第 3 節的人工匯入路徑。

### 4.2 或:在本環境外下載後提供檔案

在自己的機器上取得後,把檔案放進本專案再跑第 3 節的匯入工具即可。格式要求見 2.1,匯入工具會把品質檢查做完。

⚠️ **兩個交易對的上市日期(BTC/USDT 約 2017-11、ETH/USDT 約 2017-08)是我方記憶,本環境無網路無法核實。** 直接從各交易對最早可得的資料開始即可,不必遷就這兩個日期;實際起點請填進 `--expected-start`,匯入工具會據此檢查範圍是否完整。

---

## 5. 在資料到手之前,還能做什麼

已完成、不需要資料的:

- ✅ 統計模組(`analysis/`)與 62 項測試
- ✅ MinTRL 與時間單位防護([`CP-002`](./change-proposals/CP-002-dsr-time-unit.md))
- ✅ 可行性分析([`feasibility-paths.md`](./feasibility-paths.md))
- ✅ 資料品質閘門與匯入路徑(本文件第 3 節)

**還沒做、但同樣不需要資料的**——而且依 [`feasibility-paths.md`](./feasibility-paths.md) 5.3 節的預先登錄原則,**必須在資料到手之前做完**:

- ⬜ 決定是否採用方案 F,若採用則寫成 CP-003
- ⬜ 若採用,把 5 個策略參數中的 4 個事前固定,並明確寫下每個值的來源依據(公開先驗知識 vs. 我方推理),commit 進 git 留下時間戳

**這件事的順序不能顛倒。** 參數一旦在看過真實資料之後才定,`N` 就不再是 5,整個可行性分析的結論隨之失效。

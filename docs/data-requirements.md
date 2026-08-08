# 驗證資料:取得經過、遇到的問題與重現方式

> 狀態:**✅ 已取得 / RESOLVED(2026-08-08)。** BTC/USDT 與 ETH/USDT 各 3,271 根日 K(2017-08-17 ~ 2026-07-31),已通過 [`backtest-procedure.md`](./backtest-procedure.md) 1.4 節全部品質檢查。
> 日期:2026-08-08

---

## 0. 結果

| | |
|---|---|
| 交易對 | BTC/USDT、ETH/USDT(幣安現貨,1d) |
| 範圍 | **2017-08-17 ~ 2026-07-31**(約 **9.0 年**) |
| 筆數 | 各 **3,271** 根 |
| 缺漏 | **0**(孤立 0、連續 0) |
| 疑似異常 K 棒 | **0** |
| 完整性 | 108 個月封存逐檔 SHA256 對照官方 `.CHECKSUM`,全數通過 |

**合理性抽查** —— 校驗和只能證明「和幣安發布的一致」,不能證明「內容是真實市場」。對照已知歷史事件:

| 日期 | 事件 | BTC 實際值 |
|---|---|---|
| 2017-12-17 | 2017 泡沫頂 | high 19,798.7 |
| 2020-03-12 | 312 崩盤 | low 4,410.0 |
| 2021-11-10 | 2021 ATH | high **69,000.0** |
| 2022-11-09 | FTX 崩潰 | low 15,588.0 |

四個地標全部吻合。

**9.0 年比 [`feasibility-paths.md`](./feasibility-paths.md) 方案 F 假設的 8.7 年還多**,可行性結論不受影響(門檻只會略低於 1.20)。

> ⚠️ **資料檔不進 git**(`user_data/data/` 已被 ignore),且**容器是暫時性的,session 結束就會消失**。`user_data/data/checksums/` 下的 216 筆校驗和已納入版控,任何時候都能依第 4 節重新取得**位元組完全相同**的資料。

---

## 1. 網路:白名單生效了,但幣安擋住了另一半

白名單放行後實測:

| 端點 | 結果 |
|---|---|
| `data.binance.vision` | ✅ **200** |
| `api.binance.com` | ⚠️ **451** |

**451 不是環境擋的。** 代理回應 `Connection Established` 後才收到 451,且代理的失敗紀錄沒有新增項目 —— 這個拒絕來自幣安自己的 CloudFront(POP `ORD56`,芝加哥):

```json
{"code": 0, "msg": "Service unavailable from a restricted location
 according to 'b. Eligibility' in https://www.binance.com/en/terms."}
```

容器的出口 IP 位於幣安服務條款的限制地區。**這是幣安的地區限制,白名單改不動,也不該去繞。**

### 1.1 因此 `freqtrade download-data` 用不了

Freqtrade 2026.7 雖然也會用 `data.binance.vision`,但兩處都會撞上 451:

1. `get_historic_ohlcv_fast()` 需要 `markets=self.markets`,而 `load_markets()` 走 `api.binance.com`
2. 月封存只到前一日,最近幾根 K 棒會回退到 REST API

所以改為直接抓月封存 ZIP,不使用 REST API —— **這不是規避:兩個網域都在白名單內,451 是幣安依其條款做的限制,本專案沒有、也不嘗試繞過它,只是不使用那個端點。**

---

## 2. 取得管線

三個工具,職責分開:

```
analysis/tools/download_binance_vision.py   取得 + 完整性(SHA256)
                  ↓  6 欄 CSV
analysis/tools/ingest_market_data.py        1.4 節品質閘門 + 來源留痕
                  ↓  feather
user_data/data/binance/                     Freqtrade 直接可讀
```

下載工具**只負責取得與完整性,不做品質判斷**;缺漏與異常值一律交給 `analysis/data_quality.py`。這樣「資料哪來的」和「資料乾不乾淨」是兩個可以分別檢驗的問題。

---

## 3. 過程中抓到的兩個真實 bug

兩個都是**不會報錯、但會讓統計結論失去意義**的類型 —— 與 [`pipeline-findings.md`](./pipeline-findings.md) 的 `MAX_LOSS` 哨兵值汙染同一種。

### 3.1 🔴 幣安在 2025-01-01 把封存時間戳從毫秒改成微秒

BTCUSDT 1d 全量下載後:**2017-08~2024-12 共 2,694 列是毫秒(~1e12),2025-01 之後 577 列是微秒(~1e15)—— 同一份資料集混用兩種單位。**

原本的 `_detect_epoch_unit()` 用中位數推斷整份資料的單位,會選中毫秒(多數派),**那 577 列微秒資料會被解讀成西元五萬年**。

兩層都修了:

- **下載端**逐列依數量級正規化成毫秒(這是幣安已知的行為,下載工具有責任處理)
- **匯入端**改為:偵測到混用單位就**明確拒收**,不取多數決 —— 猜錯不報錯但會毀掉整份資料,不該由工具替使用者猜

### 3.2 🟡 秒 vs 毫秒:塌縮到 1970 年會讓缺漏偵測變成空操作

冒煙測試時發現,以秒為單位的時間戳會被當成毫秒,日期全部塌到 1970 年。危險的不是日期錯,而是**日期塌縮到同一天後,規則 1 的缺漏偵測期望範圍只剩一天、差集必為空** —— 閘門完全失效,畫面上還印「✅ 通過」。

檢查端因此獨立加了一條「日期早於比特幣創世或落在未來就擋下」的防線,不依賴匯入端猜對。

### 3.3 🟡 邊界時間 tz-aware 導致整條管線當掉

`pd.Timestamp(x, tz="UTC")` 在 `x` 已帶時區時會拋 `ValueError`。測試只傳過 tz-naive,而 CLI 傳的是 tz-aware —— 真實下載時直接崩潰。

**這次是 fail closed 的**(exit 1,什麼都沒寫入),但仍是實作缺陷。已修並補上兩種輸入的參數化回歸測試。

---

## 4. 如何重現

```bash
# 1. 下載(每個交易對約 3 分鐘,108 個月逐檔驗 SHA256)
.venv/bin/python analysis/tools/download_binance_vision.py \
    --pair BTCUSDT --start 2017-08 --end 2026-07 --out /tmp/dl/BTCUSDT-1d.csv
.venv/bin/python analysis/tools/download_binance_vision.py \
    --pair ETHUSDT --start 2017-08 --end 2026-07 --out /tmp/dl/ETHUSDT-1d.csv

# 2. 品質閘門 + 寫入(1.4 節的強制步驟)
for P in BTC ETH; do
  .venv/bin/python analysis/tools/ingest_market_data.py \
      --pair $P/USDT --input /tmp/dl/${P}USDT-1d.csv \
      --expected-start 2017-08-17 --expected-end 2026-07-31 \
      --note "data.binance.vision 月封存,逐檔 SHA256 驗證通過"
done

# 3. 比對已納入版控的校驗和,確認位元組相同
diff <(sort /tmp/dl/BTCUSDT-1d.sha256) <(sort user_data/data/checksums/BTCUSDT-1d.sha256)
```

### 4.1 閘門行為

| 情況 | 行為 |
|---|---|
| 結構性錯誤(欄位缺失、`high < max(open,close)`、日期無時區、重複/未排序、非正價格) | ❌ 擋下 |
| 日期不合理(早於比特幣創世、或在未來) | ❌ 擋下 |
| 時間戳混用多種單位 | ❌ 擋下 |
| 連續 ≥3 日缺漏(規則 2 第三級) | ❌ 擋下 |
| 孤立缺漏(規則 2 第一級) | ⚠️ 擋下並要求先重下;確實重下過仍缺才用 `--already-redownloaded` |
| 量價背離的價格尖刺(規則 3) | ⚠️ 警示,需人工比對其他資料源 |

**沒有 `--force`,也沒有 forward-fill / 插值的能力** —— 不是沒做,是刻意不提供。1.4 節規則 2 明訂 forward-fill 會捏造一根從未存在的價格;能力不存在,才不會在趕時間時被說服使用。有測試把關(`test_module_provides_no_data_repair_functions`)。

---

## 5. ⛔ 下一步的順序不能顛倒

資料已經在手上,**這使得預先登錄的時間窗口正在關閉**。依 [`feasibility-paths.md`](./feasibility-paths.md) 5.3 節與 [`CP-001`](./change-proposals/CP-001-hyperopt-epochs.md) 第 3 節:

- ⬜ **先**決定是否採用方案 F,寫成 CP-003
- ⬜ **先**把 5 個策略參數中的 4 個事前固定,寫下每個值的來源依據(公開先驗知識 vs. 我方推理),commit 進 git 留下時間戳
- ⬜ **然後才**執行 Pass A / Pass B

**參數一旦在看過真實資料之後才定,`N` 就不再是 5,[`feasibility-paths.md`](./feasibility-paths.md) 的整個可行性結論隨之失效。** git commit 的時間戳是這件事唯一的客觀證據,而它只在參數先於回測 commit 時才成立。

⚠️ 我方目前**尚未對這份資料跑過任何回測、任何指標、任何統計量**。截至本文件寫成,除了第 0 節那四個地標價格之外,沒有看過這份資料的任何性質 —— 這一點本身就是預先登錄有效性的一部分,記錄在此。

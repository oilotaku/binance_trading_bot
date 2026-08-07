# Phase 8 — 技術選型決策 / Tech Stack Decision

> 狀態:**✅ 已決定。** 本決策提前於 Phase 2-7 拍板,原因見下方說明。負責角色:system-architect(綜合 execution-engineer、risk-manager、backtest-analyst 的需求)。

## 決策:以 Freqtrade 為基礎框架開發,不從零自建

依 `reference-research.md` 的調查(Freqtrade ~48K star,持續維護,架構與本專案 Phase 1 的訊號/執行分離原則完全吻合),團隊決定採用 **選項 B**:以 Freqtrade 作為執行引擎、回測引擎、風控熔斷的基礎設施,自己只寫策略邏輯(`populate_indicators` / `populate_entry_trend` / `populate_exit_trend`)與客製化風控參數。

## 具體技術棧

| 項目 | 選擇 | 理由 |
|---|---|---|
| **語言** | Python 3.11+ | Freqtrade 原生語言,量化生態系(pandas/numpy/ta-lib)最成熟,與 Phase 2 統計驗證方法論(quant-mathematician 慣用工具鏈)直接相容 |
| **交易框架** | Freqtrade | 免重造執行引擎、回測引擎、風控熔斷;策略介面已強制「訊號生成與執行分離」,天然避免我們 Phase 1 擔心的架構混亂 |
| **交易所串接** | ccxt(Freqtrade 內建) | 已處理 rate limit、WebSocket、REST 抽象化,對應 Phase 7 execution-engineer 原本要自己寫的大部分工作 |
| **運行模式** | Freqtrade dry-run(對應 Phase 0 的模擬資金決策) | 內建功能,不需自己開發 paper trading 層 |
| **風控熔斷** | Freqtrade Protections(StoplossGuard、MaxDrawdown、CooldownPeriod)+ 自訂 MaxDrawdown 門檻對齊 `scope.md` 的 15–20% | 直接沿用 `reference-research.md` 列出的四種機制,只需調參數,不需自己實作熔斷邏輯 |
| **資料儲存** | Freqtrade 預設(SQLite 存交易紀錄,feather/parquet 存 K 線)| 免自建資料庫層 |
| **回測** | Freqtrade backtesting + hyperopt + lookahead-analysis | 內建 lookahead bias 自動檢測,直接對應 Phase 6 backtest-analyst 的驗證需求 |
| **部署** | Freqtrade 官方 Docker image,單機/單一 VPS 常駐 | 波段頻率(Phase 0 已確認)不需要低延遲基礎設施,單機部署足夠 |
| **市場模式** | Freqtrade spot 模式(現貨) | 對齊 `scope.md` 的「第一版只做現貨」決策,Freqtrade 設定檔中關閉 futures/leverage 即可 |

## 對後續 Phase 的影響

這個決策會讓 Phase 3(架構規格)、Phase 6(回測程序)、Phase 7(執行層規格)的範圍大幅縮小 —— 這三個階段**不再是「設計一個新系統」,而是「決定如何在 Freqtrade 的框架邊界內配置與客製化」**:

- **Phase 3(架構規格)** 改為:定義策略類別的目錄結構、自訂 Protections 參數如何對齊 `risk-policy.md`、日誌與監控如何接到 Freqtrade 的 webhook/Telegram RPC。
- **Phase 6(回測程序)** 改為:定義歷史資料下載範圍、Freqtrade backtesting 的成本模型參數(手續費、滑價設定)、hyperopt 的參數搜尋範圍與過擬合防線如何設定。
- **Phase 7(執行層規格)** 改為:定義 Freqtrade 設定檔(`config.json`)裡與交易所連線相關的參數、API 金鑰的環境變數注入方式,而非重新設計冪等下單/斷線重連邏輯。

**Phase 1(策略假說)與 Phase 2(數學驗證)不受影響** —— 這兩個階段的內容(3 個候選策略、統計驗證方法論)完全可以直接轉譯成 Freqtrade 策略程式碼,不需要因為換框架而重新設計。

## 為什麼提前拍板(跳過原訂順序)

開發計劃原訂 Phase 8 應在 Phase 1-7 都完成後才決定。但技術棧選擇是所有後續規格文件(尤其 Phase 3/6/7)能不能寫得具體的前提 —— 若不先定案,這三份文件會寫得空泛(因為不知道是在「設計新系統」還是「配置既有框架」)。因此提前決定,Phase 3/6/7 將依此決策撰寫,不再是通用規格,而是「Freqtrade 客製化規格」。

## 尚未解決的問題

- **是否 fork Freqtrade 原始碼,還是以 pip 套件方式引用 + 獨立的 strategies 目錄?** 建議後者(pip 安裝 + `user_data/strategies/` 放自訂策略),保留未來跟隨上游更新的彈性,除非後續發現需要修改框架核心行為。此問題留到 Phase 3 詳細架構規格時定案。

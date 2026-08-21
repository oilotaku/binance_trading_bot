# 開發計劃與流程 / Development Plan & Process

> 原則:**先完成所有規劃文件,最後才開始寫程式。** 每個階段都有明確的負責角色、產出文件、與「可以進入下一階段」的離開條件(exit criteria)。在 Phase 0-9 全部通過前,不寫任何策略/執行/風控程式碼。
>
> Principle: **All planning documents are completed first; code is written last.** Each phase has a clear owner role, deliverable documents, and exit criteria for moving to the next phase. No strategy/execution/risk code is written until Phases 0–9 are all signed off.

本計劃銜接 [`stable-profitability-roadmap.md`](./stable-profitability-roadmap.md) 中 8 個角色提出的功能需求,將其轉化為一套有順序、有把關點的規劃流程。

This plan takes the feature requirements each of the 8 roles raised in [`stable-profitability-roadmap.md`](./stable-profitability-roadmap.md) and turns them into an ordered, gated planning process.

---

## 規劃階段總覽 / Planning Phases Overview

| # | 階段 | 負責角色 | 產出(文件,非程式碼) |
|---|---|---|---|
| 0 | 目標與範圍界定 | 專案負責人(您) | `docs/scope.md` |
| 1 | 策略假說與經濟邏輯 | market-economist, quant-strategist | `docs/strategy-hypothesis.md` |
| 2 | 數學與統計方法論 | quant-mathematician | `docs/statistical-methodology.md` |
| 3 | 系統架構規格 | system-architect | `docs/architecture-spec.md` |
| 4 | 風控政策 | risk-manager | `docs/risk-policy.md` |
| 5 | 安全政策 | trading-security-reviewer | `docs/security-policy.md` |
| 6 | 回測框架與驗證程序 | backtest-analyst | `docs/backtest-procedure.md` |
| 7 | 執行層技術規格 | execution-engineer | `docs/execution-spec.md` |
| 8 | 技術選型決策 | system-architect(綜合以上規格) | `docs/tech-stack-decision.md` |
| 9 | 開發前 Go/No-Go 檢查清單 | 全體角色會審 | `docs/go-no-go-checklist.md` |

> **9 通過後才進入實作(Phase 10+)。**

---

## Phase 0 — 目標與範圍界定 / Goals & Scope

**負責:** 專案負責人
**產出:** `docs/scope.md`

**需回答的問題:**
- 目標報酬與可接受的風險是什麼?(例如:年化目標、可接受最大回撤上限)
- 初期資金規模?這會決定 Binance API 的手續費等級與滑價敏感度。
- 交易哪些市場?現貨(spot)、USDT 永續合約(perpetual futures),還是兩者?
- 交易頻率量級?(日內高頻 / 波段 / 中長線)— 這決定後續架構與延遲要求差異極大。
- 誰是唯一有權限調整風控參數與啟停機器人的人?

**離開條件:** 以上問題都有明確答案並寫成文件,而非「之後再說」。

---

## Phase 1 — 策略假說與經濟邏輯 / Strategy Hypothesis & Economic Rationale

**負責:** market-economist(市場機制)+ quant-strategist(訊號邏輯框架)
**產出:** `docs/strategy-hypothesis.md`

**內容要求:**
1. 每個候選策略都要寫出「賺的是誰的錢」(edge hypothesis)— 做市價差?資金費率套利?動能延續?
2. 該策略預期在哪些市場狀態(regime)有效、哪些狀態會失效。
3. Regime detection 要用什麼指標判斷(波動率、資金費率、OI 等)。
4. 資金費率(funding rate)在此策略中是訊號還是成本,如何量化。
5. 相關性叢集風險評估 — 若同時運行多策略,risk-off 時是否會同方向擠壓。

**離開條件:** 至少一個策略有完整、可被推翻(falsifiable)的經濟假說文件,而非只有「回測賺錢」這個理由。

---

## Phase 2 — 數學與統計方法論 / Statistical Methodology

**負責:** quant-mathematician
**產出:** `docs/statistical-methodology.md`

**內容要求:**
1. 統計顯著性檢定方法定案(Deflated Sharpe Ratio / White's Reality Check 等),明確公式與判斷門檻。
2. Walk-forward 驗證的切分規則(訓練/測試視窗長度、embargo 期間、是否 purge)。
3. 有效樣本數校正方法(Newey-West / block bootstrap)— 明確何時交易報酬需視為自相關。
4. 倉位大小數學模型定案(Fractional Kelly 的分數、或其他 sizing 公式),以及對 edge 估計誤差的敏感度分析方法。
5. 回撤機率估計方法(蒙地卡羅模擬規格:重採樣方法、模擬次數、信賴區間)。

**離開條件:** 每個 Phase 1 的候選策略,都有對應的統計驗證方法與判斷門檻(而非「看起來不錯就上」)。

---

## Phase 3 — 系統架構規格 / System Architecture Spec

**負責:** system-architect
**產出:** `docs/architecture-spec.md`

**內容要求:**
1. 模組劃分圖:資料層 / 策略層(訊號)/ 風控層 / 執行層 / 監控層,以及彼此的介面定義(輸入輸出格式)。
2. 訂單狀態機設計(掛單/成交/取消/拒絕的狀態轉換)與本地帳本 vs. 交易所對帳的頻率、方法。
3. 崩潰恢復流程:進程重啟後如何重建持倉、掛單、策略內部狀態。
4. 日誌與可觀測性規格:哪些事件必須被記錄(訊號產生、風控否決、下單、成交、錯誤),記錄格式與保存位置。
5. Kill switch 的觸發條件清單與其在架構中的位置(必須獨立於策略程式碼,說明如何做到「不可被繞過」)。
6. Paper trading / Backtest / Live 三種模式如何共用同一套執行介面的設計方式。

**離開條件:** 架構圖與介面定義完整到「不同角色可以各自平行開發模組,串接時不需要重新協調介面」的程度。

---

## Phase 4 — 風控政策 / Risk Policy

**負責:** risk-manager
**產出:** `docs/risk-policy.md`

**內容要求(每項都要給出具體數字,不能只寫「合理範圍」):**
1. 單筆交易風險上限(帳戶淨值 %)與計算公式(如何依 ATR/波動度換算實際下單量)。
2. 每個策略的強制出場規則:停損 %、停利 %、最大持倉時間 — 逐策略列出,不能有例外。
3. 帳戶級每日虧損熔斷門檻,觸發後的行為(停止當日新開倉 / 全部平倉 / 需人工介入才能恢復)。
4. 總曝險上限與同方向部位相關性上限。
5. Kill switch 的最終回撤門檻(如 -15%)與觸發後的具體行為。
6. 風控參數的變更流程:誰可以改、改了要不要重新走驗證流程。

**離開條件:** 這份文件本身就是之後 risk-manager 寫程式碼時的規格書,不需要再回頭問「這個數字應該是多少」。

---

## Phase 5 — 安全政策 / Security Policy

**負責:** trading-security-reviewer
**產出:** `docs/security-policy.md`

**內容要求:**
1. Binance API 金鑰建立規範:權限範圍(絕不含提現)、是否搭配 IP 白名單、金鑰輪替週期。
2. 秘密管理規範:`.env` 使用方式、`.gitignore` 驗證程序(如何定期確認未被 commit)。
3. 日誌脫敏規則:哪些欄位必須遮蔽。
4. Testnet / Mainnet 區隔機制的具體實作要求(啟動時如何強制顯示目前環境、是否需要二次確認)。
5. 訂單參數驗證規則(數量/金額/槓桿上限),防止胖手指或策略失控。
6. Incident response:若懷疑金鑰外洩或機器人異常下單,標準處理步驟(先做什麼、通知誰、如何緊急停止)。

**離開條件:** 有一份「金鑰外洩時該做什麼」的具體步驟文件,而不是臨時想辦法。

---

## Phase 6 — 回測框架與驗證程序 / Backtest Framework & Validation Procedure

**負責:** backtest-analyst
**產出:** `docs/backtest-procedure.md`

**內容要求:**
1. 歷史資料來源、時間範圍、資料清洗規則(缺漏值、異常值處理)。
2. 交易成本模型:手續費費率、資金費率、滑價模型、部分成交模擬方式。
3. 必須輸出的績效指標清單(對齊 Phase 2 的統計方法論):總報酬、年化、Sharpe/Sortino、MDD、勝率、賺賠比、Profit Factor、交易次數。
4. 策略「通過驗證」的具體門檻(如:樣本外 Sharpe 需 > X 且 Deflated Sharpe p-value < Y,MDD 不超過 Phase 0 設定的上限)。
5. Paper trading 銜接規則:回測通過後,需在 Testnet/模擬單觀察多久、比對哪些指標(回測 vs 實際滑點差異)才能進入下一階段。

**離開條件:** 有一套任何策略都要走過的標準化驗證流程與明確的通過/不通過門檻,不是每次都憑感覺判斷。

---

## Phase 7 — 執行層技術規格 / Execution Layer Spec

**負責:** execution-engineer
**產出:** `docs/execution-spec.md`

**內容要求:**
1. Binance API 串接方式(REST/WebSocket)、client order ID 產生規則(確保冪等性)。
2. Rate limit 處理策略:退避演算法、請求佇列設計。
3. WebSocket 斷線重連與狀態重同步的具體步驟。
4. 對帳(reconciliation)的觸發時機與比對邏輯,不一致時的處理方式(對齊 Phase 3 架構規格)。
5. 各類 API 錯誤碼(餘額不足、精度錯誤、市場閉市等)的處理分支與告警規則。

**離開條件:** 這份規格足以讓不同技術棧的實作者都能做出行為一致的執行層。

---

## Phase 8 — 技術選型決策 / Tech Stack Decision

**負責:** system-architect(綜合 Phase 1-7 的實際需求做決策)
**產出:** `docs/tech-stack-decision.md`

**內容要求:**
1. 依 Phase 0 的交易頻率量級與 Phase 3-7 的規格,評估候選語言/框架(如 Python + ccxt/python-binance,或 Node.js/TypeScript + ccxt)。
2. 明確列出選擇理由與捨棄的替代方案原因,而非只寫結論。
3. 資料儲存方案(時序資料庫 / 一般 SQL / 檔案)。
4. 部署方式(本機常駐 / VPS / 容器化)與其如何滿足 Phase 3 的「崩潰恢復」需求。

**離開條件:** 技術選型有文件記錄的理由,之後不會因為「換個語言重寫」而打掉重練。

---

## Phase 9 — 開發前 Go/No-Go 檢查清單 / Pre-development Gate

**負責:** 全體角色會審(或您本人對照文件逐項確認)
**產出:** `docs/go-no-go-checklist.md`

在開始寫任何程式碼之前,逐項確認:

- [ ] Phase 0-8 的所有文件都已完成並存在 `docs/` 目錄
- [ ] 至少一個策略有完整、可證偽的經濟假說(Phase 1)
- [ ] 該策略的統計驗證方法與通過門檻已定案(Phase 2)
- [ ] 系統架構圖與模組介面已定義,各角色能各自平行開發(Phase 3)
- [ ] 風控政策的每個數字都已寫死在文件裡,不是「之後再調」(Phase 4)
- [ ] 安全政策涵蓋金鑰管理、密鑰外洩應變流程(Phase 5)
- [ ] 回測框架的成本模型與通過門檻已定案(Phase 6)
- [ ] 執行層規格涵蓋冪等性、重連、對帳(Phase 7)
- [ ] 技術棧已選定並有文件記錄理由(Phase 8)

**只有全部打勾,才進入 Phase 10 開始寫程式。**

---

## Phase 10+ — 實作與上線後程序(規劃階段完成後才會啟動)/ Implementation & Post-Launch Procedures (only after all planning phases pass)

以下是規劃階段完成、開始寫程式後會依循的後續流程,先列出來以確保規劃階段的文件有考慮到位,但**目前尚未啟動**:

10. **依規格分模組實作** — 各角色依自己 Phase 產出的規格書寫程式碼,而非重新設計。
11. **Testnet 驗證** — 依 Phase 6/7 規格在測試網跑滿一段時間,確認行為與規格一致。
12. **Paper trading** — 接實盤行情、模擬下單,比對與回測的滑點/延遲差異。
13. **小額實盤試運行** — 依 Phase 4 風控政策,用最小允許倉位規模實盤運行,觀察是否符合預期。
14. **正式營運與監控** — 依 Phase 3 的可觀測性規格持續監控,並依 Phase 1 的 alpha decay 監控機制定期檢視策略是否仍然有效。

---

## 目前進度 / Current Status

- [x] 8 個專職角色已建立(`.claude/agents/`)
- [x] 跨角色功能需求討論已完成(`stable-profitability-roadmap.md`)
- [x] 本開發計劃文件已建立
- [x] Phase 0 — 目標與範圍界定(`scope.md`,✅ 已確認)
- [x] Phase 1 — 策略假說與經濟邏輯(`strategy-hypothesis.md`,✅ 已確認:v1 聚焦策略一「動能突破」,其餘列為 backlog)
- [x] Phase 2 — 數學與統計方法論(`statistical-methodology.md`,✅ 已確認)
- [x] Phase 3 — 系統架構規格(`architecture-spec.md`,✅ 已確認)
- [x] Phase 4 — 風控政策(`risk-policy.md`,✅ 已確認)
- [x] Phase 5 — 安全政策(`security-policy.md`,✅ 已確認)
- [x] Phase 6 — 回測框架與驗證程序(`backtest-procedure.md`,✅ 已確認)
- [x] Phase 7 — 執行層技術規格(`execution-spec.md`,✅ 已確認)
- [x] Phase 8 — 技術選型決策(`tech-stack-decision.md`,✅ 已確認:以 Freqtrade 為基礎框架,不從零自建)
- [x] Phase 9 — Go/No-Go 檢查清單(`go-no-go-checklist.md`,✅ 已確認,結論 🟢 GO)

**Phase 0-9 規劃階段全數結案(2026-08-07)。下一步:Phase 10 實作,依各角色規格分模組開發。**

### Phase 10 進度(2026-08-08 起)

- [x] Freqtrade 2026.7 安裝(`.venv/`,不進 git)
- [x] `user_data/` 骨架(`freqtrade create-userdir` 標準結構)+ `user_data/configs/` 客製分層設定
- [x] `.gitignore` 補上 `secrets-*.json`/`deploy/*.env` 規則,已實測驗證正確排除機密、保留範例檔
- [x] 策略骨架 `RegimeFilteredMomentumBreakout.py`:訊號邏輯、`custom_stoploss`、`custom_stake_amount`、`custom_exit`(time-stop)、`confirm_trade_entry`(每日熔斷)、`protections` 皆已實作並**通過真實 `StrategyResolver`/`ProtectionManager` 載入驗證**(非僅語法檢查)
- [x] 過程中順帶解決 3 項 `go-no-go-checklist.md` 4.1 節列出的待查證項目(protections 歸屬、MaxDrawdown 雙實例並存、`stop_duration_candles` 換算),詳見該文件更新
- [x] ~~卡點:出口網路被擋,無法連線 Binance API~~ → **2026-08-16 起已解決**:`analysis/tools/download_binance_vision.py` 繞開 `api.binance.com` 451(改走不受地區限制的 `data.binance.vision` 官方封存,見該檔案 docstring),真實 BTC/ETH 9 年資料已下載匯入並通過品質檢查;backtesting/hyperopt 透過 `analysis/offline_exchange.py` 的 `offline_exchange()` context manager 繞開 CLI 模式下 `reload_markets()` 仍會撞到的 451,已在 CP-003/CP-007/CP-008/策略五評估等多次真實回測中驗證可用。策略一、四、五均已對真實資料完成正式判定,見 README.md「驗證結果」。
- [x] **2026-08-21 新發現:`api.binance.com`、`testnet.binance.vision` 現在皆可直接連通(200 OK),不再是 451 地區限制。** 但用 `freqtrade list-pairs`/`trade` 這類需要 `reload_markets()` 的即時連線指令實測,發現新的卡點:即使只是讀取市場中繼資料,ccxt 的 Binance 實作也要求有效的 `apiKey`(`AuthenticationError: binance requires "apiKey" credential`)。**這代表要真正接上即時 API(即使是 dry-run),需要專案負責人自行到 Binance 申請一組 API 金鑰**(依 `security-policy.md` 第 1 節規格:Enable Reading、Enable Spot & Margin Trading 開啟,Enable Withdrawals/Futures/Margin Loan 關閉,建議加 IP 白名單),這是 AI 助理無法代為完成的步驟。在此之前,已優先把其餘執行層安全機制(啟動前檢查腳本、胖手指防護、日誌脫敏)補齊,讓金鑰到位後可以直接接上,不需要再回頭補安全機制。
- [x] `analysis/` 目錄下 `backtest-procedure.md` 第 8 節規劃的 9 個統計驗證模組(DSR、Newey-West、Kelly、蒙地卡羅回撤等)已實作,**23 個 pytest 測試全數通過**,含 `statistical-methodology.md` 兩個數值示例(DSR≈0.80、Kelly risk_fraction≈7.07%)的精確重現;自訂 hyperopt loss(`PurgedTradeSharpeLoss`)已通過 Freqtrade 真實 resolver 驗證可載入
- [x] 合成資料管線整合測試(`analysis/tools/`):端到端跑過 backtesting → hyperopt → analysis,**找出 2 個會靜默產生錯誤結論的缺陷並修正**(MAX_LOSS 哨兵值汙染 sigma_SR 使 DSR 恆為 0;hyperopt 參數檔為共用可變狀態,天真的 runner 會讓 walk-forward 失效),完整記錄於 `docs/pipeline-findings.md`
- [x] walk-forward 執行器(`analysis/tools/run_walk_forward.py`):Pass A 校準 → Pass B 正式輪全自動化,三個關鍵約束寫進程式結構並有迴歸測試;支援 `--resume` 中斷續跑
- [x] ~~完整 9-fold walk-forward 執行~~ → **方法論已由 [`CP-003`](./change-proposals/CP-003-fixed-parameters.md) 取代,未使用 `run_walk_forward.py` 這條路徑**:CP-003 把 hyperopt 搜尋(200 epochs × 9 fold)換成「4 個參數事前固定 + 1 個參數(`donchian_period`)掃描 5 個點,`N=5`」,理由是降低研究者自由度、讓 DSR 門檻更容易被跨過。策略一的正式判定([`pass-b-results.md`](./pass-b-results.md))就是走這條簡化後的路徑,**已對真實資料完成**,結果 PSR=0.9397 < 0.95 門檻,未通過(且 `pass-b-results.md` 已確認:即使 `N=1` 完全不罰,PSR 仍不通過——問題不是搜尋懲罰,是策略本身沒有 edge)。`run_walk_forward.py` 本身仍保留在 `analysis/tools/`(9 項迴歸測試通過),留待未來若有策略需要走完整 walk-forward 校準時使用,但目前三個已判定策略(一、四、五)都沒有用到它。
- [x] ~~Kelly 倉位公式的 `f*` 待真實資料~~ → **策略一未通過驗證,不會進入實盤,`custom_stake_amount` 沿用 `risk-policy.md` 硬上限的決定維持不變,不再需要補 `f*`。** 若未來 backlog 策略(二、三)通過驗證,屆時依同樣邏輯用該策略的真實 OOS Sharpe/波動度重新計算。
- [x] **策略四(波動度目標化)、策略五(Kalman 濾波出場)已依各自的 Phase 1 假說文件走完整流程,對真實資料完成正式判定**:兩者第一層(回撤控制)通過、第二層(超額報酬)不通過,詳見 [`strategy-4-cp007-results.md`](./strategy-4-cp007-results.md)、[`strategy-5-results.md`](./strategy-5-results.md)。過程中新增 `CP-006`、`CP-007`、`CP-008` 三個變更提案(`σ_target` 兩次修正 + 策略五風控重新校準)。三個已判定策略均未達可投入真實資金標準,不主張 edge,見 README.md「驗證結果」。
- [x] `user_data/strategies/` 下三個策略類別(`RegimeFilteredMomentumBreakout`、`VolatilityTargeting`、`TrendFilterExit`)均已通過真實 `StrategyResolver` 載入驗證。
- [ ] **執行層安全機制(`security-policy.md` 第 3-5 節)** — 啟動前檢查腳本(環境橫幅 + live 強制打字確認)、胖手指防護層(獨立於計算路徑的硬上限裁剪 + 輸入合理性檢查)、日誌脫敏過濾器,2026-08-21 起實作中(見本文件版本歷史後續更新)。
- [ ] **接上即時 API(即使是 dry-run)的唯一剩餘阻塞項:需要專案負責人自行申請 Binance API 金鑰**(規格見 `security-policy.md` 第 1 節),AI 助理無權限、也不應該代為完成這一步。金鑰到位、填入 `secrets-testnet.json` 後,配合上一項的安全機制即可啟動。

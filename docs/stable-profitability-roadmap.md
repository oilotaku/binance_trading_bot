# 穩定獲利功能討論紀錄 / Stable Profitability Feature Discussion

由專案內 8 個專職角色(system-architect、quant-strategist、backtest-analyst、execution-engineer、risk-manager、trading-security-reviewer、market-economist、quant-mathematician)分別從各自專業角度,針對「機器人如何達到穩定、可持續的獲利」提出的具體功能需求。

A cross-functional discussion among this project's 8 specialist roles, each proposing concrete features/requirements — from their own domain — for the bot to achieve **stable, sustained** profitability (not a one-off lucky run).

---

## 共識重點(跨角色高度重複提及)/ Cross-cutting priorities

多個角色獨立提到同一件事,代表這些是最優先要做的:

- **Kill switch / 熔斷機制**:system-architect、risk-manager、trading-security-reviewer 都強調,必須是獨立於策略程式碼之外、不可被繞過的系統層級開關。
- **本地狀態與交易所對帳(reconciliation)**:system-architect、execution-engineer 都指出,本地帳本必須定期與交易所實際狀態校正,否則長期運行必產生認知偏差。
- **每個策略要有明確出場路徑**:quant-strategist、risk-manager 都指出沒有停損/停利/最大持倉時間的策略不能上線。
- **避免 lookahead bias、防止過擬合**:quant-strategist、backtest-analyst、quant-mathematician 都強調樣本外驗證(walk-forward)的必要性。
- **市場狀態切換(regime detection)**:quant-strategist、market-economist 都認為單一策略無法全天候有效,需偵測趨勢/盤整並動態調整。
- **倉位大小要用數學方法而非直覺**:risk-manager 提出依 ATR/波動度動態換算,quant-mathematician 進一步指出應使用 **Fractional Kelly** 而非 Full Kelly,因為 edge 本身是估計值。
- **資金費率(funding rate)**:market-economist 指出這既是訊號也是隱藏成本,必須納入績效計算。
- **Testnet 優先 + API 金鑰最小權限**:execution-engineer、trading-security-reviewer 都強調預設 Testnet、金鑰絕不開提現權限。

---

## 各角色詳細意見 / Full discussion by role

### 🏗️ system-architect(系統架構)

1. **策略/執行/風控/資料四層嚴格解耦** — 任何一層改版不會波及其他層。
2. **訂單狀態機與本地帳本對帳** — 定期與交易所 REST 對帳,避免 state desync。
3. **斷線/崩潰後可安全恢復** — 啟動時必須能重建持倉與掛單狀態。
4. **全鏈路結構化日誌**(訊號→風控決策→訂單→成交) — 沒有可追溯的決策鏈,alpha decay 會被發現得太晚。
5. **Risk kill-switch 獨立於策略程式碼** — 系統層級、無法被策略邏輯繞過。
6. **Paper/Backtest/Live 共用同一套執行介面** — 避免「回測有效、實盤失效」來自程式碼路徑不一致。

### 📈 quant-strategist(量化策略)

1. **訊號生成與執行邏輯分離** — signal generation 是市場資料的純函數。
2. **嚴格禁止 lookahead bias** — 用未來資訊算出的指標在實盤會完全失效。
3. **參數外部化 + 敏感度分析** — 而非只在單一參數組合表現好。
4. **Walk-forward / out-of-sample 驗證** — 避免對歷史雜訊過擬合。
5. **策略需有明確的「為什麼有效」假說** — 講不出因果機制的策略標記為高風險。
6. **多策略/多市場狀態切換** — 單一策略在單一市場狀態失效是常態。

### 🔬 backtest-analyst(回測驗證)

1. **Walk-Forward / 滾動視窗驗證**。
2. **完整績效指標儀表板**(報酬、Sharpe、Sortino、MDD、勝率、賺賠比、Profit Factor)。
3. **交易成本與滑價模擬**(手續費、funding rate、部分成交)。
4. **過擬合防線**(蒙地卡羅重採樣、Deflated Sharpe Ratio)。
5. **對 Sharpe > 3 高度懷疑** — 通常代表資料洩漏或未計入尾部風險。
6. **Paper Trading 銜接層** — 回測通過後先接測試網觀察數週再上實單。

### ⚙️ execution-engineer(執行/連線)

1. **預設 Testnet + 分離金鑰管理**。
2. **冪等下單(Client Order ID)** — 避免重試造成重複下單。
3. **WebSocket 斷線重連 + 狀態重同步**。
4. **Rate Limit 退避與請求佇列** — 避免觸發 418/429 被封 IP。
5. **本地帳本與交易所對帳** — 不一致立即告警並暫停下單。
6. **部分成交/拒單/API 錯誤顯式處理** — 不能靜默吞掉例外。

### 🛡️ risk-manager(風險控管)

1. **倉位規模上限、明確可配置**(每筆風險固定為淨值 1-2%,依 ATR 動態換算)。
2. **每個策略必須有完整出場路徑**(停損+停利+time-stop)。
3. **帳戶級每日虧損熔斷**(如 -3% 即停止當日新開倉)。
4. **總曝險上限與相關性控管**。
5. **獨立於策略邏輯之外的 Kill Switch**(回撤觸及 -15% 強制平倉)。
6. **風控參數集中設定檔管理 + 啟動時驗證**。

> 「沒有第 3、5 點,其他都只是裝飾——沒有停損機制的策略遲早會遇到黑天鵝把獲利全部吐回去。」

### 🔒 trading-security-reviewer(交易安全)

1. **API 金鑰權限最小化** — 關閉提現權限。
2. **秘密管理與 .gitignore 實測** — 用 `git log --all --full-history` 確認金鑰從未被 commit。
3. **日誌脫敏** — log 需遮蔽 API Key/Secret/簽名參數。
4. **Testnet / Mainnet 明確區隔** — 啟動時強制印出目前環境並要求二次確認。
5. **訂單參數驗證與熔斷** — 防止胖手指或策略失控下超額單。
6. **一鍵緊急停止機制** — 秒級內停止所有新倉位。

### 🌍 market-economist(市場經濟)

1. **明確的機制假說(Edge Hypothesis)** — 講清楚「賺的是誰的錢」。
2. **機制辨識與切換(Regime Detection)** — 用波動率/funding/OI 判斷趨勢/盤整/高波動。
3. **資金費率作為訊號與成本** — 極端值預示反轉,也是持倉隱藏成本。
4. **相關性叢集風險** — risk-off 時加密資產幾乎同步下跌,傳統分散會失效。
5. **流動性碎片化與滑點感知** — 依實際訂單簿深度動態調整下單規模。
6. **Edge 衰減監控(Alpha Decay)** — 持續追蹤實際 vs 回測績效落差,設退場機制。

### 📐 quant-mathematician(數學/統計)

1. **樣本外統計顯著性檢定**(Deflated Sharpe Ratio / White's Reality Check)。
2. **Purged Walk-Forward 交叉驗證**(含 embargo,避免時間序列資訊洩漏)。
3. **有效樣本數校正** — 用 Newey-West 或 block bootstrap 估計真實 variance,而非直接用交易筆數。
4. **Fractional Kelly,非 Full Kelly** — edge 是估計值,Full Kelly 會系統性過度下注。
5. **滾動式參數估計 + 結構性斷點檢測**(如 CUSUM) — 市場非平穩,固定參數終將失效。
6. **蒙地卡羅模擬回撤分佈** — 估計破產機率與所需資金緩衝。

---

## 建議優先順序(下一步)/ Suggested build order

1. **風控 + 安全基礎設施先行**:kill switch、Testnet 預設、金鑰最小權限、每日虧損熔斷 — 這些是「不會爆帳」的底線,應該在任何策略上線前就存在。
2. **執行層基礎**:對帳機制、冪等下單、斷線重連 — 讓系統本身可信賴。
3. **一個簡單、有經濟假說的策略 + 完整回測(含 walk-forward、成本模擬)**:先求「正確」而非「複雜」。
4. **數學驗證層**:統計顯著性檢定、Fractional Kelly 倉位 — 在真金白銀上線前的最後把關。
5. **regime detection 與 alpha decay 監控**:待策略穩定運行一段時間後再迭代。

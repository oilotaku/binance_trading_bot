# Phase 9 — 開發前 Go/No-Go 檢查清單 / Pre-Development Gate

> 狀態:**✅ 已確認 / CONFIRMED。** 專案負責人已最終核准,Phase 0-9 規劃階段正式結案。
> 負責:全體角色會審(本文件由專案負責人與各角色文件對照彙整)
> 本文件是規劃階段(Phase 0-8)與實作階段(Phase 10+)之間的最後一道閘門。依 [`development-plan.md`](./development-plan.md) 的既有原則:**只有本文件全部項目確認通過,才能開始寫任何策略/執行/風控程式碼。**

---

## 1. Phase 0-8 逐項檢查(對照 `development-plan.md` 原始 9 項要求)

| # | 檢查項 | 狀態 | 依據 |
|---|---|---|---|
| 1 | Phase 0-8 的所有文件都已完成並存在 `docs/` 目錄 | ✅ **通過** | 9 份文件全部存在:`scope.md`、`strategy-hypothesis.md`、`statistical-methodology.md`、`architecture-spec.md`、`risk-policy.md`、`security-policy.md`、`backtest-procedure.md`、`execution-spec.md`、`tech-stack-decision.md` |
| 2 | 至少一個策略有完整、可證偽的經濟假說(Phase 1) | ✅ **通過** | `strategy-hypothesis.md`:v1 聚焦「Regime-Filtered Momentum Breakout」,經濟機制、失效情境(盤整市假突破)、信心評估(中)皆已明文 |
| 3 | 該策略的統計驗證方法與通過門檻已定案(Phase 2) | ✅ **通過** | `statistical-methodology.md` 第 7 節「Phase 2 通過門檻總表」:DSR≥0.95、OOS PSR≥0.95、Reality Check p<0.05、n_eff≥30、`P(MDD>20%)≤5%` 等 5 項門檻皆有具體數字 |
| 4 | 系統架構圖與模組介面已定義,各角色能各自平行開發(Phase 3) | ✅ **通過** | `architecture-spec.md` 第 1-3 節:模組邊界表、目錄結構、策略類別與 Freqtrade callback 對應表皆已定義 |
| 5 | 風控政策的每個數字都已寫死在文件裡,不是「之後再調」(Phase 4) | ✅ **通過** | `risk-policy.md` 第 0 節數字總覽表:`risk_fraction`1.5%/2.5%、ATR k∈[2.0,4.0]、time-stop 45天、每日熔斷-4%、kill switch -15% 等全部具體化,且每個數字都有推導過程 |
| 6 | 安全政策涵蓋金鑰管理、密鑰外洩應變流程(Phase 5) | ✅ **通過** | `security-policy.md`:第 1 節金鑰權限規範、第 6 節 IR-1/IR-2 事故應變 runbook 皆已具體化 |
| 7 | 回測框架的成本模型與通過門檻已定案(Phase 6) | ✅ **通過** | `backtest-procedure.md` 第 2 節成本模型(手續費、滑價假設)、第 5 節通過門檻(引用 `statistical-methodology.md` 第 7 節總表) |
| 8 | 執行層規格涵蓋冪等性、重連、對帳(Phase 7) | ✅ **通過,但方式與原始清單字面預期不同** | `execution-spec.md` 用「查證 Freqtrade 已提供什麼 + 誠實標出殘留缺口 + 定義補強機制」取代「重新設計一套執行引擎」——這是 Phase 8 技術選型決策後,對這條離開條件的**唯一合理滿足方式**,已在該文件第 10 節說明 |
| 9 | 技術棧已選定並有文件記錄理由(Phase 8) | ✅ **通過** | `tech-stack-decision.md`:Freqtrade + Python,理由、對其他 Phase 的影響、未決問題皆已記錄 |

**第 1 層檢查結論:9 項全部通過。**

---

## 2. 文件審閱狀態:已於 2026-08-07 最終確認

Phase 1-7 的 7 份文件原本狀態欄位皆為「🟡 草稿完成,待專案負責人審閱後確認」。專案負責人已於本文件彙整完成後,對全部文件表示無異議並明確核准(「Ok」),**全部 9 份規劃文件(含 `scope.md`、`tech-stack-decision.md`)狀態已同步更新為「✅ 已確認」**。這代表 Phase 0-9 規劃階段正式結案,不再是進行中的草稿。

---

## 3. 跨文件一致性檢查(本文件新增的檢查層,不在原始 9 項清單內)

在彙整過程中,發現以下需要記錄或處理的跨文件關係:

### 3.1 已由後續文件解決的懸案(無需動作,僅記錄結案)

| 懸案 | 提出文件 | 解決文件 | 結論 |
|---|---|---|---|
| Binance Testnet 端點覆寫語法 | `architecture-spec.md` 7.4 | `execution-spec.md` 第 6 節 | 給出標準做法(`sandbox: true`)+ 已知脆弱點 + 備援步驟,誠實標記精確語法待 Phase 10 實測 |
| `stop_duration` 計時單位 | `architecture-spec.md` 9 / `risk-policy.md` 5.4 | `execution-spec.md`(間接,透過原始碼查證慣例確立) | `risk-policy.md` 5.4 已定「寧多勿少」原則,`execution-spec.md` 未推翻此原則,Phase 10 依當時版本核實具體數字 |
| 環境變數 vs. `secrets-*.json` 覆寫優先序 | `security-policy.md` 7 | `execution-spec.md` 第 7 節 | **已確認:環境變數優先序高於設定檔**(官方文件明文) |
| 冪等下單的確切機制 | `architecture-spec.md`/`tech-stack-decision.md`(隱含假設) | `execution-spec.md` 第 1 節 | **修正**:機制不是「client order ID 去重」,而是「下單呼叫本身不自動重試」+「訊號層級既有持倉檢查」,見 3.2 節 |

### 3.2 需要您決定是否處理的措辭落差(不影響架構結論,僅文件精確度問題)

**`security-policy.md` 2.1 節寫「`secrets-*.json` 是 Freqtrade 讀取機密的唯一權威來源」,但 `execution-spec.md` 7.2 節查證後指出:實際生效優先序是環境變數(`deploy/*.env`)高於 `secrets-*.json`,後者在 Docker 部署情境下形同備援值。** 這不是安全風險(兩者不會同時生效互相打架,覆寫方向明確且單向),純粹是文件措辭與實際技術行為有落差。

**選項:**
- (a) 現在花幾分鐘請 trading-security-reviewer 回頭微調 `security-policy.md` 2.1 節的措辭,對齊 `execution-spec.md` 的查證結果
- (b) 不現在改,留一條紀錄在本文件,Phase 10 實作時一併處理(反正屆時要對照兩份文件寫程式碼,順手就會發現並修正)

**建議採 (b)**——這是純措辭精確度問題,不影響任何架構或安全決策,現在為此重新跑一次文件修訂流程的成本高於效益,列入第 4 節的 Phase 10 待辦即可。

### 3.3 `execution-spec.md` 對「冪等性已解決」假設的修正,是否需要回頭改其他文件?

**不需要。** `architecture-spec.md`/`tech-stack-decision.md` 當初的表述是方向性的(「Freqtrade 已處理冪等下單」),沒有錯到需要撤回的程度——`execution-spec.md` 查證後的結論(「不重試」比「重試但去重」更保守、結果相同:不會重複下單)**沒有推翻**原本的架構決策(仍然是「不需要我們自己設計冪等邏輯」),只是把「為什麼成立」的原因講得更精確,並多指出一個原本沒被明確意識到的殘留缺口(模糊結果窗口)。這屬於**知識精進**而非**架構修正**,不需要回頭改動 Phase 3/8 的決策本身,`execution-spec.md` 第 4 節已經針對這個殘留缺口設計了補強機制(定期原始餘額比對),缺口已有因應,不是懸而未決的風險。

---

## 4. Phase 10 實作前必須核實的項目總表(彙整全部文件的「已知限制」)

以下彙整 Phase 1-7 各文件「已知限制與交棒事項」章節中,明確要求 Phase 10(或 Phase 6/7 實作時)核實的項目,依性質分類,供實作階段作為單一檢查清單使用,不必再逐份文件翻找:

### 4.1 Freqtrade 版本相依,需在當時實際版本核實(中等風險——若有出入,通常是規格微調,不影響架構)

- ~~Protections 的確切設定機制是否仍是策略類別的 `protections` property~~ **✅ 已於 Phase 10 骨架建置(2026-08-08)實測確認:`protections` 只能是策略類別 attribute,`strategy_resolver.py` 的 config 覆寫清單不含此鍵,config 層級設定不會被讀取。詳見 `user_data/configs/config-common.json` 的 `_protections_comment`。**
- `/stop` 指令對已開倉部位的確切管理行為(`architecture-spec.md` 9,`execution-spec.md` 4.4 節 runbook 已設計成不依賴此細節)
- ~~`MaxDrawdown` Protection 能否在同一策略內並存兩個不同閾值的實例~~ **✅ 已於 Phase 10 骨架建置實測確認:可以並存,`ProtectionManager` 成功載入月回撤(30 天/8%)與 kill switch(365 天/15%)兩個獨立 `MaxDrawdown` 實例,無需改走自訂邏輯路徑。**
- **附帶確認**:`stop_duration_candles=1` 於 `timeframe=1d` 下實測確認精確等於 1440 分鐘(24 小時),`risk-policy.md` 5.4 節「計時單位待查證」的疑慮已解除。
- `create_order`/`create_stoploss` 是否仍不套用自動重試、`enter_positions`/`handle_similar_open_order` 訊號級防護是否仍存在、Binance Spot `stoploss_order_types` 是否仍只映射 `"limit"`(`execution-spec.md` 10,皆為原始碼查證結果,穩定性保證低於公開文件)
- `IHyperOptLoss` 介面能否取得足夠的 trade-level 資訊做 purge 過濾(`backtest-procedure.md` 9)
- `custom_stake_amount` 是否已原生支援直接裁剪,或仍需透過 `confirm_trade_entry` 否決(`security-policy.md` 7)

### 4.2 需要真實資料才能驗證的數字(高優先——直接影響是否能上線)

- 45 天 time-stop、ATR 倍數邊界 [2.0, 4.0] 是否與真實持倉時間分布相符(`risk-policy.md` 8,`backtest-procedure.md` 第 6 節已設計具體檢查清單)
- `risk_fraction` 1.5%/2.5% 的確定性推導,是否與 Monte Carlo 模擬結果一致(`risk-policy.md` 8)
- `backtest-procedure.md` 2.2 節 5 bps 滑價假設,需 dry-run 實測校正(該文件第 7 節已設計校正機制)
- ~~Embargo 校準 Pass A 用 200 epochs 是否足夠代表完整 1,000 epochs 的持倉時間分布~~ **已於 CP-001 消解:Pass A/B 現在同為 200 epochs,兩輪唯一差異是 embargo 值,不再有「縮減值是否具代表性」的問題**

### 4.3 純文件精確度問題(低優先,不阻擋開發,可在實作過程中順手修正)

- `security-policy.md` 2.1 節措辭與 `execution-spec.md` 7.2 節查證結果的落差(見 3.2 節)
- 結構化交易紀錄欄位清單是否需要額外欄位(如逐筆 ATR 值)(`architecture-spec.md` 9)
- `deploy/testnet.env`/`deploy/live.env` 的確切路徑若因 Docker 實際配置調整,需同步更新 `.gitignore` 規則(`security-policy.md` 9)
- Binance API 權限頁面確切欄位名稱依當時 UI 為準(`security-policy.md` 9)

### 4.4 刻意留白、非 Phase 10 立即需要處理(已明確記錄為「不在 v1 範圍」)

- 持續性資料源中斷的存活監控細節(輪詢頻率、告警管道)(`architecture-spec.md` 6.2、`execution-spec.md` 4.4——已有部分緩解但非完整方案)
- 多資產 Kelly(涉及協方差矩陣)——僅在納入策略二/三或多部位時才需要(`statistical-methodology.md` 8)

**用法建議:** Phase 10 開始實作時,建議把 4.1、4.2 兩類轉成實際的工程 checklist(例如 GitHub Issues 或 TODO 清單),4.3、4.4 兩類可以先擱置,不阻擋開發進度。

---

## 5. 最終結論 / Final Verdict

**Phase 0-8 全部 9 項離開條件通過(第 1 節),跨文件一致性檢查未發現任何阻擋性衝突(第 3 節,僅一項低風險措辭落差),Phase 10 實作前待核實項目已彙整為單一清單(第 4 節)。**

**結論:🟢 GO — Phase 0-9 規劃階段已全數確認結案,可以開始 Phase 10 實作。**

下一步:依 [`development-plan.md`](./development-plan.md) Phase 10 的既有原則,實作依各角色文件的規格分模組進行,不重新設計——第一個實際的工程任務會是 `tech-stack-decision.md`/`architecture-spec.md` 已定的專案骨架(`user_data/` 目錄結構、Freqtrade 安裝、`config-common.json` 等)。

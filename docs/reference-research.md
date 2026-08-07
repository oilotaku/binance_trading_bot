# 參考研究:GitHub 高星數專案 / Reference Research: High-Star GitHub Projects

> 依您建議,查詢 GitHub 上高星數的開源加密貨幣交易機器人專案,作為本專案規劃的參考基準。

## 星數比較 / Star Count Comparison

| 專案 | Star 數(約) | 語言 |
|---|---|---|
| **Freqtrade** | ~48,000 | Python |
| Hummingbot | ~18,000 | Python |
| Jesse | ~7,600 | Python |
| OctoBot | ~5,500 | Python |

**Freqtrade** 是壓倒性的第一名(星數約為第二名的 2.6 倍),且持續活躍開發(2026 年仍在發版),是目前最值得參考、甚至考慮直接建構其上的專案。

## Freqtrade 架構參考重點

### 1. 策略介面 — 與我們 Phase 1 的設計原則完全吻合

Freqtrade 的策略類別實作三個核心方法,**訊號生成與執行完全分離**:
- `populate_indicators()` — 計算技術指標
- `populate_entry_trend()` — 產生進場訊號(對整個 dataframe 做向量化運算,不用迴圈)
- `populate_exit_trend()` — 產生出場訊號

策略作者只定義「何時該交易」,執行細節(如何下單)由框架處理,同一套策略程式碼可以在回測、dry-run(模擬)、實盤之間無縫切換 — 這正是我們 system-architect 在 Phase 3 想要的「Paper/Backtest/Live 共用同一套執行介面」。框架文件本身也明確提醒開發者要避免 lookahead bias。

### 2. Protections(熔斷/風控機制) — 直接對應我們 Phase 4 的 kill switch 需求

Freqtrade 內建 4 種可組合的「Protection」機制,比我們原本規劃的更細緻,值得直接參考甚至沿用命名:

| 機制 | 作用 | 對應我們的設計 |
|---|---|---|
| **StoplossGuard** | 一段時間內停損觸發次數過多 → 暫停該幣種(或全部)交易一段時間 | 比我們原本「單一熔斷」更細緻,可分幣種/分方向 |
| **MaxDrawdown** | 帳戶回撤超過門檻 → 停止交易 | 直接對應我們 risk-manager 的每日/總回撤熔斷 |
| **LowProfitPairs** | 特定幣種持續表現不佳 → 個別鎖定該幣種,其他照常交易 | 比我們原本「全域停用」更細緻的分級熔斷 |
| **CooldownPeriod** | 出場後強制等待一段時間才能再進場同一標的 | 對應我們規劃的「kill switch 觸發後強制冷靜期」,但 Freqtrade 是做在單一交易對層級,我們原本是做在帳戶層級 — **兩者可以並存** |

### 3. 內建 Dry-run(模擬交易) — 驗證我們 Phase 0 的決策方向正確

Freqtrade 本身就有「不花錢跑一遍」的 dry-run 模式,直接對接真實行情但不真的下單 — 與我們 Phase 0 確認的「先用模擬資金」方向完全一致。

### 4. 其他值得參考的能力

- **Hyperopt**:自動化參數優化(對應我們 Phase 2 quant-mathematician 關切的過擬合風險,Freqtrade 文件也提醒需搭配樣本外驗證)
- **Backtesting + lookahead-analysis**:內建「回測是否有 lookahead bias」的自動檢測工具
- **多交易所支援**:透過 CCXT 函式庫抽象化交易所 API(對應 Phase 7 執行層規格)
- **Whitelist/Blacklist**:交易標的白名單/黑名單管理

---

## 對本專案規劃的建議 / Implications for This Project

這裡有一個值得您決定的策略性問題,建議提前到 Phase 8(技術選型)之前先想清楚:

### 選項 A:從零開始自建(原計劃)
- 優點:完全客製化、學習過程完整、無框架限制
- 缺點:Phase 3(架構)、Phase 6(回測框架)、Phase 7(執行層)大部分工作,其實是在重造 Freqtrade 已經做了 8 年、48K 人驗證過的輪子

### 選項 B:以 Freqtrade 為基礎框架,只寫自己的策略邏輯
- 優點:Phase 6(回測)、Phase 7(執行層連線/斷線重連/rate limit)、Phase 4 的部分熔斷機制(Protections)幾乎不用自己寫,團隊可以把心力全部集中在 **Phase 1(策略假說)與 Phase 2(數學驗證)這兩個真正決定「能不能穩定獲利」的核心**
- 缺點:受限於 Freqtrade 的架構假設與更新節奏,深度客製化(例如非典型訊號來源、特殊執行邏輯)可能綁手綁腳

### 建議
若目標是**盡快驗證策略是否有效**(這正是 Phase 0 設定的初期目的),選項 B 效益明顯較高 — 用 48K 星、持續維護的框架處理「不出錯」的部分(執行、風控熔斷、回測引擎),團隊專注在「有沒有 edge」這個真正困難的問題上。若之後策略證實有效、且遇到 Freqtrade 架構限制,再考慮遷移到自建系統也不遲。

**這個選擇會影響 Phase 3(架構規格)與 Phase 8(技術選型)怎麼寫,建議您先決定方向,我再請 system-architect 依此調整後續規劃。**

---

Sources:
- [GitHub - freqtrade/freqtrade](https://github.com/freqtrade/freqtrade)
- [best-of-algorithmic-trading star comparison](https://github.com/TitanFlow-Systems/best-of-algorithmic-trading)
- [Freqtrade Protections](https://github.com/freqtrade/freqtrade/blob/develop/docs/includes/protections.md)
- [Freqtrade Strategy Customization](https://github.com/freqtrade/freqtrade/blob/develop/docs/strategy-customization.md)

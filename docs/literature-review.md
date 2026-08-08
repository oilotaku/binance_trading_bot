# 文獻回顧:驗證方法論的改善方向 / Literature Review

> 狀態:**🟡 調研完成,含 2 項具體改善建議待決策 / RESEARCHED — two concrete proposals pending decision.**
> 日期:2026-08-08
> 目的:針對 [`statistical-methodology.md`](./statistical-methodology.md)、[`backtest-procedure.md`](./backtest-procedure.md) 已定案的驗證方法論,尋找學術文獻中更成熟的做法,特別是能解決我們已識別的兩個結構性瓶頸的方法。

---

## 0. 我們要解決的具體問題(不是泛泛找論文)

本次調研有明確標的。分析 DSR 公式與 Phase 0 目標的交互作用後(見 [`CP-001`](./change-proposals/CP-001-hyperopt-epochs.md) 第 2.1 節),已識別兩個結構性瓶頸:

| 瓶頸 | 具體症狀 | 想找什麼 |
|---|---|---|
| **A. `n_eff` 樣本量不足** | 日 K 波段策略年交易 20–40 筆,經自相關校正後每年僅約 12–24 個有效樣本;達 `n_eff ≥ 30` 硬性下限需 1.3–2.5 年,且 [`backtest-procedure.md`](./backtest-procedure.md) 4.4 節已預警多數 fold 可能貼近下限 | 能在**不增加資料**的前提下提高統計檢定力的方法 |
| **B. 單一路徑的高變異** | walk-forward 只產生**一條**歷史路徑的績效估計,結論高度依賴「歷史剛好這樣走」 | 能從同一份資料產生**績效分布**而非單點估計的方法 |

---

## 1. 🔴 最重要發現:CPCV(組合式淨化交叉驗證)同時解決 A 與 B

**文獻:** López de Prado, M. (2018), *Advances in Financial Machine Learning*, Ch. 12 — Combinatorial Purged Cross-Validation。
Wikipedia 條目:[Purged cross-validation](https://en.wikipedia.org/wiki/Purged_cross-validation)

### 1.1 核心機制

CPCV 把資料切成 `N` 個連續分組,每次取 `k` 組當測試集、其餘訓練,窮舉所有組合。**沿用我們已採用的 purge 與 embargo 機制**(這點很重要——不是要推翻既有設計,而是在同一個防資訊洩漏的框架上擴充)。

```
分割數 = C(N, k)
回測路徑數 φ = (k/N) × C(N, k)
```

### 1.2 對本專案的具體效益

我方以現行資料量(2020-01 至 2026-07,約 79 個月)試算:

| 分組 N | 每組長度 | k | 分割數 | **回測路徑數 φ** |
|---|---|---|---|---|
| 8 | 約 9.9 個月 | 2 | 28 | **7** |
| 10 | 約 7.9 個月 | 2 | 45 | **9** |
| 12 | 約 6.6 個月 | 2 | 66 | **11** |
| 12 | 約 6.6 個月 | 3 | 220 | **55** |

**對照:現行 walk-forward 設計產生 1 條路徑。**

用同一份資料,可以得到 7–11 條(甚至更多)樣本外路徑,每條都給出一組獨立的績效統計量 → **從單點估計變成分布**,可以計算「這個策略的 Sharpe 在不同歷史路徑下的變異有多大」,而不是賭在一條路徑上。

### 1.3 ⚠️ 必須誠實說清楚的限制

**CPCV 不會無中生有地製造資訊。** 這 11 條路徑共用同一份底層資料,**彼此不獨立**。它並不會把 `n_eff` 從 30 變成 330,任何宣稱如此的說法都是錯的。

它真正提供的是:
- ✅ **降低「結論依賴單一歷史路徑排序」的變異** —— 這是瓶頸 B 的直接解法
- ✅ **產生績效分布,讓 PBO(見第 2 節)這類需要多組樣本的檢定變得可行**
- ❌ **不解決「總交易筆數就是這麼少」的根本問題** —— 瓶頸 A 只有部分緩解

因此**瓶頸 A 仍然存在**,CPCV 不是萬靈丹。這點必須在任何採用決策中明確記錄,避免日後誤以為換了方法就解決了樣本量問題。

### 1.4 文獻對其效果的評估

有研究以合成受控環境比較各種樣本外測試方法,結論是 CPCV 在抑制過擬合上表現最佳,其 PBO 較低、DSR 檢定統計量較優([Backtest overfitting in the machine learning era, *Knowledge-Based Systems*](https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110))。

> ⚠️ 我方未能取得該論文全文(本環境網路政策擋掉多數學術網站),以上為搜尋摘要轉述。**採用前應取得全文核實其實驗設定是否與我們的情境可比**(尤其是否針對低頻策略)。

---

## 2. 🟡 PBO / CSCV:與 DSR 互補的第二道過擬合檢定

**文獻:** Bailey, D. H., Borwein, J. M., López de Prado, M., & Zhu, Q. J. (2014), "The Probability of Backtest Overfitting", *Journal of Computational Finance*, 20(4), 39–69.
- [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) ｜ [作者網站 PDF](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
- 參考實作:[CRAN `pbo` 套件](https://cran.r-project.org/web/packages/pbo/readme/README.html)、[GitHub mrbcuda/pbo](https://github.com/mrbcuda/pbo)

**核心:** 用 CSCV(combinatorially symmetric cross-validation)估計「這次回測是過擬合的機率」。論文明確指出,傳統的 hold-out 在投資回測情境下不可靠。

**與我們現有 DSR 的關係(互補,非取代):**

| | DSR(已採用) | PBO(建議增列) |
|---|---|---|
| 回答的問題 | 「這個 Sharpe 有多大機率不是運氣?」 | 「樣本內最佳的參數,在樣本外表現低於中位數的機率有多高?」 |
| 輸入 | 單一最佳結果 + 試驗次數 N | **需要多組樣本內/外配對** |
| 現行可行性 | 已實作 | **需要先有 CPCV 或類似機制產生多組配對** |

**這是把 CPCV 與 PBO 綁在一起評估的原因:PBO 需要 CPCV 提供的多路徑結構才能計算。** 兩者是一組,不宜分開採用。

---

## 3. 🟢 MinTRL:直接量化「還要多久才能證明它有效」

**文獻:** Bailey, D. H. & López de Prado, M. (2012), "The Sharpe Ratio Efficient Frontier", *Journal of Risk*, 15(2).
[SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1821643) ｜ [作者網站 PDF](https://www.davidhbailey.com/dhbpapers/sharpe-frontier.pdf)

**核心:** PSR 的反函數 —— 給定觀察到的 Sharpe、偏度、峰度與目標信心水準,**反推需要多長的track record 才能拒絕「真實 Sharpe 低於門檻」的假設**。

**為什麼這對我們特別有價值:** 這正是瓶頸 A 的正面答案。我們目前用的是自製的粗略推算(「年交易 30 筆 ÷ IF 1.67 → 每年 18 個有效樣本 → 達 30 需 1.7 年」),而 MinTRL 是這個問題的**正式解**,且已納入偏度/峰度(趨勢策略右偏肥尾的特性,正是我們的情況)。

**重要優勢:實作成本極低。** 我們的 [`analysis/significance.py`](../analysis/significance.py) 已經實作了 PSR 公式,MinTRL 只是把它對樣本數反解,**不需要引入任何新的資料結構或流程**。

---

## 4. 🟢 策略假說的文獻支撐(Phase 1 相關)

[`strategy-hypothesis.md`](./strategy-hypothesis.md) 對策略一的信心評估是「中」,並要求經濟假說必須可證偽。以下文獻與該假說直接相關:

- **加密貨幣時間序列動能確實存在:** Grobys & Sapkota (2019) 在加密貨幣期貨市場發現 BTC、ETH 存在時間序列動能。相關綜述見 [Dynamic time series momentum of cryptocurrencies](https://www.sciencedirect.com/science/article/abs/pii/S1062940821000590)、[Time Series Momentum Trading Strategy for Cryptocurrencies](https://link.springer.com/chapter/10.1007/978-981-99-6441-3_17)。

- **⚠️ 但交易成本是關鍵變數,且文獻結論分歧:** 搜尋結果顯示,有研究指出動能策略「可產生顯著正報酬,但經交易成本調整後未必仍然獲利」,且成本本身難以估計(依交易所、時段、散戶或機構而異)。另有研究指出**動能策略在期貨市場表現優於現貨市場**(流動性較佳且可做空)——而我們 v1 明確限定現貨。

**這對本專案的意義(誠實解讀):**

1. ✅ 「趨勢延續」的經濟假說**有文獻支撐**,不是憑空想像 —— 支持 `strategy-hypothesis.md` 給的「中」而非「低」信心評估。
2. ⚠️ 但「扣除成本後是否仍獲利」在文獻中**沒有一致結論**,這正好對應我們自己算出的敏感度:賺賠比 2:1 與 3:1 之間,年化從 +0.7% 跳到 +16.5%。
3. ⚠️ 文獻指出現貨表現不如期貨,而我們刻意選了現貨(基於 [`scope.md`](./scope.md) 的風險考量)。**這是一個已知的、有意識接受的劣勢**,不是疏漏 —— 但應該被明確記錄下來。

> ⚠️ 以上均為搜尋摘要轉述,我方未取得全文。引用具體數字或作為決策依據前,必須取得原文核實。

---

## 5. 建議與待決策事項

### 5.1 建議採用(低成本、高價值)

**➤ MinTRL(第 3 節)** —— 建議直接納入 `analysis/significance.py`。
- 成本:極低(PSR 公式已實作,只需反解)
- 價值:把「還要多久才能證明」從粗略推算變成正式計算,直接服務瓶頸 A
- 分級:純新增分析工具,**不改變任何既有通過門檻**,屬操作細節,不需動用核心治理變更流程

### 5.2 建議評估但暫不採用(高價值、但成本與風險需要先釐清)

**➤ CPCV + PBO(第 1、2 節)** —— 建議**先取得論文全文核實**,再決定是否提出正式變更提案。

理由:
- 這會**實質改變 [`backtest-procedure.md`](./backtest-procedure.md) 第 4 節的整個驗證流程**(從 walk-forward 換成 CPCV),影響範圍遠大於 CP-001
- 我方目前只有搜尋摘要,未取得任何一篇全文(網路政策限制),**在這個資訊基礎上提出流程級變更是不負責任的**
- 現行 walk-forward 方案並非錯誤,只是變異較高;不存在「必須立刻改」的急迫性

### 5.3 ⚠️ 一個必須先講清楚的時序原則

與 [`CP-001`](./change-proposals/CP-001-hyperopt-epochs.md) 第 3 節相同的紀律適用於此:

**若未來 Pass B 執行後策略未通過,才回頭提出「改用 CPCV 以獲得更多路徑」,即使技術論證完全正確,也必須視為不正當。** 驗證方法論的變更必須在看到結果之前決定。

**本文件於任何真實回測執行之前撰寫**,故現在提出的任何採用決策都符合預先登錄原則;但這個時間窗口會在 Pass B 執行的當下關閉。

---

## 6. 誠實揭露:本次調研的限制

- **未取得任何一篇論文全文。** 本環境的網路政策擋掉學術網站(ScienceDirect、SSRN、Wikipedia 等均為 403),所有內容來自搜尋結果摘要。**本文件的所有轉述都應視為待核實**,不得直接作為技術決策的唯一依據。
- **CPCV 路徑數的試算(1.2 節表格)是我方依公式 `φ = (k/N)·C(N,k)` 自行計算**,公式來源為搜尋結果,未經原文核實。計算本身可獨立驗算,但公式本身應核對 AFML 原文。
- **未涵蓋的方向:** 尚未調研 regime detection 的文獻(`strategy-hypothesis.md` backlog 的策略二/三會需要)、也未調研執行層滑價建模的文獻(目前 5 bps 假設仍待 dry-run 實測校正)。

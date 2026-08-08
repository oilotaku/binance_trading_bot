# 學術與專業書籍參考 / Reading List

> 狀態:**參考資料,非決策文件。**
> 日期:2026-08-08
> 編排原則:**依「對應本專案哪個決策」排序,不是依名氣或出版年份。** 每本都說明它能為我們已經做出的決定補上什麼,以及是否指出我們現行做法的不足。

---

## 0. 這份清單怎麼讀

本專案的方法論已經定案並寫進 `docs/`。這份清單的用途**不是**「還要再學什麼」,而是:

1. **追溯我們現行做法的原始出處** —— 有些做法我們是從二手來源學到的,回到原典能確認理解無誤
2. **找出我們可能做錯或做得不夠好的地方** —— 第 4 節有一個具體案例:Carver 的做法比我們現行的解法更好

---

## 1. 🔴 Robert Pardo,《The Evaluation and Optimization of Trading Strategies》(Wiley, 2nd ed. 2008)

**對應本專案:** [`backtest-procedure.md`](./backtest-procedure.md) 整個第 4 節、[`statistical-methodology.md`](./statistical-methodology.md) 第 3 節 walk-forward 切分規則

**為什麼排第一:Pardo 就是 walk-forward analysis 的發明人。** 這個方法最早出現在他 1992 年的《Design, Testing and Optimization of Trading Systems》,在本書第二版擴充,全書約三分之一篇幅專講 walk-forward。

我們整套 Phase 6 驗證流程的骨架(IS 視窗優化 → OOS 視窗驗證 → 滾動前進)就是 walk-forward,但我們是從 López de Prado 的脈絡接觸到它的。**回到原典的價值在於:Pardo 對「walk-forward 的參數該怎麼選」(IS/OOS 比例、滾動步長、多少個 window 才算足夠)有系統性的討論,而我們目前的 24mo/6mo/6mo 是從統計檢定力反推的,沒有對照過這個方法發明人自己的建議。**

書中也專章討論「過擬合的症狀」如何辨識 —— 這正是我們 [`backtest-procedure.md`](./backtest-procedure.md) 4.5 節參數穩定性檢查(CV < 0.5)想做的事。

- [Wiley](https://onlinelibrary.wiley.com/doi/book/10.1002/9781119196969) ｜ [Amazon](https://www.amazon.com/Evaluation-Optimization-Trading-Strategies/dp/0470128011)

---

## 2. 🔴 David Aronson,《Evidence-Based Technical Analysis》(Wiley, 2006)

**對應本專案:** [`statistical-methodology.md`](./statistical-methodology.md) 2.4 節 White's Reality Check、[`analysis/significance.py`](../analysis/significance.py)

**為什麼重要:這本書就是把 White's Reality Check 引進技術分析評估的代表作**,而我們的 `significance.py` 實作了 Reality Check。核心主題正是我們整份 `statistical-methodology.md` 在防的東西:**data mining bias** —— 「回測大量規則、挑出表現最好的那個」這個流程本身就會讓績效向上偏誤,因此需要新的統計檢定才能對未來獲利能力做出合理推論。

副標題「Applying the Scientific Method and Statistical Inference to Trading Signals」幾乎就是本專案 Phase 2 的宗旨。

**對我們的具體價值:** 我們的 Reality Check 實作是依公式寫的簡化版(studentized statistic + stationary bootstrap)。Aronson 有大量篇幅討論這個檢定在**實際交易規則**上的應用細節與陷阱,可用來檢驗我們的實作是否有簡化過頭。

- [Wiley](https://www.wiley.com/en-us/Evidence-Based+Technical+Analysis:+Applying+the+Scientific+Method+and+Statistical+Inference+to+Trading+Signals-p-9781118268315) ｜ [Google Books](https://books.google.com/books/about/Evidence_Based_Technical_Analysis.html?id=jbD47VkOHAEC)

---

## 3. 🔴 Marcos López de Prado,《Advances in Financial Machine Learning》(Wiley, 2018)

**對應本專案:** [`statistical-methodology.md`](./statistical-methodology.md) 幾乎全篇 —— purge/embargo、DSR、PSR、蒙地卡羅回撤;以及 [`literature-review.md`](./literature-review.md) 建議評估的 CPCV

**現況:** 這本書實質上是我們統計方法論的骨架來源(透過二手管道),但**我們從未取得原文**。[`literature-review.md`](./literature-review.md) 第 6 節已誠實記錄:本環境擋掉學術網站,所有內容都來自搜尋摘要。

**取得原文最該優先核實的三件事:**
1. **CPCV 的路徑數公式 `φ = (k/N)·C(N,k)`** —— 我方已用它試算出「12 組取 2 → 11 條路徑」並寫進 `literature-review.md`,但公式本身來自搜尋結果,未經原文核實
2. **purge 與 embargo 的精確定義** —— 我們的 `walk_forward.py` 已實作,值得對照原文確認語意無誤
3. **DSR 公式中 `σ_SR` 的正確估計方式** —— 這是我們踩過大坑的地方(MAX_LOSS 哨兵值汙染,見 [`pipeline-findings.md`](./pipeline-findings.md) 發現一)

> ⚠️ 章節編號請以實際書籍為準。我方引用的「Ch. 12 = CPCV」來自搜尋結果,未經原文核實。

---

## 4. 🟡 Robert Carver,《Systematic Trading》(Harriman House, 2015)

**對應本專案:** [`risk-policy.md`](./risk-policy.md) 第 1、4 節倉位大小與曝險上限

### ⚠️ 這本書指出了我們現行做法的一個真實不足

[`risk-policy.md`](./risk-policy.md) 4.3 節我方自己發現一個邊界案例:

```
position_size = (risk_fraction × Equity) / (k × ATR)

當 ATR% 異常小(低波動盤整期)時,分母趨近零 → 名目部位暴衝
情境 B(k=3, ATR%=0.5%):notional = 1.5% / 1.5% = 100% 權益  ← 單筆吃掉全部本金
```

**我們當時的解法是:在輸出端加一個名目部位硬上限(單筆 ≤50%、合併 ≤80% 權益),把結果裁掉。**

**Carver 的做法更好:他不裁輸出,而是在輸入端設一個「最小假設波動度」下限** —— 依長期平均波動度設定一個底線,當實際波動度低於它時就用底線值計算。這樣部位不會因為波動度趨近零而發散,**是從源頭消除問題,而不是等它發散了再截斷**。

兩者的差別在實務上很重要:硬上限被觸發時,你只知道「有東西超標了」;波動度下限則讓部位大小始終是一個平滑、有意義的函數。

**這值得評估是否納入 —— 但屬於 `risk-policy.md` 第 7 節定義的核心治理數字變更,必須走正式提案流程(比照 [`CP-001`](./change-proposals/CP-001-hyperopt-epochs.md)),不可直接改。**

書中其他直接相關的主張:**「整體風險水準是交易系統設計者最重要的決定」**、多數散戶虧損是因為部位相對帳戶規模過大 —— 與我們拒絕 Kelly 的 7%、改採 1.5% 硬上限的推導同一方向。

- [Goodreads](https://www.goodreads.com/en/book/show/25900953-systematic-trading) ｜ [Google Books](https://books.google.com/books/about/Systematic_Trading.html?id=y3dxCgAAQBAJ)

---

## 5. 🟡 Andreas Clenow,《Following the Trend》(Wiley, 2013)

**對應本專案:** [`strategy-hypothesis.md`](./strategy-hypothesis.md) 策略一的經濟假說

我們的 v1 就是趨勢跟隨(Donchian 突破 + ATR 移動停損),而這本書專講 CTA/managed futures 的趨勢跟隨,是該領域最常被推薦的實務書之一。

**對我們最有價值的部分:** 趨勢跟隨的報酬分布特性(低勝率、少數大單貢獻主要獲利)——這正是我們 [`risk-policy.md`](./risk-policy.md) 2.2 節「不設固定停利」決定的依據。我方自行算出的敏感度表顯示賺賠比從 2:1 到 3:1 會讓年化從 +0.7% 跳到 +16.5%,**這本書提供的是真實 CTA 長期績效資料**,可以檢驗我們假設的賺賠比量級是否現實。

> ⚠️ 注意適用性落差:本書講的是**跨資產、多市場分散**的期貨趨勢跟隨,而我們是**單一策略、兩個現貨標的、無槓桿**。[`literature-review.md`](./literature-review.md) 第 4 節已記錄「動能在期貨表現優於現貨」這個文獻發現。書中的績效數字**不可直接套用到我們的情境**。

- [Goodreads](https://www.goodreads.com/book/show/15845804-following-the-trend)

---

## 6. 🟡 Kevin Davey,《Building Winning Algorithmic Trading Systems》(Wiley, 2014)

**對應本專案:** [`analysis/drawdown_mc.py`](../analysis/drawdown_mc.py)、[`statistical-methodology.md`](./statistical-methodology.md) 第 6 節蒙地卡羅回撤估計

副標題是「A Trader's Journey From Data Mining to Monte Carlo Simulation to Live Trading」—— 這條路徑幾乎就是我們 Phase 6 → Phase 10 的流程。

**具體相關點:** Davey 用蒙地卡羅多次模擬的百分位數來呈現「系統績效的預期範圍」,而非單一數字 —— 這與我們 `drawdown_mc.py` 回報 `P(MDD>15%)`、`P(MDD>20%)` 與中位數/95 百分位的做法一致。**回到原書的價值在於檢驗我們的重採樣設定是否合理**(我們用 stationary bootstrap 保留自相關,這是個刻意的選擇,值得對照他人做法)。

實務導向、偏經驗談,學術嚴謹度低於前三本,但對「從回測到實盤之間會踩到什麼坑」有第一手描述。

- [Goodreads](https://www.goodreads.com/book/show/22675886-building-winning-algorithmic-trading-systems) ｜ [Amazon](https://www.amazon.com/Building-Winning-Algorithmic-Trading-Systems/dp/1118778987)

---

## 7. 閱讀優先順序建議

若只讀一本:**Pardo**(第 1 本)。我們整套驗證流程的骨架就是他發明的方法,而我們從未讀過原典。

若要解決已知問題:

| 想解決的問題 | 讀哪本 |
|---|---|
| walk-forward 的 IS/OOS 比例與視窗數該怎麼選才對 | Pardo(第 1 本) |
| 低波動期部位發散(我們用硬上限裁切,可能有更好解法) | **Carver(第 4 本)—— 已確認有更好的做法** |
| Reality Check 實作是否簡化過頭 | Aronson(第 2 本) |
| CPCV 是否值得採用、公式是否正確 | López de Prado(第 3 本) |

---

## 8. 誠實揭露

- **我未讀過上述任何一本書的原文。** 本清單的內容來自網路搜尋所得的書籍介紹、目次與書評摘要。每一項「這本書講什麼」的陳述都應視為**待原文核實**。
- 特別是第 4 節「Carver 的做法更好」這個判斷 —— 依據是搜尋結果中對其「最小假設波動度」機制的描述。**這個機制與我們問題的對應關係是我方的推論**,採用前必須讀原文確認其適用條件(例如他是否假設多標的分散、是否適用於無槓桿現貨)。
- 未涵蓋:市場微結構、選擇權、投資組合最佳化等本專案 v1 範圍外的主題;也未涵蓋加密貨幣專門書籍(該領域出版品品質參差,且核心方法論並非加密專屬)。

"""
配對交易層級的顯著性檢定 —— 策略五(Kalman 濾波出場)vs 策略一(固定 ATR 停損)。

用途(docs/strategy-5-hypothesis.md 第 3.4 節「(b) 配對比較 vs 策略一」):
    對 CP-003 已固定的進場觸發時點集合,分別套用策略一的出場規則與 Kalman
    出場規則,得到兩組「同一筆交易、不同出場方式」的報酬。本模組回答:這兩組
    報酬的 SR_trade 之差是否可信地不為零,且是否大到能承受 N=10 的搜尋懲罰。

────────────────────────────────────────────────────────────────────────────
為什麼這不能直接套用 CP-004(analysis/sharpe_difference.py)原本的用法,
需要另外一個模組(而不是改那個檔案):

    CP-004 比較的是兩條「日頻報酬時間序列」,比較軸是日曆日 t,兩序列在同一個
    t 上都有定義(策略當天在市與否都算一個報酬,可能是 0)。策略五 vs 策略一
    完全不是這種關係:兩者的「觀測單位」是交易,不是日曆日——同一筆交易在
    策略一與策略五下,進場點、進場前的價格路徑完全相同,只有出場點(因而出場
    後的那一段報酬)不同。若勉強沿用日曆日當比較軸,策略一出場、策略五仍持有
    的那段期間,策略一根本沒有部位、沒有「當日報酬」可對齊,比較會失去意義
    (docs/strategy-5-hypothesis.md 第 3.4 節、第 7 節已指出這一點,是本模組
    存在的直接原因)。正確的比較軸是「交易序號」(依進場時間排序),每個序號 i
    唯一對應一組配對觀測 (r_baseline_i, r_treatment_i)。

    這是否代表 Jobson–Korkie + Memmel 修正、HAC 標準誤、studentized 循環
    區塊 bootstrap(analysis/sharpe_difference.py 已實作,7 項測試通過)整套
    框架不適用?**不是。** 那套框架的推導(見該檔案 `_gradient`/`_hac_covariance`
    的 docstring)只用到 (r1, r2) 的聯合動差(均值、二階動差)與其序列相關結構,
    不對「r1、r2 為什麼相關」做任何結構性假設——不論相關性來自「兩者是同一天
    的市場報酬」還是「兩者是同一筆交易分岔前的共同路徑」,估計式都一樣正確,
    因為它是直接從資料估聯合共變異數,不是代入一個假設好的 ρ 公式。真正需要
    改變的只有兩件事,本模組處理:

    1. **比較軸從「日曆日」換成「交易序號」。** 呼叫端必須提供兩個等長陣列,
       依進場時間排序,索引 i 對齊同一筆交易。
    2. **小樣本的穩健性收斂假設更弱。** CP-004 的日頻比較 T≈3270;本專案的
       主檢定點策略一實測 76 筆交易(docs/pass-b-results.md `donchian_period=20`)。
       T 從三千多降到幾十,漸近(T→∞)的常態/HAC 近似更不可靠——本模組把
       studentized bootstrap p 值當唯一決策依據(HAC 常態 z/p 僅供對照,
       與 sharpe_difference.py 一貫立場相同,但此處警語更強),並且對
       Politis–White 自動區塊長度加上依 n 縮放的上限(見 `_capped_block_length`),
       避免小樣本下區塊長度相對過大、實際重抽樣的「區塊數」太少而失真。

    附帶一個對本假說有利的觀察:CP-004 3.3 節指出配對檢定的檢定力來自
    `2 − 2ρ` 這一項,ρ 越高、標準誤越小——但策略一 vs 50/50 基準的 ρ 只有
    約 0.41(在市時間低),配對檢定反而比絕對檢定更沒力。策略五 vs 策略一
    的配對結構恰好相反:兩者在進場點附近幾乎完全相同(同一段價格路徑),
    預期 ρ_trade 遠高於 0.41——這對本比較的檢定力是結構性的好消息,不是
    需要擔心的地方(輸出的 `rho_trade` 欄位可直接驗證這個預期)。

────────────────────────────────────────────────────────────────────────────
選擇「配對 SR_trade 差異的 bootstrap」為主要決策統計量、「Wilcoxon 符號等級
檢定」降為次要診斷,理由(呼應 docs/strategy-5-hypothesis.md 第 3.4 節列出的
兩個候選,本模組明確二選一,不留給呼叫端猜):

    - docs/strategy-5-hypothesis.md 第 2 節的可證偽預測,明文以 SR_trade 為
      判定單位("SR_trade 高於 0.1723"),不是「配對差的中位數是否偏離 0」。
      用 Wilcoxon 當主要決策統計量,會讓「通過/不通過」回答一個不同的問題
      (中位數位移 vs 風險調整後報酬的比值),與第 2 節預先登錄的判準脫節。
    - SR_trade(mean/std)對「集中在少數幾筆大賺交易」的右尾敏感——這正是
      本假說想量測的效應(docs/post-mortem-strategy-1.md:策略一最好 10 筆
      貢獻 157.9% 總報酬,其餘 66 筆合計為負)。Wilcoxon 是等級(rank-based)
      統計量,系統性地壓低極端值的影響力,對本假說最關心的效應反而不敏感。
    - 但正因為 SR_trade 對右尾敏感,bootstrap 檢定的顯著性結論也可能被
      少數幾筆極端配對差異「拖著跑」——如果結論只靠 1–2 筆交易撐起來,那
      個結論很脆弱。Wilcoxon 對極端值不敏感的性質,此時反而是有用的交叉
      檢查:若 bootstrap 顯著但 Wilcoxon 完全不顯著,這個落差本身就是要
      揭露的訊號(懷疑結果由極端值主導),必須在回傳結果中一併呈現。
    - ⚠️ Wilcoxon 的標準 p 值(不論常態近似或精確排列分布)假設配對觀測彼此
      獨立。docs/statistical-methodology.md 第 4 節指出交易報酬可能因連續
      突破訊號叢聚而正自相關(策略一實測 IF=1.08,見 pass-b-results.md,
      相關性其實很弱,但那是「這一個進場參數點、這一段資料」的實測值,
      不能預先假設策略五也一樣低)。本模組回傳的 `wilcoxon_p` 因此**只作為
      方向性診斷**,不做自相關校正,不可單獨作為通過/不通過的依據
      ——真正的決策統計量是 bootstrap 版本的 `p_boot`,它透過區塊重抽樣
      內建了自相關的穩健性。

────────────────────────────────────────────────────────────────────────────
N=10 懲罰如何接上(docs/strategy-5-hypothesis.md 5.3 節要求「不得因為換了
比較對象就悄悄退回 N=1 的樂觀版本」):

    直接重用 analysis/sharpe_difference.py 已實作的 `minimum_detectable_difference()`
    ——同一個函式、同一套邏輯,只是把 `se` 換成本模組算出的「配對交易
    SR_trade 差異」HAC 標準誤。`n_trials` 預設 10,對應
    docs/strategy-5-hypothesis.md 5.2/5.3 節已核准的認定;呼叫端仍可覆寫,
    但覆寫時必須能對應到一個同樣預先登錄、有文件依據的 N,不得為了讓檢定
    更容易通過而悄悄調小 ——本模組不做這個把關(不可能從程式碼判斷呼叫端
    的動機),但把預設值鎖在 10、且要求覆寫必須顯式傳參(不是偷偷改預設值),
    是這裡能做到的最大程度的「讓人不容易無意間退回 N=1」。
"""

from __future__ import annotations

import numpy as np
from scipy import stats

from analysis.sample_size import newey_west_effective_sample_size
from analysis.sharpe_difference import minimum_detectable_difference, sharpe_difference_test

# docs/statistical-methodology.md §4.4 已定案的專案級硬性下限,原樣沿用於
# 「配對交易差異序列」的有效樣本數 —— 不是本模組新發明的門檻。
N_EFF_FLOOR = 30


def _capped_block_length(n: int, diff: np.ndarray) -> float:
    """
    Politis–White 自動頻寬(sharpe_difference.py 內部用 `optimal_block_length`)
    是為 CP-004 那種 T≈3000+ 的日頻序列設計的。策略五 vs 策略一的配對交易數
    通常只有數十筆(docs/pass-b-results.md 主檢定點 76 筆),若自動估計給出
    的區塊長度相對 n 過大,重抽樣時實際能抽到的「獨立區塊數」會太少,
    bootstrap 分布會失真(退化成幾乎只有 1、2 種排列)。

    這裡在自動估計之上加一個與 n 成比例的上限:區塊長度不超過 n // 5,
    確保每次重抽樣至少能組出約 5 個區塊。這是一個工程上的保守選擇,不是
    嚴格推導出的最適值——**刻意選擇「保守」的方向**(較短的區塊低估自相關,
    傾向讓標準誤偏小、檢定偏寬鬆的方向不是本模組要的;但這裡的上限只在
    「自動估計异常大」時才生效,多數情況下 `optimal_block_length` 給出的值
    遠小於 `n // 5`,不受影響)。
    """
    if n < 2:
        return 1.0
    if diff.std(ddof=0) <= 0:
        return max(1.0, n ** (1 / 3))

    from arch.bootstrap import optimal_block_length

    est = float(np.mean(optimal_block_length(diff).values))
    if not np.isfinite(est) or est <= 0:
        est = max(1.0, n ** (1 / 3))

    cap = max(1, n // 5)
    return float(min(est, cap))


def _trade_level_n_eff(diff: np.ndarray) -> tuple[float, float]:
    """
    對配對差異序列 d_i = r_treatment_i − r_baseline_i(依交易序號排序)算
    Newey-West 有效樣本數,呼應 docs/strategy-5-hypothesis.md 3.4 節「交易
    報酬可能因連續突破訊號叢聚而自相關」的顧慮。退化情形(差異序列為常數,
    典型例子是兩組出場機制對這批交易完全沒有差異)直接回傳 IF=1、n_eff=n,
    避免把 acf() 在零變異數輸入上的未定義行為(nan/warning)傳遞出去。
    """
    n = len(diff)
    if diff.std(ddof=0) <= 1e-15:
        return float(n), 1.0
    result = newey_west_effective_sample_size(diff)
    return result.n_eff, result.inflation_factor


def paired_trade_test(
    r_baseline: np.ndarray,
    r_treatment: np.ndarray,
    n_boot: int = 5000,
    n_trials: int = 10,
    confidence: float = 0.95,
    random_state: int | None = 42,
) -> dict:
    """
    配對交易層級的 SR_trade 差異檢定(主要決策統計量)+ Wilcoxon 符號等級
    檢定(次要診斷)。

    :param r_baseline: 策略一(固定 ATR 停損)在 CP-003 固定進場點上的逐筆報酬,
        依進場時間排序
    :param r_treatment: 策略五(Kalman 濾波出場)在**同一組**進場點上的逐筆
        報酬,索引 i 必須與 r_baseline[i] 對應同一筆交易
    :param n_boot: bootstrap 重抽樣次數
    :param n_trials: 多重比較懲罰的 N,預設 10(docs/strategy-5-hypothesis.md
        5.2/5.3 節已核准的認定),見模組 docstring 末段
    :param confidence: `min_detectable_delta` 使用的單尾信心水準
    :param random_state: bootstrap 的隨機種子

    :return: dict —— 欄位語意:
        sr_baseline / sr_treatment: 兩組交易報酬各自的 SR_trade
        delta: SR_treatment − SR_baseline(正值 = 策略五優於策略一,方向對齊
            docs/strategy-5-hypothesis.md 第 2 節可證偽預測)
        se_hac, z_hac, p_hac: delta method + HAC 標準誤的常態近似檢定
            (⚠️ 小樣本下僅供對照,不作為決策依據,見模組 docstring)
        p_boot: studentized 循環區塊 bootstrap 的雙尾 p 值(**主要決策依據**)
        block_length: 實際使用的區塊長度(已套用 `_capped_block_length` 上限)
        rho_trade: r_baseline 與 r_treatment 的樣本相關係數 —— 預期偏高
            (兩者分岔前共用同一段價格路徑),對檢定力有利,見模組 docstring
        n_trades: 配對交易筆數(原始,未校正自相關)
        n_eff / inflation_factor: 配對差異序列的 Newey-West 有效樣本數 / IF
        n_eff_below_floor: n_eff 是否低於 docs/statistical-methodology.md
            §4.4 的硬性下限 30 —— 若為 True,`passes` 強制為 False
        wilcoxon_statistic / wilcoxon_p: 次要診斷(見模組 docstring),
            退化情形(差異序列全為 0)回傳 nan
        n_trials_penalty: 實際使用的 N(即傳入的 n_trials)
        min_detectable_delta: 給定 N 與 se_hac 下能被偵測到的最小 delta
            (analysis/sharpe_difference.py 既有邏輯,原樣重用)
        passes: 最終判定 —— delta ≥ min_detectable_delta 且 n_eff ≥ 30
            且非退化情形
        note: 僅退化情形(兩組交易完全相同)才出現,說明檢定退化的原因
    """
    r_baseline = np.asarray(r_baseline, dtype=float)
    r_treatment = np.asarray(r_treatment, dtype=float)
    if len(r_baseline) != len(r_treatment):
        raise ValueError("兩組交易報酬必須等長,且依相同的進場序號配對")
    n = len(r_baseline)
    if n < 2:
        raise ValueError("至少需要 2 筆配對交易才能計算差異")

    diff = r_treatment - r_baseline
    block_length = _capped_block_length(n, diff)

    # delta 定義為 SR_treatment - SR_baseline,故第一個引數傳 r_treatment。
    boot = sharpe_difference_test(
        r_treatment, r_baseline,
        n_boot=n_boot, block_length=block_length, random_state=random_state,
    )
    degenerate = "note" in boot

    n_eff, inflation_factor = _trade_level_n_eff(diff)
    n_eff_below_floor = bool(n_eff < N_EFF_FLOOR)

    nonzero = diff[diff != 0]
    if len(nonzero) < 2:
        wilcoxon_stat, wilcoxon_p = float("nan"), float("nan")
    else:
        try:
            w = stats.wilcoxon(diff)
            wilcoxon_stat, wilcoxon_p = float(w.statistic), float(w.pvalue)
        except ValueError:
            wilcoxon_stat, wilcoxon_p = float("nan"), float("nan")

    se = boot["se_hac"]
    threshold = minimum_detectable_difference(se, n_trials=n_trials, confidence=confidence)

    passes = (
        not degenerate
        and np.isfinite(boot["delta"])
        and np.isfinite(threshold)
        and boot["delta"] >= threshold
        and not n_eff_below_floor
    )

    rho_trade = float(np.corrcoef(r_baseline, r_treatment)[0, 1]) if n > 1 else float("nan")

    result = {
        "sr_baseline": boot["sr2"],
        "sr_treatment": boot["sr1"],
        "delta": boot["delta"],
        "se_hac": boot["se_hac"],
        "z_hac": boot["z_hac"],
        "p_hac": boot["p_hac"],
        "p_boot": boot["p_boot"],
        "block_length": boot["block_length"],
        "n_boot_valid": boot["n_boot_valid"],
        "rho_trade": rho_trade,
        "n_trades": n,
        "n_eff": n_eff,
        "inflation_factor": inflation_factor,
        "n_eff_below_floor": n_eff_below_floor,
        "wilcoxon_statistic": wilcoxon_stat,
        "wilcoxon_p": wilcoxon_p,
        "n_trials_penalty": n_trials,
        "min_detectable_delta": threshold,
        "passes": bool(passes),
    }
    if degenerate:
        result["note"] = boot["note"]
    return result

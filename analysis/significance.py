"""
docs/statistical-methodology.md 第 2 節:PSR/DSR、SR0 極值分布公式、
trial 相關性分群(2.3 節)、White's Reality Check / SPA(2.4 節)。

所有 Phi/Phi^-1 一律用 scipy.stats.norm,不用手算近似值
(statistical-methodology.md 2.2 節明訂「正式實作必須用 scipy.stats.norm.ppf/.cdf 精確計算」)。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

EULER_MASCHERONI = 0.5772156649015329


def probabilistic_sharpe_ratio(
    sr_hat: float, sr_star: float, n_eff: float, skew: float, kurtosis: float
) -> float:
    """
    statistical-methodology.md 2.2 節:
        PSR(SR*) = Phi[ (SR_hat - SR*) * sqrt(n_eff - 1)
                        / sqrt(1 - skew*SR_hat + ((kurtosis-1)/4)*SR_hat^2) ]
    kurtosis 為「非超額」峰度(常態分布下 = 3),與 scipy.stats.kurtosis(fisher=False) 一致。
    """
    if n_eff <= 1:
        return float("nan")

    denom = 1 - skew * sr_hat + ((kurtosis - 1) / 4) * sr_hat ** 2
    if denom <= 0:
        # 分母理論上應為正(偏度/峰度極端時可能失效)——回傳 nan 而非捏造結果,
        # 呼應 security-policy.md 5.2 節「輸入不合理時 fail closed」精神在統計計算上的對應。
        return float("nan")

    z = (sr_hat - sr_star) * np.sqrt(n_eff - 1) / np.sqrt(denom)
    return float(stats.norm.cdf(z))


def annualized_sr_to_per_trade(sr_annual: float, trades_per_year: float) -> float:
    """
    把年化 Sharpe(SR_daily,對齊 scope.md 的 1.0–1.5 目標)換算成逐筆交易
    Sharpe(SR_trade,minimum_track_record_length() 唯一合法的輸入單位)。

        SR_annual = SR_trade * sqrt(每年交易筆數)
        =>  SR_trade = SR_annual / sqrt(trades_per_year)

    ⚠️ 這個換算假設**逐筆交易報酬互相獨立**。實際上交易報酬有自相關
    (statistical-methodology.md 第 4 節,正是 IF > 1 的來源),因此本式只是
    量級換算,不是精確等式。它的用途是把 Phase 0 的年化目標翻譯成可以餵進
    MinTRL 的單位,**不可**反過來用它把回測算出的 SR_trade 包裝成年化績效
    對外報告 —— 年化績效必須用每日權益曲線實算。
    """
    if trades_per_year <= 0:
        return float("nan")
    return float(sr_annual / np.sqrt(trades_per_year))


def minimum_track_record_length(
    sr_hat: float,
    sr_star: float,
    skew: float,
    kurtosis: float,
    confidence: float = 0.95,
) -> float:
    """
    MinTRL(Minimum Track Record Length),Bailey & López de Prado (2012),
    "The Sharpe Ratio Efficient Frontier", Journal of Risk 15(2)。

    回答:「要有多少個(有效)觀測值,才能以 `confidence` 的信心水準宣稱
    真實 Sharpe 高於 `sr_star`?」

    推導 —— 把 probabilistic_sharpe_ratio() 對 n_eff 反解:
        PSR = Phi[ (SR_hat - SR*) * sqrt(n_eff - 1) / sqrt(D) ]  = confidence
        =>  Phi^-1(confidence) = (SR_hat - SR*) * sqrt(n_eff - 1) / sqrt(D)
        =>  n_eff = 1 + [Phi^-1(confidence)]^2 * D / (SR_hat - SR*)^2
        其中 D = 1 - skew*SR_hat + ((kurtosis-1)/4)*SR_hat^2

    為什麼這對本專案是「可行性檢查」而非錦上添花:
        docs/backtest-procedure.md 4.4 節已預警本策略交易頻率低、n_eff 可能貼近
        30 的硬性下限。MinTRL 把問題反過來問 —— 不是「我們有多少樣本」,而是
        「要證明這個 Sharpe,本來就需要多少樣本」。若答案遠超過可取得的資料量,
        代表這個策略在可接受的時間內**根本無法被證明**,那是在跑回測之前就該
        知道的事(見 docs/reading-list.md 與 docs/literature-review.md 第 3 節)。

    ⚠️ **時間單位陷阱(本函式最容易被誤用的地方)**

        輸出的 n_eff 是「觀測數」,而觀測的單位由 sr_hat 的時間單位決定。
        本專案的 n_eff **一律以交易筆數為單位**(statistical-methodology.md 第 1 節
        明訂,且該節詳述了為何不可用日曆天數),因此傳進來的必須是 **SR_trade
        (逐筆交易 Sharpe)**,不是年化的 SR_daily。

        誤傳年化值會讓所需樣本數被嚴重低估(年化 Sharpe 約為 SR_trade 的
        sqrt(年交易筆數) 倍,而本式對 sr_hat 是平方反比),算出「5 筆交易就能
        證明 Sharpe 0.8」這種明顯荒謬的結果。要從年化目標換算,請用
        annualized_sr_to_per_trade()。

    :param sr_hat: 觀察到(或假設)的 **SR_trade**,與 sr_star 必須同一時間單位
    :param sr_star: 門檻 Sharpe(常用 0,即「是否顯著優於零」)
    :param skew: 報酬序列偏度
    :param kurtosis: 「非超額」峰度(常態 = 3),對齊 scipy.stats.kurtosis(fisher=False)
    :param confidence: 信心水準,預設 0.95
    :return: 所需的最少有效觀測數 n_eff;若 sr_hat <= sr_star 回傳 inf
             (無論多少樣本都無法證明一個不比門檻好的 Sharpe)
    """
    if sr_hat <= sr_star:
        # 觀察值不優於門檻時,MinTRL 無定義 —— 回傳 inf 而非某個數字,
        # 避免呼叫端誤以為「再多跑一陣子就能證明」。
        return float("inf")

    denom = 1 - skew * sr_hat + ((kurtosis - 1) / 4) * sr_hat ** 2
    if denom <= 0:
        return float("nan")

    z = stats.norm.ppf(confidence)
    return float(1 + (z ** 2) * denom / ((sr_hat - sr_star) ** 2))


def mintrl_to_calendar_time(
    n_eff_required: float, trades_per_year: float, inflation_factor: float = 1.0
) -> dict:
    """
    把 MinTRL 的「有效觀測數」換算成日曆時間,這才是決策上真正要看的數字。

    自相關會讓實際交易筆數的資訊量打折(docs/statistical-methodology.md 第 4 節):
        每年有效樣本數 = trades_per_year / inflation_factor

    :param n_eff_required: minimum_track_record_length() 的輸出
    :param trades_per_year: 預期年交易筆數(BTC+ETH 合計)
    :param inflation_factor: Newey-West 校正的 IF,預設 1.0(無自相關)
    """
    if not np.isfinite(n_eff_required):
        return {
            "n_eff_required": n_eff_required,
            "raw_trades_required": float("inf"),
            "years_required": float("inf"),
            "note": "Sharpe 未優於門檻,無論多久都無法證明",
        }

    n_eff_per_year = trades_per_year / inflation_factor
    years = n_eff_required / n_eff_per_year

    return {
        "n_eff_required": n_eff_required,
        "raw_trades_required": n_eff_required * inflation_factor,
        "n_eff_per_year": n_eff_per_year,
        "years_required": years,
        "note": "raw_trades_required 是未經自相關校正的原始交易筆數",
    }


def expected_max_sharpe(sigma_sr: float, n_trials: int) -> float:
    """
    statistical-methodology.md 2.2 節:
        SR0 = E[max_N{SR_n}] ~= sigma_SR * [ (1-gamma)*Phi^-1(1 - 1/N)
                                              + gamma*Phi^-1(1 - 1/(N*e)) ]
    """
    if n_trials < 1:
        return 0.0
    if n_trials == 1:
        return 0.0  # 只試一次,沒有多重比較效應

    term1 = (1 - EULER_MASCHERONI) * stats.norm.ppf(1 - 1 / n_trials)
    term2 = EULER_MASCHERONI * stats.norm.ppf(1 - 1 / (n_trials * np.e))
    return float(sigma_sr * (term1 + term2))


def deflated_sharpe_ratio(
    sr_hat: float, n_eff: float, skew: float, kurtosis: float, n_trials: int, sigma_sr: float
) -> dict:
    """
    statistical-methodology.md 2.2 節:DSR = PSR(SR0)。
    通過門檻(2.5 節):DSR >= 0.95。
    """
    sr0 = expected_max_sharpe(sigma_sr, n_trials)
    dsr = probabilistic_sharpe_ratio(sr_hat, sr0, n_eff, skew, kurtosis)
    return {
        "sr0": sr0,
        "dsr": dsr,
        "passes_threshold": (not np.isnan(dsr)) and dsr >= 0.95,
    }


def cluster_correlated_trials(
    daily_return_matrix: np.ndarray, correlation_threshold: float = 0.9
) -> tuple[int, np.ndarray]:
    """
    statistical-methodology.md 2.3 節:hyperopt trial 間高度相關,原始 epoch 數會高估
    多重比較懲罰。對 trial 的日報酬序列兩兩計算相關係數,>threshold 視為同群,
    用分群後的群數 N' 取代原始 N。

    :param daily_return_matrix: shape (n_trials, n_days),每列一個 trial 的日報酬序列
    :return: (有效群數 N', 每個 trial 對應的群 id 陣列)
    """
    n_trials = daily_return_matrix.shape[0]
    if n_trials <= 1:
        return n_trials, np.zeros(n_trials, dtype=int)

    corr = np.corrcoef(daily_return_matrix)
    corr = np.nan_to_num(corr, nan=0.0)

    # Union-Find:相關係數超過門檻的 trial 視為連通,群數 = 連通分量數
    parent = list(range(n_trials))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for i in range(n_trials):
        for j in range(i + 1, n_trials):
            if corr[i, j] > correlation_threshold:
                union(i, j)

    roots = np.array([find(i) for i in range(n_trials)])
    unique_roots, cluster_ids = np.unique(roots, return_inverse=True)
    return len(unique_roots), cluster_ids


def reality_check_pvalue(
    candidate_returns: np.ndarray,
    n_boot: int = 5_000,
    block_length: float | None = None,
    random_state: int | None = 42,
) -> dict:
    """
    statistical-methodology.md 2.4 節:White's Reality Check(簡化實作,studentized statistic)。

    :param candidate_returns: shape (K, n_days) — K 個候選策略在同一段樣本外期間的逐日報酬
    :return: 觀察到的最佳候選統計量、bootstrap p-value

    做法(對時間軸做 stationary bootstrap,同時套用到全部 K 個候選,保留候選間同期相關性):
        1. 觀察統計量 V_obs = max_k( mean(r_k) / (std(r_k)/sqrt(n)) )   # studentized
        2. 對每次 bootstrap 重抽樣,先各自「去均值中心化」以模擬虛無假設(無真實 edge),
           再計算重抽樣後的 mean/std,取 max_k 的 studentized 統計量
        3. p-value = P(bootstrap statistic >= V_obs)
    """
    from arch.bootstrap import StationaryBootstrap

    K, n = candidate_returns.shape
    if n < 2 or K < 1:
        return {"p_value": float("nan"), "observed_statistic": float("nan")}

    means = candidate_returns.mean(axis=1)
    stds = candidate_returns.std(axis=1, ddof=1)
    stds_safe = np.where(stds > 0, stds, np.nan)
    studentized = means / (stds_safe / np.sqrt(n))
    v_obs = np.nanmax(studentized)

    # 虛無假設下:每個候選各自去均值(模擬「真實 edge 為 0」)
    centered = candidate_returns - means[:, None]

    if block_length is None:
        from analysis.sample_size import _newey_west_lag

        block_length = max(1.0, float(_newey_west_lag(n)))

    bs = StationaryBootstrap(block_length, centered.T, seed=random_state)

    boot_stats = np.empty(n_boot)
    for i, (data, _) in enumerate(bs.bootstrap(n_boot)):
        resampled = data[0].T  # shape (K, n)
        b_means = resampled.mean(axis=1)
        b_stds = resampled.std(axis=1, ddof=1)
        b_stds_safe = np.where(b_stds > 0, b_stds, np.nan)
        b_studentized = b_means / (b_stds_safe / np.sqrt(n))
        boot_stats[i] = np.nanmax(b_studentized)

    p_value = float(np.mean(boot_stats >= v_obs))

    return {
        "p_value": p_value,
        "observed_statistic": float(v_obs),
        "passes_threshold": p_value < 0.05,
    }

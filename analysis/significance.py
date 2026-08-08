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

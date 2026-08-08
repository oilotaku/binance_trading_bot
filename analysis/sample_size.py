"""
docs/statistical-methodology.md 第 4 節:Newey-West(HAC)有效樣本數校正 + block bootstrap 交叉驗證。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from statsmodels.tsa.stattools import acf


@dataclass
class SampleSizeResult:
    n: int
    n_eff: float
    inflation_factor: float
    lag_used: int
    acf_values: np.ndarray
    high_clustering_risk: bool  # IF > 3,statistical-methodology.md 4.4 節


def _newey_west_lag(n: int) -> int:
    """Newey-West (1994) 經驗法則:L = floor(4*(n/100)^(2/9))。statistical-methodology.md 4.2 節。"""
    return max(1, int(np.floor(4 * (n / 100) ** (2 / 9))))


def newey_west_effective_sample_size(
    returns: np.ndarray, lag: int | None = None
) -> SampleSizeResult:
    """
    statistical-methodology.md 4.2 節公式:
        IF = 1 + 2 * sum_{l=1}^{L} w_l * rho_l,   w_l = 1 - l/(L+1)  (Bartlett 權重)
        n_eff = n / IF
    """
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n < 2:
        return SampleSizeResult(n=n, n_eff=float(n), inflation_factor=1.0, lag_used=0,
                                 acf_values=np.array([]), high_clustering_risk=False)

    L = lag if lag is not None else _newey_west_lag(n)
    L = min(L, n - 1)

    acf_vals = acf(r, nlags=L, fft=True)  # acf_vals[0] == 1.0 (lag 0)
    rho = acf_vals[1: L + 1]
    weights = np.array([1 - l / (L + 1) for l in range(1, L + 1)])

    inflation_factor = 1 + 2 * np.sum(weights * rho)
    inflation_factor = max(inflation_factor, 1e-6)  # 理論上應 >=1,防禦負相關導致的病態值
    n_eff = n / inflation_factor

    return SampleSizeResult(
        n=n,
        n_eff=n_eff,
        inflation_factor=inflation_factor,
        lag_used=L,
        acf_values=acf_vals,
        high_clustering_risk=inflation_factor > 3,
    )


def newey_west_hac_variance(returns: np.ndarray, lag: int | None = None) -> float:
    """
    statistical-methodology.md 4.2 節「可直接實作,用於算穩健標準誤」的 Newey-West 估計式:
        Var_NW(r_bar) = (1/n^2) * [ sum_t(r_t - r_bar)^2
                                     + 2 * sum_{l=1}^{L} w_l * sum_{t=l+1}^{n}(r_t-r_bar)(r_{t-l}-r_bar) ]
    """
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n < 2:
        return float("nan")

    L = lag if lag is not None else _newey_west_lag(n)
    L = min(L, n - 1)

    r_bar = r.mean()
    centered = r - r_bar
    gamma0 = np.sum(centered ** 2)

    autocov_sum = 0.0
    for lag_l in range(1, L + 1):
        w_l = 1 - lag_l / (L + 1)
        cov_l = np.sum(centered[lag_l:] * centered[:-lag_l])
        autocov_sum += w_l * cov_l

    return (gamma0 + 2 * autocov_sum) / (n ** 2)


def block_bootstrap_sharpe_ci(
    returns: np.ndarray,
    n_boot: int = 10_000,
    block_length: float | None = None,
    ci: float = 0.95,
    random_state: int | None = 42,
) -> dict:
    """
    statistical-methodology.md 4.3 節:stationary bootstrap(Politis & Romano, 1994)交叉驗證。
    block_length 未指定時,用 1/newey_west_lag 當幾何分布參數 p 的倒數(該文件既有建議)。
    """
    from arch.bootstrap import StationaryBootstrap

    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n < 2:
        return {"median": float("nan"), "ci_lower": float("nan"), "ci_upper": float("nan"),
                "samples": np.array([])}

    if block_length is None:
        L_hat = _newey_west_lag(n)
        block_length = max(1.0, float(L_hat))

    bs = StationaryBootstrap(block_length, r, seed=random_state)

    def _sharpe(x: np.ndarray) -> float:
        std = x.std(ddof=1)
        return x.mean() / std if std > 0 else 0.0

    samples = np.array([_sharpe(data[0]) for data, _ in bs.bootstrap(n_boot)])

    alpha = 1 - ci
    lower = np.percentile(samples, 100 * alpha / 2)
    upper = np.percentile(samples, 100 * (1 - alpha / 2))

    return {
        "median": float(np.median(samples)),
        "ci_lower": float(lower),
        "ci_upper": float(upper),
        "samples": samples,
        "block_length": block_length,
    }

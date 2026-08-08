"""
兩個 Sharpe ratio 之差的穩健檢定 —— Ledoit & Wolf (2008) 的
HAC 標準誤 + studentized 循環區塊 bootstrap。

用途(CP-004 第二層):檢定策略的 Sharpe 是否顯著高於買進持有基準。

為什麼**必須**用穩健版本:
    Jobson–Korkie (1981) + Memmel (2003) 的閉式變異數假設常態獨立報酬。
    BTC 日報酬實測**峰度 18.8、偏度 −0.96**,且平方報酬 lag-1 自相關 0.106
    (波動叢聚顯著)。在這種分布下,常態公式會低估標準誤,把雜訊誤判為顯著。

Ledoit, O. & Wolf, M. (2008), "Robust performance hypothesis testing with the
Sharpe ratio", Journal of Empirical Finance 15(5), 850–859.
⚠️ 原文我方未取得,實作依 delta method 與 HAC 的標準結構自行推導
(見 docs/reading-list.md 第 8 節的一貫揭露)。第 3 節的推導已寫出以便檢查。
"""

from __future__ import annotations

import numpy as np
from scipy import stats


def _sharpe(r: np.ndarray) -> float:
    sd = r.std(ddof=0)
    return float(r.mean() / sd) if sd > 0 else float("nan")


def _gradient(r1: np.ndarray, r2: np.ndarray) -> np.ndarray:
    """
    Δ = SR₁ − SR₂,對動差向量 v = (μ₁, μ₂, γ₁, γ₂) 的梯度,其中 γ_i = E[r_i²]。

    推導(令 σ_i² = γ_i − μ_i²):
        SR_i = μ_i / σ_i
        ∂SR_i/∂μ_i = (σ_i² + μ_i²) / σ_i³ = γ_i / σ_i³
        ∂SR_i/∂γ_i = −μ_i / (2 σ_i³)

    故 ∇ = ( γ₁/σ₁³, −γ₂/σ₂³, −μ₁/(2σ₁³), μ₂/(2σ₂³) )
    """
    m1, m2 = r1.mean(), r2.mean()
    g1, g2 = (r1 ** 2).mean(), (r2 ** 2).mean()
    s1, s2 = np.sqrt(g1 - m1 ** 2), np.sqrt(g2 - m2 ** 2)
    return np.array([g1 / s1 ** 3, -g2 / s2 ** 3, -m1 / (2 * s1 ** 3), m2 / (2 * s2 ** 3)])


def _hac_covariance(y: np.ndarray, lag: int | None = None) -> np.ndarray:
    """
    Newey–West(Bartlett 核)長期共變異數矩陣估計。

    :param y: shape (T, k) 的動差序列,已去均值前由本函式處理
    :param lag: 截斷落後期;預設用 floor(4·(T/100)^(2/9)),即常見的自動規則

    ⚠️ Ledoit–Wolf 原文建議 prewhitening + Andrews 資料驅動頻寬。本實作採用
    較簡單的 Bartlett 核固定規則 —— 這是一個**已知的簡化**,在強波動叢聚下
    可能仍低估標準誤。因此本模組同時提供 bootstrap 版本,並以後者為準。
    """
    y = np.asarray(y, dtype=float)
    T, k = y.shape
    L = lag if lag is not None else int(np.floor(4 * (T / 100) ** (2 / 9)))
    L = max(0, min(L, T - 1))

    yc = y - y.mean(axis=0)
    psi = (yc.T @ yc) / T
    for l in range(1, L + 1):
        gamma = (yc[l:].T @ yc[:-l]) / T
        w = 1.0 - l / (L + 1)
        psi += w * (gamma + gamma.T)
    return psi


def sharpe_difference_se(r1: np.ndarray, r2: np.ndarray, lag: int | None = None) -> float:
    """Δ = SR₁ − SR₂ 的 HAC 標準誤(delta method)。"""
    r1, r2 = np.asarray(r1, float), np.asarray(r2, float)
    T = len(r1)
    grad = _gradient(r1, r2)
    y = np.column_stack([r1, r2, r1 ** 2, r2 ** 2])
    psi = _hac_covariance(y, lag)
    var = float(grad @ psi @ grad) / T
    return float(np.sqrt(var)) if var > 0 else float("nan")


def sharpe_difference_test(
    r1: np.ndarray,
    r2: np.ndarray,
    n_boot: int = 5000,
    block_length: float | None = None,
    random_state: int | None = 42,
) -> dict:
    """
    檢定 H₀:SR₁ = SR₂,用 studentized 循環區塊 bootstrap。

    studentize(把每次重抽的統計量除以自己的標準誤)是 Ledoit–Wolf 的核心 ——
    它讓 bootstrap 分布逼近樞紐量(pivotal),在肥尾與自相關下的覆蓋率遠優於
    直接對 Δ 做 bootstrap。

    :param block_length: 區塊長度;預設用 Politis–White 自動選擇
    :return: dict(delta, se_hac, z_hac, p_hac, p_boot, ...)
    """
    r1, r2 = np.asarray(r1, float), np.asarray(r2, float)
    if len(r1) != len(r2):
        raise ValueError("兩個報酬序列長度必須相同(必須是同期配對)")
    T = len(r1)

    delta = _sharpe(r1) - _sharpe(r2)
    se = sharpe_difference_se(r1, r2)

    # 退化情形:兩序列完全相同 -> 差與其變異數都恰為 0,統計量是 0/0。
    # 這在數學上無定義,但**答案是明確的**(沒有任何差異證據),
    # 回傳 p=1 而非 NaN,免得呼叫端把「無定義」誤讀成「檢定失敗」。
    if delta == 0.0 and (not np.isfinite(se) or se == 0.0):
        return {
            "sr1": _sharpe(r1), "sr2": _sharpe(r2), "delta": 0.0,
            "se_hac": 0.0, "z_hac": 0.0, "p_hac": 1.0, "p_boot": 1.0,
            "block_length": 0, "n_boot_valid": 0,
            "note": "兩序列完全相同,差恆為零,檢定退化",
        }

    z = delta / se if np.isfinite(se) and se > 0 else float("nan")
    p_hac = float(2 * (1 - stats.norm.cdf(abs(z)))) if np.isfinite(z) else float("nan")

    if block_length is None:
        from arch.bootstrap import optimal_block_length

        diff = r1 - r2
        # 差序列退化(例如兩序列相同)時 Politis–White 會除以零回傳 NaN,
        # 退回一個保守的固定區塊長度而非讓整個檢定崩潰。
        if diff.std() > 0:
            bl = float(np.mean(optimal_block_length(diff).values))
        else:
            bl = float("nan")
        block_length = bl if np.isfinite(bl) and bl > 0 else max(1.0, T ** (1 / 3))
    b = max(1, int(round(block_length)))

    rng = np.random.default_rng(random_state)
    n_blocks = int(np.ceil(T / b))
    boot_t = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, T, n_blocks)
        idx = np.concatenate([(np.arange(s, s + b) % T) for s in starts])[:T]
        b1, b2 = r1[idx], r2[idx]
        d_b = _sharpe(b1) - _sharpe(b2)
        se_b = sharpe_difference_se(b1, b2)
        boot_t[i] = (d_b - delta) / se_b if np.isfinite(se_b) and se_b > 0 else np.nan

    valid = boot_t[np.isfinite(boot_t)]
    p_boot = float((np.sum(np.abs(valid) >= abs(z)) + 1) / (len(valid) + 1)) if np.isfinite(z) else float("nan")

    return {
        "sr1": _sharpe(r1),
        "sr2": _sharpe(r2),
        "delta": float(delta),
        "se_hac": float(se),
        "z_hac": float(z),
        "p_hac": p_hac,
        "p_boot": p_boot,
        "block_length": b,
        "n_boot_valid": int(len(valid)),
    }


def minimum_detectable_difference(
    se: float, n_trials: int = 1, sigma_sr: float | None = None, confidence: float = 0.95
) -> float:
    """
    在給定標準誤下,能被偵測到的最小 Sharpe 差距(含多重比較懲罰)。

    與 analysis/significance.py 的 DSR 邏輯一致:先扣掉「試了 N 次、純靠運氣
    能得到的最佳差距」SR0,剩下的才要通過 z·SE。

    :param sigma_sr: N 次試驗間 Δ 的離散度;預設取 se(純雜訊下的合理基準)
    """
    from analysis.significance import expected_max_sharpe

    if not np.isfinite(se) or se <= 0:
        return float("nan")
    sr0 = expected_max_sharpe(se if sigma_sr is None else sigma_sr, n_trials)
    return float(sr0 + stats.norm.ppf(confidence) * se)

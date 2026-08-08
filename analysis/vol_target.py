"""
策略二的核心邏輯:波動度目標化(CP-004 / CP-005 已核准的規格)。

刻意實作成**與 Freqtrade 無關的純函式**,理由:
    第一層的判定是組合層的權益曲線問題,
    不是逐筆交易問題。用純函式在報酬序列上直接計算,可以完全避免
    Freqtrade 的交易撮合、部位精度、最小下單量等細節污染統計判定。
    Freqtrade 端的實作(執行層)另行對接,但**判定以本模組為準**。

事前指定的參數,全部來自已核准文件,不得在此優化:
    W                = 20      strategy-2-hypothesis.md 2.1 節
    SIGMA_TARGET     = 0.123   CP-006 修正(原 0.25 的推導把回撤當成百分比線性)
    MAX_EXPOSURE     = 0.80    CP-005 3.5 節(risk-policy.md 4.3 節的合併名目上限)
    REBALANCE_BAND   = 0.20    CP-005 第 4 節
    DD_LOOKBACK      = 365     CP-006 選項 A(取自 risk-policy.md 5.3 節既有數字)
"""

from __future__ import annotations

import numpy as np

# --- CP-004 / CP-005 定案的治理數字。改動任一個都必須走變更提案流程。---
W = 20
# CP-006 修正:σ_target 原為 0.25,推導時誤把回撤當成隨曝險線性縮放。
# 回撤在**對數空間**才線性:w₀ = −ln(1−0.30)/(−ln(1−0.8832)) = 0.1661,
# 而非 0.30/0.8832 = 0.3397。σ_target = 0.1661 × 73.8% ≈ 0.123。
SIGMA_TARGET = 0.123
MAX_EXPOSURE = 0.80
REBALANCE_BAND = 0.20

# CP-006 選項 A:回撤斜坡改用**滾動視窗**,而非歷史全期。
# 全期回撤會讓曝險歸零後權益凍結、回撤永不恢復、曝險永遠是 0(吸收態);
# 滾動視窗讓舊高點隨時間滾出視窗,策略得以恢復。
# 365 取自 risk-policy.md 5.3 節 kill switch 的 lookback_period_candles,
# 是既有的治理數字,不是本次新挑的值。
DD_LOOKBACK = 365

# CP-005 3.4 節:線性降風險斜坡的兩個端點(皆為已核准的治理數字)
DD_RAMP_START = 0.30  # CP-004 第一層回撤上限
DD_RAMP_END = 0.40    # CP-005 3.3 節 kill switch

PERIODS_PER_YEAR = 365


def drawdown_ramp_factor(drawdown: float) -> float:
    """
    CP-005 3.4 節:把 kill switch 從懸崖變成斜坡。

        factor(DD) = clip( (40% − DD) / (40% − 30%), 0, 1 )

    回撤 ≤30% 時不減碼;逼近 40% 時曝險已平滑降到 0,而不是在 40% 那一刻
    突然全部平倉。**新增自由參數為 0** —— 兩個端點都是既有的已核准數字。
    """
    span = DD_RAMP_END - DD_RAMP_START
    return float(np.clip((DD_RAMP_END - drawdown) / span, 0.0, 1.0))


def realized_volatility(log_returns: np.ndarray, window: int = W) -> np.ndarray:
    """
    年化已實現波動,**嚴格只用過去 `window` 期**。

    ⚠️ 輸出第 t 個元素對應「在第 t 期收盤後已知」的波動估計,可直接用於
    決定第 t+1 期的曝險。前 `window` 個元素為 NaN(資料不足)。
    這個對齊是無前視偏誤的關鍵,有測試把關。
    """
    r = np.asarray(log_returns, dtype=float)
    n = len(r)
    out = np.full(n, np.nan)
    if n < window:
        return out
    for t in range(window, n + 1):
        out[t - 1] = r[t - window : t].std(ddof=0) * np.sqrt(PERIODS_PER_YEAR)
    return out


def simulate(
    benchmark_log_returns: np.ndarray,
    sigma_target: float = SIGMA_TARGET,
    window: int = W,
    max_exposure: float = MAX_EXPOSURE,
    rebalance_band: float = REBALANCE_BAND,
    apply_drawdown_ramp: bool = True,
    dd_lookback: int = DD_LOOKBACK,
    cost_per_turnover: float = 0.0015,
) -> dict:
    """
    在基準的報酬序列上模擬波動度目標化。

    :param cost_per_turnover: 每單位換手的成本。取 0.1% 手續費 + 5bps 滑價 = 0.15%,
        對齊 backtest-procedure.md 第 2 節。**成本必須計入**,否則換手的代價被隱藏
        (strategy-2-hypothesis.md 第 8 節列為第四個可能的失敗方式)。

    :return: dict(returns, exposure, turnover, ...) —— 報酬為**扣除成本後**

    ⛔ 每一期的曝險只依賴到前一期為止的資訊:
       - 波動估計用 t-1 為止的視窗
       - 回撤斜坡用 t-1 為止的權益曲線
       兩者都有測試把關(截斷不變性)。
    """
    r = np.asarray(benchmark_log_returns, dtype=float)
    n = len(r)
    vol = realized_volatility(r, window)

    exposure = np.zeros(n)
    net = np.zeros(n)
    turnover = np.zeros(n)

    equity_log = 0.0
    equity_path = np.zeros(n + 1)   # equity_path[t] = 第 t 期**開始前**的對數權益
    current_w = 0.0

    for t in range(n):
        sigma_hat = vol[t - 1] if t > 0 else np.nan

        if not np.isfinite(sigma_hat) or sigma_hat <= 0:
            # 暖身期:資料不足以估計波動,不持倉。
            # 不用任何預設曝險 —— 那等於對未知波動做了一個沒有依據的假設。
            target = 0.0
        else:
            # ⚠️ 滾動視窗回撤(CP-006 選項 A),不是歷史全期。
            # 只用 t 之前已實現的權益,無前視。
            lo = max(0, t - dd_lookback)
            peak_log = equity_path[lo : t + 1].max()
            dd = 1.0 - np.exp(equity_log - peak_log)
            ramp = drawdown_ramp_factor(dd) if apply_drawdown_ramp else 1.0
            target = min(max_exposure, sigma_target * ramp / sigma_hat)

        # 再平衡帶:相對偏離未達門檻就不動,避免換手成本侵蝕
        if current_w == 0.0:
            do_rebalance = target > 0.0
        else:
            do_rebalance = abs(target - current_w) / current_w >= rebalance_band

        if do_rebalance:
            turnover[t] = abs(target - current_w)
            current_w = target

        exposure[t] = current_w
        net[t] = current_w * r[t] - turnover[t] * cost_per_turnover

        equity_log += net[t]
        equity_path[t + 1] = equity_log

    return {
        "returns": net,
        "exposure": exposure,
        "turnover": turnover,
        "volatility": vol,
        "mean_exposure": float(np.mean(exposure[window:])) if n > window else float("nan"),
        "total_turnover": float(turnover.sum()),
    }


def zero_skill_control(
    benchmark_log_returns: np.ndarray, exposure: float, cost_per_turnover: float = 0.0015
) -> dict:
    """
    對照組:**固定曝險**,不隨波動調整。

    這是 CP-004 第一層的零技巧基準 —— 縮放曝險不改變 Sharpe(對數空間線性縮放),
    策略二若沒有比它好,就等於什麼都沒做。

    ⚠️ CP-006 修正:`exposure` 應傳入**策略實際實現的平均曝險**,而非由
    「MDD上限 / 基準MDD」推算。假說本來就寫的是「在相同的平均曝險下」比較,
    而先前用 0.3397 推算的作法既算錯了(回撤在對數空間才線性),也讓兩組的
    風險水位不一致(實測 0.0478 對 0.3397,差 7 倍)。直接對齊實現曝險,
    比較才是公平的,也才是假說原本指定的。

    固定曝險理論上零換手;但實務上每日再平衡回固定權重仍有換手,
    此處保守地忽略(對照組因此**略佔優勢**,使比較不偏袒策略)。
    """
    r = np.asarray(benchmark_log_returns, dtype=float)
    return {"returns": exposure * r, "exposure": np.full(len(r), exposure)}

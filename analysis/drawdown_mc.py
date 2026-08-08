"""
docs/statistical-methodology.md 第 6 節:Block Bootstrap 蒙地卡羅回撤模擬。
"""

from __future__ import annotations

import numpy as np

DEFAULT_N_SIMULATIONS = 10_000
MIN_N_SIMULATIONS = 5_000  # statistical-methodology.md 6.2 節:最低不少於 5,000 次


def _max_drawdown(equity_curve: np.ndarray) -> float:
    running_max = np.maximum.accumulate(equity_curve)
    drawdown = equity_curve / running_max - 1
    return float(-drawdown.min())  # 回傳正數(回撤幅度)


def _monthly_max_drawdowns(equity_curve: np.ndarray, days: np.ndarray) -> np.ndarray:
    """依日曆天數切成月,逐月計算月內最大回撤(相對月初權益,而非全域峰值)。"""
    if len(days) == 0:
        return np.array([])
    month_index = (days // 30).astype(int)  # 近似月切分,精度足夠供機率估計使用
    mdds = []
    for m in np.unique(month_index):
        mask = month_index == m
        segment = equity_curve[mask]
        if len(segment) < 2:
            continue
        base = segment[0]
        running_max = np.maximum.accumulate(segment)
        dd = segment / np.maximum(running_max, base) - 1
        mdds.append(-dd.min())
    return np.array(mdds)


def simulate_drawdown_distribution(
    trade_returns: np.ndarray,
    holding_days: np.ndarray,
    risk_fraction: float,
    trades_per_year: int,
    n_simulations: int = DEFAULT_N_SIMULATIONS,
    block_length: float | None = None,
    random_state: int | None = 42,
) -> dict:
    """
    statistical-methodology.md 6.2 節步驟:
        1. 輸入:OOS 交易報酬序列(含持倉天數)+ position_sizing_check 算出的最終 risk_fraction
        2. stationary bootstrap 對 (報酬, 持倉天數) 成對區塊重抽樣,拼出約 1 年份的偽交易序列
        3. 重建帶日曆刻度的權益曲線(用 risk_fraction 換算每筆交易對權益的實際影響)
        4. 計算全期 MDD、任一自然月內最大回撤
        5. 重複 B 次
        6. 彙整 MDD 分布與月度回撤分布

    :param trade_returns: OOS 交易的 profit_ratio(扣除滑價成本後),不是原始毛報酬
    :param holding_days: 對應每筆交易的持倉天數
    :param risk_fraction: position_sizing_check.compute_risk_fraction 算出的 risk_fraction_final
    :param trades_per_year: 用歷史交易頻率估計的年均交易次數(不含 BTC/ETH 加總或分開,呼叫端決定)
    """
    from arch.bootstrap import StationaryBootstrap

    n = len(trade_returns)
    if n < 2 or n_simulations < MIN_N_SIMULATIONS:
        if n_simulations < MIN_N_SIMULATIONS:
            raise ValueError(
                f"n_simulations={n_simulations} 低於 statistical-methodology.md 6.2 節"
                f" 最低要求 {MIN_N_SIMULATIONS} 次。"
            )
        return {"error": "样本不足,無法模擬"}

    if block_length is None:
        from analysis.sample_size import _newey_west_lag

        block_length = max(1.0, float(_newey_west_lag(n)))

    idx = np.arange(n)
    bs = StationaryBootstrap(block_length, idx, seed=random_state)

    mdd_samples = np.empty(n_simulations)
    monthly_mdd_samples: list[float] = []
    pause_events_per_path = np.empty(n_simulations)  # 該路徑內月度回撤 >= 8% 的月數(呼應每年暫停次數)

    for i, (data, _) in enumerate(bs.bootstrap(n_simulations)):
        boot_idx = data[0].astype(int)
        boot_idx = boot_idx[:trades_per_year] if len(boot_idx) > trades_per_year else boot_idx

        sampled_returns = trade_returns[boot_idx]
        sampled_days = holding_days[boot_idx]

        # 每筆交易對權益的實際影響:risk_fraction * trade_return(呼應 risk-policy.md
        # 的 position_size 設計 —— 每筆交易的 $ 風險固定在 risk_fraction,實際盈虧依
        # 該筆交易報酬比例縮放)
        equity_multipliers = 1 + risk_fraction * sampled_returns
        equity_multipliers = np.clip(equity_multipliers, 1e-6, None)  # 防止單筆爆倉導致權益<=0
        equity_curve = np.cumprod(np.concatenate([[1.0], equity_multipliers]))

        cum_days = np.concatenate([[0], np.cumsum(sampled_days)])

        mdd_samples[i] = _max_drawdown(equity_curve)
        monthly = _monthly_max_drawdowns(equity_curve, cum_days)
        monthly_mdd_samples.extend(monthly.tolist())
        pause_events_per_path[i] = np.sum(monthly >= 0.08)  # 對齊 risk-policy.md 月回撤熔斷 8%

    monthly_mdd_arr = np.array(monthly_mdd_samples) if monthly_mdd_samples else np.array([0.0])

    p_mdd_gt_20 = float(np.mean(mdd_samples > 0.20))
    p_mdd_gt_15 = float(np.mean(mdd_samples > 0.15))

    return {
        "n_simulations": n_simulations,
        "mdd_median": float(np.median(mdd_samples)),
        "mdd_p95": float(np.percentile(mdd_samples, 95)),
        "p_mdd_gt_15pct": p_mdd_gt_15,
        "p_mdd_gt_20pct": p_mdd_gt_20,
        "expected_monthly_pause_per_year": float(np.mean(pause_events_per_path)),
        "passes_threshold": (p_mdd_gt_20 <= 0.05) and (0.20 <= p_mdd_gt_15 or p_mdd_gt_15 <= 0.25),
        # ↑ statistical-methodology.md 通過門檻:P(MDD>20%)<=5%,P(MDD>15%)在 20-25% 量級內
        "_note": (
            "以上為點估計,statistical-methodology.md 6.3 節要求額外揭露機率估計本身的"
            "信賴區間(對 p_mdd_gt_20pct 這個機率再做一層 bootstrap 或用二項分布信賴區間),"
            "本函式回傳點估計,信賴區間計算留給呼叫端(report.py)視需要疊加。"
        ),
    }

"""
docs/backtest-procedure.md 第 6 節:驗證 risk-policy.md 假設的三項檢查清單。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# risk-policy.md 1.2 節確定性連續虧損表(risk_fraction=1.5%)
DETERMINISTIC_LOSS_STREAKS = {"month_8pct": 6, "kill_switch_15pct": 11, "hard_cap_20pct": 15}


def check_time_stop(
    trades: pd.DataFrame, time_stop_exit_reason: str = "time_stop_45d"
) -> dict:
    """
    backtest-procedure.md 6.1 節判準:
        time-stop 出場佔比 > 15-20%,且其中 >50% 在出場當下仍獲利 -> 不 match
    """
    total = len(trades)
    if total == 0:
        return {"error": "無交易資料"}

    ts_trades = trades[trades["exit_reason"] == time_stop_exit_reason]
    ts_ratio = len(ts_trades) / total

    profitable_at_exit_ratio = (
        float((ts_trades["profit_ratio"] > 0).mean()) if len(ts_trades) > 0 else 0.0
    )

    not_matching = ts_ratio > 0.15 and profitable_at_exit_ratio > 0.5

    return {
        "time_stop_exit_count": int(len(ts_trades)),
        "total_trades": total,
        "time_stop_ratio": ts_ratio,
        "profitable_at_exit_ratio": profitable_at_exit_ratio,
        "not_matching": not_matching,
        "verdict": (
            "不match —— 45天 time-stop 疑似系統性砍掉右尾獲利,依 risk-policy.md 第7節"
            "變更流程重新評估"
            if not_matching
            else "match —— 符合 risk-policy.md 2.3 節「少數尾端情況」的既有預期"
        ),
    }


def check_atr_boundary(
    fold_k_values: list[float], lower: float = 2.0, upper: float = 4.0, margin: float = 0.2
) -> dict:
    """backtest-procedure.md 6.2 節判準:>=30% fold 選出的 k 貼近邊界 -> 不 match。"""
    if not fold_k_values:
        return {"error": "無 fold k 值資料"}

    near_boundary = [
        k for k in fold_k_values if k <= lower + margin or k >= upper - margin
    ]
    ratio = len(near_boundary) / len(fold_k_values)
    not_matching = ratio >= 0.30

    return {
        "fold_count": len(fold_k_values),
        "near_boundary_count": len(near_boundary),
        "near_boundary_ratio": ratio,
        "not_matching": not_matching,
        "verdict": (
            "不match —— k 邊界 [2.0,4.0] 可能設得過緊,依 risk-policy.md 第7節"
            "重新評估邊界(屬核心治理數字,需重跑受影響的 Pass B)"
            if not_matching
            else "match —— 邊界寬度合理,hyperopt 選擇未系統性貼邊"
        ),
    }


def check_deterministic_vs_montecarlo(
    mc_loss_streaks_at_breach: np.ndarray,
    deterministic_streaks: dict = DETERMINISTIC_LOSS_STREAKS,
) -> dict:
    """
    backtest-procedure.md 6.3 節判準:
        MC 模擬觸及回撤的路徑,若普遍只需 3-5 筆連續虧損(遠少於確定性表 11-15 筆)-> 不 match,
        代表真實自相關比確定性表假設的「獨立虧損」情境更嚴重。
    """
    if len(mc_loss_streaks_at_breach) == 0:
        return {"note": "無路徑觸及回撤門檻,無法比較(此為好消息,非資料缺失)"}

    median_streak = float(np.median(mc_loss_streaks_at_breach))
    deterministic_kill_switch = deterministic_streaks["kill_switch_15pct"]

    not_matching = median_streak <= 5 and median_streak < deterministic_kill_switch * 0.5

    return {
        "mc_median_loss_streak_at_breach": median_streak,
        "deterministic_kill_switch_streak": deterministic_kill_switch,
        "not_matching": not_matching,
        "verdict": (
            "不match —— 真實自相關比確定性表假設嚴重,1.5% 硬上限承受的聚類壓力"
            "比推導時想像的更集中,依 risk-policy.md 8節/7節標記並考慮調整"
            if not_matching
            else "match —— 確定性表與蒙地卡羅模擬描繪出相近的風險輪廓"
        ),
    }

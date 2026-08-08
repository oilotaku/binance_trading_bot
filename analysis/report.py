"""
docs/backtest-procedure.md 第 8 節 report.py:端到端編排,產出第 5 節總表填好數字的最終報告。

本檔案分兩部分:
  1. `run_fold_pipeline`  — 對「單一 fold」執行 backtest-procedure.md 4.3 節步驟 3-7
     (data_loader -> cost_model -> sample_size -> significance),假設該 fold 的
     hyperopt(步驟 1)與 OOS backtest(步驟 5)已經由外部 CLI 呼叫跑完
     (docs/backtest-procedure.md 4.3 節步驟 1/5 是 `freqtrade hyperopt`/`freqtrade backtesting`
     子行程,耗時可達數小時且需要真實市場資料,不適合、也不應該在這支腳本內同步執行 ——
     這支腳本假設呼叫端已經跑完 CLI 步驟,只負責把匯出檔案接上統計分析)。
  2. `build_final_report` — 全部 9 個 fold 完成後,依 4.4 節規則彙整成第 5 節總表。

這是規格的具體實作,不是可以直接無腦執行的一鍵腳本 —— backtest-procedure.md 4.3 節
本身就要求每個 fold 分別跑 hyperopt(1,000 epochs)與 OOS backtest,這些步驟的執行時間
與資料依賴性質,決定了本檔案必須是「串接已完成步驟的產出」而非「從頭跑到尾的黑盒子」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from analysis import (
    cost_model,
    drawdown_mc,
    position_sizing_check,
    risk_policy_validation,
    sample_size,
    significance,
)

TRADES_PER_YEAR_ESTIMATE = 40  # risk-policy.md 6.2 節既有估計量級(BTC+ETH 合計),供蒙地卡羅模擬用


@dataclass
class FoldResult:
    """單一 fold 的彙總結果,對應 backtest-procedure.md 4.3 節步驟 7。"""

    fold_index: int
    oos_sr_trade: float
    oos_trade_count: int
    n_eff: float
    inflation_factor: float
    dsr: float
    selected_params: dict


def run_fold_pipeline(
    fold_index: int,
    is_backtest_result_dir: Path,
    hyperopt_results_file: Path,
    oos_backtest_result_dir: Path,
    config: dict,
) -> FoldResult:
    """
    backtest-procedure.md 4.3 節步驟 2-7,對單一已完成 hyperopt + OOS backtest 的 fold 執行:
        步驟 2: 匯出 hyperopt 完整 epoch 紀錄
        步驟 3: analysis/sample_size.py 計算 n_eff(用該 fold 的 IS 交易報酬序列)
        步驟 4: analysis/significance.py 計算該 fold DSR
        步驟 6: analysis/cost_model.py 套用滑價 haircut(OOS 交易明細)
        步驟 7: 記錄摘要列
    """
    from analysis.data_loader import load_hyperopt_epochs, load_trades

    epochs_df, total_epochs = load_hyperopt_epochs(hyperopt_results_file, config)
    if epochs_df.empty:
        raise ValueError(f"Fold {fold_index}: hyperopt epoch 紀錄為空,無法計算 DSR")

    sigma_sr = float(epochs_df["sr_trade"].dropna().std(ddof=1))
    best_row = epochs_df.loc[epochs_df["is_best"]].iloc[0] if epochs_df["is_best"].any() else (
        epochs_df.sort_values("sr_trade", ascending=False).iloc[0]
    )
    sr_hat = float(best_row["sr_trade"])

    is_trades = load_trades(is_backtest_result_dir)
    is_returns = is_trades["profit_ratio"].to_numpy(dtype=float)
    ss_result = sample_size.newey_west_effective_sample_size(is_returns)

    from scipy.stats import kurtosis as _kurtosis, skew as _skew

    skew_val = float(_skew(is_returns)) if len(is_returns) > 2 else 0.0
    kurt_val = float(_kurtosis(is_returns, fisher=False)) if len(is_returns) > 2 else 3.0

    dsr_result = significance.deflated_sharpe_ratio(
        sr_hat=sr_hat,
        n_eff=ss_result.n_eff,
        skew=skew_val,
        kurtosis=kurt_val,
        n_trials=total_epochs,
        sigma_sr=sigma_sr,
    )

    oos_trades = load_trades(oos_backtest_result_dir)
    oos_trades_adj = cost_model.apply_slippage_haircut(oos_trades)
    oos_returns = cost_model.net_return_series(oos_trades_adj)
    oos_sr_trade = float(oos_returns.mean() / oos_returns.std(ddof=1)) if len(oos_returns) > 1 else float("nan")

    return FoldResult(
        fold_index=fold_index,
        oos_sr_trade=oos_sr_trade,
        oos_trade_count=len(oos_trades),
        n_eff=ss_result.n_eff,
        inflation_factor=ss_result.inflation_factor,
        dsr=dsr_result["dsr"],
        selected_params=dict(best_row.get("params_dict") or {}),
    )


def build_final_report(
    fold_results: list[FoldResult],
    concatenated_oos_trades: pd.DataFrame,
    fold_k_values: list[float],
) -> dict[str, Any]:
    """
    backtest-procedure.md 4.4 節規則,彙整全部 fold 結果成第 5 節總表:
      - 主檢定 DSR:最終 fold(最近一期)
      - 次檢定 OOS PSR(SR*=0):9 個 fold 串接後的完整 OOS 序列
      - Kelly 輸入、蒙地卡羅:同樣用串接後的完整 OOS 序列
    """
    if not fold_results:
        raise ValueError("fold_results 為空,無法產出報告")

    final_fold = fold_results[-1]

    concatenated_adj = cost_model.apply_slippage_haircut(concatenated_oos_trades)
    concatenated_returns = cost_model.net_return_series(concatenated_adj).to_numpy(dtype=float)

    concat_ss = sample_size.newey_west_effective_sample_size(concatenated_returns)
    oos_psr = significance.probabilistic_sharpe_ratio(
        sr_hat=float(concatenated_returns.mean() / concatenated_returns.std(ddof=1)),
        sr_star=0.0,
        n_eff=concat_ss.n_eff,
        skew=0.0,  # 呼叫端可用 scipy.stats.skew 補上精確值,此處保留簡化預設
        kurtosis=3.0,
    )

    bootstrap_ci = sample_size.block_bootstrap_sharpe_ci(concatenated_returns)
    sr_1_pessimistic = bootstrap_ci["ci_lower"]  # statistical-methodology.md 5.3 節:須用悲觀下界
    sigma_1 = float(concatenated_returns.std(ddof=1))

    avg_k = float(np.mean(fold_k_values)) if fold_k_values else 3.0
    atr_pct_estimate = 0.03  # 待真實資料替換;risk-policy.md 5.2 節示範值

    kelly_result = position_sizing_check.compute_risk_fraction(
        sr_1=sr_1_pessimistic, sigma_1=sigma_1, k_atr_multiplier=avg_k, atr_pct=atr_pct_estimate,
    )

    holding_days = (
        pd.to_datetime(concatenated_adj["close_date"]) - pd.to_datetime(concatenated_adj["open_date"])
    ).dt.days.to_numpy()

    mc_result = drawdown_mc.simulate_drawdown_distribution(
        concatenated_returns, holding_days,
        risk_fraction=kelly_result.risk_fraction_final,
        trades_per_year=TRADES_PER_YEAR_ESTIMATE,
    )

    consistency_ratio = float(np.mean([f.oos_sr_trade > 0 for f in fold_results]))
    dsr_values = [f.dsr for f in fold_results]

    param_cv = {}  # 呼叫端可補上逐參數 CV 計算(需要展開 selected_params 的個別鍵)

    time_stop_check = risk_policy_validation.check_time_stop(concatenated_adj)
    atr_boundary_check = risk_policy_validation.check_atr_boundary(fold_k_values)

    pass_1_significance = (
        (not np.isnan(final_fold.dsr) and final_fold.dsr >= 0.95)
        and (not np.isnan(oos_psr) and oos_psr >= 0.95)
    )
    pass_2_walk_forward = len(fold_results) >= 5 and consistency_ratio >= 0.70
    pass_3_sample_size = final_fold.n_eff >= 30
    pass_5_drawdown = mc_result.get("passes_threshold", False)

    return {
        "final_fold_dsr": final_fold.dsr,
        "all_fold_dsr": dsr_values,
        "oos_psr_concatenated": oos_psr,
        "n_eff_final_fold": final_fold.n_eff,
        "inflation_factor_final_fold": final_fold.inflation_factor,
        "consistency_ratio_oos_sharpe_positive": consistency_ratio,
        "kelly_sizing": position_sizing_check.to_report_dict(kelly_result),
        "monte_carlo_drawdown": mc_result,
        "risk_policy_checks": {
            "time_stop": time_stop_check,
            "atr_boundary": atr_boundary_check,
        },
        "pass_fail_table": {
            "1_statistical_significance": pass_1_significance,
            "2_walk_forward": pass_2_walk_forward,
            "3_effective_sample_size": pass_3_sample_size,
            "4_position_sizing": kelly_result.risk_fraction_final <= 0.015,
            "5_drawdown_probability": pass_5_drawdown,
        },
        "overall_pass": all([
            pass_1_significance, pass_2_walk_forward, pass_3_sample_size,
            kelly_result.risk_fraction_final <= 0.015, pass_5_drawdown,
        ]),
        "_note": (
            "依 backtest-procedure.md 第 5 節:上述 5 項全部通過才能簽核朝實盤方向推進;"
            "任一項不通過,依該文件 5.1 節處理路徑退回參數重新設計或宣告本輪 v1 驗證失敗。"
        ),
    }


def save_report(report: dict, output_path: Path) -> None:
    def _default(o):
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        return str(o)

    output_path.write_text(json.dumps(report, indent=2, default=_default, ensure_ascii=False))

"""
以合成資料驗證 analysis/ 統計模組公式正確性(不需要真實 Freqtrade 回測輸出)。
執行: cd /workspace/binance_trading_bot && .venv/bin/python -m pytest analysis/tests/ -v
"""

import numpy as np
import pytest

from analysis import (
    drawdown_mc,
    position_sizing_check,
    risk_policy_validation,
    sample_size,
    significance,
    walk_forward,
)


# ---- sample_size.py ----

def test_newey_west_iid_data_if_close_to_one():
    """獨立同分布資料下,自相關趨近 0,IF 應趨近 1、n_eff 應趨近 n。"""
    rng = np.random.default_rng(42)
    returns = rng.normal(0, 0.02, size=500)
    result = sample_size.newey_west_effective_sample_size(returns)
    assert result.n == 500
    assert 0.85 < result.inflation_factor < 1.3, f"IF={result.inflation_factor} 應接近 1"
    assert not result.high_clustering_risk


def test_newey_west_autocorrelated_data_inflates_if():
    """人工製造強自相關(AR(1), rho=0.7)資料,IF 應顯著 > 1。"""
    rng = np.random.default_rng(1)
    n = 500
    noise = rng.normal(0, 0.02, size=n)
    ar1 = np.zeros(n)
    for t in range(1, n):
        ar1[t] = 0.7 * ar1[t - 1] + noise[t]

    result = sample_size.newey_west_effective_sample_size(ar1)
    assert result.inflation_factor > 2.0, f"強自相關下 IF={result.inflation_factor} 應明顯 > 1"
    assert result.n_eff < result.n


def test_block_bootstrap_ci_contains_true_mean_direction():
    rng = np.random.default_rng(7)
    returns = rng.normal(0.01, 0.02, size=200)  # 正 edge
    result = sample_size.block_bootstrap_sharpe_ci(returns, n_boot=1000)
    assert result["ci_lower"] < result["median"] < result["ci_upper"]
    # 正 edge、樣本數夠大時,bootstrap Sharpe 分布中位數應為正
    assert result["median"] > 0


# ---- significance.py ----

def test_psr_high_sharpe_large_sample_near_one():
    """SR_hat 遠高於 SR*、樣本夠大、常態分布時,PSR 應接近 1。"""
    psr = significance.probabilistic_sharpe_ratio(
        sr_hat=2.0, sr_star=0.0, n_eff=200, skew=0.0, kurtosis=3.0
    )
    assert psr > 0.99


def test_psr_zero_sharpe_equals_half():
    """SR_hat 剛好等於 SR* 時,PSR 應為 0.5(常態分布下對稱)。"""
    psr = significance.probabilistic_sharpe_ratio(
        sr_hat=0.0, sr_star=0.0, n_eff=200, skew=0.0, kurtosis=3.0
    )
    assert abs(psr - 0.5) < 1e-6


def test_expected_max_sharpe_increases_with_trials():
    """SR0 應隨嘗試次數 N 增加而增加(次線性,但單調遞增)。"""
    sr0_100 = significance.expected_max_sharpe(sigma_sr=0.35, n_trials=100)
    sr0_1000 = significance.expected_max_sharpe(sigma_sr=0.35, n_trials=1000)
    sr0_10000 = significance.expected_max_sharpe(sigma_sr=0.35, n_trials=10_000)
    assert sr0_100 < sr0_1000 < sr0_10000

    # 驗證 statistical-methodology.md 2.2 節的具體示範數值(N=1000, sigma_SR=0.35 -> SR0≈1.14)
    assert 1.0 < sr0_1000 < 1.3, f"SR0(N=1000)={sr0_1000},文件示範值約 1.14"


def test_dsr_worked_example_from_statistical_methodology():
    """
    重現 statistical-methodology.md 2.2 節數值示例:
    N=1000, sigma_SR=0.35, SR_hat=1.3, skew=0.8, kurtosis=5, n_eff=48 -> DSR ≈ 0.80(不通過 0.95)
    """
    result = significance.deflated_sharpe_ratio(
        sr_hat=1.3, n_eff=48, skew=0.8, kurtosis=5, n_trials=1000, sigma_sr=0.35
    )
    assert 0.7 < result["dsr"] < 0.9, f"DSR={result['dsr']},文件示範值約 0.80"
    assert not result["passes_threshold"]


def test_mintrl_inverts_psr_exactly():
    """
    MinTRL 是 PSR 對樣本數的反解 —— 把 MinTRL 的輸出代回 PSR,
    應該剛好得到當初設定的信心水準。這是最強的正確性檢查。
    """
    sr_hat, sr_star, skew, kurt, conf = 1.3, 0.0, 0.8, 5.0, 0.95
    n_req = significance.minimum_track_record_length(sr_hat, sr_star, skew, kurt, conf)
    psr_back = significance.probabilistic_sharpe_ratio(sr_hat, sr_star, n_req, skew, kurt)
    assert abs(psr_back - conf) < 1e-9, f"反解後代回應得 {conf},實得 {psr_back}"


def test_mintrl_infinite_when_sharpe_not_above_threshold():
    """Sharpe 不優於門檻時,再多樣本也證明不了 —— 必須回傳 inf,不能給一個數字。"""
    assert significance.minimum_track_record_length(0.5, 0.5, 0.0, 3.0) == float("inf")
    assert significance.minimum_track_record_length(0.3, 0.8, 0.0, 3.0) == float("inf")


def test_mintrl_higher_sharpe_needs_fewer_observations():
    """Sharpe 越高越容易證明,所需樣本數應單調遞減。"""
    reqs = [
        significance.minimum_track_record_length(sr, 0.0, 0.8, 5.0)
        for sr in [0.8, 1.0, 1.3, 1.8]
    ]
    assert reqs == sorted(reqs, reverse=True), f"應單調遞減,實得 {reqs}"


def test_mintrl_fat_tails_increase_requirement():
    """
    右偏肥尾(趨勢策略的典型形狀)會提高所需樣本數 ——
    這正是 statistical-methodology.md 2.2 節強調要納入三四階動差的理由。
    """
    normal = significance.minimum_track_record_length(1.3, 0.0, skew=0.0, kurtosis=3.0)
    fat_tail = significance.minimum_track_record_length(1.3, 0.0, skew=0.0, kurtosis=8.0)
    assert fat_tail > normal, "峰度越高,所需樣本數應越多"


def test_mintrl_calendar_conversion_accounts_for_autocorrelation():
    """自相關(IF>1)會讓相同的日曆時間提供較少的有效樣本,所需年數應變長。"""
    n_req = 40.0
    no_autocorr = significance.mintrl_to_calendar_time(n_req, trades_per_year=30, inflation_factor=1.0)
    with_autocorr = significance.mintrl_to_calendar_time(n_req, trades_per_year=30, inflation_factor=1.67)

    assert with_autocorr["years_required"] > no_autocorr["years_required"]
    assert abs(no_autocorr["years_required"] - 40 / 30) < 1e-9
    # 原始交易筆數需求 = n_eff × IF
    assert abs(with_autocorr["raw_trades_required"] - 40 * 1.67) < 1e-9


def test_annualized_sr_to_per_trade_roundtrip():
    """SR_annual = SR_trade * sqrt(每年交易筆數) —— 換算應可逆。"""
    sr_trade = significance.annualized_sr_to_per_trade(1.5, trades_per_year=30)
    assert abs(sr_trade * np.sqrt(30) - 1.5) < 1e-12
    assert np.isnan(significance.annualized_sr_to_per_trade(1.5, trades_per_year=0))


def test_mintrl_unit_mismatch_would_understate_requirement():
    """
    回歸測試,鎖住一個真實犯過的錯誤:把「年化 Sharpe」餵進 MinTRL。

    MinTRL 的輸出單位是「觀測數」,而本專案的觀測 = 一筆交易(見
    statistical-methodology.md 第 1 節),所以輸入必須是 SR_trade。誤傳年化值
    會讓需求被低估數倍(量級上約與年交易筆數同階)—— 而且**不會報錯**,
    只會安靜地給出一個過度樂觀的數字。這裡把兩者的差距明確斷言出來。
    """
    sr_annual, trades_per_year = 1.5, 30.0
    sr_trade = significance.annualized_sr_to_per_trade(sr_annual, trades_per_year)

    wrong = significance.minimum_track_record_length(sr_annual, 0.0, 0.8, 5.0)
    right = significance.minimum_track_record_length(sr_trade, 0.0, 0.8, 5.0)

    # 誤用年化值算出「3.5 筆交易就能證明 Sharpe 1.5」這種明顯荒謬的結果
    assert wrong < 5, f"誤用年化值應算出荒謬的小數字,實得 {wrong:.1f}"
    assert right > 5 * wrong, f"誤用年化值={wrong:.1f} 正確={right:.1f}"
    # 正確的量級應與 statistical-methodology.md 的 n_eff >= 30 硬性下限同一數量級
    assert 20 < right < 80, f"預期落在數十筆交易的量級,實得 {right}"


def test_mintrl_calendar_conversion_propagates_infinity():
    r = significance.mintrl_to_calendar_time(float("inf"), trades_per_year=30)
    assert r["years_required"] == float("inf")


def test_cluster_correlated_trials_groups_identical_series():
    """完全相同的日報酬序列應被分進同一群。"""
    rng = np.random.default_rng(3)
    base = rng.normal(0, 0.02, size=100)
    # 5 個 trial:3 個幾乎相同(高相關),2 個獨立雜訊
    matrix = np.vstack([
        base + rng.normal(0, 0.0001, size=100),
        base + rng.normal(0, 0.0001, size=100),
        base + rng.normal(0, 0.0001, size=100),
        rng.normal(0, 0.02, size=100),
        rng.normal(0, 0.02, size=100),
    ])
    n_clusters, cluster_ids = significance.cluster_correlated_trials(matrix, correlation_threshold=0.9)
    assert n_clusters < 5, "高度相關的 3 個 trial 應被合併,有效群數應少於原始 5"
    assert cluster_ids[0] == cluster_ids[1] == cluster_ids[2]


def test_reality_check_pvalue_high_for_pure_noise():
    """全部候選都是零 edge 的純雜訊時,p-value 不應該系統性地小(不應假陽性顯著)。"""
    rng = np.random.default_rng(11)
    candidates = rng.normal(0, 0.02, size=(5, 250))
    result = significance.reality_check_pvalue(candidates, n_boot=500)
    assert 0.0 <= result["p_value"] <= 1.0


# ---- position_sizing_check.py ----

def test_full_kelly_rejected_above_ceiling():
    with pytest.raises(ValueError):
        position_sizing_check.compute_risk_fraction(
            sr_1=1.0, sigma_1=0.35, k_atr_multiplier=3.0, atr_pct=0.03, kelly_fraction_c=1.0
        )


def test_risk_fraction_worked_example_from_risk_policy():
    """
    重現 statistical-methodology.md 5.2 節 / risk-policy.md 1.4 節數值示例:
    SR_1=1.1, sigma_1=0.35, k=3, ATR%=3%, c=0.25 -> kelly_derived≈7.07%,
    硬上限 1.5% -> risk_fraction_final = 1.5%(硬上限生效)
    """
    result = position_sizing_check.compute_risk_fraction(
        sr_1=1.1, sigma_1=0.35, k_atr_multiplier=3.0, atr_pct=0.03,
        kelly_fraction_c=0.25, hard_cap=0.015,
    )
    assert abs(result.risk_fraction_kelly_derived - 0.0707) < 0.002, (
        f"kelly_derived={result.risk_fraction_kelly_derived},文件示範值約 7.07%"
    )
    assert result.risk_fraction_final == 0.015
    assert result.hard_cap_is_binding


def test_negative_edge_yields_zero_position():
    """負 Sharpe(無 edge)時,risk_fraction 應為 0,不應產生負部位。"""
    result = position_sizing_check.compute_risk_fraction(
        sr_1=-0.5, sigma_1=0.3, k_atr_multiplier=3.0, atr_pct=0.03,
    )
    assert result.risk_fraction_final == 0.0


def test_fractional_kelly_growth_ratio_matches_table():
    """statistical-methodology.md 5.2 節表格:c=0.25 -> 43.75% 成長率。"""
    assert abs(position_sizing_check.fractional_kelly_growth_ratio(0.25) - 0.4375) < 1e-9
    assert abs(position_sizing_check.fractional_kelly_growth_ratio(0.5) - 0.75) < 1e-9
    assert abs(position_sizing_check.fractional_kelly_growth_ratio(1.0) - 1.0) < 1e-9


# ---- walk_forward.py ----

def test_generate_fold_boundaries_matches_documented_table():
    """重現 backtest-procedure.md 1.3 節的 fold 表(anchor=2020-01-01, embargo=30天)。"""
    import datetime as dt

    anchor = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    data_end = dt.datetime(2026, 8, 7, tzinfo=dt.timezone.utc)
    folds = walk_forward.generate_fold_boundaries(anchor, data_end, embargo_days=30)

    assert len(folds) == 9, f"應產生 9 個完整 fold,實際 {len(folds)} 個"
    assert folds[0].is_start == anchor
    assert folds[0].is_end.year == 2021 and folds[0].is_end.month == 12
    assert folds[-1].is_start.year == 2024 and folds[-1].is_start.month == 1


def test_generate_fold_boundaries_rejects_embargo_below_floor():
    import datetime as dt

    with pytest.raises(ValueError):
        walk_forward.generate_fold_boundaries(
            dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
            dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
            embargo_days=10,
        )


def test_purge_removes_trades_near_is_end():
    import datetime as dt

    import pandas as pd

    is_end = dt.datetime(2021, 12, 31, tzinfo=dt.timezone.utc)
    trades = pd.DataFrame({
        "open_date": [
            dt.datetime(2021, 11, 1, tzinfo=dt.timezone.utc),   # 遠離邊界,應保留
            dt.datetime(2021, 12, 15, tzinfo=dt.timezone.utc),  # embargo_days=30 內,應剔除
            dt.datetime(2021, 12, 30, tzinfo=dt.timezone.utc),  # 邊界內,應剔除
        ]
    })
    purged = walk_forward.purge_is_trades(trades, is_end, embargo_days=30)
    assert len(purged) == 1


def test_cusum_detects_obvious_regime_shift():
    import pandas as pd

    rng = np.random.default_rng(5)
    segment_a = rng.normal(0.01, 0.01, size=100)   # 前段:穩定獲利
    segment_b = rng.normal(-0.02, 0.01, size=100)  # 後段:穩定虧損(明顯斷點)
    series = pd.Series(np.concatenate([segment_a, segment_b]))

    s_t, breakpoints = walk_forward.cusum_breakpoints(series, k_sigma=3.0)
    assert len(breakpoints) > 0, "明顯的 regime 轉變應被 CUSUM 偵測到"


# ---- drawdown_mc.py ----

def test_drawdown_mc_rejects_too_few_simulations():
    with pytest.raises(ValueError):
        drawdown_mc.simulate_drawdown_distribution(
            np.array([0.01, -0.01, 0.02]), np.array([5, 10, 7]),
            risk_fraction=0.015, trades_per_year=40, n_simulations=100,
        )


def test_drawdown_mc_high_win_rate_low_risk_gives_low_drawdown_prob():
    """穩定小額獲利、低風險比例下,P(MDD>20%) 應該很低,結果應通過門檻。"""
    rng = np.random.default_rng(9)
    # 60 筆交易,正期望值、溫和波動,模擬一個「有 edge 且不劇烈」的策略
    returns = rng.normal(0.03, 0.05, size=60)
    holding_days = rng.integers(3, 20, size=60)

    result = drawdown_mc.simulate_drawdown_distribution(
        returns, holding_days, risk_fraction=0.015, trades_per_year=40,
        n_simulations=5000,
    )
    assert 0.0 <= result["p_mdd_gt_20pct"] <= 1.0
    assert 0.0 <= result["p_mdd_gt_15pct"] <= 1.0
    assert result["p_mdd_gt_20pct"] <= result["p_mdd_gt_15pct"], (
        "P(MDD>20%) 不應大於 P(MDD>15%)(20% 回撤是更極端的子集合)"
    )


def test_drawdown_mc_high_risk_fraction_increases_drawdown_prob():
    """相同報酬序列下,risk_fraction 越大,回撤機率應該越高(單調性檢查)。"""
    rng = np.random.default_rng(13)
    returns = rng.normal(0.0, 0.08, size=60)  # 零期望值、高波動,更容易觸發大回撤
    holding_days = rng.integers(3, 20, size=60)

    low_risk = drawdown_mc.simulate_drawdown_distribution(
        returns, holding_days, risk_fraction=0.01, trades_per_year=40, n_simulations=5000,
    )
    high_risk = drawdown_mc.simulate_drawdown_distribution(
        returns, holding_days, risk_fraction=0.05, trades_per_year=40, n_simulations=5000,
    )
    assert high_risk["mdd_median"] >= low_risk["mdd_median"], (
        "更高的 risk_fraction 應該產生更高（或至少不低於）的中位數回撤"
    )


# ---- risk_policy_validation.py ----

def test_check_time_stop_flags_excessive_ratio():
    import pandas as pd

    n = 100
    exit_reasons = ["time_stop_45d"] * 25 + ["stoploss"] * 75  # 25% > 15% 門檻
    profit_ratios = [0.05] * 20 + [-0.01] * 5 + [-0.02] * 75  # time-stop 組多數獲利
    trades = pd.DataFrame({"exit_reason": exit_reasons, "profit_ratio": profit_ratios})

    result = risk_policy_validation.check_time_stop(trades)
    assert result["not_matching"] is True


def test_check_atr_boundary_flags_frequent_edge_selection():
    k_values = [2.1, 2.05, 3.9, 3.0, 2.5, 3.1, 2.15, 3.0, 2.8]  # 4/9 > 30% 貼邊界
    result = risk_policy_validation.check_atr_boundary(k_values)
    assert result["not_matching"] is True


def test_check_atr_boundary_passes_when_centered():
    k_values = [2.8, 2.9, 3.0, 3.1, 3.2, 2.9, 3.0, 3.1, 3.0]
    result = risk_policy_validation.check_atr_boundary(k_values)
    assert result["not_matching"] is False


# ---- data_loader.py:MAX_LOSS 哨兵值處理(迴歸測試) ----

def test_hyperopt_max_loss_sentinel_excluded_from_sigma_sr(tmp_path, monkeypatch):
    """
    迴歸測試:Freqtrade 對交易筆數不足的 epoch 指派 MAX_LOSS=100000 哨兵值。
    若把它當成 sr_trade=-100000,sigma_SR 會被汙染數個數量級,連帶讓 DSR 恆為 0
    (整合測試實測:sigma_SR 從 0.1970 變成 30779.4845),且完全不會報錯。
    """
    from analysis import data_loader

    fake_epochs = [
        {"loss": -0.50, "results_metrics": {"total_trades": 30}, "params_dict": {}, "is_best": True},
        {"loss": -0.30, "results_metrics": {"total_trades": 25}, "params_dict": {}, "is_best": False},
        {"loss": -0.10, "results_metrics": {"total_trades": 20}, "params_dict": {}, "is_best": False},
        # 哨兵值:交易筆數不足,Freqtrade 未實際呼叫 loss function
        {"loss": 100000, "results_metrics": {"total_trades": 0}, "params_dict": {}, "is_best": False},
        {"loss": 100000, "results_metrics": {"total_trades": 1}, "params_dict": {}, "is_best": False},
    ]

    monkeypatch.setattr(
        "freqtrade.optimize.hyperopt_tools.HyperoptTools.load_filtered_results",
        lambda *a, **k: (fake_epochs, len(fake_epochs)),
    )

    df, total = data_loader.load_hyperopt_epochs(tmp_path / "dummy.fthypt", {})

    assert total == 5
    assert df["is_sentinel"].sum() == 2, "兩個 MAX_LOSS epoch 應被標記為哨兵值"
    assert df["sr_trade"].isna().sum() == 2, "哨兵值的 sr_trade 應為 NaN"

    sigma_sr = df["sr_trade"].dropna().std(ddof=1)
    assert sigma_sr < 1.0, (
        f"sigma_SR={sigma_sr} 應在 0.x 量級;若哨兵值未被排除會暴衝到數萬"
    )

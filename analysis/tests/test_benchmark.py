"""驗證 analysis/benchmark.py。基準是治理參數,其定義必須被鎖住。"""

import numpy as np
import pandas as pd
import pytest

from analysis import benchmark as bm


def test_performance_summary_on_known_series():
    """固定漂移、零波動 -> Sharpe 無定義,回撤為 0,CAGR 可解析驗證。"""
    r = np.full(365, np.log(1.10) / 365)
    s = bm.performance_summary(r)
    assert abs(s["cagr"] - 0.10) < 1e-9
    assert s["max_drawdown"] == pytest.approx(0.0, abs=1e-12)


def test_max_drawdown_matches_hand_computation():
    """+50% 後 -40%,最大回撤應為 40%。"""
    r = np.array([np.log(1.5), np.log(0.6)])
    assert bm.performance_summary(r)["max_drawdown"] == pytest.approx(0.4, abs=1e-12)


def test_sharpe_annualization_uses_365_not_252():
    """
    加密 24/7 交易,年化必須用 sqrt(365)。用 252 會系統性高估 Sharpe 約 20%,
    是 statistical-methodology.md 第 1 節明確警告過的錯誤。
    """
    rng = np.random.default_rng(0)
    r = rng.normal(0.0005, 0.02, 4000)
    s = bm.performance_summary(r)
    expected = r.mean() / r.std() * np.sqrt(365)
    assert abs(s["sharpe"] - expected) < 1e-12
    assert abs(s["sharpe"] - r.mean() / r.std() * np.sqrt(252)) > 1e-3


def test_zero_skill_baseline_uses_log_space_scaling():
    """
    CP-006 修正:回撤在對數空間才隨曝險線性縮放,不是百分比空間。
    回撤上限 30%、基準回撤 88.32% -> 曝險應為約 16.61%,不是天真的 30/88.32=33.97%
    (那個算法正是 docs/strategy-4-run-1-invalid.md 第 1 節記錄的錯誤)。
    """
    b = bm.zero_skill_baseline(0.8832, 0.30)
    assert abs(b["exposure"] - 0.1661) < 1e-3
    assert b["exposure"] == b["required_retention"]


def test_zero_skill_baseline_actually_produces_target_mdd():
    """
    端到端驗證:用推算出的曝險縮放基準,實際 MDD 應等於 mdd_cap ——
    這正是第一次策略四執行失敗的地方(推算出的曝險給出 MDD 51.78% 而非 30%)。
    """
    rng = np.random.default_rng(0)
    bench_r = rng.normal(0.0008, 0.03, 5000)
    bench_mdd = bm.performance_summary(bench_r)["max_drawdown"]

    w = bm.zero_skill_baseline(bench_mdd, 0.30)["exposure"]
    scaled_mdd = bm.performance_summary(w * bench_r)["max_drawdown"]
    assert abs(scaled_mdd - 0.30) < 0.01


def test_zero_skill_baseline_capped_at_full_exposure():
    """基準回撤本來就低於上限時,曝險不應超過 1.0。"""
    assert bm.zero_skill_baseline(0.20, 0.30)["exposure"] == 1.0


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="加總"):
        bm.benchmark_log_returns(None, ("A", "B"), (0.5, 0.4))


def test_pairs_and_weights_length_must_match():
    with pytest.raises(ValueError, match="長度"):
        bm.benchmark_log_returns(None, ("A", "B"), (1.0,))


def test_governance_constants_are_pinned():
    """
    這些是 CP-004 定案的治理數字。此測試的用途是讓任何改動都必須明確地
    改測試 —— 避免有人「順手」調了基準或回撤上限而無人察覺。
    """
    assert bm.BENCHMARK_PAIRS == ("BTC/USDT", "ETH/USDT")
    assert bm.BENCHMARK_WEIGHTS == (0.5, 0.5)
    assert bm.MAX_DRAWDOWN_CAP == 0.30

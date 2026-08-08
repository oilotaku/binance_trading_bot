"""
驗證 analysis/vol_target.py。全部使用合成資料 —— 這裡測的是機制正確性,
不是策略績效;績效判定在預先登錄完成後另行執行。
"""

import numpy as np
import pytest

from analysis import vol_target as vt


def test_governance_constants_are_pinned():
    """CP-004/CP-005 定案的數字。任何改動都必須明確改測試,不能悄悄調。"""
    assert vt.W == 20
    assert vt.SIGMA_TARGET == 0.25
    assert vt.MAX_EXPOSURE == 0.80
    assert vt.REBALANCE_BAND == 0.20
    assert (vt.DD_RAMP_START, vt.DD_RAMP_END) == (0.30, 0.40)


@pytest.mark.parametrize("dd,expected", [
    (0.0, 1.0), (0.20, 1.0), (0.30, 1.0),
    (0.325, 0.75), (0.35, 0.5), (0.375, 0.25),
    (0.40, 0.0), (0.50, 0.0),
])
def test_drawdown_ramp_matches_cp005_table(dd, expected):
    assert vt.drawdown_ramp_factor(dd) == pytest.approx(expected, abs=1e-12)


def test_realized_volatility_is_causal():
    """
    ⛔ 第 t 個元素只能用到 r[0..t]。驗證:改動尾端不影響前段。
    """
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.03, 400)
    full = vt.realized_volatility(r)[:250]
    trunc = vt.realized_volatility(r[:250])
    assert np.allclose(full, trunc, equal_nan=True)


def test_realized_volatility_recovers_known_sigma():
    rng = np.random.default_rng(1)
    sigma_d = 0.02
    r = rng.normal(0, sigma_d, 20000)
    v = vt.realized_volatility(r, window=250)
    assert abs(np.nanmean(v) - sigma_d * np.sqrt(365)) < 0.02


def test_exposure_is_inverse_to_volatility():
    """
    波動加倍,目標曝險應減半 —— 這是機制的核心。

    刻意選在 0.8 上限**不會綁定**的波動區間:σ=0.01/日(年化 19%)會讓目標
    曝險算出 1.31 而被上限截斷,比值就測不出 2 了(第一版測試踩過這個坑)。
    """
    rng = np.random.default_rng(2)
    lo = rng.normal(0, 0.03, 600)   # 年化約 57% -> 目標約 0.44,未觸及上限
    hi = rng.normal(0, 0.06, 600)   # 年化約 115% -> 目標約 0.22
    e_lo = vt.simulate(lo, cost_per_turnover=0.0)["exposure"][100:].mean()
    e_hi = vt.simulate(hi, cost_per_turnover=0.0)["exposure"][100:].mean()
    assert e_hi < e_lo
    assert 1.6 < e_lo / e_hi < 2.5, f"比值 {e_lo/e_hi:.2f} 偏離預期的約 2"


def test_exposure_never_exceeds_cap():
    """極低波動下曝險必須被 0.8 擋住(CP-005 3.5 節的合併名目上限)。"""
    r = np.full(500, 1e-6)
    out = vt.simulate(r, cost_per_turnover=0.0)
    assert np.nanmax(out["exposure"]) <= vt.MAX_EXPOSURE + 1e-12


def test_simulation_is_causal():
    """整條模擬的截斷不變性 —— 前段結果不得受尾端資料影響。"""
    rng = np.random.default_rng(3)
    r = rng.normal(0.0005, 0.03, 500)
    full = vt.simulate(r)["returns"][:300]
    trunc = vt.simulate(r[:300])["returns"]
    assert np.allclose(full, trunc)


def test_rebalance_band_reduces_turnover():
    """再平衡帶的唯一作用就是降低換手 —— 若沒降低,它就沒有存在意義。"""
    rng = np.random.default_rng(4)
    r = rng.normal(0, 0.03, 1500)
    wide = vt.simulate(r, rebalance_band=0.20)["total_turnover"]
    tight = vt.simulate(r, rebalance_band=0.0)["total_turnover"]
    assert wide < tight


def test_drawdown_ramp_cuts_exposure_in_deep_drawdown():
    """
    持續下跌造成深度回撤後,斜坡應把曝險壓到接近 0。
    這是 CP-005 3.4 節取代「暫停新倉」的機制,必須真的會動作。
    """
    r = np.concatenate([np.zeros(30), np.full(400, -0.004)])
    with_ramp = vt.simulate(r, cost_per_turnover=0.0, apply_drawdown_ramp=True)
    without = vt.simulate(r, cost_per_turnover=0.0, apply_drawdown_ramp=False)
    assert with_ramp["exposure"][-1] < without["exposure"][-1]
    assert with_ramp["exposure"][-1] < 0.05


def test_zero_skill_control_preserves_sharpe():
    """
    對照組的核心性質:縮放曝險不改變 Sharpe。
    這正是「第一層是及格線而非成就」的數學理由。
    """
    rng = np.random.default_rng(5)
    r = rng.normal(0.0005, 0.03, 3000)
    ctrl = vt.zero_skill_control(r, 0.3397)["returns"]
    assert abs(r.mean() / r.std() - ctrl.mean() / ctrl.std()) < 1e-12


def test_costs_reduce_returns():
    rng = np.random.default_rng(6)
    r = rng.normal(0, 0.03, 1000)
    free = vt.simulate(r, cost_per_turnover=0.0)["returns"].sum()
    paid = vt.simulate(r, cost_per_turnover=0.0015)["returns"].sum()
    assert paid < free


def test_warmup_holds_no_position():
    """暖身期資料不足以估波動,必須空手 —— 不得用任何預設曝險填補。"""
    rng = np.random.default_rng(7)
    out = vt.simulate(rng.normal(0, 0.03, 100))
    assert np.all(out["exposure"][: vt.W] == 0.0)

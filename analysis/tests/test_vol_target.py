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
    assert vt.SIGMA_TARGET == 0.123   # CP-006 修正(原 0.25)
    assert vt.DD_LOOKBACK == 365      # CP-006 選項 A
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
    深度回撤期間,斜坡應把曝險壓到接近 0。
    這是 CP-005 3.4 節取代「暫停新倉」的機制,必須真的會動作。
    """
    r = np.concatenate([np.zeros(30), np.full(400, -0.004)])
    with_ramp = vt.simulate(r, cost_per_turnover=0.0, apply_drawdown_ramp=True)
    without = vt.simulate(r, cost_per_turnover=0.0, apply_drawdown_ramp=False)
    assert with_ramp["exposure"].min() < 0.01
    assert with_ramp["exposure"].sum() < without["exposure"].sum()


def test_ramp_is_not_an_absorbing_state():
    """
    ⛔ CP-006 選項 A 存在的唯一理由。

    第一次執行時斜坡用**歷史全期**回撤:曝險歸零 → 權益凍結 → 回撤永遠停在 40%
    → 曝險永遠是 0。策略在第 826 天死亡,其後 75% 的樣本期只是一條水平線
    (見 docs/strategy-2-run-1-invalid.md 第 2 節)。

    改用滾動視窗後,舊高點會隨時間滾出視窗,策略必須能恢復。
    """
    crash = np.concatenate([
        np.zeros(30),
        np.full(120, -0.010),      # 急跌造成深度回撤
        np.random.default_rng(0).normal(0, 0.02, 600),   # 之後回到正常波動
    ])
    out = vt.simulate(crash, cost_per_turnover=0.0)
    e = out["exposure"]
    assert e[140:200].min() < 0.05, "急跌期間斜坡未生效"
    assert e[-200:].mean() > 0.05, "回撤過後未恢復 —— 仍是吸收態"


def test_rolling_window_underreports_sustained_decline():
    """
    ⚠️ 選項 A 的已知代價,固定成測試以免被遺忘。

    持續緩跌時,滾動視窗的參考高點會跟著往下滑,滾動回撤因此永遠偏小,
    斜坡不會持續生效。實測(見下)全期回撤 45.9% 時滾動回撤只有 39.9%,
    曝險已恢復到上限。

    **這不是 bug,是滾動視窗的定義使然** —— 它換掉了吸收態,代價是對
    「長期緩跌」的保護較弱。此性質必須在結果解讀時納入考量。
    """
    r = np.concatenate([np.zeros(30), np.full(400, -0.004)])
    out = vt.simulate(r, cost_per_turnover=0.0)
    eq = np.concatenate([[0.0], np.cumsum(out["returns"])])
    t = 429
    full_dd = 1 - np.exp(eq[t] - eq[: t + 1].max())
    roll_dd = 1 - np.exp(eq[t] - eq[max(0, t - vt.DD_LOOKBACK) : t + 1].max())
    assert full_dd > roll_dd, "滾動回撤應小於全期回撤"
    assert out["exposure"][t] > 0.5, "參考高點滑落後曝險應已恢復"


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

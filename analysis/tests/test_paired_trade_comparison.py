"""驗證 analysis/paired_trade_comparison.py —— 合成配對交易資料,已知答案。

比照 analysis/tests/test_sharpe_difference.py 的模式:只驗證統計機制本身
是否正確運作(型一誤差可控、真實效應可被偵測、退化輸入不崩潰),不涉及任何
真實 BTC/ETH 回測資料 —— docs/strategy-5-hypothesis.md 第 9 節執行順序
第 6 步明訂本階段(Phase 1 方法定案)不得碰真實資料。
"""

import numpy as np
import pytest

from analysis import paired_trade_comparison as ptc


def _skewed_trade_returns(n, rng, win_prob=0.45):
    """
    模擬策略一診斷出的典型形狀(docs/post-mortem-strategy-1.md):
    多數交易小虧、少數交易大賺(右偏),賺賠比略高於 1:1。
    """
    wins = rng.random(n) < win_prob
    win_mag = rng.lognormal(mean=np.log(0.08), sigma=0.9, size=n)
    loss_mag = -rng.uniform(0.03, 0.10, size=n)
    return np.where(wins, win_mag, loss_mag), wins


def _clustered_common_component(n, rng, phi=0.8):
    """
    AR(1) regime 指標 -> 決定逐筆交易的勝率,使交易結果沿交易序號叢聚
    (呼應 docs/statistical-methodology.md 第 3 節:連續突破訊號不是獨立事件)。
    回傳的序列本身不是報酬,是拿來共享於兩個版本(baseline/treatment)的
    共同市場成分。
    """
    state = np.zeros(n)
    s = 0.0
    for i in range(n):
        s = phi * s + rng.normal(0, 1)
        state[i] = s
    win_prob = 1 / (1 + np.exp(-state))
    wins = rng.random(n) < win_prob
    win_mag = rng.lognormal(mean=np.log(0.08), sigma=0.9, size=n)
    loss_mag = -rng.uniform(0.03, 0.10, size=n)
    return np.where(wins, win_mag, loss_mag)


def test_identical_trades_gives_zero_difference_and_never_passes():
    """兩組出場機制對這批交易完全沒有差異時,不能宣稱通過。"""
    rng = np.random.default_rng(0)
    r, _ = _skewed_trade_returns(80, rng)
    res = ptc.paired_trade_test(r, r.copy(), n_boot=300)
    assert abs(res["delta"]) < 1e-12
    assert res["p_boot"] > 0.5
    assert res["passes"] is False
    assert np.isnan(res["wilcoxon_p"])  # 差異序列全為 0,退化
    assert "note" in res


def test_detects_genuinely_better_exit_mechanism():
    """
    策略五的核心機制主張:讓獲利奔跑(贏的交易報酬放大),輸的交易大致不變。
    效應夠大、樣本數與策略一實測量級相近(76 筆,見 docs/pass-b-results.md)時,
    應被偵測為顯著改善,且通過 N=10 門檻。
    """
    rng = np.random.default_rng(1)
    n = 80
    r_baseline, wins = _skewed_trade_returns(n, rng)
    # Kalman 出場「讓利潤奔跑」:贏的交易報酬放大 1.8 倍,輸的交易不變
    # (單純測試機制是否偵測到「放大右尾」這個效應本身,不混入 MAE 惡化的
    # 反向效果 —— 那個風險已在 docs/strategy-5-hypothesis.md 6.1 節另外處理)
    r_treatment = np.where(wins, r_baseline * 1.8, r_baseline)

    res = ptc.paired_trade_test(r_baseline, r_treatment, n_boot=1000, n_trials=10)
    assert res["delta"] > 0
    assert res["p_boot"] < 0.05
    assert res["passes"] is True
    assert res["wilcoxon_p"] < 0.05  # 效應均勻分布在多數交易上,兩個檢定應同意


def test_no_false_positive_under_clustered_equal_sharpe_trades():
    """
    ⛔ 本模組存在的核心理由之一:兩組出場機制的「真實」SR_trade 相同,但底層
    交易結果因訊號叢聚而沿交易序號自相關(右偏、非 iid)時,不該被誤判為顯著
    不同 —— 這正是需要區塊 bootstrap(而非樸素 iid 假設)的原因。
    """
    rng = np.random.default_rng(11)
    n = 80
    common = _clustered_common_component(n, rng)
    r_baseline = common + rng.normal(0, 0.01, n)
    r_treatment = common + rng.normal(0, 0.01, n)

    res = ptc.paired_trade_test(r_baseline, r_treatment, n_boot=500)
    assert res["p_boot"] > 0.05, f"對真實相同的 SR_trade 誤報顯著(p={res['p_boot']:.4f})"
    assert res["passes"] is False


def test_small_sample_flagged_below_n_eff_floor():
    """
    交易筆數低於 statistical-methodology.md §4.4 的硬性下限(30)時,即使
    delta 看起來很大,也不得判定通過 —— 資訊量不足本身就是理由,不看點估計。
    """
    rng = np.random.default_rng(2)
    n = 15
    r_baseline, wins = _skewed_trade_returns(n, rng)
    r_treatment = np.where(wins, r_baseline * 2.0, r_baseline)

    res = ptc.paired_trade_test(r_baseline, r_treatment, n_boot=300)
    assert res["n_trades"] == 15
    assert res["n_eff_below_floor"] is True
    assert res["passes"] is False


def test_wilcoxon_and_bootstrap_can_diverge_on_tail_driven_effect():
    """
    模擬 post-mortem-strategy-1.md 的診斷本身(10 筆貢獻 157.9% 總報酬):
    絕大多數配對交易毫無差異,只有極少數幾筆被 Kalman 出場放大成巨大右尾。
    Wilcoxon(中位數位移)不該偵測到這種效應;這個落差正是本模組設計上
    要求同時報告兩者的理由(見模組 docstring)。
    """
    rng = np.random.default_rng(3)
    n = 76
    r_baseline, _ = _skewed_trade_returns(n, rng)
    r_treatment = r_baseline.copy()
    # 只放大其中 3 筆原本就獲利的交易,其餘 73 筆完全不變
    win_idx = np.where(r_baseline > 0)[0][:3]
    r_treatment[win_idx] *= 4.0

    res = ptc.paired_trade_test(r_baseline, r_treatment, n_boot=1000)
    assert res["wilcoxon_p"] > 0.05, "只有 3 筆交易不同,中位數位移應不顯著"
    assert res["delta"] > 0, "SR_trade 對右尾放大應仍有反應,方向應為正"


def test_mismatched_lengths_rejected():
    with pytest.raises(ValueError):
        ptc.paired_trade_test(np.zeros(50), np.zeros(49))


def test_too_few_trades_rejected():
    with pytest.raises(ValueError):
        ptc.paired_trade_test(np.array([0.01]), np.array([0.02]))


def test_block_length_capped_for_small_trade_counts():
    """
    區塊長度不應超過 n // 5(見 `_capped_block_length` 的設計說明),避免
    小樣本下重抽樣的有效區塊數過少。用刻意製造高自相關的差異序列驗證上限生效。
    """
    rng = np.random.default_rng(5)
    n = 40
    common = _clustered_common_component(n, rng, phi=0.95)  # 強自相關,估計出的區塊長度容易偏大
    r_baseline = common
    r_treatment = common + rng.normal(0, 0.01, n)

    res = ptc.paired_trade_test(r_baseline, r_treatment, n_boot=200)
    assert res["block_length"] <= max(1, n // 5)


def test_n10_default_matches_sharpe_difference_module():
    """
    N=10 的門檻必須直接對應 analysis/sharpe_difference.py 既有的
    minimum_detectable_difference() 邏輯,不是另立一套公式
    (docs/strategy-5-hypothesis.md 5.3 節的要求)。
    """
    from analysis.sharpe_difference import minimum_detectable_difference

    rng = np.random.default_rng(6)
    n = 80
    r_baseline, wins = _skewed_trade_returns(n, rng)
    r_treatment = np.where(wins, r_baseline * 1.3, r_baseline)

    res = ptc.paired_trade_test(r_baseline, r_treatment, n_boot=300, n_trials=10)
    expected_threshold = minimum_detectable_difference(res["se_hac"], n_trials=10)
    assert abs(res["min_detectable_delta"] - expected_threshold) < 1e-9

    # N 越大,門檻應越高 -- 不能因為換了比較對象就悄悄退回 N=1 的樂觀版本
    res_n1 = ptc.paired_trade_test(r_baseline, r_treatment, n_boot=300, n_trials=1)
    assert res["min_detectable_delta"] > res_n1["min_detectable_delta"]


def test_high_rho_between_paired_legs():
    """
    策略五與策略一在進場點附近幾乎完全相同(同一段價格路徑分岔前的共同部分),
    預期配對交易的相關係數 rho_trade 遠高於策略一 vs 50/50 基準的 ρ≈0.41
    (docs/strategy-5-hypothesis.md 3.3/3.4 節)——這是本比較在檢定力上相對
    CP-004 第二層更有利的結構性理由,應能從合成資料驗證出來。
    """
    rng = np.random.default_rng(7)
    n = 80
    r_baseline, wins = _skewed_trade_returns(n, rng)
    # 只在出場點之後小幅偏離,大部分報酬(進場到分岔前)仍與 baseline 共享
    r_treatment = r_baseline + rng.normal(0, 0.01, n)

    res = ptc.paired_trade_test(r_baseline, r_treatment, n_boot=200)
    assert res["rho_trade"] > 0.7

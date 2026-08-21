"""
驗證 user_data/strategies/fatfinger_guard.py(security-policy.md 第 5 節,
胖手指防護層的獨立核心邏輯)。全部使用合成資料,不連線 Freqtrade/交易所——
這裡只驗證純函式本身的邊界行為是否符合 security-policy.md 5.2 節的規格。

匯入方式比照 analysis/tests/test_vol_targeting_strategy_integration.py 已有的
慣例(sys.path 插入 user_data/strategies/,直接 import 模組)。
"""

import math
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STRATEGY_DIR = _REPO_ROOT / "user_data" / "strategies"
if str(_STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(_STRATEGY_DIR))

import fatfinger_guard as ffg  # noqa: E402


# ---------------------------------------------------------------------------
# validate_sizing_inputs —— security-policy.md 5.2 節第 3 點
# ---------------------------------------------------------------------------


def test_valid_inputs_pass():
    r = ffg.validate_sizing_inputs(equity=10_000.0, risk_indicator=250.0, last_equity=None)
    assert r.is_valid
    assert r.reason is None


@pytest.mark.parametrize("bad_equity", [0.0, -100.0, math.nan, math.inf, -math.inf, None])
def test_non_finite_or_non_positive_equity_fails(bad_equity):
    r = ffg.validate_sizing_inputs(equity=bad_equity, risk_indicator=100.0, last_equity=None)
    assert not r.is_valid
    assert "equity" in r.reason


@pytest.mark.parametrize("bad_indicator", [0.0, -5.0, math.nan, math.inf, None])
def test_non_finite_or_non_positive_risk_indicator_fails(bad_indicator):
    """對應策略一/五的 ATR、策略四的 realized_vol(sigma_hat) 兩種角色。"""
    r = ffg.validate_sizing_inputs(equity=10_000.0, risk_indicator=bad_indicator, last_equity=None)
    assert not r.is_valid
    assert "risk_indicator" in r.reason


def test_first_reading_skips_jump_check():
    """last_equity=None(本輪第一次讀值)不應該因為沒有基準而被誤判為異常。"""
    r = ffg.validate_sizing_inputs(equity=1_000_000.0, risk_indicator=1.0, last_equity=None)
    assert r.is_valid


def test_equity_jump_within_bound_passes():
    # 從 10,000 跳到 14,900(+49%),低於 50% 上限
    r = ffg.validate_sizing_inputs(equity=14_900.0, risk_indicator=1.0, last_equity=10_000.0)
    assert r.is_valid


def test_equity_jump_exceeding_bound_fails():
    # 從 10,000 跳到 15,100(+51%),超過 50% 上限
    r = ffg.validate_sizing_inputs(equity=15_100.0, risk_indicator=1.0, last_equity=10_000.0)
    assert not r.is_valid
    assert "跳動" in r.reason


def test_equity_jump_downward_also_checked():
    """跳動檢查是絕對值,暴跌與暴漲同樣視為異常(security-policy.md 5.2 節未區分方向)。"""
    r = ffg.validate_sizing_inputs(equity=4_900.0, risk_indicator=1.0, last_equity=10_000.0)
    assert not r.is_valid


def test_equity_jump_boundary_exactly_at_ratio_passes():
    """剛好等於上限(非嚴格大於)不應觸發——邊界語意需明確。"""
    r = ffg.validate_sizing_inputs(equity=15_000.0, risk_indicator=1.0, last_equity=10_000.0)
    assert r.is_valid


# ---------------------------------------------------------------------------
# equity_usable_as_baseline —— 安全審查 MED-4:跳動比對失敗不得永久鎖死
#
# 「這次能不能下單(is_valid)」與「這次的 equity 能不能當下次比對的基準
# (equity_usable_as_baseline)」是兩條職責不同的規則。修正前呼叫端只在 is_valid
# 為 True 時更新基準,於是一次跳動誤判就會讓基準永遠停在舊值,之後每次比對只會
# 差得更遠 —— 單次異常升級成永久停止進場,直到 process 重啟。
# ---------------------------------------------------------------------------


def test_baseline_flag_true_when_everything_passes():
    r = ffg.validate_sizing_inputs(equity=10_000.0, risk_indicator=1.0, last_equity=10_000.0)
    assert r.is_valid
    assert r.equity_usable_as_baseline


def test_baseline_flag_still_true_when_only_the_jump_check_fails():
    """核心修正:跳動比對失敗 → 這次不下單,但這次的 equity 仍是合理讀值,要當新基準。"""
    r = ffg.validate_sizing_inputs(equity=15_100.0, risk_indicator=1.0, last_equity=10_000.0)
    assert not r.is_valid
    assert r.equity_usable_as_baseline


def test_baseline_flag_true_when_only_risk_indicator_is_bad():
    """壞的是 ATR/sigma,不是 equity —— equity 本身仍然可以當基準。"""
    r = ffg.validate_sizing_inputs(equity=10_000.0, risk_indicator=math.nan, last_equity=9_900.0)
    assert not r.is_valid
    assert r.equity_usable_as_baseline


@pytest.mark.parametrize("bad_equity", [0.0, -100.0, math.nan, math.inf, -math.inf, None])
def test_baseline_flag_false_when_equity_itself_is_unusable(bad_equity):
    """equity 本身就是壞值時**不得**存成下次的基準,否則之後每次比對都會失真。"""
    r = ffg.validate_sizing_inputs(equity=bad_equity, risk_indicator=1.0, last_equity=10_000.0)
    assert not r.is_valid
    assert not r.equity_usable_as_baseline


@pytest.mark.parametrize("bad_equity", [0.0, -100.0, math.nan, math.inf, None])
def test_equity_is_usable_as_baseline_predicate(bad_equity):
    assert ffg.equity_is_usable_as_baseline(10_000.0)
    assert not ffg.equity_is_usable_as_baseline(bad_equity)


def test_notional_check_never_claims_baseline_usability():
    """equity_usable_as_baseline 只有 validate_sizing_inputs 會設定,另一個檢查不碰它。"""
    r = ffg.validate_notional_within_caps(notional=1_000.0, equity=10_000.0)
    assert r.is_valid
    assert not r.equity_usable_as_baseline


def _simulate_caller(readings: list[float]) -> list[bool]:
    """複製三個策略呼叫端的更新邏輯,回傳每次讀值是否放行下單。

    這是 MED-4 的迴歸測試核心:三個策略檔案裡的那幾行必須與這裡一致
    ——「先依 equity_usable_as_baseline 更新基準,再依 is_valid 決定要不要下單」。
    """
    baseline: float | None = None
    allowed: list[bool] = []
    for equity in readings:
        v = ffg.validate_sizing_inputs(equity=equity, risk_indicator=1.0, last_equity=baseline)
        if v.equity_usable_as_baseline:
            baseline = equity
        allowed.append(v.is_valid)
    return allowed


def test_single_anomalous_jump_blocks_only_one_signal_then_recovers():
    """1 萬 → 3 萬(+200%,擋)→ 3.01 萬(相對新基準只差 0.3%,應恢復)。"""
    assert _simulate_caller([10_000.0, 30_000.0, 30_100.0, 30_200.0]) == [
        True,
        False,
        True,
        True,
    ]


def test_bad_equity_reading_does_not_poison_the_baseline():
    """中間插進一個 NaN 讀值:它自己被擋下,但不得覆蓋掉原本正常的基準。"""
    assert _simulate_caller([10_000.0, math.nan, 10_100.0]) == [True, False, True]


# ---------------------------------------------------------------------------
# equity_jump_baseline —— 「跳動」檢查只在 live/dry-run 生效
#
# 為什麼要有這組測試:回測/hyperopt 同樣會呼叫 custom_stake_amount,而回測裡兩次
# 進場相隔數週數月、權益變動超過 50% 是常態。誤觸發會讓回測從此永久不再進場,
# 進而改變 docs/pass-b-results.md、docs/strategy-5-results.md 等已定案的數字。
# ---------------------------------------------------------------------------


def test_equity_jump_baseline_passes_through_for_live_and_dry_run():
    """真正的交易執行:基準值原樣傳回,跳動檢查照常生效。"""
    from freqtrade.enums import RunMode

    for mode in (RunMode.LIVE, RunMode.DRY_RUN):
        assert ffg.equity_jump_baseline(mode, 10_000.0) == 10_000.0


def test_equity_jump_baseline_suppressed_for_optimize_modes():
    """回測/hyperopt:一律回傳 None,跳動檢查不生效。"""
    from freqtrade.enums import RunMode

    for mode in (RunMode.BACKTEST, RunMode.HYPEROPT):
        assert ffg.equity_jump_baseline(mode, 10_000.0) is None


@pytest.mark.parametrize(
    "unknown_runmode",
    [None, "other", "plot", "webserver", "util_exchange", 123, object()],
)
def test_equity_jump_baseline_fails_safe_for_unknown_runmode(unknown_runmode):
    """判定不出來時一律關掉這條檢查——誤關的代價(少一層輔助防禦,下單金額仍受
    clamp_stake 硬上限保護)遠低於誤開(污染已定案的回測結果)。"""
    assert ffg.equity_jump_baseline(unknown_runmode, 10_000.0) is None


def test_equity_jump_baseline_accepts_plain_string_runmode_values():
    """不依賴 freqtrade 的 RunMode 型別本身,字串值也認得(模組刻意不 import freqtrade)。"""
    assert ffg.equity_jump_baseline("live", 10_000.0) == 10_000.0
    assert ffg.equity_jump_baseline("dry_run", 10_000.0) == 10_000.0
    assert ffg.equity_jump_baseline("backtest", 10_000.0) is None


def test_equity_jump_baseline_none_last_equity_stays_none():
    assert ffg.equity_jump_baseline("live", None) is None


def test_backtest_runmode_lets_large_equity_growth_through_validation():
    """端到端:回測裡權益從 1,000 漲到 10,000(+900%),經過 equity_jump_baseline
    之後 validate_sizing_inputs 仍應通過;同一組數字在 live 下則應被擋。"""
    from freqtrade.enums import RunMode

    backtest_ok = ffg.validate_sizing_inputs(
        equity=10_000.0,
        risk_indicator=250.0,
        last_equity=ffg.equity_jump_baseline(RunMode.BACKTEST, 1_000.0),
    )
    assert backtest_ok.is_valid

    live_blocked = ffg.validate_sizing_inputs(
        equity=10_000.0,
        risk_indicator=250.0,
        last_equity=ffg.equity_jump_baseline(RunMode.LIVE, 1_000.0),
    )
    assert not live_blocked.is_valid
    assert "跳動" in live_blocked.reason


def test_finite_positive_checks_still_apply_in_backtest():
    """跳過的只有「跳動」這一條——NaN/inf/非正數的檢查在回測下必須照常生效。"""
    from freqtrade.enums import RunMode

    baseline = ffg.equity_jump_baseline(RunMode.BACKTEST, 1_000.0)
    assert not ffg.validate_sizing_inputs(
        equity=float("nan"), risk_indicator=250.0, last_equity=baseline
    ).is_valid
    assert not ffg.validate_sizing_inputs(
        equity=10_000.0, risk_indicator=float("nan"), last_equity=baseline
    ).is_valid
    assert not ffg.validate_sizing_inputs(
        equity=-1.0, risk_indicator=250.0, last_equity=baseline
    ).is_valid
    assert not ffg.validate_sizing_inputs(
        equity=10_000.0, risk_indicator=0.0, last_equity=baseline
    ).is_valid


# ---------------------------------------------------------------------------
# clamp_stake —— security-policy.md 5.2 節第 1/2 點(裁剪,而非直接拒絕整筆交易)
# ---------------------------------------------------------------------------


def test_stake_within_caps_is_not_clamped():
    equity = 10_000.0
    proposed = 0.167 * equity  # risk-policy.md 4.3 節示範算例,約 16.7% 權益
    r = ffg.clamp_stake(proposed, equity, combined_used_notional=0.0)
    assert not r.was_clamped
    assert r.stake == pytest.approx(proposed)


def test_stake_exceeding_single_cap_is_clamped_not_rejected():
    """risk-policy.md 4.3 節情境 B:極端低波動導致 notional 換算到 100% 權益。"""
    equity = 10_000.0
    proposed = equity  # 100% 權益,遠超單筆 50% 上限
    r = ffg.clamp_stake(proposed, equity, combined_used_notional=0.0)
    assert r.was_clamped
    assert r.stake == pytest.approx(0.50 * equity)
    assert r.reason is not None


def test_stake_exceeding_combined_cap_is_clamped_to_remaining_budget():
    """另一筆倉位已用掉 70% 權益的名目部位,合併上限 80% 只剩 10% 額度可用。"""
    equity = 10_000.0
    combined_used = 0.70 * equity
    proposed = 0.30 * equity  # 單筆本身在 50% 上限內,但合併會超過 80%
    r = ffg.clamp_stake(proposed, equity, combined_used_notional=combined_used)
    assert r.was_clamped
    assert r.stake == pytest.approx(0.10 * equity)


def test_combined_budget_already_exhausted_clamps_to_zero():
    equity = 10_000.0
    combined_used = 0.85 * equity  # 已超過合併上限(理論上不該發生,但要能安全處理)
    proposed = 0.10 * equity
    r = ffg.clamp_stake(proposed, equity, combined_used_notional=combined_used)
    assert r.was_clamped
    assert r.stake == 0.0


def test_non_positive_proposed_stake_or_equity_returns_zero_without_clamp_flag():
    """0 或負值不是「超出上限被裁剪」的情境,是上游已經判斷不下單。"""
    r = ffg.clamp_stake(0.0, 10_000.0)
    assert r.stake == 0.0
    assert not r.was_clamped

    r2 = ffg.clamp_stake(100.0, 0.0)
    assert r2.stake == 0.0
    assert not r2.was_clamped


def test_clamp_caps_are_independent_constants_matching_risk_policy():
    """direct pin:risk-policy.md 4.3 節數字,防止未來被悄悄改動而不自知。"""
    assert ffg.NOTIONAL_SINGLE_CAP == 0.50
    assert ffg.NOTIONAL_COMBINED_CAP == 0.80
    assert ffg.EQUITY_JUMP_MAX_RATIO == 0.50


# ---------------------------------------------------------------------------
# validate_notional_within_caps —— confirm_trade_entry 送出前的最後背書
# ---------------------------------------------------------------------------


def test_notional_within_caps_passes():
    r = ffg.validate_notional_within_caps(notional=1_000.0, equity=10_000.0)
    assert r.is_valid


def test_notional_exceeding_single_cap_fails():
    r = ffg.validate_notional_within_caps(notional=6_000.0, equity=10_000.0)
    assert not r.is_valid


def test_notional_exceeding_combined_budget_fails():
    r = ffg.validate_notional_within_caps(
        notional=2_000.0, equity=10_000.0, combined_used_notional=7_000.0
    )
    assert not r.is_valid


def test_notional_at_exact_boundary_tolerates_floating_point_noise():
    """交易所數量精度捨入可能造成幾個 bp 的浮點誤差,不應被誤判為超額。"""
    equity = 10_000.0
    limit = ffg.NOTIONAL_SINGLE_CAP * equity
    r = ffg.validate_notional_within_caps(notional=limit + 1e-9, equity=equity)
    assert r.is_valid


@pytest.mark.parametrize("bad_notional", [math.nan, math.inf, -1.0, None])
def test_notional_non_finite_or_negative_fails(bad_notional):
    r = ffg.validate_notional_within_caps(notional=bad_notional, equity=10_000.0)
    assert not r.is_valid


@pytest.mark.parametrize("bad_equity", [0.0, -1.0, math.nan, math.inf, None])
def test_notional_check_non_finite_or_non_positive_equity_fails(bad_equity):
    r = ffg.validate_notional_within_caps(notional=100.0, equity=bad_equity)
    assert not r.is_valid


# ---------------------------------------------------------------------------
# 三個策略呼叫端的接線(原始碼層級的 pin)
#
# 上面的純函式測試證明模組本身的語意正確,但 MED-4 / LOW-10 兩個缺陷都出在
# **呼叫端怎麼用**這個模組 —— 光測模組不會抓到策略檔案改回舊寫法。這裡用原始碼
# 文字比對把三個檔案的接線順序釘住;會失敗代表有人改動了接線,應回頭讀
# fatfinger_guard.validate_sizing_inputs 的職責分離說明再決定是否為預期變更。
# ---------------------------------------------------------------------------

_STRATEGY_FILES = [
    "RegimeFilteredMomentumBreakout.py",
    "TrendFilterExit.py",
    "VolatilityTargeting.py",
]


def _strategy_source(name: str) -> str:
    return (_STRATEGY_DIR / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", _STRATEGY_FILES)
def test_strategies_update_baseline_before_the_validity_gate(filename):
    """MED-4:基準更新必須在 `if not validation.is_valid: ... return 0.0` 之前,
    而且條件是 equity_usable_as_baseline,不是 is_valid。"""
    src = _strategy_source(filename)
    update_at = src.find("if validation.equity_usable_as_baseline:")
    gate_at = src.find("if not validation.is_valid:")
    assert update_at != -1, f"{filename} 沒有依 equity_usable_as_baseline 更新基準"
    assert gate_at != -1
    assert update_at < gate_at, f"{filename} 的基準更新被擋在 fail-closed return 之後"
    assert "self._fatfinger_last_equity" in src[update_at:gate_at]


@pytest.mark.parametrize("filename", ["RegimeFilteredMomentumBreakout.py", "TrendFilterExit.py"])
def test_strategies_clamp_before_checking_min_stake(filename):
    """LOW-10:裁剪必須在 min_stake 檢查之前,否則被裁剪後低於 min_stake 的金額
    會被 freqtrade 的 validate_stake_amount() 拉回 min_stake(+30% 容許),
    送出金額又超過硬上限,再被 confirm_trade_entry 的背書檢查靜默擋掉。"""
    src = _strategy_source(filename)
    clamp_at = src.find("clamped = ffg.clamp_stake(")
    min_stake_at = src.find("if min_stake and final_stake < min_stake:")
    assert clamp_at != -1 and min_stake_at != -1
    assert clamp_at < min_stake_at, f"{filename} 仍然是先比 min_stake 才裁剪"

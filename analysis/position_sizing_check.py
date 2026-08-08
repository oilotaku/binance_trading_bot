"""
docs/statistical-methodology.md 第 5 節:Fractional Kelly 倉位公式。
docs/risk-policy.md 第 1、4 節:risk_fraction 硬上限、combined/notional 曝險上限。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

# risk-policy.md 第 0/1 節既有硬上限 —— 絕不可被 hyperopt 調整,此處僅供 analysis/ 驗證用
RISK_FRACTION_SINGLE_CAP = 0.015
RISK_FRACTION_COMBINED_CAP = 0.025
KELLY_FRACTION_DEFAULT = 0.25
KELLY_FRACTION_ABSOLUTE_CEILING = 0.5  # statistical-methodology.md 5.3 節:c<=0.5 絕對上限


@dataclass
class KellySizingResult:
    sr_1: float
    sigma_1: float
    f_star: float                  # full Kelly:f* = SR_1 / sigma_1
    kelly_fraction_c: float
    growth_fraction_of_full: float  # (2c - c^2),statistical-methodology.md 5.2 節推導
    k_atr_multiplier: float
    atr_pct: float
    risk_fraction_kelly_derived: float   # c * f* * k * ATR%
    risk_fraction_hard_cap: float
    risk_fraction_final: float           # min(kelly_derived, hard_cap) —— 唯一生效值
    hard_cap_is_binding: bool


def kelly_full(sr_1: float, sigma_1: float) -> float:
    """statistical-methodology.md 5.1 節:f* = mu/sigma^2 = SR/sigma(因 mu = SR*sigma)。"""
    if sigma_1 <= 0:
        return 0.0
    return sr_1 / sigma_1


def fractional_kelly_growth_ratio(c: float) -> float:
    """statistical-methodology.md 5.2 節推導:g(c*f*) = g(f*) * (2c - c^2)。"""
    return 2 * c - c ** 2


def compute_risk_fraction(
    sr_1: float,
    sigma_1: float,
    k_atr_multiplier: float,
    atr_pct: float,
    kelly_fraction_c: float = KELLY_FRACTION_DEFAULT,
    hard_cap: float = RISK_FRACTION_SINGLE_CAP,
) -> KellySizingResult:
    """
    risk-policy.md 1.4 節公式(唯一生效版本):
        risk_fraction = min(0.25 * f* * k * ATR%, 硬上限)
        f* = SR_1 / sigma_1  (須用 OOS + bootstrap 悲觀下界,呼叫端負責提供正確輸入)

    :param sr_1: OOS Sharpe(bootstrap 信賴區間下界,悲觀估計 —— 不得傳入 in-sample 優化值)
    :param sigma_1: OOS 波動度(同上,悲觀估計)
    :param k_atr_multiplier: 該次評估用的 ATR 停損倍數 k
    :param atr_pct: ATR / entry_price
    """
    if kelly_fraction_c > KELLY_FRACTION_ABSOLUTE_CEILING:
        raise ValueError(
            f"kelly_fraction_c={kelly_fraction_c} 超過 statistical-methodology.md 5.3 節"
            f" c<=0.5 絕對上限,Full Kelly(c=1)一律視為不通過,不需個案評估。"
        )

    f_star = kelly_full(sr_1, sigma_1)
    risk_fraction_kelly = kelly_fraction_c * f_star * k_atr_multiplier * atr_pct
    risk_fraction_kelly = max(0.0, risk_fraction_kelly)  # f* 為負(負 edge)時不應產生負部位
    risk_fraction_final = min(risk_fraction_kelly, hard_cap)

    return KellySizingResult(
        sr_1=sr_1,
        sigma_1=sigma_1,
        f_star=f_star,
        kelly_fraction_c=kelly_fraction_c,
        growth_fraction_of_full=fractional_kelly_growth_ratio(kelly_fraction_c),
        k_atr_multiplier=k_atr_multiplier,
        atr_pct=atr_pct,
        risk_fraction_kelly_derived=risk_fraction_kelly,
        risk_fraction_hard_cap=hard_cap,
        risk_fraction_final=risk_fraction_final,
        hard_cap_is_binding=risk_fraction_final == hard_cap and risk_fraction_kelly > hard_cap,
    )


def to_report_dict(result: KellySizingResult) -> dict:
    """statistical-methodology.md 5.3 節要求:四個關鍵數字並列揭露,不省略計算過程。"""
    d = asdict(result)
    d["_note"] = (
        "risk_fraction_final 是唯一生效值(= min(risk_fraction_kelly_derived, "
        "risk_fraction_hard_cap))。hard_cap_is_binding=True 代表硬上限才是實際約束,"
        "這是 risk-policy.md 1.4 節預期的常態情境。"
    )
    return d

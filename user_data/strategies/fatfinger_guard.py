# pragma pylint: disable=missing-docstring
"""
胖手指防護層(security-policy.md 第 5 節)的獨立、可單元測試核心邏輯。

設計原則(security-policy.md 5.2 節):
  1. 這裡的硬上限常數必須「與計算 proposed_stake 的路徑物理隔離」——直接對應
     risk-policy.md 已拍板的數字,不透過任何 Kelly/ATR 計算函式重新推導。因此
     本模組**不** import RegimeFilteredMomentumBreakout / TrendFilterExit /
     VolatilityTargeting 的任何一行計算邏輯,常數在此重複宣告一份,與各策略
     class attribute(RISK_FRACTION_*/NOTIONAL_*)各自獨立維護。
  2. 若未來 risk-policy.md 數字變更,這裡與各策略檔案內的常數都需要同步更新
     ——這是刻意的重複,不是失誤:兩處數字理論上必須一致,但保持各自獨立
     宣告,換取「即使其中一處計算路徑寫錯,這層防護仍然正確」的獨立性。
  3. 本模組不呼叫 Freqtrade/ccxt,不做任何網路 I/O,是純函式,方便用合成
     資料單元測試(見 analysis/tests/test_fatfinger_guard.py)。

接線位置(security-policy.md 5.2 節、execution-spec.md 7 節末已標記的開放問題,
本次實作時已用原始碼查證解決,見下):
  - `custom_stake_amount` 是唯一能夠真正「裁剪」下單金額的接線點——它的
    回傳值就是 Freqtrade 實際用來下單的金額。已查證
    `IStrategy.confirm_trade_entry` 簽章(freqtrade/strategy/interface.py,
    2026-08 pin 住的版本):只能回傳 bool,完全沒有機制可以修改
    amount/rate。因此本模組的 `clamp_stake()` 接在各策略
    `custom_stake_amount` 算出 final_stake 之後、實際 return 之前;
    `confirm_trade_entry` 只能用 `validate_notional_within_caps()` 做
    「送出前最後一次背書」式的否決性檢查(理論上不該觸發,只防禦
    `custom_stake_amount` 回傳值與 Freqtrade 實際準備送出的
    amount/rate 之間出現任何未預期落差)。
  - 這個決定回應 execution-spec.md 第 7 節末段的既有開放問題:
    「第 5.2 節的裁剪(clamp)機制與 confirm_trade_entry 回傳 False
    兩種介面之間的選擇,留待 Phase 10 依當時版本文件核實確切接線位置」
    ——本次已核實,結論如上。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# --- 獨立宣告的硬上限常數(risk-policy.md 第 0/1/4 節,security-policy.md 5.2 節)---
# 刻意不 import 各策略類別的 RISK_FRACTION_*/NOTIONAL_* class attribute,
# 理由見模組 docstring 第 1 點。
NOTIONAL_SINGLE_CAP = 0.50  # 單筆名目部位上限,佔權益比例(risk-policy.md 4.3 節)
NOTIONAL_COMBINED_CAP = 0.80  # 合併名目部位上限,佔權益比例(risk-policy.md 4.3 節)
EQUITY_JUMP_MAX_RATIO = 0.50  # 單迭代權益跳動上限(security-policy.md 5.2 節第 3 點)

# 「equity 單迭代跳動」檢查只在真正的 live/dry-run 執行時生效的 runmode 白名單。
# 值刻意用字串比對 freqtrade `RunMode` 列舉的 `.value`(RunMode 是 StrEnum,
# RunMode.LIVE.value == "live"、RunMode.DRY_RUN.value == "dry_run"),而不是
# `from freqtrade.enums import RunMode` —— 本模組的設計原則第 3 點要求不依賴
# freqtrade,才能用純合成資料單元測試(見模組 docstring)。
TRADING_RUNMODE_VALUES = frozenset({"live", "dry_run"})


def equity_jump_baseline(runmode: object, last_equity: float | None) -> float | None:
    """決定要不要把 `last_equity` 交給 `validate_sizing_inputs()` 做「單迭代跳動」比對。

    **為什麼需要這個函式(重要,牽涉專案的可重現性紀律):**

    原始碼查證:`freqtrade/optimize/backtesting.py` 在模擬進場時同樣會呼叫
    `custom_stake_amount` / `confirm_trade_entry`,也就是說胖手指防護層在回測與
    hyperopt 時**一樣會生效**。其中「equity 相對上次讀值跳動 >50% 視為異常」這條
    規則,語意上假設的是「兩次讀值之間只隔幾秒到幾分鐘的即時輪詢」;但在回測裡,
    兩次進場訊號之間可能相隔數週到數月,加密貨幣的權益在這段期間變動超過 50%
    完全是正常現象,不是異常。一旦誤觸發,`validate_sizing_inputs()` 回傳
    not valid → 策略 `return 0.0`,那根 K 棒的進場訊號就被無聲丟棄了。

    (歷史註記:安全審查 MED-4 修正前,呼叫端只在驗證通過時才更新
    `_fatfinger_last_equity`,基準值會永遠停在舊值、之後每次比對只會差得更遠,
    誤觸發一次就等於**永久停止進場**。該缺陷已修正——基準值現在只要 equity
    本身合理就會更新,見 `validate_sizing_inputs()` 的職責分離說明。但即使
    如此,在回測開啟這條規則仍會零星丟棄進場訊號、改變已定案的數字,所以
    下面「回測一律不做跳動比對」的結論不變。)

    這對本專案的殺傷力不只是「回測數字變差」:`docs/strategy-5-results.md`、
    `docs/strategy-4-cp007-results.md`、`docs/pass-b-results.md` 都是已經 commit、
    依 CLAUDE.md 預先登錄紀律定案的判定結果。一個為了 live 安全而加的防護層,
    若會悄悄改變重跑這些評估腳本得到的數字,等於讓已定案的結論無法重現。

    因此:**只有 live/dry-run 才回傳真正的 `last_equity`,其餘 runmode 一律回傳
    `None`**(`validate_sizing_inputs()` 收到 `None` 就跳過跳動比對)。注意這只關掉
    「跳動」這一條;`equity`/`risk_indicator` 的「必須是有限正數」檢查沒有跨迭代的
    時間語意,不受回測影響,在所有 runmode 下都繼續生效。

    :param runmode: 通常傳 `self.dp.runmode`(freqtrade 的 `RunMode` 列舉)。
        無法判定(`None`、取不到 `dp`、未知值)時一律當成「非 live/dry-run」而回傳
        `None`。這個方向是刻意的:誤關掉這條輔助檢查,最壞情況是少一層對錯誤權益
        快照的防禦(下單金額仍受 `clamp_stake()` 的硬上限保護);誤開啟則會污染
        已定案的回測結果,後者的代價高得多。
    """
    if runmode is None:
        return None
    value = getattr(runmode, "value", runmode)
    if str(value).strip().lower() in TRADING_RUNMODE_VALUES:
        return last_equity
    return None


@dataclass(frozen=True)
class InputValidationResult:
    """輸入檢查結果。

    :param is_valid: 這次的輸入整體是否通過檢查(不通過 → 呼叫端 fail closed,
        本次訊號不下單)。
    :param reason: 未通過的原因,通過時為 None。
    :param equity_usable_as_baseline: **只有 `validate_sizing_inputs()` 會設定這個
        欄位**(`validate_notional_within_caps()` 一律留在預設 False,對它沒有意義)。
        語意是「這次讀到的 equity 本身是否合理到足以當作**下次**跳動比對的基準」,
        刻意與 `is_valid` 分開 —— 見 `validate_sizing_inputs()` docstring 的
        「兩個檢查的職責分離」段落。
    """

    is_valid: bool
    reason: str | None = None
    equity_usable_as_baseline: bool = False


@dataclass(frozen=True)
class ClampResult:
    stake: float
    was_clamped: bool
    reason: str | None = None


def equity_is_usable_as_baseline(equity: float | None) -> bool:
    """這個 equity 讀值本身是否合理到足以當作**下次**跳動比對的基準?

    只看「equity 自己」——有限、正數即可,完全不管它相對上次讀值跳了多少
    (跳動比對是另一條獨立的規則,見 `validate_sizing_inputs()` 的職責分離說明)。
    `validate_sizing_inputs()` 內部用的就是這個函式,呼叫端因此不可能與它對
    「什麼叫合理的 equity」有不同看法。
    """
    return equity is not None and math.isfinite(equity) and equity > 0


def validate_sizing_inputs(
    equity: float | None,
    risk_indicator: float | None,
    last_equity: float | None,
    *,
    max_equity_jump_ratio: float = EQUITY_JUMP_MAX_RATIO,
) -> InputValidationResult:
    """
    security-policy.md 5.2 節第 3 點:輸入合理性檢查。

    **兩個檢查的職責分離(安全審查 MED-4,重要):**

    本函式其實混合了兩條性質不同的規則:
      (1) 「equity / risk_indicator 本身必須是有限正數」—— 只看這一次的讀值;
      (2) 「equity 相對上次讀值不得跳動超過 50%」—— 需要跨迭代的基準值。

    回傳值因此拆成兩個欄位:`is_valid`(這次能不能下單)與
    `equity_usable_as_baseline`(這次的 equity 能不能當下次的基準)。

    原本的設計是「只有 `is_valid` 為 True 時呼叫端才更新基準」,這會造成
    **永久鎖死**:一旦某次跳動比對失敗(例如交易所回傳一次異常餘額快照、
    或入金/出金造成的一次合法大跳動),基準值就永遠停在那個舊值,之後每次
    讀到的新權益都拿去跟這個越來越過期的基準比,跳幅只會越來越大,於是
    每一次進場都被擋下,直到 process 重啟為止 —— 單次異常升級成永久停機。

    正確語意:只要 (1) 通過(equity 自己是合理的有限正數),不論 (2) 有沒有
    通過,這次的 equity 都應該成為下次比對的新基準;唯有 equity 本身就不合理
    (NaN/inf/非正數)時才不更新 —— 不能把壞值存成下次的基準,否則之後每次
    比對都會失真。這樣一來,單次跳動異常只會擋下單次訊號(fail closed 的
    原意),不會累積成永久鎖死。

    :param equity: 目前權益快照(self.wallets.get_total_stake_amount() 或等價)。
    :param risk_indicator: 部位大小公式分母所依賴的風險指標——
        RegimeFilteredMomentumBreakout/TrendFilterExit 用 ATR,
        VolatilityTargeting 用 realized_vol(sigma_hat)。兩者在各自的部位
        大小公式裡扮演相同角色(分母,決定「同樣的風險預算換算出多大部位」),
        用同一組「必須是有限正數」規則檢查是合理的類比延伸,不是把兩個不同
        物理量直接數值比較。
    :param last_equity: 上一次讀到的權益快照。IStrategy 本身不提供這個狀態,
        由呼叫端(各策略)自行維護 instance attribute 並在每次呼叫後更新
        ——見各策略 __init__ 內 `self._fatfinger_last_equity` 的說明。
        傳入 None 代表本次是這輪執行以來第一次讀值,不做跳動比對(沒有
        基準可比較,強行比較只會產生誤判,不是「跳過檢查」的偷懶)。
    """
    # 檢查 (1):equity 自己合不合理。這同時決定了 equity_usable_as_baseline
    # ——「能不能當下次基準」只取決於這一條,與跳動比對的結果無關。
    equity_ok = equity_is_usable_as_baseline(equity)
    if not equity_ok:
        return InputValidationResult(
            False,
            f"equity 不合理(非有限正數):{equity!r}",
            equity_usable_as_baseline=False,
        )

    if (
        risk_indicator is None
        or not math.isfinite(risk_indicator)
        or risk_indicator <= 0
    ):
        return InputValidationResult(
            False,
            f"risk_indicator 不合理(非有限正數):{risk_indicator!r}",
            # equity 本身沒問題,壞的是 risk_indicator —— 這次的 equity 仍然
            # 是個合理的讀值,理當成為下次比對的基準。
            equity_usable_as_baseline=True,
        )

    # 檢查 (2):跨迭代跳動比對。不通過只擋下這一次的訊號,不影響基準更新。
    if last_equity is not None and math.isfinite(last_equity) and last_equity > 0:
        jump_ratio = abs(equity - last_equity) / last_equity
        if jump_ratio > max_equity_jump_ratio:
            return InputValidationResult(
                False,
                f"equity 單迭代跳動 {jump_ratio:.1%} 超過上限 "
                f"{max_equity_jump_ratio:.0%}(上次 {last_equity!r} -> 這次 "
                f"{equity!r}),疑似讀到過期或錯誤的權益快照",
                equity_usable_as_baseline=True,
            )

    return InputValidationResult(True, equity_usable_as_baseline=True)


def clamp_stake(
    proposed_stake: float,
    equity: float,
    combined_used_notional: float = 0.0,
    *,
    single_cap: float = NOTIONAL_SINGLE_CAP,
    combined_cap: float = NOTIONAL_COMBINED_CAP,
) -> ClampResult:
    """
    security-policy.md 5.2 節第 2 點:proposed_stake 超出獨立上限時**裁剪**
    到上限值,而非直接拒絕整筆交易。

    :param proposed_stake: custom_stake_amount 主要計算路徑(ATR/Kelly 公式
        或 volatility-targeting 公式)算出的下單金額,裁剪前。
    :param equity: 目前權益快照。
    :param combined_used_notional: 除了這筆之外,其他既有持倉已佔用的名目
        金額總和——這是原始資料加總(sum of trade.stake_amount),不是透過
        Kelly/ATR 公式反推的值,因此不違反「與計算路徑物理隔離」的原則。
    """
    if proposed_stake <= 0 or equity <= 0:
        return ClampResult(0.0, False)

    single_limit = single_cap * equity
    combined_limit = max(0.0, combined_cap * equity - combined_used_notional)
    hard_limit = min(single_limit, combined_limit)

    if proposed_stake > hard_limit:
        reason = (
            f"proposed_stake={proposed_stake:.2f} 超出獨立胖手指上限 "
            f"{hard_limit:.2f}(單筆上限={single_limit:.2f}, "
            f"合併可用上限={combined_limit:.2f}, equity={equity:.2f}, "
            f"其他持倉已用名目={combined_used_notional:.2f})——已裁剪到上限值,"
            "這通常代表上游計算可能有問題,即使本次沒有實際超額下單也值得檢查"
        )
        return ClampResult(max(0.0, hard_limit), True, reason)

    return ClampResult(proposed_stake, False)


def validate_notional_within_caps(
    notional: float | None,
    equity: float | None,
    combined_used_notional: float = 0.0,
    *,
    single_cap: float = NOTIONAL_SINGLE_CAP,
    combined_cap: float = NOTIONAL_COMBINED_CAP,
) -> InputValidationResult:
    """
    `confirm_trade_entry` 送出前的最後一次背書檢查(見模組 docstring「接線
    位置」說明)。

    `confirm_trade_entry` 依 Freqtrade 介面只能回傳 bool,無法裁剪 amount
    ——真正的裁剪已經在 `custom_stake_amount` 內透過 `clamp_stake()` 完成。
    這裡只是防禦性地重新核對「即將真正送出的 amount*rate」是否仍在獨立上限
    內,理論上應該永遠通過(因為已經被裁剪過);若沒通過,代表
    `custom_stake_amount` 的回傳值與這裡看到的 amount 之間出現了未預期的
    落差,應直接拒絕本次下單,而不是嘗試臆測發生了什麼事。
    """
    if notional is None or not math.isfinite(notional) or notional < 0:
        return InputValidationResult(False, f"notional 不合理:{notional!r}")
    if equity is None or not math.isfinite(equity) or equity <= 0:
        return InputValidationResult(False, f"equity 不合理:{equity!r}")

    single_limit = single_cap * equity
    combined_limit = max(0.0, combined_cap * equity - combined_used_notional)
    hard_limit = min(single_limit, combined_limit)

    # 容許極小的浮點/交易所數量精度捨入誤差(數量捨入可能造成幾個 bp 的落差),
    # 不因此誤判一筆已經正確裁剪過的訂單。
    tolerance = max(hard_limit * 1e-6, 1e-8)
    if notional > hard_limit + tolerance:
        return InputValidationResult(
            False,
            f"notional={notional:.2f} 超出獨立胖手指上限 {hard_limit:.2f}"
            f"(equity={equity:.2f}, 其他持倉已用名目={combined_used_notional:.2f})"
            "——理論上不應發生,custom_stake_amount 的裁剪可能未生效或被繞過",
        )
    return InputValidationResult(True)

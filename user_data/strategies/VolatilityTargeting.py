# pragma pylint: disable=missing-docstring, invalid-name
"""
策略四:波動度目標化(Volatility Targeting)。

對應規劃文件(不重新設計,只把已核准規格接進 Freqtrade):
  - docs/strategy-4-hypothesis.md   經濟假說、演算法、可證偽預測
  - docs/change-proposals/CP-004-revised-targets.md   目標(兩層制)、基準
  - docs/change-proposals/CP-005-risk-policy-for-always-in-market.md
        風控(曝險上限 0.8、回撤斜坡 30%→40%、再平衡帶 20%、排除清單)
  - docs/strategy-4-results.md     CP-006 修正後的正式判定(σ_target=0.123 等)
  - analysis/vol_target.py         同一套邏輯的離線純函式版本(用於統計判定)

⚠️ 本檔案的存在理由是**技術驗證**,不是重新跑判定:確認這套機制能在 Freqtrade
   框架內正確下單、調整部位、觸發風控,而不只是在 analysis/vol_target.py 的
   離線模擬裡成立。正式的第一層/第二層判定以 analysis/vol_target.py +
   docs/strategy-4-results.md 為準,本檔案不產生新的統計結論。

參數全部從 analysis/vol_target.py 匯入,不在此重複定義、不得在此調整 ——
唯一事實來源(single source of truth)是那個模組,其常數已被測試鎖住。

與策略一(RegimeFilteredMomentumBreakout)的關鍵差異(見 CP-005 第 1–2 節):
  - 沒有進場/出場訊號,沒有 ATR 停損、time-stop、CooldownPeriod、StoplossGuard——
    這些是為擇時型策略設計的事件驅動機制,對永遠在市、狀態驅動的策略四不適用,
    啟用了反而會把每日再平衡誤判為新倉而凍結曝險調整(CP-005 第 2 節)。
  - `protections = []`:CP-005 3.2 節已論證這些機制必須排除,不是遺漏。
  - 唯一的風控是 σ_target 本身 + 回撤斜坡(30%→40%),兩者都在
    adjust_trade_position / custom_stake_amount 內以純運算方式決定曝險,
    不透過 Freqtrade Protections(它做不到「調整目標曝險」,只能鎖倉)。

security-policy.md 第 5 節:胖手指防護層。custom_stake_amount 尾端(final_stake
算出後、return 之前)新增一層獨立裁剪,並新增 confirm_trade_entry(本檔案原本
沒有這個 callback)做送出前最後一次背書檢查——兩者都呼叫 fatfinger_guard.py
(獨立宣告的硬上限常數,與策略一/五共用同一份獨立模組)。策略四沒有 ATR,
用 realized_vol(sigma_hat)扮演部位大小公式分母/風險指標的同一角色(見
fatfinger_guard.validate_sizing_inputs 的參數說明)。這一層是新增的防禦,
**不改動**上面已交代的 σ_target/回撤斜坡計算邏輯本身。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis import vol_target as vt  # noqa: E402


def _load_fatfinger_guard():
    """
    載入 fatfinger_guard.py,不用 `sys.path.insert(0, 策略目錄)` 這種寫法(安全審查
    LOW-11):`user_data/strategies/` 目錄會被 hyperopt 寫入自動產生的 `*.json` 參數
    覆寫檔(見 .gitignore 對這個目錄的既有規則),插到 sys.path[0] 等於讓這個「本來
    就會出現非人工維護檔案」的目錄,有能力遮蔽整個 freqtrade process 的標準函式庫/
    第三方模組匯入(例如不小心出現一個 `logging.py`)。改用 `importlib` 直接依路徑
    載入,完全不動 `sys.path`。`_REPO_ROOT` 的插入不受影響(`analysis/` 不是 hyperopt
    寫入目標,風險性質不同,維持既有做法)。
    """
    spec = importlib.util.spec_from_file_location(
        "fatfinger_guard", Path(__file__).resolve().parent / "fatfinger_guard.py"
    )
    module = importlib.util.module_from_spec(spec)
    # 必須在 exec_module 之前註冊進 sys.modules:fatfinger_guard.py 用了 @dataclass,
    # 其內部型別檢查會用 cls.__module__ 回頭查 sys.modules 找模組物件(cpython
    # dataclasses._is_type()),沒有先註冊的話會在載入 dataclass 定義時直接噴
    # AttributeError('NoneType' object has no attribute '__dict__')——這是實測踩到
    # 的坑,不是理論上的邊界情況。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ffg = _load_fatfinger_guard()  # security-policy.md 第 5 節,獨立胖手指防護層

logger = logging.getLogger(__name__)

# 目標曝險低於此值視為「等同於零」,觸發完整出場而非用 adjust_trade_position
# 減碼到底——Freqtrade 的 adjust_trade_position 只能減碼到 min_stake,無法把
# 部位精確降到 0,精確歸零必須走 custom_exit 這條路徑。
_ZERO_EXPOSURE_EPSILON = 0.01


class VolatilityTargeting(IStrategy):
    """
    docs/strategy-4-hypothesis.md 策略四:方向不可預測、風險可預測,
    永遠在市,只依已實現波動與組合回撤調整曝險。
    """

    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "1d"

    # 沒有固定停利/停損出場路徑——曝險完全由 adjust_trade_position 管理,
    # 這兩個只是框架要求的必要 fallback,不是正常出場路徑的一部分。
    minimal_roi = {"0": 100}
    stoploss = -0.99
    use_custom_stoploss = False

    process_only_new_candles = True
    use_exit_signal = False  # 沒有訊號型出場——策略本身就是「永遠在市」
    exit_profit_only = False
    position_adjustment_enable = True  # 啟用 adjust_trade_position(CP-005 第 4 節查證可行)

    # W(20)+ATR 類緩衝,對齊 architecture-spec.md 3.3 節「須 >= 週期 + 緩衝」的既有原則
    startup_candle_count: int = vt.W + 10

    max_open_trades = 2  # BTC + ETH,對齊 vt.py 的 N_PAIRS

    # CP-005 3.2 節:事件驅動機制對狀態驅動的策略四不適用,明確排除,
    # 不是遺漏。唯一的風控是 σ_target 本身 + 回撤斜坡,見 adjust_trade_position。
    protections: list = []

    N_PAIRS = 2

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        # 每個交易對各自維護一份 (日期, 對數權益) 序列,滾動視窗回撤用 ——
        # 對齊 vt.simulate() 的 equity_path,但這裡追蹤的是**整個組合**的權益
        # (透過 self.wallets),不是單一部位。
        self._equity_log_history: list[tuple[object, float]] = []
        # security-policy.md 5.2 節第 3 點:胖手指防護層需要「上一次讀值」才能
        # 判斷 equity 是否有異常跳動,IStrategy 本身不提供這個狀態,這裡新增一個
        # instance attribute 自行維護。只被 custom_stake_amount/fatfinger_guard
        # 讀寫,不影響上面 _equity_log_history 或任何既有計算邏輯。
        self._fatfinger_last_equity: float | None = None

    # ---- 指標:與 vt.realized_volatility 同一套定義 ----

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        已實現波動,窗口與 vt.W 一致。不需要 .shift(1) ——
        第 T 根收盤後算出的 realized_vol 反映的是「到 T 為止已知的資訊」,
        而 Freqtrade 的訊號在 T 收盤後才會被執行於 T+1,天然無前視。
        """
        log_return = np.log(dataframe["close"]).diff()
        dataframe["realized_vol"] = (
            log_return.rolling(window=vt.W).std(ddof=0) * np.sqrt(vt.PERIODS_PER_YEAR)
        )
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        沒有方向濾網——只要波動估計已經穩定(暖身期過後)就允許進場,
        真正的曝險大小由 custom_stake_amount 決定,不是由這個訊號決定。
        """
        dataframe.loc[dataframe["realized_vol"].notna(), "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe  # 無訊號型出場,見 custom_exit

    # ---- 組合層滾動回撤(對齊 vt.simulate 的 DD_LOOKBACK 語意)----

    def _record_and_get_drawdown(self, current_time: datetime) -> float:
        """
        記錄當日組合權益(每天只記一次),回傳滾動 DD_LOOKBACK 天視窗內的回撤。

        ⚠️ 這是 CP-006 選項 A 的即時版本:全期回撤是吸收態(見
        docs/strategy-4-run-1-invalid.md 第 2 節),滾動視窗讓舊高點隨時間
        滾出視窗,曝險才能在回撤過後恢復。
        """
        day = current_time.date()
        total = self.wallets.get_total_stake_amount()
        if total <= 0:
            return 0.0
        log_eq = float(np.log(total))

        if not self._equity_log_history or self._equity_log_history[-1][0] != day:
            self._equity_log_history.append((day, log_eq))
        else:
            # 同一天內被多次呼叫(例如同時處理 BTC 與 ETH 的回呼),更新為最新值
            self._equity_log_history[-1] = (day, log_eq)

        cutoff = day - timedelta(days=vt.DD_LOOKBACK)
        window = [lv for d, lv in self._equity_log_history if d >= cutoff]
        peak = max(window)
        return float(1.0 - np.exp(log_eq - peak))

    def _target_weight_per_pair(self, sigma_hat: float, portfolio_dd: float) -> float:
        """對齊 vt.simulate() 單一時點的曝險公式,除以標的數平均分配。"""
        if not np.isfinite(sigma_hat) or sigma_hat <= 0:
            return 0.0
        ramp = vt.drawdown_ramp_factor(portfolio_dd)
        w = min(vt.MAX_EXPOSURE, vt.SIGMA_TARGET * ramp / sigma_hat)
        return w / self.N_PAIRS

    # ---- 進場部位大小 ----

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return 0.0

        sigma_hat = dataframe["realized_vol"].iloc[-1]
        dd = self._record_and_get_drawdown(current_time)
        w = self._target_weight_per_pair(sigma_hat, dd)

        # --- security-policy.md 5.2 節第 3 點:獨立輸入合理性檢查(胖手指防護層) ---
        # 策略四沒有 ATR,用 realized_vol(sigma_hat)扮演公式分母/風險指標的
        # 同一角色。_target_weight_per_pair 內部已經有自己的 isfinite/正數檢查
        # (見上方定義),這裡用獨立模組 fatfinger_guard 不透過那條計算路徑
        # 重新驗證一次,額外涵蓋「equity 相對上次讀值異常跳動」這個上面完全
        # 沒有檢查的情況。
        #
        # `equity_jump_baseline()`:回測/hyperopt 時把跳動比對關掉(傳入 None),
        # 理由同策略一,完整說明見 fatfinger_guard.equity_jump_baseline 的 docstring
        # ——這條規則若在回測誤觸發,會讓 docs/strategy-4-cp007-results.md 的已定案
        # 數字無法重現。「有限正數」檢查在所有 runmode 下都繼續生效。
        equity_for_validation = self.wallets.get_total_stake_amount()
        validation = ffg.validate_sizing_inputs(
            equity=equity_for_validation,
            risk_indicator=sigma_hat,
            last_equity=ffg.equity_jump_baseline(
                self.dp.runmode if self.dp else None, self._fatfinger_last_equity
            ),
        )
        # 安全審查 MED-4:基準值的更新**不以本次驗證通過為條件**(三個策略一致)。
        # 只要這次的 equity 本身是合理的有限正數,就讓它成為下次跳動比對的基準;
        # 否則一次跳動誤判會讓基準永遠停在舊值,單次異常升級成永久鎖死進場。
        # 完整理由見 fatfinger_guard.validate_sizing_inputs 的職責分離段落。
        if validation.equity_usable_as_baseline:
            self._fatfinger_last_equity = equity_for_validation
        if not validation.is_valid:
            logger.warning(
                "[%s] 胖手指防護:custom_stake_amount 輸入合理性檢查未通過,"
                "本次訊號跳過不下單(fail closed)。原因:%s",
                pair,
                validation.reason,
            )
            return 0.0

        total_equity = self.wallets.get_total_stake_amount()
        stake = w * total_equity

        # --- security-policy.md 5.2 節第 1/2 點:獨立來源硬上限的最終裁剪 ---
        # fatfinger_guard.NOTIONAL_SINGLE_CAP/COMBINED_CAP 與 vt.MAX_EXPOSURE
        # (CP-005 3.5 節,已對應 risk-policy.md 4.3 節合併名目上限)各自獨立
        # 宣告——即使 _target_weight_per_pair 內的 vt.MAX_EXPOSURE 換算路徑
        # 本身有 bug,這裡仍能獨立擋下超額值。
        #
        # 順序(安全審查 LOW-10,與策略一/五一致):裁剪必須在 min_stake 檢查**之前**。
        # 若先檢查 min_stake 再裁剪,而裁剪把金額壓到 min_stake 以下,freqtrade 的
        # validate_stake_amount() 會把它拉回 min_stake(容許最多 +30%),送出的金額
        # 可能又略微超出胖手指硬上限,在 confirm_trade_entry 的背書檢查(容差極小)
        # 被拒絕,整筆訊號被靜默丟棄。改成先裁剪,再拿裁剪後的金額跟 min_stake 比,
        # 結果是明確的「不下單」而不是「下了又被自己擋掉」。
        final_stake = min(stake, max_stake)
        combined_used_notional = sum(
            (t.stake_amount or 0.0) for t in Trade.get_open_trades() if t.pair != pair
        )
        clamped = ffg.clamp_stake(final_stake, total_equity, combined_used_notional)
        if clamped.was_clamped:
            logger.warning("[%s] 胖手指防護:%s", pair, clamped.reason)
        final_stake = clamped.stake

        floor = min_stake or 0.0
        if final_stake < floor:
            return 0.0  # 低於交易所最小下單量,寧可不進場也不要下一個假的部位
        return final_stake

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        """
        security-policy.md 第 5 節:胖手指防護層,送出前最後一次背書檢查。

        策略四本身沒有 risk-policy.md 3.1 節那種每日虧損熔斷自訂邏輯——CP-005
        已論證事件驅動機制對這個永遠在市、狀態驅動的策略不適用(見檔案開頭
        風控段落),本檔案原本也沒有 confirm_trade_entry。這裡新增的**只是**
        獨立的胖手指背書檢查,不引入任何新的訊號/風控判斷,也不改動
        custom_stake_amount/adjust_trade_position 的既有計算邏輯。

        confirm_trade_entry 依 Freqtrade 介面只能回傳 bool,無法裁剪 amount
        (fatfinger_guard.py 模組 docstring「接線位置」已查證說明)——真正的
        裁剪已經在 custom_stake_amount 尾端完成,這裡只是防禦性地重新核對
        即將送出的 amount*rate 是否仍在獨立上限內,理論上應該永遠通過。
        """
        equity = self.wallets.get_total_stake_amount() if self.wallets else None
        combined_used_notional = sum(
            (t.stake_amount or 0.0) for t in Trade.get_open_trades() if t.pair != pair
        )
        backstop = ffg.validate_notional_within_caps(
            notional=amount * rate,
            equity=equity,
            combined_used_notional=combined_used_notional,
        )
        if not backstop.is_valid:
            logger.warning(
                "[%s] 胖手指防護:confirm_trade_entry 最終背書檢查未通過,拒絕本次下單。"
                "原因:%s",
                pair,
                backstop.reason,
            )
            return False

        return True

    # ---- 既有部位再平衡 ----

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: float | None,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ) -> float | None:
        dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
        if dataframe.empty:
            return None

        sigma_hat = dataframe["realized_vol"].iloc[-1]
        dd = self._record_and_get_drawdown(current_time)
        w_target = self._target_weight_per_pair(sigma_hat, dd)

        total_equity = self.wallets.get_total_stake_amount()
        target_stake = w_target * total_equity
        current_stake = trade.stake_amount
        if current_stake <= 0:
            return None

        # 目標曝險已降到接近零:交給 custom_exit 完整出場,
        # 不要用 adjust_trade_position 硬減到 min_stake 卡住(見模組頂部說明)。
        if w_target < _ZERO_EXPOSURE_EPSILON:
            return None

        # CP-005 第 4 節:再平衡帶,相對偏離未達門檻就不動,避免換手成本侵蝕
        rel_dev = abs(target_stake - current_stake) / current_stake
        if rel_dev < vt.REBALANCE_BAND:
            return None

        delta = target_stake - current_stake
        if delta > 0:
            # security-policy.md 第 5 節:胖手指防護層。
            #
            # ⚠️ 這條路徑原本完全繞過 custom_stake_amount/confirm_trade_entry 的胖手指
            # 檢查——已用 freqtrade 原始碼查證確認(freqtradebot.py:execute_entry/
            # get_valid_enter_price_and_stake):custom_stake_amount 只在 `trade is None`
            # (全新倉位)才被呼叫,confirm_trade_entry 只在 `mode == "initial"` 才被呼叫;
            # adjust_trade_position 回傳的加碼金額(mode="pos_adjust"、trade 不是 None)
            # 兩者都不會經過,直接送進 execute_entry。這是這個「永遠在市、只靠再平衡
            # 調整曝險」策略裡唯一會擴大部位的路徑,不能沒有獨立檢查。
            #
            # 因此在這裡直接補上與 custom_stake_amount 尾端同一套獨立裁剪 ——
            # combined_used_notional 用「這筆之外的其他持倉」,加碼後的總部位
            # (current_stake + 裁剪後的加碼量)才是這個交易對真正會佔用的名目金額,
            # 所以裁剪對象是 current_stake + delta(加碼後的總部位),不是 delta 本身。
            equity_for_clamp = self.wallets.get_total_stake_amount()
            combined_used_notional = sum(
                (t.stake_amount or 0.0) for t in Trade.get_open_trades() if t.pair != trade.pair
            )
            proposed_total = min(current_stake + delta, current_stake + max_stake)
            clamped = ffg.clamp_stake(proposed_total, equity_for_clamp, combined_used_notional)
            if clamped.was_clamped:
                logger.warning("[%s] 胖手指防護(再平衡加碼):%s", trade.pair, clamped.reason)
            clamped_delta = clamped.stake - current_stake
            if clamped_delta <= 0:
                return None
            return min(clamped_delta, max_stake)
        # 減碼:不得把部位降到 min_stake 以下(那要走完整出場,不是部分減碼)
        floor = min_stake or 0.0
        return -min(-delta, current_stake - floor) if current_stake > floor else None

    # ---- 目標曝險歸零時的完整出場 ----

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | bool | None:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return None

        sigma_hat = dataframe["realized_vol"].iloc[-1]
        dd = self._record_and_get_drawdown(current_time)
        w_target = self._target_weight_per_pair(sigma_hat, dd)

        if w_target < _ZERO_EXPOSURE_EPSILON:
            return "vol_target_zero_exposure"
        return None

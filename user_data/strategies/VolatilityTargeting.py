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
"""

from __future__ import annotations

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

        total_equity = self.wallets.get_total_stake_amount()
        stake = w * total_equity
        floor = min_stake or 0.0
        if stake < floor:
            return 0.0  # 低於交易所最小下單量,寧可不進場也不要下一個假的部位
        return min(stake, max_stake)

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
            return min(delta, max_stake)
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

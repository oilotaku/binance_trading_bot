# pragma pylint: disable=missing-docstring, invalid-name
"""
策略五:以 Kalman 濾波趨勢斜率取代固定停損的出場機制。

對應規劃文件(不重新設計,只把已核准規格接進 Freqtrade):
  - docs/strategy-5-hypothesis.md
        核心規格書。第 2 節可證偽預測、第 4 節參數事前固定表、
        第 6.3 節 StoplossGuard 缺口排查,均直接對應本檔案的實作。
  - docs/change-proposals/strategy-5-exit-mechanism-hypothesis.md
        原始提案,第 2.2 節出場規則的數學定義(μ̂_t < 0,θ=0.5)。
  - docs/change-proposals/CP-008-strategy-5-backstop-and-sizing.md
        災難後備停損 -22%、position sizing k'=5.0 的正式核准數字。
  - docs/change-proposals/CP-003-fixed-parameters.md
        進場參數固定值(donchian_period=20、volume_ma_period=20、
        volume_multiplier=1.5、atr_period=14)的原始核准與依據強度。
  - analysis/trend_filter.py
        出場訊號的既有實作(trend_exit_signal,已用合成資料測過 9 項,
        含「無前視偏誤」的截斷不變性測試)。本檔案不修改其邏輯,只呼叫。
  - user_data/strategies/RegimeFilteredMomentumBreakout.py
        策略一。策略五沿用它的進場邏輯、風控接線模式(每日虧損熔斷、
        protections、position sizing 公式結構),差異全部在出場機制。

============================================================================
⚠️ 本檔案在落實已核准規格的過程中,做了以下幾項**實作階段的判斷**——
不是任何已核准文件明文寫死的決定,建議專案負責人核准本檔案時一併確認:
============================================================================

1. **移除 Donchian 下軌「軟」出場與 45 天 time-stop,只保留 Kalman 訊號
   作為唯一的常規出場路徑。** 策略一原本有三條出場路徑(ATR 移動停損 /
   Donchian 下軌 / 45 天 time-stop,任一觸發即出場)。strategy-5-exit-
   mechanism-hypothesis.md §2.2 把 `μ̂_t < 0` 描述成「出場規則」(單數,
   未提及與其他規則並存),而 strategy-5-hypothesis.md 第 2 節的可證偽
   預測明確預期「平均持倉天數顯著拉長」——若仍保留 45 天 time-stop,
   持倉天數會被硬性上限鎖死在 45 天,這個預測會被人為限制到失去意義,
   Donchian 下軌自身在策略一的實測中也從未觸發過一次(post-mortem
   第 2 節)。因此本檔案採「Kalman 訊號整個取代三條路徑,而非疊加」
   這個實作方向。**✅ 專案負責人已明確確認採「完全取代」(2026-08-16),
   見 strategy-5-hypothesis.md 狀態列。**

2. **`donchian_period` 寫成普通常數,不用 `IntParameter`。** 策略一的
   `IntParameter` 包裝是為了讓 `run_parameter_scan.py` 能透過 Freqtrade
   既有的參數覆寫機制,在 5 個掃描點之間切換(CP-003 §2.2)。策略五依
   strategy-5-hypothesis.md 4.1 節的裁決,只用 `donchian_period=20`
   這一個進場參數點,不重新掃描——沒有任何外部流程需要覆寫這個值。
   用 `IntParameter(..., optimize=False)` 反而會誤導讀者以為它仍是某種
   搜尋空間的殘留物;直接寫成模組常數更誠實地表達「這是治理常數,
   不是搜尋空間」。

3. **`custom_stake_amount` 內 `_risk_fraction_of_trade` 的計算基礎改為
   即時 ATR,而非讀 `trade.stop_loss`。** 策略一的版本讀
   `trade.stop_loss`(由 `custom_stoploss` 動態維護,追蹤 k×ATR 的移動
   停損距離),藉此回推每筆既有部位佔用了多少 `risk_fraction` 配額。
   策略五沒有 `custom_stoploss`——`trade.stop_loss` 只會是靜態的 -22%
   災難後備值。若直接沿用策略一的寫法去讀它,會用「後備停損距離」
   而非「sizing 實際依據的 k'×ATR 距離」回推配額,嚴重高估已佔用的
   風險,使合併曝險上限在實務上形同把第二筆倉位全面鎖死。這裡改用
   「該交易對目前的 k'×ATR」重新計算距離,與 `custom_stake_amount`
   開新倉時的公式基礎一致,只是用當下 ATR 而非進場當下凍結的值
   (策略一的 `trade.stop_loss` 因為有移動停損,本來就會隨時間變動,
   這裡的處理方式與其精神一致,細節見 `_risk_fraction_of_trade` 內的
   註解)。這是本檔案撰寫過程中發現並處理的架構細節,不是文件明文
   交代的決定。

4. **`use_exit_signal = True`,即使 `populate_exit_trend` 不設任何
   `exit_long` 訊號。** 直接讀 Freqtrade 原始碼
   (`freqtrade/strategy/interface.py` `ft_check_exit_conditions` /
   `ft_check_exit_timed`,行號約 1462 附近)確認:`custom_exit()`
   callback 整個被包在 `if self.use_exit_signal:` 內——`use_exit_signal
   = False` 會讓 `custom_exit` 完全不被呼叫,不是只影響
   `populate_exit_trend` 訊號。策略五的出場完全依賴 `custom_exit`
   回傳 Kalman 訊號,因此 `use_exit_signal` 必須是 `True`;
   `populate_exit_trend` 保持不設任何欄位,只是確保
   `exit_ and not enter` 恆為假,讓判斷落到 `custom_exit` 分支。
   這是讀原始碼後的判斷,不是文件交代的決定——比照
   strategy-5-hypothesis.md 6.3 節「用 Freqtrade 原始碼排查、非推測」
   的同一種嚴謹度。

5. **第 6.3 節「頻率型連續虧損守門」的鎖倉語意,比照 Freqtrade 原生
   `StoplossGuard`(`freqtrade/plugins/protections/stoploss_guard.py`
   `calculate_lock_end`)——鎖倉到期時間 = 觸發窗口內最後一筆虧損交易
   的平倉時間 + `stop_duration`,而不是單純「往回看 lookback 天內是否
   還有 ≥2 筆虧損」這種會隨視窗滑動而鎖更久的簡化版本。細節見
   `_consecutive_loss_guard_locked_until`。

以上 1–5 項,任何一項若審閱者認為方向不對,都應在核准本檔案前提出,
而不是等真實回測結果出來後才回頭調整(CLAUDE.md 預先登錄原則)。
============================================================================

6. **security-policy.md 第 5 節:胖手指防護層。** custom_stake_amount 尾端
   (final_stake 算出後、return 之前)與 confirm_trade_entry 尾端(送出前
   最後一次背書)各新增一層獨立檢查,呼叫 fatfinger_guard.py(獨立宣告的
   硬上限常數,不透過本檔案的 Kelly/ATR 計算路徑推導,與策略一共用同一份
   獨立模組)。這一層是新增的防禦,**不改動**上面 1–5 項已交代的既有計算
   邏輯本身。
============================================================================
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_STRATEGY_DIR = Path(__file__).resolve().parent
if str(_STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(_STRATEGY_DIR))

from analysis import trend_filter as tf  # noqa: E402
import fatfinger_guard as ffg  # noqa: E402  security-policy.md 第 5 節,獨立胖手指防護層

logger = logging.getLogger(__name__)


class TrendFilterExit(IStrategy):
    """
    docs/strategy-5-hypothesis.md 策略五。
    進場:與策略一完全相同——日線收盤突破 20 日 Donchian 上軌(不含當日)
          且成交量 >= 20 日均量 * 1.5(CP-003,原樣繼承,不重新掃描)。
    出場(唯一的常規路徑,取代策略一原本的三條路徑,見檔案頂部判斷 1):
          custom_exit — Kalman 濾波後的趨勢斜率轉負(μ̂_t < 0,
          cutoff_period_days=45,見 strategy-5-exit-mechanism-hypothesis.md
          §2.2、§2.3)。
    災難後備停損(唯一的價格型 fallback,非正常出場路徑):
          class attribute `stoploss = -0.22`(CP-008),不設
          `custom_stoploss`——沒有 ATR 移動停損需要動態維護。
    不設固定停利(沿用策略一 risk-policy.md 2.2 節的立場),minimal_roi
    明確停用。
    """

    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "1d"

    # 不設固定停利,minimal_roi 實質停用(需 1000% 獲利才觸發)——與策略一同一立場
    minimal_roi = {"0": 10}

    # CP-008 §4.1:災難後備停損,取代策略一的 -0.25,僅適用於策略五。
    # 沒有 custom_stoploss——這是唯一的價格型出場 fallback,不是正常出場路徑
    # (正常出場路徑是 custom_exit 的 Kalman 訊號,見下)。
    stoploss = -0.22
    use_custom_stoploss = False

    process_only_new_candles = True
    # 見檔案頂部判斷 4:custom_exit 完全被包在 `if self.use_exit_signal` 內,
    # 即使 populate_exit_trend 不設任何 exit_long 訊號,這裡也必須是 True,
    # 否則 Kalman 出場訊號永遠不會被評估。
    use_exit_signal = True
    exit_profit_only = False

    # architecture-spec.md 3.3 節:須 >= max(N, M, ATR週期) + 緩衝,避免指標未穩定(NaN)
    # 產生錯誤訊號。策略五只有一個 Donchian 點(20),不像策略一要遷就掃描上界 55,
    # 緩衝可以縮小,但仍保守抓一個整數週期的餘裕。
    startup_candle_count: int = 30

    # --- 進場參數:CP-003 已核准的固定值,原樣繼承(strategy-5-hypothesis.md 第 4 節) ---
    #
    # donchian_period 不像策略一那樣用 IntParameter 包裝——理由見檔案頂部判斷 2:
    # 策略五只用這一個進場參數點(strategy-5-hypothesis.md 4.1 節裁決,不重新掃描),
    # 沒有任何外部流程(如 run_parameter_scan.py)需要在多個值之間切換它,寫成
    # IntParameter 反而會誤導讀者以為它仍是搜尋空間的一部分。這是治理常數,不是
    # hyperopt 可調參數。
    DONCHIAN_PERIOD = 20
    VOLUME_MA_PERIOD = 20
    VOLUME_MULTIPLIER = 1.5

    # atr_period=14(Wilder 1978)——策略五下用途已改變(strategy-5-hypothesis.md
    # 4.2、6.6 節已定案):不再是出場判準的一部分,只作為 position sizing 分母
    # 的來源(k' × ATR)。
    ATR_PERIOD = 14

    # CP-008 §4.2:position sizing 的 ATR 係數,取代策略一的 k=3.0。
    # 用真實 BTC/ETH「上升 regime → 斜率轉負」事件的 MAE%/ATR14% 比值
    # (p90~p95)校準,結構與策略一完全相同,只換這一個常數。
    ATR_MULTIPLIER = 5.0  # k'

    # strategy-5-exit-mechanism-hypothesis.md §2.2/§2.3:Kalman 濾波出場訊號的
    # 唯一參數,借用 risk-policy.md 2.3 節早已定案的 45 天 time-stop 時間尺度
    # (該文件已誠實揭露這是借用,非原始決策本意)。θ=0.5 已內建於
    # analysis/trend_filter.py 的 trend_exit_signal,不在此重複指定。
    CUTOFF_PERIOD_DAYS = 45.0

    # --- 治理硬上限(architecture-spec.md 3.2 節:絕不可作為 hyperopt 可調參數,一律寫死) ---
    # risk-policy.md 第 0/1 節,策略一、策略五共用同一套數字(CP-008 只變動
    # position sizing 的 k' 與災難後備停損,未變動這些上限)。
    RISK_FRACTION_SINGLE_CAP = 0.015  # 單筆 risk_fraction 硬上限 1.5%
    RISK_FRACTION_COMBINED_CAP = 0.025  # 合併(BTC+ETH 同時持倉)risk_fraction 上限 2.5%
    NOTIONAL_SINGLE_CAP = 0.50  # 單筆名目部位上限,佔權益比例
    NOTIONAL_COMBINED_CAP = 0.80  # 合併名目部位上限,佔權益比例
    # risk-policy.md 3.1 節,與策略一相同
    DAILY_LOSS_BREAKER_THRESHOLD = -0.04  # 當日已實現+未實現損益低於此值,拒絕新進場

    # strategy-5-hypothesis.md 6.3 節:StoplossGuard 補救。原生 Protection 100%
    # 偵測不到 Kalman 觸發的出場(exit_reason 不會是 stop_loss/trailing_stop_loss/
    # stoploss_on_exchange/liquidation 之一,已用 Freqtrade 原始碼排查確認)。
    # 參數沿用策略一 StoplossGuard 原值——6.3 節寫明「待 risk-manager 在 Phase 6
    # 前決定」,沿用原值是合理預設,不是新設計決策。
    LOSS_GUARD_LOOKBACK_DAYS = 10
    LOSS_GUARD_TRADE_LIMIT = 2
    LOSS_GUARD_STOP_DURATION_DAYS = 5

    # architecture-spec.md 2.2 節:protections 只能是策略類別的 class attribute。
    # StoplossGuard 保留——依 strategy-5-hypothesis.md 6.3 節,它偵測不到 Kalman
    # 觸發的出場,但對「真的因為觸及 -22% 後備停損」(ExitType.STOP_LOSS)的出場
    # 仍然有效,不是死配置。Kalman 觸發出場的補救見 confirm_trade_entry 內的
    # _consecutive_loss_guard_locked_until。
    protections = [
        {
            "method": "CooldownPeriod",
            "stop_duration_candles": 2,
        },
        {
            "method": "StoplossGuard",
            "lookback_period_candles": 10,
            "trade_limit": 2,
            "stop_duration_candles": 5,
            "only_per_pair": False,
        },
        {
            # risk-policy.md 6.4 節:月回撤熔斷(scope.md 既有 8% 門檻)
            "method": "MaxDrawdown",
            "lookback_period_candles": 30,
            "trade_limit": 2,
            "stop_duration_candles": 1,
            "max_allowed_drawdown": 0.08,
        },
        {
            # risk-policy.md 第 5 節:kill switch 層級,帳戶回撤 15%
            "method": "MaxDrawdown",
            "lookback_period_candles": 365,
            "trade_limit": 2,
            "stop_duration_candles": 1,
            "max_allowed_drawdown": 0.15,
        },
    ]

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        # security-policy.md 5.2 節第 3 點:胖手指防護層需要「上一次讀值」才能
        # 判斷 equity 是否有異常跳動,IStrategy 本身不提供這個狀態,這裡新增一個
        # instance attribute 自行維護。只被 custom_stake_amount/fatfinger_guard
        # 讀寫,不影響任何既有訊號/出場/停損計算邏輯。
        self._fatfinger_last_equity: float | None = None

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        向量化計算 Donchian 上軌、成交量均量、ATR。全部指標僅使用已收盤 K 棒。
        不計算 Donchian 下軌——策略五沒有 Donchian 出場路徑(見檔案頂部判斷 1)。
        """
        # .shift(1):今天的突破比較的是「不含今天」的過去 N 日最高價——
        # strategy-hypothesis.md「強制隔根進場,不可用未收盤K棒判斷」在指標層級的落實。
        dataframe["donchian_upper"] = (
            dataframe["high"].rolling(window=self.DONCHIAN_PERIOD).max().shift(1)
        )
        dataframe["volume_ma"] = (
            dataframe["volume"].rolling(window=self.VOLUME_MA_PERIOD).mean()
        )
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.ATR_PERIOD)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["close"] > dataframe["donchian_upper"])
                & (dataframe["volume"] >= dataframe["volume_ma"] * self.VOLUME_MULTIPLIER)
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        不設任何訊號型出場——策略五唯一的常規出場路徑是 custom_exit 的 Kalman
        訊號(見檔案頂部判斷 1)。保留這個空實作(而非完全不定義)是為了讓
        use_exit_signal=True 時 `exit_ and not enter` 恆為假,判斷才會落到
        custom_exit 分支(見檔案頂部判斷 4)。
        """
        return dataframe

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        """
        strategy-5-exit-mechanism-hypothesis.md §2.2:出場 ⟺ μ̂_t < 0
        (Kalman 濾波後的趨勢斜率後驗轉負,cutoff_period_days=45)。

        效能考量(strategy-5-hypothesis.md 未特別要求,但需要交代):每次呼叫都對
        整條已知歷史重新跑濾波,而不是維護增量式的濾波狀態。這是刻意的——
        `trend_exit_signal` 是嚴格因果的純函式(見 analysis/trend_filter.py 的
        `test_filter_uses_no_future_information`),重跑整條路徑不影響正確性,
        只影響回測要跑多久;維護增量式濾波狀態容易在 backtest 重跑、trade 提前
        關閉等邊界情況下出錯且難以察覺,不值得為了效能犧牲正確性。
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return None  # 拿不到資料時不宣告出場,退回 -22% 災難後備停損

        close = dataframe["close"]
        if close.isna().any() or (close <= 0).any():
            return None  # 輸入不合理,fail closed(不宣告出場,不是不宣告進場)

        log_price = np.log(close.to_numpy(dtype=float))
        if log_price.size < 2:
            return None  # 資料不足以濾波

        signal = tf.trend_exit_signal(log_price, cutoff_period_days=self.CUTOFF_PERIOD_DAYS)
        if signal.size and bool(signal[-1]):
            return "kalman_slope_negative"
        return None

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
        risk-policy.md 3.1-3.4 節的每日虧損熔斷,加上 strategy-5-hypothesis.md
        6.3 節新增的「頻率型連續虧損守門」(鏡射 StoplossGuard,但不篩
        exit_reason)。兩者都是本策略程式碼內的自訂邏輯,不像原生 Protection
        有框架層雙重保險,依 risk-policy.md 3.4 節要求需要加強測試覆蓋。
        """
        daily_pnl_ratio = self._get_daily_pnl_ratio(current_time)
        if daily_pnl_ratio is None:
            # fail closed:查不到當日損益基準時,不下單(security-policy.md 5.2 節同一原則)
            return False

        if daily_pnl_ratio <= self.DAILY_LOSS_BREAKER_THRESHOLD:
            return False

        lock_until = self._consecutive_loss_guard_locked_until(current_time)
        if lock_until is not None and current_time < lock_until:
            return False

        # --- security-policy.md 5.2 節:胖手指防護層,送出前最後一次背書檢查 ---
        # confirm_trade_entry 依 Freqtrade 介面只能回傳 bool,無法裁剪 amount
        # (fatfinger_guard.py 模組 docstring「接線位置」已查證說明)——真正的
        # 裁剪已經在 custom_stake_amount 尾端完成,這裡只是防禦性地重新核對
        # 即將送出的 amount*rate 是否仍在獨立上限內,理論上應該永遠通過。
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

    def _get_daily_pnl_ratio(self, current_time: datetime) -> float | None:
        """回傳當日(UTC 曆日)已實現 + 未實現損益,佔當日開盤權益的比例。"""
        if self.wallets is None:
            return None

        total_equity = self.wallets.get_total_stake_amount()
        if total_equity is None or total_equity <= 0:
            return None

        day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        if day_start.tzinfo is None:
            day_start = day_start.replace(tzinfo=timezone.utc)

        closed_today_pnl = 0.0
        for trade in Trade.get_trades_proxy(is_open=False):
            if trade.close_date_utc and trade.close_date_utc >= day_start:
                closed_today_pnl += trade.close_profit_abs or 0.0

        open_unrealized_pnl = 0.0
        for trade in Trade.get_trades_proxy(is_open=True):
            open_unrealized_pnl += trade.calc_profit(trade.close_rate or trade.open_rate) or 0.0

        # 分母用「目前權益」近似「當日開盤權益」——保守選擇:若當日已虧損,目前權益已經
        # 比開盤時小,用它當分母會讓算出的虧損比例被放大(更保守),不會低估風險。
        return (closed_today_pnl + open_unrealized_pnl) / total_equity

    def _consecutive_loss_guard_locked_until(self, current_time: datetime) -> datetime | None:
        """
        strategy-5-hypothesis.md 6.3 節:鏡射 Freqtrade 原生 `StoplossGuard`
        (freqtrade/plugins/protections/stoploss_guard.py `_stoploss_guard`)的
        鎖倉邏輯,唯一的差異是不篩 `exit_reason`——不論是 Kalman 出場還是
        -22% 後備停損觸發的出場,只要 `close_profit < 0` 就計入(原生版本
        只計入 exit_reason 屬於 stop_loss/trailing_stop_loss/
        stoploss_on_exchange/liquidation 之一的交易,策略五的 Kalman 出場
        不屬於這四類之一,見檔案頂部說明與該節排查)。

        鎖倉到期時間比照原生 `IProtection.calculate_lock_end`:觸發窗口內
        最後一筆虧損交易的平倉時間 + `stop_duration`,而不是單純「現在往回看
        lookback 天內是否還有 >= trade_limit 筆虧損」——後者會因為視窗持續
        滑動而鎖倉時間長短不一,不是原生語意的忠實鏡射。

        :return: 若目前應鎖倉,回傳鎖倉解除時間;否則回傳 None。
        """
        lookback_start = current_time - timedelta(days=self.LOSS_GUARD_LOOKBACK_DAYS)

        losing_trades = [
            trade
            for trade in Trade.get_trades_proxy(is_open=False)
            if trade.close_date_utc
            and trade.close_date_utc >= lookback_start
            and (trade.close_profit or 0.0) < 0
        ]

        if len(losing_trades) < self.LOSS_GUARD_TRADE_LIMIT:
            return None

        last_loss_close = max(trade.close_date_utc for trade in losing_trades)
        return last_loss_close + timedelta(days=self.LOSS_GUARD_STOP_DURATION_DAYS)

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
        """
        risk-policy.md 第 1、4 節部位大小公式,結構與策略一完全相同,只把
        `atr_multiplier` 換成 CP-008 核准的 `k'=5.0`(見 ATR_MULTIPLIER)。

        完整公式(statistical-methodology.md 5.2 節,待 Phase 6 補上真實 f*):
            risk_fraction = min(0.25 * f* * k' * ATR%, RISK_FRACTION_SINGLE_CAP)
        f* 尚無真實數據,本骨架版本 risk_fraction 直接採用硬上限(min() 中
        必然生效的那一項)——與策略一同一立場,理由見
        RegimeFilteredMomentumBreakout.custom_stake_amount 的同一段說明。
        """
        total_equity = self.wallets.get_total_stake_amount() if self.wallets else None
        if not total_equity or total_equity <= 0:
            return 0.0  # fail closed

        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return 0.0

        last_atr = dataframe["atr"].iloc[-1]
        if pd.isna(last_atr) or last_atr <= 0 or current_rate <= 0:
            return 0.0

        # --- security-policy.md 5.2 節第 3 點:獨立輸入合理性檢查(胖手指防護層) ---
        # 與策略一(RegimeFilteredMomentumBreakout.custom_stake_amount)同一段
        # 說明:上面兩個既有 if 區塊無法偵測 NaN,這裡呼叫獨立模組
        # fatfinger_guard 重新驗證一次,額外涵蓋 NaN/inf 與「equity 相對上次讀值
        # 異常跳動」兩種情況,不影響上面既有邏輯本身。
        #
        # `equity_jump_baseline()`:回測/hyperopt 時把跳動比對關掉(傳入 None),
        # 理由同策略一,完整說明見 fatfinger_guard.equity_jump_baseline 的 docstring
        # ——這條規則若在回測誤觸發,會讓 docs/strategy-5-results.md 的已定案數字
        # 無法重現。「有限正數」檢查在所有 runmode 下都繼續生效。
        validation = ffg.validate_sizing_inputs(
            equity=total_equity,
            risk_indicator=last_atr,
            last_equity=ffg.equity_jump_baseline(
                self.dp.runmode if self.dp else None, self._fatfinger_last_equity
            ),
        )
        # 安全審查 MED-4:基準值的更新**不以本次驗證通過為條件**(三個策略一致)。
        # 只要這次的 equity 本身是合理的有限正數,就讓它成為下次跳動比對的基準;
        # 否則一次跳動誤判會讓基準永遠停在舊值,單次異常升級成永久鎖死進場。
        # 完整理由見 fatfinger_guard.validate_sizing_inputs 的職責分離段落。
        if validation.equity_usable_as_baseline:
            self._fatfinger_last_equity = total_equity
        if not validation.is_valid:
            logger.warning(
                "[%s] 胖手指防護:custom_stake_amount 輸入合理性檢查未通過,"
                "本次訊號跳過不下單(fail closed)。原因:%s",
                pair,
                validation.reason,
            )
            return 0.0

        k_prime = self.ATR_MULTIPLIER

        # risk-policy.md 4.2 節:合併曝險上限。先算目前已用掉多少 combined risk_fraction。
        used_risk_fraction = 0.0
        for trade in Trade.get_open_trades():
            if trade.pair == pair:
                continue
            used_risk_fraction += self._risk_fraction_of_trade(trade, total_equity)

        available_risk_fraction = max(
            0.0, self.RISK_FRACTION_COMBINED_CAP - used_risk_fraction
        )
        risk_fraction = min(self.RISK_FRACTION_SINGLE_CAP, available_risk_fraction)
        if risk_fraction <= 0:
            return 0.0  # 合併上限已滿,不再開新倉

        # ATR 公式換算的下單量(risk-policy.md 1.4 節公式,k 換成 k')
        stake_from_atr = (risk_fraction * total_equity) / k_prime / (last_atr / current_rate)

        # risk-policy.md 4.3 節:獨立於 ATR 公式的名目部位硬上限(防低波動度導致的異常巨大部位)
        notional_cap = self.NOTIONAL_SINGLE_CAP * total_equity
        combined_used_notional = sum(
            (t.stake_amount or 0.0) for t in Trade.get_open_trades() if t.pair != pair
        )
        combined_notional_cap = max(
            0.0, self.NOTIONAL_COMBINED_CAP * total_equity - combined_used_notional
        )

        final_stake = min(stake_from_atr, notional_cap, combined_notional_cap, max_stake)

        # --- security-policy.md 5.2 節第 1/2 點:獨立來源硬上限的最終裁剪 ---
        # fatfinger_guard.clamp_stake 用模組自己獨立宣告的 NOTIONAL_SINGLE_CAP/
        # NOTIONAL_COMBINED_CAP(數值上與上面 self.NOTIONAL_SINGLE_CAP/
        # self.NOTIONAL_COMBINED_CAP 相同,但物理上是兩份獨立宣告)——即使上面
        # min() 那一行本身有 bug,這裡仍能獨立擋下超額值。
        #
        # 順序(安全審查 LOW-10,與策略一一致):裁剪必須在 min_stake 檢查之前。
        # 先檢查再裁剪的話,被裁剪到 min_stake 以下的金額會被 freqtrade 的
        # validate_stake_amount() 拉回 min_stake(容許最多 +30%),於是送出金額
        # 可能又超過硬上限,再被 confirm_trade_entry 的背書檢查靜默擋掉。
        clamped = ffg.clamp_stake(final_stake, total_equity, combined_used_notional)
        if clamped.was_clamped:
            logger.warning("[%s] 胖手指防護:%s", pair, clamped.reason)
        final_stake = clamped.stake

        if min_stake and final_stake < min_stake:
            return 0.0  # 算出的部位小於交易所最小下單量,寧可不下單也不要下超額單

        return final_stake

    def _risk_fraction_of_trade(self, trade: Trade, total_equity: float) -> float:
        """
        回推一筆既有交易目前佔用了多少 risk_fraction 配額,供合併上限計算使用。

        ⚠️ 與策略一的關鍵差異(檔案頂部判斷 3 已交代):策略一讀
        `trade.stop_loss`(由 custom_stoploss 動態維護,追蹤 k×ATR 的移動停損
        距離)。策略五沒有 custom_stoploss,`trade.stop_loss` 只會是靜態的
        -22% 後備值——直接沿用策略一的寫法會用「後備停損距離」而非「sizing
        實際依據的 k'×ATR 距離」回推配額,嚴重高估已佔用的風險。

        這裡改用該交易對「目前」的 k'×ATR 重新計算距離,與 custom_stake_amount
        開新倉時的公式基礎一致。取不到即時 ATR 時保守地回傳 0(不計入占用),
        寧可低估其他交易對已佔用的配額,也不要因為抓不到某個交易對的資料
        就連帶卡死這筆全新倉位的下單——這與 custom_stake_amount 對「本次要
        開的這筆倉位」缺資料時 fail closed(直接回傳 0 不開倉)不同,
        這裡處理的是「其他已存在倉位」的估計,兩種情境的正確保守方向相反。
        """
        if not trade.open_rate or trade.open_rate <= 0:
            return 0.0

        dataframe, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
        if dataframe.empty:
            return 0.0

        last_atr = dataframe["atr"].iloc[-1]
        if pd.isna(last_atr) or last_atr <= 0:
            return 0.0

        notional = trade.stake_amount or 0.0
        stop_distance_ratio = (self.ATR_MULTIPLIER * last_atr) / trade.open_rate
        return (notional * stop_distance_ratio) / total_equity

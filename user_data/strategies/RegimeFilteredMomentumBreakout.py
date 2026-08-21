# pragma pylint: disable=missing-docstring, invalid-name
"""
Phase 10 骨架實作,對應以下規劃文件的決定(不重新設計,只落實既有規格):
  - docs/strategy-hypothesis.md   策略一:Regime-Filtered Momentum Breakout
  - docs/statistical-methodology.md  Kelly 倉位公式、lookahead 防範原則
  - docs/architecture-spec.md     Freqtrade callback 對應表(3.1 節)、hyperopt 參數分類(3.2 節)
  - docs/risk-policy.md           所有具體數字(risk_fraction、k 邊界、time-stop、protections 參數)
  - docs/execution-spec.md        order_types/stoploss_on_exchange 設定(在 config-common.json,非本檔)

本檔案目前狀態:訊號邏輯、ATR 停損、風控硬上限、time-stop、每日熔斷自訂邏輯均已實作。
Kelly 倉位公式的 f*(需要 Phase 6 walk-forward 產出的 OOS Sharpe/波動度)尚未有真實數字,
custom_stake_amount 暫時只採用 risk-policy.md 的硬上限,待 Phase 6 backtest-procedure.md
跑出真實數據後,再依 statistical-methodology.md 5.2 節公式補上 Kelly 項——這是刻意的、
有文件依據的簡化,不是遺漏(硬上限本來就是 min() 的一部分,現在只是暫時只有這一項生效)。

security-policy.md 第 5 節:胖手指防護層。custom_stake_amount 尾端(final_stake 算出後、
return 之前)與 confirm_trade_entry 尾端(送出前最後一次背書)各新增一層獨立檢查,
呼叫 fatfinger_guard.py(獨立宣告的硬上限常數,不透過本檔案的 Kelly/ATR 計算路徑推導)。
這一層是新增的防禦,**不改動**上面說明的既有訊號/停損/停利/風控計算邏輯本身。
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IntParameter, IStrategy, stoploss_from_absolute

_STRATEGY_DIR = Path(__file__).resolve().parent
if str(_STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(_STRATEGY_DIR))

import fatfinger_guard as ffg  # noqa: E402  security-policy.md 第 5 節,獨立胖手指防護層

logger = logging.getLogger(__name__)


class RegimeFilteredMomentumBreakout(IStrategy):
    """
    docs/strategy-hypothesis.md 策略一。
    進場:日線收盤突破 N 日 Donchian 上軌(不含當日)且成交量 >= M 日均量 * X。
    出場(三條路徑,任一觸發即出場):
      1. custom_stoploss  — ATR * k 移動停損(risk-policy.md 2.1 節)
      2. populate_exit_trend — 跌破 Donchian 下軌(「軟」出場)
      3. custom_exit      — 45 天 time-stop(risk-policy.md 2.3 節)
    不設固定停利(risk-policy.md 2.2 節),minimal_roi 明確停用。
    """

    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "1d"

    # risk-policy.md 2.2 節:不設固定停利,minimal_roi 實質停用(需 1000% 獲利才觸發)
    minimal_roi = {"0": 10}

    # risk-policy.md 2.1 節:custom_stoploss 失效時的災難後備值,非正常出場路徑會用到的數字
    stoploss = -0.25
    use_custom_stoploss = True

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False

    # architecture-spec.md 3.3 節:須 >= max(N, M, ATR週期) + 緩衝,避免指標未穩定(NaN)產生錯誤訊號
    startup_candle_count: int = 100

    # --- 事前指定的參數(CP-003,2026-08-08 核准)---
    #
    # 依 CP-003 2.1 節,以下四個值在**看到任何真實資料的回測結果之前**寫死。
    # 這不是效能或方便考量:DSR 懲罰的是「總共試了幾次」,把搜尋次數從 200 降到 5
    # 才讓通過門檻從年化 Sharpe 4.01 降到 1.18,首次落入 scope.md 的目標區間。
    #
    # ⚠️ 這些值**不得**因為回測結果不理想而事後調整。那樣做會讓 N 從 5 悄悄變大,
    #    CP-003 第 3 節的門檻計算隨之失效,而且不會有任何機制報錯 ——
    #    唯一的防線是紀律,以及 CP-003 核准 commit 的時間戳。
    #    若這 5 個點都不理想,正確反應是接受策略未通過(CP-003 5.1 節)。
    #
    # 各值依據(強度見 CP-003 2.1 節):
    #   atr_period=14        Wilder (1978) 提出 ATR 時的原始週期          🟢 強
    #   atr_multiplier=3.0   risk-policy.md 2.1 節早已訂為起點            🟢 強
    #   volume_ma_period=20  約一個月交易日,通用慣例;次要濾網非訊號本身  🟡 中
    #   volume_multiplier=1.5「放量確認」的常見門檻,無原典出處            🟠 弱
    ATR_PERIOD = 14
    ATR_MULTIPLIER = 3.0
    VOLUME_MA_PERIOD = 20
    VOLUME_MULTIPLIER = 1.5

    # --- 唯一掃描的參數(CP-003 2.2 節)---
    #
    # donchian_period 是訊號的定義本身,而文獻恰好給出兩個同樣經典的值:
    # Turtle System 1 用 20 日突破、System 2 用 55 日 —— 沒有單一可辯護的先驗,
    # 硬選一個是假裝我們有一個實際上沒有的確定性。
    #
    # 掃描 {20, 30, 40, 50, 55}(端點即兩個經典值),恰好消耗 N=5。
    # 保留為 IntParameter 是為了讓 5 個點能透過 Freqtrade 既有的參數覆寫機制注入;
    # optimize=False —— 它不參與任何搜尋演算法,由 run_parameter_scan.py 逐點明確指定。
    DONCHIAN_SCAN_POINTS = (20, 30, 40, 50, 55)
    donchian_period = IntParameter(
        20, 55, default=DONCHIAN_SCAN_POINTS[0], space="buy", optimize=False, load=True
    )

    def _scan_period(self) -> int:
        """
        取用當前的 donchian_period,並強制它必須是 DONCHIAN_SCAN_POINTS 之一。

        ⚠️ 這個檢查是 CP-003 `N=5` 會計基礎能否成立的關鍵。若某次回測用了掃描清單外
        的週期(殘留的參數覆寫檔、有人手改 default、或像本次一樣 default 忘了同步),
        實際評估過的參數組就超過 5 個,而 CP-003 第 3 節的門檻是以 N=5 算的 ——
        門檻會偏低、結論偏樂觀,**而且不會有任何跡象**。

        因此這裡直接拋例外中止,而不是回退到某個預設值:
        寧可讓回測跑不起來,也不要讓它安靜地跑出一個統計基礎已經失效的數字。
        """
        n = self.donchian_period.value
        if n not in self.DONCHIAN_SCAN_POINTS:
            raise ValueError(
                f"donchian_period={n} 不在 CP-003 核准的掃描點 {self.DONCHIAN_SCAN_POINTS} 內。"
                " 用清單外的值會讓 N 超過 5,使 CP-003 第 3 節的門檻計算失效。"
                " 若確實要新增掃描點,必須先走正式變更提案流程並重算門檻。"
            )
        return n

    # --- 治理硬上限(architecture-spec.md 3.2 節:絕不可作為 hyperopt 可調參數,一律寫死) ---
    # risk-policy.md 第 0/1 節
    RISK_FRACTION_SINGLE_CAP = 0.015  # 單筆 risk_fraction 硬上限 1.5%
    RISK_FRACTION_COMBINED_CAP = 0.025  # 合併(BTC+ETH 同時持倉)risk_fraction 上限 2.5%
    NOTIONAL_SINGLE_CAP = 0.50  # 單筆名目部位上限,佔權益比例
    NOTIONAL_COMBINED_CAP = 0.80  # 合併名目部位上限,佔權益比例
    # risk-policy.md 2.3 節
    TIME_STOP_DAYS = 45
    # risk-policy.md 3.1 節
    DAILY_LOSS_BREAKER_THRESHOLD = -0.04  # 當日已實現+未實現損益低於此值,拒絕新進場

    # architecture-spec.md 2.2 節、user_data/configs/config-common.json 的 _protections_comment 已說明:
    # protections 只能是策略類別的 class attribute,config 層級的設定不會被讀取(原始碼查證,
    # strategy_resolver.py 的可覆寫屬性清單不含 protections)。以下對應 risk-policy.md 第 5/6 節。
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
            # stop_duration_candles=1 於 1d timeframe 下精確等於 24 小時
            "method": "MaxDrawdown",
            "lookback_period_candles": 30,
            "trade_limit": 2,
            "stop_duration_candles": 1,
            "max_allowed_drawdown": 0.08,
        },
        {
            # risk-policy.md 第 5 節:kill switch 層級,帳戶回撤 15%
            # 與上一個 MaxDrawdown 實例能否並存待 Phase 10 實測驗證(risk-policy.md 第 8 節已標記)
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
        """向量化計算 Donchian 通道、成交量均量、ATR。全部指標僅使用已收盤 K 棒。"""
        # 只算 DONCHIAN_SCAN_POINTS 這 5 個週期,不是整個 20–55 的 range ——
        # CP-003 之後掃描點是明確枚舉的,多算的欄位不會被任何訊號讀到。
        for n in self.DONCHIAN_SCAN_POINTS:
            # .shift(1):今天的突破比較的是「不含今天」的過去 N 日最高價 —
            # strategy-hypothesis.md「強制隔根進場,不可用未收盤K棒判斷」在指標層級的落實,
            # 不是效能考量。
            dataframe[f"donchian_upper_{n}"] = (
                dataframe["high"].rolling(window=n).max().shift(1)
            )
            dataframe[f"donchian_lower_{n}"] = (
                dataframe["low"].rolling(window=n).min().shift(1)
            )

        # 以下兩個週期已由 CP-003 固定,不再需要對整個 range 預算欄位
        dataframe["volume_ma"] = (
            dataframe["volume"].rolling(window=self.VOLUME_MA_PERIOD).mean()
        )
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.ATR_PERIOD)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        n = self._scan_period()

        dataframe.loc[
            (
                (dataframe["close"] > dataframe[f"donchian_upper_{n}"])
                & (dataframe["volume"] >= dataframe["volume_ma"] * self.VOLUME_MULTIPLIER)
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """「軟」出場路徑:跌破 Donchian 下軌。ATR 移動停損走 custom_stoploss(見下),不在此處理。"""
        n = self._scan_period()

        dataframe.loc[
            (dataframe["close"] < dataframe[f"donchian_lower_{n}"]),
            "exit_long",
        ] = 1

        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        """
        risk-policy.md 2.1 節:ATR * k 移動停損。
        architecture-spec.md 3.1 節:「硬」出場路徑,因為路徑相依(需要目前 ATR 值),
        populate_exit_trend 做不到,必須用這個 callback。
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return None  # 拿不到資料時不覆寫,退回 class attribute 的 -25% 後備值

        last_atr = dataframe["atr"].iloc[-1]
        if pd.isna(last_atr) or last_atr <= 0:
            return None  # 輸入不合理,不覆寫(fail closed,呼應 security-policy.md 5.2 節輸入合理性檢查)

        k = self.ATR_MULTIPLIER
        stop_price = current_rate - (k * last_atr)
        if stop_price <= 0:
            return None

        return stoploss_from_absolute(
            stop_price, current_rate, is_short=trade.is_short, leverage=trade.leverage
        )

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        """risk-policy.md 2.3 節:45 天無條件 time-stop,不論當下損益。"""
        holding_time = current_time - trade.open_date_utc
        if holding_time >= timedelta(days=self.TIME_STOP_DAYS):
            return "time_stop_45d"
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
        risk-policy.md 3.1-3.4 節:每日虧損熔斷。這是本策略所有防線中唯一沒有 Freqtrade
        Protection 框架層級保證的一層(risk-policy.md 3.4 節已明確揭露),需要與安全關鍵路徑
        同等的實作嚴謹度。重試/fail-closed 邊界依 execution-spec.md 第 9 節政策設計
        (本骨架版本先實作核心判斷邏輯本身;第 9 節要求的有界重試待接上真實交易所餘額查詢時補上,
        目前 self.wallets 是 Freqtrade 已快取的本地狀態查詢,不涉及即時網路呼叫失敗情境)。
        """
        daily_pnl_ratio = self._get_daily_pnl_ratio(current_time)
        if daily_pnl_ratio is None:
            # fail closed:查不到當日損益基準時,不下單(security-policy.md 5.2 節同一原則)
            return False

        if daily_pnl_ratio <= self.DAILY_LOSS_BREAKER_THRESHOLD:
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
        risk-policy.md 第 1、4 節部位大小公式。

        完整公式(statistical-methodology.md 5.2 節,待 Phase 6 補上真實 f*):
            risk_fraction = min(0.25 * f* * k * ATR%, RISK_FRACTION_SINGLE_CAP)
        目前 f*(= SR_1/sigma_1,須用 OOS + bootstrap 悲觀下界)尚無真實數據,
        故本骨架版本 risk_fraction 直接採用硬上限(min() 中必然生效的那一項,
        見 risk-policy.md 1.4 節「在絕大多數可預期的參數情境下,1.5% 硬上限才是實際生效的約束」)。
        Kelly 項待 Phase 6 backtest-procedure.md 的 analysis/position_sizing_check.py
        產出 f* 後,在此處補上取 min() 的比較,不需要更動下面的曝險/名目上限邏輯。
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
        # 與上面兩個既有 if 區塊(total_equity<=0、ATR<=0)刻意有重疊——上面兩個
        # 是主要計算路徑自己的防呆,但無法偵測 NaN(Python 的 `not float("nan")`
        # 為 False,NaN 會直接漏過上面的檢查繼續往下算)。這裡呼叫獨立模組
        # fatfinger_guard 重新驗證一次,額外涵蓋 NaN/inf 與「equity 相對上次讀值
        # 異常跳動」兩種上面完全沒有檢查的情況,不影響上面既有邏輯本身。
        #
        # `equity_jump_baseline()`:回測/hyperopt 時把跳動比對關掉(傳入 None)。
        # 回測同樣會呼叫 custom_stake_amount,而兩次進場訊號之間相隔數週數月時,
        # 權益變動超過 50% 是常態不是異常;誤觸發會讓回測從此永久不再進場,悄悄
        # 改變 docs/pass-b-results.md 等已定案結果的可重現性。完整理由見該函式
        # docstring。「有限正數」檢查不受影響,所有 runmode 下都繼續生效。
        validation = ffg.validate_sizing_inputs(
            equity=total_equity,
            risk_indicator=last_atr,
            last_equity=ffg.equity_jump_baseline(
                self.dp.runmode if self.dp else None, self._fatfinger_last_equity
            ),
        )
        if not validation.is_valid:
            logger.warning(
                "[%s] 胖手指防護:custom_stake_amount 輸入合理性檢查未通過,"
                "本次訊號跳過不下單(fail closed)。原因:%s",
                pair,
                validation.reason,
            )
            return 0.0
        self._fatfinger_last_equity = total_equity

        k = self.ATR_MULTIPLIER

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

        # ATR 公式換算的下單量(risk-policy.md 1.4 節公式)
        stake_from_atr = (risk_fraction * total_equity) / k / (last_atr / current_rate)

        # risk-policy.md 4.3 節:獨立於 ATR 公式的名目部位硬上限(防低波動度導致的異常巨大部位)
        notional_cap = self.NOTIONAL_SINGLE_CAP * total_equity
        combined_used_notional = sum(
            (t.stake_amount or 0.0) for t in Trade.get_open_trades() if t.pair != pair
        )
        combined_notional_cap = max(
            0.0, self.NOTIONAL_COMBINED_CAP * total_equity - combined_used_notional
        )

        final_stake = min(stake_from_atr, notional_cap, combined_notional_cap, max_stake)
        if min_stake and final_stake < min_stake:
            return 0.0  # 算出的部位小於交易所最小下單量,寧可不下單也不要下超額單

        # --- security-policy.md 5.2 節第 1/2 點:獨立來源硬上限的最終裁剪 ---
        # fatfinger_guard.clamp_stake 用的是模組自己獨立宣告的 NOTIONAL_SINGLE_CAP/
        # NOTIONAL_COMBINED_CAP(數值上與上面 self.NOTIONAL_SINGLE_CAP/
        # self.NOTIONAL_COMBINED_CAP 相同,但物理上是兩份獨立宣告,見該模組
        # docstring)——即使上面 min() 那一行本身有 bug,這裡仍能獨立擋下超額值。
        clamped = ffg.clamp_stake(final_stake, total_equity, combined_used_notional)
        if clamped.was_clamped:
            logger.warning("[%s] 胖手指防護:%s", pair, clamped.reason)
        return clamped.stake

    def _risk_fraction_of_trade(self, trade: Trade, total_equity: float) -> float:
        """回推一筆既有交易目前佔用了多少 risk_fraction 配額,供合併上限計算使用。"""
        if not trade.stop_loss or not trade.open_rate or trade.open_rate <= 0:
            return 0.0
        stop_distance_ratio = abs(trade.open_rate - trade.stop_loss) / trade.open_rate
        notional = trade.stake_amount or 0.0
        return (notional * stop_distance_ratio) / total_equity

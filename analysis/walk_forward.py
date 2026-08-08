"""
docs/backtest-procedure.md 第 1.3、4.1 節:walk-forward fold 邊界產生、purge 過濾、
docs/statistical-methodology.md 3.4 節:CUSUM 結構性斷點檢定。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

IS_MONTHS = 24
OOS_MONTHS = 6
STEP_MONTHS = 6
EMBARGO_DAYS_FLOOR = 30  # statistical-methodology.md 3.3 節:硬性下限,不得低於此值


def _add_months(dt: datetime, months: int) -> datetime:
    """簡單月份加法(以 30.44 天近似月長不夠精確,故用曆法年月位移,不用天數近似)。"""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(
        dt.day,
        [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
         31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1],
    )
    return dt.replace(year=year, month=month, day=day)


@dataclass
class Fold:
    index: int
    is_start: datetime
    is_end: datetime
    embargo_start: datetime
    embargo_end: datetime
    oos_start: datetime
    oos_end: datetime

    def as_timerange(self, start: datetime, end: datetime) -> str:
        """轉成 Freqtrade --timerange 慣用的 YYYYMMDD-YYYYMMDD 格式。"""
        return f"{start:%Y%m%d}-{end:%Y%m%d}"

    @property
    def is_timerange(self) -> str:
        return self.as_timerange(self.is_start, self.is_end)

    @property
    def oos_timerange(self) -> str:
        return self.as_timerange(self.oos_start, self.oos_end)


def generate_fold_boundaries(
    anchor: datetime,
    data_end: datetime,
    embargo_days: int = EMBARGO_DAYS_FLOOR,
    is_months: int = IS_MONTHS,
    oos_months: int = OOS_MONTHS,
    step_months: int = STEP_MONTHS,
) -> list[Fold]:
    """
    docs/backtest-procedure.md 1.3 節公式:24mo IS(滾動)/ 6mo OOS / 6mo 步進。
    只回傳 OOS 視窗已完全落在 data_end 之前的「完整」fold ——
    backtest-procedure.md 1.3 節:「日期表會隨時間過期,每次重跑前必須重新產生」,
    這正是本函式存在的理由:不寫死日期表,依 anchor/data_end 動態算。
    """
    if embargo_days < EMBARGO_DAYS_FLOOR:
        raise ValueError(
            f"embargo_days={embargo_days} 低於 statistical-methodology.md 3.3 節硬性下限"
            f" {EMBARGO_DAYS_FLOOR} 天,不合規。"
        )

    folds: list[Fold] = []
    idx = 1
    is_start = anchor
    while True:
        is_end = _add_months(is_start, is_months) - timedelta(days=1)
        embargo_start = is_end + timedelta(days=1)
        embargo_end = embargo_start + timedelta(days=embargo_days - 1)
        oos_start = embargo_end + timedelta(days=1)
        oos_end = _add_months(oos_start, oos_months) - timedelta(days=1)

        if oos_end > data_end:
            break

        folds.append(
            Fold(idx, is_start, is_end, embargo_start, embargo_end, oos_start, oos_end)
        )
        idx += 1
        is_start = _add_months(is_start, step_months)

    return folds


def purge_is_trades(
    trades: pd.DataFrame, is_end: datetime, embargo_days: int, open_date_col: str = "open_date"
) -> pd.DataFrame:
    """
    docs/statistical-methodology.md 3.3 節 purge 規則:
    任何在 IS 視窗最後 embargo_days 天內「進場」的交易,一律從 IS 目標函數計算中剔除
    (不是刪除交易本身,是不讓 hyperopt 用它的結果挑參數 —— 呼叫端決定如何處理被剔除的列)。
    """
    if trades.empty:
        return trades

    cutoff = is_end - timedelta(days=embargo_days - 1)
    open_dates = pd.to_datetime(trades[open_date_col], utc=True)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    mask = open_dates < cutoff
    return trades.loc[mask].copy()


def compute_embargo_days(
    holding_days_p95: float, indicator_lookback_days: int, floor: int = EMBARGO_DAYS_FLOOR
) -> int:
    """
    docs/statistical-methodology.md 3.3 節:
    embargo_days = max(N, M, ATR週期) + 持倉天數 95th 百分位,下限 30 天。
    docs/backtest-procedure.md 4.1 節 Pass A/B 兩輪校準流程呼叫此函式。
    """
    computed = int(np.ceil(indicator_lookback_days + holding_days_p95))
    return max(floor, computed)


def cusum_breakpoints(
    returns: pd.Series, k_sigma: float = 4.5
) -> tuple[np.ndarray, list[int]]:
    """
    docs/statistical-methodology.md 3.4 節:S_t = sum_{i=1}^{t} (r_i - r_bar)。
    偵測穿越 ±k·sigma·sqrt(n) 控制界線的時間點,回傳 (S_t 序列, 斷點索引清單)。
    這不是自動判定策略失敗,只是提醒該子區間需要獨立重新檢視(見該文件說明)。
    """
    r = returns.to_numpy(dtype=float)
    n = len(r)
    if n < 2:
        return np.array([]), []

    r_bar = r.mean()
    s_t = np.cumsum(r - r_bar)
    sigma = r.std(ddof=1)
    limit = k_sigma * sigma * np.sqrt(n)

    breakpoints = list(np.where(np.abs(s_t) > limit)[0])
    return s_t, breakpoints

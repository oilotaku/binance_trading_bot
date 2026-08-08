"""
docs/backtest-procedure.md 1.4 節「K 棒/資料品質與缺漏處理規則」的程式化實作。

該節把規則寫得很完整,但一直只是文字。本模組把規則 1(缺漏偵測)、規則 2(依缺漏
規模分級)、規則 3(異常值偵測)變成可執行的檢查,讓「資料進入任何 fold 之前的
強制步驟」真的是強制的,而不是靠人記得做。

設計上的兩個硬性立場,直接對應 1.4 節的原文:

1. **本模組永遠不修補資料。** 沒有 forward-fill、沒有插值、沒有「修正」異常值的函式,
   連選項都不提供。1.4 節規則 2 明訂 forward-fill 會捏造一根從未存在的價格,若落在
   訊號判斷窗附近就是用假資料產生真訊號。**能力不存在,才不會在趕時間時被說服使用。**
2. **fail closed。** 檢查結果預設是「不通過就擋下」,呼應 security-policy.md 5.2 節。
   要放行必須由呼叫端明確、逐項地承認,不能靠一個全域旗標蓋掉。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

# 規則 3 的門檻,取自 backtest-procedure.md 1.4 節原文「單根振幅 > 過去 20 日 ATR 的 10 倍,
# 且成交量並未同步異常放大」。
SPIKE_ATR_PERIOD = 20
SPIKE_ATR_MULTIPLE = 10.0
# 「成交量並未同步放大」的操作化定義:量能未達近期中位數的 2 倍。
# 1.4 節只寫了「並未同步異常放大」而未給數字,此處是本模組補上的操作定義,
# 選 2× 中位數是因為真實的極端行情日通常伴隨數倍以上放量,而錯誤 tick 不會。
SPIKE_VOLUME_MULTIPLE = 2.0
SPIKE_VOLUME_LOOKBACK = 20

# 規則 2 的分級界線。1.4 節用文字描述三級(單根孤立 / 重下後仍缺 / 連續多日),
# 「連續多日」的界線原文未給數字,此處定為連續 >= 3 日。
EXTENDED_GAP_MIN_DAYS = 3

# 任何早於比特幣創世區塊的加密貨幣 K 棒都不可能是真的。
PLAUSIBLE_EARLIEST = pd.Timestamp("2009-01-03", tz="UTC")


@dataclass
class QualityReport:
    """
    一次資料品質檢查的完整結果。

    刻意不提供 `bool(report)` 之類的簡便判斷 —— 呼叫端必須明確讀 `passed`,
    或自己決定要不要接受 `warnings`,避免「檢查跑了但沒人看結果」。
    """

    pair: str
    n_candles: int
    first_date: pd.Timestamp | None
    last_date: pd.Timestamp | None

    structural_errors: list[str] = field(default_factory=list)
    missing_dates: list[pd.Timestamp] = field(default_factory=list)
    isolated_gaps: list[pd.Timestamp] = field(default_factory=list)
    extended_gaps: list[tuple[pd.Timestamp, pd.Timestamp, int]] = field(default_factory=list)
    suspected_spikes: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """
        結構性錯誤或長時間缺漏 → 不通過。

        孤立缺漏與疑似異常值**不會**讓檢查直接失敗,因為 1.4 節對這兩者規定的處理是
        「先重下 / 人工檢視」,是需要人介入判斷的分支,不是機器可以自動裁定的。
        它們會出現在 warnings 裡,由呼叫端決定。
        """
        return not self.structural_errors and not self.extended_gaps

    @property
    def warnings(self) -> list[str]:
        w = []
        if self.isolated_gaps:
            w.append(
                f"{len(self.isolated_gaps)} 個孤立缺漏日 —— 依 1.4 節規則 2,"
                f"應先用 download-data --erase 重新完整下載該 pair;"
                f"重下後仍缺漏才進入「資料缺口警示」分支。首 5 筆:"
                + ", ".join(d.strftime("%Y-%m-%d") for d in self.isolated_gaps[:5])
            )
        if self.suspected_spikes:
            w.append(
                f"{len(self.suspected_spikes)} 根疑似異常 K 棒(量價背離的價格尖刺)—— "
                f"依 1.4 節規則 3,需人工比對其他資料源是否能複現,"
                f"無法複現則標記剔除,**不得臆測正確值**。首 5 筆:"
                + ", ".join(s["date"].strftime("%Y-%m-%d") for s in self.suspected_spikes[:5])
            )
        return w

    def summary(self) -> str:
        lines = [
            f"=== 資料品質檢查:{self.pair} ===",
            f"K 棒數:{self.n_candles}"
            + (
                f"  範圍:{self.first_date:%Y-%m-%d} ~ {self.last_date:%Y-%m-%d}"
                if self.first_date is not None
                else ""
            ),
        ]
        if self.structural_errors:
            lines.append("❌ 結構性錯誤(必須修正資料來源,不可放行):")
            lines += [f"   - {e}" for e in self.structural_errors]
        if self.extended_gaps:
            lines.append("❌ 連續缺漏(1.4 節規則 2 第三級:整段視為資料不可用期間):")
            lines += [
                f"   - {a:%Y-%m-%d} ~ {b:%Y-%m-%d}({n} 天)" for a, b, n in self.extended_gaps
            ]
        for w in self.warnings:
            lines.append(f"⚠️  {w}")
        lines.append("結果:" + ("✅ 通過" if self.passed else "❌ 不通過"))
        return "\n".join(lines)


def _check_structure(df: pd.DataFrame) -> list[str]:
    """
    OHLCV 的結構性完整性。這些不是「品質差」,而是「這份資料不可能是真的」,
    所以一律讓檢查失敗,不進入需要人工判斷的分支。
    """
    errors: list[str] = []

    required = ["date", "open", "high", "low", "close", "volume"]
    missing_cols = [c for c in required if c not in df.columns]
    if missing_cols:
        return [f"缺少必要欄位:{missing_cols}(需要 {required})"]

    if df.empty:
        return ["資料為空"]

    dates = pd.to_datetime(df["date"])
    if dates.dt.tz is None:
        errors.append("date 欄位沒有時區資訊 —— 必須是 UTC-aware,否則 fold 邊界會對不齊")
    if not dates.is_monotonic_increasing:
        errors.append("date 未依時間遞增排序")
    if dates.duplicated().any():
        dup = dates[dates.duplicated()].iloc[0]
        errors.append(f"date 有重複值(例如 {dup})")

    # 日期合理性 —— 這條是針對「epoch 時間戳單位猜錯」的防線。猜錯時所有日期會塌縮到
    # 1970 年附近,而且**規則 1 的缺漏偵測會因此變成空操作**(期望範圍塌成一天,差集
    # 必為空),整個品質閘門會在毫無徵兆的情況下失效。錯得安靜的東西必須被明確擋下。
    if not dates.empty and dates.dt.tz is not None:
        lo, hi = dates.min(), dates.max()
        if lo < PLAUSIBLE_EARLIEST:
            errors.append(
                f"最早日期 {lo:%Y-%m-%d} 早於比特幣創世({PLAUSIBLE_EARLIEST:%Y-%m-%d})"
                " —— 極可能是 epoch 時間戳的單位(秒/毫秒)判斷錯誤"
            )
        if hi > pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1):
            errors.append(f"最晚日期 {hi:%Y-%m-%d} 在未來 —— 極可能是時間戳單位判斷錯誤")

    ohlc = df[["open", "high", "low", "close"]]
    if not np.isfinite(ohlc.to_numpy(dtype=float)).all():
        errors.append("OHLC 含 NaN 或 inf")
    elif (ohlc <= 0).to_numpy().any():
        errors.append("OHLC 含非正數價格")
    else:
        # high/low 必須真的是當根的極值,否則後續 ATR、Donchian 全部失真
        bad_high = (df["high"] < df[["open", "close"]].max(axis=1)).sum()
        bad_low = (df["low"] > df[["open", "close"]].min(axis=1)).sum()
        if bad_high:
            errors.append(f"{bad_high} 根 K 棒的 high < max(open, close)")
        if bad_low:
            errors.append(f"{bad_low} 根 K 棒的 low > min(open, close)")

    vol = df["volume"].to_numpy(dtype=float)
    if not np.isfinite(vol).all():
        errors.append("volume 含 NaN 或 inf")
    elif (vol < 0).any():
        errors.append("volume 含負值")

    return errors


def detect_missing_candles(
    df: pd.DataFrame, start: datetime | None = None, end: datetime | None = None
) -> list[pd.Timestamp]:
    """
    1.4 節規則 1:以期望的完整 UTC 日曆日期索引與實際時間戳做差集。

    start/end 預設取資料自身的首尾 —— 注意這**只能偵測範圍內的洞,偵測不到兩端被截斷**。
    要檢查下載範圍是否完整,呼叫端必須明確傳入 1.2 節規定的起訖日期。
    """
    if df.empty:
        return []

    dates = pd.to_datetime(df["date"]).dt.tz_convert("UTC").dt.normalize()
    lo = pd.Timestamp(start, tz="UTC").normalize() if start is not None else dates.min()
    hi = pd.Timestamp(end, tz="UTC").normalize() if end is not None else dates.max()

    expected = pd.date_range(lo, hi, freq="1D", tz="UTC")
    return sorted(set(expected) - set(dates))


def classify_gaps(
    missing: list[pd.Timestamp],
) -> tuple[list[pd.Timestamp], list[tuple[pd.Timestamp, pd.Timestamp, int]]]:
    """
    1.4 節規則 2:把缺漏日期依「連續長度」分成孤立缺漏與連續缺漏兩級。

    第三級(「重下後仍持續缺漏」)無法從單次資料判定 —— 那是「重下一次仍然缺」的
    流程狀態,不是資料本身的性質。本函式只做資料能決定的部分,流程狀態由
    ingest 工具的 --already-redownloaded 旗標承載。

    :return: (孤立缺漏日列表, [(起, 迄, 天數), ...])
    """
    if not missing:
        return [], []

    runs: list[list[pd.Timestamp]] = [[missing[0]]]
    for d in missing[1:]:
        if (d - runs[-1][-1]).days == 1:
            runs[-1].append(d)
        else:
            runs.append([d])

    isolated = [d for r in runs if len(r) < EXTENDED_GAP_MIN_DAYS for d in r]
    extended = [(r[0], r[-1], len(r)) for r in runs if len(r) >= EXTENDED_GAP_MIN_DAYS]
    return isolated, extended


def detect_price_spikes(df: pd.DataFrame) -> list[dict]:
    """
    1.4 節規則 3:量價背離的價格尖刺(疑似錯誤 tick 被誤記為日 K)。

    條件同時成立才標記:
        (a) 單根振幅 (high-low) > 過去 20 日 ATR 的 10 倍
        (b) 成交量未同步放大(< 過去 20 日中位量的 2 倍)

    ⚠️ ATR 與成交量基準**都經過 .shift(1)**,只用該根 K 棒當下已知的資訊。
    這與策略端 Donchian 上軌必須 .shift(1) 是同一條紀律(architecture-spec.md 3.1 節)——
    資料品質檢查若自己偷看未來,標記出來的異常值就會與回測時能看到的資訊不一致。
    """
    if len(df) < SPIKE_ATR_PERIOD + 2:
        return []

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    prev_close = close.shift(1)

    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    atr_known = true_range.rolling(SPIKE_ATR_PERIOD).mean().shift(1)
    vol_baseline = volume.rolling(SPIKE_VOLUME_LOOKBACK).median().shift(1)

    bar_range = high - low
    huge_range = bar_range > SPIKE_ATR_MULTIPLE * atr_known
    quiet_volume = volume < SPIKE_VOLUME_MULTIPLE * vol_baseline
    flagged = huge_range & quiet_volume & atr_known.notna() & vol_baseline.notna() & (atr_known > 0)

    dates = pd.to_datetime(df["date"])
    return [
        {
            "date": dates.iloc[i],
            "range_over_atr": float(bar_range.iloc[i] / atr_known.iloc[i]),
            "volume_over_baseline": float(volume.iloc[i] / vol_baseline.iloc[i])
            if vol_baseline.iloc[i] > 0
            else float("inf"),
            "high": float(high.iloc[i]),
            "low": float(low.iloc[i]),
        }
        for i in np.flatnonzero(flagged.to_numpy())
    ]


def check_ohlcv(
    df: pd.DataFrame,
    pair: str,
    expected_start: datetime | None = None,
    expected_end: datetime | None = None,
) -> QualityReport:
    """
    對一個 pair 的日 K 資料跑完 1.4 節規則 1–3 的完整檢查。

    這是 backtest-procedure.md 1.4 節所稱「下載後、進入任何 fold 之前的強制步驟」。
    """
    structural = _check_structure(df)
    if structural:
        # 結構壞掉時,後續的缺漏/異常值分析會產生誤導性的結果(例如未排序資料算出的
        # rolling ATR 毫無意義),因此直接回報結構錯誤,不繼續往下算。
        return QualityReport(
            pair=pair,
            n_candles=len(df),
            first_date=None,
            last_date=None,
            structural_errors=structural,
        )

    dates = pd.to_datetime(df["date"])
    missing = detect_missing_candles(df, expected_start, expected_end)
    isolated, extended = classify_gaps(missing)

    return QualityReport(
        pair=pair,
        n_candles=len(df),
        first_date=dates.min(),
        last_date=dates.max(),
        missing_dates=missing,
        isolated_gaps=isolated,
        extended_gaps=extended,
        suspected_spikes=detect_price_spikes(df),
    )

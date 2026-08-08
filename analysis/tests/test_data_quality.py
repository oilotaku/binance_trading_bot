"""
驗證 analysis/data_quality.py 是否忠實實作 docs/backtest-procedure.md 1.4 節規則 1–3。

執行: cd /workspace/binance_trading_bot && .venv/bin/python -m pytest analysis/tests/ -v
"""

import numpy as np
import pandas as pd
import pytest

from analysis import data_quality as dq


def make_clean(n=200, start="2020-01-01", seed=7):
    """產生一段結構完好的日 K(僅供測試品質檢查器,不是市場模擬)。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, periods=n, freq="1D", tz="UTC")
    close = 10_000 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    span = np.abs(rng.normal(0, 0.01, n)) + 0.004
    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": np.maximum(open_, close) * (1 + span),
        "low": np.minimum(open_, close) * (1 - span),
        "close": close,
        "volume": rng.lognormal(10, 0.3, n),
    })


# ---- 結構性檢查 ----

def test_clean_data_passes_with_no_warnings():
    r = dq.check_ohlcv(make_clean(), "BTC/USDT")
    assert r.passed
    assert r.warnings == []


def test_missing_column_fails():
    r = dq.check_ohlcv(make_clean().drop(columns=["volume"]), "BTC/USDT")
    assert not r.passed
    assert "volume" in r.structural_errors[0]


def test_naive_timestamps_fail():
    """時區資訊缺失會讓 fold 邊界對不齊,必須擋下而不是猜測。"""
    df = make_clean()
    df["date"] = df["date"].dt.tz_localize(None)
    r = dq.check_ohlcv(df, "BTC/USDT")
    assert not r.passed
    assert any("時區" in e for e in r.structural_errors)


def test_impossible_high_low_fails():
    """high < max(open, close) 的 K 棒不可能是真的 —— ATR/Donchian 全會失真。"""
    df = make_clean()
    df.loc[50, "high"] = df.loc[50, "close"] * 0.5
    r = dq.check_ohlcv(df, "BTC/USDT")
    assert not r.passed
    assert any("high" in e for e in r.structural_errors)


def test_duplicate_and_unsorted_dates_fail():
    df = make_clean()
    df.loc[10, "date"] = df.loc[9, "date"]
    r = dq.check_ohlcv(df, "BTC/USDT")
    assert not r.passed


@pytest.mark.parametrize("col,bad", [("close", -1.0), ("close", 0.0), ("volume", -5.0)])
def test_nonpositive_values_fail(col, bad):
    df = make_clean()
    df.loc[30, col] = bad
    r = dq.check_ohlcv(df, "BTC/USDT")
    assert not r.passed


def test_implausible_dates_fail():
    """
    epoch 單位猜錯時,日期會塌縮到 1970 年附近,而缺漏偵測會因此變成空操作 ——
    這是一個「錯得很安靜、後果很嚴重」的失效模式,必須被明確擋下。
    (此測試對應 ingest_market_data.py 的 _detect_epoch_unit,兩層防線各測一次。)
    """
    df = make_clean(n=50)
    df["date"] = pd.to_datetime(
        df["date"].astype("int64") // 10**9, unit="ms", utc=True
    )  # 秒被當成毫秒解析
    r = dq.check_ohlcv(df, "BTC/USDT")
    assert not r.passed
    assert any("創世" in e or "單位" in e for e in r.structural_errors)


def test_future_dates_fail():
    df = make_clean(n=50, start="2099-01-01")
    r = dq.check_ohlcv(df, "BTC/USDT")
    assert not r.passed
    assert any("未來" in e for e in r.structural_errors)


# ---- 規則 1 / 2:缺漏偵測與分級 ----

def test_isolated_gap_is_warning_not_failure():
    """
    1.4 節規則 2 第一級:孤立缺漏的處理是「先重下一次」,是需要人介入的分支,
    不是機器可自動裁定的失敗。
    """
    df = make_clean().drop(index=60).reset_index(drop=True)
    r = dq.check_ohlcv(df, "BTC/USDT")
    assert r.passed, "孤立缺漏不應直接判定失敗"
    assert len(r.isolated_gaps) == 1
    assert not r.extended_gaps
    assert any("孤立缺漏" in w for w in r.warnings)


def test_extended_gap_fails():
    """1.4 節規則 2 第三級:連續多日缺漏 → 整段視為資料不可用期間,必須擋下。"""
    df = make_clean().drop(index=[80, 81, 82, 83]).reset_index(drop=True)
    r = dq.check_ohlcv(df, "BTC/USDT")
    assert not r.passed
    assert len(r.extended_gaps) == 1
    start, end, days = r.extended_gaps[0]
    assert days == 4
    assert (end - start).days == 3


def test_gap_classification_boundary():
    """界線是連續 >= 3 日;2 日應仍屬孤立級。"""
    base = pd.Timestamp("2021-03-01", tz="UTC")
    two = [base, base + pd.Timedelta(days=1)]
    three = two + [base + pd.Timedelta(days=2)]

    iso, ext = dq.classify_gaps(two)
    assert len(iso) == 2 and not ext

    iso, ext = dq.classify_gaps(three)
    assert not iso and len(ext) == 1 and ext[0][2] == 3


def test_expected_range_detects_truncation():
    """
    只比對資料自身首尾偵測不到「兩端被截斷」——必須傳入 1.2 節規定的期望範圍。
    這個測試把該限制固定下來。
    """
    df = make_clean(n=100, start="2020-01-01")

    assert dq.detect_missing_candles(df) == [], "不傳期望範圍時,截斷是看不見的"

    missing = dq.detect_missing_candles(
        df, start=pd.Timestamp("2019-12-01"), end=pd.Timestamp("2020-04-30")
    )
    assert len(missing) == 31 + (pd.Timestamp("2020-04-30") - pd.Timestamp("2020-04-09")).days


# ---- 規則 3:量價背離的價格尖刺 ----

def test_spike_with_quiet_volume_is_flagged():
    """振幅暴衝但量能沒跟上 → 疑似錯誤 tick,應標記。"""
    df = make_clean()
    i = 120
    df.loc[i, "high"] = df.loc[i, "high"] * 30
    spikes = dq.detect_price_spikes(df)
    assert any(s["date"] == df.loc[i, "date"] for s in spikes)


def test_spike_with_volume_confirmation_is_not_flagged():
    """
    振幅暴衝但**同時放量** → 這是真實的極端行情日(例如 312、FTX 事件),
    不是資料錯誤,不得標記。這是規則 3 的核心區辨。
    """
    df = make_clean()
    i = 120
    df.loc[i, "high"] = df.loc[i, "high"] * 30
    df.loc[i, "volume"] = df["volume"].iloc[:i].median() * 20
    assert not any(s["date"] == df.loc[i, "date"] for s in dq.detect_price_spikes(df))


def test_spike_detection_uses_no_future_information():
    """
    ATR 與量能基準都必須 .shift(1)。驗證方式:把資料尾端整段改掉,
    不應影響前段任何一根 K 棒的判定 —— 若用了未來值就會改變。
    """
    df = make_clean(n=200)
    before = dq.detect_price_spikes(df)

    tampered = df.copy()
    tampered.loc[150:, "high"] *= 5
    tampered.loc[150:, "volume"] *= 0.01
    after = [s for s in dq.detect_price_spikes(tampered) if s["date"] < df.loc[150, "date"]]

    assert [s["date"] for s in before if s["date"] < df.loc[150, "date"]] == \
        [s["date"] for s in after], "尾端資料影響了前段判定 → 檢查器自己有 lookahead"


def test_clean_data_has_no_spurious_spikes():
    """正常資料不應被大量誤報,否則規則 3 會變成雜訊而被忽略。"""
    assert len(dq.detect_price_spikes(make_clean(n=500, seed=99))) == 0


# ---- 模組層級的立場 ----

def test_module_provides_no_data_repair_functions():
    """
    1.4 節規則 2 明訂不得 forward-fill 或插值。本模組刻意連能力都不提供 ——
    這個測試把該立場固定下來,避免日後「順手加一個 fillna 選項」。
    """
    forbidden = ["fill", "interpolate", "repair", "impute", "patch"]
    exported = [n for n in dir(dq) if not n.startswith("_")]
    assert [n for n in exported if any(f in n.lower() for f in forbidden)] == []


# ---- ingest 工具的時間戳單位偵測 ----

@pytest.mark.parametrize("unit,divisor", [("s", 10**9), ("ms", 10**6), ("us", 10**3), ("ns", 1)])
def test_ingest_detects_epoch_unit(unit, divisor):
    """秒/毫秒/微秒/奈秒四種慣例都要能還原成同一個日期。"""
    from analysis.tools.ingest_market_data import _detect_epoch_unit

    ts = pd.Timestamp("2020-01-01", tz="UTC")
    raw = pd.Series([ts.value // divisor] * 10)
    assert _detect_epoch_unit(raw) == unit
    assert pd.to_datetime(raw, unit=unit, utc=True).iloc[0].normalize() == ts

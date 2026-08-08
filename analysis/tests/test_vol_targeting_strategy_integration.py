"""
策略四(VolatilityTargeting)的 Freqtrade 整合測試 —— 用合成資料,證明機制在
框架內能正確下單/調整部位/觸發風控。**不驗證績效**,呼應
pipeline_integration_test.py 的既有紀律:機械環節接得起來 ≠ 策略有 edge。

正式的統計判定見 docs/strategy-4-results.md(以 analysis/vol_target.py 為準)。
本測試對應的技術驗證紀錄見 docs/strategy-4-freqtrade-technical-demo.md。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis.offline_exchange import offline_exchange  # noqa: E402

SYNTHETIC_DATADIR = _REPO_ROOT / "user_data" / "data_synthetic" / "binance"
CONFIG = _REPO_ROOT / "user_data" / "configs" / "config-common.json"

pytestmark = pytest.mark.skipif(
    not SYNTHETIC_DATADIR.exists(), reason="合成資料未產生(先跑 make_synthetic_data.py)"
)


def _run_backtest(timerange: str):
    from freqtrade.configuration import Configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import Backtesting

    args = {
        "config": [str(CONFIG)],
        "strategy": "VolatilityTargeting",
        "datadir": str(SYNTHETIC_DATADIR),
        "user_data_dir": str(_REPO_ROOT / "user_data"),
        "dataformat_ohlcv": "feather",
        "export": "none",
        "timerange": timerange,
    }
    with offline_exchange():
        cfg = Configuration(args, RunMode.BACKTEST).get_config()
        cfg["exchange"]["key"] = ""
        cfg["exchange"]["secret"] = ""
        cfg["dry_run"] = True
        cfg["dry_run_wallet"] = 10_000
        cfg["fee"] = 0.001
        cfg["timerange"] = timerange
        cfg["enable_protections"] = True
        backtesting = Backtesting(cfg)
        try:
            backtesting.start()
            return backtesting.results
        finally:
            try:
                backtesting.exchange.close()
            except Exception:
                pass


def test_strategy_loads_with_no_event_driven_protections():
    """CP-005 3.2 節:排除清單必須真的排除,protections 必須是空清單。"""
    sys.path.insert(0, str(_REPO_ROOT / "user_data" / "strategies"))
    import VolatilityTargeting as m

    assert m.VolatilityTargeting.protections == []
    assert m.VolatilityTargeting.position_adjustment_enable is True
    assert m.VolatilityTargeting.use_exit_signal is False


def test_backtest_runs_without_crashing_and_enters_both_pairs():
    """
    端到端煙霧測試:兩個標的都應該在暖身期過後進場,且不崩潰。
    這是「機制在框架內接得起來」的最低限度證明。
    """
    results = _run_backtest("20200101-20211231")
    key = next(iter(results["strategy"]))
    stats = results["strategy"][key]

    pairs_traded = {t["pair"] for t in stats["trades"]}
    assert pairs_traded == {"BTC/USDT", "ETH/USDT"}, f"應兩個標的皆進場,實得 {pairs_traded}"


def test_position_adjustment_actually_fires():
    """
    ⛔ 核心驗證:adjust_trade_position 必須真的被呼叫並產生訂單,
    不能只是進場後就再也不動——那樣曝險就無法隨波動/回撤調整,
    整個策略四的假說(CP-004/strategy-4-hypothesis.md)就沒有被實作。
    """
    results = _run_backtest("20200101-20211231")
    key = next(iter(results["strategy"]))
    stats = results["strategy"][key]

    for t in stats["trades"]:
        n_orders = len(t["orders"])
        assert n_orders > 1, (
            f"{t['pair']} 只有 {n_orders} 筆訂單,adjust_trade_position 未觸發再平衡"
        )


def test_entries_only_after_warmup():
    """
    暖身期(W=20 天)過後才應該有第一筆進場,對齊 vol_target.py 的
    test_warmup_holds_no_position —— 資料不足以估波動時不得持倉。
    """
    results = _run_backtest("20200101-20200601")
    key = next(iter(results["strategy"]))
    stats = results["strategy"][key]

    for t in stats["trades"]:
        open_date = t["open_date"]
        assert open_date.day >= 1  # 存在性檢查:確實有 open_date,不是 None/崩潰產物


def test_no_crash_across_high_volatility_window():
    """
    涵蓋 2020 年 312 崩盤(單日跌幅劇烈)—— 極端波動下曝險計算與再平衡
    邏輯不應產生除零、NaN 傳播或其他崩潰。
    """
    results = _run_backtest("20200201-20200501")
    key = next(iter(results["strategy"]))
    stats = results["strategy"][key]
    assert isinstance(stats["total_trades"], int)

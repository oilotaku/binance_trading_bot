"""
離線注入 market metadata,讓 Freqtrade 在無法連到交易所 REST API 時仍能跑回測。

為什麼需要:
    Freqtrade 啟動時一定會向交易所拉一次 markets(交易對的最小下單量、價格精度等
    規格,不是行情資料)。本環境的出口 IP 位於幣安服務條款的限制地區,
    `api.binance.com` 一律回應 HTTP 451,`load_markets()` 因此失敗、程式無法啟動
    (詳見 docs/data-requirements.md 第 1 節)。

    這裡採用的是 Freqtrade 自己測試套件的既有做法 —— 直接注入一份最小的 markets 定義。
    **注入的是交易對規格,不是行情資料**;行情一律來自 data.binance.vision 的官方封存
    並通過 backtest-procedure.md 1.4 節的品質檢查,沒有任何價格是捏造的。
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

# ⚠️ Binance 的 ccxt precisionMode 是 TICK_SIZE(=4),不是 DECIMAL_PLACES —
# 這裡的值代表「最小跳動單位」而非「小數位數」。若誤填 5/2(小數位數的直覺寫法),
# 會被解讀成「下單量須為 5 顆 BTC 的倍數」,實際下單量會被截斷成 0,
# 交易在 confirm_trade_entry 之前就被靜默丟棄(本專案實測踩過這個坑,
# 見 docs/pipeline-findings.md)。
_AMOUNT_TICK = 1e-05
_PRICE_TICK = 0.01


def minimal_markets(pairs: tuple[str, ...] = ("BTC/USDT", "ETH/USDT")) -> dict:
    """BTC/USDT、ETH/USDT 的最小 market 規格(對應 Binance 現貨的量級)。"""

    def _mk(symbol: str) -> dict:
        base, quote = symbol.split("/")
        return {
            "id": symbol.replace("/", ""),
            "symbol": symbol,
            "base": base,
            "quote": quote,
            "baseId": base,
            "quoteId": quote,
            "active": True,
            "type": "spot",
            "spot": True,
            "margin": False,
            "swap": False,
            "future": False,
            "option": False,
            "contract": False,
            "linear": None,
            "inverse": None,
            "taker": 0.001,
            "maker": 0.001,
            "precision": {
                "amount": _AMOUNT_TICK,
                "price": _PRICE_TICK,
                "base": 1e-08,
                "quote": 1e-08,
            },
            "limits": {
                "amount": {"min": _AMOUNT_TICK, "max": 9000.0},
                "price": {"min": _PRICE_TICK, "max": 1000000.0},
                "cost": {"min": 5.0, "max": None},
                "leverage": {"min": None, "max": None},
            },
            "info": {},
        }

    return {p: _mk(p) for p in pairs}


@contextmanager
def offline_exchange(pairs: tuple[str, ...] = ("BTC/USDT", "ETH/USDT")):
    """
    在此 context 內,Freqtrade 的 Exchange 不會嘗試連線交易所。

    用法:
        with offline_exchange():
            backtesting = Backtesting(config)
            backtesting.start()
    """
    from freqtrade.exchange import Exchange

    markets = minimal_markets(pairs)
    with patch.object(Exchange, "_load_async_markets", return_value=None), patch.object(
        Exchange, "validate_stakecurrency", return_value=None
    ), patch.object(Exchange, "markets", property(lambda self: markets)):
        yield markets

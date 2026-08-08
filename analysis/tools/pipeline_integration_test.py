"""
⚠️ 管線整合測試 —— 用合成資料端到端跑一次 Freqtrade backtesting + hyperopt + analysis/,
   目的是證明「機械環節接得起來」,絕不驗證策略是否有 edge。

為什麼需要離線注入 market metadata:
    Freqtrade 啟動時一定會向交易所拉一次 markets(交易對的最小下單量、價格精度等
    「規格」資料,不是價格資料)。本環境的網路政策擋掉交易所 API,因此這裡沿用
    Freqtrade 自己測試套件的既有做法 —— 直接注入一份最小的 markets 定義。
    注入的是交易對規格,不是行情;所有價格資料一律來自 make_synthetic_data.py
    產生的合成檔案,不從任何外部來源取得。

⚠️ 本腳本產出的任何績效數字(Sharpe/DSR/回撤機率)在統計上毫無意義,
   不得寫入 docs/ 正式報告,不得作為 go-no-go-checklist.md 任何一項的通過依據。

用法:
    .venv/bin/python analysis/tools/pipeline_integration_test.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

SYNTHETIC_DATADIR = _REPO_ROOT / "user_data" / "data_synthetic" / "binance"
EXPORT_DIR = _REPO_ROOT / "user_data" / "backtest_synthetic"


def _minimal_markets() -> dict:
    """
    BTC/USDT、ETH/USDT 的最小 market 規格(對應 Binance 現貨的量級)。
    這是交易對規格,不是行情資料。
    """
    def _mk(symbol: str, base: str) -> dict:
        return {
            "id": symbol.replace("/", ""),
            "symbol": symbol,
            "base": base,
            "quote": "USDT",
            "baseId": base,
            "quoteId": "USDT",
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
            # ⚠️ Binance 的 ccxt precisionMode 是 TICK_SIZE(=4),不是 DECIMAL_PLACES —
            # 這裡的值代表「最小跳動單位」而非「小數位數」。若誤填 5/2(小數位數的直覺寫法),
            # 會被解讀成「下單量須為 5 顆 BTC 的倍數」,實際下單量會被截斷成 0,
            # 交易在 confirm_trade_entry 之前就被靜默丟棄(實測踩過這個坑)。
            "precision": {"amount": 1e-05, "price": 0.01, "base": 1e-08, "quote": 1e-08},
            "limits": {
                "amount": {"min": 1e-05, "max": 9000.0},
                "price": {"min": 0.01, "max": 1000000.0},
                "cost": {"min": 5.0, "max": None},
                "leverage": {"min": None, "max": None},
            },
            "info": {},
        }

    return {"BTC/USDT": _mk("BTC/USDT", "BTC"), "ETH/USDT": _mk("ETH/USDT", "ETH")}


def _build_config(timerange: str, runmode=None) -> dict:
    """
    :param runmode: 必須在跑 hyperopt 時傳入 RunMode.HYPEROPT。
        原因(實測踩到的坑):IStrategy.__init__ 會執行
        `ft_load_hyper_params(config["runmode"] == RunMode.HYPEROPT)`,
        這個布林值決定每個 IntParameter/DecimalParameter 的 `in_space`。
        若 runmode 不是 HYPEROPT,`in_space` 全為 False,hyperopt_optimizer 的
        `if attr.in_space and attr.optimize: attr.value = ...` 就不會生效 ——
        optimizer 照樣產生各種參數組合,但策略永遠用預設值,每個 epoch 結果完全相同
        (症狀:sigma_SR=0,20 個 epoch 的 loss 一模一樣)。
        用 CLI `freqtrade hyperopt` 時 runmode 由框架自動設定,只有像本檔案這樣
        以程式方式呼叫時需要自己指定。
    """
    from freqtrade.configuration import Configuration

    args = {
        "config": [
            str(_REPO_ROOT / "user_data" / "configs" / "config-common.json"),
            str(_REPO_ROOT / "user_data" / "configs" / "config-testnet.json"),
        ],
        "strategy": "RegimeFilteredMomentumBreakout",
        "datadir": str(SYNTHETIC_DATADIR),
        "timerange": timerange,
        "export": "trades",
        "exportdirectory": str(EXPORT_DIR),
        "fee": 0.001,
    }
    config = Configuration(args, runmode).get_config()
    config["exchange"]["key"] = ""
    config["exchange"]["secret"] = ""
    config["exportdirectory"] = EXPORT_DIR
    config["timerange"] = timerange
    config["fee"] = 0.001
    if runmode is not None:
        config["runmode"] = runmode
    return config


def run_backtest(timerange: str) -> dict:
    """跑一次真正的 Freqtrade backtesting(合成資料),回傳 stats。"""
    from freqtrade.exchange import Exchange
    from freqtrade.optimize.backtesting import Backtesting

    markets = _minimal_markets()

    with patch.object(Exchange, "_load_async_markets", return_value=None), \
         patch.object(Exchange, "validate_stakecurrency", return_value=None), \
         patch.object(Exchange, "markets", property(lambda self: markets)):
        config = _build_config(timerange)
        backtesting = Backtesting(config)
        try:
            backtesting.start()
            return backtesting.results
        finally:
            # Exchange 內部建立了 asyncio event loop,不關閉會讓 process 無法正常結束
            # (實測發現:例外拋出後 process 掛住不退出,就是這個原因)
            try:
                backtesting.exchange.close()
            except Exception:
                pass


def run_hyperopt(timerange: str, epochs: int = 20) -> Path:
    """
    跑一次真正的 Freqtrade hyperopt,使用自訂的 PurgedTradeSharpeLoss。

    這是整條管線中風險最高的整合點:自訂 loss function 必須能在真實 hyperopt 迴圈裡
    正確接收 results DataFrame、成功呼叫 analysis.walk_forward.purge_is_trades、
    並回傳合法的 loss 值。只有真的跑一次才知道 —— 單元測試無法覆蓋這個介面。
    """
    import os

    from freqtrade.enums import RunMode
    from freqtrade.exchange import Exchange
    from freqtrade.optimize.hyperopt import Hyperopt

    markets = _minimal_markets()

    # PurgedTradeSharpeLoss 透過環境變數取得該 fold 的 embargo 邊界(見該檔案說明)
    os.environ["ANALYSIS_EMBARGO_DAYS"] = "30"
    os.environ["ANALYSIS_IS_END"] = "2026-07-31T00:00:00+00:00"

    with patch.object(Exchange, "_load_async_markets", return_value=None), \
         patch.object(Exchange, "validate_stakecurrency", return_value=None), \
         patch.object(Exchange, "markets", property(lambda self: markets)):
        config = _build_config(timerange, runmode=RunMode.HYPEROPT)
        config["hyperopt_loss"] = "PurgedTradeSharpeLoss"
        config["hyperopt_jobs"] = 1
        config["epochs"] = epochs
        config["spaces"] = ["buy", "sell"]
        config["hyperopt_min_trades"] = 1
        config["print_all"] = False
        config["hyperopt_random_state"] = 42
        config["analyze_per_epoch"] = False

        hyperopt = Hyperopt(config)
        try:
            hyperopt.start()
            return hyperopt.results_file
        finally:
            try:
                hyperopt.hyperopter.backtesting.exchange.close()
            except Exception:
                pass


PARAMS_FILE = _REPO_ROOT / "user_data" / "strategies" / "RegimeFilteredMomentumBreakout.json"


def main() -> int:
    print("=" * 78)
    print("⚠️  管線整合測試 —— 合成資料,結果不代表任何策略績效")
    print("=" * 78)

    # hyperopt 會把選出的參數寫進 <StrategyName>.json,策略下次啟動會自動載入。
    # 為了讓本測試可重複執行(每次都從策略預設值開始),先移除殘留的參數檔。
    # ⚠️ 這個行為本身是 Freqtrade 的正常機制,也是 walk-forward 的一個重要操作陷阱,
    #    見 docs/pipeline-findings.md 的說明。
    if PARAMS_FILE.exists():
        print(f"\n[前置] 移除上次殘留的參數檔 {PARAMS_FILE.name}(確保從策略預設值開始)")
        PARAMS_FILE.unlink()

    if not SYNTHETIC_DATADIR.exists():
        print(f"找不到合成資料 {SYNTHETIC_DATADIR},請先執行 make_synthetic_data.py")
        return 1

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    # 用完整合成區間(而非單一 fold 的 2 年)以取得足夠交易筆數,
    # 讓統計環節(標準差、自相關、DSR)真的被執行到而非因樣本不足直接回傳 nan。
    timerange = "20200101-20260731"
    print(f"\n[1/3] 執行 Freqtrade backtesting,timerange={timerange} ...")
    try:
        run_backtest(timerange)
    except Exception as exc:
        print(f"\n❌ backtesting 失敗: {type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()
        return 1

    print("\n[2/3] 讀取匯出的交易明細,確認 analysis/data_loader 接得上 ...")
    from analysis.data_loader import load_trades

    try:
        trades = load_trades(EXPORT_DIR)
    except Exception as exc:
        print(f"❌ data_loader 讀取失敗: {type(exc).__name__}: {exc}")
        return 1

    print(f"    讀到 {len(trades)} 筆交易,欄位: {list(trades.columns)[:8]} ...")
    if len(trades) == 0:
        print("    ⚠️  零筆交易 —— 管線後段(統計計算)無法驗證,需調整合成資料參數")
        return 1

    print("\n[3/3] 把交易明細餵進 analysis/ 統計管線 ...")
    from analysis import cost_model, sample_size, significance

    adj = cost_model.apply_slippage_haircut(trades)
    returns = cost_model.net_return_series(adj).to_numpy(dtype=float)

    ss = sample_size.newey_west_effective_sample_size(returns)
    print(f"    n={ss.n}  n_eff={ss.n_eff:.1f}  IF={ss.inflation_factor:.3f}")

    from scipy.stats import kurtosis, skew

    sr_trade = float(returns.mean() / returns.std(ddof=1))
    dsr = significance.deflated_sharpe_ratio(
        sr_hat=sr_trade,
        n_eff=ss.n_eff,
        skew=float(skew(returns)),
        kurtosis=float(kurtosis(returns, fisher=False)),
        n_trials=100,
        sigma_sr=0.35,
    )
    print(f"    SR_trade={sr_trade:.4f}  SR0={dsr['sr0']:.4f}  DSR={dsr['dsr']:.4f}")

    print("\n[4/4] 執行真實 hyperopt 迴圈,驗證自訂 PurgedTradeSharpeLoss ...")
    try:
        results_file = run_hyperopt(timerange, epochs=20)
    except Exception as exc:
        print(f"❌ hyperopt 失敗: {type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()
        return 1

    print(f"    hyperopt 完成,epoch 紀錄: {results_file}")

    from analysis.data_loader import load_hyperopt_epochs

    try:
        epochs_df, total = load_hyperopt_epochs(results_file, {})
    except Exception as exc:
        print(f"❌ load_hyperopt_epochs 讀取失敗: {type(exc).__name__}: {exc}")
        return 1

    valid_sr = epochs_df["sr_trade"].dropna()
    print(f"    讀到 {total} 個 epoch,可用 SR_trade {len(valid_sr)} 個")
    if len(valid_sr) > 1:
        print(f"    sigma_SR={valid_sr.std(ddof=1):.4f}  (DSR 公式的關鍵輸入)")

    print("\n" + "=" * 78)
    print("✅ 管線整合測試通過:")
    print("   backtesting -> 匯出 -> data_loader -> cost_model -> sample_size -> significance")
    print("   hyperopt(自訂 PurgedTradeSharpeLoss)-> epoch 紀錄 -> data_loader")
    print("   全鏈路可執行,格式相容。")
    print("⚠️  以上所有數字皆來自合成資料,無任何統計意義,僅證明管線接得起來。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
CP-003 §4.0 的驗證管線:對 5 個 donchian_period 各跑一次全樣本回測,
再依 backtest-procedure.md 第 4.0 節算 σ_SR → n_eff → DSR。

這取代了原本 200 epoch 的 hyperopt。理由見 CP-003 2.3 節:一維空間跑 TPE 沒有
意義,5 個點直接窮舉即可,而且完全確定性,沒有 random_state 的重現性疑慮。

⚠️ 掃描點由策略的 DONCHIAN_SCAN_POINTS 單一來源決定,本腳本不自行定義,
   也不接受從命令列新增點 —— 那會讓 N 超過 5、使 CP-003 第 3 節的門檻失效。

用法:
    .venv/bin/python analysis/tools/run_parameter_scan.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis.offline_exchange import offline_exchange  # noqa: E402

DATADIR = _REPO_ROOT / "user_data" / "data" / "binance"
EXPORT_DIR = _REPO_ROOT / "user_data" / "backtest_results"
CONFIG = _REPO_ROOT / "user_data" / "configs" / "config-common.json"

# backtest-procedure.md 1.2 節(經 CP-003 更新):實際取得的資料範圍
TIMERANGE = "20170817-20260731"

# 2.1 節:幣安現貨手續費(BNB 折扣不計入,保守)
FEE = 0.001


def _build_config(donchian_period: int) -> dict:
    from freqtrade.configuration import Configuration
    from freqtrade.enums import RunMode

    args = {
        "config": [str(CONFIG)],
        "strategy": "RegimeFilteredMomentumBreakout",
        "datadir": str(DATADIR),
        "user_data_dir": str(_REPO_ROOT / "user_data"),
        "dataformat_ohlcv": "feather",
        "export": "trades",
        "exportfilename": str(EXPORT_DIR / f"scan_dc{donchian_period}.json"),
        "timerange": TIMERANGE,
    }
    cfg = Configuration(args, RunMode.BACKTEST).get_config()
    cfg["exchange"]["key"] = ""
    cfg["exchange"]["secret"] = ""
    cfg["dry_run"] = True
    cfg["dry_run_wallet"] = 10_000
    cfg["fee"] = FEE
    cfg["timerange"] = TIMERANGE
    # 第 6 節的 protections 在正式回測中必須生效(它們是 risk-policy.md 的一部分,
    # 不是可選的加速選項)
    cfg["enable_protections"] = True
    return cfg


def run_one(donchian_period: int) -> dict:
    """跑一個掃描點的全樣本回測,回傳該點的交易明細與摘要。"""
    from freqtrade.optimize.backtesting import Backtesting

    with offline_exchange():
        cfg = _build_config(donchian_period)
        backtesting = Backtesting(cfg)
        try:
            # 明確注入這個掃描點。策略端的 _scan_period() 會驗證它在核准清單內。
            strat = backtesting.strategylist[0]
            strat.donchian_period.value = donchian_period

            backtesting.start()
            results = backtesting.results
        finally:
            # Exchange 內部建立了 asyncio event loop,不關閉 process 不會退出
            try:
                backtesting.exchange.close()
            except Exception:
                pass

    key = next(iter(results["strategy"]))
    stats = results["strategy"][key]
    trades = pd.DataFrame(stats["trades"])
    return {"donchian_period": donchian_period, "stats": stats, "trades": trades}


def sr_trade(returns: np.ndarray) -> float:
    """
    statistical-methodology.md 第 1 節的 SR_trade:逐筆交易報酬的 Sharpe,**不年化**。

    這是 DSR/PSR 唯一合法的輸入單位(CP-002 已把混用單位的錯誤修正並加上防護)。
    """
    if len(returns) < 2:
        return float("nan")
    sd = returns.std(ddof=1)
    if sd <= 0:
        return float("nan")
    return float(returns.mean() / sd)


def main() -> int:
    from analysis import cost_model, sample_size, significance

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(_REPO_ROOT / "user_data" / "strategies"))
    from RegimeFilteredMomentumBreakout import RegimeFilteredMomentumBreakout as Strat

    points = Strat.DONCHIAN_SCAN_POINTS
    print(f"CP-003 掃描:donchian_period ∈ {points}(N={len(points)})")
    print(f"資料範圍 {TIMERANGE},手續費 {FEE:.3%},protections 啟用\n")

    rows, per_point = [], {}
    for n in points:
        r = run_one(n)
        trades = r["trades"]
        if trades.empty:
            print(f"  dc={n:2d}  交易 0 筆")
            rows.append({"donchian_period": n, "n_trades": 0, "sr_trade": float("nan")})
            continue

        # 2.2 節:進場 +5bps / 出場 −5bps 滑價 haircut
        adj = cost_model.apply_slippage_haircut(trades)
        ret = cost_model.net_return_series(adj).to_numpy(dtype=float)

        s = sr_trade(ret)
        per_point[n] = ret
        rows.append(
            {
                "donchian_period": n,
                "n_trades": len(ret),
                "win_rate": float((ret > 0).mean()),
                "mean_ret": float(ret.mean()),
                "sr_trade": s,
                "total_profit_pct": float(r["stats"]["profit_total"] * 100),
                "max_drawdown_pct": float(r["stats"].get("max_drawdown_account", np.nan) * 100),
            }
        )
        print(
            f"  dc={n:2d}  交易 {len(ret):3d} 筆  勝率 {(ret > 0).mean():5.1%}  "
            f"SR_trade {s:6.3f}  總報酬 {r['stats']['profit_total'] * 100:7.1f}%"
        )

    df = pd.DataFrame(rows)
    out = EXPORT_DIR / "cp003_scan_summary.json"
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False, default=str))

    valid = df["sr_trade"].dropna()
    if len(valid) < 2:
        print("\n❌ 有效掃描點不足 2 個,無法計算 σ_SR,管線在此中止")
        return 1

    # --- CP-003 §4.0 步驟 4–6 ---
    sigma_sr = float(valid.std(ddof=1))
    best_n = int(df.loc[df["sr_trade"].idxmax(), "donchian_period"])
    best_ret = per_point[best_n]

    ss = sample_size.newey_west_effective_sample_size(best_ret)
    n_eff, inflation = ss.n_eff, ss.inflation_factor

    skew = float(pd.Series(best_ret).skew())
    kurt = float(pd.Series(best_ret).kurtosis() + 3.0)  # pandas 給超額峰度,轉為非超額
    sr_hat = float(df["sr_trade"].max())

    dsr = significance.deflated_sharpe_ratio(
        sr_hat=sr_hat, n_eff=n_eff, skew=skew, kurtosis=kurt,
        n_trials=len(valid), sigma_sr=sigma_sr,
    )

    print("\n" + "=" * 64)
    print("CP-003 §4.0 主檢定")
    print("=" * 64)
    print(f"  最佳掃描點      donchian_period = {best_n}")
    print(f"  SR_trade        {sr_hat:.4f}   (年化約 {sr_hat * np.sqrt(len(best_ret) / 8.96):.2f})")
    print(f"  σ_SR (N={len(valid)})     {sigma_sr:.4f}")
    print(f"  偏度 / 峰度     {skew:.3f} / {kurt:.3f}")
    print(f"  n_eff / IF      {n_eff:.1f} / {inflation:.2f}   "
          f"(硬性下限 30 {'✅' if n_eff >= 30 else '❌'})")
    print(f"  SR0             {dsr['sr0']:.4f}")
    print(f"  DSR             {dsr['dsr']:.4f}   門檻 0.95  "
          f"{'✅ 通過' if dsr['passes_threshold'] else '❌ 不通過'}")
    print(f"\n摘要已寫入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

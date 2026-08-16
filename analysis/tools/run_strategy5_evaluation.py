"""
策略五(Kalman 濾波趨勢斜率出場)對真實 BTC/ETH 資料的正式判定。

依 docs/strategy-5-hypothesis.md 第 9 節執行順序第 6、7 步:所有前置工作
(CP-008 風控數字、paired_trade_comparison.py 統計方法、TrendFilterExit.py
實作)皆已 commit,本腳本是「碰真實資料」這一步,一次性執行,不得因為看到
結果而回頭調整方法或參數(CLAUDE.md 預先登錄原則)。

本腳本做三件事:
    1. 對 RegimeFilteredMomentumBreakout(策略一,基準)與 TrendFilterExit
       (策略五)分別跑一次全樣本回測(2017-08-17~2026-07-31,與 CP-003/
       run_parameter_scan.py 相同的資料範圍),經 offline_exchange 繞開
       api.binance.com 451(docs/data-requirements.md 第 1 節已記錄的環境
       限制,不是規避真實資料檢查——行情資料仍 100% 來自
       data.binance.vision 官方封存)。--export trades 產出的交易明細
       透過 analysis/data_loader.load_trades() 重新讀回,而不是直接用
       backtesting.results 裡的 dict(刻意模擬 CLI `--export trades` 之後
       重讀匯出檔的真實路徑,兩者理論上應完全一致,這裡選擇「重讀」是
       為了讓交付物包含真正可驗證的匯出檔案)。

    2. 誠實處理「進場點分岔」:策略一、策略五的進場邏輯完全相同,但因為
       出場時間點不同,`max_open_trades`/單一交易對不可重複進場的框架
       限制會讓兩者的**實際**進場點集合隨時間分岔(策略一出場快、可能
       在策略五仍持倉時接下策略五接不到的新進場訊號)。用 (pair, open_date)
       取交集,並誠實統計分岔比例。

    3. 兩項正式判定:
       (a) CP-004 兩層制,策略五 vs 每日再平衡等權 50/50 基準。策略五不是
           「永遠在市」策略,日頻報酬序列從 Freqtrade 回測本身逐日追蹤的
           錢包權益(`Backtesting.all_bt_content[strategy]["wallet_summary"]`,
           非另外重建)換算而得——見下方 `wallet_daily_log_returns()` 的
           揭露:這條權益曲線含 Freqtrade 自己模擬的手續費(cfg["fee"]=0.1%/邊),
           但**不含**本專案慣例的額外 5bps/邊滑價 haircut
           (analysis/cost_model.py,backtest-procedure.md 2.2 節)——這是
           本次執行過程中發現、需要誠實揭露的限制,見腳本末尾與最終報告。
       (b) 配對比較,策略五 vs 策略一本身,用第 2 步的交集交易明細,呼叫
           analysis/paired_trade_comparison.paired_trade_test()。這裡的
           逐筆報酬**有**套用 cost_model.apply_slippage_haircut(與
           pass-b-results.md/CP-008 一貫的方法一致),因為配對比較是
           trade-level 的,可以精確做到。

用法:
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe analysis/tools/run_strategy5_evaluation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis import benchmark as bm  # noqa: E402
from analysis import cost_model  # noqa: E402
from analysis import sharpe_difference as sdiff  # noqa: E402
from analysis.data_loader import load_trades  # noqa: E402
from analysis.offline_exchange import offline_exchange  # noqa: E402
from analysis.paired_trade_comparison import paired_trade_test  # noqa: E402

DATADIR = _REPO_ROOT / "user_data" / "data" / "binance"
CONFIG = _REPO_ROOT / "user_data" / "configs" / "config-common.json"
EXPORT_ROOT = _REPO_ROOT / "user_data" / "backtest_results" / "strategy5_eval"

# CP-003/run_parameter_scan.py 使用的同一段全樣本範圍(docs/pass-b-results.md 第 1 節)
TIMERANGE = "20170817-20260731"
FEE = 0.001  # 幣安現貨手續費(不計 BNB 折扣),backtest-procedure.md 2.1 節

# docs/strategy-5-hypothesis.md 5.2/5.3 節已核准:主判定與配對比較均採 N=10
N_TRIALS = 10


# ---------------------------------------------------------------------------
# 1. 執行回測
# ---------------------------------------------------------------------------

def _build_config(strategy: str, export_dir: Path) -> dict:
    from freqtrade.configuration import Configuration
    from freqtrade.enums import RunMode

    export_dir.mkdir(parents=True, exist_ok=True)
    args = {
        "config": [str(CONFIG)],
        "strategy": strategy,
        "datadir": str(DATADIR),
        "user_data_dir": str(_REPO_ROOT / "user_data"),
        "dataformat_ohlcv": "feather",
        "export": "trades",
        # 用 exportdirectory(非已棄用的 exportfilename)—— 見腳本開發過程中
        # 核實 freqtrade/configuration/configuration.py 219-246 行:backtest
        # 模式下 exportfilename 已棄用,實際生效的是 exportdirectory。
        "exportdirectory": str(export_dir),
        "timerange": TIMERANGE,
    }
    cfg = Configuration(args, RunMode.BACKTEST).get_config()
    cfg["exchange"]["key"] = ""
    cfg["exchange"]["secret"] = ""
    cfg["dry_run"] = True
    cfg["dry_run_wallet"] = 10_000
    cfg["fee"] = FEE
    cfg["timerange"] = TIMERANGE
    cfg["enable_protections"] = True
    return cfg


def run_backtest(strategy: str, export_dir: Path) -> dict:
    """跑一次全樣本回測。回傳:
        stats       — Freqtrade 產出的策略統計 dict
        trades      — 經 --export trades 匯出後,用 data_loader.load_trades() 重讀的交易明細
        wallet_df   — Backtesting.all_bt_content[strategy]["wallet_summary"]
                      (date, currency, rate, balance),Freqtrade 自己逐日追蹤的錢包狀態
        strategy_key — 策略類別名稱(= strategy 參數本身)
    """
    from freqtrade.optimize.backtesting import Backtesting

    with offline_exchange():
        cfg = _build_config(strategy, export_dir)
        backtesting = Backtesting(cfg)
        try:
            backtesting.start()
            strat_key = next(iter(backtesting.results["strategy"]))
            stats = backtesting.results["strategy"][strat_key]
            wallet_df = backtesting.all_bt_content[strat_key].get("wallet_summary")
        finally:
            try:
                backtesting.exchange.close()
            except Exception:
                pass

    trades = load_trades(export_dir, strategy=strat_key)
    return {"stats": stats, "trades": trades, "wallet_df": wallet_df, "strategy_key": strat_key}


# ---------------------------------------------------------------------------
# 2. 進場點分岔
# ---------------------------------------------------------------------------

def entry_key_set(trades: pd.DataFrame) -> set[tuple[str, pd.Timestamp]]:
    if trades.empty:
        return set()
    day = pd.to_datetime(trades["open_date"]).dt.floor("D")
    return set(zip(trades["pair"], day))


def entry_divergence_report(baseline: pd.DataFrame, treatment: pd.DataFrame) -> dict:
    b_keys = entry_key_set(baseline)
    t_keys = entry_key_set(treatment)
    common = b_keys & t_keys
    only_b = b_keys - t_keys
    only_t = t_keys - b_keys
    return {
        "n_baseline": len(b_keys),
        "n_treatment": len(t_keys),
        "n_common": len(common),
        "n_only_baseline": len(only_b),
        "n_only_treatment": len(only_t),
        "frac_baseline_excluded": len(only_b) / len(b_keys) if b_keys else float("nan"),
        "frac_treatment_excluded": len(only_t) / len(t_keys) if t_keys else float("nan"),
        "common_keys": common,
    }


def paired_returns_on_intersection(
    baseline_adj: pd.DataFrame, treatment_adj: pd.DataFrame, common_keys: set
) -> pd.DataFrame:
    """
    用交集鍵(pair, open_day)把兩組(已套用 cost_model 滑價 haircut 的)交易對齊,
    依 open_day 排序,回傳兩欄 baseline_ret / treatment_ret,索引 i 對齊同一筆交易
    (同一個進場點,不同出場方式)。
    """
    b = baseline_adj.copy()
    b["open_day"] = pd.to_datetime(b["open_date"]).dt.floor("D")
    b = b[b.apply(lambda r: (r["pair"], r["open_day"]) in common_keys, axis=1)]

    t = treatment_adj.copy()
    t["open_day"] = pd.to_datetime(t["open_date"]).dt.floor("D")
    t = t[t.apply(lambda r: (r["pair"], r["open_day"]) in common_keys, axis=1)]

    merged = pd.merge(
        b[["pair", "open_day", "profit_ratio_adj"]].rename(
            columns={"profit_ratio_adj": "baseline_ret"}
        ),
        t[["pair", "open_day", "profit_ratio_adj"]].rename(
            columns={"profit_ratio_adj": "treatment_ret"}
        ),
        on=["pair", "open_day"],
        how="inner",
    ).sort_values(["open_day", "pair"]).reset_index(drop=True)
    return merged


# ---------------------------------------------------------------------------
# 3(a). CP-004 兩層制:重建日頻報酬序列
# ---------------------------------------------------------------------------

def wallet_daily_log_returns(wallet_df: pd.DataFrame) -> pd.Series:
    """
    Freqtrade 自身逐日追蹤的錢包狀態 -> 日頻對數報酬序列。

    wallet_df 欄位(date, currency, rate, balance)每天每個幣別一列,balance 為
    該幣別的餘額(USDT 部位、或持有的 BTC/ETH 數量),rate 是當天(候選 K 棒
    開盤時)的估值價格(stake currency 本身 rate=1)。total_quote = rate*balance
    加總同一天所有幣別即為當天總權益(USDT 計價)——這與 Freqtrade 官方
    optimize_reports.generate_wallet_stats() 算法一致(未使用私有 API,
    只是重做同一段公開邏輯,因為該函式本身不對外匯出)。

    ⚠️ 已知限制(執行過程中發現,strategy-5-hypothesis.md 未預見):
    這條權益曲線只反映 Freqtrade 自己模擬的手續費(cfg["fee"]=0.1%/邊),
    **不包含**本專案其餘所有正式判定(pass-b-results.md 的 SR_trade、CP-008
    的 MAE 校準)一貫套用的額外 5bps/邊滑價 haircut(analysis/cost_model.py,
    backtest-procedure.md 2.2 節)。原因:滑價 haircut 目前的實作是「重算
    單筆交易的 profit_ratio」,只對逐筆交易表格有意義,沒有現成的方法把它
    套用到一條連續的逐日權益曲線上(那需要在每筆交易的進出場那兩天分別
    扣一次性成本,量級遠小於本身的日報酬雜訊,但不是零)。因此下面算出的
    CP-004 CAGR / Sharpe 對兩個策略都是**略微樂觀**的估計,且樂觀的方向、
    幅度大致對稱(兩策略的滑價成本以「每邊 5bps」的相同假設估算,見下方
    main() 印出的粗略量級估計)。
    """
    if wallet_df is None or wallet_df.empty:
        return pd.Series(dtype=float)

    df = wallet_df.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df["total_quote"] = df["rate"].astype(float) * df["balance"].astype(float)
    daily_equity = df.groupby("date")["total_quote"].sum().sort_index()
    daily_equity = daily_equity[daily_equity > 0]
    log_equity = np.log(daily_equity)
    return log_equity.diff().dropna()


def in_market_fraction(trades: pd.DataFrame, calendar_index: pd.DatetimeIndex) -> float:
    """佔評估期天數的比例,只要任一標的有未平倉部位就算「在市」。"""
    if trades.empty or len(calendar_index) == 0:
        return 0.0
    flag = pd.Series(False, index=calendar_index)
    for _, t in trades.iterrows():
        od = pd.Timestamp(t["open_date"])
        cd = pd.Timestamp(t["close_date"])
        flag |= (calendar_index >= od) & (calendar_index < cd)
    return float(flag.mean())


# ---------------------------------------------------------------------------
# 交易層級統計(SR_trade / 賺賠比 / 持倉天數)—— 對照 pass-b-results.md /
# post-mortem-strategy-1.md 的既有定義,原樣沿用。
# ---------------------------------------------------------------------------

def sr_trade(returns: np.ndarray) -> float:
    """statistical-methodology.md 第 1 節定義:逐筆交易報酬的 Sharpe,不年化。"""
    r = np.asarray(returns, dtype=float)
    if len(r) < 2:
        return float("nan")
    sd = r.std(ddof=1)
    return float(r.mean() / sd) if sd > 0 else float("nan")


def trade_level_summary(trades_adj: pd.DataFrame) -> dict:
    ret = trades_adj["profit_ratio_adj"].to_numpy(dtype=float)
    holding_days = (
        pd.to_datetime(trades_adj["close_date"]) - pd.to_datetime(trades_adj["open_date"])
    ).dt.total_seconds() / 86400.0

    wins = ret[ret > 0]
    losses = ret[ret < 0]
    avg_win = float(wins.mean()) if len(wins) else float("nan")
    avg_loss = float(losses.mean()) if len(losses) else float("nan")
    win_loss_ratio = (
        abs(avg_win / avg_loss) if np.isfinite(avg_loss) and avg_loss != 0 else float("nan")
    )

    return {
        "n_trades": len(ret),
        "win_rate": float((ret > 0).mean()) if len(ret) else float("nan"),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "win_loss_ratio": win_loss_ratio,
        "avg_holding_days": float(holding_days.mean()) if len(holding_days) else float("nan"),
        "median_holding_days": float(holding_days.median()) if len(holding_days) else float("nan"),
        "sr_trade": sr_trade(ret),
        "total_return_pct": float(((1 + ret).prod() - 1) * 100) if len(ret) else float("nan"),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("策略五正式判定 —— 對真實 BTC/ETH 資料執行(2026-08-16)")
    print("=" * 78)
    print(f"資料範圍:{TIMERANGE}  手續費:{FEE:.3%}/邊  protections 啟用")
    print(f"策略一(基準):RegimeFilteredMomentumBreakout(donchian_period=20)")
    print(f"策略五(對照):TrendFilterExit(Kalman 濾波斜率出場)\n")

    baseline = run_backtest("RegimeFilteredMomentumBreakout", EXPORT_ROOT / "regime_baseline")
    treatment = run_backtest("TrendFilterExit", EXPORT_ROOT / "trend_filter_treatment")

    b_trades, t_trades = baseline["trades"], treatment["trades"]
    print(f"策略一原始匯出交易筆數:{len(b_trades)}")
    print(f"策略五原始匯出交易筆數:{len(t_trades)}\n")

    if b_trades.empty or t_trades.empty:
        print("❌ 至少一組回測沒有產生任何交易,無法繼續判定。")
        return 1

    # ---- 套用滑價 haircut(backtest-procedure.md 2.2 節,與 pass-b-results.md 一致) ----
    b_adj = cost_model.apply_slippage_haircut(b_trades)
    t_adj = cost_model.apply_slippage_haircut(t_trades)

    b_summary = trade_level_summary(b_adj)
    t_summary = trade_level_summary(t_adj)

    print("-" * 78)
    print("交易層級統計(cost_model 滑價 haircut 後,對照 pass-b-results.md 定義)")
    print("-" * 78)
    print(f"{'':28}{'策略一(本次重跑)':>20}{'策略五':>20}")
    print(f"{'交易筆數':28}{b_summary['n_trades']:>20}{t_summary['n_trades']:>20}")
    print(f"{'勝率':28}{b_summary['win_rate']:>19.1%}{t_summary['win_rate']:>19.1%}")
    print(f"{'平均獲利':28}{b_summary['avg_win']:>19.2%}{t_summary['avg_win']:>19.2%}")
    print(f"{'平均虧損':28}{b_summary['avg_loss']:>19.2%}{t_summary['avg_loss']:>19.2%}")
    print(f"{'賺賠比':28}{b_summary['win_loss_ratio']:>20.3f}{t_summary['win_loss_ratio']:>20.3f}")
    print(f"{'平均持倉天數':28}{b_summary['avg_holding_days']:>20.1f}{t_summary['avg_holding_days']:>20.1f}")
    print(f"{'中位持倉天數':28}{b_summary['median_holding_days']:>20.1f}{t_summary['median_holding_days']:>20.1f}")
    print(f"{'SR_trade(未年化)':28}{b_summary['sr_trade']:>20.4f}{t_summary['sr_trade']:>20.4f}")
    print(f"{'總報酬':28}{b_summary['total_return_pct']:>19.1f}%{t_summary['total_return_pct']:>19.1f}%")
    print()
    print("對照 pass-b-results.md(donchian_period=20 原始判定):交易 76 筆、"
          "勝率 44.7%、賺賠比 1.92、平均持倉 14.2 天、SR_trade 0.1723")
    print()

    print("出場原因分布 —— 策略一:")
    print(b_adj["exit_reason"].value_counts().to_string())
    print("\n出場原因分布 —— 策略五:")
    print(t_adj["exit_reason"].value_counts().to_string())
    print()

    # ---- 進場點分岔 ----
    div = entry_divergence_report(b_trades, t_trades)
    print("=" * 78)
    print("進場點分岔統計(依 (pair, open_date) 取交集)")
    print("=" * 78)
    print(f"策略一總進場筆數:{div['n_baseline']}")
    print(f"策略五總進場筆數:{div['n_treatment']}")
    print(f"交集(兩邊都真的開倉):{div['n_common']}")
    print(f"僅策略一有、策略五沒有:{div['n_only_baseline']}"
          f"(佔策略一總數 {div['frac_baseline_excluded']:.1%})")
    print(f"僅策略五有、策略一沒有:{div['n_only_treatment']}"
          f"(佔策略五總數 {div['frac_treatment_excluded']:.1%})")
    print()

    # ---- CP-004 兩層制 ----
    print("=" * 78)
    print("(a) CP-004 兩層制:策略五 vs 每日再平衡等權 50/50 BTC/ETH")
    print("=" * 78)

    bench = bm.benchmark_log_returns(DATADIR)
    b_daily = wallet_daily_log_returns(baseline["wallet_df"])
    t_daily = wallet_daily_log_returns(treatment["wallet_df"])

    common_idx_t = bench.index.intersection(t_daily.index)
    common_idx_b = bench.index.intersection(b_daily.index)
    print(f"基準日頻序列長度(全樣本):{len(bench)}")
    print(f"策略五權益曲線對齊後長度:{len(common_idx_t)}")
    print(f"策略一權益曲線對齊後長度(僅供對照):{len(common_idx_b)}\n")

    def _tier_report(name: str, strat_daily: pd.Series, common_idx) -> dict:
        bench_aligned = bench.loc[common_idx].sort_index().to_numpy()
        strat_aligned = strat_daily.loc[common_idx].sort_index().to_numpy()

        b_perf = bm.performance_summary(bench_aligned)
        s_perf = bm.performance_summary(strat_aligned)
        zero_skill = bm.zero_skill_baseline(b_perf["max_drawdown"])

        mdd_ok = s_perf["max_drawdown"] <= bm.MAX_DRAWDOWN_CAP
        retained = s_perf["cagr"] / b_perf["cagr"] if b_perf["cagr"] else float("nan")
        beats_zero_skill = retained >= zero_skill["required_retention"]
        tier1_pass = mdd_ok and beats_zero_skill

        rho = float(np.corrcoef(strat_aligned, bench_aligned)[0, 1])
        test = sdiff.sharpe_difference_test(strat_aligned, bench_aligned, n_boot=5000)
        delta_ann = test["delta"] * np.sqrt(365)
        se_ann = test["se_hac"] * np.sqrt(365)
        required = sdiff.minimum_detectable_difference(se_ann, n_trials=N_TRIALS)
        tier2_pass = np.isfinite(delta_ann) and np.isfinite(required) and delta_ann >= required

        print(f"--- {name} ---")
        print(f"基準(對齊後):CAGR {b_perf['cagr']:.2%}  Sharpe {b_perf['sharpe']:.4f}  "
              f"MDD {b_perf['max_drawdown']:.2%}  n={b_perf['n']}")
        print(f"策略  :CAGR {s_perf['cagr']:.2%}  Sharpe {s_perf['sharpe']:.4f}  "
              f"MDD {s_perf['max_drawdown']:.2%}  n={s_perf['n']}")
        print(f"第一層:MDD<=30% {'✓' if mdd_ok else '×'}  "
              f"保留報酬比 {retained:.1%} vs 零技巧基準所需 {zero_skill['required_retention']:.1%}  "
              f"{'✓' if beats_zero_skill else '×'}  → {'通過' if tier1_pass else '不通過'}")
        print(f"第二層:ρ={rho:.4f}(結構性要求 ≥0.7 {'✓' if rho >= 0.7 else '×'})  "
              f"Δ(年化)={delta_ann:+.4f}  HAC SE(年化)={se_ann:.4f}  "
              f"bootstrap p={test['p_boot']:.4f}  N={N_TRIALS} 門檻={required:.4f}  "
              f"→ {'通過' if tier2_pass else '不通過'}")
        print()
        return {
            "bench_perf": b_perf, "strat_perf": s_perf, "zero_skill": zero_skill,
            "tier1_pass": tier1_pass, "rho": rho, "delta_ann": delta_ann,
            "se_ann": se_ann, "p_boot": test["p_boot"], "required": required,
            "tier2_pass": tier2_pass, "retained": retained,
        }

    tier_result_t = _tier_report("策略五(正式)", t_daily, common_idx_t)
    tier_result_b = _tier_report("策略一(對照,重跑核對用,非本次正式判定對象)", b_daily, common_idx_b)

    calendar = bench.index
    im_t = in_market_fraction(t_trades, calendar)
    im_b = in_market_fraction(b_trades, calendar)
    print(f"在市時間比例(整個評估期):策略一 {im_b:.1%}  策略五 {im_t:.1%}\n")

    # ---- 滑價 haircut 對權益曲線的粗略量級估計(揭露用,不做修正)----
    approx_slip_cost_b = len(b_trades) * 2 * 0.0005  # 5bps/邊 * 2 邊,以總報酬%量級估
    approx_slip_cost_t = len(t_trades) * 2 * 0.0005
    print(f"[揭露] wallet_summary 未含的滑價成本粗估(交易筆數 * 2 * 5bps,量級參考,"
          f"非精確值):策略一 ≈{approx_slip_cost_b:.2%}  策略五 ≈{approx_slip_cost_t:.2%} "
          f"(分散在 9 年、{len(b_trades)}/{len(t_trades)} 筆交易的進出場日,"
          f"對年化 Sharpe/CAGR 的影響遠小於此總量級數字本身)\n")

    # ---- 配對比較 vs 策略一 ----
    print("=" * 78)
    print("(b) 配對比較:策略五 vs 策略一(交集交易,含滑價 haircut)")
    print("=" * 78)

    paired = paired_returns_on_intersection(b_adj, t_adj, div["common_keys"])
    print(f"配對交易筆數(交集):{len(paired)}\n")

    paired_result = None
    if len(paired) >= 2:
        paired_result = paired_trade_test(
            paired["baseline_ret"].to_numpy(),
            paired["treatment_ret"].to_numpy(),
            n_trials=N_TRIALS,
        )
        for k, v in paired_result.items():
            if k == "note":
                continue
            print(f"  {k:24}{v}")
        if "note" in paired_result:
            print(f"  note: {paired_result['note']}")
    else:
        print("  配對交易筆數不足 2 筆,無法執行配對檢定。")
    print()

    # ---- 可證偽預測逐一核對(strategy-5-hypothesis.md 第 2 節) ----
    print("=" * 78)
    print("可證偽預測核對(strategy-5-hypothesis.md 第 2 節,基準:策略一實測值)")
    print("=" * 78)
    baseline_ref = {"holding_days": 14.2, "win_loss_ratio": 1.92, "sr_trade": 0.1723}
    pred1 = t_summary["avg_holding_days"] > baseline_ref["holding_days"]
    pred2 = (
        np.isfinite(t_summary["win_loss_ratio"])
        and t_summary["win_loss_ratio"] > baseline_ref["win_loss_ratio"]
    )
    pred3 = np.isfinite(t_summary["sr_trade"]) and t_summary["sr_trade"] > baseline_ref["sr_trade"]
    print(f"1. 平均持倉天數拉長:{t_summary['avg_holding_days']:.1f} 天 vs 基準 14.2 天  "
          f"→ {'成立' if pred1 else '不成立'}")
    print(f"2. 賺賠比提升:{t_summary['win_loss_ratio']:.3f} vs 基準 1.92  "
          f"→ {'成立' if pred2 else '不成立'}")
    print(f"3. SR_trade 提升:{t_summary['sr_trade']:.4f} vs 基準 0.1723  "
          f"→ {'成立' if pred3 else '不成立'}")
    print(f"\n本假說核心機制主張(第 2 節):{'成立' if (pred1 and pred2 and pred3) else '被推翻'}"
          f"(三項必須同時成立才算未被推翻)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
docs/backtest-procedure.md 第 4 節完整驗證流程的執行器。

把 Pass A(embargo 校準輪)→ 重新校準 → Pass B(正式輪)整條流程自動化,
並在結構上強制執行 docs/pipeline-findings.md 發現的三個硬性約束:

  約束一(發現二):hyperopt 與 OOS 回測必須「逐 fold 成對、緊鄰」執行。
      hyperopt 會覆寫共用的 <StrategyName>.json 參數檔,若先跑完所有 fold 的
      hyperopt 再跑回測,每個 fold 的 OOS 都會用到最後一個 fold 的參數,
      walk-forward 徹底失效卻不會報錯。本腳本的迴圈主體同時包含這兩步,
      沒有「先全部 hyperopt」這個選項存在。

  約束二:每個 fold 的參數檔在寫入後立刻備份到該 fold 的 artifacts 目錄,
      供事後稽核「這個 fold 的 OOS 到底用了哪組參數」。

  約束三:OOS 回測一律 --cache none。Freqtrade 預設會重用當日的快取回測結果,
      在逐 fold 反覆回測同一個策略檔的情境下,這是另一個可能悄悄回傳
      舊結果的來源。

支援中斷續跑:每個 fold 完成後寫入 state.json,重跑時自動跳過已完成的 fold
(9 個 fold × 200 epochs 仍可能要跑數小時,不應該因為中途中斷就得從頭來過)。

用法:
    # 步驟 1:Pass A 校準輪(只為取得持倉天數分布,epochs 與 Pass B 相同)
    .venv/bin/python analysis/tools/run_walk_forward.py --pass a --epochs 200

    # 步驟 2:依 Pass A 印出的建議值跑 Pass B 正式輪
    .venv/bin/python analysis/tools/run_walk_forward.py --pass b --epochs 200 --embargo-days 34

    # 中斷後續跑(自動跳過已完成的 fold)
    .venv/bin/python analysis/tools/run_walk_forward.py --pass b --epochs 200 --embargo-days 34 --resume
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

STRATEGY = "RegimeFilteredMomentumBreakout"
PARAMS_FILE = _REPO_ROOT / "user_data" / "strategies" / f"{STRATEGY}.json"
HYPEROPT_RESULTS_DIR = _REPO_ROOT / "user_data" / "hyperopt_results"
ARTIFACTS_ROOT = _REPO_ROOT / "analysis" / "artifacts"
FREQTRADE_BIN = _REPO_ROOT / ".venv" / "bin" / "freqtrade"

# docs/backtest-procedure.md 0.2 節執行參數
ANCHOR = datetime(2020, 1, 1, tzinfo=timezone.utc)
CONFIGS = [
    _REPO_ROOT / "user_data" / "configs" / "config-common.json",
    _REPO_ROOT / "user_data" / "configs" / "config-testnet.json",
]
SECRETS_FILE = _REPO_ROOT / "user_data" / "configs" / "secrets-testnet.json"
FEE = 0.001  # backtest-procedure.md 2.1 節:Binance 現貨 VIP0,未計 BNB 折扣
RANDOM_STATE = 42  # 固定以確保可重現


# 由 main() 依 --datadir 設定;None 表示沿用設定檔的預設資料目錄
_DATADIR_OVERRIDE: str | None = None


def _config_args() -> list[str]:
    args = []
    for c in CONFIGS:
        args += ["-c", str(c)]
    if SECRETS_FILE.exists():
        args += ["-c", str(SECRETS_FILE)]
    if _DATADIR_OVERRIDE:
        args += ["--datadir", _DATADIR_OVERRIDE]
    return args


def _run(cmd: list[str], env: dict | None = None) -> None:
    printable = " ".join(str(c) for c in cmd)
    print(f"    $ {printable[:160]}{'...' if len(printable) > 160 else ''}")
    result = subprocess.run(cmd, env={**os.environ, **(env or {})}, cwd=_REPO_ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"指令失敗(returncode={result.returncode}): {printable}")


def _snapshot(directory: Path, suffix: str) -> set[Path]:
    return set(directory.glob(f"*{suffix}")) if directory.exists() else set()


def _newest_new_file(before: set[Path], directory: Path, suffix: str) -> Path:
    after = _snapshot(directory, suffix)
    new = after - before
    if not new:
        raise RuntimeError(f"{directory} 中沒有產生新的 {suffix} 檔案")
    return max(new, key=lambda p: p.stat().st_mtime)


def run_fold(fold, epochs: int, embargo_days: int, fold_dir: Path) -> dict:
    """
    執行單一 fold 的 hyperopt + OOS 回測。

    ⚠️ 這兩步刻意寫在同一個函式裡、無法分開呼叫 —— 見檔案開頭「約束一」。
    """
    fold_dir.mkdir(parents=True, exist_ok=True)

    # 每個 fold 都從策略預設值開始,不繼承上一個 fold 選出的參數
    if PARAMS_FILE.exists():
        PARAMS_FILE.unlink()

    # --- 步驟 1:IS 視窗上跑 hyperopt ---
    print(f"  [1/2] hyperopt  IS={fold.is_timerange}  epochs={epochs}")
    before = _snapshot(HYPEROPT_RESULTS_DIR, ".fthypt")
    _run(
        [
            str(FREQTRADE_BIN), "hyperopt",
            *_config_args(),
            "--strategy", STRATEGY,
            "--hyperopt-loss", "PurgedTradeSharpeLoss",
            "--spaces", "buy", "sell",
            "-e", str(epochs),
            "--timerange", fold.is_timerange,
            "--random-state", str(RANDOM_STATE),
            "--fee", str(FEE),
            "--min-trades", "1",
        ],
        # PurgedTradeSharpeLoss 由環境變數取得該 fold 的 purge 邊界
        env={
            "ANALYSIS_EMBARGO_DAYS": str(embargo_days),
            "ANALYSIS_IS_END": fold.is_end.isoformat(),
        },
    )
    hyperopt_file = _newest_new_file(before, HYPEROPT_RESULTS_DIR, ".fthypt")
    shutil.copy2(hyperopt_file, fold_dir / "hyperopt.fthypt")

    # --- 約束二:立刻備份這個 fold 選出的參數 ---
    if not PARAMS_FILE.exists():
        raise RuntimeError(
            f"hyperopt 未產生參數檔 {PARAMS_FILE}。若沒有這個檔案,下一步的 OOS 回測"
            f"會用策略預設值而非本 fold 選出的參數,結果無效。"
        )
    shutil.copy2(PARAMS_FILE, fold_dir / "params.json")
    selected_params = json.loads(PARAMS_FILE.read_text()).get("params", {})
    print(f"        選出參數: buy={selected_params.get('buy')} sell={selected_params.get('sell')}")

    # --- 步驟 2:立刻用剛寫入的參數跑 OOS 回測(不得延後到其他 fold 之後)---
    print(f"  [2/2] OOS 回測  OOS={fold.oos_timerange}")
    oos_export = fold_dir / "oos_backtest"
    _run(
        [
            str(FREQTRADE_BIN), "backtesting",
            *_config_args(),
            "--strategy", STRATEGY,
            "--timerange", fold.oos_timerange,
            "--fee", str(FEE),
            "--export", "trades",
            "--export-directory", str(fold_dir),
            "--backtest-filename", str(oos_export),
            # 約束三:禁用回測快取,避免悄悄回傳舊結果
            "--cache", "none",
        ]
    )

    from analysis.data_loader import load_trades

    oos_trades = load_trades(fold_dir)
    holding_days = (
        (pd.to_datetime(oos_trades["close_date"]) - pd.to_datetime(oos_trades["open_date"]))
        .dt.total_seconds() / 86400
    ).tolist() if len(oos_trades) else []

    summary = {
        "fold_index": fold.index,
        "is_timerange": fold.is_timerange,
        "oos_timerange": fold.oos_timerange,
        "embargo_days": embargo_days,
        "epochs": epochs,
        "oos_trade_count": int(len(oos_trades)),
        "selected_params": selected_params,
        "holding_days": holding_days,
        "hyperopt_file": str(fold_dir / "hyperopt.fthypt"),
    }
    (fold_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"        OOS 交易 {len(oos_trades)} 筆")
    return summary


def calibrate_embargo(summaries: list[dict]) -> int:
    """
    docs/backtest-procedure.md 4.1 節:
        embargo_days = max(N, M, ATR週期) + 持倉天數 95th 百分位,下限 30 天。
    """
    from analysis.walk_forward import compute_embargo_days

    all_holding = [d for s in summaries for d in s["holding_days"]]
    if not all_holding:
        print("⚠️  Pass A 沒有任何交易,無法校準 embargo,沿用下限 30 天")
        return 30

    p95 = float(np.percentile(all_holding, 95))

    lookbacks = []
    for s in summaries:
        buy = s["selected_params"].get("buy", {})
        sell = s["selected_params"].get("sell", {})
        lookbacks += [
            buy.get("donchian_period", 0),
            buy.get("volume_ma_period", 0),
            sell.get("atr_period", 0),
        ]
    indicator_lookback = int(max(lookbacks)) if lookbacks else 55

    embargo = compute_embargo_days(p95, indicator_lookback)
    print(f"\n  持倉天數 95th 百分位 = {p95:.1f} 天")
    print(f"  指標最大回顧窗(各 fold 聯集上界)= {indicator_lookback} 天")
    print(f"  → 校準後 embargo_days = {embargo}(下限 30)")
    if embargo > 45:
        print(
            "  ⚠️  校準值已超過 risk-policy.md 的 45 天 time-stop,依 backtest-procedure.md 4.1 節\n"
            "      需重新產生 fold 表並檢查 fold 數是否仍 >= 5"
        )
    return embargo


def main() -> int:
    parser = argparse.ArgumentParser(description="walk-forward 驗證執行器")
    parser.add_argument("--pass", dest="pass_name", choices=["a", "b"], required=True,
                        help="a=embargo 校準輪, b=正式輪")
    parser.add_argument("--epochs", type=int, required=True, help="每個 fold 的 hyperopt epochs")
    parser.add_argument("--embargo-days", type=int, default=30,
                        help="Pass B 用 Pass A 校準出的值;Pass A 用下限 30")
    parser.add_argument("--resume", action="store_true", help="跳過已完成的 fold")
    parser.add_argument("--data-end", type=str, default=None,
                        help="資料結束日 YYYY-MM-DD(預設今天),用於產生 fold 表")
    parser.add_argument("--datadir", type=str, default=None,
                        help="覆寫資料目錄(預設用設定檔的 user_data/data/binance)")
    args = parser.parse_args()

    global _DATADIR_OVERRIDE
    _DATADIR_OVERRIDE = args.datadir

    from analysis.walk_forward import generate_fold_boundaries

    data_end = (
        datetime.fromisoformat(args.data_end).replace(tzinfo=timezone.utc)
        if args.data_end else datetime.now(timezone.utc)
    )

    folds = generate_fold_boundaries(ANCHOR, data_end, embargo_days=args.embargo_days)
    if len(folds) < 5:
        print(f"❌ 只能產生 {len(folds)} 個完整 fold,低於 statistical-methodology.md 3.5 節"
              f"的最少 5 個門檻。驗證程序本身無效,請檢查資料範圍或 embargo 設定。")
        return 1

    pass_dir = ARTIFACTS_ROOT / f"pass_{args.pass_name}"
    pass_dir.mkdir(parents=True, exist_ok=True)
    state_file = pass_dir / "state.json"
    state = json.loads(state_file.read_text()) if (args.resume and state_file.exists()) else {"completed": []}

    print("=" * 78)
    print(f"Walk-forward Pass {args.pass_name.upper()}  |  {len(folds)} 個 fold  |  "
          f"epochs={args.epochs}  embargo={args.embargo_days} 天")
    print(f"產出目錄: {pass_dir}")
    if args.pass_name == "b":
        print("⚠️  正式輪:結果將作為 backtest-procedure.md 第 5 節通過/不通過判定依據")
    print("=" * 78)

    summaries: list[dict] = []
    for fold in folds:
        fold_dir = pass_dir / f"fold_{fold.index:02d}"
        if fold.index in state["completed"] and (fold_dir / "summary.json").exists():
            print(f"\n[Fold {fold.index}/{len(folds)}] 已完成,跳過(--resume)")
            summaries.append(json.loads((fold_dir / "summary.json").read_text()))
            continue

        print(f"\n[Fold {fold.index}/{len(folds)}]")
        try:
            summary = run_fold(fold, args.epochs, args.embargo_days, fold_dir)
        except Exception as exc:
            print(f"\n❌ Fold {fold.index} 失敗: {type(exc).__name__}: {exc}")
            print(f"   已完成的 fold 保留在 {pass_dir},可加 --resume 續跑")
            return 1

        summaries.append(summary)
        state["completed"].append(fold.index)
        state_file.write_text(json.dumps(state, indent=2))

    print("\n" + "=" * 78)
    if args.pass_name == "a":
        print("Pass A 完成 —— embargo 校準結果:")
        embargo = calibrate_embargo(summaries)
        print(f"\n下一步:")
        print(f"  .venv/bin/python analysis/tools/run_walk_forward.py \\")
        print(f"      --pass b --epochs 200 --embargo-days {embargo}")
    else:
        print("Pass B 完成 —— 產生最終報告 ...")
        report_path = pass_dir / "final_report.json"
        try:
            _build_report(pass_dir, summaries, report_path)
            print(f"報告已寫入: {report_path}")
        except Exception as exc:
            print(f"⚠️  報告產生失敗: {type(exc).__name__}: {exc}")
            print(f"   各 fold 的原始產出仍保留在 {pass_dir},可手動用 analysis/report.py 處理")
            return 1
    print("=" * 78)
    return 0


def _build_report(pass_dir: Path, summaries: list[dict], report_path: Path) -> None:
    """串接所有 fold 的 OOS 交易,呼叫 analysis/report.py 產出第 5 節總表。"""
    from analysis.data_loader import load_trades
    from analysis.report import FoldResult, build_final_report, run_fold_pipeline, save_report

    fold_results = []
    all_oos = []
    fold_k_values = []

    for s in summaries:
        fold_dir = pass_dir / f"fold_{s['fold_index']:02d}"
        all_oos.append(load_trades(fold_dir))
        k = s["selected_params"].get("sell", {}).get("atr_multiplier")
        if k is not None:
            fold_k_values.append(float(k))

        fold_results.append(
            run_fold_pipeline(
                fold_index=s["fold_index"],
                is_backtest_result_dir=fold_dir,
                hyperopt_results_file=Path(s["hyperopt_file"]),
                oos_backtest_result_dir=fold_dir,
                config={},
            )
        )

    concatenated = pd.concat(all_oos, ignore_index=True).sort_values("open_date")
    report = build_final_report(fold_results, concatenated, fold_k_values)
    save_report(report, report_path)

    print("\n  --- backtest-procedure.md 第 5 節通過/不通過總表 ---")
    for k, v in report["pass_fail_table"].items():
        print(f"    {'✅' if v else '❌'}  {k}")
    print(f"\n  整體結果: {'✅ 通過' if report['overall_pass'] else '❌ 不通過'}")
    if not report["overall_pass"]:
        print("  依 backtest-procedure.md 5.1 節判斷處理路徑:"
              "方法論執行細節問題 -> 修正後重跑;策略假說問題 -> 退回 Phase 1")


if __name__ == "__main__":
    raise SystemExit(main())

"""
驗證 analysis/tools/run_walk_forward.py 的關鍵約束。

⚠️ 本測試不實際呼叫 freqtrade CLI(那需要連交易所載入 markets)。
   它把 _run() 換成 mock,專門驗證 docs/pipeline-findings.md 發現二所要求的
   「hyperopt 與 OOS 回測必須逐 fold 成對、緊鄰執行」這個約束在程式結構上確實成立 ——
   這正是最容易在重構時被無意破壞、且破壞後不會報錯的部分。
"""

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from analysis.tools import run_walk_forward as wf


def test_fold_generation_matches_documented_table():
    folds = wf.__dict__["generate_fold_boundaries"] if False else None  # 明示不從此模組取
    from analysis.walk_forward import generate_fold_boundaries

    folds = generate_fold_boundaries(
        wf.ANCHOR, datetime(2026, 8, 7, tzinfo=timezone.utc), embargo_days=30
    )
    assert len(folds) == 9


def test_calibrate_embargo_never_below_floor():
    """即使持倉時間極短,校準結果也不得低於 30 天下限。"""
    summaries = [{
        "holding_days": [1.0, 2.0, 1.5],
        "selected_params": {"buy": {"donchian_period": 20, "volume_ma_period": 10},
                            "sell": {"atr_period": 14}},
    }]
    assert wf.calibrate_embargo(summaries) >= 30


def test_calibrate_embargo_uses_p95_plus_lookback():
    """持倉時間長時,校準值應為 max(指標回顧窗) + 持倉天數 95th 百分位。"""
    import numpy as np

    holding = [40.0] * 20 + [60.0]
    summaries = [{
        "holding_days": holding,
        "selected_params": {"buy": {"donchian_period": 55, "volume_ma_period": 30},
                            "sell": {"atr_period": 14}},
    }]
    embargo = wf.calibrate_embargo(summaries)
    expected = int(np.ceil(55 + np.percentile(holding, 95)))
    assert embargo == expected, f"應為 max(lookback)=55 + p95,期望 {expected},實得 {embargo}"
    assert embargo > 30, "此情境的校準值應明顯高於 30 天下限"


def test_hyperopt_and_backtest_run_paired_per_fold(tmp_path, monkeypatch):
    """
    核心迴歸測試(docs/pipeline-findings.md 發現二):
    每個 fold 必須是 hyperopt -> 備份參數 -> OOS 回測 的緊鄰順序。
    若有人把它重構成「先跑完所有 hyperopt,再跑所有回測」,每個 fold 的 OOS 都會
    用到最後一個 fold 的參數,walk-forward 徹底失效卻不會報錯 —— 本測試就是防這件事。
    """
    from analysis.walk_forward import generate_fold_boundaries

    call_sequence: list[str] = []
    params_file = tmp_path / "params.json"

    def fake_run(cmd, env=None):
        subcommand = cmd[1]
        call_sequence.append(subcommand)
        if subcommand == "hyperopt":
            # 模擬 freqtrade hyperopt 寫出參數檔
            params_file.write_text(json.dumps({
                "params": {"buy": {"donchian_period": 30}, "sell": {"atr_multiplier": 3.0}}
            }))
            hd = tmp_path / "hyperopt_results"
            hd.mkdir(exist_ok=True)
            # 每次產生不同檔名,如同真實 freqtrade(檔名帶時間戳),
            # 才能讓 _newest_new_file 的「找出新增檔案」邏輯被真正驗證到
            n = sum(1 for c in call_sequence if c == "hyperopt")
            (hd / f"run_{n}.fthypt").write_text("{}")
        elif subcommand == "backtesting":
            # OOS 回測執行的當下,參數檔必須存在(代表確實是 hyperopt 之後緊鄰執行)
            assert params_file.exists(), (
                "OOS 回測執行時參數檔不存在 —— hyperopt 與回測沒有成對執行"
            )

    monkeypatch.setattr(wf, "_run", fake_run)
    monkeypatch.setattr(wf, "PARAMS_FILE", params_file)
    monkeypatch.setattr(wf, "HYPEROPT_RESULTS_DIR", tmp_path / "hyperopt_results")
    monkeypatch.setattr(
        "analysis.data_loader.load_trades",
        lambda *a, **k: pd.DataFrame({
            "open_date": pd.to_datetime(["2022-02-01"], utc=True),
            "close_date": pd.to_datetime(["2022-02-10"], utc=True),
        }),
    )

    folds = generate_fold_boundaries(
        wf.ANCHOR, datetime(2024, 8, 1, tzinfo=timezone.utc), embargo_days=30
    )
    assert len(folds) >= 2, "本測試需要至少 2 個 fold 才能驗證交錯順序"

    for fold in folds[:2]:
        wf.run_fold(fold, epochs=5, embargo_days=30, fold_dir=tmp_path / f"fold_{fold.index}")

    # 關鍵斷言:序列必須是 hyperopt,backtesting,hyperopt,backtesting ...
    # 而不是 hyperopt,hyperopt,backtesting,backtesting
    assert call_sequence == ["hyperopt", "backtesting", "hyperopt", "backtesting"], (
        f"執行順序不是逐 fold 成對:{call_sequence}"
    )


def test_run_fold_fails_loudly_if_params_file_missing(tmp_path, monkeypatch):
    """hyperopt 沒產生參數檔時必須明確報錯,不能讓 OOS 回測靜默使用策略預設值。"""
    def fake_run(cmd, env=None):
        if cmd[1] == "hyperopt":
            (tmp_path / "hyperopt_results").mkdir(exist_ok=True)
            (tmp_path / "hyperopt_results" / "run.fthypt").write_text("{}")
        # 刻意不寫出 params 檔

    monkeypatch.setattr(wf, "_run", fake_run)
    monkeypatch.setattr(wf, "PARAMS_FILE", tmp_path / "never_written.json")
    monkeypatch.setattr(wf, "HYPEROPT_RESULTS_DIR", tmp_path / "hyperopt_results")

    from analysis.walk_forward import generate_fold_boundaries

    fold = generate_fold_boundaries(
        wf.ANCHOR, datetime(2024, 8, 1, tzinfo=timezone.utc), embargo_days=30
    )[0]

    with pytest.raises(RuntimeError, match="未產生參數檔"):
        wf.run_fold(fold, epochs=5, embargo_days=30, fold_dir=tmp_path / "fold_1")


def test_oos_backtest_disables_cache(tmp_path, monkeypatch):
    """
    OOS 回測必須帶 --cache none。
    Freqtrade 預設會重用當日快取結果,在逐 fold 反覆回測同一策略檔時,
    這是另一個可能悄悄回傳舊結果的來源(docs/pipeline-findings.md 約束三)。
    """
    captured: list[list[str]] = []
    params_file = tmp_path / "params.json"

    def fake_run(cmd, env=None):
        captured.append([str(c) for c in cmd])
        if cmd[1] == "hyperopt":
            params_file.write_text(json.dumps({"params": {}}))
            (tmp_path / "hyperopt_results").mkdir(exist_ok=True)
            (tmp_path / "hyperopt_results" / "run.fthypt").write_text("{}")

    monkeypatch.setattr(wf, "_run", fake_run)
    monkeypatch.setattr(wf, "PARAMS_FILE", params_file)
    monkeypatch.setattr(wf, "HYPEROPT_RESULTS_DIR", tmp_path / "hyperopt_results")
    monkeypatch.setattr("analysis.data_loader.load_trades", lambda *a, **k: pd.DataFrame())

    from analysis.walk_forward import generate_fold_boundaries

    fold = generate_fold_boundaries(
        wf.ANCHOR, datetime(2024, 8, 1, tzinfo=timezone.utc), embargo_days=30
    )[0]
    wf.run_fold(fold, epochs=5, embargo_days=30, fold_dir=tmp_path / "fold_1")

    backtest_cmd = next(c for c in captured if c[1] == "backtesting")
    assert "--cache" in backtest_cmd and backtest_cmd[backtest_cmd.index("--cache") + 1] == "none"


def test_hyperopt_receives_purge_env_vars(tmp_path, monkeypatch):
    """PurgedTradeSharpeLoss 靠環境變數取得該 fold 的 purge 邊界,必須正確傳入。"""
    captured_env: dict = {}
    params_file = tmp_path / "params.json"

    def fake_run(cmd, env=None):
        if cmd[1] == "hyperopt":
            captured_env.update(env or {})
            params_file.write_text(json.dumps({"params": {}}))
            (tmp_path / "hyperopt_results").mkdir(exist_ok=True)
            (tmp_path / "hyperopt_results" / "run.fthypt").write_text("{}")

    monkeypatch.setattr(wf, "_run", fake_run)
    monkeypatch.setattr(wf, "PARAMS_FILE", params_file)
    monkeypatch.setattr(wf, "HYPEROPT_RESULTS_DIR", tmp_path / "hyperopt_results")
    monkeypatch.setattr("analysis.data_loader.load_trades", lambda *a, **k: pd.DataFrame())

    from analysis.walk_forward import generate_fold_boundaries

    fold = generate_fold_boundaries(
        wf.ANCHOR, datetime(2024, 8, 1, tzinfo=timezone.utc), embargo_days=30
    )[0]
    wf.run_fold(fold, epochs=5, embargo_days=37, fold_dir=tmp_path / "fold_1")

    assert captured_env["ANALYSIS_EMBARGO_DAYS"] == "37"
    assert captured_env["ANALYSIS_IS_END"].startswith(fold.is_end.strftime("%Y-%m-%d"))

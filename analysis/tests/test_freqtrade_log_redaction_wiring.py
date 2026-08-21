"""
驗證 `analysis/log_redaction.py` 的脫敏過濾器**真的掛在實際執行 freqtrade 的那個
process 的 log handler 上**(而不只是理論上應該有效)。

分兩層:

1. 行程內單元測試 —— `scripts/run_freqtrade_trade.py` 對 `logging.Logger.addHandler`
   的包裝是否讓「之後才被建立的 handler」也自動帶上 filter(這正是 freqtrade
   `setup_logging()` 內 `logging.config.dictConfig()` 清空並重建 root handler 之後
   仍要生效的關鍵)。

2. 端到端子行程整合測試 —— 真的用 `python scripts/run_freqtrade_trade.py trade
   --config ...` 跑起 freqtrade,讓它走完 `setup_logging()`(dictConfig 建立
   console + file handler)、印出一行帶有假機密的日誌,再讀回 log 檔確認被遮蔽。
   同時跑一次**未經包裝**的 `python -m freqtrade trade`(negative control),確認
   同一個假機密在沒有包裝時確實會外洩 —— 沒有這個對照組,上面那個測試可能只是
   「機密根本沒被印出來」而不是「被遮蔽了」。

⚠️ 安全性:
- 全部使用假機密字串(`api_key=abcd1234efgh5678`),不使用任何真實金鑰。
- 不連線任何交易所:刻意把 `exchange.name` 設成一個 ccxt 不認識的名字,freqtrade
  會在 `setup_logging()` **之後**、任何網路連線**之前**於 `check_exchange()` 拋出
  `OperationalException`,由 `freqtrade/main.py` 的 `except FreqtradeException` 用
  `logger.error(str(e))` 印出 —— 這行錯誤訊息內含我們控制的假機密字串,正好是
  「經過 dictConfig 建立的真實 handler」的探針。
- `dry_run: true`,且流程根本走不到 Worker/交易所初始化。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from analysis.log_redaction import REDACTED, SecretRedactionFilter

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import run_freqtrade_trade as wrapper  # noqa: E402

# 假機密:格式刻意符合 security-policy.md 3.3 節的 api_key 規則,但值是無意義字串。
FAKE_SECRET = "abcd1234efgh5678"
PROBE_EXCHANGE_NAME = f"api_key={FAKE_SECRET}"

_WRAPPER_PATH = _SCRIPTS_DIR / "run_freqtrade_trade.py"


# ---------------------------------------------------------------------------
# 1) 行程內:addHandler 包裝
# ---------------------------------------------------------------------------


@pytest.fixture()
def add_handler_patch():
    """裝上 addHandler 包裝,測試結束後還原(避免汙染 pytest 自己的 logging)。"""
    redaction_filter = wrapper.install_freqtrade_log_redaction()
    yield redaction_filter
    wrapper.uninstall_add_handler_patch()
    # 清掉這次可能被掛到 root/bufferHandler 上的 filter,還原測試前狀態。
    for handler in list(logging.getLogger().handlers):
        handler.removeFilter(redaction_filter)
    try:
        from freqtrade.loggers import bufferHandler  # type: ignore[import-not-found]

        bufferHandler.removeFilter(redaction_filter)
    except ImportError:
        pass


def test_handler_added_after_install_gets_filter(add_handler_patch):
    """關鍵性質:filter 掛上去之後才建立的 handler(=dictConfig 重建的那些)也要被涵蓋。"""
    logger = logging.getLogger("test_ft_redaction.late_handler")
    logger.handlers.clear()
    handler = logging.NullHandler()
    logger.addHandler(handler)

    assert any(isinstance(f, SecretRedactionFilter) for f in handler.filters)


def test_late_handler_actually_redacts(add_handler_patch):
    """不只是掛上去,實際送一筆紀錄過去要真的被遮蔽。"""
    logger = logging.getLogger("test_ft_redaction.late_handler_effective")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)

    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger.addHandler(_Collector())
    logger.info("connecting with api_key=%s", FAKE_SECRET)

    assert len(records) == 1
    assert FAKE_SECRET not in records[0].getMessage()
    assert REDACTED in records[0].getMessage()


def test_install_is_idempotent_and_does_not_stack_wrappers(add_handler_patch):
    """重複呼叫不應該把包裝層層疊上去(疊了會導致還原不完全)。"""
    wrapper.install_freqtrade_log_redaction()
    wrapper.install_freqtrade_log_redaction()

    assert wrapper.uninstall_add_handler_patch() is True
    assert wrapper.uninstall_add_handler_patch() is False, "應該只有一層包裝"

    # 還原後新 handler 不再自動帶 filter —— 確認包裝真的被拆乾淨了。
    logger = logging.getLogger("test_ft_redaction.after_uninstall")
    logger.handlers.clear()
    handler = logging.NullHandler()
    logger.addHandler(handler)
    assert not any(isinstance(f, SecretRedactionFilter) for f in handler.filters)

    # fixture 的 teardown 會再呼叫一次 uninstall(回傳 False),不影響。
    wrapper.install_freqtrade_log_redaction()


# ---------------------------------------------------------------------------
# 2) 端到端:真的跑起 freqtrade,走過 setup_logging() 之後才輸出的那行日誌
# ---------------------------------------------------------------------------


def _write_probe_config(tmp_path: Path) -> Path:
    """最小可用設定:dry_run,exchange 名稱是探針字串(ccxt 不認識 → 立刻失敗退出)。"""
    config = {
        "max_open_trades": 1,
        "stake_currency": "USDT",
        "stake_amount": 10,
        "dry_run": True,
        "timeframe": "1d",
        "exchange": {
            "name": PROBE_EXCHANGE_NAME,
            "pair_whitelist": ["BTC/USDT"],
            "pair_blacklist": [],
        },
        "pairlists": [{"method": "StaticPairList"}],
        "entry_pricing": {"price_side": "same", "use_order_book": True, "order_book_top": 1},
        "exit_pricing": {"price_side": "same", "use_order_book": True, "order_book_top": 1},
    }
    path = tmp_path / "probe-config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _subprocess_env() -> dict[str, str]:
    """
    移除 PYTEST_VERSION / PYTEST_CURRENT_TEST。

    原始碼查證(freqtrade/loggers/__init__.py `setup_logging()`):
        if os.environ.get("PYTEST_VERSION") is None or config.get("ft_tests_force_logging"):
            ... logging.config.dictConfig(log_config)
    子行程會繼承 pytest 設定的環境變數,若不清掉,freqtrade 會**跳過** dictConfig,
    測試就不是在測真實啟動路徑了。
    """
    env = dict(os.environ)
    env.pop("PYTEST_VERSION", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_probe(entry_argv: list[str], tmp_path: Path) -> tuple[subprocess.CompletedProcess, str]:
    """跑一次探針,回傳 (CompletedProcess, log 檔內容)。"""
    config_path = _write_probe_config(tmp_path)
    log_path = tmp_path / "probe.log"
    datadir = tmp_path / "datadir"
    datadir.mkdir(exist_ok=True)

    cmd = [
        sys.executable,
        *entry_argv,
        "trade",
        "--config",
        str(config_path),
        "--logfile",
        str(log_path),
        "--datadir",
        str(datadir),
    ]
    proc = subprocess.run(
        cmd,
        cwd=_REPO_ROOT,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    return proc, log_text


def test_wrapper_redacts_secret_written_by_real_freqtrade_handlers(tmp_path):
    """核心驗證:經包裝腳本啟動時,freqtrade 自己的 handler 寫出的日誌已被遮蔽。"""
    proc, log_text = _run_probe([str(_WRAPPER_PATH)], tmp_path)

    # 先確認確實走到了 setup_logging() 之後:log 檔是 dictConfig 建立的 file handler
    # 才會產生的,而且內容含 setup_logging() 自己印的 "Logfile configured"。
    assert log_text, f"沒有產生 log 檔,代表沒走到 setup_logging()。stderr:\n{proc.stderr[:2000]}"
    assert "Logfile configured" in log_text
    assert "Checking exchange" in log_text, "應該走到 check_exchange(setup_logging 之後)"

    # 核心斷言:假機密不出現在任何輸出,且確實是「被遮蔽」而不是「沒印出來」。
    assert FAKE_SECRET not in log_text
    assert REDACTED in log_text
    assert FAKE_SECRET not in proc.stdout
    assert FAKE_SECRET not in proc.stderr


def test_control_unwrapped_freqtrade_leaks_the_same_secret(tmp_path):
    """
    Negative control:同一個探針、改用未包裝的 `python -m freqtrade trade` 執行時,
    假機密**確實**會原樣寫進 log 檔。

    這個測試存在的意義:證明上面那個測試不是因為「這行訊息本來就不會出現」而通過。
    它同時也是這次修補之前的實際行為(preflight_check.py 原本開的就是這個指令)。
    """
    proc, log_text = _run_probe(["-m", "freqtrade"], tmp_path)

    assert log_text, f"沒有產生 log 檔。stderr:\n{proc.stderr[:2000]}"
    assert FAKE_SECRET in log_text, (
        "對照組應該外洩假機密;若這裡失敗,表示探針本身失效(例如 freqtrade 改了錯誤訊息),"
        "上面那個正向測試的結論也就不再可信,需要重新設計探針。"
    )
    assert REDACTED not in log_text


# ---------------------------------------------------------------------------
# 3) preflight_check 確實改用包裝腳本
# ---------------------------------------------------------------------------


def test_preflight_launches_the_wrapper_not_bare_freqtrade():
    import preflight_check as pc

    cmd = pc.build_launch_command(["a.json", "b.json"], ["--strategy", "Foo"])

    assert cmd[0] == sys.executable
    assert Path(cmd[1]) == _WRAPPER_PATH, "必須透過脫敏包裝腳本啟動"
    assert cmd[2] == "trade"
    assert "-m" not in cmd, "不得回退成 `python -m freqtrade`(那個 process 沒有脫敏過濾器)"
    assert cmd[3:] == [
        "--config",
        "a.json",
        "--config",
        "b.json",
        "--strategy",
        "Foo",
    ], "config 與透傳參數必須原樣、依序轉發"

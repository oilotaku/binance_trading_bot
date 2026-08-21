#!/usr/bin/env python3
"""
`freqtrade trade` 的脫敏包裝入口 —— 讓 `analysis/log_redaction.py` 的過濾器真正
掛在「實際執行 freqtrade 的那個 process」的 log handler 上。

為什麼需要這支腳本(問題本身)
------------------------------
`analysis.log_redaction.install_secret_redaction()` 操作的是**呼叫它的那個 Python
process 的 `logging` 模組狀態**。`scripts/preflight_check.py` 原本用
`subprocess.run([sys.executable, "-m", "freqtrade", "trade", ...])` 開一個全新子行程,
子行程有自己獨立的 `logging` 狀態 —— 父行程掛的 filter 對它完全沒有效果。
本腳本取代 `python -m freqtrade` 成為子行程的進入點:先在**同一個 process 內**掛好
過濾器,再把控制權交給 freqtrade 自己的 `main()`。

`freqtrade trade` 的實際呼叫鏈(查證自 .venv 內安裝的 freqtrade 2026.7 原始碼)
--------------------------------------------------------------------------
    python -m freqtrade
      └─ freqtrade/__main__.py           → freqtrade.main.main()
         └─ freqtrade/main.py:main()
            ├─ setup_logging_pre()       ← 第一次設定 logging:
            │                              logging.basicConfig(handlers=[FtRichHandler, bufferHandler])
            ├─ Arguments(sysargv).get_parsed_arg()
            └─ args["func"](args)        → freqtrade/commands/trade_commands.py:start_trading()
               └─ Worker(args)
                  └─ Worker._init(False)
                     └─ Configuration(args, None).get_config()
                        └─ Configuration.load_config()
                           └─ Configuration._process_logging_options(config)
                              └─ freqtrade/loggers/__init__.py:setup_logging(config)
                                 ├─ logging.config.dictConfig(log_config)   ← 關鍵時間點
                                 └─ root.addHandler(bufferHandler)
                     └─ FreqtradeBot(config)   ← 交易所/策略初始化(最可能洩漏機密的階段)

關鍵時間點與為什麼不能只在啟動時掛一次
--------------------------------------
`setup_logging()` 內的 `logging.config.dictConfig()` 會**清空 root logger 既有的
handler 列表並建立全新的 handler 物件**(cpython `logging/config.py` 的
`_clearExistingHandlers()` → `logging.shutdown()` + `del logging._handlerList[:]` +
`root.handlers.clear()`)。因此:

  - 在 `main()` 之前掛上去的 filter,會在 `setup_logging()` 執行後被連同舊 handler 一起丟掉;
  - 而 `setup_logging()` 是在 `Worker.__init__` **裡面**才被呼叫的,呼叫端拿不到
    「dictConfig 剛跑完」這個時間點的控制權 —— 等 `start_trading()` 回來時 bot 已經
    結束了,交易所初始化階段的日誌早就輸出完畢。
  - freqtrade 的 `/reload_config`(`Worker._reconfigure()` → `_init(True)`)會再跑一次
    `Configuration` → `setup_logging()` → `dictConfig()`,所以這不是只發生一次的事件。

唯一例外是 `freqtrade.loggers.bufferHandler`:它是模組層級的單例物件,`dictConfig()`
之後由 `setup_logging()` 用 `root.addHandler(bufferHandler)` 重新掛回去,物件本身不會被
重建,掛在它上面的 filter 會存活(這也是 `install_secret_redaction()` 特別處理它的原因)。

採用的掛勾方式
--------------
`install_freqtrade_log_redaction()` 做兩件事:

  1. 先呼叫 `analysis.log_redaction.install_secret_redaction()`,涵蓋「呼叫當下已經存在」
     的 handler(含 `bufferHandler`)。
  2. 包裝 `logging.Logger.addHandler`,讓**之後任何時間點**被加到任何 logger 上的 handler
     都自動補掛同一個 filter 實例。

選擇包裝 `addHandler` 而不是包裝 `freqtrade.loggers.setup_logging` 的理由:

  - `logging.basicConfig()`(`setup_logging_pre`)與 `logging.config.dictConfig()`
    (`setup_logging`)在 cpython 內部**都是**透過 `root.addHandler(...)` 把 handler 掛上去的,
    一個掛勾同時涵蓋兩個階段,包含 `setup_logging_pre` 到 `setup_logging` 之間那段
    「早期日誌」的空窗期。
  - `freqtrade/configuration/configuration.py` 是用 `from freqtrade.loggers import setup_logging`
    在 module import 時就綁定名稱的,想覆寫必須精準改寫 `freqtrade.configuration.configuration`
    這個 module attribute —— 依賴 freqtrade 內部 import 佈局,比依賴 stdlib `logging` 的
    公開行為脆弱。
  - `dictConfig` 每次重跑(reload_config)都會重新 `addHandler`,所以這個掛勾對重載自動生效。

涵蓋範圍與已知限制
------------------
涵蓋範圍完全等同 `analysis/log_redaction.py` 模組 docstring 的「涵蓋範圍與已知限制」段落
(所有經過 Python `logging` 送出的紀錄),本腳本只負責解決「掛在哪個 process」的問題,
不擴大也不縮小規則涵蓋範圍。特別重申其中**未涵蓋**的一項仍然未涵蓋:
freqtrade 主動推播的 Telegram 告警(`freqtrade/worker.py` 捕捉 `OperationalException`
後直接組字串呼叫 `RPCManager.send_msg()`)不經過 `logging`,`logging.Filter` 掛不上去。

用法
----
    # testnet(dry_run,預設且應優先使用的環境)
    python scripts/run_freqtrade_trade.py trade \\
        --config user_data/configs/config-common.json \\
        --config user_data/configs/config-testnet.json \\
        --config user_data/configs/secrets-testnet.json \\
        --strategy RegimeFilteredMomentumBreakout

參數原樣轉發給 freqtrade,語意與 `python -m freqtrade ...` 完全相同。

人為啟動請走 `scripts/preflight_check.py`(security-policy.md 第 4 節的確認關卡),
它會自動改呼叫本腳本。行程監督(systemd `ExecStart` / docker-compose `command`)的
自動重啟指令則應直接指向本腳本(而不是 `python -m freqtrade trade`),
否則重啟後的那個 process 就沒有脫敏過濾器。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 讓 `analysis.log_redaction` 可被 import —— 直接執行本腳本時 sys.path[0] 是 scripts/,
# 專案根目錄不在路徑上。
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.log_redaction import (  # noqa: E402
    SecretRedactionFilter,
    install_secret_redaction,
)

# 標記在包裝後的函式上,讓重複呼叫 install_freqtrade_log_redaction() 不會層層疊包裝。
_PATCH_MARKER = "_ft_secret_redaction_wrapper"


def _attach_filter(handler: logging.Handler, redaction_filter: SecretRedactionFilter) -> None:
    """把 filter 掛到單一 handler 上(冪等,已經有同型別的 filter 就跳過)。"""
    try:
        if not any(isinstance(existing, SecretRedactionFilter) for existing in handler.filters):
            handler.addFilter(redaction_filter)
    except Exception:  # pragma: no cover - handler 行為異常時不該讓 bot 起不來
        pass


def _patch_add_handler(redaction_filter: SecretRedactionFilter) -> bool:
    """
    包裝 `logging.Logger.addHandler`,讓之後被加上的每一個 handler 自動補掛 filter。

    回傳 True 表示這次真的裝上了包裝;False 表示先前已經裝過(冪等)。
    """
    original = logging.Logger.addHandler
    if getattr(original, _PATCH_MARKER, False):
        return False

    def add_handler(self: logging.Logger, hdlr: logging.Handler) -> None:
        original(self, hdlr)
        _attach_filter(hdlr, redaction_filter)

    setattr(add_handler, _PATCH_MARKER, True)
    add_handler.__wrapped__ = original  # type: ignore[attr-defined]
    logging.Logger.addHandler = add_handler  # type: ignore[method-assign]
    return True


def uninstall_add_handler_patch() -> bool:
    """還原 `logging.Logger.addHandler`(僅供測試清理用;正式執行路徑不需要呼叫)。"""
    current = logging.Logger.addHandler
    if not getattr(current, _PATCH_MARKER, False):
        return False
    logging.Logger.addHandler = current.__wrapped__  # type: ignore[attr-defined,method-assign]
    return True


def install_freqtrade_log_redaction() -> SecretRedactionFilter:
    """在**目前這個 process** 內把脫敏過濾器掛好,並確保之後新建的 handler 也會被掛上。

    必須在 `freqtrade.main.main()` 之前呼叫(見模組 docstring 的呼叫鏈說明)。
    回傳共用的 filter 實例。
    """
    redaction_filter = install_secret_redaction()
    _patch_add_handler(redaction_filter)
    return redaction_filter


def main(argv: list[str] | None = None) -> None:
    """掛好過濾器後,把參數原樣交給 freqtrade 自己的 `main()`。

    `freqtrade.main.main()` 在 `finally` 區塊呼叫 `sys.exit(return_code)`,
    所以本函式正常情況下不會回傳 —— 離開狀態碼與直接跑 `python -m freqtrade` 相同。
    """
    argv = sys.argv[1:] if argv is None else argv

    install_freqtrade_log_redaction()

    from freqtrade.main import main as freqtrade_main

    freqtrade_main(argv)


if __name__ == "__main__":
    main()

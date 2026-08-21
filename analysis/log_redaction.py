"""
日誌/告警脫敏過濾器(docs/security-policy.md 第 3 節「機制二」的程式碼落地)。

背景與定位
----------
docs/security-policy.md 3.2 節的「機制一」(正式環境日誌等級固定 INFO,不用 DEBUG)
才是主要防線;本模組實作的是**機制二**——防「即使在 INFO 等級,程式碼仍可能因為疏失
把機密內插進一行原本無害的訊息裡」(3.3 節)的補漏層,以及 3.4 節要求的「任何轉發到
Telegram 或寫入日誌的例外/錯誤文字,都必須先經過同一套過濾規則」。

本模組刻意只依賴 Python 標準函式庫(`re`、`logging`),不 import `analysis` 底下其他
統計模組,也不依賴 freqtrade——`install_secret_redaction()` 對 freqtrade 的整合是
best-effort(用 try/except 動態 import,freqtrade 不存在時仍可獨立使用/測試)。

三個可重複使用的入口(對應「不能各自重複實作」的要求):

1. `redact_secrets(text)` —— 純函式,對任意字串套用 `SENSITIVE_PATTERNS` 的全部規則。
   任何呼叫路徑(logger 呼叫前手動處理、Telegram 通知組字串前、except 區塊組錯誤訊息時)
   都可以直接呼叫這個函式,不需要各自重新寫一次正則。
2. `SecretRedactionFilter` —— 標準 `logging.Filter` 子類別,掛在 log handler 上即可
   讓「所有」流經該 handler 的紀錄自動套用第 1 點的規則(含格式化訊息、%-style 參數、
   未捕捉例外的 traceback 文字),不需要每個 `logger.xxx()` 呼叫端自己記得脫敏。
3. `install_secret_redaction()` —— 便利函式,把同一個 Filter 實例掛到目前所有已知的
   log handler 上(root logger 的 handlers,以及 freqtrade 用來餵給 REST `/log` 端點與
   Telegram `/logs` 指令的 `bufferHandler`,見下方「涵蓋範圍」)。

涵蓋範圍與已知限制(誠實記錄,對應 docs/security-policy.md 3.4 節的查證要求)
----------------------------------------------------------------------
- 查證依據:freqtrade 2026.7(.venv 內實際安裝版本)原始碼。
- **有涵蓋**:所有經過 Python `logging` 模組送出的紀錄——console/file handler(透過
  freqtrade `log_config` 設定的 dictConfig handlers)、以及 `freqtrade.loggers.bufferHandler`
  (`freqtrade/loggers/__init__.py`)。這個 bufferHandler 同時是 REST `/log` 端點
  (`RPC._rpc_get_logs`)與 Telegram `/logs` 指令(`Telegram._logs`,見
  `freqtrade/rpc/telegram.py`)讀取歷史日誌的來源——因為 `BufferingHandler.handle()`
  在 `emit()`(把 record 存進 buffer)之前會先跑 handler 上掛的 filter,所以只要把本模組
  的 Filter 掛到這個 handler 上,連 Telegram `/logs` 指令顯示出來的內容也會先被脫敏。
- **沒有涵蓋(已查證,非疏漏)**:freqtrade 主動推播的 Telegram 告警(例如
  `freqtrade/worker.py` 補捉到 `OperationalException` 時,直接把
  `traceback.format_exc()` 組進字串呼叫 `self.freqtrade.notify_status(...,
  msg_type=RPCMessageType.EXCEPTION)`)**不會**經過 Python `logging` 模組——它是組好字串後
  直接呼叫 `RPCManager.send_msg()` 送出,`logging.Filter` 機制掛不上這條路徑。這正是
  docs/security-policy.md 交辦時已經預期到的情況:這條路徑目前規劃交給 freqtrade 框架本身
  處理通知傳送,我們自己能且應該掛上這層過濾的地方,是我們自己撰寫的錯誤處理邏輯
  (例如策略/執行層程式碼裡的 `logger.warning`/`logger.error` 呼叫,或未來若自建 Telegram
  通知包裝函式,在組出最終文字、呼叫送出之前手動呼叫 `redact_secrets()`)。這也是為什麼
  `redact_secrets()` 特意設計成一個獨立、無副作用的純函式而不只是包在 Filter 裡——
  它可以在 `SecretRedactionFilter` 涵蓋不到的地方被直接呼叫。

使用方式範例
------------
掛在 Freqtrade 啟動流程中(`setup_logging()` 呼叫之後):

    from analysis.log_redaction import install_secret_redaction
    install_secret_redaction()

⚠️ 兩個常見的接線陷阱(實際整合 freqtrade 時查證出來的,詳見
`scripts/run_freqtrade_trade.py` 的模組 docstring):

1. 本函式只對**呼叫它的那個 process** 的 logging 狀態生效。用 `subprocess` 另外開一個
   process 跑 `freqtrade trade`,父行程呼叫本函式對子行程完全沒有效果。
2. freqtrade 的 `setup_logging()` 內部會呼叫 `logging.config.dictConfig()`,**清空
   root logger 既有的 handler 並重建全新 handler 物件**;在那之前掛上的 filter 會連同
   舊 handler 一起被丟掉(模組層級單例的 `bufferHandler` 是唯一例外)。而
   `setup_logging()` 是在 `Worker.__init__` 內部才被呼叫的,外部拿不到「剛跑完」的時機。

因此正式啟動路徑一律走 `scripts/run_freqtrade_trade.py`,由它在 freqtrade 執行的那個
process 內處理上述兩點;端到端驗證見 `analysis/tests/test_freqtrade_log_redaction_wiring.py`。

或在自訂的例外處理/告警組字串邏輯中直接呼叫:

    from analysis.log_redaction import redact_secrets
    safe_text = redact_secrets(str(exc))
    logger.error(safe_text)
"""

from __future__ import annotations

import logging
import re

__all__ = [
    "REDACTED",
    "SENSITIVE_PATTERNS",
    "redact_secrets",
    "SecretRedactionFilter",
    "install_secret_redaction",
]

REDACTED = "***REDACTED***"

# 金鑰「值」可能出現的字元集。原本只有 `[A-Za-z0-9]`,那是「Binance HMAC-SHA256 金鑰
# 是 64 個英數字元」這個假設的產物;但 Binance 同時支援 Ed25519/RSA API key,此時
# secret 是 base64/PEM 格式,含 `+` `/` `=`(以及 URL-safe 變體的 `-` `_`)。字元集
# 沒放寬的話,遮蔽會停在第一個 `+` 之前,尾段原樣外洩(審查實測案例之一)。
_SECRET_VALUE_CHARS = r"[A-Za-z0-9+/=_\-]"

# 長度下限維持 8(不是放寬建議的 16):既有測試 `BINANCE_API_KEY=ZxCvBnM12345678`
# 的值只有 15 字元,提高下限反而會讓原本遮得到的東西漏出去。放寬字元集是「遮更多」,
# 提高長度下限是「遮更少」,只採用前者。
_SECRET_VALUE = rf"{_SECRET_VALUE_CHARS}{{8,}}"

# docs/security-policy.md 3.3 節列出的 5 條規則,加上 3 條**額外新增**的規則
# (PEM 私鑰區塊、JSON `"key"`/`"secret"` 鍵值對、FREQTRADE__* 環境變數)。
#
# ⚠️ 為什麼會比 3.3 節多:3.3 節的正則本身標明是「概念示意」,而它假設的欄位名是
# `api_key`/`api_secret`——本專案實際用的卻是 freqtrade config schema 的 `"key"`/
# `"secret"`,以及 2.1 節自己指定的 `FREQTRADE__EXCHANGE__KEY/SECRET` 環境變數。
# 安全審查實測確認:只照 3.3 節字面實作的話,本專案**真正會出現在日誌裡的那兩種
# 格式完全不會被遮蔽**。這裡是往「遮更多」的方向補齊,不是放寬。
# docs/security-policy.md 3.3 節的示意正則建議由專案負責人另行同步更新。
#
# 每一條是 (pattern, replacement) 的二元組;replacement 使用 \1 保留欄位名稱/前綴,
# 只有值本身被蓋掉。順序上 PEM 規則必須排在最前面(理由見該條註解),其餘各條的
# key literal 彼此不重疊,順序不影響結果。
SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # PEM 私鑰區塊(Ed25519/RSA API key 的 secret 形式)。放在最前面:整個 BEGIN/END
    # 區塊一次蓋掉,才不會讓後面逐行的規則只遮到片段。`(?s)` 讓 `.` 跨行匹配。
    (
        re.compile(r"(?s)(-----BEGIN [A-Z ]*PRIVATE KEY-----)(.*?)(-----END [A-Z ]*PRIVATE KEY-----)"),
        rf"\1{REDACTED}\3",
    ),
    # JSON 鍵值對形式的 `"key"` / `"secret"`(可帶 api_/api- 前綴)——本專案
    # secrets-*.json / config-*.json 用的就是 freqtrade config schema 自己的鍵名
    # `"key"`/`"secret"`,不是 `api_key`/`api_secret`(審查實測案例之一)。
    #
    # 三個刻意的設計:
    #  1. **鍵名必須被引號包住**,才不會誤傷 `lookback_key=donchian_20` 這種欄位名
    #     碰巧含 "key"、或英文散文裡出現 "key" 這個單字的情況(既有的「不誤傷」測試)。
    #  2. 值用 `[^"']{4,}` 而不是 `_SECRET_VALUE`:JSON 字串值裡可能有 `\n` 跳脫的 PEM、
    #     或任何非英數字元;鍵名已經明確表態這是機密,值就整段蓋掉不做格式假設。
    #  3. 冒號寫成 `[:=]?`(**可有可無**):freqtrade 的 `load_config_file()` 在 JSON
    #     語法錯誤時,會把出錯位置前後的原始檔案內容組進 ConfigurationError 訊息——
    #     而「漏打冒號」正是最典型的那種語法錯誤,此時原始文字長得像 `"key" "值"`。
    #     要求冒號存在的話,恰好在最需要脫敏的那個情境失效。
    (
        re.compile(r"""(?i)(["'](?:api[_-]?)?(?:key|secret)["']\s*[:=]?\s*["'])([^"']{4,})"""),
        rf"\1{REDACTED}",
    ),
    # Freqtrade 環境變數覆寫(docs/security-policy.md 2.1 節指定的 Docker 注入機制):
    # FREQTRADE__EXCHANGE__KEY / FREQTRADE__EXCHANGE__SECRET。順帶涵蓋同一套命名慣例
    # 下的其他機密欄位(例如 FREQTRADE__TELEGRAM__TOKEN)。值用 `[^\s"',]+` 而非 `\S+`,
    # 這樣 `'FREQTRADE__EXCHANGE__KEY': 'xxx',`(os.environ 的 repr 形式)裡的結尾
    # 引號與逗號會留在遮蔽字串外面,讀起來仍然是合法的鍵值對。
    (
        re.compile(
            r"(FREQTRADE__[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD)[\"']?\s*[:=]\s*[\"']?)([^\s\"',]+)"
        ),
        rf"\1{REDACTED}",
    ),
    # API key
    (
        re.compile(rf"(?i)(api[_-]?key[\"']?\s*[:=]\s*[\"']?)({_SECRET_VALUE})"),
        rf"\1{REDACTED}",
    ),
    # API secret
    (
        re.compile(rf"(?i)(api[_-]?secret[\"']?\s*[:=]\s*[\"']?)({_SECRET_VALUE})"),
        rf"\1{REDACTED}",
    ),
    # Binance 簽名請求標頭
    (
        re.compile(r"(?i)(x-mbx-apikey:\s*)(\S+)"),
        rf"\1{REDACTED}",
    ),
    # 已簽名請求 URL 中的 signature= 查詢參數值:HMAC-SHA256 恆為 64 個 hex 字元,
    # 精確匹配長度,不誤傷其他等長度但語意不同的參數。
    (
        re.compile(r"(signature=)([A-Fa-f0-9]{64})"),
        rf"\1{REDACTED}",
    ),
    # Telegram bot token(格式固定為 <數字 bot id>:<35 字元左右的 token 本體>)
    (
        re.compile(r"(?i)(telegram[_-]?bot[_-]?token[\"']?\s*[:=]\s*[\"']?)(\d+:[A-Za-z0-9_-]{30,})"),
        rf"\1{REDACTED}",
    ),
]


def redact_secrets(text: str) -> str:
    """對任意字串套用 docs/security-policy.md 3.3 節的全部脫敏規則,回傳遮蔽後的字串。

    純函式、無副作用——這是本模組唯一的「單一事實來源」,`SecretRedactionFilter`
    與未來任何呼叫路徑(Telegram 通知組字串、except 區塊)都應該呼叫這個函式,
    不應該各自重新實作一份正則規則(3.4 節的明確要求)。

    不含敏感欄位的正常文字會原封不動地回傳(見 analysis/tests/test_log_redaction.py
    的「不誤傷」測試)。
    """
    if not text:
        return text
    redacted = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class SecretRedactionFilter(logging.Filter):
    """掛在 log handler 上,讓所有流經該 handler 的紀錄自動套用 `redact_secrets()`。

    設計重點:
    - 對 `record.getMessage()`(已套用 %-style 參數插值後的最終文字)做脫敏,並把結果寫回
      `record.msg`、清空 `record.args`——這樣不論原始呼叫是 `logger.info("...%s...", secret)`
      這種延遲插值寫法,或是已經組好的 f-string,插值後的最終文字都會被檢查到,不會漏掉
      藏在 `args` 裡、還沒被插入訊息本文的機密值。
    - 若紀錄帶有例外資訊(`exc_info`),額外預先算出、脫敏並快取
      `record.exc_text`——`logging.Formatter.format()` 在發現 `record.exc_text` 已經
      有值時不會重新產生原始(未脫敏)的 traceback 文字,見 cpython `logging/__init__.py`
      `Formatter.format()` 的 `if not record.exc_text:` 判斷。這正是 3.4 節「未捕捉例外的
      堆疊追蹤」這個常被忽略的洩漏管道的對應處理。
    - 一律回傳 `True`(不丟棄任何紀錄,只改內容)——這是脫敏過濾器,不是等級過濾器。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - getMessage() 本身格式化失敗的極端情況
            return True

        redacted_message = redact_secrets(message)
        record.msg = redacted_message
        record.args = None

        if record.exc_info:
            try:
                raw_exc_text = record.exc_text or logging.Formatter().formatException(record.exc_info)
                record.exc_text = redact_secrets(raw_exc_text)
            except Exception:  # pragma: no cover - traceback 格式化本身失敗的極端情況
                pass

        return True


def install_secret_redaction(logger: logging.Logger | None = None) -> SecretRedactionFilter:
    """把一個共用的 `SecretRedactionFilter` 實例掛到目前所有已知的 log handler 上。

    涵蓋範圍(見模組 docstring「涵蓋範圍與已知限制」):
    - `logger`(預設 root logger)目前已註冊的所有 handler(console、file 等)。
    - 若 freqtrade 已安裝且 `freqtrade.loggers.bufferHandler` 存在(REST `/log` 端點與
      Telegram `/logs` 指令共用的 buffer handler),一併掛上——freqtrade 未安裝或尚未
      呼叫過 `setup_logging()` 時靜默略過,不拋錯,讓本模組在非 freqtrade 環境
      (例如單元測試、離線分析腳本)也能獨立使用。

    重複呼叫是安全的(冪等)——同一個 filter 實例不會被重複掛在同一個 handler 上兩次。

    回傳掛上的 filter 實例,方便呼叫端需要時另外掛到其他 handler。
    """
    target_logger = logger if logger is not None else logging.getLogger()
    redaction_filter = SecretRedactionFilter()

    handlers: list[logging.Handler] = list(target_logger.handlers)

    try:
        from freqtrade.loggers import bufferHandler  # type: ignore[import-not-found]

        handlers.append(bufferHandler)
    except ImportError:
        pass

    for handler in handlers:
        if not any(isinstance(existing, SecretRedactionFilter) for existing in handler.filters):
            handler.addFilter(redaction_filter)

    return redaction_filter

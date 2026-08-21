"""
驗證 analysis/log_redaction.py(docs/security-policy.md 3.3/3.4 節「機制二」脫敏過濾器)。

⚠️ 全部使用合成、假格式的測試樣式(例如 `api_key: "abcd1234efgh5678"`),
不使用任何真實金鑰,也不連線任何交易所/Telegram API——這裡只測正則遮蔽邏輯與
logging.Filter 的接線是否正確,不是端到端整合測試。
"""

from __future__ import annotations

import logging

import pytest

from analysis.log_redaction import REDACTED, SecretRedactionFilter, install_secret_redaction, redact_secrets


# ---------------------------------------------------------------------------
# 1) 各類敏感欄位確實被遮蔽 + 2) 遮蔽後仍保留欄位名稱
# ---------------------------------------------------------------------------


def test_api_key_redacted_json_style():
    text = 'config = {"api_key": "abcd1234efgh5678"}'
    out = redact_secrets(text)
    assert "abcd1234efgh5678" not in out
    assert REDACTED in out
    assert "api_key" in out, "欄位名稱應保留,方便除錯"


def test_api_key_redacted_env_style_case_insensitive():
    text = "BINANCE_API_KEY=ZxCvBnM12345678"
    out = redact_secrets(text)
    assert "ZxCvBnM12345678" not in out
    assert REDACTED in out
    assert "API_KEY" in out


def test_api_secret_redacted():
    text = 'api_secret: "s3cr3tVALUEwithLettersAndNumbers9"'
    out = redact_secrets(text)
    assert "s3cr3tVALUEwithLettersAndNumbers9" not in out
    assert REDACTED in out
    assert "api_secret" in out


def test_api_secret_not_confused_with_api_key():
    """api_key 與 api_secret 是兩條獨立規則,同一行同時出現時兩者都要被各自遮蔽。"""
    text = 'api_key="AAAAAAAABBBBBBBB" api_secret="CCCCCCCCDDDDDDDD"'
    out = redact_secrets(text)
    assert "AAAAAAAABBBBBBBB" not in out
    assert "CCCCCCCCDDDDDDDD" not in out
    assert out.count(REDACTED) == 2
    assert "api_key" in out and "api_secret" in out


def test_x_mbx_apikey_header_redacted():
    text = "Request headers: X-MBX-APIKEY: aVeryLongApiKeyValue1234567890"
    out = redact_secrets(text)
    assert "aVeryLongApiKeyValue1234567890" not in out
    assert REDACTED in out
    assert "X-MBX-APIKEY" in out


def test_signature_query_param_redacted():
    sig = "a" * 64  # 64 個 hex 字元(HMAC-SHA256 恆長)
    text = f"GET /api/v3/order?symbol=BTCUSDT&timestamp=123&signature={sig}"
    out = redact_secrets(text)
    assert sig not in out
    assert REDACTED in out
    assert "signature=" in out


def test_telegram_bot_token_redacted():
    text = 'telegram_bot_token = "123456789:ABCDefGhIJKlmNoPQRstuVwxYZ0123456789"'
    out = redact_secrets(text)
    assert "123456789:ABCDefGhIJKlmNoPQRstuVwxYZ0123456789" not in out
    assert REDACTED in out
    assert "telegram_bot_token" in out


# ---------------------------------------------------------------------------
# 2b) 本專案「實際」使用的欄位命名與金鑰格式(安全審查實測失敗的四個案例)
#
# 這一組全部是審查者實測「修復前完全沒有被遮蔽」的具體輸入,補進來當回歸測試。
# 同樣全部是合成假值,沒有任何真實金鑰。
# ---------------------------------------------------------------------------


def test_freqtrade_config_schema_key_and_secret_redacted():
    """案例 1:secrets-*.json 用的是 freqtrade config schema 自己的鍵名 "key"/"secret",
    不是 api_key/api_secret —— 這是本專案 secrets 檔案的實際長相。"""
    fake_key = "FAKEKEYAAAABBBBCCCCDDDDEEEEFFFF0000111122223333444455556666777788"
    fake_secret = "FAKESECRET99998888777766665555444433332222111100009999888877776666"
    text = f'{{"exchange": {{"key": "{fake_key}", "secret": "{fake_secret}"}}}}'
    out = redact_secrets(text)
    assert fake_key not in out
    assert fake_secret not in out
    assert out.count(REDACTED) == 2
    assert '"key"' in out and '"secret"' in out, "鍵名應保留,方便除錯"


def test_malformed_json_parse_error_snippet_redacted():
    """案例 2(HIGH-1 的洩漏管道本身):freqtrade 的 load_config_file() 在 JSON 語法
    錯誤時,會把出錯位置附近的**原始檔案內容**組進 ConfigurationError 訊息。最典型的
    語法錯誤就是漏打冒號,所以此時原文長得像 `"key" "值"`(沒有冒號)。"""
    fake_key = "FAKEKEYAAAABBBBCCCCDDDDEEEEFFFF0000111122223333444455556666777788"
    text = f'Parse error at offset 34: ...\n    "key" "{fake_key}",\n'
    out = redact_secrets(text)
    assert fake_key not in out
    assert REDACTED in out


def test_freqtrade_env_var_override_redacted():
    """案例 3:security-policy.md 2.1 節指定的 Docker 環境變數注入機制。"""
    fake_key = "FAKEKEYAAAABBBBCCCCDDDDEEEEFFFF0000111122223333444455556666777788"
    text = f"FREQTRADE__EXCHANGE__KEY={fake_key}"
    out = redact_secrets(text)
    assert fake_key not in out
    assert REDACTED in out
    assert "FREQTRADE__EXCHANGE__KEY" in out


def test_freqtrade_env_var_secret_redacted_in_environ_repr_style():
    """同上,但長得像 os.environ 的 repr(帶引號與逗號)。"""
    fake_secret = "FAKESECRET99998888777766665555444433332222111100009999888877776666"
    text = f"env: {{'FREQTRADE__EXCHANGE__SECRET': '{fake_secret}', 'PATH': '/usr/bin'}}"
    out = redact_secrets(text)
    assert fake_secret not in out
    assert REDACTED in out
    assert "/usr/bin" in out, "同一個 dict 裡的非機密項目不該被波及"


def test_base64_style_secret_fully_redacted_no_tail_leak():
    """案例 4:Binance 也支援 Ed25519/RSA API key,此時 secret 是 base64 格式
    (含 `+` `/` `=`)。舊規則的 `[A-Za-z0-9]` 字元集會停在第一個 `+` 之前,
    尾段原樣外洩 —— 這裡確認整段都被遮蔽,沒有任何一小段殘留。"""
    fake_b64 = "MC4CAQAwBQYDK2VwBCIEIA+aB/c9dEfG=="
    text = f"api_key: {fake_b64}"
    out = redact_secrets(text)
    assert fake_b64 not in out
    assert REDACTED in out
    # 逐段確認:不能只遮掉 `+` 之前那段,後面的 `aB/c9dEfG==` 也不能留下。
    assert "aB/c9dEfG" not in out
    assert "+" not in out


def test_pem_private_key_block_redacted():
    """PEM 多行私鑰格式(Ed25519/RSA 的另一種呈現方式)整個區塊被蓋掉。"""
    text = (
        "loaded secret:\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "MC4CAQAwBQYDK2VwBCIEIFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAK\n"
        "abcdefghijklmnopqrstuvwxyz0123456789+/==\n"
        "-----END PRIVATE KEY-----\n"
    )
    out = redact_secrets(text)
    assert "MC4CAQAwBQYDK2VwBCIEIFAKE" not in out
    assert "abcdefghijklmnopqrstuvwxyz" not in out
    assert REDACTED in out
    assert "-----BEGIN PRIVATE KEY-----" in out, "區塊界線保留,方便看出這裡曾有一把私鑰"


def test_pem_private_key_inside_json_string_redacted():
    """PEM 被塞進 JSON 字串值(`\\n` 跳脫)時,由 "secret" 鍵值規則整段蓋掉。"""
    text = '{"secret": "-----BEGIN PRIVATE KEY-----\\nMC4CAQAwBQYDK2VwFAKE\\n-----END PRIVATE KEY-----"}'
    out = redact_secrets(text)
    assert "MC4CAQAwBQYDK2VwFAKE" not in out
    assert REDACTED in out


# ---------------------------------------------------------------------------
# 3) 正常訊息完全不受影響
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "benign_text",
    [
        "Entering trade BTC/USDT at 65000.0, stake 100.0 USDT",
        "StoplossGuard triggered for ETH/USDT, locking until 2026-08-22",
        "Order abc-123 filled: side=buy amount=0.01 price=65000.0",
        "Reconciliation: local open trades=2, exchange open orders=2, no mismatch",
        "Connection to Binance testnet re-established after websocket disconnect",
        # 散文裡出現 key/secret 這兩個英文單字,不是欄位名,不該被當成機密鍵值對。
        "The key insight is that donchian breakout needs volume confirmation",
        "No secret sauce here: the exit is a plain ATR trailing stop",
        # 環境變數名稱裡有 FREQTRADE__ 但不是金鑰欄位,不該被遮蔽。
        "FREQTRADE__MAX_OPEN_TRADES=2 FREQTRADE__DRY_RUN=true",
        "",
    ],
)
def test_benign_messages_untouched(benign_text):
    assert redact_secrets(benign_text) == benign_text


def test_unrelated_field_named_key_is_not_touched():
    """欄位名稱裡有 'key' 但語意上不是 api key(例如策略參數 'lookback_key')不該被誤判。"""
    text = "lookback_key=donchian_20 stoploss_key=atr_3x"
    assert redact_secrets(text) == text


# ---------------------------------------------------------------------------
# 4) signature= 邊界情況:精確匹配 64 個 hex 字元,不誤傷其他等長度巧合
# ---------------------------------------------------------------------------


def test_signature_63_hex_chars_not_redacted():
    """長度不足 64,不符合 HMAC-SHA256 長度,不應被視為簽名而遮蔽。"""
    sig = "a" * 63
    text = f"signature={sig}"
    assert redact_secrets(text) == text


def test_signature_65_hex_chars_only_first_64_redacted_boundary():
    """65 個 hex 字元:規則只精確匹配 64 個,多出的第 65 個字元應留在遮蔽字串之後。"""
    sig = "b" * 65
    text = f"signature={sig}"
    out = redact_secrets(text)
    assert out == f"signature={REDACTED}b"


def test_other_param_with_64_hex_chars_not_named_signature_untouched():
    """orderId 剛好也是 64 個 hex 字元,但欄位名不是 signature,不該被誤傷。"""
    coincidental_hex = "0123456789abcdef" * 4  # 64 hex 字元
    assert len(coincidental_hex) == 64
    text = f"orderId={coincidental_hex}&symbol=BTCUSDT"
    assert redact_secrets(text) == text


def test_signature_with_non_hex_chars_not_redacted():
    """含非 hex 字元(如大寫 G 或符號)不符合 HMAC-SHA256 十六進位規則,不遮蔽。"""
    not_hex_sig = "g" * 64  # 'g' 不是合法 hex 字元
    text = f"signature={not_hex_sig}"
    assert redact_secrets(text) == text


# ---------------------------------------------------------------------------
# SecretRedactionFilter:掛在 logging handler 上是否確實生效
# ---------------------------------------------------------------------------


def _make_capturing_logger(name: str) -> tuple[logging.Logger, list[logging.LogRecord]]:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()
    logger.filters.clear()

    records: list[logging.LogRecord] = []

    class _CollectingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _CollectingHandler()
    logger.addHandler(handler)
    return logger, records


def test_filter_redacts_percent_style_args_not_just_format_string():
    """secret 藏在 logger.info(fmt, secret) 的 args 裡,而不是格式字串本身,也要被抓到。"""
    logger, records = _make_capturing_logger("test_log_redaction.percent_args")
    handler = logger.handlers[0]
    handler.addFilter(SecretRedactionFilter())

    logger.info("Sending request with api_key=%s", "abcd1234efgh5678")

    assert len(records) == 1
    formatted = records[0].getMessage()
    assert "abcd1234efgh5678" not in formatted
    assert REDACTED in formatted
    assert records[0].args is None, "args 應被清空,避免下游再次插值出原始值"


def test_filter_redacts_exception_traceback_text():
    """3.4 節:未捕捉例外的堆疊追蹤,若字串裡帶有機密,也必須被同一套規則遮蔽。"""
    logger, records = _make_capturing_logger("test_log_redaction.exc_traceback")
    handler = logger.handlers[0]
    handler.addFilter(SecretRedactionFilter())

    secret_url = "https://api.binance.com/api/v3/order?signature=" + "c" * 64
    try:
        raise RuntimeError(f"HTTP request failed: {secret_url}")
    except RuntimeError:
        logger.exception("Unhandled exchange error")

    assert len(records) == 1
    record = records[0]
    formatted_exc = logging.Formatter().format(record)
    assert secret_url.split("signature=")[1] not in formatted_exc
    assert REDACTED in formatted_exc


def test_filter_does_not_alter_benign_log_record():
    logger, records = _make_capturing_logger("test_log_redaction.benign")
    handler = logger.handlers[0]
    handler.addFilter(SecretRedactionFilter())

    logger.info("Placed order for %s at %s", "BTC/USDT", 65000.0)

    assert len(records) == 1
    assert records[0].getMessage() == "Placed order for BTC/USDT at 65000.0"


def test_filter_never_drops_records():
    """脫敏過濾器只改內容,不是等級過濾器——不應該吃掉任何一筆紀錄。"""
    logger, records = _make_capturing_logger("test_log_redaction.no_drop")
    handler = logger.handlers[0]
    handler.addFilter(SecretRedactionFilter())

    for i in range(5):
        logger.warning("event %d api_key=%s", i, "secretvalue123456")

    assert len(records) == 5


# ---------------------------------------------------------------------------
# install_secret_redaction:接線便利函式
# ---------------------------------------------------------------------------


def test_install_secret_redaction_attaches_to_all_handlers_of_given_logger():
    logger = logging.getLogger("test_log_redaction.install_target")
    logger.handlers.clear()
    logger.filters.clear()
    logger.addHandler(logging.NullHandler())
    logger.addHandler(logging.NullHandler())

    install_secret_redaction(logger=logger)

    for handler in logger.handlers:
        assert any(isinstance(f, SecretRedactionFilter) for f in handler.filters)


def test_install_secret_redaction_is_idempotent():
    logger = logging.getLogger("test_log_redaction.install_idempotent")
    logger.handlers.clear()
    logger.filters.clear()
    logger.addHandler(logging.NullHandler())

    install_secret_redaction(logger=logger)
    install_secret_redaction(logger=logger)

    handler = logger.handlers[0]
    redaction_filters = [f for f in handler.filters if isinstance(f, SecretRedactionFilter)]
    assert len(redaction_filters) == 1, "重複呼叫不應該把同一個 filter 掛兩次"


def test_install_secret_redaction_does_not_raise_regardless_of_freqtrade_availability():
    """不論當下環境有沒有裝 freqtrade、freqtrade 是否已呼叫過 setup_logging(),
    都不應該拋錯——`install_secret_redaction()` 對 freqtrade 的整合是 best-effort。"""
    logger = logging.getLogger("test_log_redaction.freqtrade_optional")
    logger.handlers.clear()
    logger.filters.clear()
    logger.addHandler(logging.NullHandler())

    # 不應拋出例外
    install_secret_redaction(logger=logger)


def test_install_secret_redaction_default_targets_root_logger():
    """未傳入 logger 時預設作用於 root logger,呼叫本身不應拋錯。"""
    returned_filter = install_secret_redaction()
    assert isinstance(returned_filter, SecretRedactionFilter)
    # 清理:避免這個測試把 filter 永久留在 root logger 上,汙染其他測試/pytest 自身輸出。
    for handler in logging.getLogger().handlers:
        handler.removeFilter(returned_filter)
    try:
        from freqtrade.loggers import bufferHandler  # type: ignore[import-not-found]

        bufferHandler.removeFilter(returned_filter)
    except ImportError:
        pass

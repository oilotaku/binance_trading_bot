# pragma pylint: disable=missing-docstring
"""
啟動前檢查腳本(security-policy.md 第 4 節)—— 啟動 `freqtrade trade` 的唯一
正式入口。任何人為啟動(部署、change 後重新上線、冷靜期後恢復)都應該透過
這支腳本,而不是直接下 `freqtrade trade --config ...`。

核心邏輯(security-policy.md 4.1/4.2 節):
    1. 解析這次疊加的 `--config` 檔案清單。
    2. 用 Freqtrade 自己的 `Configuration` 類別載入合併後的設定,讀出合併後
       生效的 `dry_run` 值,以及這次疊加鏈裡實際出現的 `secrets-*.json`
       是哪一個(只看檔名,絕不讀取/印出內容)。
    3. 交叉核對兩者是否一致——見 `cross_check_environment()` 的docstring。
    4. 印出環境橫幅(config 清單、dry_run 值與交叉核對結果、secrets 檔名、
       目前 git commit hash;若判定為 live 環境,額外印出 risk-policy.md
       的關鍵風控數字,直接從 RegimeFilteredMomentumBreakout.py 的 class
       attribute 讀出,不在這裡另外寫死一份數字,避免兩處數字未來不同步)。
    5. Live 環境:要求輸入 `I CONFIRM LIVE TRADING <today>`(完全相符才繼續)。
       Testnet 環境:只需要 Enter 或 Ctrl-C。
    6. 通過檢查後,才真正呼叫 `python scripts/run_freqtrade_trade.py trade
       --config ...`,把這次執行拿到的 config 參數與其他透傳參數(例如
       --strategy)原樣轉發。

為什麼第 6 步不是直接 `python -m freqtrade trade`(重要):
    子行程有自己獨立的 `logging` 模組狀態,`analysis/log_redaction.py` 的
    `install_secret_redaction()` 在本腳本的 process 裡呼叫對子行程完全無效。
    `scripts/run_freqtrade_trade.py` 是等價的 freqtrade 進入點(參數原樣轉發給
    `freqtrade.main.main()`),差別只在於它會先在**子行程自己的 process 內**
    掛上脫敏過濾器,並確保 freqtrade 的 `setup_logging()` 重建 handler 之後
    過濾器仍在。查證過的呼叫鏈與掛勾時間點見該腳本的模組 docstring。

與行程監督自動重啟機制的分工(security-policy.md 4.4 節,重要,避免誤解):
    這支腳本設計給**人為決策啟動**使用(操作者要決定接下來用哪一組 --config
    組合的當下)。systemd unit / docker-compose 的 `ExecStart`/`command` 應該
    直接指向 `python scripts/run_freqtrade_trade.py trade --config ...`
    (不透過本腳本)——崩潰後的自動重啟只是重新執行同一份、已經被人類互動
    確認過的靜態設定,不應該、也不能在無人值守時卡在互動輸入上。本腳本是
    操作者手動編輯/安裝部署設定「之前」的那一次性人為確認關卡,不是行程監督的
    啟動指令本身。(注意:自動重啟指令同樣要指向包裝腳本而不是
    `python -m freqtrade trade`,否則重啟後的 process 就沒有脫敏過濾器。)

已知限制(誠實揭露,不在此腳本內嘗試連線 Binance API):
    - execution-spec.md 7.3 節建議把「目前生效的環境變數是否對應到預期環境」
      也納入交叉驗證,並用一次唯讀 API 呼叫核實實際生效的金鑰帳戶類型/餘額
      量級。本腳本只做到「FREQTRADE__EXCHANGE__KEY/SECRET 是否存在於目前
      process 環境」這個不需要網路連線的部分(見 banner 的 `環境變數` 欄),
      未做遠端驗證——那需要真實可用的 API 金鑰,依任務範圍不在此實作,
      交由專案負責人在取得金鑰後依 execution-spec.md 7.3 節的建議手動驗證。

用法範例(Windows 主控台若出現中文亂碼,比照專案既有慣例——見
analysis/tools/run_strategy5_evaluation.py 用法段——加上
`PYTHONUTF8=1 PYTHONIOENCODING=utf-8`):
    # testnet(dry-run,低儀式感確認)
    python scripts/preflight_check.py \\
        --config user_data/configs/config-common.json \\
        --config user_data/configs/config-testnet.json \\
        --config user_data/configs/secrets-testnet.json \\
        --strategy RegimeFilteredMomentumBreakout

    # live(需要完整輸入確認,含當天日期)
    python scripts/preflight_check.py \\
        --config user_data/configs/config-common.json \\
        --config user_data/configs/config-live.json \\
        --config user_data/configs/secrets-live.json \\
        --strategy RegimeFilteredMomentumBreakout
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 讓 `analysis.log_redaction` 可被 import —— 直接執行本腳本時 sys.path[0] 是 scripts/,
# 專案根目錄不在路徑上(同 scripts/run_freqtrade_trade.py 的處理方式)。
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.log_redaction import redact_secrets  # noqa: E402

_SECRETS_FILENAME_RE = re.compile(r"^secrets-(testnet|live)\.json$")

# security-policy.md 4.2 節第 2 點:確認語句的固定文字部分,日期從系統時間讀,
# 不寫死(不得預先知道今天日期就能照抄舊指令稿蒙混過關)。
_LIVE_CONFIRMATION_PREFIX = "I CONFIRM LIVE TRADING"

# 環境變數覆寫(execution-spec.md 第 7 節)可能攜帶的金鑰鍵名,這裡只檢查
# 是否「存在」,絕不讀取/印出其值(security-policy.md 第 3 節脫敏規則)。
_EXCHANGE_KEY_ENV_VARS = ("FREQTRADE__EXCHANGE__KEY", "FREQTRADE__EXCHANGE__SECRET")


class PreflightAbort(Exception):
    """交叉核對失敗、或使用者未通過確認時拋出,呼叫端一律以非零狀態結束。"""


@dataclass(frozen=True)
class EnvironmentDecision:
    """cross_check_environment() 的回傳值,dry_run/secrets 交叉核對的結論。"""

    dry_run: bool
    secrets_file: str | None
    is_live: bool
    problems: list[str] = field(default_factory=list)

    @property
    def is_consistent(self) -> bool:
        return not self.problems


# ---------------------------------------------------------------------------
# 1. 參數解析
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """
    回傳 (config_paths, passthrough_args)。

    -c/--config 可重複(比照 freqtrade 本身 --config 的用法);任何其他無法
    辨識的參數原樣保留,稍後透傳給 `freqtrade trade`(例如 --strategy)。
    """
    parser = argparse.ArgumentParser(
        description="Freqtrade 啟動前檢查(security-policy.md 第 4 節)",
        add_help=True,
    )
    parser.add_argument(
        "-c",
        "--config",
        action="append",
        dest="config",
        required=True,
        metavar="PATH",
        help="疊加的設定檔路徑,可重複多次,依疊加順序列出",
    )
    known, unknown = parser.parse_known_args(argv)
    return list(known.config), unknown


# ---------------------------------------------------------------------------
# 2. 讀出合併後生效的 dry_run 值(用 Freqtrade 自己的 Configuration 類別)
# ---------------------------------------------------------------------------


def load_merged_dry_run(config_paths: list[str]) -> bool:
    """
    比照 analysis/tools/run_strategy5_evaluation.py、
    analysis/tests/test_vol_targeting_strategy_integration.py 已有的模式,
    呼叫 Freqtrade 內部的 Configuration API 取得「疊加合併後實際生效」的值
    ——不是自己重新寫一套 json 合併邏輯去猜測 Freqtrade 的合併規則。
    """
    from freqtrade.configuration import Configuration

    for p in config_paths:
        if not Path(p).is_file():
            raise PreflightAbort(f"設定檔不存在:{p}")

    # runmode=None 比照 freqtrade/worker.py 呼叫 Configuration 的方式(`freqtrade trade`
    # 實際執行路徑)——讓 Configuration._process_runmode() 依合併後的 dry_run 值自己決定
    # RunMode.DRY_RUN/RunMode.LIVE,而不是由這支腳本自己先猜一個 RunMode 傳進去
    # (原始碼查證:Configuration 沒有 RunMode.TRADE 這個列舉值)。
    args = {"config": config_paths}
    cfg = Configuration(args, None).get_config()
    return bool(cfg.get("dry_run", True))


# ---------------------------------------------------------------------------
# 3. 偵測這次疊加鏈裡的 secrets-*.json(只看檔名)
# ---------------------------------------------------------------------------


def detect_secrets_file(config_paths: list[str]) -> str | None:
    """
    掃描 config_paths 的檔名(不讀取內容),回傳偵測到的 secrets-*.json 檔名。

    若同時偵測到 secrets-testnet.json 與 secrets-live.json,視為設定組合
    本身已經矛盾(不應該同時疊加兩個環境的機密檔案),直接中止。
    """
    matches = []
    for p in config_paths:
        name = Path(p).name
        if _SECRETS_FILENAME_RE.match(name):
            matches.append(name)

    unique = sorted(set(matches))
    if len(unique) > 1:
        raise PreflightAbort(
            f"同時偵測到多個 secrets-*.json 疊加在同一次啟動:{unique}"
            "——這本身就是設定組合矛盾,不應該同時載入一個以上環境的機密檔案。"
        )
    return unique[0] if unique else None


# ---------------------------------------------------------------------------
# 4. 交叉核對(security-policy.md 4.1 節第 3 點)
# ---------------------------------------------------------------------------


def cross_check_environment(dry_run: bool, secrets_file: str | None) -> EnvironmentDecision:
    """
    security-policy.md 4.1 節第 3 點定義的兩個矛盾情境:
        (a) 載入 secrets-live.json 但 dry_run=True
        (b) 載入 secrets-testnet.json 但 dry_run=False

    本函式額外多加一條**非文件明文寫死、但同一種精神的延伸檢查**(見下方
    "(c)"),原因見 scripts/preflight_check.py 模組頂部與交付說明:本專案
    的 config-testnet.json 明確放棄了 ccxt sandbox 端點覆寫,改以
    「dry_run=True 對接正式 Binance API」實作 testnet(execution-spec.md
    第 6 節)——也就是說,這個專案裡沒有「dry_run=False 但仍然安全」的
    testnet 模式:一旦 dry_run 變成 False,不論疊加了哪個 secrets 檔案,
    送出的都是對 Binance 正式環境的真實下單請求。因此:
        (c) dry_run=False,但疊加鏈裡完全沒有 secrets-live.json、
            也沒有 FREQTRADE__EXCHANGE__KEY 環境變數 —— 沒有任何可能的
            真實金鑰來源,幾乎必然是設定疏漏(而非刻意的部署模式),
            與其讓 freqtrade 啟動後才因認證失敗才發現,不如在此直接擋下。
        (d) 疊加了 secrets-*.json,但目前 process 環境**同時**也有
            FREQTRADE__EXCHANGE__KEY/SECRET —— 兩個金鑰來源同時存在。
            execution-spec.md 第 7 節已明文記載:環境變數的優先序高於
            `--config` 疊加鏈,所以實際生效的是環境變數裡的那一組,而不是
            操作者眼睛看得到、寫在指令列上的 secrets 檔案。兩者若不是同一組
            金鑰(例如殘留在 shell 裡的 testnet 金鑰 + 指令列指定的
            secrets-live.json),操作者會對「現在到底用哪個帳戶在交易」
            產生完全錯誤的認知 —— 這正是本腳本要防的那類誤用。

    is_live(是否需要走 4.2 節「完整輸入確認」流程)的判斷只看 dry_run:
    dry_run=False 就一律視為 live,不因為疊加了哪個 secrets 檔案而不同
    ——理由同上,這個專案沒有「dry_run=False 但资金安全」的中間地帶。
    """
    problems: list[str] = []

    # 只檢查「是否存在」,絕不讀取/印出其值(security-policy.md 第 3 節)。
    present_key_env = [v for v in _EXCHANGE_KEY_ENV_VARS if os.environ.get(v)]
    has_key_env = bool(present_key_env)

    if secrets_file == "secrets-live.json" and dry_run:
        problems.append(
            "偵測到 secrets-live.json,但合併後 dry_run=True。"
            "若這只是要用 dry-run 模式測試,請不要疊加 secrets-live.json;"
            "若你真的要切換成實單交易,請先把 config-live.json 的 dry_run 改為 false。"
        )

    if secrets_file == "secrets-testnet.json" and not dry_run:
        problems.append(
            "偵測到 secrets-testnet.json,但合併後 dry_run=False。"
            "本專案的 testnet 模式是「dry_run=True 對接正式 Binance API」"
            "(execution-spec.md 第 6 節),沒有真正的沙盒環境——"
            "dry_run=False 疊加 testnet 金鑰,等同用 testnet 金鑰對正式環境送出真實訂單。"
        )

    if not dry_run and secrets_file != "secrets-live.json" and not has_key_env:
        problems.append(
            "dry_run=False(即將送出真實訂單),但這次疊加鏈裡沒有 secrets-live.json,"
            "目前 process 環境也沒有 FREQTRADE__EXCHANGE__KEY/SECRET——"
            "沒有任何可能的真實金鑰來源,啟動後幾乎必然因認證失敗而報錯,"
            "或代表你其實疊錯了設定檔組合。"
        )

    if secrets_file is not None and has_key_env:
        problems.append(
            f"同時偵測到兩個金鑰來源:疊加鏈裡有 {secrets_file},目前 process 環境"
            f"也設了 {present_key_env}。**環境變數的優先序高於 --config 疊加鏈**"
            "(execution-spec.md 第 7 節),所以實際生效的會是環境變數裡的金鑰,"
            f"不是 {secrets_file} 裡的那一組——兩者若不是同一組金鑰,你會用到"
            "和你以為的完全不同的帳戶。請二擇一:要嘛 unset 這些環境變數,"
            "要嘛從 --config 疊加鏈裡拿掉 secrets 檔案。"
        )

    return EnvironmentDecision(
        dry_run=dry_run,
        secrets_file=secrets_file,
        is_live=not dry_run,
        problems=problems,
    )


# ---------------------------------------------------------------------------
# 5. 從 RegimeFilteredMomentumBreakout.py 讀回風控硬上限(唯一事實來源)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskNumbers:
    risk_fraction_single_cap: float
    notional_single_cap: float
    notional_combined_cap: float
    kill_switch_drawdown: float


def load_risk_numbers_from_strategy() -> RiskNumbers:
    """
    security-policy.md 4.2 節第 1 點要求 live 橫幅顯示 risk_fraction 上限、
    kill switch 門檻、單筆/合併名目部位上限——這些數字**直接從**
    RegimeFilteredMomentumBreakout.py 的 class attribute 讀出,不在這裡另外
    寫死一份,避免兩處數字未來不同步(任務指示明確要求)。

    kill switch 門檻取 `protections` 清單中 `method == "MaxDrawdown"` 且
    `lookback_period_candles` 最大的那個實例的 `max_allowed_drawdown`——
    risk-policy.md 第 5 節定義 kill switch 是「長窗口/近全歷史」的那一層,
    與 6.4 節「月回撤」的短窗口(30 天)實例區分。
    """
    strategies_dir = REPO_ROOT / "user_data" / "strategies"
    if str(strategies_dir) not in sys.path:
        sys.path.insert(0, str(strategies_dir))

    import RegimeFilteredMomentumBreakout as strat_module

    strat = strat_module.RegimeFilteredMomentumBreakout

    max_drawdown_entries = [p for p in strat.protections if p.get("method") == "MaxDrawdown"]
    if not max_drawdown_entries:
        raise PreflightAbort(
            "RegimeFilteredMomentumBreakout.protections 內找不到任何 MaxDrawdown 實例"
            "——無法讀出 kill switch 門檻,拒絕在看不到這個數字的情況下繼續 live 啟動。"
        )
    kill_switch_entry = max(
        max_drawdown_entries, key=lambda p: p.get("lookback_period_candles", 0)
    )

    return RiskNumbers(
        risk_fraction_single_cap=strat.RISK_FRACTION_SINGLE_CAP,
        notional_single_cap=strat.NOTIONAL_SINGLE_CAP,
        notional_combined_cap=strat.NOTIONAL_COMBINED_CAP,
        kill_switch_drawdown=kill_switch_entry["max_allowed_drawdown"],
    )


# ---------------------------------------------------------------------------
# 6. git commit hash
# ---------------------------------------------------------------------------


def get_git_commit_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip()
    except Exception as exc:  # noqa: BLE001 - 這裡刻意寬鬆:讀不到 commit hash不該讓腳本崩潰
        return f"<無法讀取,git rev-parse 失敗:{exc}>"


# ---------------------------------------------------------------------------
# 7. 橫幅輸出
# ---------------------------------------------------------------------------


def render_banner(
    config_paths: list[str],
    decision: EnvironmentDecision,
    commit_hash: str,
    risk_numbers: RiskNumbers | None,
) -> str:
    lines = []
    lines.append("=" * 78)
    lines.append("Freqtrade 啟動前檢查(security-policy.md 第 4 節)")
    lines.append("=" * 78)
    lines.append("")
    lines.append("疊加的 --config 檔案清單(依疊加順序):")
    for i, p in enumerate(config_paths, start=1):
        lines.append(f"  {i}. {p}")
    lines.append("")
    lines.append(f"合併後生效的 dry_run 值:{decision.dry_run}")
    lines.append(
        f"這次疊加鏈中偵測到的機密檔案:{decision.secrets_file or '(無)'}"
        "  <- 只有檔名,內容絕不印出"
    )
    has_key_env = [v for v in _EXCHANGE_KEY_ENV_VARS if os.environ.get(v)]
    lines.append(
        f"process 環境中偵測到的金鑰環境變數:{has_key_env or '(無)'}"
        "  <- 只確認存在,絕不印出其值(execution-spec.md 7.3 節)"
    )
    lines.append(f"目前 git commit hash:{commit_hash}")
    lines.append("")

    if decision.is_consistent:
        lines.append("交叉核對結果:一致,無矛盾。")
    else:
        lines.append("交叉核對結果:*** 偵測到矛盾 ***")
        for problem in decision.problems:
            lines.append(f"  - {problem}")
    lines.append("")

    judged_env = "LIVE(真實資金)" if decision.is_live else "TESTNET / DRY-RUN"
    lines.append(f"判定環境:{judged_env}")

    if decision.is_live and risk_numbers is not None:
        lines.append("")
        lines.append(
            "⚠️  LIVE 環境 —— 以下風控硬上限讀自 "
            "user_data/strategies/RegimeFilteredMomentumBreakout.py,"
            "請對照 docs/risk-policy.md 第 0/1/4 節確認數字相符:"
        )
        lines.append(
            f"    單筆 risk_fraction 上限:{risk_numbers.risk_fraction_single_cap:.2%}"
        )
        lines.append(f"    Kill switch 帳戶回撤門檻:-{risk_numbers.kill_switch_drawdown:.0%}")
        lines.append(f"    單筆名目部位上限:{risk_numbers.notional_single_cap:.0%} 權益")
        lines.append(f"    合併名目部位上限:{risk_numbers.notional_combined_cap:.0%} 權益")

    lines.append("=" * 78)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8. 確認流程
# ---------------------------------------------------------------------------


def require_live_confirmation(today: date, input_fn=input) -> None:
    expected = f"{_LIVE_CONFIRMATION_PREFIX} {today.isoformat()}"
    print()
    print("輸入以下文字以繼續(需完全相符,包含今天日期):")
    print(f"  {expected}")
    typed = input_fn("> ")
    if typed != expected:
        raise PreflightAbort("輸入的確認文字與預期不符,中止啟動。未呼叫 freqtrade trade。")


def require_testnet_confirmation(input_fn=input) -> None:
    print()
    input_fn("[按 Enter 繼續 / Ctrl-C 中止] ")


# ---------------------------------------------------------------------------
# 9. 通過檢查後才真正啟動 freqtrade
# ---------------------------------------------------------------------------


#
# 啟動的是 scripts/run_freqtrade_trade.py(脫敏包裝入口),不是 `python -m freqtrade`。
# 理由:analysis/log_redaction.py 的 install_secret_redaction() 只對「呼叫它的那個
# process 的 logging 狀態」生效,這裡開的是全新子行程,父行程掛的 filter 對它無效。
# 包裝腳本負責在子行程內、freqtrade 自己的 setup_logging() 之後把過濾器掛上去
# (掛勾方式與查證過的呼叫鏈見 scripts/run_freqtrade_trade.py 的模組 docstring)。
# 除此之外參數語意與 `python -m freqtrade trade ...` 完全相同。
FREQTRADE_LAUNCHER = REPO_ROOT / "scripts" / "run_freqtrade_trade.py"


def build_launch_command(config_paths: list[str], passthrough_args: list[str]) -> list[str]:
    """組出實際要執行的子行程指令(抽出來以便測試,不產生任何副作用)。"""
    cmd = [sys.executable, str(FREQTRADE_LAUNCHER), "trade"]
    for p in config_paths:
        cmd += ["--config", p]
    cmd += passthrough_args
    return cmd


def launch_freqtrade(config_paths: list[str], passthrough_args: list[str]) -> int:
    cmd = build_launch_command(config_paths, passthrough_args)

    print()
    print(f"通過檢查,啟動:{' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    return result.returncode


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    config_paths, passthrough_args = parse_args(argv)

    try:
        dry_run = load_merged_dry_run(config_paths)
        secrets_file = detect_secrets_file(config_paths)
        decision = cross_check_environment(dry_run, secrets_file)

        risk_numbers = load_risk_numbers_from_strategy() if decision.is_live else None
        commit_hash = get_git_commit_hash()

        print(render_banner(config_paths, decision, commit_hash, risk_numbers))

        if not decision.is_consistent:
            raise PreflightAbort(
                "設定組合矛盾(見上方交叉核對結果),中止啟動。未呼叫 freqtrade trade。"
            )

        if decision.is_live:
            require_live_confirmation(date.today())
        else:
            require_testnet_confirmation()

    except PreflightAbort as exc:
        print(f"\n[preflight_check] 中止:{redact_secrets(str(exc))}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[preflight_check] 使用者中止(Ctrl-C)。未呼叫 freqtrade trade。", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - 見下方說明,這裡的 catch-all 是刻意的安全措施
        # 為什麼一定要有這個 catch-all(security-policy.md 第 3 節,安全審查 HIGH-1):
        #
        #   load_merged_dry_run() 呼叫 freqtrade 的 Configuration.get_config(),它內部的
        #   load_config_file() 在遇到 JSON 語法錯誤時,會把「出錯位置前後約 80 字元的
        #   原始檔案內容」組進 ConfigurationError 的訊息裡。而這裡讀的檔案正是
        #   secrets-*.json —— 於是一個漏打的逗號,就會讓明文金鑰以未捕捉例外的形式
        #   完整噴到 stderr(已實測重現)。
        #
        #   本腳本又是刻意設計成「絕不印出機密內容,只印檔名」的(見 render_banner),
        #   所以讓任何未預期例外原樣冒泡出去,等於在唯一的啟動關卡上開一個洩漏缺口。
        #
        # 處理方式:自己格式化 traceback,整段(含例外訊息與所有堆疊框的原始碼行)
        # 過一次 redact_secrets() 再輸出。不 re-raise —— re-raise 會讓 Python 直接印出
        # **未經脫敏**的原始 traceback,那正是要防的事。exit code 1 已足以表達失敗。
        safe_traceback = redact_secrets(traceback.format_exc())
        print(
            "\n[preflight_check] 未預期的錯誤,中止啟動。未呼叫 freqtrade trade。\n"
            "以下錯誤訊息已經過 analysis/log_redaction.redact_secrets() 脫敏"
            "(security-policy.md 第 3 節)。若遮蔽後仍看到疑似金鑰的內容,"
            "請立刻視為金鑰已洩漏並輪替:",
            file=sys.stderr,
        )
        print(safe_traceback, file=sys.stderr)
        return 1

    return launch_freqtrade(config_paths, passthrough_args)


if __name__ == "__main__":
    raise SystemExit(main())

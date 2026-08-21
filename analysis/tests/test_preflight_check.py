"""
驗證 scripts/preflight_check.py(security-policy.md 第 4 節,啟動前檢查腳本)
的核心判斷邏輯。全部用合成/暫存設定檔測試,**不會**真的呼叫
`freqtrade trade`(那需要 launch_freqtrade() 才會發生,本檔案完全不呼叫它)。

匯入方式比照 analysis/tests/test_vol_targeting_strategy_integration.py 已有的
慣例(sys.path 插入目標目錄,直接 import 模組)。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import preflight_check as pc  # noqa: E402

COMMON = str(_REPO_ROOT / "user_data" / "configs" / "config-common.json")
CONFIG_TESTNET = str(_REPO_ROOT / "user_data" / "configs" / "config-testnet.json")
CONFIG_LIVE = str(_REPO_ROOT / "user_data" / "configs" / "config-live.json")


@pytest.fixture(autouse=True)
def _clear_key_env(monkeypatch):
    """
    金鑰環境變數會影響 cross_check_environment() 的判斷,預設一律清掉,讓每個
    測試的起點固定 —— 否則開發機上剛好 export 過 FREQTRADE__EXCHANGE__KEY 的人
    會看到與 CI 不同的結果。需要這些環境變數的測試自己 monkeypatch.setenv
    (autouse fixture 先跑,測試本體的 setenv 會覆蓋掉這裡的清除)。
    """
    for var in pc._EXCHANGE_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def tmp_secrets(tmp_path):
    """建立暫存的 secrets-testnet.json / secrets-live.json(佔位假值,非真實金鑰)。"""
    testnet = tmp_path / "secrets-testnet.json"
    testnet.write_text(
        json.dumps({"exchange": {"key": "dummy_testnet", "secret": "dummy_testnet"}})
    )
    live = tmp_path / "secrets-live.json"
    live.write_text(json.dumps({"exchange": {"key": "dummy_live", "secret": "dummy_live"}}))
    return {"testnet": str(testnet), "live": str(live)}


def _dry_run_override(tmp_path, value: bool) -> str:
    p = tmp_path / f"override-dry-run-{value}.json"
    p.write_text(json.dumps({"dry_run": value}))
    return str(p)


# ---------------------------------------------------------------------------
# detect_secrets_file
# ---------------------------------------------------------------------------


def test_detect_secrets_file_finds_testnet(tmp_secrets):
    assert pc.detect_secrets_file([COMMON, CONFIG_TESTNET, tmp_secrets["testnet"]]) == (
        "secrets-testnet.json"
    )


def test_detect_secrets_file_finds_live(tmp_secrets):
    assert pc.detect_secrets_file([COMMON, CONFIG_LIVE, tmp_secrets["live"]]) == "secrets-live.json"


def test_detect_secrets_file_none_when_absent():
    assert pc.detect_secrets_file([COMMON, CONFIG_TESTNET]) is None


def test_detect_secrets_file_rejects_both_at_once(tmp_secrets):
    with pytest.raises(pc.PreflightAbort):
        pc.detect_secrets_file([COMMON, tmp_secrets["testnet"], tmp_secrets["live"]])


# ---------------------------------------------------------------------------
# cross_check_environment —— security-policy.md 4.1 節第 3 點的兩個矛盾情境,
# 以及本腳本額外新增的第三個情境(見 preflight_check.py 模組內註解)
# ---------------------------------------------------------------------------


def test_consistent_testnet_dry_run_true():
    d = pc.cross_check_environment(dry_run=True, secrets_file="secrets-testnet.json")
    assert d.is_consistent
    assert not d.is_live


def test_consistent_live_dry_run_false():
    d = pc.cross_check_environment(dry_run=False, secrets_file="secrets-live.json")
    assert d.is_consistent
    assert d.is_live


def test_contradiction_live_secrets_with_dry_run_true():
    """security-policy.md 4.1 節:secrets-live.json 但 dry_run=True。"""
    d = pc.cross_check_environment(dry_run=True, secrets_file="secrets-live.json")
    assert not d.is_consistent
    assert any("secrets-live.json" in p for p in d.problems)


def test_contradiction_testnet_secrets_with_dry_run_false():
    """security-policy.md 4.1 節:secrets-testnet.json 但 dry_run=False。"""
    d = pc.cross_check_environment(dry_run=False, secrets_file="secrets-testnet.json")
    assert not d.is_consistent
    assert any("secrets-testnet.json" in p for p in d.problems)


def test_contradiction_live_with_no_credential_source(monkeypatch):
    """本腳本額外新增:dry_run=False 但沒有 secrets-live.json、也沒有金鑰環境變數。"""
    monkeypatch.delenv("FREQTRADE__EXCHANGE__KEY", raising=False)
    monkeypatch.delenv("FREQTRADE__EXCHANGE__SECRET", raising=False)
    d = pc.cross_check_environment(dry_run=False, secrets_file=None)
    assert not d.is_consistent


def test_live_with_env_var_credentials_is_not_flagged_missing_source(monkeypatch):
    """env var 金鑰來源(Docker 部署模式,security-policy.md 2.1 節)應視為合法來源。"""
    monkeypatch.setenv("FREQTRADE__EXCHANGE__KEY", "dummy")
    d = pc.cross_check_environment(dry_run=False, secrets_file=None)
    assert d.is_consistent
    assert d.is_live


def test_testnet_with_no_secrets_file_is_not_a_contradiction():
    """dry_run=True 時沒有 secrets 檔案不是 4.1 節定義的兩個矛盾之一。"""
    d = pc.cross_check_environment(dry_run=True, secrets_file=None)
    assert d.is_consistent


# ---------------------------------------------------------------------------
# cross_check_environment (d):secrets 檔案與環境變數金鑰同時存在
#
# execution-spec.md 第 7 節:環境變數優先序高於 --config 疊加鏈。操作者看得到的是
# 指令列上的 secrets 檔案,實際生效的卻是環境變數 —— 兩者不同組時是嚴重誤用。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("secrets_file", ["secrets-testnet.json", "secrets-live.json"])
@pytest.mark.parametrize("env_var", ["FREQTRADE__EXCHANGE__KEY", "FREQTRADE__EXCHANGE__SECRET"])
def test_contradiction_secrets_file_and_env_var_both_present(monkeypatch, secrets_file, env_var):
    monkeypatch.setenv(env_var, "dummy")
    # dry_run 取與 secrets 檔案相符的值,確保被偵測到的矛盾是「兩個金鑰來源並存」
    # 這一條本身,而不是順帶被 (a)/(b) 那兩條抓到。
    dry_run = secrets_file == "secrets-testnet.json"
    d = pc.cross_check_environment(dry_run=dry_run, secrets_file=secrets_file)
    assert not d.is_consistent
    conflict = [p for p in d.problems if "優先序" in p]
    assert len(conflict) == 1, f"應恰好有一條「兩個金鑰來源並存」的矛盾:{d.problems}"
    assert secrets_file in conflict[0]
    assert env_var in conflict[0]
    assert "dummy" not in conflict[0], "訊息只能提到環境變數名稱,絕不能帶出其值"


def test_secrets_file_alone_without_env_var_is_not_flagged(tmp_secrets):
    """只有 secrets 檔案、沒有環境變數 —— 這是正常用法,不該被誤判為矛盾。"""
    d = pc.cross_check_environment(dry_run=True, secrets_file="secrets-testnet.json")
    assert d.is_consistent


def test_env_var_alone_without_secrets_file_is_not_flagged(monkeypatch):
    """只有環境變數、沒有 secrets 檔案 —— 這是 Docker 部署模式,同樣正常。"""
    monkeypatch.setenv("FREQTRADE__EXCHANGE__KEY", "dummy")
    d = pc.cross_check_environment(dry_run=True, secrets_file=None)
    assert d.is_consistent


# ---------------------------------------------------------------------------
# load_merged_dry_run —— 呼叫 Freqtrade 自己的 Configuration 類別
# ---------------------------------------------------------------------------


def test_load_merged_dry_run_testnet_defaults_true():
    assert pc.load_merged_dry_run([COMMON, CONFIG_TESTNET]) is True


def test_load_merged_dry_run_live_defaults_true_until_switched():
    """config-live.json 目前刻意仍是 dry_run=true(骨架建置階段的安全預設)。"""
    assert pc.load_merged_dry_run([COMMON, CONFIG_LIVE]) is True


def test_load_merged_dry_run_respects_later_override(tmp_path):
    override = _dry_run_override(tmp_path, False)
    assert pc.load_merged_dry_run([COMMON, CONFIG_TESTNET, override]) is False


def test_load_merged_dry_run_missing_file_aborts():
    with pytest.raises(pc.PreflightAbort):
        pc.load_merged_dry_run([COMMON, str(_REPO_ROOT / "does" / "not" / "exist.json")])


# ---------------------------------------------------------------------------
# 端到端:testnet/live 兩條標準組合的完整交叉核對結果
# ---------------------------------------------------------------------------


def test_end_to_end_testnet_combo_is_consistent(tmp_secrets):
    paths = [COMMON, CONFIG_TESTNET, tmp_secrets["testnet"]]
    dry_run = pc.load_merged_dry_run(paths)
    secrets = pc.detect_secrets_file(paths)
    decision = pc.cross_check_environment(dry_run, secrets)
    assert decision.is_consistent
    assert not decision.is_live


def test_end_to_end_live_combo_requires_explicit_dry_run_false(tmp_secrets):
    """
    config-live.json 目前預設 dry_run=true —— 疊加 secrets-live.json 但不手動
    覆寫 dry_run 為 false 時,應該被擋下(這正是防止「忘了切換」誤用的設計)。
    """
    paths = [COMMON, CONFIG_LIVE, tmp_secrets["live"]]
    dry_run = pc.load_merged_dry_run(paths)
    secrets = pc.detect_secrets_file(paths)
    decision = pc.cross_check_environment(dry_run, secrets)
    assert not decision.is_consistent


def test_end_to_end_live_combo_consistent_once_dry_run_explicitly_false(tmp_secrets, tmp_path):
    override = _dry_run_override(tmp_path, False)
    paths = [COMMON, CONFIG_LIVE, tmp_secrets["live"], override]
    dry_run = pc.load_merged_dry_run(paths)
    secrets = pc.detect_secrets_file(paths)
    decision = pc.cross_check_environment(dry_run, secrets)
    assert decision.is_consistent
    assert decision.is_live


# ---------------------------------------------------------------------------
# load_risk_numbers_from_strategy —— 唯一事實來源必須真的從策略檔案讀出
# ---------------------------------------------------------------------------


def test_risk_numbers_match_risk_policy_pinned_values():
    """
    這裡直接 pin 住 risk-policy.md 第 0/1/4 節的數字,確保
    load_risk_numbers_from_strategy() 讀到的是這些數字本身,而非誤讀到
    其他欄位;若未來 RegimeFilteredMomentumBreakout.py 的常數改動,這個
    測試會失敗,提醒同步檢查(呼應 risk-policy.md 第 7 節變更流程)。
    """
    rn = pc.load_risk_numbers_from_strategy()
    assert rn.risk_fraction_single_cap == pytest.approx(0.015)
    assert rn.notional_single_cap == pytest.approx(0.50)
    assert rn.notional_combined_cap == pytest.approx(0.80)
    assert rn.kill_switch_drawdown == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# require_live_confirmation —— 打字確認,需完全相符(含當天日期)
# ---------------------------------------------------------------------------


def test_live_confirmation_accepts_exact_match():
    pc.require_live_confirmation(
        date(2026, 8, 21), input_fn=lambda prompt="": "I CONFIRM LIVE TRADING 2026-08-21"
    )  # 不拋例外即為通過


@pytest.mark.parametrize(
    "typed",
    [
        "I CONFIRM LIVE TRADING 2026-08-22",  # 日期錯
        "i confirm live trading 2026-08-21",  # 大小寫不符
        "I CONFIRM LIVE TRADING",  # 缺日期
        "yes",
        "",
    ],
)
def test_live_confirmation_rejects_any_mismatch(typed):
    with pytest.raises(pc.PreflightAbort):
        pc.require_live_confirmation(date(2026, 8, 21), input_fn=lambda prompt="": typed)


# ---------------------------------------------------------------------------
# render_banner —— 絕不印出機密內容,只印檔名
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# main() 的例外處理 —— 絕不讓明文金鑰以未捕捉例外的形式噴到 stderr
#
# 洩漏管道(安全審查 HIGH-1,已實測重現):freqtrade 的 load_config_file() 在 JSON
# 語法錯誤時,會把出錯位置前後約 80 字元的**原始檔案內容**組進 ConfigurationError
# 訊息。這裡讀的正是 secrets-*.json,所以一個漏打的冒號就會把明文金鑰印出來。
# ---------------------------------------------------------------------------


def test_main_does_not_leak_secret_from_malformed_json_config(tmp_path, capsys):
    fake_key = "FAKEKEYAAAABBBBCCCCDDDDEEEEFFFF0000111122223333444455556666777788"
    fake_secret = "FAKESECRET99998888777766665555444433332222111100009999888877776666"
    # 刻意漏掉 "key" 後面的冒號 —— 最典型的 JSON 語法錯誤,也正是審查實測用的形式。
    broken = tmp_path / "secrets-testnet.json"
    broken.write_text(
        "{\n"
        '  "exchange": {\n'
        f'    "key" "{fake_key}",\n'
        f'    "secret": "{fake_secret}"\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    rc = pc.main([
        "--config", COMMON,
        "--config", CONFIG_TESTNET,
        "--config", str(broken),
    ])

    captured = capsys.readouterr()
    combined = captured.out + captured.err

    assert rc == 1, "設定檔壞掉時必須以非零狀態結束,且不得啟動 freqtrade"
    assert fake_key not in combined, "明文 API key 洩漏到終端機輸出"
    assert fake_secret not in combined, "明文 API secret 洩漏到終端機輸出"
    # 確認確實走到了脫敏路徑,而不是碰巧因為別的原因沒印出檔案內容。
    assert "REDACTED" in combined


def test_main_unexpected_exception_is_caught_and_redacted(monkeypatch, capsys):
    """任何未預期例外(不只 ConfigurationError)都要被攔下並脫敏,不能原樣冒泡。"""
    fake_key = "FAKEKEYAAAABBBBCCCCDDDDEEEEFFFF0000111122223333444455556666777788"

    def _boom(_config_paths):
        raise RuntimeError(f'unexpected failure while reading {{"key": "{fake_key}"}}')

    monkeypatch.setattr(pc, "load_merged_dry_run", _boom)

    rc = pc.main(["--config", COMMON])

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc == 1
    assert fake_key not in combined
    assert "REDACTED" in combined


def test_main_preflight_abort_message_is_also_redacted(monkeypatch, capsys):
    fake_key = "FAKEKEYAAAABBBBCCCCDDDDEEEEFFFF0000111122223333444455556666777788"

    def _abort(_config_paths):
        raise pc.PreflightAbort(f'設定檔內容:{{"secret": "{fake_key}"}}')

    monkeypatch.setattr(pc, "load_merged_dry_run", _abort)

    rc = pc.main(["--config", COMMON])

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc == 1
    assert fake_key not in combined


def test_banner_never_prints_secret_values():
    rn = pc.RiskNumbers(
        risk_fraction_single_cap=0.015,
        notional_single_cap=0.50,
        notional_combined_cap=0.80,
        kill_switch_drawdown=0.15,
    )
    decision = pc.EnvironmentDecision(
        dry_run=False, secrets_file="secrets-live.json", is_live=True, problems=[]
    )
    banner = pc.render_banner(
        ["user_data/configs/config-common.json", "user_data/configs/config-live.json"],
        decision,
        "deadbeef",
        rn,
    )
    assert "secrets-live.json" in banner  # 檔名可以出現
    assert "dummy_live" not in banner  # 內容絕不可出現(這裡假設沒有機密值被誤傳入)
    assert "-15%" in banner
    assert "1.50%" in banner

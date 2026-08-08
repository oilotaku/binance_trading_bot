"""
把外部取得的真實市場資料匯入成 Freqtrade 可讀格式,**並在寫入前強制通過
docs/backtest-procedure.md 1.4 節的資料品質檢查**。

存在理由:本執行環境的網路政策擋掉全部市場資料來源(見 docs/data-requirements.md),
`freqtrade download-data` 無法使用,資料只能由外部提供。但「資料是人工搬進來的」
不能變成「資料品質檢查被跳過」—— 那正是 1.4 節要防的事。本工具讓匯入這件事
只有一條路徑,而那條路徑上就有品質閘門。

用法:
    .venv/bin/python analysis/tools/ingest_market_data.py \
        --pair BTC/USDT --input /path/to/BTCUSDT-1d.csv \
        --expected-start 2017-11-01 --expected-end 2026-07-31

    # 依 1.4 節規則 2,孤立缺漏必須先重下一次;確實重下過仍缺漏才加這個旗標
    ... --already-redownloaded

輸入格式(CSV 或 JSON,自動判斷):
    - CSV:需含表頭,欄位 date/open/high/low/close/volume(date 可為 ISO 字串或毫秒時間戳)
    - JSON:Binance/Freqtrade 慣用的陣列格式 [[ts_ms, o, h, l, c, v], ...]

⚠️ 本工具不會修補任何資料。檢查不通過就是不寫入,沒有 --force。
   要放行需要人先去修好資料來源本身。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis.data_quality import check_ohlcv  # noqa: E402

DATADIR = _REPO_ROOT / "user_data" / "data" / "binance"
PROVENANCE_FILE = DATADIR / "PROVENANCE.md"


def _detect_epoch_unit(values: pd.Series) -> str:
    """
    依數量級判斷 epoch 時間戳的單位。

    不同資料源慣例不同(Binance 用毫秒、Coinbase/CryptoCompare 用秒),而**猜錯不會
    報錯,只會安靜地把所有日期算成 1970 年**——更糟的是,日期一旦全部塌縮到同一天,
    規則 1 的缺漏偵測就會變成空操作(期望範圍只剩一天,差集必為空),整個品質閘門
    形同虛設。這正是本專案已經踩過一次的那類錯誤:錯得很安靜、後果很嚴重。

    判斷方式:以 2001-09-09(1e9 秒)為錨,看數量級落在哪一檔。
    """
    v = float(pd.Series(values).abs().median())
    for unit, upper in (("s", 1e11), ("ms", 1e14), ("us", 1e17)):
        if v < upper:
            return unit
    return "ns"


def load_input(path: Path) -> pd.DataFrame:
    """讀入外部檔案並正規化成 Freqtrade 的 6 欄格式。不做任何值的修補。"""
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text())
        df = pd.DataFrame(raw, columns=["date", "open", "high", "low", "close", "volume"])
    else:
        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]
        alias = {"time": "date", "timestamp": "date", "open_time": "date", "vol": "volume"}
        df = df.rename(columns={k: v for k, v in alias.items() if k in df.columns})

    if "date" not in df.columns:
        raise SystemExit(f"❌ 找不到 date 欄位。實際欄位:{list(df.columns)}")

    if pd.api.types.is_numeric_dtype(df["date"]):
        df["date"] = pd.to_datetime(df["date"], unit=_detect_epoch_unit(df["date"]), utc=True)
    else:
        df["date"] = pd.to_datetime(df["date"], utc=True)

    keep = ["date", "open", "high", "low", "close", "volume"]
    missing = [c for c in keep if c not in df.columns]
    if missing:
        raise SystemExit(f"❌ 缺少欄位 {missing}。實際欄位:{list(df.columns)}")

    return df[keep].sort_values("date").reset_index(drop=True)


def record_provenance(pair: str, source_path: Path, report, note: str) -> None:
    """
    1.4 節的規則預設資料來自 `freqtrade download-data`,來源不言自明。人工匯入時
    來源不再不言自明,所以必須留痕 —— 否則日後無法回答「這份資料哪來的、
    什麼時候抓的、涵蓋範圍多少」,而那是回測結果可信度的前提。
    """
    DATADIR.mkdir(parents=True, exist_ok=True)
    if not PROVENANCE_FILE.exists():
        PROVENANCE_FILE.write_text(
            "# 資料來源紀錄 / Data Provenance\n\n"
            "> 本檔由 `analysis/tools/ingest_market_data.py` 自動追加。\n"
            "> 依 docs/backtest-procedure.md 1.4 節,人工匯入的資料必須可追溯來源,\n"
            "> 否則任何以其產出的回測結果都不具備可驗證性。\n"
        )

    with PROVENANCE_FILE.open("a") as f:
        f.write(
            f"\n---\n\n## {pair} — 匯入於 {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}\n\n"
            f"- 來源檔:`{source_path}`\n"
            f"- K 棒數:{report.n_candles}\n"
            f"- 涵蓋範圍:{report.first_date:%Y-%m-%d} ~ {report.last_date:%Y-%m-%d}\n"
            f"- 缺漏日數:{len(report.missing_dates)}"
            f"(孤立 {len(report.isolated_gaps)}、連續區段 {len(report.extended_gaps)})\n"
            f"- 疑似異常 K 棒:{len(report.suspected_spikes)}\n"
            f"- 說明:{note or '(未提供)'}\n"
        )


def main() -> int:
    p = argparse.ArgumentParser(description="匯入真實市場資料(含強制品質檢查)")
    p.add_argument("--pair", required=True, help="例如 BTC/USDT")
    p.add_argument("--input", required=True, type=Path, help="外部資料檔(.csv 或 .json)")
    p.add_argument("--timeframe", default="1d")
    p.add_argument("--expected-start", type=str, default=None, help="YYYY-MM-DD,依 1.2 節下載範圍")
    p.add_argument("--expected-end", type=str, default=None, help="YYYY-MM-DD")
    p.add_argument(
        "--already-redownloaded",
        action="store_true",
        help="依 1.4 節規則 2,孤立缺漏須先重新完整下載一次。加此旗標代表已重下過仍缺漏,"
        "該筆缺漏將轉為「資料缺口警示」記錄在案,而非阻擋匯入",
    )
    p.add_argument("--note", default="", help="寫入 PROVENANCE.md 的來源說明")
    p.add_argument("--dry-run", action="store_true", help="只跑檢查、不寫入")
    args = p.parse_args()

    if not args.input.exists():
        print(f"❌ 找不到輸入檔:{args.input}")
        return 1

    df = load_input(args.input)

    def _parse(s):
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc) if s else None

    report = check_ohlcv(df, args.pair, _parse(args.expected_start), _parse(args.expected_end))
    print(report.summary())

    if not report.passed:
        print(
            "\n❌ 未通過品質檢查,不寫入。\n"
            "   依 1.4 節,結構性錯誤與連續缺漏都必須從資料來源本身解決,\n"
            "   本工具刻意不提供 forward-fill / 插值 / --force 等繞過手段。"
        )
        return 2

    if report.isolated_gaps and not args.already_redownloaded:
        print(
            "\n❌ 偵測到孤立缺漏,依 1.4 節規則 2 必須先重新完整下載該 pair 一次。\n"
            "   若已重下過且仍然缺漏,請加上 --already-redownloaded,\n"
            "   該筆缺漏會被記錄為「資料缺口警示」,並在第 4 節執行時人工檢查\n"
            "   缺口是否影響指標計算窗或任一筆交易的進出場判斷。"
        )
        return 3

    if args.dry_run:
        print("\n(--dry-run:檢查通過,未寫入)")
        return 0

    from freqtrade.data.history import get_datahandler

    DATADIR.mkdir(parents=True, exist_ok=True)
    handler = get_datahandler(DATADIR, data_format="feather")
    from freqtrade.enums import CandleType

    handler.ohlcv_store(args.pair, args.timeframe, data=df, candle_type=CandleType.SPOT)
    record_provenance(args.pair, args.input, report, args.note)

    print(f"\n✅ 已寫入 {DATADIR}({args.pair} {args.timeframe},{len(df)} 根)")
    print(f"   來源已記錄於 {PROVENANCE_FILE.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

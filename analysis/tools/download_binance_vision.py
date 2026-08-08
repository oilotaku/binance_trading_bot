"""
從幣安官方歷史資料公開站 https://data.binance.vision 下載月封存 K 線。

存在理由 —— 為什麼不直接用 `freqtrade download-data`:
本環境的出口 IP 位於幣安的限制地區,`api.binance.com` 回應 HTTP 451
(「Service unavailable from a restricted location」)。Freqtrade 的下載路徑雖然
也會用到 data.binance.vision,但它需要先 `load_markets()`(走 api.binance.com),
且封存只到前一日、最近幾根 K 棒會回退到 REST API —— 兩處都會撞上 451。

data.binance.vision 本身不受該限制(實測 200),所以本工具直接抓月封存 ZIP,
繞開 REST API。這**不是**規避網路政策:兩個網域都在環境白名單內,451 是幣安
依其服務條款做的地區限制,本工具沒有、也不嘗試繞過它 —— 只是不使用那個端點。

完整性驗證:每個 ZIP 都有官方發布的 .CHECKSUM(SHA256),本工具逐檔比對,
不符即中止。這是 docs/backtest-procedure.md 1.4 節「資料必須有可驗證來源」
在人工下載情境下的具體落實。

用法:
    .venv/bin/python analysis/tools/download_binance_vision.py \
        --pair BTCUSDT --start 2017-08 --end 2026-07 \
        --out /tmp/BTCUSDT-1d.csv

輸出為 ingest_market_data.py 可直接吃的 6 欄 CSV。**本工具只負責取得與完整性,
不做品質判斷** —— 缺漏、異常值一律交給 analysis/data_quality.py,職責分開。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

BASE = "https://data.binance.vision/data/spot/monthly/klines"

# 幣安月封存 CSV 的欄位順序(無表頭)。只取前 6 欄,其餘為成交筆數等衍生欄位。
BINANCE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]


def month_range(start: str, end: str) -> list[str]:
    s = date.fromisoformat(f"{start}-01")
    e = date.fromisoformat(f"{end}-01")
    out, cur = [], s
    while cur <= e:
        out.append(f"{cur:%Y-%m}")
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return out


def _get(url: str, timeout: int = 120) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def fetch_month(pair: str, timeframe: str, month: str) -> tuple[pd.DataFrame, str]:
    """
    下載並驗證單月封存。回傳 (DataFrame, sha256)。

    校驗和不符時直接拋例外中止 —— 不重試、不略過。一個內容與官方公布雜湊不符的
    封存檔,無論原因是傳輸損毀還是別的,都不該被靜默接受進資料集。
    """
    name = f"{pair}-{timeframe}-{month}.zip"
    url = f"{BASE}/{pair}/{timeframe}/{name}"

    blob = _get(url)
    digest = hashlib.sha256(blob).hexdigest()

    expected = _get(url + ".CHECKSUM").decode().split()[0]
    if digest != expected:
        raise RuntimeError(f"{name} SHA256 不符:官方 {expected},實得 {digest}")

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        csv_name = z.namelist()[0]
        with z.open(csv_name) as f:
            head = f.read(64)
        with z.open(csv_name) as f:
            # 幣安在 2025 年前後為部分封存加上了表頭,兩種都要能吃
            has_header = head.lower().startswith(b"open_time")
            df = pd.read_csv(
                f,
                header=0 if has_header else None,
                names=None if has_header else BINANCE_COLUMNS,
            )

    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
    df["open_time"] = _normalize_to_millis(df["open_time"])
    return df, digest


def _normalize_to_millis(ts: pd.Series) -> pd.Series:
    """
    把時間戳逐列正規化成毫秒。

    ⚠️ 這不是防禦性寫法,是針對一個實測到的事實:**幣安在 2025-01-01 把月封存的
    時間戳單位從毫秒改成微秒**。BTCUSDT 1d 全量下載後,2017-08~2024-12 共 2,694 列
    是毫秒(約 1e12),2025-01 之後 577 列是微秒(約 1e15),同一份資料集混用兩種單位。

    因此**不能對整份資料取單一單位**(例如用中位數推斷)—— 那會讓其中一段被錯誤
    解讀成西元五萬年或 1970 年。必須逐列依數量級判斷。
    """
    v = ts.astype("int64")
    # 以 2017 年之後的合理範圍為錨:秒 ~1e9、毫秒 ~1e12、微秒 ~1e15、奈秒 ~1e18
    return (
        v.where(v < 10**14, v // 1000)          # 微秒/奈秒 → 毫秒
        .pipe(lambda s: s.where(s < 10**14, s // 1000))
        .pipe(lambda s: s.where(s >= 10**11, s * 1000))  # 秒 → 毫秒
    )


def main() -> int:
    p = argparse.ArgumentParser(description="從 data.binance.vision 下載月封存 K 線")
    p.add_argument("--pair", required=True, help="幣安符號,例如 BTCUSDT(非 BTC/USDT)")
    p.add_argument("--timeframe", default="1d")
    p.add_argument("--start", required=True, help="YYYY-MM")
    p.add_argument("--end", required=True, help="YYYY-MM(含)")
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()

    months = month_range(args.start, args.end)
    print(f"下載 {args.pair} {args.timeframe}:{months[0]} ~ {months[-1]}({len(months)} 個月)")

    frames, checksums = [], []
    for i, m in enumerate(months, 1):
        try:
            df, digest = fetch_month(args.pair, args.timeframe, m)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"  [{i:3d}/{len(months)}] {m}  404 —— 該月無封存,跳過")
                continue
            raise
        frames.append(df)
        checksums.append((m, digest))
        print(f"  [{i:3d}/{len(months)}] {m}  {len(df):3d} 根  sha256 ✓")

    if not frames:
        print("❌ 沒有取得任何資料")
        return 1

    out = pd.concat(frames, ignore_index=True).sort_values("open_time")
    before = len(out)
    out = out.drop_duplicates(subset="open_time").reset_index(drop=True)
    if len(out) != before:
        # 月封存之間理論上不重疊;真的重疊代表來源異常,必須講出來而不是安靜去重
        print(f"⚠️  移除 {before - len(out)} 筆重複時間戳 —— 月封存間不應重疊,請留意")

    out = out.rename(columns={"open_time": "date"})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)

    checksum_file = args.out.with_suffix(".sha256")
    checksum_file.write_text(
        "\n".join(f"{d}  {args.pair}-{args.timeframe}-{m}.zip" for m, d in checksums) + "\n"
    )

    span = pd.to_datetime(out["date"], unit="ms", utc=True)
    print(
        f"\n✅ {len(out)} 根 → {args.out}"
        f"\n   範圍:{span.min().date()} ~ {span.max().date()}"
        f"\n   各月校驗和:{checksum_file}"
        f"\n\n下一步(1.4 節的強制品質檢查):"
        f"\n   .venv/bin/python analysis/tools/ingest_market_data.py \\"
        f"\n       --pair {args.pair[:-4]}/{args.pair[-4:]} --input {args.out} \\"
        f"\n       --expected-start {months[0]}-01 --expected-end <該月最後一天>"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

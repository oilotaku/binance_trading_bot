"""
策略三(跨資產相對強度輪動)第 1 節 point-in-time universe 重建。

依 docs/strategy-3-beta-neutrality-precheck.md 第 1 節(方法論定案、不得因結果調整)
重建每季(1/1、4/1、7/1、10/1)的 Top-30 alt universe:

    1. 候選集合:GET https://api.binance.com/api/v3/exchangeInfo 取得目前所有
       USDT 現貨交易對,依文件第 1 節「2026-08-22 排除規則修正」排除:BTC 本身
       (beta 基準)、法幣對、黃金/貴金屬代幣、穩定幣(用過去 90 日價格穩定性
       point-in-time 判定,不是固定 ticker 清單)、包裝幣、槓桿代幣。
       ⚠️ 這是「現在」的名單,不是「歷史上曾經存在過」的完整名單 —— 已下市的
       交易對不會出現在這裡,這是本方法的已知限制,見腳本輸出與稽核報告揭露。

       2026-08-22 修正紀錄:原本用固定 ticker 清單排除穩定幣,實際執行後發現
       `EURUSDT`(法幣)、`PAXGUSDT`(黃金)、`USD1USDT`/`RLUSDUSDT`/`USDPUSDT`
       (規則核准時尚未出現或未列入的新穩定幣)都依原規則字面意義通過資格檢查、
       入選過 Top-30。這是候選集合建構後、迴歸執行前發現並修正的方法論缺口,
       詳見 docs/strategy-3-beta-neutrality-precheck.md 第 1 節的修正紀錄 callout。
    2. 上市月份代理指標:data.binance.vision 月封存(monthly klines)存在與否
       (HTTP 200 / 404)代理「該交易對在該月是否已上市」,月份精度對 180 天
       門檻的判斷已經足夠(不需要精確到日)。用二分搜尋定位每個候選的第一個
       有封存的月份,大幅降低請求數(相對逐月線性掃描)。
    3. 依文件第 1 節規則,每季重建:上市滿 180 天 + 過去 90 日平均每日成交額
       (USDT 計價,quote_volume 欄位)排名前 30。
    4. 輸出兩份稽核用資料檔(見 main() 結尾路徑):
       - 候選集合完整表(含排除原因、偵測到的上市月份)—— 進版控,供稽核。
       - 每季入選名單(交易對、排名、90 日均量、上市月份)—— 進版控,供下一步
         (beta 迴歸)讀取。
       中間下載的月封存快取(僅供本腳本計算用,不是正式資料集)落在
       analysis/artifacts/strategy3_universe_cache/,已在 .gitignore
       的 analysis/artifacts/ 規則下,不進版控 —— 可重新產生。

本腳本**不**執行迴歸或統計檢定,也**不**是正式資料匯入路徑 —— 正式匯入
(供 Freqtrade / 回測使用)一律另外走
analysis/tools/download_binance_vision.py → analysis/tools/ingest_market_data.py,
對本腳本篩出的最終聯集清單各自執行一次,見任務交付流程。

用法:
    .venv/bin/python analysis/tools/build_strategy3_universe.py
    .venv/bin/python analysis/tools/build_strategy3_universe.py --smoke-test 15
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 重用既有下載工具已經踩過坑、驗證過的「epoch 時間戳單位逐列正規化」邏輯
# (幣安 2025-01-01 把月封存時間戳從毫秒改成微秒)—— 不重寫一份可能不同步的複本。
from analysis.tools.download_binance_vision import (  # noqa: E402
    BINANCE_COLUMNS,
    _normalize_to_millis,
)

EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
ARCHIVE_BASE = "https://data.binance.vision/data/spot/monthly/klines"

CACHE_DIR = _REPO_ROOT / "analysis" / "artifacts" / "strategy3_universe_cache"
LISTING_CACHE_FILE = CACHE_DIR / "listing_months.json"
VOLUME_CACHE_DIR = CACHE_DIR / "volume"

OUT_CANDIDATES_CSV = _REPO_ROOT / "analysis" / "strategy3_candidate_listing_months.csv"
OUT_UNIVERSE_CSV = _REPO_ROOT / "analysis" / "strategy3_universe_quarterly.csv"

# --- docs/strategy-3-beta-neutrality-precheck.md 第 1 節,2026-08-22 修正後版本,原文照抄,不得修改 ---
# 包裝幣:窄且穩定的類別(市面上主流「幣安上市的 BTC/ETH 包裝版」就這幾個),
# 不像穩定幣持續有新品項出現,用固定清單維護足夠。
WRAPPED_TOKENS = {"WBTC", "WETH"}
# 黃金/貴金屬代幣:同樣是窄類別,且沒有像穩定幣「價格穩定性」那樣乾淨的統計代理指標
# 可以自動判定(貴金屬本身價格會波動,不能用「接近 $1」判斷)——用固定清單維護,
# 已知的就這兩個(PAXG、XAUT),若未來出現新的追蹤貴金屬代幣需要人工補列。
PRECIOUS_METAL_TOKENS = {"PAXG", "XAUT"}
# 法幣對:base 或 quote 為法定貨幣。ISO 4217 常見代碼裡,Binance 現貨歷史上出現過
# 或可能出現的部分,清單本身是封閉、緩慢變動的(不像穩定幣持續有新品項),用固定
# 清單維護是合理選擇。
FIAT_CURRENCY_CODES = {
    "EUR", "GBP", "TRY", "AUD", "BRL", "RUB", "UAH", "ZAR", "JPY", "CHF",
    "PLN", "RON", "CZK", "MXN", "COP", "ARS", "NGN", "IDR", "INR", "KRW",
    "VND", "THB", "PHP", "CAD", "NZD", "SEK", "NOK", "DKK", "HKD", "SGD",
    "ILS", "SAR", "AED",
}
# 穩定幣:**不**用固定 ticker 清單(2026-08-22 修正的核心),改用過去 90 日價格
# 穩定性 point-in-time 判定 —— 見 is_stablecoin_asof()。這裡只保留判定門檻常數。
STABLECOIN_PRICE_LOW = 0.99
STABLECOIN_PRICE_HIGH = 1.01
STABLECOIN_PRICE_STD_MAX = 0.01
STABLECOIN_MIN_DAYS = 30  # 資料天數低於此門檻視為無法可靠判定,保守起見不排除

MIN_LISTING_DAYS = 180
VOLUME_LOOKBACK_DAYS = 90
TOP_N = 30
STUDY_START = date(2020, 1, 1)
STUDY_END = date(2026, 7, 31)
# --- 以上為文件第 1 節規則,以下為本腳本執行細節,非文件規則本身 ---

# BTC 是本策略迴歸的 beta 基準本身(策略假設文件:「相對 BTC 過去 M 日報酬排名」),
# 不是候選 alt —— 2026-08-22 已正式補進文件第 1 節排除規則第 1 項,原本是本次
# 執行的合理詮釋,現已由專案負責人確認採納。
BENCHMARK_BASE_EXCLUDE = {"BTC"}

LISTING_SCAN_LOW = "2017-06"  # Binance 現貨最早 USDT 交易對(BTCUSDT,2017-08)之前的安全下界
LISTING_SCAN_HIGH = "2026-07"  # 與既有 BTC/ETH PROVENANCE 涵蓋範圍一致的最新完整月封存
REQUEST_DELAY_SEC = 0.05  # 合理間隔請求,不高並發轟炸;data.binance.vision 無官方 rate limit 文件


def _get(url: str, timeout: int = 30) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def _get_with_retry(url: str, timeout: int = 30, attempts: int = 3) -> bytes:
    """
    包一層有界重試(指數退避)。實測踩到的坑:smoke test 在下載第 2 個 symbol 的
    月封存 .CHECKSUM 時遇到 `TimeoutError: The read operation timed out`(socket
    底層逾時,不是乾淨的 HTTPError)——`_get()` 本身沒有重試,單次暫時性逾時就讓
    整支腳本崩潰。`month_exists()` 原本就有這一層保護,這裡補齊讓
    `fetch_month_ohlcv_volume()` 享有同樣的韌性,兩處共用同一份重試邏輯,不是
    各自維護一份可能不同步的複本。HTTP 404 不重試,直接往上拋給呼叫端判斷
    (呼叫端用 404 代表「該月無封存」,是預期中的正常分支,不是錯誤)。
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return _get(url, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_exc = e
            if attempt == attempts - 1:
                raise
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError("unreachable") from last_exc


def month_to_index(m: str) -> int:
    y, mo = map(int, m.split("-"))
    return y * 12 + mo


def index_to_month(idx: int) -> str:
    idx0 = idx - 1
    y, mo = divmod(idx0, 12)
    return f"{y:04d}-{mo + 1:02d}"


def month_exists(pair: str, month: str) -> bool:
    """
    月封存的 .CHECKSUM 是否存在(HTTP 200 / 404)—— 代理該月是否已上市。

    ⚠️ `pair` 必須先 URL 編碼:實測發現候選集合裡有 `币安人生USDT`(baseAsset 是
    中文,顯然是幣安上的一個異常/玩笑性質列表,非典型英數字代號)——Python 的
    http.client 預設把 request line 編碼為 ASCII,非 ASCII 字元會讓 urlopen()
    直接以 UnicodeEncodeError 崩潰,而不是回傳乾淨的 404。用
    urllib.parse.quote() 讓這類候選也能被正常判斷存在與否(多半會 404 或成交量
    極低而被排名自然篩掉),不需要為了這個個案另外手動列入排除清單。
    """
    safe_pair = urllib.parse.quote(pair, safe="")
    url = f"{ARCHIVE_BASE}/{safe_pair}/1d/{safe_pair}-1d-{month}.zip.CHECKSUM"
    try:
        _get_with_retry(url, timeout=20)
        time.sleep(REQUEST_DELAY_SEC)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            time.sleep(REQUEST_DELAY_SEC)
            return False
        raise


def detect_listing_month(pair: str) -> tuple[str | None, str]:
    """
    二分搜尋找出候選交易對第一個存在月封存的月份,以此代理上市月份。

    假設:存在性在 [LISTING_SCAN_LOW, LISTING_SCAN_HIGH] 區間內單調
    (先全 False 後全 True)。這個假設對「上市後持續交易至今」的交易對成立,
    但對「曾經下市又重新上市」的交易對可能不成立 —— 因此收斂後另外抽查
    往前 1、2 個月是否仍為 False,抽查失敗就整筆標記為需人工複查,不採信
    二分搜尋結果(寧可少一筆資料,不要一筆可能悄悄錯誤的資料)。
    """
    if not month_exists(pair, LISTING_SCAN_HIGH):
        return None, (
            f"{LISTING_SCAN_HIGH} 封存不存在 —— 可能於研究窗口結束後才上市,"
            "或封存尚未發布,不納入本次研究窗口"
        )

    if month_exists(pair, LISTING_SCAN_LOW):
        return LISTING_SCAN_LOW, (
            f"{LISTING_SCAN_LOW}(掃描下界)已存在封存 —— 實際上市月份可能早於"
            "掃描下界,本次未進一步往前探測(下界已早於 Binance 最早 USDT 交易對)"
        )

    lo, hi = month_to_index(LISTING_SCAN_LOW), month_to_index(LISTING_SCAN_HIGH)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if month_exists(pair, index_to_month(mid)):
            hi = mid
        else:
            lo = mid
    listing_month = index_to_month(hi)

    for back in (1, 2):
        check_idx = hi - back
        if check_idx < month_to_index(LISTING_SCAN_LOW):
            break
        if month_exists(pair, index_to_month(check_idx)):
            return None, (
                f"二分搜尋收斂於 {listing_month},但往前 {back} 個月"
                f"({index_to_month(check_idx)})仍偵測到封存 —— 疑似曾經下市又"
                "重新上市,違反二分搜尋的單調假設,標記為需人工複查,不採信此結果"
            )

    return listing_month, "二分搜尋 + 前 2 個月抽查通過"


def fetch_candidates() -> list[dict]:
    """
    取得候選集合起點:目前所有 USDT 現貨交易對,排除文件第 1 節(2026-08-22 修正版)
    的靜態類別:BTC 基準、法幣對、貴金屬代幣、包裝幣、槓桿代幣。

    ⚠️ 穩定幣**不**在這裡排除 —— 那是 point-in-time(每季重建日各自判定)的動態
    類別,必須用該季的過去 90 日價格資料才能判定,在這個階段(還沒抓任何價格
    資料)做不到,留給 build_universe() 在有 daily_series 之後逐季處理。

    ⚠️ 這是「現在」的名單 —— 歷史上曾經上市又下市的交易對不會出現在這裡,
    這是本方法的已知限制,不是本腳本能彌補的(見任務交付的稽核報告)。
    """
    raw = json.loads(_get(EXCHANGE_INFO_URL, timeout=30))
    out = []
    for s in raw["symbols"]:
        if s["quoteAsset"] != "USDT":
            continue
        if s["status"] != "TRADING":
            continue
        if not s.get("isSpotTradingAllowed"):
            continue
        base = s["baseAsset"]
        symbol = s["symbol"]
        if base in BENCHMARK_BASE_EXCLUDE:
            reason = "BTC 為迴歸 beta 基準本身,非 alt candidate(文件第 1 節排除規則第 1 項)"
        elif base in FIAT_CURRENCY_CODES:
            reason = "法幣對(文件第 1 節排除規則第 2 項)"
        elif base in PRECIOUS_METAL_TOKENS:
            reason = "黃金/貴金屬代幣(文件第 1 節排除規則第 3 項)"
        elif base in WRAPPED_TOKENS:
            reason = "包裝幣(文件第 1 節排除規則第 5 項)"
        elif base.endswith("UP") or base.endswith("DOWN"):
            reason = "槓桿代幣 *UP/*DOWN(文件第 1 節排除規則第 6 項)"
        else:
            reason = None
        out.append({"symbol": symbol, "base": base, "excluded_reason": reason})
    return out


def is_stablecoin_asof(daily: pd.DataFrame, as_of: date) -> tuple[bool, dict | None]:
    """
    文件第 1 節排除規則第 4 項:穩定幣用過去 90 日價格穩定性判定,point-in-time
    (每季各自判定,不是一次性、全域的 ticker 清單)—— 同一個 ticker 在不同季度
    可能今非昔比(例如脫錨後不再穩定),每季用該季的過去 90 日資料重新判定。

    資料不足 STABLECOIN_MIN_DAYS 天時視為「無法可靠判定」,回傳 False(保守起見
    不排除,寧可誤放行一個可能是穩定幣的候選,也不要誤排除一個資料不足但其實是
    正常波動 alt 的候選)。
    """
    if daily.empty:
        return False, None
    as_of_ts = pd.Timestamp(as_of, tz="UTC")
    window_start = as_of_ts - pd.Timedelta(days=VOLUME_LOOKBACK_DAYS)
    mask = (daily["date"] >= window_start) & (daily["date"] < as_of_ts)
    sub = daily.loc[mask]
    if len(sub) < STABLECOIN_MIN_DAYS:
        return False, None
    close = sub["close"].astype(float)
    lo, hi, std = float(close.min()), float(close.max()), float(close.std())
    is_stable = (lo >= STABLECOIN_PRICE_LOW) and (hi <= STABLECOIN_PRICE_HIGH) and (std < STABLECOIN_PRICE_STD_MAX)
    return is_stable, {"min": lo, "max": hi, "std": std, "n": len(sub)}


def load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def phase1_listing_months(candidates: list[dict]) -> dict:
    """對所有未被排除的候選跑上市月份偵測,結果落地快取,可中斷續跑。"""
    cache = load_json(LISTING_CACHE_FILE)
    todo = [c for c in candidates if c["excluded_reason"] is None and c["symbol"] not in cache]
    print(f"[phase1] 待偵測上市月份:{len(todo)} 個(已快取 {len(cache)} 個)")
    for i, c in enumerate(todo, 1):
        sym = c["symbol"]
        month, note = detect_listing_month(sym)
        cache[sym] = {"listing_month": month, "note": note}
        save_json(LISTING_CACHE_FILE, cache)
        print(f"  [{i:4d}/{len(todo)}] {sym:16s} -> {month}  ({note})")
    return cache


def quarter_dates() -> list[date]:
    out = []
    for year in range(STUDY_START.year, STUDY_END.year + 1):
        for month in (1, 4, 7, 10):
            d = date(year, month, 1)
            if STUDY_START <= d <= STUDY_END:
                out.append(d)
    return out


def is_eligible(listing_month: str, as_of: date) -> bool:
    y, mo = map(int, listing_month.split("-"))
    listed_since = date(y, mo, 1)
    return (as_of - listed_since).days >= MIN_LISTING_DAYS


def month_range_inclusive(start_month: str, end_month: str) -> list[str]:
    lo, hi = month_to_index(start_month), month_to_index(end_month)
    return [index_to_month(i) for i in range(lo, hi + 1)]


def fetch_month_ohlcv_volume(pair: str, month: str) -> pd.DataFrame | None:
    """
    下載單月封存,只保留 date/close/volume/quote_volume。逐檔 SHA256 驗證,
    與 download_binance_vision.py 同一套完整性標準,不因為「只是拿來排名用」
    就放鬆 —— 用未驗證的資料做排名一樣可能誤導最終 universe 組成。

    結果快取於 analysis/artifacts/strategy3_universe_cache/volume/,重跑本腳本
    不會重複下載。
    """
    cache_file = VOLUME_CACHE_DIR / pair / f"{month}.csv"
    if cache_file.exists():
        return pd.read_csv(cache_file, parse_dates=["date"])

    safe_pair = urllib.parse.quote(pair, safe="")
    name = f"{safe_pair}-1d-{month}.zip"
    url = f"{ARCHIVE_BASE}/{safe_pair}/1d/{name}"
    try:
        blob = _get_with_retry(url, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"    {pair} {month} 404 —— 該月無封存,略過(可能是延遲上市/提前下市)")
            time.sleep(REQUEST_DELAY_SEC)
            return None
        raise
    digest = hashlib.sha256(blob).hexdigest()
    expected = _get_with_retry(url + ".CHECKSUM", timeout=30).decode().split()[0]
    if digest != expected:
        raise RuntimeError(f"{name} SHA256 不符:官方 {expected}、實得 {digest}")

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        csv_name = z.namelist()[0]
        with z.open(csv_name) as f:
            head = f.read(64)
        with z.open(csv_name) as f:
            has_header = head.lower().startswith(b"open_time")
            df = pd.read_csv(
                f, header=0 if has_header else None,
                names=None if has_header else BINANCE_COLUMNS,
            )
    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df[["open_time", "close", "volume", "quote_volume"]].copy()
    df["open_time"] = _normalize_to_millis(df["open_time"])
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.normalize()
    df = df[["date", "close", "volume", "quote_volume"]]

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_file, index=False)
    time.sleep(REQUEST_DELAY_SEC)
    return df


def load_symbol_daily_series(pair: str, listing_month: str) -> pd.DataFrame:
    months = month_range_inclusive(listing_month, LISTING_SCAN_HIGH)
    frames = []
    for m in months:
        df = fetch_month_ohlcv_volume(pair, m)
        if df is not None:
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["date", "close", "volume", "quote_volume"])
    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset="date").sort_values("date")
    return out.reset_index(drop=True)


def avg_quote_volume_90d(daily: pd.DataFrame, as_of: date) -> tuple[float | None, int]:
    """過去 90 日平均每日成交額(USDT 計價),嚴格早於 as_of(不看重建日當天的資料)。"""
    if daily.empty:
        return None, 0
    as_of_ts = pd.Timestamp(as_of, tz="UTC")
    window_start = as_of_ts - pd.Timedelta(days=VOLUME_LOOKBACK_DAYS)
    mask = (daily["date"] >= window_start) & (daily["date"] < as_of_ts)
    sub = daily.loc[mask]
    if sub.empty:
        return None, 0
    return float(sub["quote_volume"].mean()), len(sub)


def build_universe(candidates: list[dict], listing_cache: dict, smoke_test: int | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    eligible_symbols = {
        c["symbol"]
        for c in candidates
        if c["excluded_reason"] is None
        and listing_cache.get(c["symbol"], {}).get("listing_month") is not None
    }

    quarters = quarter_dates()
    # 找出每個候選在「任一季」符合 180 天門檻的集合 —— 只有這些才需要下載成交量資料
    needed: dict[str, str] = {}  # symbol -> listing_month
    for sym in eligible_symbols:
        lm = listing_cache[sym]["listing_month"]
        if any(is_eligible(lm, q) for q in quarters):
            needed[sym] = lm

    if smoke_test is not None:
        needed = dict(list(needed.items())[:smoke_test])
        print(f"[smoke-test] 只處理前 {len(needed)} 個 symbol 的成交量資料")

    print(f"[phase2] 需要下載成交量歷史的 symbol 數:{len(needed)}")

    daily_series: dict[str, pd.DataFrame] = {}
    for i, (sym, lm) in enumerate(sorted(needed.items()), 1):
        print(f"  [{i:4d}/{len(needed)}] {sym:16s} 上市月份 {lm} -> 下載歷史成交量...")
        daily_series[sym] = load_symbol_daily_series(sym, lm)

    stablecoin_exclusions: dict[str, int] = {}  # symbol -> 被判定為穩定幣而排除的季數
    rows = []
    for q in quarters:
        ranked = []
        stable_this_quarter = []
        for sym, lm in needed.items():
            if not is_eligible(lm, q):
                continue
            is_stable, stable_info = is_stablecoin_asof(daily_series[sym], q)
            if is_stable:
                stablecoin_exclusions[sym] = stablecoin_exclusions.get(sym, 0) + 1
                stable_this_quarter.append((sym, stable_info))
                continue
            avg_vol, n_days = avg_quote_volume_90d(daily_series[sym], q)
            if avg_vol is None:
                continue
            ranked.append(
                {
                    "quarter": q.isoformat(),
                    "symbol": sym,
                    "listing_month": lm,
                    "days_listed": (q - date(*map(int, lm.split("-")), 1)).days,
                    "avg_90d_quote_volume_usdt": avg_vol,
                    "volume_days_available": n_days,
                }
            )
        ranked.sort(key=lambda r: r["avg_90d_quote_volume_usdt"], reverse=True)
        for rank, r in enumerate(ranked[:TOP_N], 1):
            r["rank"] = rank
            rows.append(r)
        if stable_this_quarter:
            print(
                f"[quarter {q.isoformat()}] 依過去 90 日價格穩定性排除穩定幣 "
                f"{len(stable_this_quarter)} 個:"
                + ", ".join(f"{s}(std={info['std']:.4f})" for s, info in stable_this_quarter)
            )
        print(
            f"[quarter {q.isoformat()}] 符合 180 天門檻且有成交量資料的候選數:{len(ranked)}"
            f"{'  ⚠️ 不足 30 檔' if len(ranked) < TOP_N else ''}"
        )

    universe_df = pd.DataFrame(rows, columns=[
        "quarter", "rank", "symbol", "listing_month", "days_listed",
        "avg_90d_quote_volume_usdt", "volume_days_available",
    ])

    audit_rows = []
    for c in candidates:
        sym = c["symbol"]
        info = listing_cache.get(sym, {})
        audit_rows.append({
            "symbol": sym,
            "base": c["base"],
            "excluded_reason": c["excluded_reason"] or "",
            "listing_month": info.get("listing_month") or "",
            "listing_detection_note": info.get("note") or "",
            "ever_eligible_in_study_window": sym in needed,
            # point-in-time 穩定幣判定(文件第 1 節排除規則第 4 項)排除掉該 symbol
            # 的季數 —— 跟 excluded_reason 不同,這是動態、逐季判定的結果,不是
            # 候選層級的單一靜態原因,所以獨立成一欄,而不是塞進 excluded_reason。
            "stablecoin_excluded_quarters": stablecoin_exclusions.get(sym, 0),
        })
    audit_df = pd.DataFrame(audit_rows)

    return universe_df, audit_df


def main() -> int:
    p = argparse.ArgumentParser(description="策略三 point-in-time universe 重建(文件第 1 節)")
    p.add_argument("--smoke-test", type=int, default=None, help="只處理前 N 個 symbol 的成交量下載,快速驗證規則跑得通")
    p.add_argument("--skip-listing", action="store_true", help="略過 phase1(沿用既有快取,不重新偵測上市月份)")
    args = p.parse_args()

    print("=== 策略三 Point-in-Time Universe 重建 ===")
    print(f"研究窗口:{STUDY_START} ~ {STUDY_END}\n")

    candidates = fetch_candidates()
    excluded = [c for c in candidates if c["excluded_reason"]]
    print(f"候選集合(目前 USDT 現貨交易對):{len(candidates)}")
    print(f"  其中靜態排除:{len(excluded)}(BTC 基準/法幣對/貴金屬代幣/包裝幣/槓桿代幣)")
    print("  穩定幣改用 point-in-time 價格穩定性判定,不在此階段排除,見逐季輸出")
    print(f"  進入上市月份偵測:{len(candidates) - len(excluded)}\n")

    if args.skip_listing:
        listing_cache = load_json(LISTING_CACHE_FILE)
    else:
        listing_cache = phase1_listing_months(candidates)

    universe_df, audit_df = build_universe(candidates, listing_cache, args.smoke_test)

    OUT_CANDIDATES_CSV.parent.mkdir(parents=True, exist_ok=True)
    audit_df.to_csv(OUT_CANDIDATES_CSV, index=False, quoting=csv.QUOTE_MINIMAL)
    universe_df.to_csv(OUT_UNIVERSE_CSV, index=False, quoting=csv.QUOTE_MINIMAL)

    print(f"\n✅ 候選集合稽核表 -> {OUT_CANDIDATES_CSV}")
    print(f"✅ 每季 universe -> {OUT_UNIVERSE_CSV}")
    if not universe_df.empty:
        final_symbols = sorted(universe_df["symbol"].unique())
        print(f"\n所有季度聯集(供正式 ingest 的最終清單,共 {len(final_symbols)} 檔):")
        print(", ".join(final_symbols))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
策略三 Beta 中性化前置檢驗 —— 依 docs/strategy-3-beta-neutrality-precheck.md
第 2、3 節方法論,單次執行、結果留痕(第 4 節「一次性判定原則」)。

事前指定的參數(第 2 節,不做搜尋):
    M = 90 天(動能回顧窗口,與 universe 90 日成交額排名窗口一致)
    N = 30 天(再平衡週期)
    K = 5 檔(Top-30 universe 的前五分之一)

執行邏輯(第 3 節):
    1. 每 N=30 日重建一次 Top-K=5 alt basket。候選池是當下生效的季度 Top-30
       universe(analysis/strategy3_universe_quarterly.csv,由
       analysis/tools/build_strategy3_universe.py 產出),依「過去 M=90 日累計
       報酬 減去 BTC 同期累計報酬」(相對強度)排序,取前 5。
    2. Top-5 等權重買入,之後用每日收盤價 mark-to-market(不是每 30 日才算一次
       區塊報酬)——非再平衡日之間權重隨價格自然漂移,只在下次 30 日再平衡時
       用當時的總資產淨值重新等權重買入新一批 Top-5。
    3. 對 r_p,t 與 BTC/USDT 現貨日報酬 r_BTC,t 做單因子迴歸,標準誤重用
       analysis/sharpe_difference.py 已驗證過的 Newey-West HAC 長期共變異數
       估計(Bartlett 核,lag = floor(4*(T/100)**(2/9)) 自動規則),不重新發明
       一套可能不一致的實作。

強制平倉的處理(對應 universe 建構階段 FTT/LUNA/CVC 的處理):
    這三檔在各自的最後一個有效交易日之後,user_data/data/binance/ 的資料**刻意
    截斷**(不含停牌期間、也不含之後恢復交易的資料,見 PROVENANCE.md)。這裡的
    basket 引擎完全不需要為此另外寫特殊分支 —— 因為資料本身已經截斷,
    `price.asof(d)` 在最後一個有效交易日之後,自然只能拿到「最後一個有效交易日」
    的價格(沒有更晚的資料可拿),這根股票的份額對 NAV 的貢獻從那天起自動凍結,
    等於「轉現金、貢獻 0 報酬」——不會、也不可能意外撿到停牌期間或恢復交易後的
    價格。這正是「強制平倉」在報酬計算上該有的效果:最後一個交易日當天的報酬
    (可能是崩盤當天的巨幅虧損)正常計入,之後的每一天不再有新的損益,直到下次
    30 日再平衡把全部資產(含這筆被動凍結的現金)重新等權重投入新一批 Top-5。

⚠️ 本檢驗只執行一次。以下操作性選擇是在對任何真實資料執行前、看到任何
α/β/p 之前寫定的(commit 時間戳為證),不因結果而回頭調整,寫在這裡以便稽核:
    - 相對強度定義:M 日累計報酬的「差」(alt 減 BTC),不是比值 —— 兩者在
      「取排序」這件事上單調等價(排序不受影響),差值運算更直覺、對 BTC
      接近 0 報酬時也不會有除零疑慮。
    - 再平衡錨點:2020-01-01(universe 研究窗口起點),往後每 30 個日曆日一次
      (不是每個月的第幾天,單純固定 30 天間隔)。
    - 候選資格:該次再平衡當下,必須有 >= M 天連續價格資料可算動能、且當下
      仍有有效收盤價(尚未被強制平倉)才能入選 Top-5。
    - 若候選不足 5 檔(理論上不該發生,universe 應永遠有 30 檔候選),用實際
      檔數等權重,不強行湊滿 5 檔。
    - 若完全沒有候選可選(更不該發生),該期空手(NAV 持平,不产生報酬)。

用法:
    .venv/bin/python analysis/tools/strategy3_beta_precheck.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 重用既有、已在 CP 流程下驗證過的 Newey-West HAC 長期共變異數估計(Bartlett 核)——
# 文件第 3 節明文要求「比照 analysis/sharpe_difference.py 已在用的...規則」,
# 不重新發明一套可能與既有實作不一致的版本。
from analysis.sharpe_difference import _hac_covariance  # noqa: E402

DATADIR = _REPO_ROOT / "user_data" / "data" / "binance"
UNIVERSE_CSV = _REPO_ROOT / "analysis" / "strategy3_universe_quarterly.csv"
HOLDINGS_LOG_CSV = _REPO_ROOT / "analysis" / "strategy3_basket_holdings_log.csv"
RESULTS_DOC = _REPO_ROOT / "docs" / "strategy-3-beta-neutrality-results.md"

M_LOOKBACK_DAYS = 90
N_REBALANCE_DAYS = 30
K_HOLDINGS = 5
STUDY_START = date(2020, 1, 1)
STUDY_END = date(2026, 7, 31)
ANNUALIZATION_DAYS = 365  # 對齊本專案既有「全年 365 天」慣例
ALPHA_SIG_LEVEL = 0.05


def load_price_series(base: str) -> pd.Series:
    """讀單一 symbol 的收盤價序列(index = tz-aware UTC 日期,已正規化到日界)。"""
    path = DATADIR / f"{base}_USDT-1d.feather"
    if not path.exists():
        return pd.Series(dtype=float)
    df = pd.read_feather(path)
    s = df.set_index("date")["close"].astype(float)
    s.index = pd.to_datetime(s.index, utc=True).normalize()
    return s.sort_index()


def load_universe() -> pd.DataFrame:
    df = pd.read_csv(UNIVERSE_CSV)
    df["quarter"] = pd.to_datetime(df["quarter"], utc=True)
    return df


def active_quarter(quarters: np.ndarray, as_of: pd.Timestamp) -> pd.Timestamp:
    """截至 as_of 生效的季度重建日 —— 最近一個 <= as_of 的 quarter,point-in-time。"""
    eligible = quarters[quarters <= as_of]
    return eligible.max()


def rebalance_dates() -> list[pd.Timestamp]:
    out = []
    d = pd.Timestamp(STUDY_START, tz="UTC")
    end = pd.Timestamp(STUDY_END, tz="UTC")
    while d <= end:
        out.append(d)
        d = d + pd.Timedelta(days=N_REBALANCE_DAYS)
    return out


def momentum_asof(price: pd.Series, as_of: pd.Timestamp, lookback_days: int) -> float | None:
    """
    過去 lookback_days 天累計報酬,point-in-time:只用截至 as_of **前一天**收盤為止
    的資料,不看 as_of 當天(重建日當天的收盤價尚未確定/不該用來決定當天的排名)。

    兩端都必須真的落在該 symbol 資料涵蓋範圍內才計算,否則回傳 None(資料不足,
    保守排除,不臆測、不外推)。
    """
    if price.empty:
        return None
    end_ts = as_of - pd.Timedelta(days=1)
    start_ts = as_of - pd.Timedelta(days=lookback_days + 1)
    if price.index.min() > start_ts or price.index.max() < end_ts:
        return None
    p_end = price.asof(end_ts)
    p_start = price.asof(start_ts)
    if pd.isna(p_end) or pd.isna(p_start) or p_start <= 0:
        return None
    return float(p_end / p_start - 1.0)


def mark_to_market(shares: dict[str, float], prices: dict[str, pd.Series], as_of: pd.Timestamp) -> float:
    """
    用 as_of 當天收盤價計算持倉總市值。

    對已強制平倉(資料截斷)的標的,`price.asof(as_of)` 只會拿到最後一個有效
    交易日的價格,該部位價值從此凍結 —— 等同於在最後成交價轉現金,不外推、
    不回填(見本模組開頭「強制平倉的處理」)。
    """
    total = 0.0
    for base, sh in shares.items():
        px = prices[base].asof(as_of)
        if pd.isna(px):
            continue  # 防禦性:進場時已驗證過該標的當下有有效收盤價
        total += sh * float(px)
    return total


def build_basket(universe: pd.DataFrame, prices: dict[str, pd.Series], btc_price: pd.Series) -> tuple[pd.Series, list[dict]]:
    """
    建構 Top-5 alt basket 的每日 NAV 序列(mark-to-market)。

    :return: (nav_series, holdings_log) —— holdings_log 是每次再平衡選中的
        5 檔與相對強度分數,供稽核與交付報告使用。
    """
    dates_all = btc_price.index
    dates_all = dates_all[
        (dates_all >= pd.Timestamp(STUDY_START, tz="UTC")) & (dates_all <= pd.Timestamp(STUDY_END, tz="UTC"))
    ].sort_values()

    quarters = universe["quarter"].unique()
    rebals = rebalance_dates()

    nav: dict[pd.Timestamp, float] = {}
    holdings_log: list[dict] = []
    current_nav = 1.0
    prev_shares: dict[str, float] = {}  # base -> 份額,上一期持倉,跨再平衡日結轉

    for k, t_k in enumerate(rebals):
        t_next = rebals[k + 1] if k + 1 < len(rebals) else (pd.Timestamp(STUDY_END, tz="UTC") + pd.Timedelta(days=1))
        period_dates = dates_all[(dates_all >= t_k) & (dates_all < t_next)]
        if len(period_dates) == 0:
            continue

        # 換倉實際發生的交易日:t_k 當天(資料完整時 mark_date == t_k;若 t_k
        # 剛好沒有 K 棒,順延到該期第一個有交易的日子,舊持倉的 mark-to-market
        # 與新持倉的進場價一律用同一天,避免兩邊取到不同日期的價格)。
        mark_date = period_dates[0]

        q = active_quarter(quarters, t_k)
        pool = universe.loc[universe["quarter"] == q, "symbol"].tolist()

        btc_mom = momentum_asof(btc_price, t_k, M_LOOKBACK_DAYS)

        scored = []
        if btc_mom is not None:
            for sym in pool:
                base = sym[:-4]
                price = prices.get(base, pd.Series(dtype=float))
                if price.empty:
                    continue
                mom = momentum_asof(price, t_k, M_LOOKBACK_DAYS)
                if mom is None:
                    continue
                entry_price = price.asof(mark_date)
                if pd.isna(entry_price):
                    continue
                scored.append((sym, base, mom - btc_mom, float(entry_price)))

        scored.sort(key=lambda x: x[2], reverse=True)
        top = scored[:K_HOLDINGS]

        holdings_log.append({
            "rebalance_date": t_k.date().isoformat(),
            "active_quarter": q.date().isoformat(),
            "n_pool": len(pool),
            "n_scored_candidates": len(scored),
            "n_selected": len(top),
            "btc_momentum_90d": btc_mom,
            "selected_symbols": ",".join(s for s, b, rm, ep in top),
            "selected_relative_momentum": ",".join(f"{rm:.4f}" for s, b, rm, ep in top),
        })

        # ── 再平衡日的基差(2026-08-22 修正,quant-analyst 獨立覆核發現)──────
        # 舊版在這裡直接用「前一天的 NAV」除以「當天的進場價」算份額,於是
        #     NAV[t_k] = Σ_i (NAV_{t_k-1} / n / ep_i) · ep_i ≡ NAV_{t_k-1}
        # 恆等成立,把再平衡當天的報酬**強制歸零**(81 期中有 80 天
        # |r_p| < 1e-15,對照非再平衡日 |r_p| 中位數 2.59%),等於每 30 天
        # 被迫空手 1 天。這既不符合 precheck 文件第 3 節「連續日報酬序列」,
        # 也與本函式 docstring 宣稱的「用**當時的**總資產淨值」矛盾。
        # 正確做法:再平衡日先用**舊持倉**在當天收盤 mark-to-market,得到當天
        # 真實 NAV,再用這個 NAV 換倉買進新一批 Top-5。
        if prev_shares:
            current_nav = mark_to_market(prev_shares, prices, mark_date)
            nav[mark_date] = current_nav
            hold_dates = period_dates[1:]
        else:
            hold_dates = period_dates  # 第一期沒有舊持倉,mark_date 就是建倉日

        if not top:
            for d in hold_dates:
                nav[d] = current_nav
            prev_shares = {}
            continue

        n = len(top)
        shares = {base: (current_nav / n) / ep for sym, base, rm, ep in top}

        for d in hold_dates:
            total = mark_to_market(shares, prices, d)
            nav[d] = total
            current_nav = total

        prev_shares = shares

    nav_series = pd.Series(nav).sort_index()
    return nav_series, holdings_log


def hac_regression(r_p: np.ndarray, r_btc: np.ndarray) -> dict:
    """
    r_p,t = alpha + beta * r_btc,t + eps_t,Newey-West HAC 標準誤。

    推導(標準 GMM/sandwich 結構,與 sharpe_difference.py 的 HAC 用法一致):
        beta_hat = (X'X)^-1 X'y
        score g_t = X_t * u_t(u_t = 殘差),g 的長期共變異數 S 用
        analysis.sharpe_difference._hac_covariance(g, lag=L) 估計
        (Bartlett 核,截斷 lag L)。
        Avar(beta_hat) = T * (X'X)^-1 @ S @ (X'X)^-1
        —— 這是標準 sandwich 公式;代入 iid 同質變異的退化情形可驗證化簡回
        普通 OLS 的 sigma^2 (X'X)^-1,見開發時的推導檢查(不寫入正式檔案,
        避免與程式碼本身重複)。
    """
    T = len(r_p)
    X = np.column_stack([np.ones(T), r_btc])
    XtX = X.T @ X
    XtX_inv = np.linalg.inv(XtX)
    beta_hat = XtX_inv @ (X.T @ r_p)
    alpha, beta = float(beta_hat[0]), float(beta_hat[1])
    resid = r_p - X @ beta_hat

    L = int(np.floor(4 * (T / 100) ** (2 / 9)))
    g = X * resid[:, None]
    S = _hac_covariance(g, lag=L)
    var_beta_hat = T * (XtX_inv @ S @ XtX_inv)
    se_alpha = float(np.sqrt(var_beta_hat[0, 0])) if var_beta_hat[0, 0] > 0 else float("nan")
    se_beta = float(np.sqrt(var_beta_hat[1, 1])) if var_beta_hat[1, 1] > 0 else float("nan")

    t_alpha = alpha / se_alpha if np.isfinite(se_alpha) and se_alpha > 0 else float("nan")
    p_hac = float(2 * (1 - stats.norm.cdf(abs(t_alpha)))) if np.isfinite(t_alpha) else float("nan")

    alpha_annual = alpha * ANNUALIZATION_DAYS
    passed = bool(np.isfinite(p_hac) and alpha_annual > 0 and p_hac < ALPHA_SIG_LEVEL)

    return {
        "T": T, "hac_lag_L": L,
        "alpha_daily": alpha, "alpha_annual": alpha_annual,
        "se_hac_alpha_daily": se_alpha, "t_hac_alpha": t_alpha, "p_hac": p_hac,
        "beta": beta, "se_hac_beta": se_beta,
        "passed": passed,
    }


def main() -> int:
    print("=== 策略三 Beta 中性化前置檢驗(單次執行)===")
    universe = load_universe()
    btc_price = load_price_series("BTC")
    if btc_price.empty:
        print("❌ 找不到 BTC 價格資料")
        return 1

    all_symbols = sorted(universe["symbol"].unique())
    print(f"universe 聯集 symbol 數:{len(all_symbols)}")
    prices = {sym[:-4]: load_price_series(sym[:-4]) for sym in all_symbols}
    missing = [b for b, s in prices.items() if s.empty]
    if missing:
        print(f"⚠️ 以下 base 找不到價格資料,將在動能排名中自動被排除:{missing}")

    nav_series, holdings_log = build_basket(universe, prices, btc_price)

    pd.DataFrame(holdings_log).to_csv(HOLDINGS_LOG_CSV, index=False)
    print(f"✅ 每期持倉稽核記錄 -> {HOLDINGS_LOG_CSV}")

    r_p = nav_series.pct_change().dropna()
    r_btc_full = btc_price.reindex(nav_series.index).pct_change().dropna()
    common = r_p.index.intersection(r_btc_full.index)
    r_p_arr = r_p.loc[common].to_numpy(dtype=float)
    r_btc_arr = r_btc_full.loc[common].to_numpy(dtype=float)

    print(f"迴歸樣本數 T = {len(r_p_arr)}(日報酬,{common.min().date()} ~ {common.max().date()})")

    result = hac_regression(r_p_arr, r_btc_arr)

    print("\n=== 迴歸結果 ===")
    for k, v in result.items():
        print(f"  {k}: {v}")

    n_rebalances = len(holdings_log)
    n_selected_lt5 = sum(1 for h in holdings_log if h["n_selected"] < K_HOLDINGS)
    n_empty = sum(1 for h in holdings_log if h["n_selected"] == 0)
    print(f"\n再平衡次數:{n_rebalances}(其中入選 < 5 檔:{n_selected_lt5},完全空手:{n_empty})")

    result["n_rebalances"] = n_rebalances
    result["n_rebalances_lt5_holdings"] = n_selected_lt5
    result["n_rebalances_empty"] = n_empty
    result["sample_start"] = str(common.min().date())
    result["sample_end"] = str(common.max().date())

    import json
    (Path(HOLDINGS_LOG_CSV).parent / "strategy3_regression_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(f"\n✅ 迴歸結果 JSON -> {Path(HOLDINGS_LOG_CSV).parent / 'strategy3_regression_result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

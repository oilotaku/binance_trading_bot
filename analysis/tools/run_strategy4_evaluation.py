"""
策略二(波動度目標化)的正式評估。

規格全部來自已核准文件,本腳本不含任何可調整的東西:
    CP-004  目標(MDD ≤ 30%、保留報酬 ≥ 34.0%、第二層 Δ ≥ 0.78)
    CP-005  風控(曝險上限 0.8、回撤斜坡 30%→40%、再平衡帶 20%)
    strategy-4-hypothesis.md  參數(W=20、σ_target=25%)

用法:
    .venv/bin/python analysis/tools/run_strategy2_evaluation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis import benchmark as bm  # noqa: E402
from analysis import sharpe_difference as sdiff  # noqa: E402
from analysis import vol_target as vt  # noqa: E402

DATADIR = _REPO_ROOT / "user_data" / "data" / "binance"


def main() -> int:
    bench = bm.benchmark_log_returns(DATADIR).to_numpy()
    b = bm.performance_summary(bench)

    print("=" * 70)
    print("策略二評估 — 波動度目標化(CP-004 / CP-005 規格)")
    print("=" * 70)
    print(f"基準(每日再平衡等權 50/50):n={b['n']}  CAGR {b['cagr']:.2%}  "
          f"Sharpe {b['sharpe']:.4f}  MDD {b['max_drawdown']:.2%}")

    strat = vt.simulate(bench)
    s = bm.performance_summary(strat["returns"])

    # CP-006:對照組的曝險對齊策略**實際實現**的平均曝險。
    # 假說第 3 節寫的就是「在相同的平均曝險下」比較;第一次執行用
    # 「MDD上限/基準MDD」推算 w0 既算錯了(回撤在對數空間才線性),
    # 也讓兩組的風險水位差了 7 倍,那不是同一個比較。
    w0 = strat["mean_exposure"]
    print(f"對照組曝險 = 策略實現平均曝險 = {w0:.4f}\n")

    ctrl = vt.zero_skill_control(bench, w0)["returns"]
    c = bm.performance_summary(ctrl)

    print("-" * 70)
    print(f"{'':22}{'零技巧對照組':>16}{'波動度目標化':>18}")
    print("-" * 70)
    rows = [
        ("年化報酬", f"{c['cagr']:.2%}", f"{s['cagr']:.2%}"),
        ("最大回撤", f"{c['max_drawdown']:.2%}", f"{s['max_drawdown']:.2%}"),
        ("Sharpe", f"{c['sharpe']:.4f}", f"{s['sharpe']:.4f}"),
        ("平均曝險", f"{w0:.4f}", f"{strat['mean_exposure']:.4f}"),
        ("保留報酬比", f"{c['cagr'] / b['cagr']:.1%}", f"{s['cagr'] / b['cagr']:.1%}"),
        ("報酬/回撤", f"{c['cagr'] / c['max_drawdown']:.3f}",
         f"{s['cagr'] / s['max_drawdown']:.3f}"),
    ]
    for name, a, d in rows:
        print(f"{name:22}{a:>16}{d:>18}")
    print(f"{'總換手':22}{'0.00':>16}{strat['total_turnover']:>18.2f}")

    # ---- CP-004 第一層 ----
    mdd_ok = s["max_drawdown"] < vt.DD_RAMP_START
    beats_mdd = s["max_drawdown"] < c["max_drawdown"]
    beats_ret = s["cagr"] > c["cagr"]
    print("\n" + "=" * 70)
    print("第一層(必達,不宣稱 edge)")
    print("=" * 70)
    print(f"  (a) 絕對約束:MDD {s['max_drawdown']:.2%} ≤ 30%          "
          f"{'✅' if mdd_ok else '❌'}")
    print(f"  (b) 同曝險下優於零技巧:")
    print(f"      MDD      {s['max_drawdown']:.2%} vs {c['max_drawdown']:.2%}   "
          f"{'✅' if beats_mdd else '❌'}")
    print(f"      年化報酬 {s['cagr']:.2%} vs {c['cagr']:.2%}   "
          f"{'✅' if beats_ret else '❌'}")
    print(f"  → 假說預測「相同平均曝險下 MDD 更低且/或報酬更高」:"
          f"{'✅ 成立' if (beats_mdd or beats_ret) else '❌ 被推翻'}")

    # ---- CP-004 第二層 ----
    rho = float(np.corrcoef(strat["returns"], bench)[0, 1])
    test = sdiff.sharpe_difference_test(strat["returns"], bench, n_boot=5000)
    delta_ann = test["delta"] * np.sqrt(365)
    se_ann = test["se_hac"] * np.sqrt(365)
    required = sdiff.minimum_detectable_difference(se_ann, n_trials=13)  # CP-006

    print("\n" + "=" * 70)
    print("第二層(可選,宣稱 edge)")
    print("=" * 70)
    print(f"  與基準相關 ρ = {rho:.4f}   (CP-004 設計約束要求 ≥ 0.7 "
          f"{'✅' if rho >= 0.7 else '❌'})")
    print(f"  Sharpe 差距 Δ = {delta_ann:+.4f}(年化)")
    print(f"  HAC 標準誤     = {se_ann:.4f}")
    print(f"  bootstrap p    = {test['p_boot']:.4f}")
    print(f"  N=13 所需門檻  = {required:.4f}")
    print(f"  → {'✅ 通過' if delta_ann >= required else '❌ 不通過'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

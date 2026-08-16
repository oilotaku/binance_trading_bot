"""
CP-007:用校準期(與策略四評估期不重疊)的已實現波動,反推凸性修正後的 SIGMA_TARGET。

背景:原本的 SIGMA_TARGET 假設 E[σ_target/σ̂] ≈ σ_target/E[σ̂],忽略了 1/x 是凸函數
(Jensen 不等式),導致實現平均曝險系統性偏高(見 docs/strategy-4-results.md 第 3.1 節)。
CP-006 明文禁止直接用「拿來判定 MDD≤30% 的同一段樣本」反推校準常數
(docs/strategy-4-results.md 第 3.2 節)——那等於先射箭再畫靶。

CP-007 第 6 節(quant-mathematician 設計)的解法:切出一段跟正式判定樣本
**不重疊**的校準期,只用這段期間的已實現波動分布估計 E[1/σ̂],藉此推出
SIGMA_TARGET,而不去看評估期(判定期)的任何結果。

    校準期 = 2017-11-01 ~ 2020-07-27(約 1000 個交易日,由 n_eff≥50 精度需求反推,
             不是挑選出來讓評估期結果好看的日期)
    w0 = 0.1661   （MDD 30% 換算的零技巧固定曝險,對數空間正確值,
                     見 docs/strategy-4-run-1-invalid.md 第 1 節；CP-004 已核准,
                     沿用不重新推導,避免另開一個未經核准的設計分支）
    SIGMA_TARGET = w0 / E[1/σ̂]_校準期

本工具只負責印出推導過程與結果,**不會自動寫回 analysis/vol_target.py**——
比照 CP-006 的既有作法,治理數字是寫死在程式碼裡、有推導紀錄的常數,不是
執行時動態算出來的優化結果,寫回動作必須是一次可審閱的獨立 commit。

用法:
    .venv/Scripts/python.exe analysis/tools/calibrate_sigma_target.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis import benchmark as bm  # noqa: E402
from analysis import vol_target as vt  # noqa: E402

DATADIR = _REPO_ROOT / "user_data" / "data" / "binance"

CALIBRATION_START = "2017-11-01"  # 資料起點,見 user_data/data/binance/PROVENANCE.md
W0 = 0.1661  # CP-004 零技巧固定曝險(對數空間),沿用既有核准值,不在此重新推導


def main() -> int:
    bench = bm.benchmark_log_returns(DATADIR)
    calib = bench.loc[CALIBRATION_START:vt.CALIBRATION_END]
    print(f"校準期:{calib.index.min():%Y-%m-%d} ~ {calib.index.max():%Y-%m-%d}  n={len(calib)}")

    vol = vt.realized_volatility(calib.to_numpy(), window=vt.W)
    valid = vol[np.isfinite(vol)]
    inv_mean = float(np.mean(1.0 / valid))
    sigma_target = W0 / inv_mean

    print(f"有效波動觀測數:{len(valid)}(約 n_eff≈{len(valid) / vt.W:.0f} 個非重疊區塊)")
    print(f"E[σ̂]_校準期  = {float(np.mean(valid)):.4f}")
    print(f"E[1/σ̂]_校準期 = {inv_mean:.4f}")
    print(f"1/E[σ̂]        = {1.0 / float(np.mean(valid)):.4f}  (凸性偏誤對照,恆 < E[1/σ̂])")
    print()
    print(f"SIGMA_TARGET = w0 / E[1/σ̂]_校準期 = {W0} / {inv_mean:.4f} = {sigma_target:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

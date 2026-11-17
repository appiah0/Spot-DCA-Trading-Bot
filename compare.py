"""Compare several settings on BTC and ETH in one go. Run in Colab: python compare.py"""
import time
from strategy import CFG, make_exchange, fetch_candles, add_indicators
import backtest

SYMBOLS = ["BTC/USDT", "ETH/USDT"]
DAYS = 2000
VARIANTS = {
    "default (hot=0.5)": {},
    "no selling": {"tp_pct": 99},
    "hot=1.0 (buy normal when hot)": {"hot_mult": 1.0},
    "hot=1.0 + no selling": {"hot_mult": 1.0, "tp_pct": 99},
    "hot=0 (skip when hot)": {"hot_mult": 0.0},
}

ex = make_exchange(CFG["data_exchange"])
pages = DAYS * 24 // backtest.HRS // 1000 + 2
since = int((time.time() - DAYS * 86400) * 1000)
base = dict(CFG)
W = backtest.WARMUP
for sym in SYMBOLS:
    df = add_indicators(fetch_candles(ex, sym, CFG["timeframe"], since, 1000, pages))
    cut = int(len(df) * 0.6)
    parts = {"train": df.iloc[:cut].reset_index(drop=True),
             "test": df.iloc[cut - W:].reset_index(drop=True),
             "full": df}
    for vname, over in VARIANTS.items():
        CFG.update(base)
        CFG.update(over)
        print(f"\n{sym} | {vname}")
        for pname, part in parts.items():
            p, s = backtest.run(part, False), backtest.run(part, True)
            print(f"  {pname:5} plain {p['ret_pct']:+.1f}% (dd {p['worst_pnl_pct']:.0f})"
                  f" | smart {s['ret_pct']:+.1f}% (dd {s['worst_pnl_pct']:.0f})")
CFG.update(base)
print("\nPick settings using TRAIN only, then confirm on TEST. dd = worst dip in %.")

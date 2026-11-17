"""Compare settings on BTC and ETH. Run in Colab: python compare.py"""
import pandas as pd
from strategy import CFG, make_exchange
import backtest

SYMBOLS = ["BTC/USDT", "ETH/USDT"]
DAYS = 2000
VARIANTS = {
    "v3 default": {},
    "no dip boost": {"dip_boost": 0},
    "+ fear&greed": {"use_fng": 1},
    "no selling": {"tp_pct": 99},
    "no dip, no sell": {"dip_boost": 0, "tp_pct": 99},
}

ex = make_exchange(CFG["data_exchange"])
base = dict(CFG)
for sym in SYMBOLS:
    full = backtest.load(ex, sym, DAYS)
    d0, d1 = (pd.to_datetime(full["ts"].iloc[i], unit="ms").date() for i in (0, -1))
    print(f"\n##### {sym}: {len(full)} candles, {d0} to {d1} #####")
    parts = backtest.split(full)
    for vname, over in VARIANTS.items():
        CFG.update(base)
        CFG.update(over)
        print(f"\n{sym} | {vname}")
        for pname, part in parts.items():
            p, s = backtest.run(part, False), backtest.run(part, True)
            print(f"  {pname:5} plain {p['ret_pct']:+.1f}% (dd {p['worst_pnl_pct']:.0f})"
                  f" | smart {s['ret_pct']:+.1f}% (dd {s['worst_pnl_pct']:.0f}, idle ${s['idle_cash']})")
CFG.update(base)
print("\nSame budget for both. Pick using TRAIN only, confirm on TEST. dd = worst dip %.")

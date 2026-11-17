"""Backtest smart DCA vs plain DCA. Run in Colab/Termux: python backtest.py [days]"""
import sys, time
import pandas as pd
from strategy import CFG, make_exchange, fetch_candles, add_indicators, buy_multiplier, should_sell

WARMUP = 200
HRS = {"1h": 1, "4h": 4, "1d": 24}[CFG["timeframe"]]


def run(df, smart):
    fee, slip, base = CFG["fee"], CFG["slippage"], CFG["base_usd"]
    qty = cost = invested = proceeds = 0.0
    last_buy = last_sell = -1e18
    worst = 0.0
    n_buys = n_sells = 0
    for i in range(WARMUP, len(df) - 1):
        r, nxt = df.iloc[i], df.iloc[i + 1]
        px = float(nxt["open"])           # signal on close, fill at next open (no look-ahead)
        hrs = (r["ts"] - last_buy) / 3.6e6
        if hrs >= CFG["interval_h"]:
            m = buy_multiplier(r) if smart else 1.0
            if m > 0:
                spend = base * m
                qty += spend * (1 - fee) / (px * (1 + slip))
                cost += spend
                invested += spend
                last_buy = r["ts"]
                n_buys += 1
        if smart and (r["ts"] - last_sell) / 3.6e6 >= CFG["sell_cooldown_h"] and should_sell(px, qty, cost, r):
            sq = qty * CFG["tp_frac"]
            proceeds += sq * px * (1 - slip) * (1 - fee)
            cost -= cost * CFG["tp_frac"]
            qty -= sq
            last_sell = r["ts"]
            n_sells += 1
        if invested:
            worst = min(worst, (qty * px + proceeds) / invested - 1)
    last = float(df.iloc[-1]["close"])
    value = qty * last + proceeds
    ret = (value / invested - 1) * 100 if invested else 0
    return dict(invested=round(invested, 2), value=round(value, 2), ret_pct=round(ret, 2),
                worst_pnl_pct=round(worst * 100, 2), buys=n_buys, sells=n_sells)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 730
    ex = make_exchange(CFG["data_exchange"])
    per_page = 1000
    pages = days * 24 // HRS // per_page + 2
    since = int((time.time() - days * 86400) * 1000)
    df = add_indicators(fetch_candles(ex, CFG["symbol"], CFG["timeframe"], since, per_page, pages))
    print(f"{CFG['symbol']} {CFG['timeframe']}: {len(df)} candles")
    cut = int(len(df) * 0.6)
    parts = {"train (first 60%)": df.iloc[:cut].reset_index(drop=True),
             "test (last 40%)": df.iloc[cut - WARMUP:].reset_index(drop=True),
             "full": df}
    for name, part in parts.items():
        print(f"\n== {name} ==")
        print("plain DCA:", run(part, False))
        print("smart DCA:", run(part, True))
    print("\nTrust it only if smart beats plain on BOTH train and test, after fees.")

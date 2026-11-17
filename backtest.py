"""Fair backtest: smart DCA vs plain DCA with the SAME budget. Run: python backtest.py [days]"""
import sys, time
from strategy import (CFG, TF_HOURS, make_exchange, fetch_candles, fetch_fng, add_fng,
                      add_indicators, buy_multiplier, should_sell)

WARMUP = 200
HRS = TF_HOURS[CFG["timeframe"]]
H = 3.6e6


def run(df, smart):
    fee, slip, base = CFG["fee"], CFG["slippage"], CFG["base_usd"]
    qty = cost = pool = contributed = 0.0
    last_contrib = last_buy = last_sell = -1e18
    worst, buys, sells = 0.0, 0, 0
    for i in range(WARMUP, len(df) - 1):
        r, px, ts = df.iloc[i], float(df.iloc[i + 1]["open"]), df.iloc[i]["ts"]   # fill at next open
        if ts - last_contrib >= CFG["interval_h"] * H:           # new budget arrives
            pool += base
            contributed += base
            last_contrib = ts
        if smart and ts - last_sell >= CFG["sell_cooldown_h"] * H and should_sell(px, qty, cost, r):
            sq = qty * CFG["tp_frac"]
            pool += sq * px * (1 - slip) * (1 - fee)             # proceeds return to the pool
            cost -= cost * CFG["tp_frac"]
            qty -= sq
            last_sell = ts
            sells += 1
        if pool > 1e-9 and ts - last_buy >= CFG["interval_h"] * H:
            m = buy_multiplier(r) if smart else 1.0
            spend = min(pool, base * m)
            if spend > 0:
                pool -= spend
                qty += spend * (1 - fee) / (px * (1 + slip))
                cost += spend
                last_buy = ts
                buys += 1
        worst = min(worst, (qty * px + pool) / contributed - 1)
    value = qty * float(df.iloc[-1]["close"]) + pool            # idle cash counts at face value
    return dict(contributed=round(contributed), value=round(value),
                ret_pct=round((value / contributed - 1) * 100, 2),
                worst_pnl_pct=round(worst * 100, 1), idle_cash=round(pool), buys=buys, sells=sells)


def load(ex, symbol, days):
    pages = days * 24 // HRS // 1000 + 2
    since = int((time.time() - days * 86400) * 1000)
    return add_fng(add_indicators(fetch_candles(ex, symbol, CFG["timeframe"], since, 1000, pages)), fetch_fng())


def split(df):
    cut = int(len(df) * 0.6)
    return {"train": df.iloc[:cut].reset_index(drop=True),
            "test": df.iloc[cut - WARMUP:].reset_index(drop=True), "full": df}


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 730
    df = load(make_exchange(CFG["data_exchange"]), CFG["symbol"], days)
    print(f"{CFG['symbol']} {CFG['timeframe']}: {len(df)} candles (same budget for both bots)")
    for name, part in split(df).items():
        print(f"\n== {name} ==")
        print("plain:", run(part, False))
        print("smart:", run(part, True))

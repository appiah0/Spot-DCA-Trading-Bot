"""Live/testnet bot. One run = check last closed candle, maybe buy/sell, exit."""
import os, json, time
from strategy import CFG, make_exchange, fetch_candles, add_indicators, buy_multiplier, should_sell

STATE = "state.json"


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"last_buy": 0, "last_sell": 0, "qty": 0.0, "cost": 0.0}


def main():
    sym = CFG["symbol"]
    pub = make_exchange(CFG["data_exchange"])
    df = add_indicators(fetch_candles(pub, sym, CFG["timeframe"], limit=500))
    r = df.iloc[-2]                      # last CLOSED candle
    price = float(r["close"])
    st, now = load_state(), time.time()
    print(f"price={price:.2f} rsi={r['rsi']:.1f} close>ema200={price > r['ema200']} atr%={r['atr_pct']:.2f}")

    key, sec = os.getenv("API_KEY"), os.getenv("API_SECRET")
    live = bool(key and sec)
    ex = None
    if live:
        ex = make_exchange(CFG["exchange"], apiKey=key, secret=sec)
        if os.getenv("TESTNET", "1") == "1":
            ex.set_sandbox_mode(True)
        ex.load_markets()
    else:
        print("No API keys: DRY RUN (logging only)")

    # ---- SELL (only coins this bot bought) ----
    if should_sell(price, st["qty"], st["cost"]) and (now - st["last_sell"]) / 3600 >= CFG["sell_cooldown_h"]:
        q = st["qty"] * CFG["tp_frac"]
        if live:
            q = float(ex.amount_to_precision(sym, q))
            ex.create_market_sell_order(sym, q)
            st["cost"] -= st["cost"] * CFG["tp_frac"]
            st["qty"] -= q
            st["last_sell"] = now
        print(f"SELL {q} {sym} (take profit)")

    # ---- BUY ----
    m = buy_multiplier(r)
    if (now - st["last_buy"]) / 3600 >= CFG["interval_h"] and m > 0:
        spend = CFG["base_usd"] * m
        q = spend / price
        if live:
            q = float(ex.amount_to_precision(sym, q))
            mn = (ex.market(sym)["limits"]["cost"] or {}).get("min")
            if mn and q * price < mn:
                print(f"Order {q * price:.2f} below exchange minimum {mn}; skipped")
                return save(st)
            ex.create_market_buy_order(sym, q)
            st["qty"] += q
            st["cost"] += q * price
            st["last_buy"] = now
        print(f"BUY ~${spend:.2f} (x{m}) {q} {sym}")
    else:
        print(f"No buy (multiplier={m}, hours since last buy={(now - st['last_buy']) / 3600:.1f})")
    save(st)


def save(st):
    json.dump(st, open(STATE, "w"))


if __name__ == "__main__":
    main()

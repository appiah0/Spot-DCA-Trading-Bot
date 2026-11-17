"""Live/testnet bot (binance, bybit, okx, mexc). One run = check last closed candle, maybe trade, exit."""
import os, json, time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from strategy import (CFG, SUPPORTED, TF_HOURS, make_exchange, fetch_candles, fetch_fng, add_fng,
                      add_indicators, buy_multiplier, should_sell)

STATE = "state.json"
FEEDS = ["https://cointelegraph.com/rss", "https://www.coindesk.com/arc/outboundfeeds/rss/"]
RISK_WORDS = ["hacked", "exploited", "bankrupt", "insolven", "halts withdrawal", "suspends withdrawal",
              "delist", "depeg", "rug pull"]
# a headline must ALSO mention the exchange we trade on, Bitcoin or a major stablecoin


def news_risk(max_age_h=6):
    """Recent headlines with a danger word AND a market/exchange name. A safety brake, not a predictor."""
    hits = []
    for url in FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(urllib.request.urlopen(req, timeout=15).read())
            for it in root.iter("item"):
                title = (it.findtext("title") or "")
                age = time.time() - parsedate_to_datetime(it.findtext("pubDate")).timestamp()
                t = title.lower()
                if (age < max_age_h * 3600 and any(w in t for w in RISK_WORDS)
                        and any(w in t for w in [CFG["exchange"], "bitcoin", "btc", "tether", "usdt", "usdc", "stablecoin"])):
                    hits.append(title)
        except Exception as e:
            print("news feed unavailable:", url, e)
    return hits[:5]


def load_state():
    st = {"last_buy": 0, "last_sell": 0, "last_contrib": 0, "pool": 0.0, "qty": 0.0, "cost": 0.0}
    try:
        st.update(json.load(open(STATE)))
    except Exception:
        pass
    return st


def main():
    name, sym = CFG["exchange"], CFG["symbol"]
    assert name in SUPPORTED, f"EXCHANGE must be one of {SUPPORTED}"
    pub = make_exchange(CFG["data_exchange"])
    tf_ms = TF_HOURS[CFG["timeframe"]] * 3600 * 1000
    since = int(time.time() * 1000) - 1200 * tf_ms
    df = add_indicators(fetch_candles(pub, sym, CFG["timeframe"], since, 1000, 15))
    df = add_fng(df, fetch_fng() if CFG["use_fng"] else {})
    r = df.iloc[-2]                                   # last CLOSED candle
    price = float(r["close"])
    print(f"{sym} price={price:.2f} rsi={r['rsi']:.1f} adx={r['adx']:.0f} macd_hist={r['macd_hist']:.2f} "
          f"bb%={r['bb_pct']:.2f} vol_ratio={r['vol_ratio']:.2f} dd_from_high={r['dd_high']*100:.0f}% "
          f"above_ema200={price > r['ema200']} fng={r.get('fng')}")

    if len(df) < 300:
        print(f"Only {len(df)} candles from {CFG['data_exchange']}: not enough history. No trading.")
        return
    try:
        live_px = float(make_exchange(name).fetch_ticker(sym)["last"])
    except Exception as e:
        print(f"Could not check the live price on {name} ({e}). No trading this run.")
        return
    if abs(live_px / price - 1) > 0.05:
        print(f"PRICE MISMATCH: data feed {price:.2f} vs {name} live {live_px:.2f}. Data looks wrong, no trading.")
        return
    print(f"Price check OK: {name} live {live_px:.2f}, last closed candle {price:.2f}")

    key, sec, pw = os.getenv("API_KEY"), os.getenv("API_SECRET"), os.getenv("API_PASSWORD")
    testnet = os.getenv("TESTNET", "1") == "1"
    live = bool(key and sec)
    if live and testnet and name == "mexc":
        print("MEXC has no testnet: DRY RUN only. Set TESTNET=0 for real trading.")
        live = False
    ex = None
    if live:
        ex = make_exchange(name, apiKey=key, secret=sec, **({"password": pw} if pw else {}))
        if testnet:
            ex.set_sandbox_mode(True)
        ex.load_markets()
    elif not (key and sec):
        print("No API keys: DRY RUN (logging only)")

    st, now = load_state(), time.time()
    if now - st["last_contrib"] >= CFG["interval_h"] * 3600:      # new budget arrives
        st["pool"] += CFG["base_usd"]
        st["last_contrib"] = now

    # ---- SELL (only coins this bot bought) ----
    if should_sell(price, st["qty"], st["cost"], r) and (now - st["last_sell"]) / 3600 >= CFG["sell_cooldown_h"]:
        q = st["qty"] * CFG["tp_frac"]
        if live:
            q = float(ex.amount_to_precision(sym, q))
            ex.create_market_sell_order(sym, q)
            st["pool"] += q * price * (1 - CFG["fee"])
            st["cost"] -= st["cost"] * CFG["tp_frac"]
            st["qty"] -= q
            st["last_sell"] = now
        print(f"SELL {q} {sym} (take profit)")

    # ---- BUY ----
    hits = news_risk() if os.getenv("NEWS_GUARD", "1") == "1" else []
    m = buy_multiplier(r)
    hrs = (now - st["last_buy"]) / 3600
    if hits:
        print("NEWS GUARD: buying paused. Headlines:", *hits, sep="\n  ")
    elif hrs >= CFG["interval_h"] and m > 0 and st["pool"] > 0:
        spend = min(st["pool"], CFG["base_usd"] * m)
        q = spend / price
        if live:
            q = float(ex.amount_to_precision(sym, q))
            mn = (ex.market(sym)["limits"]["cost"] or {}).get("min")
            quote = sym.split("/")[1]
            free = ex.fetch_balance()["free"].get(quote, 0)
            if (mn and q * price < mn) or free < q * price:
                print(f"Skipped: order ~{q * price:.2f} below minimum ({mn}) or balance {free} too low")
                return save(st, live)
            o = ex.create_market_buy_order(sym, q)
            spent = o.get("cost") or q * price
            st["pool"] -= spent
            st["cost"] += spent
            st["qty"] += o.get("filled") or q
            st["last_buy"] = now
        left = st["pool"] if live else st["pool"] - spend
        print(f"BUY ~${spend:.2f} (x{m}) {q} {sym}; pool left ${left:.2f}")
    else:
        print(f"No buy (multiplier={m}, pool=${st['pool']:.2f}, hours since last buy={hrs:.1f})")
    save(st, live)


def save(st, live):
    if live:
        json.dump(st, open(STATE, "w"))


if __name__ == "__main__":
    main()

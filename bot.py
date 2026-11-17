"""Multi-coin bot. Settings come from the BOT_CONFIG repo variable (edited by the web app).
One run = for each enabled coin check the last closed candle, maybe buy/sell, exit.
Manual trades: MANUAL_ACTION=buy|sell with MANUAL_SYMBOL and MANUAL_USD (from the workflow inputs)."""
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
PARAMS = ["hot_mult", "dip_boost", "tp_pct", "tp_ext", "tp_frac"]
DEFAULT_CONFIG = {
    "paused": False, "exchange": "okx", "data_exchange": "okx", "testnet": True,
    "interval_h": 24, "news_guard": True, "use_fng": False, "max_manual_usd": 100,
    "coins": [{"symbol": "BTC/USDT", "base_usd": 10, "enabled": True}], "params": {},
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    raw = (os.getenv("BOT_CONFIG") or "").strip()
    if raw:
        try:
            cfg.update(json.loads(raw))
        except Exception as e:
            print("BOT_CONFIG is not valid JSON, using defaults:", e)
    return cfg


def load_state():
    st = {"coins": {}, "history": [], "last_run": 0}
    try:
        raw = json.load(open(STATE))
        if "pool" in raw:                       # old single-coin format
            raw = {"coins": {"BTC/USDT": raw}}
        st.update(raw)
    except Exception:
        pass
    return st


def coin_state(st, sym):
    return st["coins"].setdefault(sym, {"last_buy": 0, "last_sell": 0, "last_contrib": 0,
                                        "pool": 0.0, "qty": 0.0, "cost": 0.0})


def log(st, sym, side, usd, qty, price, mode, note=""):
    st["history"].append({"t": int(time.time()), "symbol": sym, "side": side, "usd": round(usd, 2),
                          "qty": qty, "price": round(price, 2), "mode": mode, "note": note})
    st["history"] = st["history"][-100:]


def news_risk(exchange, max_age_h=6):
    """Recent headlines with a danger word AND the exchange / Bitcoin / a stablecoin. A brake, not a predictor."""
    hits, subjects = [], [exchange, "bitcoin", "btc", "tether", "usdt", "usdc", "stablecoin"]
    for url in FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(urllib.request.urlopen(req, timeout=15).read())
            for it in root.iter("item"):
                title = it.findtext("title") or ""
                t = title.lower()
                age = time.time() - parsedate_to_datetime(it.findtext("pubDate")).timestamp()
                if age < max_age_h * 3600 and any(w in t for w in RISK_WORDS) and any(w in t for w in subjects):
                    hits.append(title)
        except Exception as e:
            print("news feed unavailable:", url, e)
    return hits[:5]


def place(ex, sym, side, usd, price):
    """Real (or demo) market order. Returns (filled_qty, usd_value) or None if skipped."""
    q = float(ex.amount_to_precision(sym, usd / price))
    base, quote = sym.split("/")
    free = ex.fetch_balance()["free"]
    if side == "sell" and free.get(base, 0) < q:
        q = float(ex.amount_to_precision(sym, free.get(base, 0)))
    mn = (ex.market(sym)["limits"]["cost"] or {}).get("min")
    if q <= 0 or (mn and q * price < mn):
        print(f"Skipped {side} {sym}: ~${q * price:.2f} is below the exchange minimum ({mn})")
        return None
    if side == "buy" and free.get(quote, 0) < q * price:
        print(f"Skipped buy {sym}: balance {free.get(quote, 0)} {quote} too low")
        return None
    o = ex.create_market_buy_order(sym, q) if side == "buy" else ex.create_market_sell_order(sym, q)
    return (o.get("filled") or q), (o.get("cost") or q * price)


def manual_trade(ex, tex, live, mode, cfg, st, sym, side, usd):
    allowed = [c["symbol"] for c in cfg["coins"]]
    if sym not in allowed or side not in ("buy", "sell"):
        return print(f"Manual trade refused: {sym} is not in your coin list or side invalid")
    if not 0 < usd <= float(cfg["max_manual_usd"]):
        return print(f"Manual trade refused: amount must be between 0 and {cfg['max_manual_usd']} USD")
    px = float(tex.fetch_ticker(sym)["last"])
    if not live:
        return print(f"DRY RUN: would {side} ${usd} of {sym} at {px}")
    res = place(ex, sym, side, usd, px)
    if not res:
        return
    q, val = res
    cs = coin_state(st, sym)
    if side == "buy":
        cs["qty"] += q
        cs["cost"] += val
    elif cs["qty"] > 0:
        frac = min(1.0, q / cs["qty"])
        cs["cost"] *= 1 - frac
        cs["qty"] = max(0.0, cs["qty"] - q)
    log(st, sym, side, val, q, px, mode, "manual")
    print(f"MANUAL {side.upper()} {q} {sym} (~${val:.2f})")


def run_coin(pub, tex, ex, live, mode, cfg, c, st, hits, fng, now):
    sym, base = c["symbol"], float(c["base_usd"])
    since = int(now * 1000) - 1200 * TF_HOURS[CFG["timeframe"]] * 3600 * 1000
    df = add_fng(add_indicators(fetch_candles(pub, sym, CFG["timeframe"], since, 1000, 15)), fng)
    if len(df) < 300:
        return print(f"{sym}: only {len(df)} candles, not enough history. No trading.")
    r = df.iloc[-2]                                   # last CLOSED candle
    price = float(r["close"])
    print(f"{sym} price={price:.2f} rsi={r['rsi']:.1f} adx={r['adx']:.0f} macd_hist={r['macd_hist']:.2f} "
          f"bb%={r['bb_pct']:.2f} vol_ratio={r['vol_ratio']:.2f} dd_from_high={r['dd_high'] * 100:.0f}% "
          f"above_ema200={price > r['ema200']}")
    live_px = float(tex.fetch_ticker(sym)["last"])
    if abs(live_px / price - 1) > 0.05:
        return print(f"{sym}: PRICE MISMATCH feed {price:.2f} vs live {live_px:.2f}. No trading.")
    if cfg["paused"] or not c.get("enabled", True):
        return print(f"{sym}: bot paused or coin disabled, no trading")
    cs = coin_state(st, sym)
    if now - cs["last_contrib"] >= CFG["interval_h"] * 3600:      # new budget arrives
        cs["pool"] += base
        cs["last_contrib"] = now

    if should_sell(price, cs["qty"], cs["cost"], r) and (now - cs["last_sell"]) / 3600 >= CFG["sell_cooldown_h"]:
        q = cs["qty"] * CFG["tp_frac"]
        if live:
            res = place(ex, sym, "sell", q * live_px, live_px)
            if res:
                q, val = res
                cs["pool"] += val * (1 - CFG["fee"])
                cs["cost"] -= cs["cost"] * CFG["tp_frac"]
                cs["qty"] -= q
                cs["last_sell"] = now
                log(st, sym, "sell", val, q, live_px, mode, "take profit")
                print(f"{sym}: SELL {q} (take profit)")
        else:
            print(f"{sym}: DRY RUN would sell {q} (take profit)")

    m = buy_multiplier(r)
    hrs = (now - cs["last_buy"]) / 3600
    if hits:
        print(f"{sym}: NEWS GUARD, buying paused. Headlines:", *hits, sep="\n  ")
    elif hrs >= CFG["interval_h"] and m > 0 and cs["pool"] > 0:
        spend = min(cs["pool"], base * m)
        if live:
            res = place(ex, sym, "buy", spend, live_px)
            if res:
                q, val = res
                cs["pool"] -= val
                cs["cost"] += val
                cs["qty"] += q
                cs["last_buy"] = now
                log(st, sym, "buy", val, q, live_px, mode, f"x{m}")
                print(f"{sym}: BUY ~${val:.2f} (x{m}); pool left ${cs['pool']:.2f}")
        else:
            print(f"{sym}: DRY RUN would buy ~${spend:.2f} (x{m})")
    else:
        print(f"{sym}: no buy (multiplier={m}, pool=${cs['pool']:.2f}, hours since last buy={hrs:.1f})")


def main():
    cfg = load_config()
    name = cfg["exchange"]
    assert name in SUPPORTED, f"exchange must be one of {SUPPORTED}"
    CFG.update(exchange=name, interval_h=int(cfg["interval_h"]), use_fng=1.0 if cfg["use_fng"] else 0.0)
    CFG.update({k: float(v) for k, v in cfg.get("params", {}).items() if k in PARAMS})
    print(f"exchange={name} testnet={cfg['testnet']} paused={cfg['paused']} "
          f"coins={[c['symbol'] for c in cfg['coins'] if c.get('enabled', True)]}")

    key, sec, pw = os.getenv("API_KEY"), os.getenv("API_SECRET"), os.getenv("API_PASSWORD")
    live = bool(key and sec)
    if live and cfg["testnet"] and name == "mexc":
        print("MEXC has no testnet: DRY RUN only. Set testnet to false for real trading.")
        live = False
    ex = None
    if live:
        ex = make_exchange(name, apiKey=key, secret=sec, **({"password": pw} if pw else {}))
        if cfg["testnet"]:
            ex.set_sandbox_mode(True)
        ex.load_markets()
    elif not (key and sec):
        print("No API keys: DRY RUN (logging only)")
    mode = "dry" if not live else ("demo" if cfg["testnet"] else "live")
    pub, tex = make_exchange(cfg["data_exchange"]), make_exchange(name)
    st, now = load_state(), time.time()

    action = (os.getenv("MANUAL_ACTION") or "auto").lower()
    if action in ("buy", "sell"):
        manual_trade(ex, tex, live, mode, cfg, st, os.getenv("MANUAL_SYMBOL", ""), action,
                     float(os.getenv("MANUAL_USD") or 0))
    else:
        hits = news_risk(name) if cfg["news_guard"] else []
        fng = fetch_fng() if cfg["use_fng"] else {}
        for c in cfg["coins"]:
            if not c.get("enabled", True):
                continue
            try:
                run_coin(pub, tex, ex, live, mode, cfg, c, st, hits, fng, now)
            except Exception as e:                       # one failing coin must not stop the others
                print(f"{c['symbol']}: ERROR {type(e).__name__}: {str(e)[:200]}")
    st["last_run"] = int(now)
    if live:
        json.dump(st, open(STATE, "w"), indent=1)


if __name__ == "__main__":
    main()

"""Shared strategy: indicators + buy/sell rules. Used by backtest.py and bot.py."""
import os
import pandas as pd

CFG = {
    "exchange": os.getenv("EXCHANGE", "binance"),            # where orders are placed
    "data_exchange": os.getenv("DATA_EXCHANGE", "binance"),  # where candles come from (try binanceus if blocked)
    "symbol": os.getenv("SYMBOL", "BTC/USDT"),
    "timeframe": os.getenv("TIMEFRAME", "4h"),
    "base_usd": float(os.getenv("BASE_USD", "10")),
    "interval_h": int(os.getenv("INTERVAL_H", "24")),   # min hours between buys
    "fee": 0.001,           # 0.1% per trade (check your exchange)
    "slippage": 0.0005,     # 0.05% assumed
    "tp_pct": 0.20,         # sell when price >= avg cost * 1.20
    "tp_frac": 0.25,        # sell 25% of holdings each time
    "sell_cooldown_h": 24,
    "max_mult": 2.5,
}


def make_exchange(name, **kw):
    import ccxt
    return getattr(ccxt, name)({"enableRateLimit": True, **kw})


def fetch_candles(ex, symbol, tf, since_ms=None, limit=1000, pages=1):
    rows, since = [], since_ms
    for _ in range(pages):
        batch = ex.fetch_ohlcv(symbol, tf, since=since, limit=limit)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + 1
        if len(batch) < limit:
            break
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    return df.drop_duplicates("ts").reset_index(drop=True)


def add_indicators(df):
    c = df["close"]
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + up / dn)
    df["ema50"] = c.ewm(span=50, adjust=False).mean()
    df["ema200"] = c.ewm(span=200, adjust=False).mean()
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - c.shift()).abs(),
                    (df["low"] - c.shift()).abs()], axis=1).max(axis=1)
    df["atr_pct"] = tr.ewm(alpha=1 / 14, adjust=False).mean() / c * 100
    return df


def buy_multiplier(r):
    """0 = skip, 1 = normal DCA amount, up to max_mult = buy more."""
    if pd.isna(r["ema200"]) or pd.isna(r["rsi"]):
        return 0.0
    m = 1.0
    if r["rsi"] < 30:
        m += 1.0          # deeply oversold: buy more
    elif r["rsi"] < 40:
        m += 0.5
    elif r["rsi"] > 70:
        m = 0.0           # overheated: wait
    elif r["rsi"] > 60:
        m -= 0.5
    if r["close"] < r["ema200"] and r["rsi"] >= 30:
        m *= 0.5          # downtrend: go slower unless deeply oversold
    if r["atr_pct"] > 3:
        m *= 0.7          # very volatile: smaller size
    return round(max(0.0, min(m, CFG["max_mult"])), 2)


def should_sell(price, qty, cost):
    if qty <= 0:
        return False
    return price >= (cost / qty) * (1 + CFG["tp_pct"])

"""Shared strategy: data, indicators, buy/sell rules. Used by backtest.py, compare.py, bot.py."""
import os, json, urllib.request
import pandas as pd

SUPPORTED = ["binance", "bybit", "okx", "mexc"]
TF_HOURS = {"1h": 1, "4h": 4, "1d": 24}


def _f(name, default):
    return float(os.getenv(name, default))


CFG = {
    "exchange": os.getenv("EXCHANGE", "binance"),            # where orders are placed
    "data_exchange": os.getenv("DATA_EXCHANGE", "binance"),  # where candles come from (try binanceus if blocked)
    "symbol": os.getenv("SYMBOL", "BTC/USDT"),
    "timeframe": os.getenv("TIMEFRAME", "4h"),
    "base_usd": _f("BASE_USD", "10"),        # budget added to the pool each interval
    "interval_h": int(_f("INTERVAL_H", "24")),
    "fee": 0.001,                            # 0.1% per trade (check your exchange)
    "slippage": 0.0005,
    "tp_pct": _f("TP_PCT", "0.5"),           # sell only if price >= avg cost * 1.5 ...
    "tp_ext": _f("TP_EXT", "0.25"),          # ... AND >= 25% above the 200-EMA
    "tp_frac": _f("TP_FRAC", "0.15"),
    "sell_cooldown_h": 168,
    "hot_mult": _f("HOT_MULT", "0.5"),       # size when RSI>70 in an uptrend
    "dip_boost": _f("DIP_BOOST", "1"),       # 1 = buy more when far below the 180-day high
    "use_fng": _f("USE_FNG", "0"),           # 1 = use Fear & Greed index
    "max_mult": 2.5,
}


def make_exchange(name, **kw):
    import ccxt
    return getattr(ccxt, name)({"enableRateLimit": True, **kw})


def fetch_candles(ex, symbol, tf, since_ms=None, limit=1000, pages=1):
    rows, since, last = [], since_ms, -1
    for _ in range(pages):
        batch = ex.fetch_ohlcv(symbol, tf, since=since, limit=limit)
        if not batch or batch[-1][0] <= last:
            break
        rows += batch
        last = batch[-1][0]
        since = last + 1
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    return df.drop_duplicates("ts").reset_index(drop=True)


def fetch_fng():
    """Crypto Fear & Greed index (free, no key): {day_number: value 0-100}."""
    try:
        url = "https://api.alternative.me/fng/?limit=0&format=json"
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.load(r)["data"]
        return {int(d["timestamp"]) // 86400: int(d["value"]) for d in data}
    except Exception as e:
        print("Fear & Greed unavailable:", e)
        return {}


def add_fng(df, fng):
    day = df["ts"] // 86400000 - 1          # use yesterday's value: no look-ahead
    df["fng"] = day.map(fng)
    return df


def add_indicators(df):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + up / dn)
    df["ema50"] = c.ewm(span=50, adjust=False).mean()
    df["ema200"] = c.ewm(span=200, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df["atr_pct"] = atr / c * 100
    # --- extra indicators: computed and logged; only dd_high is used in rules so far ---
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    df["macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()
    ma20, sd20 = c.rolling(20).mean(), c.rolling(20).std()
    df["bb_pct"] = (c - (ma20 - 2 * sd20)) / (4 * sd20)       # 0 = lower band, 1 = upper band
    um, dm = h.diff(), -l.diff()
    pdm = ((um > dm) & (um > 0)) * um
    mdm = ((dm > um) & (dm > 0)) * dm
    pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    mdi = 100 * mdm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    df["adx"] = (100 * (pdi - mdi).abs() / (pdi + mdi)).ewm(alpha=1 / 14, adjust=False).mean()
    df["vol_ratio"] = v / v.rolling(30).mean()
    window = int(180 * 24 / TF_HOURS[os.getenv("TIMEFRAME", "4h")])
    df["dd_high"] = c / c.rolling(window, min_periods=1).max() - 1   # 0 = at 180-day high
    return df


def buy_multiplier(r):
    """0 = skip, 1 = normal amount, up to max_mult. Skipped money stays in the pool for later."""
    if pd.isna(r["ema200"]) or pd.isna(r["rsi"]):
        return 0.0
    uptrend = r["close"] > r["ema200"] and r["ema50"] > r["ema200"]
    rsi = r["rsi"]
    if rsi < 30:
        m = 2.0
    elif rsi < 40:
        m = 1.5
    elif rsi > 70:
        m = CFG["hot_mult"] if uptrend else 0.0
    elif rsi > 60:
        m = 1.0 if uptrend else 0.5
    else:
        m = 1.0
    if r["close"] < r["ema200"] and rsi >= 30:
        m *= 0.5
    if not uptrend and r["atr_pct"] > 3:
        m *= 0.7
    if CFG["dip_boost"]:                       # deep drawdown from 180-day high: deploy the reserve
        if r["dd_high"] <= -0.5:
            m += 1.0
        elif r["dd_high"] <= -0.3:
            m += 0.5
    if CFG["use_fng"] and pd.notna(r.get("fng")):
        if r["fng"] <= 25:
            m += 0.5                           # extreme fear
        elif r["fng"] >= 75:
            m *= 0.5                           # extreme greed
    return round(max(0.0, min(m, CFG["max_mult"])), 2)


def should_sell(price, qty, cost, r):
    """Take profit only when well above BOTH your average cost and the 200-EMA."""
    if qty <= 0:
        return False
    above_cost = price >= (cost / qty) * (1 + CFG["tp_pct"])
    stretched = price >= r["ema200"] * (1 + CFG["tp_ext"])
    return bool(above_cost and stretched)

"""The price layer. Every figure in the store originates here, in code.

The model may reason freely about what -3.1% means; it must not be able to
invent that number. Same rule daily-feeds runs on (SPEC 5).
"""
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from db import JST, iso, market_closed_for, now

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
CACHE_FRESH_MIN = 15


def _fetch(symbol, rng="3mo"):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?range={rng}&interval=1d")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())["chart"]["result"][0]
    except Exception:
        return None
    tz = d.get("meta", {}).get("exchangeTimezoneName") or "Asia/Tokyo"
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz)
    except Exception:
        zone = JST
    out = {}
    closes = d["indicators"]["quote"][0]["close"]
    for t, c in zip(d.get("timestamp") or [], closes):
        if c is None:
            continue
        out[datetime.fromtimestamp(t, zone).date().isoformat()] = float(c)
    return out or None


def series(con, symbol, need_date=None):
    """{date: close} for a symbol, cache-first. Settled bars never refetch."""
    rows = con.execute("SELECT date, close, fetched_at FROM bars_cache "
                       "WHERE symbol=? ORDER BY date", (symbol,)).fetchall()
    cache = {r["date"]: r["close"] for r in rows}
    today = now().date().isoformat()
    if rows:
        age_min = (now() - datetime.fromisoformat(max(r["fetched_at"] for r in rows))
                   ).total_seconds() / 60.0
        have = need_date in cache if need_date else True
        settled = bool(need_date) and need_date < today
        if have and (settled or age_min <= CACHE_FRESH_MIN):
            return cache, "cache"

    fetched = _fetch(symbol)
    if fetched is None:
        return cache, ("cache-stale" if cache else "unavailable")
    ts = iso(now())
    con.executemany(
        "INSERT INTO bars_cache(symbol,date,close,fetched_at) VALUES(?,?,?,?) "
        "ON CONFLICT(symbol,date) DO UPDATE SET close=excluded.close, fetched_at=excluded.fetched_at",
        [(symbol, d, c, ts) for d, c in fetched.items()])
    con.commit()
    cache.update(fetched)
    return cache, "network"


def move(con, symbol, date=None):
    """One session's change for one symbol. Never guesses; says so instead."""
    date = date or now().date().isoformat()
    bars, origin = series(con, symbol, need_date=date)
    if not bars:
        return {"symbol": symbol, "status": "unavailable", "move_pct": None,
                "note": "no data from the chart endpoint"}
    dates = sorted(bars)
    usable = [d for d in dates if d <= date]
    if len(usable) < 2:
        return {"symbol": symbol, "status": "unavailable", "move_pct": None,
                "note": "insufficient history"}
    bar_date, prev_date = usable[-1], usable[-2]
    close, prev = bars[bar_date], bars[prev_date]
    pct = round((close / prev - 1) * 100, 2) if prev else None

    today = now().date().isoformat()
    if bar_date != date:
        status, note = "stale", f"no bar for {date}; used {bar_date} (holiday, weekend or future date)"
    elif date == today and not market_closed_for(now()):
        status, note = "intraday", "session still open; this is a partial bar"
    else:
        status, note = "settled", None
    if origin == "cache-stale":
        note = (note + "; " if note else "") + "chart endpoint unreachable, served from cache"
    return {"symbol": symbol, "status": status, "bar_date": bar_date, "prev_date": prev_date,
            "close": round(close, 4), "prev_close": round(prev, 4), "move_pct": pct,
            "origin": origin, "note": note}


def measure(con, symbol, benchmark=None, date=None):
    """A move plus the benchmark it should be judged against. Excess is the
    whole point: 'hit hardest' is meaningless without it."""
    m = move(con, symbol, date)
    out = {"instrument": m, "benchmark": None, "excess_pct": None,
           "measured_at": iso(now()), "status": m["status"], "note": m.get("note")}
    if benchmark and benchmark != symbol:
        b = move(con, benchmark, date)
        out["benchmark"] = b
        if m.get("move_pct") is not None and b.get("move_pct") is not None:
            out["excess_pct"] = round(m["move_pct"] - b["move_pct"], 2)
        if b["status"] in ("stale", "unavailable") and out["status"] == "settled":
            out["status"] = "settled"
            out["note"] = (out["note"] + "; " if out["note"] else "") + \
                          f"benchmark {benchmark} {b['status']}"
    return out

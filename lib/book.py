"""Read-only view of the trading diary. Granger never writes there.

Without this, the expensive bias is invisible: an observation that
conveniently argues for a position you already hold looks exactly like
an observation.
"""
import difflib
import sqlite3
from datetime import datetime, timedelta

from db import BOOK_DB, JST, attach_readonly, normalize, now

RECENT_CLOSE_DAYS = 10


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    return con if attach_readonly(con, BOOK_DB, "book") else None


def _ms(v):
    if not v:
        return None
    return datetime.fromtimestamp(v / 1000.0, JST).isoformat(timespec="seconds")


def positions(recent_days=RECENT_CLOSE_DAYS):
    """Open trades, plus closes recent enough to still be rationalised."""
    con = _con()
    if con is None:
        return {"available": False, "open": [], "recent_closed": []}
    cutoff = int((now() - timedelta(days=recent_days)).timestamp() * 1000)
    try:
        rows = con.execute(
            "SELECT id,status,symbol,direction,quantity,entry_price,exit_price,pnl,r_multiple,"
            "setup,entry_at,exit_at FROM book.trades "
            "WHERE status='open' OR exit_at >= ? ORDER BY entry_at DESC", (cutoff,)).fetchall()
    except sqlite3.Error as e:
        return {"available": False, "error": str(e), "open": [], "recent_closed": []}
    finally:
        pass
    out = {"available": True, "open": [], "recent_closed": []}
    for r in rows:
        t = dict(r)
        t["entry_at"], t["exit_at"] = _ms(t["entry_at"]), _ms(t["exit_at"])
        (out["open"] if t["status"] == "open" else out["recent_closed"]).append(t)
    con.close()
    return out


def relates(trade_symbol, instrument):
    """Diary symbols are free text ('NIKKEI 255 MICRO'); instruments are yahoo
    symbols. Match loosely and honestly, and let the model judge the rest."""
    a = normalize(trade_symbol or "")
    if not a:
        return False
    for cand in (instrument.get("display") or "", instrument.get("symbol") or "",
                 (instrument.get("symbol") or "").replace(".T", "").lstrip("^")):
        b = normalize(cand)
        if not b:
            continue
        if b and (b in a or a in b):
            return True
        if difflib.SequenceMatcher(None, a, b).ratio() >= 0.8:
            return True
    # index instruments vs futures shorthand
    if instrument.get("symbol") == "^N225" and "nikkei" in a:
        return True
    return False


def exposure_for(instruments):
    """Which live/recent trades bear on the instruments in this observation."""
    book = positions()
    if not book["available"]:
        return book, []
    hits = []
    for t in book["open"] + book["recent_closed"]:
        for ins in instruments:
            if relates(t["symbol"], ins):
                hits.append({**t, "matched_instrument": ins["symbol"]})
                break
    return book, hits

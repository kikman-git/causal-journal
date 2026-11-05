"""Resolve, don't proliferate.

Drivers, instruments and channels are entities, not free text — that is the
whole analytical payoff. Free-text causes give you a diary; resolved drivers
let you ask "every time I blamed the yen, what actually happened?"

Near-misses are flagged (dupe_of), never blocked. Capture always wins.
"""
import difflib
import re
import sqlite3

from db import BOOK_DB, FEEDS_DB, attach_readonly, iso, normalize, now, slugify

# daily-feeds' ticker mirror carries sector17, which is exactly the NEXT FUNDS
# TOPIX-17 ETF series. A sector benchmark for every TSE name, for free.
SECTOR17_ETF = {
    "食品": "1617.T", "エネルギー資源": "1618.T", "建設・資材": "1619.T",
    "素材・化学": "1620.T", "医薬品": "1621.T", "自動車・輸送機": "1622.T",
    "鉄鋼・非鉄": "1623.T", "機械": "1624.T", "電機・精密": "1625.T",
    "情報通信・サービスその他": "1626.T", "電力・ガス": "1627.T",
    "運輸・物流": "1628.T", "商社・卸売": "1629.T", "小売": "1630.T",
    "銀行": "1631.T", "金融（除く銀行）": "1632.T", "不動産": "1633.T",
}
TOPIX = "1306.T"
DRIVER_KINDS = ("macro-print", "policy", "fx", "rates", "flows", "single-name",
                "sector-rotation", "geopolitics", "technical", "unknown")

SURE, MAYBE = 0.88, 0.72


def _best(text, candidates):
    """(key, ratio) of the closest candidate. candidates: {key: [strings]}"""
    best, ratio = None, 0.0
    for key, strings in candidates.items():
        for s in strings:
            r = difflib.SequenceMatcher(None, normalize(text), normalize(s)).ratio()
            if r > ratio:
                best, ratio = key, r
    return best, ratio


# ------------------------------------------------------------------ instruments

def _tse_lookup(code=None, name=None):
    """The JPX mirror inside daily-feeds. Absent file -> None, never an abort."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    if not attach_readonly(con, FEEDS_DB, "feeds"):
        return None
    try:
        if code:
            r = con.execute("SELECT * FROM feeds.tickers WHERE code=?", (code,)).fetchone()
            return dict(r) if r else None
        q = f"%{name}%"
        rows = con.execute(
            "SELECT * FROM feeds.tickers WHERE name_ja LIKE ? OR name_en LIKE ? OR romaji LIKE ? "
            "LIMIT 6", (q, q, q)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return None
    finally:
        con.close()


def benchmark_for(kind, sector17=None, explicit=None):
    if explicit:
        return explicit
    if kind == "equity":
        return SECTOR17_ETF.get(sector17 or "", TOPIX)
    if kind == "etf":
        return TOPIX
    return None


def _looks_like_symbol(s):
    return bool(re.fullmatch(r"\^[A-Z0-9.]+|[A-Z0-9]{1,6}=[A-Z]|[0-9A-Z]{4}\.T|[A-Z.]{1,6}", s.strip()))


def resolve_instrument(con, text, create=True):
    """-> (row, created, note) | (None, False, error string)"""
    raw = (text or "").strip()
    if not raw:
        return None, False, "empty instrument"
    key = normalize(raw)
    hit = con.execute(
        "SELECT i.* FROM instrument_aliases a JOIN instruments i ON i.id=a.instrument_id "
        "WHERE a.alias=?", (key,)).fetchone()
    if hit:
        return dict(hit), False, None

    sym, kind, display, sec33, sec17, scale = None, None, None, None, None, None

    code = re.fullmatch(r"([0-9][0-9A-Z]{3})(\.T)?", raw.upper())
    if code:
        t = _tse_lookup(code=code.group(1))
        sym = code.group(1) + ".T"
        if t:
            kind = "etf" if t["kind"] == "etf" else "equity"
            display = t.get("name_en") or t["name_ja"]
            sec33, sec17, scale = t.get("sector33"), t.get("sector17"), t.get("scale")
        else:
            kind, display = "equity", sym
    elif _looks_like_symbol(raw):
        sym = raw.strip() if raw.startswith("^") or "=" in raw else raw.upper()
        kind = "index" if sym.startswith("^") else ("fx" if "=" in sym else "equity")
        display = sym
    else:
        matches = _tse_lookup(name=raw) or []
        if len(matches) == 1:
            t = matches[0]
            sym = t["code"] + ".T"
            kind = "etf" if t["kind"] == "etf" else "equity"
            display = t.get("name_en") or t["name_ja"]
            sec33, sec17, scale = t.get("sector33"), t.get("sector17"), t.get("scale")
        elif len(matches) > 1:
            names = ", ".join(f"{m['code']} {m['name_ja']}" for m in matches[:5])
            return None, False, f"ambiguous instrument '{raw}' — candidates: {names}"
        else:
            rows = {r["symbol"]: [r["symbol"], r["display"]]
                    for r in con.execute("SELECT symbol, display FROM instruments")}
            cand, ratio = _best(raw, rows)
            if ratio >= SURE:
                row = con.execute("SELECT * FROM instruments WHERE symbol=?", (cand,)).fetchone()
                con.execute("INSERT OR IGNORE INTO instrument_aliases(alias,instrument_id) VALUES(?,?)",
                            (key, row["id"]))
                return dict(row), False, None
            return None, False, (f"unknown instrument '{raw}' — give a ticker code or yahoo symbol"
                                 + (f" (closest known: {cand})" if ratio >= MAYBE else ""))

    exist = con.execute("SELECT * FROM instruments WHERE symbol=?", (sym,)).fetchone()
    if exist:
        con.execute("INSERT OR IGNORE INTO instrument_aliases(alias,instrument_id) VALUES(?,?)",
                    (key, exist["id"]))
        return dict(exist), False, None
    if not create:
        return None, False, f"instrument '{raw}' not registered"

    ts = iso(now())
    cur = con.execute(
        "INSERT INTO instruments(symbol,kind,display,benchmark_symbol,sector33,sector17,scale,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (sym, kind, display, benchmark_for(kind, sec17), sec33, sec17, scale, ts))
    iid = cur.lastrowid
    for a in {key, normalize(sym), normalize(display or "")} - {""}:
        con.execute("INSERT OR IGNORE INTO instrument_aliases(alias,instrument_id) VALUES(?,?)", (a, iid))
    row = con.execute("SELECT * FROM instruments WHERE id=?", (iid,)).fetchone()
    return dict(row), True, None


# ---------------------------------------------------------------------- drivers

def resolve_driver(con, text, kind=None):
    raw = (text or "").strip()
    if not raw:
        return None, False, None
    key = normalize(raw)
    hit = con.execute(
        "SELECT d.* FROM driver_aliases a JOIN drivers d ON d.id=a.driver_id WHERE a.alias=?",
        (key,)).fetchone()
    if hit:
        return dict(hit), False, None
    slug = slugify(raw)
    hit = con.execute("SELECT * FROM drivers WHERE slug=?", (slug,)).fetchone()
    if hit:
        con.execute("INSERT OR IGNORE INTO driver_aliases(alias,driver_id) VALUES(?,?)", (key, hit["id"]))
        return dict(hit), False, None

    rows = {r["slug"]: [r["slug"], r["display"]]
            for r in con.execute("SELECT slug, display FROM drivers")}
    cand, ratio = _best(raw, rows)
    if ratio >= SURE:
        row = con.execute("SELECT * FROM drivers WHERE slug=?", (cand,)).fetchone()
        con.execute("INSERT OR IGNORE INTO driver_aliases(alias,driver_id) VALUES(?,?)", (key, row["id"]))
        return dict(row), False, None

    kind = kind if kind in DRIVER_KINDS else "unknown"
    ts = iso(now())
    dupe = None
    if ratio >= MAYBE:
        dupe = con.execute("SELECT id FROM drivers WHERE slug=?", (cand,)).fetchone()["id"]
    cur = con.execute(
        "INSERT INTO drivers(slug,kind,display,dupe_of,created_at,last_seen_at) VALUES(?,?,?,?,?,?)",
        (slug, kind, raw, dupe, ts, ts))
    con.execute("INSERT OR IGNORE INTO driver_aliases(alias,driver_id) VALUES(?,?)", (key, cur.lastrowid))
    row = dict(con.execute("SELECT * FROM drivers WHERE id=?", (cur.lastrowid,)).fetchone())
    return row, True, (f"?dupe:{cand}" if dupe else None)


# --------------------------------------------------------------------- channels

def resolve_channel(con, text):
    raw = (text or "").strip()
    if not raw:
        return None, False, None
    slug = slugify(raw)
    hit = con.execute("SELECT * FROM channels WHERE slug=?", (slug,)).fetchone()
    if hit:
        return dict(hit), False, None
    rows = {r["slug"]: [r["slug"], r["display"]]
            for r in con.execute("SELECT slug, display FROM channels")}
    cand, ratio = _best(raw, rows)
    if ratio >= SURE:
        return dict(con.execute("SELECT * FROM channels WHERE slug=?", (cand,)).fetchone()), False, None
    ts = iso(now())
    cur = con.execute("INSERT INTO channels(slug,display,created_at,last_seen_at) VALUES(?,?,?,?)",
                      (slug, raw, ts, ts))
    row = dict(con.execute("SELECT * FROM channels WHERE id=?", (cur.lastrowid,)).fetchone())
    return row, True, (f"?near:{cand}" if ratio >= MAYBE else None)


def touch(con, table, _id):
    con.execute(f"UPDATE {table} SET last_seen_at=? WHERE id=?", (iso(now()), _id))

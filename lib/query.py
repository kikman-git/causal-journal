"""The only eye. Read views the ack and the weekly read are built from.

Answer from these, cite ids and dates. If the store is silent on something,
say exactly that.
"""
import statistics
from datetime import timedelta

from db import connect, now


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def observation(con, oid):
    o = con.execute("SELECT * FROM observations WHERE id=?", (oid,)).fetchone()
    if not o:
        return None
    o = dict(o)
    o["links"] = _rows(con.execute(
        "SELECT l.*, d.slug driver, d.kind driver_kind, c.slug channel, i.symbol, i.display "
        "FROM links l LEFT JOIN drivers d ON d.id=l.driver_id LEFT JOIN channels c ON c.id=l.channel_id "
        "JOIN instruments i ON i.id=l.instrument_id WHERE l.observation_id=? ORDER BY l.seq", (oid,)))
    for l in o["links"]:
        l["flags"] = _rows(con.execute(
            "SELECT flag, note, raised_by FROM link_flags WHERE link_id=?", (l["id"],)))
        l["feed_refs"] = _rows(con.execute(
            "SELECT feed_event_id, brief_date, title_snapshot, why_snapshot, risk_direction, risk_score "
            "FROM feed_refs WHERE link_id=?", (l["id"],)))
    o["flags"] = _rows(con.execute(
        "SELECT flag, note, raised_by FROM observation_flags WHERE observation_id=?", (oid,)))
    o["sources"] = _rows(con.execute(
        "SELECT * FROM sources WHERE observation_id=? ORDER BY id", (oid,)))
    o["book_snapshot"] = _rows(con.execute(
        "SELECT * FROM book_snapshot WHERE observation_id=?", (oid,)))
    return o


def recent(con, n=20, include_superseded=False):
    where = "" if include_superseded else "WHERE o.superseded_by IS NULL"
    rows = _rows(con.execute(
        f"SELECT o.id, o.at, o.trade_date, o.session, o.text, o.author, "
        f"(SELECT count(*) FROM links l WHERE l.observation_id=o.id) n_links, "
        f"(SELECT count(*) FROM link_flags lf JOIN links l ON l.id=lf.link_id "
        f" WHERE l.observation_id=o.id) n_flags "
        f"FROM observations o {where} ORDER BY o.at DESC LIMIT ?", (n,)))
    return rows


def driver(con, slug, limit=40):
    d = con.execute("SELECT * FROM drivers WHERE slug=?", (slug,)).fetchone()
    if not d:
        near = _rows(con.execute("SELECT slug, display FROM drivers ORDER BY last_seen_at DESC LIMIT 12"))
        return {"found": False, "slug": slug, "known": near}
    d = dict(d)
    links = _rows(con.execute(
        "SELECT l.id, l.direction, l.confidence, l.move_measured_pct, l.benchmark_move_pct, "
        "l.excess_pct, l.measure_session, l.verdict, l.horizon_days, i.symbol, c.slug channel, "
        "o.id observation_id, o.trade_date, o.text FROM links l "
        "JOIN observations o ON o.id=l.observation_id JOIN instruments i ON i.id=l.instrument_id "
        "LEFT JOIN channels c ON c.id=l.channel_id "
        "WHERE l.driver_id=? AND o.superseded_by IS NULL ORDER BY o.trade_date DESC LIMIT ?",
        (d["id"], limit)))
    ex = [l["excess_pct"] for l in links if l["excess_pct"] is not None]
    conf = [l["confidence"] for l in links if l["confidence"] is not None]
    verdicts = [l["verdict"] for l in links if l["verdict"]]
    return {"found": True, "driver": d, "n": len(links), "links": links, "stats": {
        "n_measured": len(ex),
        "mean_excess_pct": round(sum(ex) / len(ex), 2) if ex else None,
        "median_excess_pct": round(statistics.median(ex), 2) if ex else None,
        "mean_confidence": round(sum(conf) / len(conf), 2) if conf else None,
        "verdicts": {v: verdicts.count(v) for v in set(verdicts)} or None,
        "channels": _rows(con.execute(
            "SELECT c.slug, count(*) n FROM links l JOIN channels c ON c.id=l.channel_id "
            "JOIN observations o ON o.id=l.observation_id "
            "WHERE l.driver_id=? AND o.superseded_by IS NULL GROUP BY c.slug ORDER BY n DESC",
            (d["id"],))),
    }}


def channel(con, slug, limit=40):
    c = con.execute("SELECT * FROM channels WHERE slug=?", (slug,)).fetchone()
    if not c:
        return {"found": False, "slug": slug, "known": _rows(con.execute(
            "SELECT slug, display FROM channels ORDER BY slug"))}
    links = _rows(con.execute(
        "SELECT l.id, l.direction, l.confidence, l.excess_pct, l.verdict, i.symbol, d.slug driver, "
        "o.trade_date, o.text FROM links l JOIN observations o ON o.id=l.observation_id "
        "JOIN instruments i ON i.id=l.instrument_id LEFT JOIN drivers d ON d.id=l.driver_id "
        "WHERE l.channel_id=? AND o.superseded_by IS NULL ORDER BY o.trade_date DESC LIMIT ?",
        (c["id"], limit)))
    ex = [l["excess_pct"] for l in links if l["excess_pct"] is not None]
    return {"found": True, "channel": dict(c), "n": len(links), "links": links,
            "mean_excess_pct": round(sum(ex) / len(ex), 2) if ex else None}


def instrument(con, symbol, limit=30):
    i = con.execute(
        "SELECT i.* FROM instruments i JOIN instrument_aliases a ON a.instrument_id=i.id "
        "WHERE a.alias=? OR i.symbol=?", (symbol.lower(), symbol)).fetchone()
    if not i:
        return {"found": False, "symbol": symbol}
    links = _rows(con.execute(
        "SELECT l.id, l.direction, l.confidence, l.move_measured_pct, l.excess_pct, l.verdict, "
        "d.slug driver, c.slug channel, o.id observation_id, o.trade_date, o.text FROM links l "
        "JOIN observations o ON o.id=l.observation_id LEFT JOIN drivers d ON d.id=l.driver_id "
        "LEFT JOIN channels c ON c.id=l.channel_id "
        "WHERE l.instrument_id=? AND o.superseded_by IS NULL ORDER BY o.trade_date DESC LIMIT ?",
        (i["id"], limit)))
    return {"found": True, "instrument": dict(i), "n": len(links), "links": links,
            "drivers_blamed": _rows(con.execute(
                "SELECT d.slug, count(*) n FROM links l JOIN drivers d ON d.id=l.driver_id "
                "JOIN observations o ON o.id=l.observation_id "
                "WHERE l.instrument_id=? AND o.superseded_by IS NULL "
                "GROUP BY d.slug ORDER BY n DESC", (i["id"],)))}


def contradicts(con, symbol, direction, days=14):
    """Recent claims on the same name pointing the other way."""
    opp = {"up": "down", "down": "up"}.get(direction)
    if not opp:
        return []
    since = (now() - timedelta(days=days)).date().isoformat()
    return _rows(con.execute(
        "SELECT l.id, l.direction, o.id observation_id, o.trade_date, o.text, d.slug driver "
        "FROM links l JOIN observations o ON o.id=l.observation_id "
        "JOIN instruments i ON i.id=l.instrument_id LEFT JOIN drivers d ON d.id=l.driver_id "
        "WHERE i.symbol=? AND l.direction=? AND o.trade_date>=? AND o.superseded_by IS NULL "
        "ORDER BY o.trade_date DESC LIMIT 8", (symbol, opp, since)))


def context(con, instruments=(), drivers=(), days=30):
    """Pre-write intel: what has already been said about these names."""
    return {
        "instruments": [instrument(con, s, 8) for s in instruments],
        "drivers": [driver(con, s, 8) for s in drivers],
        "recent": recent(con, 10),
    }


def flags_vocab(con):
    return _rows(con.execute("SELECT slug, severity, description, auto FROM flags ORDER BY severity, slug"))


def registries(con):
    return {
        "drivers": _rows(con.execute(
            "SELECT d.slug, d.kind, d.display, d.dupe_of, "
            "(SELECT count(*) FROM links l WHERE l.driver_id=d.id) n "
            "FROM drivers d ORDER BY n DESC, d.slug")),
        "channels": _rows(con.execute(
            "SELECT c.slug, c.display, (SELECT count(*) FROM links l WHERE l.channel_id=c.id) n "
            "FROM channels c ORDER BY n DESC, c.slug")),
        "instruments": _rows(con.execute(
            "SELECT i.symbol, i.kind, i.display, i.benchmark_symbol, i.sector17, "
            "(SELECT count(*) FROM links l WHERE l.instrument_id=i.id) n "
            "FROM instruments i ORDER BY n DESC, i.symbol")),
    }


def validate(con):
    problems = []
    q = lambda s, *a: con.execute(s, a).fetchone()[0]
    orphan = q("SELECT count(*) FROM links l LEFT JOIN observations o ON o.id=l.observation_id "
               "WHERE o.id IS NULL")
    if orphan:
        problems.append(f"{orphan} links with no observation")
    unmeasured = q("SELECT count(*) FROM links WHERE move_measured_pct IS NULL")
    dupes = q("SELECT count(*) FROM drivers WHERE dupe_of IS NOT NULL")
    agent_self = q("SELECT count(*) FROM observations WHERE author='agent' AND supersedes_id IS NULL")
    return {"ok": not problems, "problems": problems, "counts": {
        "observations": q("SELECT count(*) FROM observations"),
        "links": q("SELECT count(*) FROM links"),
        "unmeasured_links": unmeasured,
        "drivers": q("SELECT count(*) FROM drivers"),
        "flagged_dupe_drivers": dupes,
        "instruments": q("SELECT count(*) FROM instruments"),
        "sources": q("SELECT count(*) FROM sources"),
        "agent_entries": agent_self,
    }}

"""The only pen. Nothing enters the store except through here.

Invalid input writes nothing at all — the whole call rolls back and the errors
come back as JSON. Substance is never UPDATEd: corrections supersede.
"""
import json
import statistics
from datetime import datetime, timedelta

import book
import brief
import measure as M
import registry as R
from db import JST, MEDIA_DIR, connect, iso, now, session_of

DIRECTIONS = ("up", "down", "flat")
SOURCE_KINDS = ("chart", "article", "post", "quote", "data")


class Invalid(Exception):
    def __init__(self, errors):
        self.errors = errors if isinstance(errors, list) else [errors]


def _num(v, lo=None, hi=None, name=""):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise Invalid(f"{name}: not a number ({v!r})")
    if lo is not None and f < lo or hi is not None and f > hi:
        raise Invalid(f"{name}: out of range [{lo}, {hi}] ({f})")
    return f


def _when(payload):
    at = payload.get("at")
    dt = datetime.fromisoformat(at).astimezone(JST) if at else now()
    trade_date = payload.get("trade_date") or dt.date().isoformat()
    return iso(dt), trade_date, payload.get("session") or session_of(dt)


# ------------------------------------------------------------------- statistics

def driver_history(con, driver_id, exclude_link=None):
    rows = con.execute(
        "SELECT l.direction, l.confidence, l.move_measured_pct, l.excess_pct, l.verdict, "
        "o.trade_date FROM links l JOIN observations o ON o.id=l.observation_id "
        "WHERE l.driver_id=? AND o.superseded_by IS NULL AND l.id<>COALESCE(?,-1) "
        "ORDER BY o.trade_date DESC LIMIT 60", (driver_id, exclude_link)).fetchall()
    if not rows:
        return {"n": 0}
    ex = [r["excess_pct"] for r in rows if r["excess_pct"] is not None]
    verdicts = [r["verdict"] for r in rows if r["verdict"]]
    return {
        "n": len(rows),
        "last_seen": rows[0]["trade_date"],
        "mean_excess_pct": round(sum(ex) / len(ex), 2) if ex else None,
        "n_measured": len(ex),
        "verdicts": {v: verdicts.count(v) for v in set(verdicts)} or None,
    }


def instrument_history(con, instrument_id, days=30, exclude_link=None):
    since = (now() - timedelta(days=days)).date().isoformat()
    rows = con.execute(
        "SELECT l.id, l.direction, l.confidence, o.id oid, o.trade_date, o.text, d.slug driver "
        "FROM links l JOIN observations o ON o.id=l.observation_id "
        "LEFT JOIN drivers d ON d.id=l.driver_id "
        "WHERE l.instrument_id=? AND o.trade_date>=? AND o.superseded_by IS NULL "
        "AND l.id<>COALESCE(?,-1) ORDER BY o.trade_date DESC LIMIT 12",
        (instrument_id, since, exclude_link)).fetchall()
    return [dict(r) for r in rows]


def confidence_spread(con, limit=20):
    rows = con.execute(
        "SELECT confidence FROM links WHERE confidence IS NOT NULL "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    vals = [r["confidence"] for r in rows]
    if len(vals) < 8:
        return {"n": len(vals)}
    return {"n": len(vals), "stdev": round(statistics.pstdev(vals), 3),
            "min": min(vals), "max": max(vals)}


# ------------------------------------------------------------------------ write

def add(payload, con=None):
    own = con is None
    con = con or connect()
    errors = []
    raw = (payload.get("raw") or "").strip()
    text = (payload.get("text") or "").strip()
    if not raw:
        errors.append("raw: required — his verbatim words are the data")
    if not text:
        errors.append("text: required — your cleaned one-liner")
    author = payload.get("author", "self")
    if author not in ("self", "agent"):
        errors.append("author: must be self|agent")
    if errors:
        raise Invalid(errors)

    at, trade_date, sess = _when(payload)
    ts = iso(now())
    notes = []

    try:
        con.execute("BEGIN")
        cur = con.execute(
            "INSERT INTO observations(at,trade_date,session,raw,text,source,author,supersedes_id,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (at, trade_date, sess, raw, text, payload.get("source", "telegram"), author,
             payload.get("supersedes"), ts))
        oid = cur.lastrowid
        if payload.get("supersedes"):
            prev = con.execute("SELECT id FROM observations WHERE id=?", (payload["supersedes"],)).fetchone()
            if not prev:
                raise Invalid(f"supersedes: observation {payload['supersedes']} does not exist")
            con.execute("UPDATE observations SET superseded_by=? WHERE id=?", (oid, prev["id"]))

        link_ids, instruments, intel_links = [], [], []
        for seq, l in enumerate(payload.get("links") or []):
            ins, created, err = R.resolve_instrument(con, l.get("instrument", ""))
            if err:
                raise Invalid(f"links[{seq}].instrument: {err}")
            if created:
                notes.append(f"new instrument {ins['symbol']} ({ins['display']})")
            R.touch(con, "instruments", ins["id"])
            instruments.append(ins)

            direction = (l.get("direction") or "").lower()
            if direction not in DIRECTIONS:
                raise Invalid(f"links[{seq}].direction: must be one of {DIRECTIONS}")

            drv = chn = None
            if l.get("driver"):
                drv, dcreated, dnote = R.resolve_driver(con, l["driver"], l.get("driver_kind"))
                R.touch(con, "drivers", drv["id"])
                if dcreated:
                    kind_note = " ⚠ kind unset — pass driver_kind" if drv["kind"] == "unknown" else ""
                    notes.append(f"new driver {drv['slug']} ({drv['kind']})"
                                 + (f" {dnote}" if dnote else "") + kind_note)
            if l.get("channel"):
                chn, ccreated, cnote = R.resolve_channel(con, l["channel"])
                R.touch(con, "channels", chn["id"])
                if ccreated:
                    notes.append(f"new channel {chn['slug']}" + (f" {cnote}" if cnote else ""))

            bench = R.benchmark_for(ins["kind"], ins.get("sector17"),
                                    l.get("benchmark") or ins.get("benchmark_symbol"))
            m = M.measure(con, ins["symbol"], bench, trade_date)
            im, bm = m["instrument"], (m["benchmark"] or {})

            asserted = _num(l.get("move_asserted_pct"), -100, 1000, f"links[{seq}].move_asserted_pct")
            conf = _num(l.get("confidence"), 0, 1, f"links[{seq}].confidence")
            horizon = l.get("horizon_days")

            cur = con.execute(
                "INSERT INTO links(observation_id,seq,driver_id,channel_id,instrument_id,direction,"
                "move_asserted_pct,confidence,implication,horizon_days,move_measured_pct,"
                "benchmark_symbol,benchmark_move_pct,excess_pct,measured_at,measure_session,"
                "measure_note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid, seq, drv["id"] if drv else None, chn["id"] if chn else None, ins["id"],
                 direction, asserted, conf, l.get("implication"),
                 int(horizon) if horizon else None, im.get("move_pct"), bench,
                 bm.get("move_pct"), m.get("excess_pct"), m["measured_at"], m["status"],
                 m.get("note"), ts))
            lid = cur.lastrowid
            link_ids.append(lid)

            hist = driver_history(con, drv["id"], lid) if drv else {"n": 0}
            intel_links.append({
                "link_id": lid, "seq": seq, "instrument": ins["symbol"],
                "instrument_display": ins["display"], "driver": drv["slug"] if drv else None,
                "channel": chn["slug"] if chn else None, "direction": direction,
                "measure": m, "driver_history": hist,
                "instrument_recent": instrument_history(con, ins["id"], 30, lid),
            })

            # a soft, snapshotted reference to the pre-open brief
            if drv:
                mt = brief.match(l.get("driver", ""), trade_date)
                if mt:
                    e = mt["event"]
                    con.execute(
                        "INSERT INTO feed_refs(observation_id,link_id,feed_event_id,feed_thread_id,"
                        "brief_date,title_snapshot,why_snapshot,risk_direction,risk_score,created_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (oid, lid, e["id"], e["thread_id"], e["date_jst"],
                         e["title_en"] or e["title_ja"], e["why"], e["risk_direction"],
                         e["risk_score"], ts))
                    intel_links[-1]["brief"] = {**mt, "event": dict(e)}

        # sources bind to the edge they are evidence for, not just the episode
        for s in payload.get("sources") or []:
            kind = (s.get("kind") or "").lower()
            if kind not in SOURCE_KINDS:
                raise Invalid(f"sources: kind must be one of {SOURCE_KINDS} (got {kind!r})")
            ls = s.get("link_seq")
            lid = link_ids[ls] if isinstance(ls, int) and 0 <= ls < len(link_ids) else None
            pub = s.get("published_at")
            precedes = None
            if pub:
                try:
                    precedes = 1 if datetime.fromisoformat(pub).astimezone(JST).date().isoformat() \
                        <= trade_date else 0
                except ValueError:
                    raise Invalid(f"sources.published_at: not an ISO timestamp ({pub!r})")
            con.execute(
                "INSERT INTO sources(observation_id,link_id,kind,url,title,publisher,published_at,"
                "path,chart_symbol,chart_timeframe,markup,precedes_move,note,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid, lid, kind, s.get("url"), s.get("title"), s.get("publisher"), pub,
                 s.get("path"), s.get("chart_symbol"), s.get("chart_timeframe"),
                 s.get("markup"), precedes, s.get("note"), ts))

        # freeze the exposure that any position-serving flag will cite
        bk, hits = book.exposure_for(instruments)
        for t in hits:
            con.execute(
                "INSERT INTO book_snapshot(observation_id,trade_id,symbol,direction,status,quantity,"
                "entry_price,r_multiple,pnl,entry_at,setup,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid, t["id"], t["symbol"], t["direction"], t["status"], t["quantity"],
                 t["entry_price"], t["r_multiple"], t["pnl"], t["entry_at"], t["setup"], ts))

        for f in payload.get("flags") or []:
            _insert_flag(con, f, oid, link_ids, ts)

        con.commit()
    except Invalid:
        con.rollback()
        raise
    except Exception as e:
        con.rollback()
        raise Invalid(f"{type(e).__name__}: {e}")

    intel = {
        "links": intel_links,
        "book": {"available": bk["available"], "open": bk["open"],
                 "recent_closed": bk["recent_closed"], "related": hits},
        "confidence_spread": confidence_spread(con),
        "signals": _signals(intel_links, hits, payload),
        "notes": notes,
    }
    out = {"ok": True, "observation_id": oid, "trade_date": trade_date, "session": sess,
           "link_ids": link_ids, "intel": intel}
    if own:
        con.close()
    return out


def _signals(intel_links, related, payload):
    """Deterministic, code-owned observations about the entry. Not judgments —
    the model raises flags, this only hands it arithmetic it cannot fudge."""
    sigs = {"suggested_flags": []}
    srcs = payload.get("sources") or []
    linked_srcs = {s.get("link_seq") for s in srcs}
    for il in intel_links:
        seq, im = il["seq"], il["measure"]["instrument"]
        meas, ex = im.get("move_pct"), il["measure"].get("excess_pct")
        ass = (payload["links"][seq] or {}).get("move_asserted_pct")
        entry = {"seq": seq, "instrument": il["instrument"]}
        if ass is not None and meas is not None:
            entry["asserted_vs_measured"] = round(float(ass) - meas, 2)
            if abs(float(ass) - meas) >= max(1.0, 0.5 * abs(meas)):
                sigs["suggested_flags"].append(
                    {"seq": seq, "flag": "unverified-magnitude",
                     "why": f"asserted {ass}%, measured {meas}%"})
        if ex is not None and meas not in (None, 0):
            entry["idiosyncratic_share"] = round(abs(ex) / abs(meas), 2) if meas else None
            if abs(meas) > 0.3 and abs(ex) < 0.5 * abs(meas):
                sigs["suggested_flags"].append(
                    {"seq": seq, "flag": "no-baseline",
                     "why": f"{il['instrument']} {meas}% vs benchmark "
                            f"{il['measure']['benchmark']['move_pct']}% — excess only {ex}pp"})
        if im.get("status") in ("intraday", "stale", "unavailable"):
            entry["measure_caveat"] = im.get("status")
        if seq not in linked_srcs and il.get("driver"):
            sigs["suggested_flags"].append(
                {"seq": seq, "flag": "evidence-free", "why": "no source attached to this edge"})
        for t in related:
            aligned = (il["direction"] == "down" and t["direction"] == "short") or \
                      (il["direction"] == "up" and t["direction"] == "long")
            if aligned and t["status"] == "open":
                sigs["suggested_flags"].append(
                    {"seq": seq, "flag": "position-serving",
                     "why": f"open {t['direction']} {t['symbol']} since {t['entry_at'][:10]}"})
        sigs.setdefault("links", []).append(entry)
    return sigs


def _insert_flag(con, f, oid, link_ids, ts):
    slug = f.get("flag")
    if not con.execute("SELECT 1 FROM flags WHERE slug=?", (slug,)).fetchone():
        raise Invalid(f"flags: unknown flag '{slug}' — run `granger flags` for the vocabulary")
    by = f.get("raised_by", "agent")
    ls = f.get("link_seq")
    lid = f.get("link") or (link_ids[ls] if isinstance(ls, int) and 0 <= ls < len(link_ids) else None)
    if lid:
        con.execute("INSERT OR REPLACE INTO link_flags(link_id,flag,note,raised_by,created_at) "
                    "VALUES(?,?,?,?,?)", (lid, slug, f.get("note"), by, ts))
    else:
        con.execute("INSERT OR REPLACE INTO observation_flags(observation_id,flag,note,raised_by,"
                    "created_at) VALUES(?,?,?,?,?)", (oid, slug, f.get("note"), by, ts))


def flag(payload, con=None):
    """Raise flags after seeing the measurements. Repeatable, idempotent."""
    own = con is None
    con = con or connect()
    items = payload.get("flags") or [payload]
    ts = iso(now())
    applied = []
    try:
        con.execute("BEGIN")
        for f in items:
            lid, oid = f.get("link"), f.get("observation")
            if not lid and not oid:
                raise Invalid("each flag needs a link or observation id")
            if lid and not con.execute("SELECT 1 FROM links WHERE id=?", (lid,)).fetchone():
                raise Invalid(f"link {lid} does not exist")
            if oid and not con.execute("SELECT 1 FROM observations WHERE id=?", (oid,)).fetchone():
                raise Invalid(f"observation {oid} does not exist")
            _insert_flag(con, f, oid, [], ts)
            applied.append({"flag": f.get("flag"), "link": lid, "observation": oid})
        con.commit()
    except Invalid:
        con.rollback()
        raise
    except Exception as e:
        con.rollback()
        raise Invalid(f"{type(e).__name__}: {e}")
    if own:
        con.close()
    return {"ok": True, "applied": applied}

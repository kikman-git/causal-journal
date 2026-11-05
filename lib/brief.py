"""Read-only view of the 07:00 daily-feeds brief.

The brief is an independent, *pre-open* read of the same causes. That makes it
both a lookup and a bias check: restating its channel is not an observation.
"""
import difflib
import sqlite3

from db import FEEDS_DB, attach_readonly, normalize


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    return con if attach_readonly(con, FEEDS_DB, "feeds") else None


def events_for(date):
    con = _con()
    if con is None:
        return {"available": False, "events": []}
    try:
        rows = con.execute(
            "SELECT e.id, e.title_en, e.title_ja, e.risk_score, e.risk_direction, e.tier, e.rank, "
            "e.why, e.thread_id, b.date_jst FROM feeds.events e JOIN feeds.briefs b ON b.id=e.brief_id "
            "WHERE b.date_jst=? ORDER BY e.rank", (date,)).fetchall()
        return {"available": True, "events": [dict(r) for r in rows]}
    except sqlite3.Error as e:
        return {"available": False, "error": str(e), "events": []}
    finally:
        con.close()


def match(text, date, threshold=0.42):
    """Best brief event for a driver phrase. Snapshot what we find — daily-feeds
    CASCADE-deletes events on a --force re-triage, so the id alone is not durable."""
    got = events_for(date)
    if not got["available"] or not got["events"]:
        return None
    key = normalize(text or "")
    best, ratio = None, 0.0
    for e in got["events"]:
        for cand in (e["title_en"], e["title_ja"], e["why"]):
            if not cand:
                continue
            r = difflib.SequenceMatcher(None, key, normalize(cand)).ratio()
            if r > ratio:
                best, ratio = e, r
    if best and ratio >= threshold:
        return {"event": best, "ratio": round(ratio, 2)}
    return None

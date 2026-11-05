"""The Sunday read. Cross-entry patterns are the only place most biases live —
no single ack can see them.

This builds the arithmetic. The model writes the prose, and may not add a
number that is not in here.
"""
import statistics
from collections import Counter
from datetime import timedelta

from db import now


def bundle(con, asof=None, days=7):
    end = asof or now().date().isoformat()
    start = (now().date() - timedelta(days=days - 1)).isoformat() if not asof else \
        (__import__("datetime").date.fromisoformat(asof) - timedelta(days=days - 1)).isoformat()

    obs = [dict(r) for r in con.execute(
        "SELECT * FROM observations WHERE trade_date BETWEEN ? AND ? AND superseded_by IS NULL "
        "AND author='self' ORDER BY trade_date, at", (start, end))]
    links = [dict(r) for r in con.execute(
        "SELECT l.*, d.slug driver, d.kind driver_kind, c.slug channel, i.symbol, i.kind instrument_kind, "
        "o.trade_date, o.text, o.id oid FROM links l JOIN observations o ON o.id=l.observation_id "
        "LEFT JOIN drivers d ON d.id=l.driver_id LEFT JOIN channels c ON c.id=l.channel_id "
        "JOIN instruments i ON i.id=l.instrument_id "
        "WHERE o.trade_date BETWEEN ? AND ? AND o.superseded_by IS NULL ORDER BY o.trade_date",
        (start, end))]

    drivers = Counter(l["driver"] for l in links if l["driver"])
    channels = Counter(l["channel"] for l in links if l["channel"])
    conf = [l["confidence"] for l in links if l["confidence"] is not None]
    errs = [(l["move_asserted_pct"] - l["move_measured_pct"], l["symbol"], l["trade_date"])
            for l in links if l["move_asserted_pct"] is not None and l["move_measured_pct"] is not None]

    # the same name argued both ways inside one week, without either side noting it
    by_symbol = {}
    for l in links:
        by_symbol.setdefault(l["symbol"], []).append(l)
    contradictions = []
    for sym, ls in by_symbol.items():
        dirs = {l["direction"] for l in ls if l["direction"] in ("up", "down")}
        if len(dirs) > 1:
            contradictions.append({"symbol": sym, "links": [
                {"id": l["id"], "trade_date": l["trade_date"], "direction": l["direction"],
                 "driver": l["driver"], "text": l["text"]} for l in ls]})

    # per-channel: is the mechanism you cite most doing any work?
    ch_perf = []
    for ch in channels:
        ex = [l["excess_pct"] for l in links if l["channel"] == ch and l["excess_pct"] is not None]
        signed = [l["excess_pct"] * (1 if l["direction"] == "up" else -1)
                  for l in links if l["channel"] == ch and l["excess_pct"] is not None
                  and l["direction"] in ("up", "down")]
        ch_perf.append({"channel": ch, "n": channels[ch], "n_measured": len(ex),
                        "mean_excess_pct": round(sum(ex) / len(ex), 2) if ex else None,
                        "mean_signed_excess_pct": round(sum(signed) / len(signed), 2) if signed else None})
    ch_perf.sort(key=lambda x: -x["n"])

    flags = [dict(r) for r in con.execute(
        "SELECT lf.flag, count(*) n FROM link_flags lf JOIN links l ON l.id=lf.link_id "
        "JOIN observations o ON o.id=l.observation_id WHERE o.trade_date BETWEEN ? AND ? "
        "GROUP BY lf.flag ORDER BY n DESC", (start, end))]

    sourced = con.execute(
        "SELECT count(DISTINCT l.id) FROM links l JOIN observations o ON o.id=l.observation_id "
        "LEFT JOIN sources s ON s.link_id=l.id WHERE o.trade_date BETWEEN ? AND ? AND s.id IS NOT NULL",
        (start, end)).fetchone()[0]
    parroted = con.execute(
        "SELECT count(DISTINCT f.link_id) FROM feed_refs f JOIN links l ON l.id=f.link_id "
        "JOIN observations o ON o.id=l.observation_id WHERE o.trade_date BETWEEN ? AND ? "
        "AND o.superseded_by IS NULL", (start, end)).fetchone()[0]

    top = drivers.most_common(1)[0] if drivers else None
    return {
        "window": {"start": start, "end": end, "days": days},
        "counts": {"observations": len(obs), "links": len(links),
                   "distinct_drivers": len(drivers), "distinct_instruments": len(by_symbol),
                   "sourced_links": sourced, "brief_matched_links": parroted},
        "driver_concentration": {
            "top": top[0] if top else None,
            "top_n": top[1] if top else 0,
            "top_share": round(top[1] / len(links), 2) if top and links else None,
            "counts": drivers.most_common(),
        },
        "channels": ch_perf,
        "confidence": {
            "n": len(conf),
            "mean": round(sum(conf) / len(conf), 2) if conf else None,
            "stdev": round(statistics.pstdev(conf), 3) if len(conf) > 1 else None,
            "min": min(conf) if conf else None, "max": max(conf) if conf else None,
        },
        "magnitude_error": {
            "n": len(errs),
            "mean_abs_pp": round(sum(abs(e[0]) for e in errs) / len(errs), 2) if errs else None,
            "worst": sorted(errs, key=lambda e: -abs(e[0]))[:3],
        },
        "contradictions": contradictions,
        "flags_raised": flags,
        "observations": [{"id": o["id"], "trade_date": o["trade_date"], "session": o["session"],
                          "text": o["text"]} for o in obs],
    }

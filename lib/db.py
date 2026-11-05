"""Connection, schema init, seeding, and the JST clock everything else agrees on."""
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.expanduser(os.environ.get("GRANGER_DB", "~/.openclaw/data/causal.sqlite"))
MEDIA_DIR = os.path.expanduser(os.environ.get("GRANGER_MEDIA", "~/.openclaw/data/causal-media"))
FEEDS_DB = os.path.expanduser(os.environ.get("GRANGER_FEEDS_DB", "~/.openclaw/data/daily-feeds.sqlite"))
BOOK_DB = os.path.expanduser(os.environ.get("GRANGER_BOOK_DB", "~/trading-diary/data/journal.db"))
SCHEMA_VERSION = "1"


def now():
    return datetime.now(JST)


def iso(dt):
    return dt.isoformat(timespec="seconds")


# TSE runs 09:00-11:30, 12:30-15:30 JST (close extended to 15:30 in Nov 2024).
SESSIONS = (
    (time(0, 0), time(8, 0), "overnight"),
    (time(8, 0), time(9, 0), "pre-open"),
    (time(9, 0), time(11, 30), "morning"),
    (time(11, 30), time(12, 30), "lunch"),
    (time(12, 30), time(15, 30), "afternoon"),
    (time(15, 30), time(18, 0), "close"),
    (time(18, 0), time(23, 59, 59), "after-hours"),
)


def session_of(dt):
    if dt.weekday() >= 5:
        return "weekend"
    t = dt.timetz().replace(tzinfo=None)
    for lo, hi, name in SESSIONS:
        if lo <= t < hi:
            return name
    return "after-hours"


def market_closed_for(dt):
    """Has the cash session for dt's own date finished?"""
    return dt.weekday() < 5 and dt.timetz().replace(tzinfo=None) >= time(15, 30)


def slugify(s):
    s = unicodedata.normalize("NFKC", (s or "").strip())
    s = s.lower()
    s = re.sub(r"[^\w぀-ヿ一-鿿]+", "-", s, flags=re.UNICODE)
    return re.sub(r"-+", "-", s).strip("-")


def normalize(s):
    """Alias key: case-folded, width-folded, punctuation-stripped."""
    s = unicodedata.normalize("NFKC", (s or "").strip()).lower()
    return re.sub(r"[\s\-_/、。,.()（）]+", "", s)


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def attach_readonly(con, path, name):
    """Read-only ATTACH. Returns False when the file is absent — a missing
    trading-diary or brief must degrade, never abort a capture."""
    if not os.path.exists(path):
        return False
    try:
        con.execute("ATTACH DATABASE ? AS " + name, (f"file:{path}?mode=ro&immutable=0",))
        return True
    except sqlite3.Error:
        return False


def init(con=None, verbose=False):
    own = con is None
    con = con or connect()
    with open(os.path.join(ROOT, "lib", "schema.sql")) as f:
        con.executescript(f.read())
    ts = iso(now())
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (SCHEMA_VERSION,))

    with open(os.path.join(ROOT, "seed", "flags.json")) as f:
        for fl in json.load(f):
            con.execute(
                "INSERT INTO flags(slug,severity,description,auto) VALUES(?,?,?,?) "
                "ON CONFLICT(slug) DO UPDATE SET severity=excluded.severity, "
                "description=excluded.description, auto=excluded.auto",
                (fl["slug"], fl["severity"], fl["description"], fl["auto"]))

    with open(os.path.join(ROOT, "seed", "channels.json")) as f:
        for ch in json.load(f):
            con.execute(
                "INSERT INTO channels(slug,display,note,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(slug) DO NOTHING", (ch["slug"], ch["display"], ch.get("note"), ts))

    with open(os.path.join(ROOT, "seed", "instruments.json")) as f:
        for ins in json.load(f):
            con.execute(
                "INSERT INTO instruments(symbol,kind,display,benchmark_symbol,created_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(symbol) DO NOTHING",
                (ins["symbol"], ins["kind"], ins["display"], ins.get("benchmark"), ts))
            row = con.execute("SELECT id FROM instruments WHERE symbol=?", (ins["symbol"],)).fetchone()
            for a in ins.get("aliases", []) + [ins["symbol"], ins["display"]]:
                con.execute("INSERT OR IGNORE INTO instrument_aliases(alias,instrument_id) VALUES(?,?)",
                            (normalize(a), row["id"]))
    con.commit()
    if verbose:
        n = lambda t: con.execute(f"SELECT count(*) c FROM {t}").fetchone()["c"]
        print(f"db={DB_PATH} flags={n('flags')} channels={n('channels')} instruments={n('instruments')}")
    if own:
        con.close()
    return True

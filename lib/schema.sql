-- Granger — causal attribution journal.
-- Three registries, one episode table, one edge table.
-- Substance is immutable: corrections supersede, they never UPDATE.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- ---------------------------------------------------------------- registries

CREATE TABLE IF NOT EXISTS drivers (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  slug         TEXT NOT NULL UNIQUE,          -- kebab: jpy-strength, boj-hawkish-presser
  kind         TEXT NOT NULL,                 -- macro-print|policy|fx|rates|flows|single-name|
                                              -- sector-rotation|geopolitics|technical|unknown
  display      TEXT NOT NULL,
  dupe_of      INTEGER REFERENCES drivers(id),-- near-miss; capture never blocks on it
  created_at   TEXT NOT NULL,
  last_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS driver_aliases (
  alias     TEXT PRIMARY KEY,                 -- normalised free text -> driver
  driver_id INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS instruments (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol           TEXT NOT NULL UNIQUE,      -- yahoo symbol: 6920.T, ^N225, JPY=X
  kind             TEXT NOT NULL,             -- equity|index|etf|fx|future|rate|commodity
  display          TEXT NOT NULL,
  benchmark_symbol TEXT,                      -- what "outperformed" is measured against
  sector33         TEXT,
  sector17         TEXT,
  scale            TEXT,
  created_at       TEXT NOT NULL,
  last_seen_at     TEXT
);
CREATE TABLE IF NOT EXISTS instrument_aliases (
  alias         TEXT PRIMARY KEY,
  instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS channels (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  slug         TEXT NOT NULL UNIQUE,          -- fx-translation, rate-path, sox-beta
  display      TEXT NOT NULL,
  note         TEXT,
  created_at   TEXT NOT NULL,
  last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS flags (
  slug        TEXT PRIMARY KEY,
  severity    TEXT NOT NULL,                  -- low|med|high
  description TEXT NOT NULL,
  auto        INTEGER NOT NULL DEFAULT 1      -- may the agent raise it unprompted
);

-- ------------------------------------------------------------------ episodes

CREATE TABLE IF NOT EXISTS observations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  at            TEXT NOT NULL,                -- ISO8601 +09:00, when it was observed
  trade_date    TEXT NOT NULL,                -- YYYY-MM-DD JST, the session it is ABOUT
  session       TEXT NOT NULL,                -- pre-open|morning|lunch|afternoon|close|
                                              -- after-hours|overnight|weekend
  raw           TEXT NOT NULL,                -- verbatim. his words are the data
  text          TEXT NOT NULL,                -- cleaned one-liner
  source        TEXT NOT NULL DEFAULT 'telegram',
  author        TEXT NOT NULL DEFAULT 'self', -- self|agent
  supersedes_id INTEGER REFERENCES observations(id),
  superseded_by INTEGER REFERENCES observations(id),
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS obs_trade_date ON observations(trade_date);
CREATE INDEX IF NOT EXISTS obs_at         ON observations(at);

CREATE TABLE IF NOT EXISTS links (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
  seq            INTEGER NOT NULL DEFAULT 0,
  driver_id      INTEGER REFERENCES drivers(id),
  channel_id     INTEGER REFERENCES channels(id),
  instrument_id  INTEGER NOT NULL REFERENCES instruments(id),
  direction      TEXT NOT NULL,               -- up|down|flat

  -- what he claimed
  move_asserted_pct REAL,
  confidence        REAL,                     -- 0..1
  implication       TEXT,                     -- optional falsifiable statement
  horizon_days      INTEGER,

  -- what the tape says. code owns every one of these
  move_measured_pct  REAL,
  benchmark_symbol   TEXT,
  benchmark_move_pct REAL,
  excess_pct         REAL,
  measured_at        TEXT,
  measure_session    TEXT,                    -- settled|intraday|stale|unavailable
  measure_note       TEXT,

  -- the web UI's columns. the agent never writes these
  verdict         TEXT,                       -- hit|miss|partial|void
  resolved_at     TEXT,
  resolution_note TEXT,

  supersedes_id INTEGER REFERENCES links(id),
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS links_obs    ON links(observation_id, seq);
CREATE INDEX IF NOT EXISTS links_driver ON links(driver_id);
CREATE INDEX IF NOT EXISTS links_instr  ON links(instrument_id);

CREATE TABLE IF NOT EXISTS link_flags (
  link_id    INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE,
  flag       TEXT NOT NULL REFERENCES flags(slug),
  note       TEXT,
  raised_by  TEXT NOT NULL DEFAULT 'agent',   -- agent|self|code
  created_at TEXT NOT NULL,
  PRIMARY KEY (link_id, flag)
);
CREATE TABLE IF NOT EXISTS observation_flags (
  observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
  flag           TEXT NOT NULL REFERENCES flags(slug),
  note           TEXT,
  raised_by      TEXT NOT NULL DEFAULT 'agent',
  created_at     TEXT NOT NULL,
  PRIMARY KEY (observation_id, flag)
);

-- evidence. a chart proves the move; only a source can speak to the cause
CREATE TABLE IF NOT EXISTS sources (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
  link_id        INTEGER REFERENCES links(id) ON DELETE SET NULL,
  kind           TEXT NOT NULL,               -- chart|article|post|quote|data
  url            TEXT,
  title          TEXT,
  publisher      TEXT,
  published_at   TEXT,
  path           TEXT,                        -- relative to ~/.openclaw/data/causal-media/
  chart_symbol   TEXT,
  chart_timeframe TEXT,
  markup         TEXT,                        -- what HE drew on it
  precedes_move  INTEGER,                     -- 1 published before the session, 0 after, NULL unknown
  note           TEXT,
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sources_obs ON sources(observation_id);

-- soft reference to the 07:00 brief. snapshotted: daily-feeds CASCADE-deletes
-- its events on a --force re-triage, so the id alone is not durable
CREATE TABLE IF NOT EXISTS feed_refs (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  observation_id INTEGER REFERENCES observations(id) ON DELETE CASCADE,
  link_id        INTEGER REFERENCES links(id) ON DELETE CASCADE,
  feed_event_id  INTEGER,
  feed_thread_id INTEGER,
  brief_date     TEXT,
  title_snapshot TEXT,
  why_snapshot   TEXT,
  risk_direction TEXT,
  risk_score     INTEGER,
  created_at     TEXT NOT NULL
);

-- the exposure that justified a position-serving flag, frozen at capture time.
-- the trade will close and be edited later; the flag must stay auditable
CREATE TABLE IF NOT EXISTS book_snapshot (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  observation_id INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
  trade_id       INTEGER,
  symbol         TEXT,
  direction      TEXT,
  status         TEXT,
  quantity       REAL,
  entry_price    REAL,
  r_multiple     REAL,
  pnl            REAL,
  entry_at       TEXT,
  setup          TEXT,
  created_at     TEXT NOT NULL
);

-- the yahoo endpoint is unofficial; daily-feeds hits it 7x/day, granger hits it per message
CREATE TABLE IF NOT EXISTS bars_cache (
  symbol     TEXT NOT NULL,
  date       TEXT NOT NULL,                   -- YYYY-MM-DD of the bar
  close      REAL NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (symbol, date)
);

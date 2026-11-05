# causal-journal

A **causal attribution journal** for Japanese equities, built for one owner. You write
down what moved and why you think it moved; Granger (a Telegram observer) files it,
measures it against the tape, and tells you where the reasoning is weak. Not a trade
journal (that's `trading-diary`), not a forecast (that's `daily-feeds`): this is the
record of *what you believed about cause and effect*, and the only system on this box
that checks it.

> **Did you find a mechanism, or did you find a story that fits?**

Decisions of record: **`SPEC.md`**. Working manual: **`CLAUDE.md`**.

## How it works

```
 Telegram @grangertbot -> OpenClaw agent `granger` + skill `causal-graph` --+
 OpenClaw cron `granger-weekly` (Sun 21:00 JST) ----------------------------+--> bin/granger
   bin/granger writes:   causal.sqlite (+ causal-media/)
   bin/granger reads:    trading-diary journal.db, daily-feeds.sqlite   (read-only)
   bin/granger fetches:  Yahoo chart endpoint (unofficial), cached in bars_cache
```

- One Telegram message = one **observation** (your verbatim words in `raw`, a cleaned
  one-liner in `text`) + 0..n **causal links**, each `driver -> channel -> instrument ->
  direction`, measured against a benchmark. Rival explanations of one move are two links
  on one observation, never two observations.
- **Measured, not asserted:** every market number originates in code (`lib/measure.py`).
  `move_asserted_pct` (yours) and `move_measured_pct` (the tape's) are both stored.
  Benchmark: TOPIX-17 sector ETF (1617-1633) for a TSE equity, TOPIX `1306.T` for an
  ETF, none for index/FX. `excess_pct` is the point.
- **Flags:** code suggests only mechanical ones (`unverified-magnitude`, `no-baseline`,
  `evidence-free`, `position-serving`); judgment flags (`single-factor`,
  `hindsight-framing`, ...) are the agent's. Vocabulary: `seed/flags.json`.
- **Capture never blocks:** the entry lands, then the critique rides in the same reply.
  `verdict` and `resolved_at` belong to the unbuilt web UI and stay NULL for now.

Illustrative output of one entry:

```
observation #148   2026-08-26 15:40 JST  · afternoon
raw: "himino was hawkish af, banks ripped like 3%"

  link 1  driver:   boj-himino-terminal-rate-signal  (policy)
          channel:  nim-expansion
          instrument: 1631.T  direction: up  conf 0.8
          asserted +3.0%  ·  measured +1.27%  ·  TOPIX +0.35%  ·  excess +0.92pp
          → brief evt #255 (risk-off 76)
          ⚠ unverified-magnitude · ⚠ parroting-brief
```

The three rules: **(1) Immutable substance:** corrections supersede, nothing is rewritten
(hindsight quietly editing the record is the bias this exists to catch). **(2) Numbers
only from code:** asserted and measured are both stored, always; the gap is the feature.
**(3) Registries, not free text:** drivers, channels and instruments are resolved entities
(aliases, `?dupe` flagging), which is what makes "every time I blamed the yen..." answerable.

## CLI

`bin/granger` is the only read/write path. Every command prints exactly one JSON object;
`{"ok": false, "errors": [...]}` (exit 1) means **nothing was written**.

```bash
bin/granger init                       # create the store, seed vocabularies (idempotent)
bin/granger add --json -               # the pen: one observation + links + sources (below)
bin/granger flag --json -              # raise flags after seeing the measurements
bin/granger revise --json -            # supersede an observation; never an edit ("supersedes")
bin/granger recent -n 20               # latest observations (--all includes superseded)
bin/granger show 148                   # one observation in full
bin/granger driver jpy-strength        # every time you blamed it
bin/granger channel fx-translation     # is the mechanism doing any work?
bin/granger instrument 6920.T          # what you've said about one name
bin/granger context --instrument 6920.T --driver jpy-strength  # pre-write intel (repeatable)
bin/granger contradicts 6920.T down    # recent claims pointing the other way (--days 14)
bin/granger flags                      # the flag vocabulary
bin/granger registries                 # the vocabulary, with usage counts
bin/granger book                       # open positions (read-only)
bin/granger brief --date 2026-08-26    # what the 07:00 brief said
bin/granger week                       # the Sunday bundle (--asof DATE, --days 7)
bin/granger measure 6920.T --benchmark 1625.T  # probe the tape; only the price cache is written
bin/granger resolve instrument 6920    # preview registry resolution (or: driver, channel)
bin/granger validate                   # integrity check and counts
```

`add` reads JSON from stdin (`-`) or a file. Required: `raw`, `text`. Optional: `at` (ISO
timestamp, default now JST), `trade_date`, `session`, `author`, `source`, `supersedes`,
`flags[]`, and:

- `links[]`: `instrument` + `direction` (`up|down|flat`) required; `driver`,
  `driver_kind`, `channel`, `move_asserted_pct`, `confidence` (0..1), `implication`,
  `horizon_days`, `benchmark` optional.
- `sources[]`: `kind` (`chart|article|post|quote|data`), `url`, `title`, `publisher`,
  `published_at`, `path`, `chart_symbol`, `chart_timeframe`, `markup`, `link_seq`, `note`.

## Setup

Python 3.12, standard library only (`zoneinfo` needs system tzdata): no venv, no pip.
Prices come from `query1.finance.yahoo.com` over HTTPS (no API key); offline, a
measurement is served from the cache (`cache-stale`) or reported `unavailable`.

```bash
bin/granger init       # creates the store + media dir; seeds flags, channels, instruments
bin/granger validate   # counts and integrity
GRANGER_DB=/tmp/try.sqlite GRANGER_MEDIA=/tmp/try-media bin/granger init   # scratch store
```

## Run / deploy

- No server, no unit file, no port. The CLI runs on demand: from the `causal-graph`
  skill (agent `granger`), from the `granger-weekly` cron job, and by hand.
- **Weekly read:** `openclaw cron` job `granger-weekly`, Sundays 21:00 JST (registered in
  OpenClaw, not in this repo). Code builds the arithmetic (`bin/granger week`); the model
  writes the prose. Under ~10 observations in the window it says the sample is thin.
- **Web UI: not built.** Planned `causal-web` (Next.js + `node:sqlite`, not
  `better-sqlite3`): loopback `127.0.0.1:18796` -> `tailscale serve` `:8452` ->
  https://openclaw-vps.tail0d53dc.ts.net:8452/ (port reserved in
  `~/bin/tailscale-serve-apps.sh`; nothing mapped, no directory yet). When it lands,
  `next start` needs `next build` first, and gateway restarts wipe serve mappings until
  `tailscale-serve-apps.timer` re-asserts them (within ~3 minutes).

## Configuration

No config file, no secrets, no API keys. Optional env vars, all path overrides:
`GRANGER_DB` (default `~/.openclaw/data/causal.sqlite`), `GRANGER_MEDIA`
(`~/.openclaw/data/causal-media`), `GRANGER_FEEDS_DB` (`~/.openclaw/data/daily-feeds.sqlite`)
and `GRANGER_BOOK_DB` (`~/trading-diary/data/journal.db`). Hard-coded: JST and TSE
sessions (`lib/db.py`), 15-minute price-cache freshness (`measure.py`), resolver
thresholds `SURE`/`MAYBE` = 0.88/0.72 (`registry.py`), 10-day recent-close window (`book.py`).

## Data

- `~/.openclaw/data/causal.sqlite` (WAL, so `-wal`/`-shm` sidecars): registries (`drivers`,
  `instruments`, `channels`; aliases for the first two), `observations`, `links`,
  `sources`, flag tables, `feed_refs`, `book_snapshot`, `bars_cache`.
- `~/.openclaw/data/causal-media/`: chart screenshots and attachments. `sources.path` is
  relative to it; the CLI only creates the directory and records the path.
- Read-only inputs (a missing file degrades, never aborts a capture):
  `~/trading-diary/data/journal.db` (open and recent trades; the exposure behind a
  `position-serving` flag is frozen in `book_snapshot`), `~/.openclaw/data/daily-feeds.sqlite`
  (07:00 brief, JPX ticker mirror with `sector17`).
- **Not in git, on purpose:** the store (private market views plus snapshots of positions
  and P&L), its WAL files, the media and any backups. Pushing this repo does not back
  them up, and there is no backup of the store yet.
- `seed/` **is** in git: generic vocabulary fixtures (14 flags, 12 channels, 26
  instruments: indices and TOPIX-17 ETFs), no personal data.

## Related

- Telegram **@grangertbot** -> OpenClaw agent `granger` (DM-allowlisted), workspace
  `~/.openclaw/workspace-granger/`. Skill `~/.openclaw/skills/causal-graph/SKILL.md`
  (outside this repo) is the bot's manual.
- Neighbours: `trading-diary` (the owner's trades; read, never written), `daily-feeds`
  (the 07:00 brief and ticker mirror; read, never written), `tse-scanner` (no
  relationship). The owner's life is Dr. Fell's (`myself-journal`), people are Boswell's.

## Gotchas

- Writes go through `lib/write.py`, reads through `lib/query.py`. Substance is never
  UPDATEd: `revise` inserts a new row and sets the old one's `superseded_by`, so every
  read view (including new ones) must filter `superseded_by IS NULL`.
- `measure_session` is `settled|intraday|stale|unavailable`. Never present an `intraday`
  number as a close; TSE closes 15:30 JST.
- The Yahoo endpoint is unofficial and will eventually break; `lib/measure.py` is the one
  place to fix it. Settled bars never refetch; today's refresh after 15 minutes.
- Brief event ids are not durable (`daily-feeds` CASCADE-deletes events on a `--force`
  re-triage), so `feed_refs` snapshots title, why, direction and score.
- Trading-diary symbols are free text ("NIKKEI 255 MICRO"); `book.relates()` matches
  loosely on purpose.
- `init` only creates what is missing (`IF NOT EXISTS`); there is no migration step.

## License

MIT. See [LICENSE](LICENSE).

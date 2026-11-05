# CLAUDE.md

Working manual for this repo. **Code is the source of truth**; this file
captures the *why* the code can't. `README.md` is the front page, `SPEC.md` the
decisions of record.

## What this is

Phat's market cause-and-effect journal. A Telegram bot (Granger, @grangertbot)
captures observations; this CLI is the only write path; a web UI (not built)
will own review and resolution.

Three neighbours, deliberately separate:
- `trading-diary` — his own trades. Granger **reads** it to detect
  position-serving observations, never writes.
- `daily-feeds` — the 07:00 pre-open brief. Granger **reads** it to link and to
  detect parroting, never writes. Also supplies the JPX ticker mirror.
- `tse-scanner` — setup mechanics. No relationship.

## Status (2026-08-26)

Built and smoke-tested end to end against live data: schema, registries,
measurement + cache, write/flag/revise, all read views, weekly bundle. Agent
wired (`granger` on `anthropic/claude-opus-4-8` via claude-cli runtime),
Telegram account `granger` routed, `granger-weekly` cron scheduled Sun 21:00
JST. Store is empty by design — entry #1 will be his.

**Not built:** the web UI (`causal-web`, planned tailnet `:8452`). Until it
exists nothing writes `verdict` / `resolved_at`, and those stay NULL.

## Architecture

```
bin/granger          argparse CLI; one JSON object per command, always
lib/schema.sql       DDL. three registries, one episode table, one edge table
lib/db.py            connection, init/seed, JST clock, session boundaries, slugs
lib/measure.py       yahoo chart endpoint + bars_cache; the ONLY source of numbers
lib/registry.py      resolve drivers/instruments/channels; sector17 -> ETF benchmark
lib/book.py          read-only ATTACH of trading-diary
lib/brief.py         read-only ATTACH of daily-feeds
lib/write.py         add/flag/revise + the `intel` block the ack is built from
lib/query.py         all read views
lib/week.py          the Sunday bundle (arithmetic only; the model writes prose)
seed/                flags, channels, instruments vocabularies
```

## Conventions & gotchas

- **All writes go through `write.py`.** Reads through `query.py`. Keep the split.
- **Invalid input writes nothing.** Every write path is one transaction that
  rolls back whole. Never "partially" file an observation.
- **Substance is never UPDATEd.** `revise` inserts a new observation and sets
  the old row's `superseded_by`. Every read view filters
  `superseded_by IS NULL` — if you add a view or an aggregate, *filter it too*.
  This was the source of the only two bugs found in build testing.
- **`author`**: his observations are `self`; Granger's own weekly reads are
  `agent`. The week bundle counts only `author='self'`.
- **Benchmarks**: TSE equity → its TOPIX-17 sector ETF (1617–1633, mapped from
  daily-feeds' `sector17`); ETF → TOPIX `1306.T`; index/FX → none. `excess_pct`
  is the whole point — "hit hardest" is unfalsifiable without it.
- **`measure_session`** is `settled|intraday|stale|unavailable`. Never present
  an `intraday` number as a close. TSE closes 15:30 JST (extended Nov 2024).
- **`bars_cache`**: settled bars never refetch; today's refreshes after 15 min.
  The Yahoo endpoint is unofficial — it will eventually break, and `measure.py`
  is the single place to fix it.
- **Brief event ids are not durable.** `daily-feeds` CASCADE-deletes events on a
  `--force` re-triage, so `feed_refs` snapshots title/why/direction/score.
  Never join live to `feeds.events` for anything a user will read later.
- **Trading-diary symbols are free text** ("NIKKEI 255 MICRO"), not tickers.
  `book.relates()` matches loosely on purpose; tighten it only with evidence.
- **Code suggests, the model judges.** `write._signals()` emits only mechanical
  flags (`unverified-magnitude`, `no-baseline`, `evidence-free`,
  `position-serving`). Judgment flags (`single-factor`, `hindsight-framing`,
  `coincident-not-causal`, …) are the agent's to raise. Don't move judgment
  into code, and don't let the model invent arithmetic.

## Next steps

1. **`causal-web`** — review, edit-by-supersede, and resolution
   (`verdict`/`resolved_at`). Next.js + `node:sqlite`, loopback `:18796` →
   tailnet `:8452`. Do NOT use `better-sqlite3` (see root CLAUDE.md).
2. **Driver vocabulary drift** — watch week one. If `jpy-strength`, `yen-up` and
   `usdjpy-down` all exist by Friday, tighten `registry.SURE/MAYBE` before
   anything else gets built.
3. **Intraday resolution** — daily bars can't tell whether a 14:05 article
   preceded a 13:40 drop. `sources.precedes_move` is currently date-granularity
   only and says so.
4. **Backup** — the store has no backup yet. `trading-diary/scripts/backup.sh`
   is the pattern to copy.

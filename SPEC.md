# SPEC.md — decisions of record

Settled 2026-08-26 in a design interview. Each entry is a fork that was
deliberately taken, with what it cost. Change these only deliberately.

---

### 1. The artifact is a falsifiable claim ledger, not a narrative journal

Every observation carries structure that *could* turn out wrong: a named
instrument, a direction, a measured move, optionally an implication and a
horizon. A diary of market prose cannot be audited, and an unauditable record of
your own reasoning is where bias goes to live.

**Rejected:** free-form notes with in-the-moment critique and no scoring; and
"narrative now, structure later" — the habit of stating something falsifiable is
the hard part, not the schema, and retrofitting asks you to re-derive what you
believed after you already know the answer.

### 2. The bot captures; a web UI reviews

Granger files, measures, and flags. It never writes `verdict` or `resolved_at`.
Review, editing and resolution belong to `causal-web` (not yet built).

Same split as ledger-bot → ledger-web and Boswell → human-graph. The columns
exist from day one so the schema never has to move.

### 3. One message = one observation + 0..n causal links

Mirrors `expenses`/`line_items` and `events`/`event_items`. Keeps what he
actually wrote intact as a unit while giving the web UI individually scorable
edges. Two rival explanations of the same move are two links on one
observation — never two observations, which would double-count the move and
lose the fact that they were rivals.

**Rejected:** event-anchored (no parent when you notice a move with no known
cause — which is most of the time); flat one-row-per-edge (shreds the message).

### 4. Capture never blocks; the critique rides in the ack

The entry lands unconditionally, then Granger pushes back in the same reply.
Flags are stored on the row for the web UI to surface later.

**Rejected:** blocking until the claim is falsifiable — highest-quality store,
but every observation becomes a conversation and on a fast tape he stops
filing. A sloppy observation filed beats a sharp one abandoned.

### 5. Granger reads the trading diary

Read-only on `~/trading-diary/data/journal.db`, to raise `position-serving` —
an observation that conveniently argues for an open position, or rationalises a
loss. This is the bias that costs money and the only one invisible from
Granger's own store.

The exposure that triggers a flag is frozen in `book_snapshot` at capture time,
because the trade will close and be edited within the week and the flag has to
stay auditable.

**Cost accepted:** two repos coupled, and the bot knows his book.

### 6. Magnitudes are measured at capture, and both figures are kept

`move_asserted_pct` (his) and `move_measured_pct` (the tape's) sit side by side,
with `benchmark_move_pct`, `excess_pct`, `measured_at` and `measure_session`.
The gap between asserted and measured is the single best correction in the
system, and it is free.

Inherits daily-feeds SPEC §5: every market number originates in code. The model
may reason about what −3.1% means; it must not be able to invent it.

**Rejected:** storing his number unverified and reconciling in the web UI
(delivers the correction hours later to a calmer person who has moved on);
never storing his figure at all (loses the ability to chart how far off his
eyeball is).

### 7. Soft, snapshotted reference to the 07:00 brief

A link may carry `feed_event_id` and `feed_thread_id`, but `feed_refs` also
snapshots the event's title, `why`, direction and score. `daily-feeds`
CASCADE-deletes events on a `--force` re-triage, so the id alone is not durable.

Enables `parroting-brief`: restating the brief's own transmission channel is not
an independent observation.

**Rejected:** no link at all (two systems that never learn they said the same
thing); hard join (most of what he notices — single-name news, flows, intraday
mechanics — was never in a macro brief).

### 8. Attachments: identifiers extracted, numbers still from the tape

A TradingView screenshot yields ticker, timeframe and *his own markup*; those
identifiers drive a real price fetch. A link yields title, publisher and
`published_at`, degrading gracefully on Nikkei's paywall. Sources bind to the
specific edge they are evidence for.

**A chart is evidence of the move and no evidence of the cause.** It is the most
seductive thing in the message to over-read, hence the narrow extraction.

**Rejected:** full vision extraction of levels and prices off a JPEG — exactly
the failure the numbers-from-code rule exists to prevent.

### 9. Registries, not free text

`drivers`, `channels`, `instruments` are resolved entities with aliases and
`?dupe` flagging. Free-text causes give a diary; resolved drivers make "every
time I blamed the yen, what actually happened?" answerable.

`channels` was the optional one and was kept: recurring *reasoning patterns* are
where bias lives, and it is the only table that captures them.

### 10. Immutable substance; corrections supersede

No write path UPDATEs a claim's content. `revise` inserts a new observation and
marks the old `superseded_by`. The web UI shows the head; history stays intact.

Non-negotiable for this system specifically: the failure mode being built
against is quietly rewriting what you believed once you know the answer. A store
that permits silent edits leaves that back door open.

### 11. One scheduled message: the weekly read

`granger-weekly`, Sundays 21:00 JST, via `openclaw cron`. Cross-entry patterns
— default drivers, flat confidence, contradictions within a week — cannot fire
inside a single ack.

Weekly rather than daily because most days carry 1–3 observations, which is too
few for a pattern; a nightly bot either says nothing useful or manufactures
something, and then gets muted. Below ~10 observations in the window it reports
that the sample is thin and files nothing.

### 12. Scope

Market cause and effect. No sizing, no entries, no exits, no trade calls —
inheriting daily-feeds' boundary: *the model judges events, you judge
positions.* His trades' P&L is the trading diary's; his life is Dr. Fell's;
people are Boswell's.

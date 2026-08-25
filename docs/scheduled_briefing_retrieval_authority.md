# P0-06 Scheduled Briefing Retrieval Authority Adoption

## Purpose

The scheduled briefing consumer can read a raw GitHub file at an exact commit
SHA, but its environment receives HTTP 403 when it tries to resolve `main`
through the GitHub Git Data or commits APIs.  P0-05's immutable retrieval
contract therefore existed but could not be adopted by that consumer.

P0-06 publishes only the missing bootstrap: an append-only record containing
the exact `source_commit`, `generation_id`, expected KST date, stale-detection
result, and immutable artifact URLs.  It does not copy the read model, make an
investment judgment, or confer any trading authority.

## Bootstrap trust boundary

The bootstrap path is unique by decision date, slot, and sequential revision:

```text
evidence/scheduled_briefing_retrieval/YYYY-MM-DD/{morning|evening}/rev-NNN.json
```

The consumer may use `main` only to read these unique append-only bootstrap
paths.  It starts at `rev-001.json`, increments until the first missing path,
and uses the highest valid revision already found.  Existing revisions are
never overwritten.  A same-day recovery therefore creates `rev-002.json`
instead of mutating `rev-001.json`.

This eliminates the old-generation cache ambiguity: before creation there are
no valid bytes at that complete date/slot/revision URL, and after creation its
bytes never change.  A missing revision, gap, malformed record, mismatched
date/generation, or unavailable URL produces
`RETRIEVAL_AUTHORITY_UNAVAILABLE`.  It never permits a prior-date or floating
artifact fallback.

Every actual artifact URL inside a valid bootstrap is pinned to the full
40-character `source_commit`:

```text
https://raw.githubusercontent.com/yonggeun1021-hub/atlas-data/<source_commit>/<path>
```

The producer derives hashes and generation metadata from `git show` at that
exact commit, not from potentially dirty working-tree bytes. Publication is
deliberately two-phase: phase A commits and pushes the daily briefing plus the
H-24 locator, producing consumer-ready commit `S`; phase B publishes the
append-only bootstrap whose `source_commit` is `S`. This avoids the impossible
self-reference that would result from trying to put a pointer to a commit
inside that same commit. A concurrent advance causes a bounded fetch-first
retry; no rebase or force-push is used.

The envelope binds not only Step0 and health, but also the H-24 locator and the
exact index, packet, and rendered briefing bytes named by that locator. Their
paths and SHA-256 values are independently checked at `S`.

## Scheduled consumer contract — both AM and PM

The same instructions apply to the 07:15 KST morning briefing and the 18:35
KST evening briefing; only `slot` differs.

1. Set `expected_kst_date` to today's KST date and `slot` to `morning` or
   `evening`.
2. Read `rev-001.json`, then `rev-002.json`, and so on at the date/slot-specific
   bootstrap URL, appending a fresh per-session query nonce to every request.
   Stop only on an explicit HTTP 404 at the first missing revision and use the
   highest already validated revision. A gap or any non-404 transport failure
   is `RETRIEVAL_AUTHORITY_UNAVAILABLE`, not permission to use an older record.
3. Require the bootstrap's date and slot to match the requested values,
   `stale_detection=PASS`, a full lowercase commit SHA, one 64-character
   generation ID, and every investment/trading authority flag to remain false.
4. Read Step0, health, and requested compact files only from the exact
   commit-pinned URLs in the bootstrap. Confirm the expected KST date and the
   same generation ID across every consumed artifact.
5. If any step fails, report `RETRIEVAL_AUTHORITY_UNAVAILABLE` and do not make
   a new investment judgment from stale or floating data.

The repository provides the executable consumer contract:

```bash
python3 .github/scripts/consume_scheduled_briefing_authority.py \
  --expected-kst-date YYYY-MM-DD \
  --slot morning \
  --output-dir /tmp/atlas-verified-briefing
```

It discovers sequential revisions using a fresh request nonce, accepts only an
explicit 404 as the end of the sequence, validates the closed envelope schema,
and persists verified immutable bytes atomically. A missing first revision,
revision gap, non-404 transport error, mixed generation, stale compact, H-24
hash mismatch, or floating artifact URL all fail closed.

The prompt must not use `refs/heads/main`, `raw/.../main/...`, a prior date, or
an alternate endpoint for Step0/health/compact artifacts.  The bootstrap URL
is the only narrow exception to the floating-`main` ban.

## Workflow placement

`Atlas Daily Briefing Integration v1` runs at 07:05 and 18:30 KST, before the
human scheduled briefings.  It re-syncs to the latest main, builds the normal
briefing and H-24 locator, publishes and validates the P0-06 bootstrap, then
commits all outputs in one race-sensitive push.

Natural scheduled-session proof is still required before P0-06 can close.
Manual workflow dispatch or local tests do not count as that proof.

## Authority boundary

`retrieval_pointer_only=true`. Collector, Stage, Buy, Action, Order,
Production, and Trading authorities are all false.  No policy threshold,
position size, trade proposal, or order is created by P0-06.

# P0-06 Scheduled Briefing Retrieval Authority Adoption

## Purpose

The scheduled briefing consumer can read a raw GitHub file at an exact commit
SHA, but its environment receives HTTP 403 when it tries to resolve `main`
through the GitHub Git Data or commits APIs.  P0-05's immutable retrieval
contract therefore existed but could not be adopted by that consumer.

P0-06 publishes only the missing bootstrap: an append-only record containing
the exact `source_commit`, `generation_id`, expected KST date, stale-detection
result, an explicit source-date binding, and immutable artifact URLs.  It does not copy the read model, make an
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
deliberately ordered: phase A commits and pushes the daily briefing plus the
H-24 locator, producing consumer-ready commit `S`; phase B immediately
publishes the append-only bootstrap whose `source_commit` is `S`; downstream
Decision-lineage, Shadow-readiness, and acceptance sidecars run only after that
bootstrap exists. A downstream semantic rejection remains fail-closed and
writes no invalid sidecar, but it cannot erase or suppress a separately valid
P0-06 retrieval authority. This avoids the impossible self-reference that
would result from trying to put a pointer to a commit inside that same commit.
A concurrent advance causes a bounded fetch-first retry; no rebase or
force-push is used.

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
   Schema v3 normally binds `source_evidence_kst_date` to the decision date.
   The only exception is a Saturday/Sunday `morning` round, which may bind
   exactly the previous Friday and must declare
   `MARKET_CLOSED_NO_NEW_SESSION_LATEST_CONFIRMED_EVIDENCE`. This is an exact
   immutable binding, not a search or prior-date fallback.
4. Read Step0, health, and requested compact files only from the exact
   commit-pinned URLs in the bootstrap. Confirm the envelope's exact
   `source_evidence_kst_date` and the same generation ID across every consumed
   artifact. Weekend delivery bytes must explicitly state market closure, no
   new session, the previous-Friday evidence date, and that it was not
   relabelled as the decision date.
5. If any step fails, report `RETRIEVAL_AUTHORITY_UNAVAILABLE` and do not make
   a new investment judgment from stale or floating data.

The external consumer validates the fetched packet against the immutable
envelope hashes, its own canonical self-hash, date/slot identity, status-count
consistency, and the fixed false authority boundary.  It deliberately does
not call the producer's full `daily_orchestrator.validate_packet()` against the
consumer's local checkout: that validator re-derives non-frozen components
from local evidence and is only valid in the exact producer generation.  The
producer performs that full semantic rebuild before publication; the consumer
then proves that it received those exact commit-pinned bytes without granting
any new authority.

The repository provides the executable consumer contract:

```bash
python3 .github/scripts/consume_scheduled_briefing_authority.py \
  --expected-kst-date YYYY-MM-DD \
  --slot morning \
  --wait-timeout-seconds 600 \
  --poll-interval-seconds 15 \
  --output-dir /tmp/atlas-verified-briefing
```

It discovers sequential revisions using a fresh request nonce. For the first
revision only, the consumer may poll explicit HTTP 404 responses for a bounded
window (maximum 900 seconds); every attempt gets a fresh nonce. The deadline
never permits a prior date, alternate slot, floating artifact, or non-404
transport fallback. After the deadline, a still-missing first revision remains
`RETRIEVAL_AUTHORITY_UNAVAILABLE`. Once one valid revision exists, the first
missing next revision is the normal end of sequential discovery and is not
waited on. The consumer validates the closed envelope schema and persists
verified immutable bytes atomically. A missing first revision,
revision gap, non-404 transport error, mixed generation, stale compact, H-24
hash mismatch, or floating artifact URL all fail closed.

The prompt must not use `refs/heads/main`, `raw/.../main/...`, a prior date, or
an alternate endpoint for Step0/health/compact artifacts.  The bootstrap URL
is the only narrow exception to the floating-`main` ban.

## Workflow placement

`Atlas Daily Briefing Integration v1` runs at 07:05 and 18:30 KST, before the
human scheduled briefings. It re-syncs to the latest main, builds the normal
briefing and H-24 locator, then publishes and validates the P0-06 bootstrap in
its own append-only commit. The multi-minute offline regression suite runs in
a parallel job and therefore cannot consume the short producer-to-consumer
window. Downstream lineage/Shadow sidecars preserve their own fail-closed
status without blocking the already-valid retrieval bootstrap.

Natural scheduled-session proof is still required before P0-06 can close.
Manual workflow dispatch or local tests do not count as that proof.

## Authority boundary

`retrieval_pointer_only=true`. Collector, Stage, Buy, Action, Order,
Production, and Trading authorities are all false.  No policy threshold,
position size, trade proposal, or order is created by P0-06.

# KOFIA Release-Timing Observation Contract (P1-KR-03)

Status: diagnostic observation mechanism. Source release timing, `available_at`,
the durable historical range, API unit, Regime, Production, and trading remain
unratified or unauthorized.

## Investment purpose

Investor deposits and credit financing may become useful liquidity context only
when Atlas knows what was available at the decision time. The existing
append-only first-seen collector preserves the source rows but intentionally
does not summarize the empirical delay between `basDt` and Atlas first seeing
the exact row. This layer makes that delay auditable without converting it into
an investment input.

## Input and point-in-time boundary

The builder reuses `kofia_first_seen.read_prior_first_seen()` to replay every
committed bundle from retained gzip bytes. It then derives, per operation,
observation date, and exact row hash:

- the latest verified probe where that exact row hash was absent, if one
  exists (this includes a missing date or a previously observed revision);
- the earliest verified Atlas capture of that exact row hash;
- the open/closed observation window between those two probes;
- the calendar-day lag at Atlas first-seen; and
- whether more than one exact row hash was seen for the same observation date.

The lower bound is exclusive and the upper bound is inclusive. Both are Atlas
probe facts. Neither is the official KOFIA publication time. A row with no
earlier verified missing probe is `UPPER_BOUND_ONLY`, not backfilled.

## Meaning and authority

- `available_at` is always `null`.
- `release_timing_policy_status` is always `UNRATIFIED`.
- conflicting primary evidence about the API unit remains visible.
- the report cannot produce a Regime score or authorize Production/trading.
- builder and validator independently rebuild the report from the retained raw
  sequence only through the packet's immutable `as_of_capture_utc`; later
  bundles therefore do not invalidate an older append-only packet, while a
  re-signed semantic change still fails closed.

## Operational wiring

The existing P1-KR-03 workflow keeps raw evidence publication independent. It
first commits and pushes the immutable provider response. Only then, for
`first_seen` mode, it builds a content-bound derived report under the same
run identity and commits it separately. A derived failure therefore cannot
discard or rewrite successfully captured raw evidence. There is no new cron,
provider request, secret, polling path, or source fallback.

```text
data/observations/kofia_release_timing/{KST_DATE}/
  run-{RUN_ID}-attempt-{ATTEMPT}/packet.json
```

The tracked output date and run identity must exactly match the latest source
bundle included in the packet. A correctly signed historical packet cannot be
relabeled under a later run path.

This mechanism is evidence collection, not P1-KR-03 Exit Gate closure. An
official answer or later approved policy is still required before any
`available_at` or decision-eligibility contract can be adopted.

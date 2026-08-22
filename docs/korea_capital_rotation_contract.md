# P2-03 Korea Capital Rotation contract

Status: 2026-08-22, a minimal own-benchmark rotation_policy is RATIFIED for
real (see `.github/scripts/korea_capital_rotation_ledger_proof.py`) -- real
KOSPI/KOSDAQ own-benchmark ranking, TOP/MIDDLE/BOTTOM buckets, and state
ledger records now exist against a real post-ratification observation pair.
Still open: live P2-01 Theme population (theme_id stays a positional proxy
tied to the real P1-KR-07 SECTOR identity, never a cross-market Theme
grouping), Korea Breadth durable `available_at` lineage (still null ->
BLOCKED, holding the overall decision back regardless of rotation_policy),
confirmed investor-flow release timing, and live scheduled-cron briefing
integration (this remains a manual proof, not a cron).

## Own-benchmark scopes

The transform consumes two hash-bound `korea_leadership/v1` derived packets.
KOSPI and KOSDAQ Theme series have different benchmarks, so the transform does
not compare or rank them in one cross-benchmark list. An external policy must
provide disjoint benchmark scopes, and ranking occurs only within each exact
scope using relative strength versus that scope's own benchmark.

KRX `series_identity` is never assumed to be a P2-01 Theme ID. Every scope
contains an explicit externally ratified `series_identity → theme_id` mapping.
One Theme ID cannot silently use multiple benchmark proxies; an aggregation
policy for that case is not ratified, so duplicate proxy mapping fails closed.

No benchmark, Theme, TOP/BOTTOM count, or cadence is a repository default.
The policy is bound to the exact P2-01 taxonomy decision/packet and Korea
Leadership policy SHA, must predate the prior observation, and must be
effective across both observations.

## Breadth and investor-flow boundary

Korea Breadth context now carries real, minimum-sufficient per-market
(`KOSDAQ`/`KOSPI`) lineage facts -- `lineage_sha256`, `as_of_date`,
`available_at` -- rather than a hardcoded placeholder. `status` is one of
exactly four CIO-ratified values, always independently re-derived from those
raw facts (both at build time and by `validate_packet()`), never trusted from
a caller's own declaration:

- `AVAILABLE`: lineage, as_of, `available_at`, and freshness (within the
  caller-supplied `freshness_limit_days`) are all present and verified for
  both required markets.
- `BLOCKED`: an observation exists (lineage/as_of present) but its
  `available_at` is null -- exactly what every P1-KR-05 Breadth observation
  packet emits today, since its own `decision_eligible` is always false.
- `UNKNOWN`: no observation was supplied for a market at all.
- `STALE`: `available_at` is present but older than `freshness_limit_days`
  relative to this packet's `as_of_date`.

The worst status across the two required markets wins -- one market being
fresher never masks the other being blocked or unknown. `decision_eligible`
is true only when the derived status is `AVAILABLE`; `ranking_input_authorized`
stays `false` unconditionally regardless of status, matching the contract's
own closed `breadth_as_ranking_input_authorized` authority -- `AVAILABLE`
breadth is provably real and fresh, but still never becomes a ranking input.
A `BLOCKED` context can never be silently relabeled `NEUTRAL`, `AVAILABLE`,
or treated as a pass.

Current investor flow is KRX-only, excludes NXT, has an unverified source
release time and `available_at=null`, and is not decision eligible. This
boundary is exact required context, but is not a ranking input; it cannot
silently become zero, neutral, or total-market flow.

Therefore this packet is explicitly price-relative rotation, not a complete
price+breadth+flow capital-allocation claim. Those coverage gaps remain in the
output.

## Transition and authority

An effective policy emits prior/current within-benchmark ranks,
TOP/MIDDLE/BOTTOM buckets, and structural bucket transitions. It does not emit
`EMERGING`, `STRONG`, or `WEAKENING`; P2-05 owns that vocabulary and ledger.
Cross-benchmark ranking, Regime, candidate ranking, Stage, Production, and
trading remain false. The CLI is offline and can write only outside the repo.

## Standalone output validation

`validate_packet()` treats a stored packet as untrusted. It validates the exact
identity, observation pair, taxonomy and coverage bindings, embedded policy,
retention, lineage, authority, unresolved boundaries, and packet digest. For
each benchmark scope it independently re-derives canonical numeric values,
prior/current ranks, rank changes, TOP/MIDDLE/BOTTOM buckets, transitions, and
top/bottom summaries. Recomputing `payload_sha256` after changing one of those
fields cannot make the packet valid.

The output deliberately omits upstream source rows -- retaining full upstream
Leadership packets would violate this module's own `output_retention_policy`
-- but `observation_pair` (schema `korea_capital_rotation_packet/3`) persists
each observation's own `available_at` alongside its date. `validate_packet()`
re-parses both timestamps, requiring them present, ISO8601, and
timezone-aware, and independently re-derives prior-before-current order, the
effective interval covering both observations, and
ratified-before-prior-observation from those persisted values alone -- with
no live source pointer, current file, or monkeypatch -- so a revision's own
packet remains standalone-reprovable even after live source state moves on,
and a self-rehashed tamper of any of these facts (order, gap, ratification
timing) fails closed.

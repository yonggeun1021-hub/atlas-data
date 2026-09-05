# P2-03 Korea Capital Rotation contract

Status: 2026-08-22, a minimal own-benchmark rotation_policy is RATIFIED for
real (see `.github/scripts/korea_capital_rotation_ledger_proof.py`) -- real
KOSPI/KOSDAQ own-benchmark ranking, TOP/MIDDLE/BOTTOM buckets, and state
ledger records now exist against a real post-ratification observation pair.
A real PIT temporal-invariant audit and correction (also 2026-08-22, see
"Breadth PIT temporal invariant" below) fixed a genuine, previously-backwards
Breadth availability check -- the end-to-end result is now real `AVAILABLE`/
`READY` for observation pairs whose real evidence supports it (proven against
real 2026-08-13/2026-08-14 data), not permanently `BLOCKED`. Still open: live
P2-01 Theme population (theme_id stays a positional proxy tied to the real
P1-KR-07 SECTOR identity, never a cross-market Theme grouping), verified
`source_available_at` official publication timing (still null -- Korea
Breadth's own eligibility today rests entirely on first-seen evidence),
confirmed investor-flow release timing, and live scheduled-cron briefing
integration (this remains a manual proof, not a cron). READY at the P2-03
level still never grants Buy/Stage/Action/Order/Production/trading authority
-- those stay closed unconditionally, independent of this contract.

## Observation-pair job dependency and the real schedule gap (2026-08-22)

`.github/workflows/p2-03-korea-observation-pair.yml` sequences the two
already-approved Korea Breadth (P1-KR-05) and Korea Leadership live-fetch
paths with a real GitHub Actions `needs:` dependency: Leadership's job
cannot start until Breadth's own capture-and-commit job has genuinely
completed and pushed. This structurally guarantees Breadth's real
`first_seen_at` predates Leadership's real `available_at` (decision_time)
for a same-day observation, rather than relying on manually remembering
the correct trigger order each run. It reuses every existing job step
verbatim -- no new fetch logic, no new endpoint, no duplicate KRX request.

**Update (2026-09-04, CIO-approved P2-03 bounded cadence slice):** the
standalone `korea-leadership-live-proof.yml` now has a real weekday
schedule (18:10/18:25 KST, reusing the exact evening cadence
`korea-market-signals.yml` already established and this repo's own
ratified `config/korea_leadership_policy.json.earliest_usable_time`). A
scheduled run discovers its own prior/current completed KRX trading
dates via `korea_market_signals.py`'s existing `discover_session_pair()`
(unchanged) -- no invented trading-day calendar. Manual
`workflow_dispatch` with explicit dates is unchanged.

Honest, still-open half of the gap: Korea Breadth
(`p1-kr05-korea-breadth-live.yml`) and this combined observation-pair
workflow remain `workflow_dispatch`-only -- there is still no automatic
daily trigger for Breadth, so a same-date Breadth+Leadership pair (what
`korea_capital_rotation.py`'s own no-lookahead check actually needs) is
not yet fully automatic end to end. A scheduled Leadership-only sample
can therefore still see `BREADTH_MARKET_SOURCE_AVAILABLE_AT` unavailable
for its own date until Breadth is separately dispatched (or scheduled)
for that same date. Closing that remaining half is a separate, not yet
approved, bounded slice.

### Real dispatch failure and fix (2026-08-22, run 32566229770)

The workflow's first real dispatch (2026-08-10 prior / 2026-08-11 current)
surfaced a genuine race: `actions/checkout` resolves to the commit SHA
fixed when the `workflow_dispatch` run started, for every job in that run
-- it does not track `main`'s live tip. The Breadth context-commit job
pushed real evidence mid-run; the downstream Leadership job's own
checkout still pointed at the pre-run SHA, so its commit was based on a
stale parent and its push was correctly rejected as non-fast-forward
(`! [rejected] main -> main (fetch first)`). The real KRX-fetched
2026-08-11 Leadership packet was committed locally in that job but never
reached origin, so it was lost when the runner was torn down -- an honest
infrastructure bug, not a policy or taxonomy gap, and not forced to a
false pass. Fix: both write jobs (`korea-breadth-context-commit`,
`korea-leadership-live-fetch`) now `git fetch origin main && git reset
--hard origin/main` immediately before staging/committing their own
evidence, so each commit is based on the real live tip -- including any
commit an earlier job in the same run just pushed -- rather than the
run's fixed start-of-run SHA. This only re-syncs the branch pointer and
tracked tree; it does not touch the new untracked evidence file already
written to disk by the fetch step above it.

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

## Breadth PIT temporal invariant (audited and corrected, 2026-08-22)

**The audit finding.** Every Breadth market fact carries `lineage_sha256`,
`as_of_date` (the market day the observation describes), `source_available_at`
(verified official publication timing -- still permanently null; KRX gives
none today), `captured_at`/`first_seen_at` (the real instant P1-KR-05's own
live fetch completed), and `capture_mode` (`forward_live` | `historical_
backfill`, a required, explicitly-declared fact -- date math alone cannot
tell a genuine next-day capture from a convenient later catch-up). Until
2026-08-22 this contract required `available_at <= as_of_date` and raised if
violated -- backwards: an EOD statistic describing trading day D cannot
genuinely become available before D closes, so real T+1-or-later evidence was
always rejected as "from the future", exactly like `korea_leadership.py`'s
own upstream `available_at` had *already* required (`available_at >=
observation_date`, `UPSTREAM_AVAILABLE_BEFORE_OBSERVATION`) -- Breadth's own
check pointed the opposite way in the very same packet.

**The fix.** No-lookahead is now `decision_available_at <= decision_time`,
never `<= observation_date`:
- `decision_time` is the real current Leadership observation's own
  `available_at` -- an already-KST-validated real timestamp already present
  in every packet, reused rather than fabricated (no wall-clock, no new
  caller-supplied field). No part of this rotation decision could genuinely
  have been made before this instant.
- `decision_available_at` is `source_available_at` when present, else a
  genuine `forward_live` capture's `first_seen_at`; `historical_backfill`
  (or an undeclared capture_mode) never counts.
- A `source_available_at`/`first_seen_at` before its own `as_of_date`, or a
  `first_seen_at` before its own `captured_at`, is structurally impossible
  and fails closed as a real chronology defect (`BREADTH_MARKET_SOURCE_
  AVAILABLE_AT_BEFORE_AS_OF`, `BREADTH_MARKET_FIRST_SEEN_BEFORE_AS_OF`,
  `BREADTH_MARKET_FIRST_SEEN_BEFORE_CAPTURED`).
- A `decision_available_at` genuinely AFTER `decision_time` is **not** a
  chronology defect -- two independently-scheduled real captures (Leadership,
  Breadth) can legitimately complete in either order -- so it degrades to
  `BLOCKED`, not a raised error.

`status` stays exactly the same four CIO-ratified values, always
independently re-derived from raw facts (both at build time and by
`validate_packet()`), never trusted from a caller's own declaration:

- `AVAILABLE`: lineage/as_of present, `decision_available_at` resolves and is
  `<= decision_time`, within `freshness_limit_days` of it, for both required
  markets.
- `BLOCKED`: an observation exists but `decision_available_at` is null (no
  source timing, no eligible first-seen) or genuinely after `decision_time`.
- `UNKNOWN`: no observation was supplied for a market at all.
- `STALE`: `decision_available_at` resolves and is `<= decision_time` but
  older than `freshness_limit_days` relative to it.

The worst status across the two required markets wins -- one market being
fresher never masks the other being blocked or unknown. `decision_eligible`
is true only when the derived status is `AVAILABLE`; `ranking_input_
authorized` stays `false` unconditionally regardless of status, matching the
contract's own closed `breadth_as_ranking_input_authorized` authority --
`AVAILABLE` breadth is provably real and fresh, but still never becomes a
ranking input. A `BLOCKED` context can never be silently relabeled `NEUTRAL`,
`AVAILABLE`, or treated as a pass. There is no separate "confirmed_history"
bypass channel: first-seen evidence flows through this real Breadth lineage
directly.

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
-- but `observation_pair` (schema `korea_capital_rotation_packet/4`) persists
each observation's own `available_at` alongside its date. `validate_packet()`
re-parses both timestamps, requiring them present, ISO8601, and
timezone-aware, and independently re-derives prior-before-current order, the
effective interval covering both observations, and
ratified-before-prior-observation from those persisted values alone -- with
no live source pointer, current file, or monkeypatch -- so a revision's own
packet remains standalone-reprovable even after live source state moves on,
and a self-rehashed tamper of any of these facts (order, gap, ratification
timing) fails closed.


## Existing Theme taxonomy v2 consumer path

The legacy `theme_taxonomy/1` opaque binding remains byte-compatible. A binding
for the existing producer's `theme_taxonomy/2` contract now requires the real
`theme_taxonomy_input/1` source through `--taxonomy-graph` (or the
`taxonomy_source_bytes` build argument). The consumer rebuilds that source with
`rotation.theme_taxonomy.build_packet`, including its existing independent
Git-provenance authority resolution, and compares taxonomy identity, decision
identity, decision hash, packet hash and decision date. Rotation policy theme
references must exist as active Theme nodes in that exact graph; this does not
infer security memberships or change market-native classifications.

The v2 binding retains the exact public source JSON text and SHA-256, graph
status, authority-resolution status and membership-authorization result.
Packet-only validation, including the common Rotation State Ledger consumer,
rebuilds the embedded source and rechecks those derived fields. Re-signing a
false membership/authority assertion or source digest is rejected. An external
source supplied at validation must match the embedded bytes. No file path is
trusted as the graph, and the CLI still refuses tracked output.

The empty repository authority registry remains non-authorized. Existing
externally supplied rotation policy still owns ranking and bucket thresholds;
this change adds none. The output remains `korea_capital_rotation_packet/4` with
an optional v2 binding variant, preserving legacy packets. It does not migrate
the default /1 configuration or source-population registry pins, and it does
not ratify graphs, populate US/Crypto memberships, ingest Asset Master data,
claim a natural sample, or unlock candidate/Stage/REAL/order/trading authority.

Validation uses synthetic graph/leadership fixtures through the real producer,
Korea consumer and ledger. Operational completion still requires an actual
canonical graph/source and existing ratified rotation policy to pass this path
in a natural run; engineering integration is not that completion.

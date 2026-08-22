# P8-12 Opportunity Trigger + Dynamic Review Clock

Operational (not retrospective) layer: `Evidence -> Trigger Event -> Dynamic
Re-review -> Human-review candidate`. Built on top of PR #210's (P10-02/
P10-03 Opportunity Capture PIT Replay) `replay/` package: every actual
trigger DETECTION call is the same, unmodified `replay.trigger_engine`
function that audit used, itself gated by `replay.lookahead_gate`. This
module contributes the genuinely new piece PR #210 never needed
(retrospective replay has no live clock): `clock/dynamic_clock.py`'s
episode/cooldown/expiry/re-activation state machine, and the Human Review
Candidate output contract in `clock/review_candidate.py`.

## WBS scope note (read this before assuming P8-10 is also in scope)

The Notion Master WBS Tracker's canonical rows separate **P8-10 "Price
Reflection"** (a different, already-owned WBS row -- "기존 P8-10 canonical
row 유지. 신규 row 금지", owned by a separate "Price/PIT" slice, scoped to
building real historical price/benchmark time series and splitting
`UNKNOWN` into `PRICE_DATA_MISSING`/`PRICE_STALE`/
`REFLECTION_UNCERTAIN_WITH_VALID_PRICE`) from **P8-12 "Opportunity Trigger +
Dynamic Review Clock"** (this module's actual scope, matching this
document). This module does not implement P8-10 and does not import
`decision/price_reflection.py`; see "Deliberate non-coupling" below.

## Markets and trigger types (item 1)

Markets: BTC, KOREA (current watchlist), CRYPTO (ratified PIT-eligible
taxonomy). Trigger types: the same seven `replay.opportunity_trigger.
TRIGGER_TYPES` PR #210 defined (PRICE_CONFIRMATION, INVALIDATION_TRIGGER,
FLOW_REVERSAL, RELATIVE_STRENGTH_REVERSAL, FUNDAMENTAL_REVISION,
CATALYST_APPROACH, EXPECTATION_DISLOCATION).

`clock/operational_scan.py::MARKET_TRIGGER_COMPUTABILITY` declares, per
market, whether each type is `COMPUTABLE` or `NOT_COMPUTABLE` from this
repo's real committed evidence today -- never silently omitted:

| Trigger type | BTC | KOREA | CRYPTO |
|---|---|---|---|
| PRICE_CONFIRMATION | COMPUTABLE | COMPUTABLE | COMPUTABLE |
| INVALIDATION_TRIGGER | COMPUTABLE | COMPUTABLE | COMPUTABLE |
| FLOW_REVERSAL | NOT_COMPUTABLE (no flow field on the dedicated BTC collector) | COMPUTABLE (KRX `net_value.외국인합계`) | NOT_COMPUTABLE (breadth collector is OHLC-only) |
| RELATIVE_STRENGTH_REVERSAL | NOT_COMPUTABLE (no peer series for BTC's single-asset collector) | COMPUTABLE (own-watchlist peers) | COMPUTABLE (other ratified-eligible pairs) |
| FUNDAMENTAL_REVISION | NOT_COMPUTABLE | NOT_COMPUTABLE | NOT_COMPUTABLE |
| CATALYST_APPROACH | NOT_COMPUTABLE | NOT_COMPUTABLE | NOT_COMPUTABLE |
| EXPECTATION_DISLOCATION | NOT_COMPUTABLE | NOT_COMPUTABLE | NOT_COMPUTABLE |

The last three are structurally `NOT_COMPUTABLE` everywhere because no
parsed guidance/catalyst-calendar series is committed anywhere in this repo
as a dated structured series (`replay.trigger_engine.NOT_COMPUTABLE_TYPES`,
reused verbatim -- see that module's docstring).

## Dynamic Clock (item 2)

`clock/dynamic_clock.py::build_episode_history` turns a chronological
stream of trigger detections for one `(subject, market, trigger_type)` into
**episodes**:

1. **Duplicate-event suppression** -- the exact same `evidence_hash`
   re-observed inside an open episode is a no-op.
2. **Cooldown** -- distinct new evidence arriving before the open episode's
   `expiry` folds INTO that episode (renews it: extends `expiry`, resets
   `next_review_at` to `cooldown_days` later) instead of opening a
   duplicate candidate. Real example: BTC's PRICE_CONFIRMATION fired with a
   different `evidence_hash` on 2026-08-20/21/22 -- one continuing episode,
   three renewals, not three review requests.
3. **Expiry** -- an episode not renewed within `expiry_days` closes as
   `EXPIRED`. Replaces the "wait for the next monthly review" default with
   an explicit, short human-review shelf life
   (`clock/dynamic_clock.py::TRIGGER_CLOCK_POLICY`, mirroring the Control
   Loop doc's "재검토 시계": price/flow triggers -> next trading
   day/24h; long-thesis-class triggers -> 30 days).
4. **Re-activation** -- a fresh detection after an episode has expired
   opens a brand-new episode, linked via `reactivated_from_episode_id` for
   audit traceability -- never silently merged into or dropped by the dead
   one.

Zero future-data leakage: every event handed to this state machine already
passed `replay.opportunity_trigger.build_trigger_event`'s construction-time
anti-lookahead gate (`FIRST_SEEN_AT_AFTER_DECISION_DATE`, etc.), and
`build_episode_history` itself adds a second, defense-in-depth check
(`EVIDENCE_AVAILABLE_AT_AFTER_DETECTED_AT`) rather than trusting the
upstream guarantee alone.

## Output contract (item 3)

`clock/review_candidate.py::build_review_candidate` produces one record per
ACTIVE episode with every field the task requires: `subject`, `market`,
`trigger_type`, `detected_at`, `evidence_available_at`, `source` +
`evidence_hash`, `thesis_linkage`, `price_reflection_status`, `urgency`,
`expiry`, `next_review_at`, `human_review_required` (always `True`), plus an
explicit `authority` block with every Stage/Buy/Action/Order/Production/
trading field hard-`False`/`None`/`0` (`trade_proposal` is always `None`).
`build_expired_record` is the parallel, `human_review_required=False`
record for a stale/un-renewed episode.

**Deliberate non-coupling**: `thesis_linkage` and `price_reflection_status`
are honest placeholders (`NOT_LINKED_THIS_SLICE` + a reason), never
fabricated, and never produced by importing `decision/forward_thesis.py`
(P8-08) or `decision/price_reflection.py` (P8-10) -- both are owned by the
separate Forward Alpha / Price-PIT WBS rows this workstream's isolation was
designed not to couple against. A future integration can populate them once
those slices produce real linkable output.

`reference_forward_metrics_first_detection` / `_latest_detection` are
diagnostic-only fields reusing `replay.forward_metrics.compute_forward_
metrics` verbatim (PR #210's anti-backdated-entry invariant) -- never an
entry authorization.

## Regression cases and preserved boundaries (item 4)

- BTC 2026-08-20's real `ACTION_CONVERSION_FAILURE` finding from PR #210's
  audit (PRICE_CONFIRMATION, `signal_evaluation_at=2026-08-19`,
  `hypothetical_entry_at=2026-08-21`, forward return +7.30%) surfaces here
  as an active Dynamic Clock episode opened 2026-08-20 (verified directly
  against real evidence in `test/test_dynamic_clock_end_to_end.py`).
- No backdated/prior-day entry pricing: `reference_forward_metrics_*`
  reuses `compute_forward_metrics()` unmodified, so PR #210's structural
  `hypothetical_entry_at > action_eligible_at` invariant applies here too.
- KOREA's population is explicitly labeled
  `CURRENT_WATCHLIST_OPERATIONAL_COHORT` -- `config/universe.json` is used
  as the forward-looking scan population (a legitimate operational use),
  never presented as reconstructed historical PIT-eligible evidence.
- CRYPTO's population is `PIT_RATIFIED_ELIGIBLE_UNIVERSE`, built from
  `replay.asset_identity.crypto_pit_eligible_pair_ids` exactly as PR #210
  used it -- empty for essentially the whole window before 2026-08-19, not
  invented.

## Authority (item 5)

A Trigger firing is a re-review REQUEST only. It is not a Buy signal and
not order authority. Nothing in this module can produce a
`trade_proposal`, promote a Stage, or set any Action/Order/Production/
trading boolean `True` -- enforced structurally
(`clock/review_candidate.py::AUTHORITY_ALL_FALSE`,
`validate_review_candidate`) and checked end-to-end in
`test/test_dynamic_clock_end_to_end.py::AuthorityInvariantAcrossReportTests`.

## Briefing connection (item 6)

`clock/run_dynamic_clock.py::build_briefing_section` produces the
standalone artifact item 6 asks for -- new triggers, expired triggers, and
tickers needing re-review, per market. It is deliberately **not** wired
into `briefing/daily_orchestrator.py` in this PR: that file is actively
owned and recently touched by the concurrent Forward Alpha session (see the
PR description's conflict-check section). A separate, later PR can import
this function once that risk is confirmed clear.

## Verification (item 7)

- `python3 clock/run_dynamic_clock.py` is fully deterministic (no
  `datetime.now()` anywhere; "as of" is each market's own latest real
  evidence capture_date) -- verified by
  `test_dynamic_clock_end_to_end.py::DeterminismTests`.
- `test/test_dynamic_clock_state_machine.py` -- duplicate/cooldown/expiry/
  reactivation unit regression.
- `test/test_review_candidate_contract.py` -- output-contract field
  presence, authority hard-false, non-coupling, round-trip validation.
- `test/test_operational_scan.py` -- NOT_COMPUTABLE matrix, reuse (not
  reimplementation) of `replay.trigger_engine`, PIT-boundary labels.
- `test/test_dynamic_clock_end_to_end.py` -- the real BTC 2026-08-20
  regression case, a full anti-lookahead sweep, and the briefing-section
  shape.
- `test/test_dynamic_clock_fail_closed.py` -- malformed dates, future-dated
  evidence, unknown trigger types/markets, and malformed episode dicts all
  raise rather than silently degrade.

Committed, reproducible artifacts: `evidence/operational/dynamic_clock/
dynamic_clock_report.json` (full report) and `.../briefing_section.json`
(the item-6 slice), both regenerated byte-identically by
`python3 clock/run_dynamic_clock.py`.

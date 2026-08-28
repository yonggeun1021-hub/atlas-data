# Atlas Daily Briefing Integration v1 (P8 orchestrator)

## P8-07 / P10-06 Decision Review integration

The daily packet now includes `INVESTMENT_DECISION_REVIEW` and
`INVESTMENT_REVIEW_SHADOW`. Until a TSM Thesis and externally ratified
`RULE-0003` through `RULE-0009` result packet exist, the orchestrator emits a
deterministic `BLOCKED` review status with `trade_proposal=null` and
`money_action=NONE`. It also emits `ledger_record_created=false`, zero capital,
and null action/order/stage change for P10-06.

The briefing therefore exposes the Decision Engine blocker on every morning
and evening run instead of silently omitting the layer. It does not synthesize
a Thesis, ratify PASS/FAIL, create a Shadow eligibility, or grant capital/order
authority.

## Zero-capital review surface (P5-06 / P7-08 / P8-13 bridge)

`daily_orchestrator/3` adds `SHADOW_ENTRY_REVIEW`. It does not replace the
blocked executable Decision Review. It independently validates the exact
committed Dynamic Clock report, candidate-identity observation, review
contract and `shadow_entry_review.json`, then exposes only rows already marked
`ZERO_CAPITAL_HUMAN_REVIEW_ITEM`. The rest of the population remains visible
as counts so the briefing cannot flood with dozens of non-reviewable names.

The retained row fields answer two different questions without mixing them:

- **why review now**: review state, participation state, trigger types, price
  state, next-review date and deterministic review reason;
- **why no trade yet**: candidate-validity, entry, position-management and
  position-size policies remain `UNRATIFIED`.

Every component and row keeps `trade_proposal=null`, `capital=0`, null
quantity/entry-zone/invalidation/max-loss, and Stage/Buy/Action/Order/
Production/trading authority false. Forward returns, MFE/MAE and post-hoc
audit labels are forbidden from both the daily packet and the H-24 delivery
projection. `UPSTREAM_WORKFLOW_RUN` is labelled a natural operational sample;
manual/local runs remain diagnostic and cannot be presented as natural.

## Exact official-release facts

`daily_orchestrator/4` adds `OFFICIAL_RELEASE_SUMMARY` as an additive,
evidence-only component. It rebuilds and validates the P4-04 retained Sandisk
Exhibit 99.1 packet, then renders the complete ordered five-item `News Summary`
with its official title and publication date. It does not select a favourable
item, assign positive/negative meaning, rank the source, promote a candidate,
or feed the unified decision/action path. The component remains `PENDING` with
interpretation and source ranking explicitly `UNRATIFIED`.

## Defensive action and strategic capital readiness

`daily_orchestrator/5` adds `DEFENSIVE_ACTION_DECISION` and
`STRATEGIC_CAPITAL_POSTURE` before `ACTION_RISK_PORTFOLIO_SUMMARY`. The first
revalidates the current P6 cash, hedge, inverse, and long/short boundaries while
keeping missing P1/P2 production contracts explicit. The second consumes that
exact P6 readiness packet and the currently available P7 risk packets while
keeping Regime, cross-market flow/rotation, and a ratified allocation policy
explicitly unavailable.

Both components are readiness inventories only. Every decision and constraint
remains `NOT_EVALUATED`; market budgets and target exposures remain null;
proposals and orders remain empty; Production and trading authority remain
false. Their producer validators run again when P8-06 consumes them. A producer
failure therefore degrades only the dependent component chain instead of
allowing a self-rehashed semantic mutation into the briefing.

Status: provider-free daily orchestrator implemented and scheduled; most
downstream components remain PENDING/POLICY_BLOCKED/DATA_BLOCKED/UNAVAILABLE
because their own upstream policies are unratified or their own upstream
data has not yet accumulated. No action, order, Production, or trading
authority exists anywhere in this layer.

## What this is

`briefing/daily_orchestrator.py` calls no live provider and fetches nothing
itself. It reads only evidence and packets already committed to this
repository, and calls the existing production builders under `regime/`,
`rotation/`, `discovery/`, `rules/`, `bridge/`, `portfolio/`, `briefing/`,
and `decision/` to assemble one combined daily briefing packet twice a day
(`morning`, `evening`). It does not duplicate or rewrite any of those
builders' logic; it is purely the wiring between them and this repository's
persisted evidence.

Every one of the 44 tracked components is reported with:

- `component_id`, `contract_version`, `status` (one of `READY`, `PENDING`,
  `UNKNOWN`, `DEGRADED`, `POLICY_BLOCKED`, `DATA_BLOCKED`, `UNAVAILABLE`),
  `reason` (required unless `READY`);
- `as_of_date`, `generated_at`, `available_at` where known;
- `source_packet_path` / `source_packet_sha256` where the component reads
  tracked evidence;
- `validated` (whether the component's own production validator accepted
  it), `authority` (that component's own authority booleans, verbatim);
- `decision_eligible`, `action_eligible`, `order_eligible` -- always `false`
  in this layer, regardless of what the embedded packet itself claims.

A component with no real committed input today is never given a fabricated
neutral/zero/PASS value. It is reported `PENDING`/`POLICY_BLOCKED`/
`DATA_BLOCKED`/`UNAVAILABLE` with the concrete reason (e.g. `0/25 Rules
consumable`, `constitution not ratified`, `no live axis adapter wired`,
`insufficient contiguous history`).

## Pipeline order

```text
Step 0 / read-model health
  -> per-market sensor evidence (US/KR/Crypto)
  -> 3-Market Regime header (US/KR/CRYPTO regime_output, honestly UNKNOWN
     today -- no live axis adapter exists yet)
  -> Rotation / Theme + Discovery (honest empty ledger/case set -- no
     ratified rotation or discovery policy exists yet)
  -> Rule evaluation (real deterministic run: 0/25 Rules are consumable, so
     every Rule is UNKNOWN/UNDEFINED, never PASS/FAIL)
  -> Portfolio / Risk (bucket membership and currency exposure are
     UNAVAILABLE; the regime-driven structural pass-throughs -- cash
     exposure action, regime-inverse invariant, long/short invariant -- run
     for real against the honest UNKNOWN regime/rule state)
  -> P6 Defensive Action readiness (BLOCKED, decisions NOT_EVALUATED,
     selected action/proposal/order absent)
  -> P7 Strategic Capital Posture readiness (BLOCKED, all numeric budgets,
     targets, and allocation proposals absent)
  -> P8 Action/Risk/Portfolio read model (all action categories remain
     NOT_EVALUATED and null)
  -> Unified Decision (assembles whichever of the six components above are
     actually available; state is always NO_ACTION_AUTHORIZED)
  -> Action/Risk/Portfolio summary (one required source, fourteen optional;
     every unavailable optional source carries an explicit reason)
```

A stage with no real upstream input never fabricates a value for the stage
below it -- it is threaded through as `None` with a reason, and the
downstream builder's own contract records that as `UNAVAILABLE`/`PENDING`.

## Morning vs evening

Both slots build the identical component set and schema. The only
behavioural difference is `KRX_POST_CLOSE`:

- `morning`: never built. Decision-relevant KRX history stays
  confirmed-only, matching P0-02/P0-03's existing morning contract.
- `evening`: built via `briefing/krx_post_close.py`, which the orchestrator
  does not modify. Its own `READY_OBSERVED_UNCONFIRMED` status,
  `confirmed=false`, `decision_eligible=false` boundary is preserved
  verbatim; the orchestrator additionally forces its own
  `decision_eligible`/`action_eligible`/`order_eligible` fields to `false`
  regardless of what the embedded packet says.

## Failure isolation

Every component builder catches its own exceptions and reports `DEGRADED`
with the exception identity as the reason, rather than raising out of
`build_packet` and losing every other component. `STEP0_READ_MODEL_HEALTH`
and `KRX_PREOPEN_COMPACT` additionally distinguish a collector-data failure
(`DATA_BLOCKED`) from a read-model-only failure (`DEGRADED`) -- the same
distinction `check_briefing_readiness.py` already draws -- because the two
have different remediation paths.

The DART observation projection preserves the same boundary inside one
component: valid symbols remain visible while metadata/content failures are
reported as `source_failed`/`content_failed` counts. A partial failure changes
the component reason but never creates event, importance, promotion, action,
order, Production, or trading authority.

## Point-in-time safety: every sensor is decision_date-pinned

Every filesystem-reading component resolves its evidence relative to
`decision_date`, never to "whatever is currently newest in the repo":

- `BTC_TREND`, `BTC_RISK`, `STABLECOIN_NET_ISSUANCE`, `CRYPTO_BREADTH` require
  an evidence directory named *exactly* `decision_date` (`_dated_dir_for_
  decision`); if it does not exist, the component is `DATA_BLOCKED`, never
  silently substituted from a different date.
- `US_BREADTH_MEMBERSHIP` calls `us_breadth_forward.universe_as_of(decision_
  date, ...)` directly -- the module's own as-of-date-safe forward-fill API,
  which resolves to the latest snapshot on or before `decision_date` and
  refuses anything after it.
- `KOFIA_FIRST_SEEN` requires an exact `decision_date` capture directory;
  `DART_FILING_CONTENT` / `SEC_FILING_CONTENT` require their mutable status
  file's own `collected_for_kst_date` to equal `decision_date` before
  trusting its content.
- `KRX_PREOPEN_COMPACT` / `STEP0_READ_MODEL_HEALTH` (via `check_briefing_
  readiness.evaluate(decision_date, ...)`) and `KRX_POST_CLOSE` (via its own
  `expected_date` argument) were already exact-date-bound by the modules
  they call.

Replaying an older `decision_date` therefore always resolves to that date's
own evidence, even when newer evidence for a later date already exists in
the repository -- this is what makes both future-leak prevention and
independent re-validation of a closed decision_date possible.

On top of the per-sensor guards above, `build_packet()` applies one common,
generic time boundary to *every* row uniformly (`_enforce_temporal_
boundary()`), applied IMMEDIATELY after each row is built -- never after a
downstream aggregator has already consumed it -- so a future sensor cannot
silently smuggle future-dated, not-yet-available, or future-captured
evidence past this simply by not implementing its own guard:

- `as_of_date` must not be after `decision_date`.
- `available_at` (when a source declares one) must not be after
  `generated_at` -- evidence that only became available after the packet
  was generated did not exist yet at generation time.
- The row's own `generated_at` -- which several real sensors set to a
  genuine, independent capture/observation timestamp rather than the
  packet's own invocation time -- must not be after the packet's own
  `generated_at` either.

That per-sensor `generated_at` is wired to a real, on-disk retrieval
timestamp, not left null: `KOFIA_FIRST_SEEN` uses `captured_at_utc` from
its own `_observation.json`; `DART_FILING_CONTENT`/`SEC_FILING_CONTENT`
use `observed_at_utc` from their status file; `BTC_TREND`/`BTC_RISK`/
`STABLECOIN_NET_ISSUANCE`/`CRYPTO_BREADTH`/`US_BREADTH_MEMBERSHIP` read
the real `_downloaded_at.txt` every one of these collectors writes
alongside its raw capture (`_read_downloaded_at()`) -- the actual UTC
instant the source was fetched, often hours before the packet's own
`generated_at`. `STEP0_READ_MODEL_HEALTH`/`KRX_PREOPEN_COMPACT` read the
same `data/latest_{krx,dart,sec}.json` files
`BRIEFING_READINESS.evaluate()` already reads, but that function's own
return value never surfaces their real `collected_at_utc` (only
`collected_for_kst_date`, a date with no time-of-day) -- each source's raw
`collected_at_utc` string is read directly (an additional, read-only file
read per source; `check_briefing_readiness.py` itself is not modified)
and frozen as-is, invalid or not.

Whether that raw triple is actually *usable* is decided separately, by
`_qualify_collected_at_utc()` -- a pure function with no I/O of its own,
so it re-derives the same verdict from the frozen raw values forever,
live or replayed. All three of krx/dart/sec must independently be
present, a string, ISO-8601 parseable, timezone-aware, and exactly UTC
(`+00:00`/`Z`); a naive timestamp or one with a non-UTC offset
disqualifies the whole triple -- silently treating an ambiguous offset as
UTC is exactly the kind of gap this check exists to close. On success,
`generated_at` is the latest of the three; on failure, both components
are downgraded (`READY` never survives) to
`DEGRADED`/`TEMPORAL_QUALIFICATION_FAILED:<KRX|DART|SEC>_COLLECTED_AT_UTC_
<MISSING|UNPARSEABLE|NAIVE|NOT_UTC>` with `generated_at: null` and
`validated: false` -- there is no path where a missing or invalid
timestamp still promotes `STEP0_READ_MODEL_HEALTH` to `READY`, and none
where `KRX_PREOPEN_COMPACT` is `READY` while its sibling failed
qualification
(`test_step0_and_krx_preopen_refuse_ready_on_missing_or_invalid_
timestamp`, `test_qualify_collected_at_utc_requires_all_three_valid_utc_
timestamps`).

Every other component whose `generated_at` is wired to a real retrieval
timestamp downgrades the same way when that timestamp is genuinely
absent, rather than being silently promoted to `READY` with an unknown
temporal basis
(`_downloaded_at_guard`).

`KRX_POST_CLOSE` is wired the same way, not to its own `generated_at_kst`
(which is just the packet's own invocation time, KST-formatted): it uses
the *conservative* -- latest, not earliest -- of `source.json`'s
`collected_at_utc` and every per-symbol `symbols/*.json`'s own
`observed_at_kst` (`_read_krx_post_close_observed_at()`). A bundle whose
individual symbols were actually observed a few minutes into the evening
must not be read as `READY` by a packet claiming to have been generated
right at the 18:00 KST evening floor, even though the bundle as a whole
exists by then.

A violating row is downgraded to `DATA_BLOCKED` rather than promoted to a
decision-ready status, *before* `UNIFIED_DECISION`/`ACTION_RISK_PORTFOLIO_
SUMMARY` are built, so neither aggregator can ever consume the smuggled
value even transiently within a single `build_packet()` call
(`test_temporal_boundary_applies_before_unified_decision_and_action_risk_
summary`). This is real, live-triggering behaviour today, not merely
defense-in-depth: this repo's own real `BTC_TREND`/`STABLECOIN_NET_
ISSUANCE` captures for a given `decision_date` are genuinely fetched
several hours into that day, so a packet claiming `generated_at` of
midnight UTC that same day correctly sees them as `DATA_BLOCKED`
(`test_source_retrieval_time_after_generated_at_is_not_promoted_to_
ready`). Likewise `KRX_POST_CLOSE`'s real bundle for a given
`decision_date` is genuinely observed several minutes after the 18:00 KST
evening floor, so a packet claiming `generated_at` of exactly 18:00:00 KST
that same evening correctly sees it as `DATA_BLOCKED`
(`test_krx_post_close_real_observed_at_after_generated_at_is_not_
promoted_to_ready`). Likewise `data/latest_{krx,dart,sec}.json`'s real
`collected_at_utc` values are genuinely well into the KST morning, so a
packet claiming `generated_at` before all three were actually collected
correctly sees `STEP0_READ_MODEL_HEALTH`/`KRX_PREOPEN_COMPACT` as
`DATA_BLOCKED`
(`test_step0_and_krx_preopen_real_collected_at_after_generated_at_is_not_
promoted_to_ready`); see
`test_temporal_boundary_rejects_available_at_after_generated_at` for the
`available_at`-specific proof.

## Determinism and publication

`build_packet(slot, decision_date, generated_at)` is a pure function of its
three arguments plus whatever is currently committed to the repository:
identical arguments against an unchanged repository state produce a
byte-identical packet.

`validate_packet()` independently re-derives *every* component and requires
an exact match to the persisted packet -- with **no blind-trust exemption**
for any of them. Two prior designs both failed here for
`STEP0_READ_MODEL_HEALTH`, `KRX_PREOPEN_COMPACT`, `DART_FILING_CONTENT`,
`SEC_FILING_CONTENT` -- which read `data/briefing_status.json`'s inputs and
`data/latest_{dart,sec}_content.json`, *mutable rolling pointer* files the
collector workflow overwrites every cycle with no per-date archive behind
them:

1. Trusting the persisted row outright (`frozen_rows`) instead of
   re-deriving it. This let a semantic tamper of exactly those rows,
   followed by recomputing the outer `packet_sha256` over the tampered
   payload, slip past undetected.
2. Always re-fetching the live pointer. This meant a genuinely honest,
   untampered packet could legitimately fail re-validation later purely
   because the pointer had moved on -- not tampered, just no longer
   re-derivable from live state.

The actual fix: **freeze the input, not the output.** `build_packet()`
always populates `packet["frozen_sources"]` with the exact raw snapshot
every `FROZEN_SOURCE_COMPONENTS` row was built from -- whether building
fresh from live state or replaying a persisted packet.
`validate_packet()` feeds that same, packet-embedded `frozen_sources` back
into a fresh `build_packet()` call, so these rows are re-derived from data
that is now part of the very packet being validated, never from live,
current-moment state. This is a genuine, independent re-derivation, not a
blind acceptance of the persisted row: a semantic tamper of the row itself
(leaving `frozen_sources` untouched) still fails, because the rebuild
never reads the tampered row as its input -- it re-derives from
`frozen_sources`, which the tamper never touched.

`FROZEN_SOURCE_COMPONENTS` covers two distinct kinds of staleness:

- `STEP0_READ_MODEL_HEALTH`/`DART_FILING_CONTENT`/`SEC_FILING_CONTENT`
  (and, transitively through `STEP0_READ_MODEL_HEALTH`'s own snapshot,
  `KRX_PREOPEN_COMPACT`) read `data/briefing_status.json`'s inputs and
  `data/latest_{dart,sec}_content.json` -- *mutable rolling pointer* files
  the collector workflow overwrites every cycle with no per-date archive
  behind them. Their frozen snapshot is the raw fetched payload itself.
- `KOFIA_FIRST_SEEN`/`US_BREADTH_MEMBERSHIP`/`BTC_TREND`/`BTC_RISK`/
  `STABLECOIN_NET_ISSUANCE`/`CRYPTO_BREADTH`/`KRX_POST_CLOSE` read a
  genuinely immutable, append-only, per-date evidence archive -- once
  present, its *content* never changes (`KRX_POST_CLOSE.COLLECTOR.
  check_bundle()` re-validates the bundle against its own committed
  `source.json` every time it is read, so a re-read is a real, safe
  independent re-derivation, not a blind trust). What can still change
  between build time and a later revalidation is *presence*: a directory
  or bundle that does not exist yet at build time (correctly
  `DATA_BLOCKED`/`UNKNOWN`) can be created later the same day (or, for
  `KRX_POST_CLOSE`, the same evening), and re-deriving an old revision
  after that would wrongly promote it to `READY` -- not because the
  immutable content changed, but because presence/absence itself is not
  retroactively knowable without recording it. Their frozen snapshot is
  therefore just the presence/absence fact (plus, once present, the
  resolved directory name and its real retrieval timestamp -- see below)
  -- no content digest of the immutable bytes is needed
  (`test_evidence_that_arrives_after_build_time_never_flips_an_old_data_
  blocked_revision`,
  `test_krx_post_close_bundle_that_arrives_after_build_time_never_flips_
  an_old_unknown_revision`).

Because of this, these ten components are re-derivable forever,
independent of live/current-moment state, with no live `data/` access and
no monkeypatch required at validation time
(`test_step0_revisions_validate_across_a_rolling_pointer_change_without_
fault_injection`). All ten are marked `validated: true`, like every other
component, and there is no "cannot be independently revalidated" boundary
in `unresolved_boundaries` any more.

## Same-day recovery: append-only revisions, not a single bundle

`evidence/daily_briefing/{slot}/{decision_date}/` holds one or more
`rev-NNN/` directories (`packet.json` + `briefing.md` each) plus a
deterministic `index.json` naming the latest revision. `publish()`:

1. Builds and validates a fresh candidate packet.
2. If a prior revision exists, cheaply self-hash-checks it (tamper
   detection) -- deliberately *not* a full `validate_packet()` rebuild,
   because while evidence for `decision_date` may still be actively
   arriving, re-deriving an older revision against *today's* fuller
   evidence is expected to disagree with what that older revision correctly
   recorded at the time (e.g. a component that was `DATA_BLOCKED` before a
   capture landed). That disagreement is the trigger for a new revision,
   not evidence of tampering.
3. Compares the new candidate's per-component *semantic fingerprint*
   against the latest existing revision's (`_component_semantic_
   fingerprint()`) -- deliberately not just `{component_id: status}`: a
   component can stay `READY -> READY` across two builds while its actual
   retained value, reason, source path/sha, or authority silently changed
   underneath, and a status-only comparison would miss that as "nothing
   changed". The fingerprint hashes every row field at full fidelity for
   real-evidence/real-deterministic components (including nested source
   hashes -- e.g. `STEP0_READ_MODEL_HEALTH`'s `sources.krx.source_sha256`
   changing while status/counts stay identical still triggers a new
   revision, `test_semantic_fingerprint_includes_real_nested_source_sha_
   not_just_status`). Noise-stripping (dropping the orchestrator's own
   invocation timestamp and everything derived from it --
   `packet_sha256`, `source_sha256`, `source_as_of`, `generated_at_kst`,
   ...) is applied ONLY to the fixed, known set of purely synthetic
   components built from `generated_at` with no real external evidence
   (`_GENERATED_AT_TAINTED_SELF_HASH_COMPONENTS`) -- never as a blanket
   "drop every key ending in sha256" rule, since the same key name means
   a real signal in one component's nested content and pure invocation-
   timestamp noise in another's. If the fingerprint is identical, the
   candidate is a true no-op and the existing revision is reused
   (`created: False`) rather than publishing an identical-in-substance
   duplicate.
4. If the fingerprint differs (a previously blocked component now has real
   evidence, or a real value/reason/source changed even at the same
   status), a new `rev-NNN` is published and `index.json` is rewritten to
   point at it. No prior revision is ever edited or deleted.
5. A corrupted existing revision (self-hash mismatch) fails closed with
   `EXISTING_REVISION_INVALID` rather than silently publishing a new
   revision on top of it.

This means a first same-day run that catches several sensors mid-capture
(several components `DATA_BLOCKED`) is not a dead end: rerunning the
workflow later the same day re-aggregates from whatever now exists and adds
`rev-002` once something real has changed, while `rev-001` stays exactly as
it was. Every revision that has ever been published -- not only the latest
-- independently re-validates on its own under its own real build-time
conditions, and independently rejects a semantic tamper of its own bytes
(`test_same_day_recovery_every_revision_independently_validates_and_rejects_
tamper`).

**No automatic same-day retrigger is scheduled.** `publish()` correctly adds
a recovery revision when called again, but nothing currently re-invokes it
automatically during the day: the workflow has exactly the two approved
scheduled entry points (07:05/18:30 KST) plus manual `workflow_dispatch`. A
provider-free automatic same-day retry trigger is not implemented, and an
unapproved new cron was deliberately not added to manufacture one -- this is
an honest WBS blocker, disclosed in every packet's `unresolved_boundaries`
as `SAME_DAY_AUTOMATIC_RECOVERY_TRIGGER_NOT_SCHEDULED`.

Unlike the P8 packet builders it calls (`three_market_regime_header.py`,
`rotation_discovery.py`, `unified_decision_contract.py`, and the rest, which
all refuse to write inside the repository, since each is meant to be
invoked ad hoc against any caller-supplied packets), the orchestrator's
combined output is a deliberate new persisted evidence category: the
scheduled record of what a real run actually assembled from real repository
state.

## Human-readable rendering

`render_markdown()` turns the packet into a deterministic Markdown briefing
with sections for read-model health, sensors, Regime, Rotation/Theme,
Discovery, Rule status, Portfolio/Risk, and the decision/action boundary,
plus an explicit roll-up of every non-`READY` component and its reason, and
the packet's `unresolved_boundaries`. No section is silently hidden because
it has nothing real to show; a `PENDING`/`POLICY_BLOCKED`/`DATA_BLOCKED`/
`UNAVAILABLE` component is always listed with its reason. The rendering
opens with an explicit statement that no action, order, Production, or
trading authority is granted.

The `ROTATION_DISCOVERY` detail now includes the latest independently
validated P3-08 DART observation packet. It renders at most ten filing-title
rows plus total/raw-verified/metadata-only counts. These rows explicitly say
event type and importance are unratified, promotion is unauthorized, and
action is null; a filing observation is never presented as a recommendation.

`_format_component_detail()` pulls actual retained values out of each
component's own packet -- BTC direction/200DMA, realized-volatility and
drawdown fractions, the exact stablecoin daily/weekly net issuance amount,
US breadth member count, per-market Regime state/direction/confidence and
axis coverage ratio, Rotation/Discovery change counts, Rule PASS/FAIL/
UNKNOWN/UNDEFINED totals, Unified Decision state, and each P6 packet's
`cash_action`/`inverse_signal`/`invariant_status`/short-pass counts -- never
a raw JSON dump. A component with nothing retained (blocked/unavailable)
contributes no detail line; the status + reason above it already explains
why. A packet shape the formatter does not recognize falls back to no
detail line rather than raising, so a future upstream schema change cannot
break the whole render.

## Storage is not delivery

Committing `evidence/daily_briefing/...` to `main` is *storage*, not proof
that a person received the briefing. `.github/workflows/daily-briefing.yml`
also writes the rendered `briefing.md` into the run's job summary
(`$GITHUB_STEP_SUMMARY`) on every run, published or not -- that is the one
delivery path this workflow itself provides. Anything beyond that (a push
notification, an email, a message from Claude/Codex) is not implemented
here and must not be reported as done until live delivery evidence exists,
matching how P0-02/P0-04's own "독립 06:57/18:00 caller" gap is tracked: the
documented, ready-to-use consumption path for an external read-only
reporter is

```bash
python3 briefing/daily_orchestrator.py validate \
  evidence/daily_briefing/{slot}/{decision_date}/rev-NNN/packet.json
cat evidence/daily_briefing/{slot}/{decision_date}/rev-NNN/briefing.md
```

The operational read model does not perform that selection itself. H-24
publishes `data/briefing/daily_briefing_sources.json`, which binds one exact
slot/date/revision to the index, packet, and rendered briefing hashes. The
read-only consumer is:

```bash
python3 .github/scripts/daily_briefing_delivery.py consume \
  --slot morning --decision-date YYYY-MM-DD
```

It rejects a wrong slot/date, an index that no longer names the revision,
any path or hash drift, a missing Decision Review/Shadow component, and any
authority flag set to true. It never lists a directory, falls back to a
prior day, or guesses another slot. A BLOCKED Decision Review and a Shadow
row with no ledger record are delivered results, not delivery failures.

## Scheduling

`.github/workflows/daily-briefing.yml` runs two schedule entries (07:05 KST
morning, 18:30 KST evening weekdays) plus `workflow_dispatch`. The slot is
determined from the *exact cron expression* GitHub reports fired
(`github.event.schedule`), not from the KST wall-clock hour the runner
happens to start at -- a large scheduler delay could otherwise push a
morning run past noon KST and misclassify it as evening. An unrecognized
schedule expression fails closed rather than guessing.

The workflow does not call any collector or re-fetch anything the existing
`collect.yml` / `krx-post-close.yml` workflows already fetched; it runs the
offline regression, then always calls `briefing/daily_orchestrator.py
publish` (which itself decides whether a new revision is warranted -- see
same-day recovery above), then `briefing/daily_orchestrator.py validate` on
the result, and only commits if `publish` reported `created=true`. A step
failure here cannot lose or roll back anything the collector workflows
already committed, because this workflow never runs until after they have.

## Boundaries this integration does not cross

- No new source, threshold, taxonomy, unit, or `available_at` decision is
  made anywhere in this layer. Every blocked component cites the exact
  existing WBS gate it is waiting on.
- No Regime score, Rotation ranking, Discovery promotion, Rule PASS/FAIL,
  Portfolio sizing, action, or order authority is granted. Every
  `*_authorized` field this orchestrator touches remains `false`, and every
  component's `decision_eligible`/`action_eligible`/`order_eligible` field
  is forced `false` independent of what the embedded packet claims.
- No paid data, secret, or new provider call is introduced. The module
  contains no HTTP client of any kind.
- No new, unapproved cron schedule is introduced to manufacture an
  automatic same-day recovery retrigger. That capability is disclosed as an
  open WBS blocker instead (`SAME_DAY_AUTOMATIC_RECOVERY_TRIGGER_NOT_
  SCHEDULED`), not silently claimed.

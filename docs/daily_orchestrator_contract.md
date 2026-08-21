# Atlas Daily Briefing Integration v1 (P8 orchestrator)

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

Every one of the 32 tracked components is reported with:

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

## Determinism and publication

`build_packet(slot, decision_date, generated_at)` is a pure function of its
three arguments plus whatever is currently committed to the repository:
identical arguments against an unchanged repository state produce a
byte-identical packet.

`validate_packet()` independently re-derives every component from the same
real, decision_date-pinned evidence and requires an exact match to the
persisted packet -- with one disclosed, bounded exception. Four components
(`STEP0_READ_MODEL_HEALTH`, `KRX_PREOPEN_COMPACT`, `DART_FILING_CONTENT`,
`SEC_FILING_CONTENT` -- `NON_REVALIDATABLE_COMPONENTS`) read a *mutable
rolling pointer* file that the collector workflow overwrites every cycle,
with no per-date archive behind it. Once that pointer has moved past
`decision_date`, there is nothing left to independently re-derive those four
from, so `validate_packet()` trusts the persisted row for exactly those four
(passed through as `frozen_rows` to `build_packet`) rather than incorrectly
failing a live re-fetch. A bit-level edit of any of the four is still caught
by the outer `packet_sha256` self-hash check; a self-rehashed semantic
tamper of exactly those four is not caught by design, and is pinned by
`test_non_revalidatable_components_are_a_disclosed_bounded_exemption`
rather than left as a silent gap. It is listed in the packet's own
`unresolved_boundaries` as
`SAME_DAY_ROLLING_POINTER_COMPONENTS_NOT_INDEPENDENTLY_REVALIDATED`.

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
3. Compares the new candidate's per-component status vector against the
   latest existing revision's. If identical, the candidate is a true
   no-op -- every decision_date-pinned evidence source is immutable once
   captured (per the pinning above, modulo the four disclosed exceptions),
   so an identical status vector means nothing has changed -- and the
   existing revision is reused (`created: False`) rather than publishing an
   identical-in-substance duplicate.
4. If the status vector differs (a previously blocked component now has
   real evidence), a new `rev-NNN` is published and `index.json` is
   rewritten to point at it. No prior revision is ever edited or deleted.
5. A corrupted existing revision (self-hash mismatch) fails closed with
   `EXISTING_REVISION_INVALID` rather than silently publishing a new
   revision on top of it.

This means a first same-day run that catches several sensors mid-capture
(several components `DATA_BLOCKED`) is not a dead end: rerunning the
workflow later the same day re-aggregates from whatever now exists and adds
`rev-002` once something real has changed, while `rev-001` stays exactly as
it was.

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

using the revision `index.json` names as `latest_revision` to find the
current `rev-NNN` for a given `(slot, decision_date)`.

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

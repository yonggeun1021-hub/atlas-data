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

## Determinism and publication

`build_packet(slot, decision_date, generated_at)` is a pure function of its
three arguments plus whatever is currently committed to the repository:
identical arguments against an unchanged repository state produce a
byte-identical packet. `validate_packet()` independently rebuilds the whole
packet from the same real evidence and requires an exact match, so a
hand-edited or corrupted published packet is rejected.

`publish()` builds and validates entirely in memory, writes to a temporary
directory, and only then atomically renames it into
`evidence/daily_briefing/{slot}/{YYYY-MM-DD}/` (`packet.json` +
`briefing.md`). The target directory is append-only: a second publish
attempt for an already-published `(slot, date)` fails
`APPEND_ONLY_VIOLATION` without touching the existing bundle. Any failure
before that final rename leaves no partial directory behind.

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

## Scheduling

`.github/workflows/daily-briefing.yml` runs two schedule entries (07:05 KST
morning, 18:30 KST evening weekdays) plus `workflow_dispatch`. It does not
call any collector or re-fetch anything the existing `collect.yml` /
`krx-post-close.yml` workflows already fetched; it runs the offline
regression, then `briefing/daily_orchestrator.py publish`, then
`briefing/daily_orchestrator.py validate` on the result, and only commits
if a new bundle was actually published (an already-published `(slot,
date)` is skipped, matching the KOFIA/US-breadth append-only workflow
pattern). A step failure here cannot lose or roll back anything the
collector workflows already committed, because this workflow never runs
until after they have.

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

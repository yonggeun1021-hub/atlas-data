# Regime Live Axis Adapter (`regime_live_axis_adapter/v7`)

`regime/live_axis_adapter.py` proves, for each of the fifteen `market/axis`
combinations in the P1-COM-01 envelope, whether qualified point-in-time
evidence currently exists. It never interprets that evidence: every result is
`DEFINED` (evidence present, PIT-consistent, lineage complete, rebuilt from
raw and cross-checked against the caller's component row) or `UNDEFINED`
(fail-closed for any reason -- missing, stale, taxonomy-incomplete,
policy-unratified, or PIT-inconsistent). No axis or aggregate output from this
module, or from anything downstream that consumes it (`regime/output_contract.py`),
ever carries a Regime, direction, confidence, threshold, weight, ranking, or
action value. All `*_authorized` fields stay `false`.

## Korea five-axis evidence coverage

`regime_live_axis_adapter/v7` binds `KR/TREND`, `KR/BREADTH`, `KR/RISK_VOL`,
`KR/LIQUIDITY`, and `KR/LEADERSHIP` to the same validated official-KRX
`korea_market_signals/1` aggregate packet. `DEFINED` means only that the
measurement exists with point-in-time lineage. It is not a Korea
`RISK_ON`/`RISK_OFF` interpretation.

## US current free-data evidence coverage

`v7` binds three US axes without creating a market label:

| Axis | Qualified evidence | Boundary |
| --- | --- | --- |
| `US/TREND` | replayed SPY/QQQ/IWM IEX daily bars | presence only; IEX is partial |
| `US/RISK_VOL` | replayed FRED VIXCLS response | presence only; no risk threshold |
| `US/LIQUIDITY` | FRED WRESBAL/TOTBKCR current no-raw snapshot | response-hash attested; no historical PIT claim |

Sector ETF returns versus SPY are retained for the user-facing market screen,
but remain reference observations and cannot define `US/LEADERSHIP`.
`US/BREADTH` also stays `UNDEFINED`: the forward membership roster still has
no qualified advance/decline price population. Therefore `3/5` means only
that three inputs are observable; it is not a `RISK_ON`, `RISK_OFF`, or trade
decision.

## P1-CR-08: Crypto axis coverage

As of `regime_live_axis_adapter/v5` (P1-CR-08), all five Crypto axes have a
real binding -- up from three of five before this change:

| Axis | Source component | Status as of this PR |
| --- | --- | --- |
| `CRYPTO/TREND` | `BTC_TREND` | Bound since `v4`; unchanged |
| `CRYPTO/RISK_VOL` | `BTC_RISK` | Bound since `v4`; unchanged |
| `CRYPTO/LIQUIDITY` | `STABLECOIN_NET_ISSUANCE` **and/or** `UPBIT_MARKET_EVIDENCE` | Bound since `v4` (stablecoin); **extended** in this PR to also accept P4-07 Upbit microstructure evidence as a second, independently-qualifying input |
| `CRYPTO/BREADTH` | `CRYPTO_BREADTH` (P1-CR-06) | **Newly bound** in this PR |
| `CRYPTO/LEADERSHIP` | `CRYPTO_LEADERSHIP` (P1-CR-07) | **Newly bound** in this PR, but see the blocker below |

### `CRYPTO/LIQUIDITY`'s two-input rule

`CRYPTO/LIQUIDITY` is `DEFINED` when **at least one** of its two qualifying
inputs has qualified, PIT-valid evidence -- an "any qualifying input present"
rule, still a pure presence check, never a judgment about which input is more
informative:

- Stablecoin net issuance only -> `DEFINED` (unchanged behavior from `v4`;
  cites the stablecoin evidence pointer; warns
  `CRYPTO_LIQUIDITY_UPBIT_MICROSTRUCTURE_INPUT_UNAVAILABLE`)
- Upbit microstructure only -> `DEFINED` (new in this PR; cites the Upbit
  evidence pointer; warns `CRYPTO_LIQUIDITY_STABLECOIN_INPUT_UNAVAILABLE`)
- Both present -> `DEFINED` (cites the stablecoin pointer for deterministic,
  backward-compatible byte-identity with `v4` outputs; no sibling-missing
  warning)
- Neither present -> `UNDEFINED` (`LIVE_AXIS_EVIDENCE_UNAVAILABLE`)

In production today, real Upbit microstructure evidence is not yet
achievable even when its binding logic runs: `config/upbit_market_evidence_policy.json`
carries `approval_status: "PROPOSED_UNRATIFIED"`, and no
`evidence/crypto/upbit/microstructure/` raw capture directory is committed
yet. `CRYPTO/LIQUIDITY` therefore currently resolves via the stablecoin path
alone, exactly as it did before this PR -- fully backward compatible.

### `CRYPTO/BREADTH` coverage-ratio diagnostics (observability only, not a new gate)

`CRYPTO/BREADTH`'s `DEFINED`/`UNDEFINED` pass/fail semantics are unchanged:
it is `DEFINED` only when `crypto_breadth.py::build_transform()["status"]`
is exactly `"OBSERVED_UNCLASSIFIED"`, exactly as before. What changed is
that `CRYPTO_BREADTH`'s component row now also carries real, already-computed
taxonomy-coverage diagnostics from `qualified_members()`'s own `universe`
block -- `target_asset_count`, `known_eligible_count_so_far`,
`resolved_cutoff_slot_count`,
`taxonomy_unknown_before_cutoff_count`/`_assets`, and a derived
`coverage_ratio_bps` (resolved cutoff slots divided by target slots). An
unresolved above-cutoff asset therefore keeps the ratio below 100% even when
the already-known eligible population equals the target count -- so a reviewer or portal can
see *how close* an `UNKNOWN`/`TAXONOMY_COVERAGE_UNKNOWN` day is to clearing
the gate, and exactly which asset(s) are blocking it, without weakening the
existing all-or-nothing gate in `qualified_members()` (that gate is
deliberately strict: an unresolved candidate ranked above the selection
cutoff could in principle displace an already-selected member, so it must
never be treated as "close enough"; see that function's own docstring). No
new pass path was added -- the gate already reaches `DEFINED` with real
committed evidence whenever real coverage is complete (see
`test_crypto_breadth_defined_with_real_evidence_on_taxonomy_complete_day` in
`test/test_regime_live_axis_adapter.py`, which predates this change and uses
real `2026-08-28` evidence). `regime/live_axis_adapter.py::_crypto_breadth()`
independently re-derives the same diagnostics from a fresh
`crypto_breadth.py` rebuild and requires an exact match
(`COMPONENT_REDERIVATION_MISMATCH` otherwise), so a tampered or stale
diagnostic value cannot silently ride along with a real axis result.

### `CRYPTO/LEADERSHIP` row wiring (resolved) and its remaining natural-history blocker

`regime/live_axis_adapter.py`'s bindings only ever *consume* a `component_id`
row handed to `build_axis_factors()`; they never fetch or capture evidence
themselves. `briefing/daily_orchestrator.py` produces a `CRYPTO_BREADTH` row
(added by P1-CR-06) and, since the Breadth/Leadership axis-wiring follow-up to
P1-CR-08, also produces a `CRYPTO_LEADERSHIP` row -- `build_packet()` now
calls `build_crypto_leadership()`/`_classify_crypto_leadership()` the same way
it already called `build_crypto_breadth()`/`_classify_crypto_breadth()`, and
`CRYPTO_LEADERSHIP` is a `FROZEN_SOURCE_COMPONENTS` member sharing
`CRYPTO_BREADTH`'s `evidence/crypto/breadth/raw` source root (mirroring how
`BTC_TREND`/`BTC_RISK` independently freeze the same `evidence/crypto/btc/raw`
directory). `build_crypto_leadership()`/`_classify_crypto_leadership()`
themselves were not new -- P1-CR-08 already added them for
`regime/crypto_live_component_registry.py`'s independent rebuild path -- what
was missing, and is now fixed, was the call from the main daily briefing
`rows` dict that `build_regime_outputs()`/`build_axis_factors()` actually
reads.

`UPBIT_MARKET_EVIDENCE` remains genuinely unwired into `daily_orchestrator.py`
-- that is unchanged and out of scope for this update; see P4-07/Upbit
liquidity follow-up work.

Independently of the row-wiring, `CRYPTO_LEADERSHIP`'s own dual-window
methodology (a 7-day pilot window and a 30-day primary window, both built
from the same CR-06 Crypto Breadth snapshots) needs a real, unbroken run of
history to observe. `config/crypto_leadership_policy.json`'s
`effective_from` is `2026-08-19`; the 30-day primary window cannot complete
until 30 real contiguous days of `evidence/crypto/breadth/raw/` snapshots
exist on/after that date (around `2026-09-17`/`2026-09-18`, assuming the
daily capture workflow keeps running without a gap). Even the shorter 7-day
pilot window requires every one of its seven days' own independent
`crypto_breadth.py` transform to resolve `OBSERVED_UNCLASSIFIED` (not just
be present) -- a single day inside the window failing its own taxonomy-
coverage or 90%-observation gate (see the `CRYPTO/BREADTH` coverage-ratio
section above) fails the whole window closed
(`SOURCE_POINT_UNKNOWN`). `CRYPTO/LEADERSHIP` therefore correctly continues
to report `UNDEFINED` today even though the row now exists -- this is a
second, independent, and equally genuine reason, on top of (not instead of)
the row-wiring that this update resolves. Nothing in this update forces,
backfills, or shortcuts that natural-history requirement.

## Known, user-acknowledged scope decision: evidence-presence only, not the Notion "5축 판정" interpreted values

P1-CR-08's Notion Crypto policy document describes a "5축 판정" (five-axis
determination) section whose axis values are **interpreted** market-state
labels (e.g. POSITIVE/NEUTRAL/NEGATIVE, 확산/편중/붕괴-style judgments for
breadth/leadership dispersion or concentration). This PR does **not**
implement that. It implements only `DEFINED`/`UNDEFINED` evidence-presence
per axis, matching the ratified `P1-COM-01` common Regime contract
(`regime/output_contract.py`) exactly, and does not add, weaken, or bypass
that contract's `PRE_SCORE_UNKNOWN_ONLY` enforcement in any way.

This is a real, identified gap between the Notion canon and this repository's
actual ratified authority -- surfaced during P1-CR-08's investigation and
escalated to the user, who explicitly chose evidence-presence-only scope
(2026-08-29) rather than asking this PR to invent an unratified interpretation
policy. It is a known, recorded, user-acknowledged limitation pending a
future policy/contract reconciliation -- not an implementation oversight, and
not something this PR should be read as quietly deciding on its own
authority. Any future work that wants to emit POSITIVE/NEUTRAL/NEGATIVE-style
axis values (or any other interpreted state) needs its own explicit design,
ratification, and a new `regime_output_contract` schema version -- this
module's `EVIDENCE_ONLY_NO_INTERPRETATION` mode and the `REGIME_INTERPRETATION_UNAUTHORIZED`
warning discipline on every `DEFINED` factor exist specifically to keep that
boundary honest until such a decision is made.

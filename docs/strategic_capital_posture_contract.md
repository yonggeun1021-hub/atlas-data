# P7-12 Strategic Capital Posture readiness contract

`portfolio/strategic_capital_posture.py` is a zero-capital readiness boundary,
not an allocation engine.  It inventories the P1 Regime, P2 Flow/Rotation,
P6 Defensive Action, and P7 portfolio-risk packets required before a
cross-market budget can be evaluated.  Every supported P2/P6/P7 packet is run
through its original validator again at the consumption boundary; schema,
status, hash, time, and closed execution authority are not trusted merely
because a JSON object exists.

`P2_CROSS_MARKET_FLOW` is bound to the P2-COM-02 cross-market flow reference
(`capital_flow_posture_reference/v1`, statuses `REFERENCE_AVAILABLE` and
`PARTIAL_REFERENCE_AVAILABLE`) through the same rename-only adapter precedent
P6-06 already uses for its `P2_FLOW_ENGINE` slot: that producer names its
identity field `payload_sha256` and its checker `validate_reference`, and the
adapter renames only the identity field so the generic source path applies
unchanged.  The producer's own validator re-derives the packet from committed
evidence and fails closed on any tamper.  Binding this source supplies
evidence and lineage only; it asserts no money flow, creates no leader or
laggard claim, and unlocks no budget, posture, action, or authority.

Point-in-time validation uses each source contract's effective availability
field, not only its date label.  P2 cross-market flow and P6 are bounded by
their top-level `generated_at`; the self-validating P7 concentration,
market/theme, crypto, and planned-loss packets are bounded by their embedded
input `generated_at_utc`; currency exposure is bounded by `available_at`.  A
semantically valid upstream packet whose effective availability is later than
the P7-12 consumer `generated_at` fails closed as `SOURCE_FROM_FUTURE`.

The current repository has no ratified P1 Regime Decision, P2 Rotation State
production contract, or Strategic Capital Posture policy.  Those two slots stay
unavailable-only, and their `*_UNAVAILABLE` unresolved boundaries are derived
from the contract's `unavailable_only_source_slots` rather than a fixed list,
so the boundary list can never disagree with the slots themselves.
Consequently the only valid runtime result is
`STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED`.  `market_budget` exposes the
three market keys with `null` values, while `cash_reserve`, `hedge_budget`,
`max_gross_risk`, `max_net_risk`, and `theme_headroom` are also `null`.
Missing never means zero, and BLOCKED never means `NO_ACTION`.

## `P1_REGIME_DECISION` blockers and the daily derivation version

The `P1_REGIME_DECISION` slot stays unavailable-only, but *why* it is
unavailable is reported at two different fidelities depending on the daily
packet's `runtime_regime_readiness_version` derivation marker in
`briefing/daily_orchestrator.py`:

| marker | P6-06 `P1_REGIME_DECISION` reasons | P7-12 `P1_REGIME_DECISION` reasons | `ACTION_RISK_PORTFOLIO_SUMMARY` component row `as_of_date` |
| --- | --- | --- | --- |
| absent | generic `*_PRODUCTION_CONTRACT_UNAVAILABLE` | generic | ambiguous: KST business date **or** `generated_at` UTC day |
| exactly `1` | exact re-derived runtime blockers | generic | ambiguous: KST business date **or** `generated_at` UTC day |
| exactly `2` (default) | exact re-derived runtime blockers | exact re-derived runtime blockers | KST business date of the validated summary packet |

Under version 2 the P7-12 reasons are re-derived by the orchestrator from the
same `regime_output/v1` envelopes the run already built, run through
`regime/runtime_regime_readiness.py` and
`portfolio/defensive_action_decision.py::p1_regime_decision_unavailable_reasons`,
and validated again by this module's own `_reasons` guard.  P7-12 does not
read P6-06's packet or component row to obtain them.  Only sorted reason
codes are forwarded: the readiness packet's own `packet_sha256`, `age_seconds`
and every other invocation-derived value are deliberately excluded, so a
component's semantic fingerprint does not change merely because the briefing
was rebuilt at a different time.

Naming the real blockers changes nothing else.  The slot stays `UNAVAILABLE`
with a null source identity, `decision_status` stays `BLOCKED`, budgets stay
null, `order_intents` stays empty and every authority flag stays false.  This
is a derivation version, not a policy version, a ratification, or an authority
change.

## Legacy replay compatibility, and what it does not prove

A marker-absent or explicit-`1` daily packet is ambiguous about exactly one
field: the `ACTION_RISK_PORTFOLIO_SUMMARY` *component row*'s `as_of_date`.
Packets of both kinds were genuinely issued under the earlier
`generated_at`-UTC-day basis and under the current KST-business-date basis,
and nothing inside such a packet records which.  `validate_packet()` therefore
rebuilds a legacy packet **in full** under each of those two enumerated bases
and accepts it only on complete canonical equality with an entire
reconstruction.  The persisted row's stored `as_of_date` is never read as a
reconstruction input and is never copied into a rebuild to force a match, so
every other tamper — source bytes, dates, reasons, authority, row content, an
unknown or re-signed version — still fails closed.  For same-KST-day geometry
(the 18:30 KST evening run) the two reconstructions are byte-identical and the
check collapses to a single historical result.

This is historical compatibility, **not release-origin authentication**.
Accepting a legacy packet proves it is one of the two valid historical
derivations of its own recorded inputs.  It does not prove which release
produced it, and the two legitimately valid legacy forms cannot be
distinguished from each other as provenance.  Authenticated release-specific
provenance would require retained producer identity that these packets do not
carry, and is out of scope here.

Version 2 has exactly one derivation.  It never falls back to a legacy form,
and the archival date basis is unreachable from any new build.  An explicitly
persisted null marker is rejected rather than treated as absence, as are
booleans, strings, `0`, negatives and unknown integers.

Allocation-sum, overlap-exposure, and currency-boundary checks are present as
explicit `NOT_EVALUATED` rows.  They cannot report PASS until the required
source packets and a separately ratified policy exist.  In particular,
amounts in different quote currencies cannot be added without a ratified FX
conversion boundary, and overlapping market/theme exposures cannot be counted
as independent headroom.

This capability grants readiness-inventory authority only.  It grants no
Regime, Flow, Rotation, policy, budget, allocation, cash, hedge, gross/net
risk, theme headroom, FX conversion, action proposal, position sizing, order,
Production, or trading authority.  The CLI is offline and may write only to a
path outside the repository so a diagnostic run cannot create tracked state.

Completing this capability does not complete the P7-12 WBS Exit Gate.  Actual
numeric budgets and posture remain a separate CIO/user-ratified policy step.

# P7-05 Crypto Separate Exposure Limit Contract

`portfolio/crypto_exposure_limit.py` is a PAPER-only risk evaluator bound to
the cached canonical WBS row `P7-05`, page
`3bf9f2d7-3c84-816c-ac6a-e59938e2d99d`, Order `705`, Status
`🔵 검증대기`. The contract pins the cached snapshot and exact row hashes; it
does not update the WBS or create new authority evidence.

The four ratified PAPER v1 limits are exact contract values:

- per-trade planned loss: `"0.0025"` of virtual NAV;
- total Crypto exposure: `"0.05"` of virtual NAV;
- single-asset Crypto exposure: `"0.02"` of virtual NAV;
- concurrent open positions: integer `3`.

Decimal limits remain canonical decimal strings and concurrency remains an
exact integer. Numeric/boolean aliases, alternate decimal spellings, caller
overrides, different source hashes, and self-rehashed substitutions fail
closed. Each input position has a unique `position_id`. Multiple positions in
one asset are allowed and are summed before the single-asset check; concurrency
counts position records, not distinct assets. An explicit empty position list
evaluates exposure and concurrency as integer/numeric zero.

The canonical row does not ratify total planned-loss or annualized realized
volatility limits. Those fields remain exactly `null` / `UNKNOWN`; their
assessments are always `NOT_COMPUTABLE` with `UNRATIFIED_LIMIT`. The existing
`btc_risk/v1` observation identity is still validated as evidence, but it is
not converted into a limit, Regime, selection, sizing, or action. No default
value or caller-supplied value may fill either unresolved limit.

Output schema `crypto_exposure_packet/2` retains the two consumer-facing
success states. `WITHIN_RATIFIED_LIMITS` means only the four ratified PAPER
axes passed; it does not claim that unresolved axes passed. A breach is an
evidence-only risk result. Recommended action, target exposure, position sizes,
and order intent stay `null` or empty, and repository-default policy,
automatic reduction, sizing, order, Production, and Trading authority all
remain false.

`validate_packet()` revalidates the embedded input and policy packets, exact
WBS source identity, hashes, effective interval, upstream P7-04 status, asset
and position lineage, and every derived assessment. CLI output inside the
repository tree remains forbidden. Natural virtual-account replay and
exposure/drawdown/concurrency observation are still required before the WBS
Exit Gate can complete; fixture tests are not natural evidence.

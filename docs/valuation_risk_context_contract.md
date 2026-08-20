# P3-10 Valuation / Risk deterioration context contract

Status: offline attachment capability; live context population and interpretation policy are not implemented.

## Candidate boundary

`discovery/valuation_risk_context.py` consumes immutable references to existing
Discovery Cases. A reference includes the case schema, case ID, asset/market,
observation date, and exact case payload SHA-256. The output never rewrites the
candidate, changes its rank or Stage, evaluates a Rule, or creates an action.

For each candidate the packet keeps separate `VALUATION` and `RISK` sections.
If a dimension is absent it is `EVIDENCE_ABSENT`; incomplete observations are
`UNKNOWN_EVIDENCE`. Neither state is converted to zero, neutral, or safe.

## Raw context

Every context metric uses exactly two caller-declared dates. The helper emits
the prior value, latest value, and `latest - prior` change together with all
source identities. Valuation metrics for US/Korea require both a fundamental
source and a market-price source at each point. Risk metrics require the
market-specific price source. Crypto valuation is intentionally unsupported
and remains `UNDEFINED_NO_RATIFIED_METRIC`.

This capability reuses source fragments; it does not claim that their upstream
P1 contracts are operationally ratified. In particular, US/Korea risk input
policies and Korea publication timing remain open.

## Deterioration policy gate

Raw context never implies that a candidate deteriorated. An external policy
must be effective and explicitly `RATIFIED`, and must bind the exact market,
context ID, dimension, measurement, metric type, unit, and comparison basis.
Only that policy may specify `HIGHER_IS_DETERIORATION` or
`LOWER_IS_DETERIORATION` and a non-negative minimum change.

The policy hash and ratification proof are attached to an interpreted metric.
There is no repository default policy, metric selection, or source hierarchy.
Even a matched deterioration label grants no candidate rank, Stage promotion,
Rule evaluation, portfolio action, Production, or trading authority.

## Operation

The helper makes no network request and writes only to an explicit path outside
the repository. A failed run preserves an existing output. Workflow wiring,
tracked context artifacts, live population, and briefing integration remain
separate WBS gates.

# P6-05 RISK_OFF/STRESS != automatic inverse order

This capability enforces the separation between a market-state observation and
an executable bearish position. `RISK_OFF` or `STRESS` never selects an inverse
instrument, creates an inverse signal, or creates an order intent.

The current `regime_output/v1` runtime authorizes only `UNKNOWN`. The integration
path validates that complete upstream output contract before emitting an
invariant packet with all inverse fields null and status `NOT_EVALUATED`.

The invariant primitive separately accepts the full future Regime vocabulary so
the critical boundary can be tested today. Direct `RISK_OFF` and `STRESS` inputs
still produce:

- `inverse_instrument=null`
- `inverse_signal=null`
- `inverse_order_intent=null`
- `inverse_evaluation_status=NOT_EVALUATED`

An inverse position requires independent ratification of hedge-instrument
eligibility, a separate bear/hedge risk budget, an inverse-strategy evaluation,
and order risk checks. This module does not implement any of those decisions.
The CLI is offline and writes only outside the repository.

# Free market data evidence

This slice captures FRED `VIXCLS` observations and, when a dedicated
market-data-only credential is present, Alpaca Basic IEX latest and bounded
daily bars. It is evidence-only. IEX is a single-exchange partial US feed and
cannot authorize US breadth, market-wide prices, entry, action, order, broker
submission, production, or trading.

FRED response bytes are transient and never published. Atlas retains only the
derived value, observation/vintage metadata, response SHA-256, and the explicit
`TRANSIENT_NOT_PERSISTED` retention marker. Alpaca bytes may be retained as
deterministic gzip files only under the separate IEX evidence boundary.

Provider outcomes are independent. Missing or failed Alpaca credentials produce
an explicit component-level `BLOCKED_BY_*` or `ALPACA_CAPTURE_FAILED:*` state
while a valid FRED derived observation is still published. This is a partial,
degraded evidence result—not a market-wide price, Regime, or trading PASS.
Malformed FRED data, authority drift, fabricated Alpaca rows in a blocked state,
or a non-IEX contract fail closed.

# Free market data evidence

This slice captures FRED `VIXCLS` observations and, when a dedicated
market-data-only credential is present, Alpaca Basic IEX latest and bounded
daily bars. It is evidence-only. IEX is a single-exchange partial US feed and
cannot authorize US breadth, market-wide prices, entry, action, order, broker
submission, production, or trading.

FRED response bytes are public market evidence and are retained in a
content-and-capture-addressed append-only revision. Each revision contains a
deterministic gzip response and a manifest; their immutable locators and hashes
are included in `free_market_data_capture/4`. Repeated identical capture is a
byte-identical no-op, while a different response on the same day creates a new
revision and cannot overwrite the earlier one. An independent validator reads
the exact retained bytes, recomputes their hashes, re-derives the latest VIXCLS
observation, checks the capture time, and compares it with the derived pointer.
Alpaca bytes remain under the separate IEX evidence boundary.

Provider outcomes are independent. Missing or failed Alpaca credentials produce
an explicit component-level `BLOCKED_BY_*` or `ALPACA_CAPTURE_FAILED:*` state
while a valid FRED derived observation is still published. This is a partial,
degraded evidence result—not a market-wide price, Regime, or trading PASS.
Malformed or future FRED data, raw/manifest/path tampering, authority drift,
fabricated Alpaca rows in a blocked state, or a non-IEX contract fail closed.

The independently replayed VIX observation may define only the US `RISK_VOL`
evidence axis. It does not interpret the value, classify a Regime, assign a
direction or confidence, rank a market, or authorize an investment action.

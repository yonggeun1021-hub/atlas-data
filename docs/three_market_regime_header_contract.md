# P8-04 three-market Regime header

This read-model adapter requires exactly one valid `regime_output/v1` packet
for each market, ordered as US, Korea, and Crypto. It exposes each source's
Regime state, direction, confidence, coverage, evidence date, availability,
warnings, and SHA-256 in one morning/evening header.

The current Regime source contract authorizes only `UNKNOWN` state,
`UNKNOWN` direction, and `confidence=null`. The header preserves those values;
it cannot turn incomplete evidence into `NEUTRAL`, a score, or a confidence.

The header is deliberately non-interpretive:

- it does not rank the three markets or choose a favorable one;
- it does not create strategy eligibility or an action;
- it does not authorize Production or trading;
- it does not fetch data or write inside the repository.

Missing, duplicated, unexpected, malformed, or future-dated source packets are
rejected. The output digest makes later presentation-layer mutation visible.
Production briefing wiring and live morning/evening observation remain outside
this capability.

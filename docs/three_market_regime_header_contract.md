# P8-04 three-market Regime header

This read-model adapter requires exactly one valid `regime_output/v1` packet
for each market, ordered as US, Korea, and Crypto. It exposes each source's
Regime state, direction, confidence, coverage, evidence date, availability,
warnings, and SHA-256 in one morning/evening header.

Header schema v2 also retains the three complete validated Regime packets in
`source_packets`. `validate_header()` reruns the production Regime validator and
rebuilds each projected row, source SHA, market order, summary, authority, and header
digest. A caller cannot change a projected coverage/evidence field and a plausible
source SHA, recompute the outer hash, and have the header remain valid.

The current Regime source contract authorizes only `UNKNOWN` state,
`UNKNOWN` direction, and `confidence=null`. The header preserves those values;
it cannot turn incomplete evidence into `NEUTRAL`, a score, or a confidence.

The evidence-only live-axis adapter also publishes exact deferred-axis reasons.
For Korea Breadth, the committed `korea_breadth_context_lineage/2` receipts
retain source hashes and observation timestamps but deliberately omit the
participation counts needed to rederive a market-wide Breadth observation.
They therefore remain `UNDEFINED` with
`MARKET_WIDE_SOURCE_CONTENT_NOT_RETAINED`. Even a self-rehashed receipt with
fabricated counts cannot define that axis. This is a source-readiness boundary,
not a Breadth state, normalization rule, or Regime policy.

The header is deliberately non-interpretive:

- it does not rank the three markets or choose a favorable one;
- it does not create strategy eligibility or an action;
- it does not authorize Production or trading;
- it does not fetch data or write inside the repository.

Missing, duplicated, unexpected, malformed, or future-dated source packets are
rejected. The output digest makes later presentation-layer mutation visible.
Production briefing wiring and live morning/evening observation remain outside
this capability.

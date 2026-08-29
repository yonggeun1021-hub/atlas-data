# Korea market five-signal observation contract

`korea_market_signals/1` closes the Korea market-wide **data-connection** gap
without creating an investment decision. It uses only the already-approved,
official KRX Open API stock and index daily endpoints.

One append-only observation binds five plain measurements to the Regime axes:

- `TREND`: one-session KOSPI and KOSDAQ benchmark returns.
- `BREADTH`: advancing, declining and unchanged counts across the exact KRX
  KOSPI/KOSDAQ stock responses.
- `RISK_VOL`: benchmark absolute moves and the cross-sectional mean absolute
  stock move. These are measurements, not a stress threshold.
- `LIQUIDITY`: whole-response trading value and turnover, compared with the
  preceding completed session.
- `LEADERSHIP`: official KRX sector-index returns relative to their own KOSPI
  or KOSDAQ benchmark. Largest and smallest observed relative returns are a
  descriptive ordering only, never an investable ranking.

The producer discovers the latest two dates for which all four KRX response
families (KOSPI/KOSDAQ stock and index) are present. It never assumes that a
weekday is a trading day. A supplied explicit pair must also be ordered and
complete.

## Persistence and lineage

The producer persists only aggregate/derived metrics, endpoint identities,
response SHA-256 values and real fetch timestamps. Raw response bodies,
per-symbol prices and per-symbol identities are memory-only and have
`raw_persistence: 0` / `per_symbol_persistence: 0`.

Packets are append-only at
`data/observations/korea_market_signals/YYYY-MM-DD/packet.json`. The rolling
pointer `data/latest_korea_market_signals.json` is a byte-identical copy of the
latest validated packet. A same-date rerun validates and reuses the committed
packet; it does not overwrite it with a new capture time.

## Authority boundary

The output status is always `OBSERVED_UNCLASSIFIED`. It proves five-axis
evidence availability only. It does not authorize a Regime label, threshold,
confidence, market preference, stage, buy, action, order, production or
trading. The separate P1-COM-05 policy-value and replay ratification remains
the only route to an authoritative `RISK_ON` / `NEUTRAL` / `RISK_OFF`
classification.

# Hedge Instrument Eligibility Registry (P6-02)

Status: explicit registry validator implemented; no repository default
instrument, automatic selection, sizing, Production, or trading authority.

## Contract

Atlas accepts an index/sector hedge instrument only from an external registry
that is explicitly `RATIFIED` by the CIO and bound by an exact packet SHA-256.
Every effective record must identify the market, venue, symbol, currency,
instrument type, hedge scope, and hedged exposure.  It must also carry dated,
source-hashed observations for both cost and tracking error plus an explicit
eligibility decision and its evidence hash.

Metric names, units, values, and the eligibility decision come from the
ratified registry.  This validator checks their shape, lineage, effective
dates, deterministic ordering, and non-overlap; it does not create thresholds
or reinterpret measurements.  Missing cost/tracking evidence, identity drift,
overlapping decisions, a non-CIO registry, or a hash mismatch fails closed.

## Authority boundary

An `eligible=true` record means only that the external registry's eligibility
decision was validated.  The output never chooses among eligible instruments,
calculates hedge size, or creates an order.  `selected_instrument` and
`hedge_size` remain `null`, and `order_intents` remains empty.

There is intentionally no committed default registry.  Until a real registry
is separately approved, the WBS capability is implemented but operating
eligibility remains unresolved.

## Offline use

```bash
python3 portfolio/hedge_instrument_eligibility.py /tmp/registry.json \
  --as-of-date 2026-08-21 \
  --out /tmp/hedge-eligibility.json
```

The CLI is offline and refuses to write inside the repository.

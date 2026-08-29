# P8-16 Crypto Funnel & PAPER Decision Briefing

`briefing/crypto_funnel_briefing.py` is a public, read-only projection of one
exact `crypto_paper_decision_snapshot_packet/1` generation. It revalidates the
source packet and every retained P3-12/P4-07/P9-06 source byte before exposing
the five Crypto axes, funnel counts, candidate reasons, finalized-candle and
realtime freshness, and authority flags.

The JSON packet is the API/Portal contract. `rendered_markdown` is derived from
that same packet, so the briefing cannot report different counts or reasons.
No threshold, Regime interpretation, candidate promotion, PAPER order, or
exchange action is computed here.

## Funnel semantics

- `OBSERVATION_POOL`, `TRADEABLE_UNIVERSE`, `FOCUSED_REVIEW`, and
  `PAPER_READY` are copied from the exact P5 decision generation.
- `PAPER_POSITION` is `UNKNOWN` with `count=null` until Mac A's private P10/P7
  runtime exposes a separately approved redacted summary. Missing private
  state is never displayed as zero and no private holdings, quantities,
  balances, fees, or P&L are committed to this public repository.
- Candidate trend, relative strength, liquidity, trigger, and order-draft
  fields are projections of the already-derived P5-08/P5-09 fields. Missing
  evidence remains null/UNKNOWN.

## Time and freshness

The packet preserves the source decision's explicit UTC/KST time basis. The
finalized-candle attestation and realtime quote/orderbook freshness remain
separate. A stale, missing, mixed-generation, or tampered source is rejected or
shown exactly as the source's fail-closed state; no earlier generation is used
as fallback.

## Authority

Only `briefing_read_model_only=true`. PAPER order, Upbit exchange order,
withdrawal, Production, Trading, and real-capital authority are false. This
contract uses public market data only and contains no network or order client.

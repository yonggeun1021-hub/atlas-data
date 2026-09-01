# Gate 2–3 three-market Regime + Rotation aggregate

This isolated read model consumes exact receipt descriptors from PAPER 12-4,
KRX 12-5, US 12-6, Crypto 12-11, the released Crypto spot adapter/common
funnel pair, and P2-COM-02 Flow Rotation. It emits independent Gate 2 Regime
and Gate 3 Rotation receipts for KRX, US, and Crypto, two aggregate headers,
and an eight-entry hash-chained transition ledger.

It does not read market sources, run a market writer, define or ratify a
threshold, classify a Regime, rank a candidate, create a strategy, mutate a
virtual account, or call a broker. A missing or invalid market descriptor is
localized to that market as `WAIT/UNKNOWN/HOLD` and `PENDING/BLOCKED`; the
other market receipt hashes are unchanged.

## Current fail-closed facts

- KRX has a ratified leadership policy and `5/5` coverage, but scoring,
  signed-direction, freshness/TTL, and hysteresis authority are absent. Its
  Gate 2 result is `WAIT/UNKNOWN/HOLD`; Korea rotation stays `PENDING`.
- US leadership and coverage remain unratified with `0/5`. Its Gate 2 result
  is `WAIT/UNKNOWN/HOLD`, and rotation is `DEGRADED/BLOCKED`.
- Crypto leadership remains ratified while group coverage is unratified at
  `0/5`. Private merge `742053c…` and public common-funnel `7e6021f…` expose
  eight natural candidates, but Regime, relative-strength, liquidity, and
  four-component score evidence remain incomplete. Therefore
  `INVESTMENT_PAPER=0`, Regime is `UNKNOWN/HOLD`, and rotation is blocked.

Every aggregate authority flag is false. Candidate state remains `NONE` in
the strategy-authority sense; the separately displayed count of eight is
observational adapter ingress and cannot become PAPER authority.

## Offline CLI

```sh
python3 -m paper_gate_2_3_three_market_aggregate.cli build \
  --input paper_gate_2_3_three_market_aggregate/fixtures/current_blocked/input_bundle.json \
  --output /tmp/gate23-aggregate.json

python3 -m paper_gate_2_3_three_market_aggregate.cli verify \
  --input paper_gate_2_3_three_market_aggregate/fixtures/current_blocked/input_bundle.json \
  --aggregate /tmp/gate23-aggregate.json
```

The fixture is non-promotable regression evidence. Natural inputs must arrive
as separately owned exact receipts and may not be inferred from this file.

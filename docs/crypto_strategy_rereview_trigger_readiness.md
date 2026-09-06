# P10-12 Crypto strategy re-review trigger readiness

This is a PAPER-shadow preparation boundary, not a strategy or backtest. It
turns the four canonical re-review triggers into a deterministic, fail-closed
readiness packet while preserving Candidate `NONE` and live engine count `0`.

The primary WBS binding is **P10-12 — Crypto PAPER Counterfactual Validation &
Live Review Gate**. P1-CR-08, P1-CR-09, and P4-07 are supporting evidence rows;
this capability does not create a duplicate WBS item.

## Current result

All four triggers remain unproven:

1. Capital ≥ USD 10,000 is `NOT_COMPUTABLE` because there is no canonical
   account-capital packet. No private account or broker connection is read.
2. A ratified Regime change is `NOT_COMPUTABLE` because the current official
   crypto Regime is `UNKNOWN` and no ratified baseline/current comparison
   contract exists.
3. A genuinely new measurement source is `FAIL` because the general source
   audit has no ratified strategy-measurement-family baseline. Venue visibility
   alone is not treated as a new causal measurement.
4. A material exchange policy/cost change is `FAIL` because there is no
   canonical historical observation pair or ratified materiality policy. A
   current public fee quote alone cannot establish a change.

Because the proven-trigger count is zero, the four-question mechanism
qualification and the 7/7 Event Study gate are both
`NOT_EVALUATED_TRIGGER_NOT_PROVEN`. No failed strategy is revived and no new
candidate is created.

## Local audit

Write the derived packet outside the repository:

```bash
python3 audit/crypto_strategy_rereview_trigger_readiness.py \
  --out /tmp/crypto-strategy-rereview-trigger-readiness.json
```

The packet binds the exact public commit, readiness contract, official crypto
Regime status, and source inventory hashes. Rehashed semantic tampering,
authority escalation, malformed inputs, and writes into the tracked repository
fail closed.

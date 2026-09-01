# PAPER 12-31 — US upstream Gate 1-4 natural lineage

This isolated package connects the existing committed 2026-08-31 US natural
observations to the existing PAPER 12-6 deterministic market-judgement
receipt. It does not reimplement the US universe, leadership, completed-bar,
or rotation modules.

The natural inputs prove that daily IEX ETF bars, representative breadth and
sector leadership references, and a 13,177-row forward source-coverage
universe were observed. They do **not** prove an official finished session,
freshness, canonical five-axis Regime, investable universe, or candidate / entry
/ exit eligibility. The receipt therefore stays `UNKNOWN / HOLD / 0/5`.

Exact blockers are separated by Gate:

1. official date-specific exchange calendar and completed 15m/1h series;
2. ratified US freshness policy, TTL, and provider SLA;
3. ratified five-axis classification, price-breadth authority, and leadership
   policy;
4. investable-universe/liquidity/leadership classification plus candidate,
   entry, hold/exit, and P8-13 policies.

`natural_gate_receipt.json` is a machine-readable natural-lineage report. Its
nested PAPER 12-6 receipt exposes the exact PAPER 12-4 and PAPER 12-1 subtree
pins. Natural evidence is consumed only from immutable committed paths; there
is no natural-evidence fixture.

Run the focused suite with:

```bash
python3 -m unittest paper_12_31_us_upstream_gate_lineage.tests.test_receipt -v
```

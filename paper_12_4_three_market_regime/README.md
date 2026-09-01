# PAPER 12-4 three-market Regime receipt boundary

This isolated package validates retained KRX, US, and Crypto market-input
envelopes and emits independent fail-closed Regime receipts, a three-market
header, rotation-input readiness, exact source lineage, and a transition
ledger.

It does not fetch data, score a market, rank markets, create a strategy,
authorize PAPER, submit an order, or change production/trading state. Every
receipt remains `regime=UNKNOWN`, `paper_disposition=HOLD`, and all authority
flags remain false. `INPUTS_READY` means only that the envelope may be handed
to an externally owned, ratified classifier; it is not PAPER authority.

## Contracts

- `contracts/market_input_envelope.schema.json`: one market's leadership,
  sector-flow, and five-axis input envelope.
- `contracts/receipt_bundle.schema.json`: market receipts, header, ledger, and
  closed authority boundary.
- `receipt_pipeline.py`: completed-bar, source-time, TTL, coverage, policy,
  exact source hash, nested canonical snapshot hash, receipt/header/ledger
  derivation, and tamper validation.
- `cli.py`: offline `build` and `verify` commands.

`source_path` and nested `canonical_snapshot.path` are repository-relative and
must remain inside the supplied source root. Present sources require an exact
SHA-256 match. Missing sources are represented explicitly and become `WAIT`;
they are never inferred from another market.

## Offline usage

```sh
python3 -m paper_12_4_three_market_regime.cli build \
  --envelope paper_12_4_three_market_regime/fixtures/current_blocked/krx_envelope.json \
  --envelope paper_12_4_three_market_regime/fixtures/current_blocked/us_envelope.json \
  --envelope paper_12_4_three_market_regime/fixtures/current_blocked/crypto_envelope.json \
  --evaluation-time-utc 2026-08-31T01:00:00Z \
  --output /tmp/paper12-4-bundle.json

python3 -m paper_12_4_three_market_regime.cli verify \
  --bundle /tmp/paper12-4-bundle.json
```

The `fixtures/current_blocked` payloads are explicitly marked
`TEST_FIXTURE_ONLY_NON_AUTHORITATIVE`. They pin current local canonical paths
and hashes for regression only. They do not convert retained observations into
natural evidence and do not promote any gate.

## Tests

```sh
python3 -m unittest -v \
  paper_12_4_three_market_regime.tests.test_receipt_pipeline
```

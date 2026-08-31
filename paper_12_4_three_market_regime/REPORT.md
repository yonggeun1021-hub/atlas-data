# Atlas PAPER 12-4 — Three-market Regime receipt report

- Report date: 2026-08-31
- Local base: `origin/main` at `2911e99`
- Branch: `feat/paper-12-4-three-market-regime-20260831`
- Scope: `paper_12_4_three_market_regime/` only
- Network, broker, credential, GET, OAuth, POST, order, cancel: 0
- REAL/live/real-capital/Production/Trading: false

## Outcome

The isolated boundary now accepts a separate leadership, sector-flow, and
five-axis envelope for each market; validates completed bars, source time,
TTL, policy status, coverage policy, coverage count, fixture SHA-256, and
nested local canonical-source SHA-256; then emits independent market receipts,
the three-market header, rotation readiness, exact lineage hashes, and a
four-entry transition ledger.

The current facts were preserved. No market was promoted to `PASS` or
`NO_ACTION`, and no receipt has PAPER authority.

| Market | Local canonical bytes connected by the fixture envelope | Receipt | Regime / disposition | Rotation input | Blocking boundary |
| --- | --- | --- | --- | --- | --- |
| KRX | `config/korea_leadership_policy.json` `3b532a…`; `data/latest_korea_market_signals.json` `35b1fa…`; `data/latest_paper_regime_reference.json` `42fe56…` | `WAIT` | `UNKNOWN / HOLD` | `BLOCKED`, declared `PENDING` | Leadership policy remains `RATIFIED` and axis coverage remains `5/5`, but Regime scoring is `UNRATIFIED`; Korea rotation remains `PENDING`. |
| US | `config/us_leadership_policy.json` `268ce2…`; retained free-market bytes `c3a86a…` | `WAIT` | `UNKNOWN / HOLD` | `BLOCKED`, declared `DEGRADED` | `us_leadership/draft-v1` is `UNRATIFIED`; leadership/sector-flow/axis envelope coverage is retained as `0/5`; scoring is `UNRATIFIED`. |
| CRYPTO | `config/crypto_leadership_policy.json` `d37a73…`; no natural group-flow or axis source is claimed | `WAIT` | `UNKNOWN / HOLD` | `BLOCKED`, declared `DEGRADED` | `crypto_leadership/v1` remains `RATIFIED`, group coverage remains `UNRATIFIED`, coverage remains `0/5`, and scoring is `UNRATIFIED`. |

Deterministic blocked-fixture receipts at `2026-08-31T01:00:00Z`:

- KRX receipt: `22b98ecca616ff356e806aa8c4df19323177d3cf3f6c614cb6ed73c03c5562e9`
- US receipt: `5853bd4b4b90afc0c1436126f1a75436717931c842e78cb5466cfd3b9b9fca80`
- CRYPTO receipt: `e72ec4ddc65ee1d403e1b8f526a82ad065a052a5a78ebdb2e991e60bb14ce371`
- Three-market header: `853a9669898c59202cca0b67c08cb801e61683e8efbdc75cacad962fa0046a31`
- Bundle: `45803dd5f1af0b88ca7d9eec2d5b517cecbfdfb2cc728236f3a09f6983ce813b`

Header result is `PENDING`; rotation discovery is `DEGRADED`. A missing market
is synthesized only as that market's `WAIT / UNKNOWN / HOLD` header row. It
does not remove or rewrite receipts for the other two markets.

## Validation

Command:

```sh
python3 -m unittest -v paper_12_4_three_market_regime.tests.test_receipt_pipeline
```

Result: 12 isolated tests passed. The suite covers current-state preservation,
unratified policy and coverage fail-closed behavior, completed-bar/source-time/
TTL blockers, exact source and nested canonical hash rejection, market-loss
isolation, receipt/header/bundle/ledger hashes, self-rehashed derivation tamper,
policy/coverage status-to-source binding, prior-state transition lineage,
fixture non-authority, and offline CLI
round-trip. In addition, 79 existing decision-authority, three-market-header,
Korea-rotation, and Crypto-rotation regressions passed unchanged.

## Authority and ownership boundary

Only the new isolated package is changed. Existing Crypto runtime/PR/timer,
KRX/US active-owner files, Portal, public contracts, schedules, and data
pointers are untouched. The local canonical inputs above are read and pinned
by hash; they are not rewritten. The fixture layer is regression evidence, not
natural operational evidence.

Live Notion and open-PR state were not queried because the task explicitly
requires Network 0. Therefore this implementation does not claim a WBS Exit
Gate or canonical status change.

## Next Gate

1. The existing policy owners ratify and publish exact-hash scoring/freshness
   semantics; KRX must also publish a non-`PENDING` Korea-rotation input.
2. US must ratify leadership and coverage policy and retain completed-bar 5/5
   inputs. Crypto must ratify group coverage and retain natural group-flow and
   five-axis inputs.
3. The accountable integration owner runs this CLI against those natural
   retained envelopes and reviews the receipt/header hashes. Even then,
   classification and PAPER authority remain separate externally owned gates;
   this package cannot grant them.

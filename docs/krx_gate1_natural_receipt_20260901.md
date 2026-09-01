# KRX Gate 1 official-session and natural completed-bar receipt

Status: mechanism implemented; retained natural input remains incomplete.
Runtime remains `UNKNOWN/HOLD`, writer invocation and ledger mutation remain 0.

## Pre-implementation audit

The audit was performed before adding this lane. The canonical decision is
`CIO_GATE1_TTL_SLA_OPTION_B_20260901 / APPROVED_OPTION_B`: numeric TTL is
`null`, repository default is `ABSENT`, provider SLA is `UNKNOWN`, governance
is `OBSERVATION_REQUIRED`, and runtime is `UNKNOWN/HOLD`.

| Requirement | Existing exact evidence | Missing before this change | Disposition |
| --- | --- | --- | --- |
| KRX regular-session completed-bar rules | public PR #488 merge `37e07659aa934e9a6e09b27b786dc49203060af1`; `market_data/krx_session_bars.py` SHA-256 `79e0058a6ed4540b953e9bbb975296a58fcbe6b0f245a299fae65bec5176dbd0` | no producer/receipt binding natural files to the validator | reuse exact validator; no wrapper semantics |
| Current natural KRX market judgement | public PR #524 merge `19fefca7dd26be1984c5b4b5cc4256b5e2cb27dc`; producer SHA-256 `41e1428949ffd48a7dcb839502bddf4b562791dc51f52ff601d22be1459a22a2` | Gate 1 receipt sees only the daily observation; no official date-specific calendar or 15m/1h series | exact-pin the producer and remain independent of scoring |
| Official date-specific open session | existing contract requires KIS `CTCA0903R` plus KRX market rules | no retained 2026-09-01 response bytes/ref/SHA receipt | accept only an exact-hash retained source envelope; current status `UNKNOWN` |
| Natural completed 15m/1h | existing validator defines 26 completed 15m intervals and 6 full 1h intervals | no retained normalized minute packet; KIS minute timestamp semantics unratified | accept only exact 390 normalized interval starts plus a separate normalization receipt |
| Freshness/TTL/SLA | P9-01 repository default `ABSENT`; Option B receipt | no numeric TTL and no official provider SLA | preserve `null/ABSENT/UNKNOWN`; never manufacture `FRESH` |
| 1d bar | current natural judgement has a completed daily observation | it is not the requested official-calendar + normalized-intraday receipt | record `UNCONFIRMED_NOT_PROMOTED`; accepted count 0 |
| Downstream writer | private PR #143 head `12cfbeb9bc703dd32f84b8014de61d30237f7105` | current producer output is `UNKNOWN/HOLD/action=null` | writer invocation 0, ledger mutation 0 |

## Repository and active-owner evidence

Audit base was public main `2ec4bf86a09ccfa77a80e272f791ef01881cc6c5`
and private main `be4088bef4fc71c9a69bb9b590b48eeba41a831b`.
Relevant public merged history was #523 `7d3a991d`, #524 `19fefca7`,
#525 `6b749d4e`, #526 `00e9cf64`, #527 `0abd349d`, and #522
`2ec4bf86`. Public active files were #528's three new
`regime_source_owner_registry_v2` paths and draft #475's four Upbit
microstructure paths. Neither intersects this lane.

Private active owners and exact local/PR heads were:

- #139 `93fc0cfb772730bb28259fc5dd55654472c75ddb`: US writer resume paths;
- #140 `db86cf4953dfbd2bd5ebf4bc4aee3007604f9274`: weight/quantity bridge paths;
- #142 `cd5db716efb5036301e8c8f621feb2cc561615a5`: three-market natural lifecycle paths;
- #143 `12cfbeb9bc703dd32f84b8014de61d30237f7105`: KRX producer-to-writer handoff paths;
- #144 `ad845b37af99752d8ad38f315b06307578505e93`: US natural PAPER mutation paths.

Local public active heads also included Gate 4 `d774cd132fff4430d801f47fb469371b049f69c6`
and Gate 2 source-owner registry `78122714f17212789a9a4c404f88ca8a749a9e32`.
The proposed files below intersect none of those file lists. The P9-01 WBS
row explicitly classified the KRX natural completed-bar follow-up as not
active WIP. Therefore there was no existing owner for the missing official
calendar + natural-intraday receipt producer, and no duplicate WBS row was
created.

This lane changes only:

- `.github/workflows/krx-gate1-natural-receipt.yml`
- `config/krx_gate1_natural_receipt_contract.json`
- `docs/krx_gate1_natural_receipt_20260901.md`
- `evidence/krx_gate1_natural_receipt/2026-09-01/{input_manifest,receipt}.json`
- `krx_gate1_natural_receipt/{__init__,receipt}.py`
- `schemas/krx_gate1_natural_receipt.schema.json`
- `test/krx_gate1_natural_receipt/{__init__,test_receipt}.py`

Intersection with every active file list above is 0. Private PR #143 files,
`decision/paper_decision_bridge.py`, common Gate/writer primitives, and all
existing validator files are unchanged.

## Receipt behavior

The producer is offline and pure with respect to external systems. It accepts
three optional exact-hash bindings:

1. `krx_date_specific_session_source/1`, containing retained KIS
   `CTCA0903R` response identity/SHA and the calendar object accepted by the
   existing validator;
2. a normalized KIS one-minute packet for one asset, using only
   `FHKST03010230` or `FHKST03010200` lineage;
3. `krx_minute_timestamp_normalization_receipt/1`, proving
   `INTERVAL_START_RATIFIED` semantics effective on the session date.

Only when all three bind exactly does it reuse
`aggregate_normalized_minutes()`, `expected_intervals()`, and the existing bar
validator to require exactly 26 completed 15m intervals and 6 full 1h
intervals. The 15:00-15:30 tail is never called a 1h bar. A test fixture can
prove structure only and is emitted as `TEST_ONLY`; it can never become
`PASS` or natural evidence.

The retained 2026-09-01 manifest has all three bindings absent. Its exact
receipt is therefore:

- calendar `UNKNOWN`, official response ref/SHA `null`;
- 15m `UNKNOWN`, accepted 0/26;
- 1h `UNKNOWN`, accepted 0/6;
- 1d `UNCONFIRMED_NOT_PROMOTED`, accepted 0;
- Gate 1 `UNKNOWN` and runtime `UNKNOWN/HOLD`;
- numeric TTL `null`, repository default `ABSENT`, provider SLA `UNKNOWN`;
- writer invocation, ledger mutation, order, and cancel counts all 0.

No broker, network, credential, OAuth, order, cancel, REAL, live, Production,
or Trading capability exists in this producer.

## Next natural observation

The earliest candidate observation is after the next regular-session close,
`2026-09-02 15:30 Asia/Seoul`, conditional on a same-date official
`CTCA0903R` open-session receipt, a complete retained minute packet, and a
valid normalization receipt. A closure, special session, missing minute,
partial bucket, PIT inversion, or absent normalization remains
`UNKNOWN/HOLD/WAIT` with mutation 0. This timestamp is not an SLA and does not
assert that 2026-09-02 is open without the official date-specific receipt.

## Verification

```bash
python3 test/krx_gate1_natural_receipt/test_receipt.py -v
python3 -m krx_gate1_natural_receipt.receipt \
  evidence/krx_gate1_natural_receipt/2026-09-01/input_manifest.json \
  --check-receipt evidence/krx_gate1_natural_receipt/2026-09-01/receipt.json
python3 validation/tests/test_krx_market_data.py
python3 test/krx_market_judgement/test_krx_market_judgement.py
```

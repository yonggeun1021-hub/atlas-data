# KRX PAPER public completed-bar → Shadow → P8-13 pipeline

This lane is a public, side-effect-free readiness boundary. It revalidates and
content-addresses the exact merged contracts from completed-bar PR #488,
execution measurement PR #491, Shadow PR #484, and P8-13 bridge PR #490.

The pipeline requires one natural, same-date identity: an authority-bearing KRX
Universe identity; a CTCA0903R `OPEN_REGULAR` snapshot; complete and fresh 15m,
1h, and 1d bars under a ratified/effective P9-01 Korea policy; ratified KIS
`stck_cntg_hour` interval-start semantics; a GET-only execution-measurement
receipt; an effective-dated strategy/entry/hold-exit/size policy binding; and
effective COMMON_SAFETY/KRX Shadow PASS. Missing, stale, mixed-identity,
cross-date, incomplete, duplicate, or conflicting evidence locks the whole
packet.

The repository intentionally ships no positive policy binding. Existing
policy-proposal files are evidence proposals, not authority. With the current
merged contracts the expected operational result is therefore
`LOCKED_FAIL_CLOSED`, `symbol=NONE`, and `proposal=NONE`.

Only a sanitized readiness/proposal envelope is emitted. Quantity is always 0;
order draft, broker route, and KIS submission are always null; broker/KIS POST
counts are always 0. Internal/private ledger execution is outside this lane.
Same exact identity and proposal replay returns `NO_CHANGE`; duplicate identity
or a conflicting proposal locks rather than overwrites history.

Run the focused regression with:

```bash
python3 validation/tests/test_krx_paper_public_pipeline.py
```

# P3-12-GOV-03 — identity authority audit and minimal freeze

Status: `PENDING_GOVERNANCE_RESOLUTION`

Scope: PAPER-only governance repair; Candidate `NONE`; live engine count `0`

Authority: identity/taxonomy/PAPER-promotion/exchange/order/production/real-capital/trading are all `false`

## Executive finding

PR #474 is directionally correct that merge `69e1cd27d62ea1f2c871d1d91657b05f11a6e699`
did not establish a valid identity source-authority contract. Its original head was a
33-file inverse revert (`+250/-12,111`) that conflicted with current `main`. While this
audit was running, another worker updated it to head `93898f29059fc8df72e6ce57c393fc71c4fbcf03`:
41 files (`+1,270/-12,242`), mergeable, with an added exact-hash revocation layer and
new CI in progress. It still retains the full revert and deletes eight current-main
paths, including bounded identity evidence and its packet. Keep it Draft and unmerged.

The bounded official Upbit listing observation is valid for all 282 audited markets.
It does **not** establish canonical identity. None of the 81 researched identity rows
binds a typed source to a validated authority domain, captured content hash,
`observed_at`, and `available_at`; therefore 0 identities are authority-valid and all
55 mappings installed by #465 are authority-invalid. The minimum safe repair is to
preserve the historical bytes while preventing authoritative consumers from reading
the registry, taxonomy, or blocked PAPER8 lineage until explicit CIO re-ratification.

## Independent chronology

All timestamps below are UTC; KST is UTC+09:00.

| Time | Event |
|---|---|
| 2026-08-30 00:29:01 | PR #465 created. |
| 01:31:15 | PR #465 moved to Ready for review. |
| 02:20:35 | PR #465 converted to Draft. |
| 02:20:37 | CIO comment: `CIO MERGE HOLD — source authority contract와 LIT identity conflict 미해결`. |
| 02:59:51 | Independent review reported 290 focused passes and requested exact-main sync plus Actions before leaving Draft. |
| 03:07:51 | Actions run 33288258087 completed successfully for older head `a72bb763…`. |
| 03:08:57 | PR #465 moved to Ready for review; no explicit post-HOLD CIO approval text was found. |
| 03:10:18 | Exact final-head Actions run 33289647365 started for `c7c3d457…`. |
| 03:10:19 | PR #465 merged as `69e1cd27…`, before exact-head Actions completed. |
| 03:43:54 | Exact final-head Actions completed successfully, after the merge. |
| 04:46:51 | Draft PR #474 created as the inverse revert of #465. |
| 05:14:34 | #474 Actions run 33293250713 failed; the failure includes `DECISION_SOURCE_BYTES_MISMATCH` against the later P3 record. |
| 05:51:14 | A separate worker merged latest main into #474 head `93898f29…` after adding an exact-hash revocation layer; #474 became mergeable and started a new CI run. This audit did not rebase or modify it. |

A Ready-for-review transition is not the explicit approval required to clear the
recorded CIO HOLD. Passing CI after merge proves regression status, not ratification.

## 282-market source-authority audit

The audit separates official exchange listing observation from canonical asset identity.
The raw `GET /v1/market/all?is_details=true` body decompresses to SHA-256
`b778a7021128fb9e3e52c1535b47b2096814be08fc6a2cbc61ccf702095f439a`,
captured at `2026-08-29T00:52:31Z`. All 282 bounded markets exist in that body;
all 81 researched rows match its Korean/English names, response hash, and availability time.

| Deterministic authority class | Count | Meaning |
|---|---:|---|
| first-party candidate, unbound | 23 | At least one plausible project-controlled domain, but no typed/validated/hash-and-time-bound citation. |
| third-party only | 32 | Verified candidates rely only on aggregators, explorers, exchanges, news, release distributors, or source hosts. |
| ambiguous | 25 | Researched collision/missing-source HOLD other than KRW-LIT. |
| conflict | 1 | KRW-LIT content contradiction between official listing and taxonomy narrative. |
| unresolved / official listing only | 201 | Official Upbit market observation exists; no bounded canonical-identity research row exists. |
| **Total** | **282** | Every bounded market is classified exactly once. |

Authority-valid identity count is **0**. Researched-but-untrusted count is **81**;
official-listing-only unresolved count is **201**. The current registry contains **55**
authority-invalid mappings; 26 researched rows remained HOLD. These counts are source
authority findings, not assertions that every asset name is semantically wrong.

### Collision audit

The deterministic tests assert that all 55 registry markets equal the 55 candidate
markets, all 26 HOLD markets are absent, canonical targets are unique, and all
researched plus official-listing-only markets sum to 282. This prevents a one-off
KRW-LIT patch from hiding another symbol alias.

KRW-LIT specifically is **Lighter** in the official Upbit response and was correctly
`HOLD_TICKER_COLLISION`; it never entered the 55 mappings or PAPER8. The ratified
taxonomy nevertheless describes that same row as the old Litentry token preceding
HEI migration while also quoting `english_name=Lighter`. That taxonomy record is a
content conflict. It is a lineage-integrity issue, not a live-trading safety incident.

## Consumer freeze

The retained historical P3 PAPER8 record is blocked by exact payload hash:

`a9be9c63f9a39d1afbfd282a5707e797a7db61138edc9538b7ccf4a6a43d2d12`

Its inner packet payload hash is:

`3ba2721dec6ff574b0e1652fd4d8712259d17797aa2f60f8ba022020ff702c3f`

The eight preserved historical markets are BTC, ETH, LINK, SHIB, SOL, SUI, WLD,
and XRP. The freeze does not delete the P3 record, the successful P4 capture, the
SOL/WLD `UNKNOWN` results, or any P9/P10 natural evidence. Instead:

- the shipped registry and taxonomy status becomes `PENDING_GOVERNANCE_RESOLUTION`;
- universe classification retains observations but exposes no effective identity mapping;
- promotion requires currently ratified identity and fails closed otherwise;
- the P3→P4 bridge verifies the exact governance file hash before any provider call,
  rejects the blocked record, and requires `RATIFIED_BY_EXPLICIT_CIO_DECISION` to release;
- decision snapshots surface governance-pending freshness and force tradeable count to zero;
- every authority flag remains false.

Release requires typed sources, validated authority domains, captured content hashes,
both observation/availability timestamps, structural collision checks, a corrected
taxonomy, exact registry/taxonomy/consumer hashes, focused/related/full green CI, and
an explicit CIO comment naming those hashes. Current taxonomy must not be used to
retroactively reclassify historical evidence.

## Recovery option comparison

| Option | Concrete diff / conflicts | Lineage effect | Rollback cost | Finding |
|---|---|---|---|---|
| Full revert (#474) | Original `d2ed71a…`: 33 files, `+250/-12,111`, 1 conflict, failed Actions. Current `93898f2…`: 41 files, `+1,270/-12,242`, mergeable, 8 deleted paths, added exact-hash quarantine, new CI pending. | The newer quarantine protects some consumers, but the retained full revert still deletes bounded source evidence/packet and rewrites the committed P3 record instead of preserving history. | High: reconstruct removed evidence and continue maintaining both revert and revocation layers. | Reject. Keep Draft, then close as superseded only after approved replacement merge. |
| Minimal authority freeze (this change) | 21 files including tests and this packet; no historical observation/evidence deletion; based directly on current main. | Blocks one exact PAPER8 record and all pending identity authority while preserving later code/evidence. | Low: a later explicit, exact-hash re-ratification changes the release contract without reconstructing history. | **Recommend.** |
| Surgical invalidation + hardened re-ratification | Freeze first, then a separate evidence PR correcting 55 mappings and all 26 holds; exact diff cannot be independently verified because claimed local commit `47de85e` is not present in the remote or any inspected local repository. | Can restore authority only prospectively after full evidence binding; historical bytes remain historical. | Medium and presently unbounded until the candidate object is recoverable or rebuilt. | Phase 2 only; separate Draft, never bundled into emergency freeze. |

## CIO decision packet

Recommended sequence:

1. Approve the minimal authority freeze at its exact PR head and governance contract hash.
2. Merge only after focused, related, and full CI are green; do not merge #474.
3. Close #474 as superseded after the freeze lands.
4. Prepare a separate hardened-evidence Draft; do not release it without exact-hash CIO re-ratification.

Exact requested decision:

> CIO APPROVES P3-12-GOV-03 MINIMAL AUTHORITY FREEZE at the exact Draft PR head and governance contract SHA identified in the approval comment. Keep PR #474 unmerged and close it as superseded only after this freeze is merged. Do not release P3 record `a9be9c63f9a39d1afbfd282a5707e797a7db61138edc9538b7ccf4a6a43d2d12`; authorize only a separate hardened-evidence Draft. This grants no exchange, order, PAPER-exit, production, real-capital, or trading authority.

## Sources

- [PR #465](https://github.com/yonggeun1021-hub/atlas-data/pull/465) and merge `69e1cd27d62ea1f2c871d1d91657b05f11a6e699`
- [PR #474](https://github.com/yonggeun1021-hub/atlas-data/pull/474) and Actions run [33293250713](https://github.com/yonggeun1021-hub/atlas-data/actions/runs/33293250713)
- P3-12 Notion WBS, live policy-ratification record, Execution Cockpit, and Master Execution Map, fetched independently on 2026-08-30 before synchronization
- Repository raw snapshot, bounded identity packet, registry, taxonomy, P3→P4 bridge contract, and committed natural evidence named above

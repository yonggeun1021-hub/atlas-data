# security_sector_membership/1 — KR security ↔ sector membership (TKT-3, W2)

Maps each KIS KOSPI/KOSDAQ common stock to at most one of the 46 ratified P2-03
rotation sector series, using the `theme_id` of
`config/korea_rotation_sector_identity_binding_document.json` as `membership_id`.
This is what lets a rotation "strong sector" reach individual stocks
(T2 condition C5).

> Membership is evidence, not approval. It grants no candidate, PAPER order,
> REAL order, capital or production authority.

## Source policy (CIO decision CIO-TKT3-MEMBERSHIP-D1-D5-20260914)

| Decision | Rule |
|---|---|
| D1 source | `KIS_ONLY`, `source_count = 1`, `verification = SINGLE_SOURCE_KIS`. Satisfies T2 (PAPER) C5 only; T3/REAL keeps `BOTH_MUST_AGREE`. KRX `MDCSTAT03901` needs a login and is not used; no login, cookie reuse, credentials or bypass. |
| D1 change | On every KIS master publication, a stock whose assignment changed is `PENDING_CHANGE` (not a member) for one publication, then `ACTIVE`. |
| D2 alias | Exactly two ratified aliases keyed by (market, code): KOSPI `0013` and KOSDAQ `1028`, source name `전기·전자` → `전기전자`. No punctuation normalization; any other name mismatch is `UNMAPPED`. |
| D3 parents | `ACTIVE` uses the deepest non-zero KIS level only (≤ 1 per asset). `KOSPI::제조`, `KOSDAQ::제조`, `KOSPI::금융` are a derived `parent_view` that never counts as `ACTIVE`. C5 passes when rotation selects the stock's `ACTIVE` series, or a parent whose child is that series. |
| D4 ids | `membership_id` = binding `theme_id` (`KOSPI.SECTOR.nn` / `KOSDAQ.SECTOR.nn`); `path_a_crosswalk` links the Path A `THEME.KR.*` id without renaming it. |
| D5 no code | Stocks with no KIS sector code stay `UNMAPPED`; nothing is inferred. |

## Inputs

- KIS `kospi_code.mst.zip` / `kosdaq_code.mst.zip`, read with `universe/krx_investable_registry.py` (`_read_master`, product classifier `_product`; population = `COMMON_STOCK`).
- KIS `idxcode.mst.zip` (45-byte lines: group, 4-digit code, cp949 name).
- The contract `config/security_sector_membership_contract.json`: the 46-row (market, code) → series table, the two aliases, `parent_view`, crosswalk, pinned binding hash, all-false authority.
- `as_of_session` (the KR session the master reflects, supplied by the caller) and the previous document, if any.

## Classification (per common stock)

1. `sector_small` non-zero → `UNMAPPED` `SECTOR_SMALL_LEVEL_UNDECLARED`.
2. No large and no medium code → `UNMAPPED` `NO_SECTOR_CODE`.
3. Medium code present: the large code must be a declared parent listing that child, else `UNMAPPED` `HIERARCHY_UNDECLARED`; leaf = medium.
4. Otherwise leaf = large.
5. Leaf not in the table → `SECTOR_CODE_NOT_IN_TABLE`; idxcode name ≠ the table's `source_name` (leaf or its parent) → `SECTOR_CODE_NAME_CHANGED`.
6. Else `ACTIVE` on the table's `membership_id`, with `parent_membership_id` when the leaf is a declared child.

## Document and time semantics

Each document holds the full row history (`rows`). A row's `effective_from` is the
`observed_at_utc` (latest source retrieval time) of the publication that first
recorded it — never a listing date or an earlier master. A later publication may
only close an open row at its own `observed_at_utc` and append rows; this is
checked by `validate_successor` (no dropped, rewritten or backdated rows).

`members(document, membership_id, t)` returns `ACTIVE` assets with
`effective_from <= t < effective_to`. `c5_rotation_membership(document, asset_id,
selected_membership_ids, t)` returns PASS/FAIL with the reason, `via_parent`, and
`t3_two_source_verified = false`.

`CONFLICT` is reserved for a future second source and is never produced under `KIS_ONLY`.

## Distribution boundary

- Private (`atlas-private-evidence`): the per-stock document and its manifest.
- Public (this repo): contract, code, tests, and `build_public_summary` output —
  per-market coverage, per-`membership_id` counts and hashes only, no asset identifiers.
- Raw KIS master bytes are not committed to this repo.

## CLI

```
python3 universe/security_sector_membership.py --input input.json \
  --output-document private/document.json --output-summary summary.json
```

## Tests

```
python3 test/test_security_sector_membership.py
```

Synthetic fixed-width masters and temp output only; no network.

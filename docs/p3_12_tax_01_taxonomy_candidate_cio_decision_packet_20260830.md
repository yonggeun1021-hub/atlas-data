# P3-12-TAX-01 -- Upbit Taxonomy Schema & Eligible Content -- CIO decision packet -- 2026-08-30

Status: **REVIEW MATERIAL, DRAFT ONLY**. `config/upbit_exclusion_taxonomy.json`'s
content has been drafted with new records in this branch, but its
`approval_status` is unchanged (`PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY`). This
PR is a **draft PR** -- it is not merged, and it is not marked ready for
merge. No ratified identity registry or policy ratification is bundled
with this change.

Evidence packet: `data/observations/upbit_taxonomy_schema_eligible_candidate/2026-08-29/packet.json`
(`payload_sha256` recorded inside the file). Builder source:
`universe/upbit_taxonomy_schema_eligible_candidate.py` +
`.github/scripts/upbit_taxonomy_schema_eligible_candidate_build.py`.
Contract: `docs/upbit_taxonomy_schema_eligible_candidate_contract.md`. This
work directly follows up on the CIO-approved P3-12 Shadow Validation
Harness (`docs/p3_12_shadow_validation_cio_decision_packet_20260829.md`,
merged via PR #459).

## 1. What changed in `config/upbit_exclusion_taxonomy.json`

| Field | Before | After |
|---|---|---|
| `approval_status` | `PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY` | **unchanged** -- `PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY` |
| `excluded_categories` | `stablecoin, wrapped, leveraged, derivative_like, unverified_identity` | + **`commodity_linked`** |
| `records` | 6 (all `stablecoin`) | 6 unchanged + **75 new** = 81 |

New records by category: **72 `eligible_crypto`, 1 `stablecoin` (USDG), 1
`unverified_identity` (RE), 1 `commodity_linked` (XAUT)**.

Every new record's `effective_from` is `2026-08-29` (today's snapshot
date), `effective_to` is `null`, and its `reason` text cites both the exact
Upbit market confirmation (market code, `english_name`, capture timestamp)
and the exact Kraken RATIFIED record it was corroborated against (verbatim
reason text), matching the existing 6 records' own evidentiary style.

## 2. The one rule behind every new record

A market's candidate canonical asset id got a new record **if and only if**
`config/crypto_breadth_exclusion_taxonomy.json` (RATIFIED, loaded through
the shared fail-closed contract `identity/candidate_identity_gap_inventory.py::_load_taxonomy()`)
already carries a record for that exact id, *effective-dated active as of
2026-08-29*. The drafted category is exactly that Kraken record's category.
**No record was ever drafted from a name/symbol pattern alone.**

## 3. Explicit CIO-directed classifications

| Market | Canonical id | Outcome | Basis |
|---|---|---|---|
| `KRW-XAUT` ("Tether Gold") | XAUT | **Drafted as `commodity_linked`** (new category added to schema) | Kraken RATIFIED: `commodity_linked`, "gold-linked token" |
| `KRW-RE` ("Re") | RE | **Drafted as `unverified_identity`, never `eligible_crypto`** -- *and* stays on the identity-layer hold list | Kraken RATIFIED: `unverified_identity`, documents a real ticker collision on the identical symbol |
| `KRW-CHIP` ("USD.AI") | CHIP | **NOT classified as `stablecoin`** despite containing "USD" -- routed to hold, no record drafted | No Kraken record, no official issuer corroboration found |
| `KRW-USDG` ("Global Dollar") | USDG | **Drafted as `stablecoin`** | Kraken RATIFIED: `stablecoin`, "USD-pegged stablecoin" |
| 200 other Upbit-only assets | (201 total incl. CHIP) | **Held, no record** | No independent (Kraken or otherwise) corroboration exists for these canonical ids at all |

`leveraged`/`derivative_like` remain reserved excluded categories with
**zero drafted records** -- no ratified criteria exist anywhere in this
repository for either, and nothing in today's snapshot has independent
corroboration for them.

`schema_gaps`: **0** -- no Kraken-corroborated category (beyond the newly
added `commodity_linked`) lacks an Upbit equivalent in today's real data
(no `fiat`/`staked` matches were found).

## 4. Full hold list (202 items)

| Reason | Count |
|---|---|
| `NO_INDEPENDENT_CORROBORATION_UPBIT_ONLY` | 200 |
| `NO_INDEPENDENT_STABLECOIN_ISSUER_CORROBORATION` (`KRW-CHIP` only) | 1 |
| `IDENTITY_TICKER_COLLISION_PRECEDENT_KRAKEN_UNVERIFIED_IDENTITY` (`KRW-RE`, in addition to its drafted record) | 1 |

The full per-market list is in the evidence packet's `hold_list` field --
not reproduced here in full (200+ rows), but every row carries its own
`market`/`candidate_canonical_asset_id`/`reason`.

## 5. Shadow-recomputed funnel (P3-12 harness re-run against this draft)

Re-running `universe/upbit_shadow_validation_harness.py` (same 2026-08-29
snapshot, unchanged policy/identity logic) against this branch's new
`config/upbit_exclusion_taxonomy.json` content:

| Scenario | market_count | OBSERVATION_POOL | TRADEABLE_UNIVERSE | PAPER_ELIGIBLE |
|---|---|---|---|---|
| Before (production today, pre-this-PR) | 282 | 282 | 0 | 0 |
| **After -- ratify policy + this draft taxonomy + identity exactly as written** | 282 | 253 | 1 | **28** |
| Supplemental hypothetical (add Kraken-corroborated `eligible_crypto` records not already present) | 282 | 253 | 1 | 28 (**0 additional records to add** -- see below) |

**This exactly matches PR #459's prior supplemental estimate of ~28
`PAPER_ELIGIBLE`, with zero difference to explain.** That estimate was
computed by hypothetically adding the same 72 `eligible_crypto` records
this PR now drafts for real; since the underlying market snapshot
(2026-08-29) is unchanged and the classification rule is identical, the
funnel is numerically identical. The supplemental scenario's
`hypothetical_records_added` correctly dropped from 72 to **0**, because
every one of those 72 ids is now already present in the real taxonomy --
the supplemental scenario has nothing left to hypothesize.

Reason distribution for the 282 markets under this draft (ratified as
written):

| Reason | Count |
|---|---|
| `TAXONOMY_UNKNOWN` (the 201 held, uncorroborated assets minus 1 -- see note) | 194 |
| `SPREAD_ABOVE_THRESHOLD` | 33 |
| `PAPER_ELIGIBLE_ALL_GATES_PASSED` | 28 |
| `INVESTMENT_WARNING_ACTIVE` | 8 |
| `LISTING_HISTORY_BELOW_THRESHOLD` | 7 |
| `TAXONOMY_EXCLUDED:stablecoin` | 7 (6 original + USDG) |
| `TURNOVER_BELOW_THRESHOLD` | 2 |
| `TAXONOMY_EXCLUDED:unverified_identity` | 1 (RE) |
| `TAXONOMY_EXCLUDED:commodity_linked` | 1 (XAUT) |
| `SLIPPAGE_ABOVE_THRESHOLD` | 1 |

(Note on the arithmetic: the 202-item hold list includes `KRW-RE`, which
*does* get a drafted record (`unverified_identity`) and so is correctly
reported as `TAXONOMY_EXCLUDED:unverified_identity`, not `TAXONOMY_UNKNOWN`
-- leaving 201 markets that would otherwise show `TAXONOMY_UNKNOWN` (200
Upbit-only + `KRW-CHIP`). Of those 201, 7 are also under Upbit's own
investment warning and are force-excluded by that gate *before* ever
reaching the taxonomy gate (each market's `reason` is its first-failing
gate only, never double-counted), leaving 194 actually reported as
`TAXONOMY_UNKNOWN`: 201 - 7 = 194. The 8th `INVESTMENT_WARNING_ACTIVE`
market, `KRW-BONK`, is unrelated to the hold list -- it has a newly drafted
`eligible_crypto` record but is separately excluded by the same
investment-warning gate.)

Slippage-by-order-size detail for the 29 markets reaching
`TRADEABLE_UNIVERSE`/`PAPER_ELIGIBLE` is in the shadow packet's
`funnel_supplemental_hypothetical.slippage_curve_sample` (unchanged
mechanism from PR #459, reused as-is).

## 6. Items still needing CIO research (not resolved by this PR)

* **`KRW-CHIP` ("USD.AI")** -- needs a human check of its official issuer
  documentation to determine if it is genuinely a 1:1 USD-pegged instrument
  or a differently-structured (e.g. yield/compute-collateral) token merely
  branded with "USD".
* **`KRW-RE`** -- taxonomy-excluded and safe, but the identity-layer
  question (is Upbit's "Re" the same project Kraken's collision note
  describes, or a distinct, legitimately-identified project?) remains open
  and is tracked in the shadow harness's `identity_review.manual_review_queue`,
  not resolved here.
* **200 Upbit-only assets with no independent corroboration** -- each would
  need either an official project/issuer source pair or a broadened
  cross-reference registry before any could be considered for
  `eligible_crypto`. This PR does not attempt that research; it only
  refuses to guess.
* **`leveraged`/`derivative_like` category definitions** -- still
  undefined anywhere in this repository. A future CIO decision to define
  criteria for either category is a prerequisite before any asset could
  ever be classified into them.

## 7. Safety boundary confirmation

* `approval_status` was not changed anywhere in this PR.
* This PR is a **draft PR** -- not merged, not marked ready for merge.
* No identity registry or policy ratification is bundled with this change
  -- only taxonomy content was drafted.
* No classification criterion was invented: every new record's category is
  copied 1:1 from an independently RATIFIED registry already in this
  repository; every hold is a refusal to guess, not a new rule.
* No Upbit order/withdrawal/private endpoint was called.
* Every `authority` field in the evidence packet is hardcoded `false`
  except `review_only`.
* KIS/Portal work areas were not touched.
* This is data-infrastructure completion, not proof of alpha -- Candidate
  NONE and all order authority remain unchanged and `false`.

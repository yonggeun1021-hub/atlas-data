# Portfolio Risk Input Contract

Status: merged (PR #215/#216). WBS: P5-06 / P7-08 (`🟡 개발중`, no new row).

**2026-08-23 cutover -- exit gate satisfied, live capture moved to a private repo.** A successful private-side live proof happened (see `yonggeun1021-hub/atlas-private-evidence`, run `32642860831`, evidence commit `e92ba2d`). `ALPACA_API_KEY`/`ALPACA_API_SECRET` have been **removed from this repo's GitHub secrets entirely** and now live only in the private repo. `.github/workflows/portfolio-risk-input.yml` (the public verify-only workflow) has been **decommissioned** -- there is no live-capture-capable workflow left in this repo at all. `portfolio_risk/` itself is UNCHANGED and stays here: `atlas-private-evidence` pulls and executes an explicitly pinned, approved commit of this exact code (see its `config/approved_public_commit.json`) rather than forking or duplicating it. This repo's own `test/test_portfolio_risk_input.py` continues to serve as the authoritative regression for that pinned code.

## Purpose

This is **not** "decide how much to buy." It supplies the real, PIT-safe
account facts (NAV, cash, positions, exposure) that a **future** sizing /
policy decision will need. Risk-budget percentages, stop-loss caps,
max-concurrent-Probe counts, and any other policy number are **not**
ratified or implemented by this module. See the CIO's own §8 recommendation
in the P5-06/P7-08 policy comparison packet (Notion, `3c59f2d7-3c84-81c9-a297-ff26fe229c29`,
v2.1) for the origin of this task.

## Physical separation of concerns

Every snapshot this package builds carries four top-level, physically
separate keys -- never merged:

| Key | Meaning | This PR's value |
|---|---|---|
| `portfolio_facts` | Real observed account facts (Alpaca paper account/positions, or explicitly-labeled manual snapshots) | Real data |
| `risk_capacity_inputs` | Inputs a future policy calculation will consume (NAV/cash/exposure breakdowns, completeness/staleness) | Real data |
| `risk_policy` | A ratified risk policy | Always `{"approval_status": "UNRATIFIED"}` |
| `position_size` | A computed position size | Always `{"status": "NOT_COMPUTABLE_POLICY_UNRATIFIED"}` |

## Data sources

- **Alpaca Paper account** (`portfolio_risk/alpaca_client.py`): `GET /v2/account`,
  `GET /v2/positions` against `https://paper-api.alpaca.markets`

### Position provider identity lineage (additive `portfolio_position_source_lineage/1`)

Every Alpaca position now transports the exact provider pair
`(source_name="alpaca_paper_positions", source_asset_id=<the response's
asset_id>)`. The stable provider `asset_id` is read directly at the adapter
boundary; it is never reconstructed from the display symbol. `AVAILABLE`
therefore means only that this exact provider pair is present. It does **not**
mean that a canonical instrument has been resolved, that two aliases have
been deduplicated, or that the position is investable.

Manual Korea/Crypto positions remain
`NOT_COMPUTABLE_SOURCE_IDENTITY_LINEAGE_MISSING`. A caller may supply one
complete provider pair for audit, but it remains unverified; partial pairs are
rejected and manual input can never claim `AVAILABLE`.

This extension deliberately leaves the already-ratified
`config/portfolio_risk_input_contract.json` bytes and
`portfolio_risk_input/1` schema identity unchanged. Its own contract is
`config/portfolio_position_source_lineage_contract.json`, and each lineage
object carries that separate contract version.

The existing `exposure_by_ticker` output remains a raw diagnostic view and is
now explicitly labeled by
`exposure_identity_basis=RAW_PROVIDER_SYMBOL_DIAGNOSTIC_NOT_CANONICAL_INSTRUMENT`.
It must not be used as canonical exposure. In particular, XBT/XXBT or other
provider aliases are not silently stripped or merged here; that requires a
separately ratified canonical-instrument authority record.
  (the trading/account host -- distinct from `https://data.alpaca.markets`,
  the market-data host already used by `collectors/free_market_data.py`).
  Credential pattern reused verbatim: `ALPACA_API_KEY`/`ALPACA_API_SECRET`
  via `os.getenv(...)`, same header pair. No new secrets mechanism.
- **Manual/fixture snapshots** (`portfolio_risk/portfolio_snapshot.build_manual_account_fact`):
  for accounts not connected this way today (Korea, Crypto). Always
  force-labeled `verification_status: PAPER_OR_MANUAL_UNVERIFIED` -- a
  caller cannot disguise a manual entry as `BROKER_VERIFIED` (that raises
  `MANUAL_INPUT_DISGUISED_AS_VERIFIED`).

## Structural (not conventional) order-API safety

`portfolio_risk/alpaca_client.py` can never issue an order-creation /
modification / cancellation call:

1. `PAPER_API_BASE` is a hard-coded module constant, never a parameter.
2. `_get()` is the only function in the module that opens a network
   connection, and it never passes `data=`/`method=` to
   `urllib.request.Request` -- a GET by construction.
3. Every fetch function hits a path from `ALLOWED_PATHS =
   frozenset({"/v2/account", "/v2/positions"})` only, checked **before**
   any network call. `/v2/orders` is not a member and no function in the
   module can reach it.

Proven in `test/test_portfolio_risk_input.py::CounterExample11NoOrderApiCallPossible`.

## FX / currency separation

Every cross-currency total (`connected_scope_nav`, `full_portfolio_nav`,
`total_cash_base_currency`, `gross_exposure_base_currency`,
`net_exposure_base_currency`) is computed only when either (a) every
account is in the same currency, or (b) every non-base-currency amount has
a **fresh** FX rate on file (via the single shared conversion path,
`_convert_amount_to_base`/`_aggregate_base_currency`). A missing or stale
rate never produces a silently-blended estimate -- the field's own
`*_status` becomes `NOT_COMPUTABLE_MISSING_FX_RATE` /
`NOT_COMPUTABLE_STALE_FX_RATE` and the value itself is `null`. Per-currency
raw amounts (`exposure_by_currency`, `cash_by_currency`) are **never**
summed across currency, regardless of whether the base-currency total
succeeded -- they are reported separately in every case. FX provenance
(`rate`/`as_of`/`source`) is always kept as separate fields in
`portfolio_facts.fx_rates`, never blended into a single number.

## Completeness / Account Scope Registry

Account scope is **not** a caller-suppliable parameter -- there is nothing
in `assemble_snapshot()`'s signature a caller could pass to shrink it.
`CANONICAL_ACCOUNT_SCOPE = frozenset({"ALPACA_PAPER_ACCOUNT", "KOREA", "CRYPTO"})`
is a fixed module-level registry. `risk_capacity_inputs.account_scope_label`
always says exactly what population is actually connected
(`US_PAPER_ACCOUNT_SCOPE_ONLY`, `FULL_CANONICAL_ACCOUNT_SCOPE`, or
`PARTIAL_ACCOUNT_SCOPE:<markets>`), and `full_portfolio_nav` /
`full_portfolio_nav_status` is non-null **only** when every canonical
market is present -- an Alpaca-only connection is `connected_scope_nav`
(labeled `US_PAPER_ACCOUNT_SCOPE_ONLY`), never presented as the full
portfolio total. `full_portfolio_nav_status` is
`NOT_COMPUTABLE_MISSING_ACCOUNT_SCOPE` whenever any canonical market is
absent.

## Stale / mismatched data forces the WHOLE risk block NOT_COMPUTABLE

If **any** connected account is stale (`staleness_status: STALE`, i.e. its
`captured_at` is more than 24h before `decision_at`) or has a NAV/positions
reconciliation mismatch (`nav_reconciliation_status: MISMATCH_FLAGGED`),
`risk_capacity_inputs.status` becomes
`NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT` and **every** numeric field in
the block (`connected_scope_nav`, `full_portfolio_nav`,
`total_cash_base_currency`, `gross_exposure_base_currency`,
`net_exposure_base_currency`, `existing_position_count`, and all the
breakdown lists) is `null`/empty -- not merely flagged while a number keeps
getting computed anyway.

## Security: this repo is PUBLIC -- no real financial data reaches it, ever

`yonggeun1021-hub/atlas-data` is a **public** repository. Round 2 of CIO
review found that round 1's fix (stripping the account number out of raw
evidence) was not sufficient: real NAV, cash, buying power, positions/
quantities/market values, unrealized P&L, and even a stable
`account_id_hash` are ALL real financial data that must never be committed
publicly, regardless of how well any one identifying field is scrubbed.
This is a data-classification/storage-location problem, not a
field-scrubbing problem -- see "CIO review round 2" below for the full
fix. Structural summary:

- API keys/secrets are read from environment variables only, never
  hard-coded, and never appear as literal values anywhere in source.
- `portfolio_risk/capture.py` contains **zero filesystem-write code** --
  no `open(..., "wb")`, `os.replace`, `gzip`, `tempfile`, or `Path.write*`
  call exists anywhere in the file. A real snapshot is fetched, built, and
  `validate_snapshot()`-verified entirely **in memory**, then discarded.
  The only thing `capture.run()` ever returns (and `main()` ever prints)
  is the output of `_redact_for_public_repo()` -- an explicit,
  allowlist-only constructor (`PUBLIC_SAFE_CAPTURE_RESULT_KEYS`) that
  includes only status labels, the schema version, `source=ALPACA_PAPER`,
  the all-`False` authority block, timestamps, and an error-class code --
  never a dollar amount, quantity, or NAV figure.
- **2026-08-23 cutover**: `.github/workflows/portfolio-risk-input.yml` --
  the public verify-only workflow described in the rest of this section --
  has been **removed entirely** now that `yonggeun1021-hub/atlas-private-evidence`
  (a private repo) does all real capture via a pull-based, pinned-commit
  execution of this exact code. `ALPACA_API_KEY`/`ALPACA_API_SECRET` no
  longer exist in this repo's secrets at all. The description below of
  that workflow's `contents: read`/no-commit/no-schedule discipline is
  kept for historical record (and because `portfolio_risk/` itself is
  unchanged and still governed by it, wherever it runs).
- Real-account-data persistence now lives in that private repo, append-only
  and normalized-only (no raw broker response body persisted -- see its
  own README and the ratified design doc). The `real_data_persistence_status`
  field this repo's `capture.py` still produces
  (`PRIVATE_STORAGE_REQUIRED_BEFORE_LIVE_PERSISTENCE`) is now historical
  from this repo's point of view: as of the cutover, persistence is no
  longer blocked, just relocated to the private boundary, and this repo's
  copy of `capture.py` is never invoked live any more (it is pulled and
  invoked from the private repo instead).
- `portfolio_snapshot.sanitize_for_raw_evidence()` (recursively strips
  `account_number`/`id`) is kept as a tested utility for any future
  private-storage path, but is **not** what makes the current PR safe --
  the current PR is safe because it writes nothing real anywhere, full
  stop.

## Evidence layout: none in this repo, by design -- moved to the private repo

This repo never held an evidence directory for real captures, and still
doesn't. As of the 2026-08-23 cutover, real append-only evidence lives in
`yonggeun1021-hub/atlas-private-evidence` (`evidence/<day>/<sha16>.json` +
`data/latest_pointer.json`, normalized packet only, no raw broker response
body -- see that repo's README). `evidence/operational/portfolio_risk_input/`
in this repo remains an empty placeholder directory and is not used.

## CIO review round 1 (2026-08-23) -- 6 defects fixed

An independent CIO code review of the first version of this PR found 4 P0
and 2 P1 defects, all fixed in the same PR (see
`test/test_portfolio_risk_input.py` for the regression proving each):

| # | Defect | Fix | Regression |
|---|---|---|---|
| P0-1/2 | Raw Alpaca response (incl. real `account_number`) committed verbatim, gzip is not encryption | `sanitize_for_raw_evidence()` strips forbidden keys recursively, applied before every compression | `CounterExampleAccountNumberNeverInRawEvidence` (decompresses the actual stored gzip and scans it) |
| P0-2 | Evidence not actually append-only -- same-day runs overwrote each other | Content-addressed filenames (`<name>-<sha16>.json[.gz]`); identical content = no-op, different content = new file, colliding-but-different content = hard failure | `EvidencePublishTests::test_rerun_with_identical_snapshot_is_a_byte_identical_noop`, `::test_two_genuinely_different_snapshots_same_day_both_preserved`, `::test_collision_with_genuinely_different_content_at_same_path_hard_fails` |
| P0-3 | `total_cash`/`gross_exposure`/`net_exposure` summed raw amounts across currency | Only explicit `*_base_currency` fields are cross-currency (FX-safe, same missing/stale-rate rule as NAV); raw per-currency breakdowns never blended | `CounterExample04MixedCurrencyNoFx::test_missing_fx_rate_blocks_base_currency_totals_but_never_blends_raw` |
| P0-4 | `total_nav_status=OK` reachable even when stale/mismatched (numbers kept computing) | ANY stale account or NAV mismatch forces the WHOLE `risk_capacity_inputs` block to `NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT` -- every field `null`/empty | `CounterExample02StaleBalance`, `CounterExample08NavPositionMismatch` |
| P1-5 | Caller-supplied `expected_sources` could arbitrarily shrink account scope | `expected_sources` parameter removed entirely; scope is the fixed `CANONICAL_ACCOUNT_SCOPE` registry | `CounterExample09PartialMarketMissing::test_assemble_snapshot_has_no_expected_sources_parameter_to_shrink` |
| P1-6 | An Alpaca-only NAV could read as a complete total | `account_scope_label` (`US_PAPER_ACCOUNT_SCOPE_ONLY` etc.) + separate `full_portfolio_nav`/`full_portfolio_nav_status` (`NOT_COMPUTABLE_MISSING_ACCOUNT_SCOPE` unless full canonical scope connected) | `CounterExample09PartialMarketMissing::test_alpaca_only_never_presented_as_full_portfolio` |
| P0-7/8 | `validate_snapshot()` only re-hashed -- a re-signed tamper (value changed + hash regenerated) passed | Independently RE-DERIVES `risk_capacity_inputs` from `portfolio_facts` via the same `_compute_risk_capacity_inputs()` used to build it, and compares field-by-field | `CounterExampleReSignedSemanticTamperRejected` (3 tests) |

## CIO review round 2 (2026-08-23) -- public-repo data exposure + 4 PIT defects

Round 1's fix closed the account-number leak but not the underlying
problem: this repo is public, and round 1 still committed real NAV/cash/
positions/P&L (sanitized-but-real) evidence. Round 2 also directly
reproduced 4 real PIT timing defects on round 1's code. All fixed together:

| # | Defect | Fix | Regression |
|---|---|---|---|
| P0 (repo exposure) | Real Alpaca account data (NAV/cash/positions/P&L/`account_id_hash`) committed to a **public** repo -- sanitizing the account number alone doesn't fix this | `capture.py` never writes any file; a real snapshot is built/verified in memory then discarded; only `_redact_for_public_repo()`'s explicit allowlist is ever returned/printed; workflow loses `contents: write`, its commit/push step, and its schedule | `PublicRepoNeverReceivesRealFinancialData` (6 tests: no filesystem-write capability in source, redacted keys subset of the allowlist, no real value/key leaks, failure path doesn't leak via exception text, end-to-end `run()` proof, workflow YAML has no write permission/commit step/schedule) |
| PIT-1 | A future-dated account `captured_at` silently passed as `FRESH` against a past `decision_at` (negative staleness age isn't `> 24h`) | `_enforce_pit_timing()` explicitly rejects any `captured_at > decision_at` BEFORE staleness is ever computed, in both `build_alpaca_paper_account_fact()` and `build_manual_account_fact()` | `CIORound2PitReproduction01FutureSnapshotPassedAsFresh` (2 tests) |
| PIT-2 | `available_at > decision_at` had no check at all | `_validate_snapshot_timing()` now explicitly rejects it (`AVAILABLE_AFTER_DECISION_REJECTED`) | `CIORound2PitReproduction02AvailableAfterDecisionPassedValidation` (2 tests) |
| PIT-3 | A future-dated FX `as_of` had the same silent-FRESH bug as PIT-1 | `assemble_fx_rates()` calls the same `_enforce_pit_timing()` per pair | `CIORound2PitReproduction03FutureFxPassedAsFresh` |
| PIT-4 | Manual/unverified account data reached `status: COMPUTABLE` and `full_portfolio_nav_status: OK` exactly like fully broker-verified data, even with `FULL_CANONICAL_ACCOUNT_SCOPE` | Any unverified source anywhere forces `status: DIAGNOSTIC_UNVERIFIED_ACCOUNT_SOURCE_PRESENT` (never `COMPUTABLE`); `full_portfolio_nav` stays `null`/`NOT_COMPUTABLE_UNVERIFIED_ACCOUNT_SOURCE` unconditionally; every other computed status downgrades from `OK` to `DIAGNOSTIC_UNVERIFIED` | `CIORound2PitReproduction04ManualInputReachedFullCanonicalComputable` |

Exit-gate tracking item renamed from "awaiting `workflow_dispatch`
registration" to **`PRIVATE_STORAGE_REQUIRED_BEFORE_LIVE_PERSISTENCE`** --
the blocker is no longer a GitHub mechanics issue, it's a real
data-exposure boundary that must be designed (a private evidence store)
before any live persistence happens.

## CIO review round 3 (2026-08-23) -- 2 validator-side PIT bypasses closed

Round 2 locked the BUILDER's PIT checks down, but `validate_snapshot()`
itself had two gaps of the same shape as round 2's original "hash-only"
defect, one level deeper: it never called `_validate_snapshot_timing()`
at all, and it trusted each account fact's own embedded
`staleness_status`/`nav_reconciliation_status`/
`nav_reconciliation_mismatch_pct`/`position_count` instead of recomputing
them -- so `_compute_risk_capacity_inputs()`'s "independent" re-derivation
just re-read the same tampered values and trivially agreed with them.

Fixed via one shared, single-implementation pattern reused at both build
time and validate time (so there is no second copy of this logic to drift
out of sync again):
- `_derive_account_fact_diagnostics()` -- the ONE place
  `position_count`/`nav_reconciliation_status`/
  `nav_reconciliation_mismatch_pct`/`staleness_status` are computed from a
  fact's raw `equity`/`cash`/`positions`/`captured_at`. Used by
  `build_alpaca_paper_account_fact()`, `build_manual_account_fact()`, AND
  `validate_snapshot()` (to recompute and compare against the fact's
  claimed values).
- `_derive_fx_staleness()` -- same pattern for FX `staleness_status`,
  used by `assemble_fx_rates()` and `validate_snapshot()`.
- `validate_snapshot()` now calls `_validate_snapshot_timing()` on the
  packet's own timestamps FIRST, before anything else, then checks every
  account fact's `captured_at <= available_at`, then every FX rate's
  `as_of <= decision_at` (via `_derive_fx_staleness`), then re-derives and
  compares every fact's diagnostics, then every FX rate's `rate`/
  `staleness_status`, then (as before) `risk_capacity_inputs`, then the
  final hash.

All 6 CIO-specified tamper-and-rehash counter-examples are locked as
permanent regressions in `CIORound3ValidatorPitBypassRejected`: future
top-level `available_at`, future account `captured_at`, future FX `as_of`,
a tampered account `staleness_status`, a tampered `position_count`, and a
tampered NAV-reconciliation result -- each tampers a validly-built packet
AND regenerates a fresh, internally-consistent `packet_sha256`, and each
is still rejected. The public-safe redaction/read-only workflow structure
(`capture.py`, `.github/workflows/portfolio-risk-input.yml`) was not
touched in this round -- confirmed correct by CIO round 3 review.

**No live Alpaca Paper capture or
scheduled workflow run has been executed at any point during this fix,**
per explicit CIO instruction.

## Counter-example scenarios (all independently tested)

See `test/test_portfolio_risk_input.py` -- one dedicated `TestCase` class
per scenario:

1. Future-dated snapshot vs. a past decision -- rejected (`FUTURE_DATED_SNAPSHOT_REJECTED`).
2. Stale account balance used as current -- rejected (forces the whole `risk_capacity_inputs` block to `NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT`, not just flagged).
3. Duplicate positions -- deduplicated (identical) / rejected (conflicting, `DUPLICATE_POSITION_CONFLICTING_DATA`).
4. Mixed-currency amounts summed without an FX rate -- rejected (`NOT_COMPUTABLE_MISSING_FX_RATE` / `NOT_COMPUTABLE_STALE_FX_RATE` on every `*_base_currency` field; raw per-currency breakdowns never blended).
5. Manual input disguised as broker-verified -- rejected (`MANUAL_INPUT_DISGUISED_AS_VERIFIED`).
6. Alpaca live vs. paper account confusion -- structurally impossible (hard-coded paper host, no parameter, no live-host string anywhere in the module).
7. Negative or NaN NAV -- rejected (`NEGATIVE_NAV_OR_CASH_REJECTED` / `NON_FINITE_VALUE`).
8. Account-level NAV disagreeing with the sum of positions -- rejected (forces the whole `risk_capacity_inputs` block to `NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT`, not just flagged).
9. Total NAV confirmed while some market's data is missing -- rejected (`full_portfolio_nav_status: NOT_COMPUTABLE_MISSING_ACCOUNT_SCOPE`; a partial scope is only ever `connected_scope_nav`, explicitly labeled, never presented as the full portfolio). Even when scope IS fully connected, any unverified/manual source present still forces `full_portfolio_nav_status: NOT_COMPUTABLE_UNVERIFIED_ACCOUNT_SOURCE` (round 2 PIT-4).
10. Same-timestamp data tampering -- detected, including a **re-signed** tamper (value changed + hash regenerated to match) via independent semantic re-derivation, not just a hash check (`SEMANTIC_TAMPER_DETECTED`, falling back to `PACKET_HASH_MISMATCH` for anything the semantic check doesn't cover).
11. Any order-API call attempted from the read-only path -- structurally impossible, proven by a test.
12. Sizing/quantity/weight computed while policy is unratified -- rejected (`POSITION_SIZE_COMPUTED_WHILE_POLICY_UNRATIFIED`, and `position_size` is a fixed module-level constant, never a function).
13. Any existing authority field flipping to `true` -- rejected (`AUTHORITY_BLOCK_TAMPERED_OR_NOT_ALL_FALSE`).

## What this PR unlocks (not part of this PR)

Connecting stop-distance (`UNRATIFIED_DIAGNOSTIC_NOT_AN_EXECUTABLE_STOP` in
the P5-06/P7-08 packet) to actual account-dollar loss, and then properly
comparing the two-axis P5-06 policy options (Entry Eligibility E1/E2 x
Post-Entry Management M1/M2/M3) against real risk capacity. None of that
sizing/policy math exists in this PR.

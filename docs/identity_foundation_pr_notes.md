# Identity Foundation stage — PR notes

Design source: "Canonical Security Identity / Market Scope Authority" v2
(Notion design packet, CIO-approved 2026-08-24 as this stage's
implementation baseline) and the paired "Dynamic Clock Candidate Validity
Window" v2 packet (combined CIO recommendation section).

## ⛔ Rev 2 claim downgraded — PARTIALLY_VERIFIED (not fully retracted)

CIO independent code review of HEAD `3bd9e0e` (rev 2) returned
**CHANGES_REQUIRED** again, with 5 further boundaries found on top of
round 1's fixes (round 1's fixes themselves were confirmed fine, no
action needed there). The rev-2 claim **"exact-content provenance
verified"** is downgraded to **`PARTIALLY_VERIFIED`** — round 1's fixes
stand, but round 2 closes 5 more real gaps:

1. **P0 — ratification time could still be backdated via the evidence
   file.** `real_usable_from` included the row's own verified first-seen,
   but not the EVIDENCE FILE's own first-seen — a brand-new evidence file
   with a backdated `ratified_at` could make an old row look ratified
   since the past. Fixed: `verify_evidence_first_seen_at` (real git
   history of the evidence file itself, independent of the row) is now a
   4th input to `real_usable_from`, and `verify_approval_evidence` also
   cross-checks the evidence file's own claimed `ratified_at` against the
   row's.
2. **P0 — the append-only registry was a backdating bypass API by
   construction.** `record_first_seen(..., at=<any past date>)` and the
   editable JSONL registry file gave any caller a way to assert an
   arbitrary first-seen time with no independent verification. Removed
   entirely (function, parameter, and all call sites) — public authority
   is now verified ONLY via real git history in this PR. A real hash-chain
   / private append-only store is explicitly deferred to a future PR.
3. **P1 — git verification used the file's basename, not its real
   repo-relative path.** `git show {commit}:{path.name}` silently fails
   to find content for any file nested under a directory (e.g. the real
   `config/canonical_security_identity.json`) — it only happened to work
   in rev-2's own test because that test placed its fixture at repo root.
   Fixed: `_git_repo_root` + real repo-root-relative path resolution, used
   for both `git log --follow` and `git show`. New regression test
   (`test_basename_only_lookup_would_have_failed`) proves the old
   basename-only approach really would have failed against this repo's
   own real nested `config/` file.
4. **P0 — a directly-injected document's policy version was never
   checked.** Only `load_authority()` validated `policy_version`; a
   dict injected straight into a resolver skipped that check entirely.
   Fixed: `validate_security_identity_document`/
   `validate_market_account_scope_document` are now called at the top of
   every public resolver, file-loaded or injected alike — the exact same
   function `load_authority`/`load_scope_authority` use.
5. **P1 — `resolve_instrument_by_id` never verified the linked issuer.**
   It returned `RESOLVED` on the instrument row alone, even with an
   orphan, `PROVISIONAL`, or ambiguous issuer — inconsistent with
   `resolve_instrument_identity`'s full-chain judgment. Fixed: the linked
   issuer now goes through the exact same `_resolve_layer_row` gate.

## ⛔ Rev 1 claims retracted — SUPERSEDED_UNAPPROVED

CIO independent code review of HEAD `c819a38` returned
**CHANGES_REQUIRED** with 7 P0 defects. The rev-1 report's claims
**"exact-content provenance verified"** and **"28 tests validate
Foundation"** are retracted and marked `SUPERSEDED_UNAPPROVED`. All 7
defects were fixed together in one pass (HEAD `0df57a4` and onward — see
commit `Fix 7 P0 defects from CIO code review of HEAD c819a38` for the
full per-defect breakdown). Summary of what changed:

1. **Provenance was self-hash only, not real evidence.** Split into two
   distinct, honestly-labeled checks: `business_payload_sha256`
   (self-consistency only — `verify_business_payload`, documented as NOT
   a security control) and `approval_evidence_ref`/`approval_evidence_sha256`
   (independent verification of a REAL external evidence file's real
   bytes, whose own content must corroborate `rule_id`/`rule_version`/
   `RATIFIED`/the exact `business_payload_sha256` approved —
   `verify_approval_evidence`).
2. **`first_seen_at` was self-declared, uncross-checked.** Now verified
   against real git commit history (`_git_first_commit_time_for_content`)
   or a separate append-only registry (`record_first_seen`/
   `verify_first_seen_at`), producing `verified_first_seen_at`.
   `real_usable_from` uses ONLY the verified value, never the row's own
   claim. Unverifiable → `IDENTITY_NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED`.
3. **`require_instrument_id` bypassed the authority gate** on mere
   structural existence. Now delegates to `resolve_instrument_by_id`, the
   real operational resolver — a `PROVISIONAL`-only instrument row
   correctly fails.
4. **The resolver never invoked `validate_authority_row`.** Every row
   (file-loaded or dependency-injected) is now validated at the point it
   becomes a resolution candidate, via the single shared
   `_resolve_layer_row` pipeline every layer goes through.
5. **Dates were compared as raw strings.** All temporal fields now go
   through a strict parser (`_parse_temporal`) into real UTC datetimes.
   Same-calendar-day comparisons mixing `DATE_ONLY` and full-timestamp
   precision raise `TimePrecisionAmbiguous`, surfaced as
   `IDENTITY_NOT_COMPUTABLE_TIME_PRECISION`.
6. **Multi-active-row handling was inconsistent across layers.** Every
   layer (issuer/instrument/listing/source_alias/market_account_scope)
   now requires EXACTLY ONE active row via the shared pipeline — closing
   the gap where issuer/instrument resolution inside listing lookup never
   checked ambiguity at all.
7. **The new test file wasn't registered in `run_all.py::APPROVED_TESTS`,
   and the real authoritative run never completed.** Registered; see the
   Verification section below for the actual completed result.

## Scope of this PR

Builds the identity-resolution **mechanism** only:

- `identity/canonical_identity.py` — 4-layer (issuer / instrument /
  listing / source_asset_id) resolver, the PIT anti-backdating gate
  (`real_usable_from = max(effective_from, ratified_at, verified_first_seen_at)`),
  independent approval-evidence verification (distinct from business-payload
  self-consistency — see retraction section above), strict temporal
  parsing, exactly-one-active-row ambiguity detection at every layer, and
  layer-confusion detection.
- `config/canonical_security_identity.json` — authority record schema.
  **Zero rows.** No real identity is asserted or ratified by this PR.
- `config/market_account_scope_map.json` — authority record schema.
  **Zero edges.** No real market↔account-scope edge is asserted or
  ratified by this PR.
- `test/test_identity_foundation.py` — the 18 originally-required
  counter-examples (rev-2 API) plus new counter-examples specifically for
  defects 2/3/5/6, structural-validation coverage, and a direct test that
  the real shipped authority files (not synthetic fixtures) resolve every
  real query to `IDENTITY_NOT_COMPUTABLE_*`. Registered in
  `run_all.py::APPROVED_TESTS`.

This PR does **not**:
- wire into the Shadow Matrix (`atlas-private-evidence`) in any way
- change any Dynamic Clock timestamp or `clock/dynamic_clock.py` behavior
- open P8-13 Entry Proposal
- contain any in-code mapping table or hardcoded per-ticker/market
  special-casing
- claim any row `RATIFIED`
- patch `portfolio_risk/portfolio_snapshot.py`'s raw-symbol double-count
  defect

## Dependent defect: `portfolio_risk/portfolio_snapshot.py` raw-symbol double-count

Found during Packet 1 v2 review (counter-example 11): `by_ticker` groups
exposure by `p["symbol"]` (the raw per-source symbol string), not by
`canonical_instrument_id`. Concretely: if the same real BTC position were
ever reported under two Kraken aliases (`BTC` and `XBT`, both real,
documented in `config/crypto_asset_identity_exceptions.json`) within one
snapshot, it would be double-counted as two separate positions.

**This is a dependent defect that cannot be safely resolved until
canonical-instrument adoption actually happens in that file** — fixing it
today with a quick ticker-normalization workaround would either (a)
hardcode exactly the kind of ad-hoc special-casing this stage forbids, or
(b) collide with the separate session already assigned to this file
(background task `task_8dcdbccb`). It is tracked, not fixed, here.
`identity/canonical_identity.py`'s `group_positions_by_instrument` exists
only to demonstrate, in `test_identity_foundation.py`, why
instrument-level grouping is the correct eventual fix — it is not called
from any real portfolio code path by this PR.

## Expected real-world outcome of this PR

Since no real row is `RATIFIED` in either shipped authority file, every
real resolution attempt against them correctly returns
`IDENTITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD` (see
`RealShippedAuthorityFilesAreEmptyTests` in the test file). **This is the
correct outcome for this stage, not a shortfall** — the mechanism is
proven end-to-end using synthetic fixtures in the other test classes,
which include a fully successful `RESOLVED` path once rows are (in
memory, for the test only) genuinely `RATIFIED` with valid provenance.

## Verification — real, completed authoritative run (round 1, HEAD `0df57a4`)

`ATLAS_DISPOSABLE_CHECKOUT=1 python3 run_all.py --authoritative`, run in
a genuinely fresh disposable clone of `feature/identity-foundation` at
HEAD `0df57a4` (not the working clone used to write the code):

- `[2/5]` builder ①→⑭ serial rebuild: all 14 ok.
- `[3/5]` committed ↔ rebuilt byte comparison: **14/14 byte-identical.**
- `[4/5]` 171 approved regression files, **all 171 ok**, including
  `test/test_identity_foundation.py` and every pre-existing
  identity-adjacent regression suite (`test_canonical_identity.py`,
  `test_replay_asset_identity.py`, `test_crypto_taxonomy_identity_slice.py`,
  `test_crypto_breadth_unverified_identity_real_evidence.py`) — confirmed
  unbroken by this change.
- `[5/5]` Fault Injection suite: **50 PASS / 0 FAIL** (FI-3 frozen-input
  tamper remains a pre-existing, already-documented `KNOWN GAP / NOT
  GATED`, unrelated to this PR).
- Final line: **`✅ Actions PASS = YES`**.

This is the genuine, completed result — not `unittest discover` (which is
not this repo's canonical verification path and hung on a live-network
test file when tried in rev 1).

## Verification — real, completed authoritative run (round 2, HEAD `a52b84f`)

`ATLAS_DISPOSABLE_CHECKOUT=1 python3 run_all.py --authoritative`, run in
a SECOND genuinely fresh disposable clone of `feature/identity-foundation`
at HEAD `a52b84f` (round-2 fixes on top of round 1's), separate from the
working clone:

- `[2/5]`/`[3/5]` 14/14 builders rebuilt byte-identical.
- `[4/5]` **171/171 approved regression files ok**, including
  `test/test_identity_foundation.py` (now git-repo-backed, 51 tests) and
  every pre-existing identity-adjacent suite.
- `[5/5]` Fault Injection suite: **50 PASS / 0 FAIL.**
- Final line: **`✅ Actions PASS = YES`**.

Full log preserved outside the repo at
`run_all_authoritative_round2.log` (scratchpad).

## Next stage

Only after this PR's independent CIO review completes does the next
stage (timestamp precision improvements) begin.

# Identity Foundation stage — PR notes

Design source: "Canonical Security Identity / Market Scope Authority" v2
(Notion design packet, CIO-approved 2026-08-24 as this stage's
implementation baseline) and the paired "Dynamic Clock Candidate Validity
Window" v2 packet (combined CIO recommendation section).

## ⛔ Rev 6 claim stays PARTIALLY_VERIFIED — 2 narrow explicit-pin-path contract mismatches closed

CIO independent re-verification of HEAD `e595ac7` (rev 6) confirmed the
core fix — default-HEAD-mode memory+disk co-tamper and dirty-working-tree
blocking — was correctly closed. Two narrower contract mismatches
remained, both scoped to the EXPLICIT-PIN path only:

1. **"Byte-for-byte" was documented but not what the code did.** The
   disk<->trusted-commit comparison parsed JSON and compared canonical
   hashes. Since the explicit-pin path skips the default mode's dirty
   check, a whitespace/indentation-only edit to the disk file (canonically
   identical, but a real, uncommitted change to the actual bytes) still
   passed:
   ```
   disk bytes == pinned git blob: False
   verify_document_matches_source: True
   resolver: RESOLVED
   ```
   **Fixed**: that specific comparison is now a raw `disk_bytes ==
   git_bytes` equality check, never JSON-parsed or hashed. The
   memory<->disk comparison is unchanged and remains canonical/structural
   (CIO confirmed that part is fine as-is — memory is never raw bytes to
   begin with).
2. **`trusted_commit` accepted mutable rev-expressions.** `HEAD`, a
   branch name, a tag, `HEAD~1`, an abbreviated SHA — all resolved and
   were accepted, which didn't match the "pinned commit" contract's
   actual intent (a pin should be immutable). **Fixed**: a new
   `_is_pinned_immutable_commit` gate rejects anything that isn't exactly
   40 (SHA-1) or 64 (SHA-256) lowercase hex characters, AND requires
   `git rev-parse --verify <trusted_commit>^{commit}` to resolve to that
   EXACT SAME string, unchanged — every mutable ref instead resolves to a
   different string (the real SHA it currently points at) and is
   rejected as `IDENTITY_NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED`.

Rev 6's 75 tests and the default-HEAD-mode co-tamper blocking are fully
intact — this was a narrow closing pass on the explicit-pin path only.

## ⛔ Rev 5 claim stays PARTIALLY_VERIFIED — the predicted disk+memory co-tamper bypass closed

CIO independent re-verification of HEAD `104d567` (rev 5) returned CI
green on that exact HEAD but **`CHANGES_REQUIRED` anyway** — a design
gap, not a test-run problem. Rev 5's `verify_document_matches_source`
only checked `input memory document == current disk file` — it never
checked whether the disk file itself was the real git-canonical version.
CIO reproduced:

```
Original:                     IDENTITY_NOT_COMPUTABLE_AMBIGUOUS
Row deleted in memory+disk:   RESOLVED
```

— git commit, approval evidence, the remaining row, and git history all
untouched; only the working tree was made dirty (memory mirrored to
match the tampered disk file, so the old memory-vs-disk-only check
passed).

**Fixed**: `verify_document_matches_source` is now a THREE-way check —
memory == disk == the real git blob at a trusted commit. Two modes:

- **Default** (no `trusted_commit` given): resolves to the repo's actual
  current HEAD via a real `git rev-parse HEAD` call at verification time,
  AND additionally requires `git status --porcelain` for that exact file
  to be completely clean. This catches a direct disk edit AND an
  uncommitted `git checkout <old-sha> -- <path>` revert — the "revert to
  an old real single-row commit and use it as current" bypass — since
  both leave the working tree dirty relative to HEAD.
- **Explicit pin** (`trusted_commit` given): the HEAD-relative dirty
  check is skipped (disk legitimately differing from current branch HEAD
  is expected when a caller has deliberately chosen to trust a different,
  specific, named commit); disk must still match that pinned commit's
  real git blob byte-for-byte. `trusted_commit` is a caller-supplied
  parameter on every public resolver — never read from the document under
  verification, which would let a caller self-declare which commit to
  trust.

Two failure classes: `IDENTITY_NOT_COMPUTABLE_DOCUMENT_TAMPERED`
(memory/disk mismatch, or disk genuinely differs from the trusted
commit's real content) and `IDENTITY_NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED`
(not a real git repo, working tree dirty relative to the default trust
source, or the trusted commit's blob can't be read) — both forbid
`RESOLVED` either way. Applied via the same shared function
(`verify_document_matches_source`/`_document_tamper_status`) across every
record type (instrument/issuer/listing/source_alias/scope-edge) and
every public entry point, not duplicated per type.

## ⛔ Rev 4 claim stays PARTIALLY_VERIFIED — one more defect in the same family closed

CIO independent re-verification of HEAD `82dde6f` (rev 4) confirmed rev
4's fixes were correctly closed (single-field tampers on
`effective_to`/`effective_from`/`rule_version`/`ratified_at` all blocked,
CI green on the exact HEAD) — but found one more defect in the same
family by direct reproduction: every check up to that point only asked
"does the SELECTED row match real git history / the real evidence file?"
None of them asked whether the WHOLE input DOCUMENT still matches its
real source file. CIO reproduced:

```
Original document (2 conflicting active RATIFIED rows for same instrument ID): AMBIGUOUS
After deleting one conflicting row:                                             RESOLVED
```

— evidence, hash, git history, and the remaining row itself all
completely untouched; the remaining row genuinely did exist in git
history on its own, so every existing check passed.

**Fixed** with `verify_document_matches_source`: every public resolver
now compares a canonical hash of its ENTIRE current input document
(excluding internal `_`-prefixed keys) against a freshly-computed
canonical hash of the real file at `_source_path`, re-read from disk on
every call. `canonical_json` preserves list/array order, so this also
catches pure row reordering, not just add/remove/edit. A mismatch is an
immediate `IDENTITY_NOT_COMPUTABLE_DOCUMENT_TAMPERED`, checked before any
row is even looked at (and, in `require_instrument_id`, before
`identify_layer_of_id` runs so a tampered document can't even change
which layer an id structurally appears to belong to). A document with no
`_source_path` (pure synthetic/injected) is not itself a "tamper" — it
has nothing real to compare against and falls through to the existing
per-row checks.

**On retiring arbitrary-dict direct injection** (CIO: "if feasible"): not
done. This was explicitly framed as optional, and the mandatory fallback
(re-verify the whole document at resolver entry) achieves the same
security property without disrupting the large existing body of tests
for other failure modes (ambiguity, unratified, no-authority,
layer-mismatch) that legitimately don't need real git backing.

## ⛔ Rev 3 claim stays PARTIALLY_VERIFIED — one more P0 closed

CIO independent code review of HEAD `d382467` (rev 3) returned
**CHANGES_REQUIRED** again, this time via a DIRECT REPRODUCTION, not just
a code-reading finding: the approval-evidence binding only covered
`business_payload` (per-layer identity fields) — NOT `effective_from`/
`effective_to`/`rule_id`/`rule_version`/`approval_status`/`ratified_at`,
even though those fields directly control the eligibility determination.
CIO reproduced it directly:

```
Original:  IDENTITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD
Tampered:  RESOLVED
```

— take an already-expired `RATIFIED` row and mutate ONLY its in-memory
`effective_to` to `null`, with the evidence file, its hash, and its git
first-seen all completely untouched, and it resolves again.

**Fixed** by introducing `full_determining_payload` (business fields +
`rule_id`/`rule_version`/`approval_status`/`ratified_at`/`effective_from`/
`effective_to`) as the payload bound in TWO independent places:

1. The evidence file's `approved_full_payload_sha256` (replacing the
   narrower `approved_business_payload_sha256`) — `verify_approval_evidence`
   now fails the instant ANY determining field no longer matches what the
   real, git-verified evidence file says was approved.
2. Git-history row-matching (`_row_matcher`, used by
   `verify_row_first_seen_at`) now matches on the SAME full determining
   payload, not just `business_payload_sha256` — closing the adjacent
   "borrow an old row's real `_source_path` while mutating only its
   metadata" bypass: the mutated content no longer matches ANYTHING that
   was ever actually committed, independent of the evidence-file check.

`business_payload_sha256`/`verify_business_payload` are unchanged and
remain the narrower, honestly-labeled self-consistency-only check they
always were.

**The "exact-content provenance" claim stays `PARTIALLY_VERIFIED`** —
this closes one more real gap CIO found; it is not treated as the final
word until CIO's review of this round confirms it.

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

Builds the identity-resolution **mechanism**. A later, separately reviewed
pilot adds only the mechanical records listed in
`docs/identity_authority_pilot.md`:

- `identity/canonical_identity.py` — 4-layer (issuer / instrument /
  listing / source_asset_id) resolver, the PIT anti-backdating gate
  (`real_usable_from = max(effective_from, ratified_at, verified_first_seen_at)`),
  independent approval-evidence verification (distinct from business-payload
  self-consistency — see retraction section above), strict temporal
  parsing, exactly-one-active-row ambiguity detection at every layer, and
  layer-confusion detection.
- `config/canonical_security_identity.json` — authority record schema and the
  three approved pilot identity chains (BTC, Samsung Electronics common,
  SK hynix common).
- `config/market_account_scope_map.json` — authority record schema and the
  three approved pilot edges (`BTC→CRYPTO`, `CRYPTO→CRYPTO`,
  `KOREA→KOREA`).
- `test/test_identity_foundation.py` — the 18 originally-required
  counter-examples (rev-2 API) plus new counter-examples specifically for
  defects 2/3/5/6, structural-validation coverage, and direct tests that
  the real shipped authority files contain only the approved pilot while
  unlisted identities remain `IDENTITY_NOT_COMPUTABLE_*`. Registered in
  `run_all.py::APPROVED_TESTS`.

This PR does **not**:
- wire into the Shadow Matrix (`atlas-private-evidence`) in any way
- change any Dynamic Clock timestamp or `clock/dynamic_clock.py` behavior
- open P8-13 Entry Proposal
- contain any in-code mapping table or hardcoded per-ticker/market
  special-casing
- infer or upgrade any row to `RATIFIED` in resolver code
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

## Expected real-world outcome

Only the separately reviewed pilot identities and scope edges can resolve,
and only after their verified Git first-seen boundary. Every unlisted real
query remains `IDENTITY_NOT_COMPUTABLE_*`. Resolution grants mechanical
identity only: every investment, entry, sizing, order, production, and
trading authority remains false.

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

## Verification — real, completed authoritative run (round 3, HEAD `bc6a45a`)

`ATLAS_DISPOSABLE_CHECKOUT=1 python3 run_all.py --authoritative`, run in
a THIRD genuinely fresh disposable clone of `feature/identity-foundation`
at HEAD `bc6a45a` (round-3 fix on top of rounds 1-2), separate from every
prior verification clone:

- `[2/5]`/`[3/5]` 14/14 builders rebuilt byte-identical.
- `[4/5]` **171/171 approved regression files ok**, including
  `test/test_identity_foundation.py` (57 tests) and every pre-existing
  identity-adjacent suite.
- `[5/5]` Fault Injection suite: **50 PASS / 0 FAIL.**
- Final line: **`✅ Actions PASS = YES`**.

Full log preserved outside the repo at `run_all_authoritative_round3.log`
(scratchpad).

## Verification — real, completed authoritative run (round 4, HEAD `5aec4d4`)

`ATLAS_DISPOSABLE_CHECKOUT=1 python3 run_all.py --authoritative`, run in
a FOURTH genuinely fresh disposable clone of `feature/identity-foundation`
at HEAD `5aec4d4` (round-4 whole-document tamper check on top of rounds
1-3), separate from every prior verification clone:

- `[2/5]`/`[3/5]` 14/14 builders rebuilt byte-identical.
- `[4/5]` **171/171 approved regression files ok**, including
  `test/test_identity_foundation.py` (67 tests, now git-subprocess-heavy
  enough to visibly extend this stage's wall time) and every
  pre-existing identity-adjacent suite.
- `[5/5]` Fault Injection suite: **50 PASS / 0 FAIL.**
- Final line: **`✅ Actions PASS = YES`**.

Full log preserved outside the repo at `run_all_authoritative_round4.log`
(scratchpad).

## Verification — real, completed authoritative run (round 5, HEAD `8b69132`)

`ATLAS_DISPOSABLE_CHECKOUT=1 python3 run_all.py --authoritative`, run in
a FIFTH genuinely fresh disposable clone of `feature/identity-foundation`
at HEAD `8b69132` (round-5 disk+memory co-tamper fix on top of rounds
1-4), separate from every prior verification clone:

- `[2/5]`/`[3/5]` 14/14 builders rebuilt byte-identical.
- `[4/5]` **171/171 approved regression files ok**, including
  `test/test_identity_foundation.py` (75 tests) and every pre-existing
  identity-adjacent suite.
- `[5/5]` Fault Injection suite: **50 PASS / 0 FAIL.**
- Final line: **`✅ Actions PASS = YES`**.

Full log preserved outside the repo at `run_all_authoritative_round5.log`
(scratchpad).

## Verification — real, completed authoritative run (round 6, HEAD `bcce03e`)

`ATLAS_DISPOSABLE_CHECKOUT=1 python3 run_all.py --authoritative`, run in
a SIXTH genuinely fresh disposable clone of `feature/identity-foundation`
at HEAD `bcce03e` (round-6 explicit-pin-path byte-comparison + immutable
-commit fix on top of rounds 1-5), separate from every prior verification
clone:

- `[2/5]`/`[3/5]` 14/14 builders rebuilt byte-identical.
- `[4/5]` **171/171 approved regression files ok**, including
  `test/test_identity_foundation.py` (83 tests) and every pre-existing
  identity-adjacent suite.
- `[5/5]` Fault Injection suite: **50 PASS / 0 FAIL.**
- Final line: **`✅ Actions PASS = YES`**.

Full log preserved outside the repo at `run_all_authoritative_round6.log`
(scratchpad).

## Next stage

Only after this PR's independent CIO review completes does the next
stage (timestamp precision improvements) begin.

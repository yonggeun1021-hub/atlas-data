# P2-03 durable sector identity binding + rotation-policy candidate lane

Status: candidate/evidence only. Nothing in this document or the files it
describes is RATIFIED. No schedule, cron, or full-packet automation is added
or changed. No Regime/Candidate/Stage/briefing/Production/trading authority
is opened.

## History

**First attempt (2026-09-11)** used the real, already-merged P2-01
`theme_taxonomy/2` graph producer to derive real, non-placeholder identity
hashes. CIO correction: a `theme_taxonomy/2` graph is evaluated for one
specific `as_of_date` (`rotation/korea_capital_rotation.py::
_consume_taxonomy()` requires the consumed packet's `as_of_date` to equal the
rotation's own decision date), so that candidate could not be reused
unchanged across Day N, Day N+1, and later natural sessions — it needed a new
graph, a new packet, and a new `taxonomy_packet_sha256` every trading day.
Flipping that graph to `RATIFIED` was rejected too: the real
`theme_taxonomy/2` contract requires a ratified graph to carry non-empty
edges, non-empty asset memberships, and both KOREA + US market coverage —
the cross-market P2-01 taxonomy contract (owned separately under PR #576),
intentionally broader than P2-03's own Korea sector-rotation identity. The
legacy `theme_taxonomy/1` opaque placeholder was also rejected as a fallback
— real, meaningful, independently reviewable identity was required, not
opaque tokens.

**This version (2026-09-12, current)** adds one dedicated, P2-03-owned,
**date-independent** contract instead: `korea_sector_identity_binding/1`.

## Why this needed a small, real code change

`rotation/korea_capital_rotation.py::_validate_binding()` previously accepted
exactly two `taxonomy_contract_version` values: the legacy opaque binding
(`contract["taxonomy_contract_version"]`, `"theme_taxonomy/1"`) and the P2-01
producer's own current version (`taxonomy_producer_contract_version()`,
`"theme_taxonomy/2"`). Neither fits: legacy is opaque-only, and v2 is
date-bound and cross-market. This lane adds:

- `config/korea_sector_identity_binding_contract.json` — a new, narrow,
  P2-03-owned contract (`contract_version: "korea_sector_identity_binding/1"`,
  `date_independent: true`), independent of `theme_taxonomy/1`/`/2` and of
  `config/theme_taxonomy_authority_registry.json`.
- `rotation/korea_capital_rotation.py::sector_identity_binding_contract_version()`
  — reads that contract's `contract_version` live (same drift-safety pattern
  as `taxonomy_producer_contract_version()`).
- `_validate_binding()` now accepts a third `taxonomy_contract_version`:
  `legacy_version`, `producer_version`, or `sector_identity_version`. Nothing
  else changes: `_consume_taxonomy()` and `validate_packet()`'s
  re-derivation already treated "not the v2 producer version" as the
  no-graph-consumption path, so the durable binding falls through that
  existing branch (`return {}, None`) with **zero** further code changes —
  no per-date graph, no authority-registry interaction, no edges/memberships
  requirement, ever, for this binding kind.

## The four candidate documents (all UNRATIFIED)

1. **`config/korea_rotation_sector_identity_decision_rationale_candidate.json`**
   — the small, real, committed document
   `taxonomy_binding.taxonomy_decision_sha256` is a content hash of.
2. **`config/korea_rotation_sector_identity_binding_document_candidate.json`**
   — the durable identity content itself: a fixed positional
   `series_identity -> theme_id` map for the real 46 already-`RATIFIED`
   `config/korea_leadership_policy.json` SECTOR records (24 KOSPI + 22
   KOSDAQ). **No `as_of_date` field exists anywhere in this document** —
   nothing about it is decision-date-specific, so its hash
   (`taxonomy_packet_sha256`) never needs to change across sessions, as long
   as the upstream Leadership policy it is pinned to does not change.
3. **`config/korea_rotation_sector_identity_taxonomy_binding_candidate.json`**
   — the exact 6-field `taxonomy_binding` shape `_validate_binding()`
   requires, using the new `korea_sector_identity_binding/1` contract
   version.
4. **`config/korea_capital_rotation_policy_candidate.json`** — a standalone,
   schema-valid `korea_capital_rotation_policy/1` object:
   `approval_status: "UNRATIFIED"`, `ratified_by: null`,
   `ratified_at_utc: null`. **CIO candidate direction (revised):**
   `top_count = bottom_count = 3`, `maximum_calendar_gap_days = 7` (supersedes
   this lane's first, narrower `1`/`30` proposal). `ranking_metric`,
   `ranking_order`, and `tie_break` semantics are unchanged. `effective_from`
   is a placeholder authored on `2026-09-12` — inert either way, since
   `approval_status != "RATIFIED"` forces `effective = False`
   unconditionally regardless of any date. The real ratification's
   `effective_from` must eventually be **the first verified KRX session
   after the real CIO ratification event** — not this placeholder, not
   `2026-08-22` (the tainted self-declared date), and not `2026-09-11` (the
   date this lane's first, superseded attempt happened to be authored on).

## Proven against real, unmodified code — including a real durability regression

`test/test_korea_capital_rotation_policy_candidate.py` (26 tests) proves,
against the real functions, not a reimplementation of their logic:

- **Durability, the core CIO requirement**: two genuine Leadership
  observation packets — Day N (`2026-09-07→08→09`) and Day N+1
  (`2026-09-08→09→10`, a real rolling window: Day N's own "current"
  observation becomes Day N+1's "prior") — built by the real, unmodified
  `.github/scripts/korea_leadership.py::build_transform()` against the real,
  committed `config/korea_leadership_policy.json` (not a synthetic fixture
  policy, so `policy.policy_sha256` genuinely equals this candidate's pinned
  `upstream_leadership_policy_sha256`). The **exact same, unmodified**
  committed `taxonomy_binding` and `rotation_policy` documents are fed to the
  real `korea_capital_rotation.py::build_packet()` for both days —
  byte-identical before and after, no rebuild, no new hash, no date-literal
  edit.
- **Fail-closed, proven, not asserted**: a fabricated `series_identity` in
  the policy's `benchmark_scopes` fails closed
  (`POLICY_THEME_COVERAGE_MISMATCH`); a missing real sector identity in a
  Leadership payload fails closed at the Leadership build stage
  (`PIT_TAXONOMY_COVERAGE_MISMATCH`); a deliberately mutated (drifted) copy
  of the Leadership policy file produces a real packet whose
  `policy.policy_sha256` no longer matches this candidate's pinned value, and
  `build_packet()` correctly rejects it (`UPSTREAM_POLICY_BINDING_MISMATCH`).
- `rotation/korea_capital_rotation.py::_validate_binding()` accepts the
  binding candidate under the new `korea_sector_identity_binding/1` version;
  `_validate_policy()` accepts the policy candidate and returns
  `effective=False` regardless of dates; `_consume_taxonomy()` never enters
  the v2 graph-consumption branch for this binding kind.
- The real P2-01 authority registry (`config/theme_taxonomy_authority_registry.json`)
  still has 0 records — this lane never uses the `theme_taxonomy/2` graph
  producer at all, so it cannot touch that registry.
- None of the four documents contain `ratified_by`/`ratified_at_utc`, the
  tainted `2026-08-22T07:19:09Z` timestamp, the all-zero taxonomy placeholder
  tokens, or the `2026-09-11` date the first, superseded candidate used.

## What this lane does not do

- Does not populate or modify `config/theme_taxonomy_authority_registry.json`
  or any file owned by PR #576.
- Does not modify `rotation/theme_taxonomy.py` or `rotation/theme_taxonomy_authority.py`.
- Does not use the P2-01 `theme_taxonomy/2` graph producer at all for this
  binding.
- Does not add or change any `schedule:`/cron trigger.
- Does not build a full `korea_capital_rotation/4` packet against real
  Breadth/Leadership evidence for production use, and does not touch
  `data/latest_korea_rotation.json`.
- Does not open Regime, Candidate, Stage, briefing, Production, or trading
  authority for any market.

## What happens next

This lane stops here for CIO ratification. If and when the CIO ratifies the
candidate mapping/counts/gap (real `ratified_by`, real `ratified_at_utc` fixed
at the actual ratification instant, a real `effective_from` equal to the
first verified KRX session after that event, and confirmed or revised
`top_count`/`bottom_count`/`maximum_calendar_gap_days`), a **separate** future
slice would flip the rotation policy's `approval_status` to `"RATIFIED"` with
those real fields, and only then would Phase B (schedule migration,
full-packet producer, rolling-pointer wiring) become unblocked — still gated
separately on Korea's own producer/cadence availability.

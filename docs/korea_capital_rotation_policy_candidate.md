# P2-03 rotation-policy canonicalization-only candidate lane

Status: candidate/evidence only. Nothing in this document or the files it
describes is RATIFIED. No schedule, cron, or full-packet automation is added
or changed. No Regime/Candidate/Stage/briefing/Production/trading authority
is opened. `rotation/korea_capital_rotation.py` is byte-unchanged.

## Why this lane exists

Phase A of the "Korea Natural Rotation Producer" slice found that the only
`korea_capital_rotation_policy/1` object anywhere in this repository (other
than the explicit test-only fixture in `test/test_korea_capital_rotation.py`)
is `REAL_ROTATION_POLICY`, built inline by `.github/scripts/korea_capital_
rotation_ledger_proof.py` — a manual one-shot proof script, not a committed,
independently reviewable artifact:

- It self-asserts `ratified_by: "Atlas CIO"` and
  `ratified_at_utc: "2026-08-22T07:19:09Z"` with no external ratification
  trail — no PR review comments on the PR that introduced it (#199), no
  linked Notion decision packet (unlike, e.g., P1-COM-05's real, documented
  *Regime Policy Ratification Decision Packet v1*).
- Its `taxonomy_binding` is an honest all-zero placeholder:
  `taxonomy_id: "TAXONOMY.NOT_RATIFIED"`, `taxonomy_decision_id:
  "DECISION.NOT_RATIFIED"`, `taxonomy_decision_sha256`/`taxonomy_packet_sha256`
  = 64 zero characters.

CIO verdict: `P2_03_ROTATION_POLICY_CANONICALIZATION_REQUIRED`. Phase B
(schedule migration, full-packet automation) stays blocked. This lane opens a
**policy-canonicalization-only** track instead: build real, independently
reviewable candidate documents for CIO ratification, and stop.

`.github/scripts/korea_capital_rotation_ledger_proof.py` is left exactly as
Phase A found it — this lane does not edit or delete it (a regression test
confirms the tainted values are still there, unmodified, superseded rather
than silently rewritten).

## What changed since Phase A's own read of `korea_capital_rotation.py`

Phase A's initial reading of `rotation/korea_capital_rotation.py` was
(mistakenly) taken from a stale, out-of-sync working tree rather than
`origin/main`. The real current file (merged 2026-09-05, commit `248141b9`)
already ships a **real** `theme_taxonomy/2` consumption path: a caller can
supply a real `theme_taxonomy_input/1` graph's raw bytes
(`taxonomy_source_bytes` on `build_packet()`), which gets independently
re-run through the real, already-existing P2-01 producer
(`rotation/theme_taxonomy.py::build_packet()`) and its independent authority
resolver (`rotation/theme_taxonomy_authority.py::resolve_graph_authority()`).
Nothing about the graph's own claimed identity or approval is trusted by
`korea_capital_rotation.py` — it is independently re-derived every time. This
lane uses that real, already-merged path instead of inventing a competing
binding scheme.

## The five candidate documents

All built and validated by `rotation/korea_capital_rotation_policy_candidate.py`,
none RATIFIED:

1. **`config/korea_rotation_theme_identity_graph_candidate.json`** — a real
   `theme_taxonomy_input/1` graph. One `THEME` node per real, already-
   `RATIFIED` `config/korea_leadership_policy.json` SECTOR record (24 KOSPI +
   22 KOSDAQ), positional `theme_id`s (`{KOSPI|KOSDAQ}.SECTOR.NN`, reusing
   only the already-established, non-arbitrary naming from the superseded
   proof script — never its ratification claim). No edges, no memberships:
   neither is required unless `approval.approval_status == "RATIFIED"`
   (`rotation/theme_taxonomy.py::build_packet()`), and this graph's approval
   is honestly `"UNRATIFIED"`.
2. **`config/korea_rotation_theme_identity_decision_rationale_candidate.json`**
   — the small, real, committed document the graph's own
   `approval.decision_sha256` is a content hash of, so that hash is
   independently reviewable rather than an opaque number.
3. **`config/korea_rotation_theme_identity_taxonomy_packet_candidate.json`**
   — the REAL, unmodified `rotation/theme_taxonomy.py::build_packet()` output
   for document #1, committed as a verified snapshot (committed-vs-rebuilt
   byte-identical, regression-tested — this repo's usual discipline). Because
   the graph's approval is `UNRATIFIED` and the real authority registry
   (`config/theme_taxonomy_authority_registry.json`) has 0 records, this
   resolves honestly to:
   - `graph_status: "DRAFT_OR_NOT_EFFECTIVE_GRAPH"`
   - `authority_resolution.status: "AUTHORITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD"`
   - `theme_membership_authorized: false`, every `authority.*` flag `false`

   never a fabricated or self-declared authorization.
4. **`config/korea_rotation_theme_identity_binding_candidate.json`** — the
   exact 6-field `taxonomy_binding` shape
   `korea_capital_rotation.py::_validate_binding(value, contract,
   derived=False)` requires from a caller: the real producer's current
   contract version (`theme_taxonomy/2`, read live from that producer's own
   committed contract, never a literal) plus the real identity/hashes from
   documents #1–#3.
5. **`config/korea_capital_rotation_policy_candidate.json`** — a standalone,
   schema-valid `korea_capital_rotation_policy/1` object:
   `approval_status: "UNRATIFIED"`, `ratified_by: null`,
   `ratified_at_utc: null`, `effective_from: "2026-09-11"` (the date this
   lane was authored — inert either way, since `approval_status !=
   "RATIFIED"` forces `effective = False` unconditionally regardless of any
   date). `taxonomy_decision_sha256` / `taxonomy_packet_sha256` /
   `upstream_leadership_policy_sha256` are the same real values as document
   #4's — never re-typed or independently re-derived. `top_count = bottom_count
   = 1` and `maximum_calendar_gap_days = 30` are carried forward as
   **proposals** for CIO confirmation (the same "minimal non-arbitrary
   realization of TOP/BOTTOM" reasoning the superseded proof script used for
   the count, not its ratification claim) — not asserted as already decided.

## Proven against real, unmodified code — not reimplemented rules

`test/test_korea_capital_rotation_policy_candidate.py` (20 tests) proves,
against the real functions, not a reimplementation of their logic:

- `rotation/theme_taxonomy.py::build_packet()` accepts the graph and produces
  exactly the committed packet snapshot.
- `rotation/theme_taxonomy_authority.py`'s registry still has 0 records (this
  lane never touched it — still #576's separate, owned scope).
- `rotation/korea_capital_rotation.py::_validate_binding(derived=False)`
  accepts the binding candidate.
- `rotation/korea_capital_rotation.py::_validate_policy()` accepts the policy
  candidate and returns `effective=False`.
- `rotation/korea_capital_rotation.py::_consume_taxonomy()` genuinely consumes
  the real graph bytes and `_assert_theme_nodes()` confirms every
  policy-declared `theme_id` is a real active node in the real consumed graph
  (and genuinely raises when given a fabricated one — tested directly).
- None of the five documents contain `ratified_by`/`ratified_at_utc`, the
  tainted `2026-08-22T07:19:09Z` timestamp, or the all-zero taxonomy
  placeholder tokens.

## What this lane does not do

- Does not populate or modify `config/theme_taxonomy_authority_registry.json`
  (owned separately, under PR #576).
- Does not modify `rotation/theme_taxonomy.py`, `rotation/theme_taxonomy_
  authority.py`, or `rotation/korea_capital_rotation.py`.
- Does not add or change any `schedule:`/cron trigger.
- Does not build a full `korea_capital_rotation/4` packet against real
  Breadth/Leadership evidence, and does not touch `data/latest_korea_rotation.json`.
- Does not open Regime, Candidate, Stage, briefing, Production, or trading
  authority for any market.

## What happens next

This lane stops here for CIO ratification. If and when the CIO ratifies the
candidate mapping/counts/gap (real `ratified_by`, real `ratified_at_utc`,
confirmed or revised `top_count`/`bottom_count`/`maximum_calendar_gap_days`),
a **separate** future slice would: promote the graph's `approval` to
`RATIFIED` with real ratification fields (which the real `theme_taxonomy.py`
and `theme_taxonomy_authority.py` would then independently re-validate,
including requiring a real authority-registry record for full effect), flip
the rotation policy's `approval_status` to `"RATIFIED"` with matching real
fields, and only then would Phase B (schedule migration, full-packet
producer, rolling-pointer wiring) become unblocked — still gated separately
on each market's own producer availability.

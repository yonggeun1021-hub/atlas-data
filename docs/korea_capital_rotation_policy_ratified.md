# P2-03 rotation-policy RATIFICATION MATERIALIZATION

Status: **RATIFIED** identity binding + rotation policy, real artifacts. This
is a separate, bounded slice from the P2-03 rotation-policy
canonicalization-only candidate lane (PR #669, merged) — that lane's own
module (`rotation/korea_capital_rotation_policy_candidate.py`) and test
(`test/test_korea_capital_rotation_policy_candidate.py`) are unmodified and
remain historical `UNRATIFIED` candidate evidence.

## External ratification trail

CIO review comment on PR #669:
<https://github.com/yonggeun1021-hub/atlas-data/pull/669#issuecomment-5643258809>
("CIO RATIFICATION DECISION — P2-03 Korea Rotation Policy semantics: GO"),
posted `2026-09-12T03:47:23Z`, ratifying the corrected candidate head
`9ce4a2b9b85896173ed53c4bb4f60e41c9710654` (exact-head CI run `34660715590`,
SUCCESS), reconciled onto post-#670 `main` and merged as
`04d66c71483fdf51ba93f96cd0c9b538d83846be`.

## Ratified semantics (unchanged from the candidate — now real)

- Dedicated, date-independent `korea_sector_identity_binding/1` contract
  (`config/korea_sector_identity_binding_contract.json`, from PR #669,
  untouched by this slice).
- The same explicit 46 KOSPI (24) / KOSDAQ (22) SECTOR
  `series_identity -> theme_id` positional map.
- `RELATIVE_STRENGTH_VS_OWN_BENCHMARK` / `DESCENDING_WITHIN_BENCHMARK_SCOPE`
  / `SERIES_IDENTITY_ASC`.
- `top_count = bottom_count = 3`, `maximum_calendar_gap_days = 7`.
- `ratified_by = "Atlas CIO"`, `ratified_at_utc = "2026-09-12T03:47:23Z"` —
  the real decision instant. Never the tainted `2026-08-22T07:19:09Z`
  self-declared timestamp, never the `2026-09-11`/`2026-09-12`
  candidate-authoring placeholders.
- `effective_from = "2026-09-14"`.

## `effective_from` derivation — mechanical, not weekday arithmetic

`rotation/korea_capital_rotation_policy_ratified.py::resolve_effective_from()`
computes this from the real, already-committed official KRX holiday capture
(`evidence/market_calendar/krx_global_holiday/2026-09-09/capture-2026.json`),
the same canonical source used for the earlier P2-05 Korea gap derivation:

- Ratification instant `2026-09-12T03:47:23Z` UTC = `2026-09-12T12:47:23+09:00`
  KST. `2026-09-12` is itself a **Saturday** — not eligible even though the
  ratification event happened on it.
- `2026-09-13` is a **Sunday** — closed.
- `2026-09-14` is a **Monday**, not listed as a KRX holiday — the first real
  verified trading session after ratification.

## Four ratified artifacts

1. `config/korea_rotation_sector_identity_decision.json` — the ratified
   decision record: `approval_status: "RATIFIED"`, real `ratified_by`/
   `ratified_at_utc`, `external_decision_trail` citing the PR #669 comment,
   `superseded_candidate_pr: 669`.
2. `config/korea_rotation_sector_identity_binding_document.json` — the
   ratified identity content itself (unchanged mapping from the candidate,
   still no `as_of_date` field — fully date-independent).
3. `config/korea_rotation_sector_identity_taxonomy_binding.json` — the exact
   6-field `taxonomy_binding` shape `korea_capital_rotation.py::
   _validate_binding()` requires.
4. `config/korea_capital_rotation_policy_ratified.json` — the ratified
   `korea_capital_rotation_policy/1`. **Deliberately not named
   `config/korea_capital_rotation_policy.json`** — that exact path is a
   reserved, tested-absent location
   (`test/test_korea_capital_rotation.py::
   test_contract_default_policy_cli_atomic_and_tracked_output_boundaries`
   asserts it does not exist, proving `repository_default_policy: "ABSENT"`
   as a real repo-layout property, not just a JSON claim). This artifact is
   a real, externally-ratified policy a caller supplies explicitly — never a
   checked-in default `korea_capital_rotation.py` auto-loads.

## Why ratification alone is not a natural proof

`korea_capital_rotation.py::_validate_policy()`'s own `covers_both` check
requires `effective_from <= prior_date`. Because `effective_from`
(`2026-09-14`) postdates `ratified_at_utc` (`2026-09-12T03:47:23Z`), no
observation pair that predates ratification can ever satisfy `covers_both` —
this is a structural property of the artifact, not an assertion.
`test/test_korea_capital_rotation_policy_ratified.py` proves, against the
real, unmodified `korea_capital_rotation.py`:

- A pre-`effective_from` pair stays honestly `effective=False`.
- A pair whose `current_date` reaches `effective_from` but whose
  `prior_date` does not is **still** `effective=False` — both dates of the
  pair must be inside the interval, per the CIO's explicit rule.
- A structurally in-interval pair does become `effective=True` — proving the
  mechanism is correctly wired, **not** a claim that a real natural
  observation pair exists yet.

The first genuine post-ratification natural proof still requires a **real**
observation pair whose `prior_date >= 2026-09-14` and whose `current_date`
is also inside the effective interval. This slice does not fabricate,
backfill, or claim that pair.

## What this slice does not do

- Does not modify the candidate lane's module or test (PR #669's own files
  stay byte-identical, historical `UNRATIFIED` evidence).
- Does not modify `rotation/korea_capital_rotation.py`,
  `rotation/theme_taxonomy.py`, `rotation/theme_taxonomy_authority.py`, or
  `config/theme_taxonomy_authority_registry.json` (still 0 records).
- Does not add or change any `schedule:`/cron trigger.
- Does not build a full `korea_capital_rotation/4` packet against real
  Breadth/Leadership evidence, and does not touch
  `data/latest_korea_rotation.json`.
- Does not open Regime, Candidate, Stage, briefing, Production, trading,
  order, or capital authority for any market. Phase B (schedule migration,
  full-packet automation, rolling-pointer wiring) remains closed until a
  real post-ratification natural observation pair is verified.

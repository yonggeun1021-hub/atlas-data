# P3-12 Shadow Validation Harness contract

Status: **PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY**, review material only. This
harness ratifies nothing, mutates no canonical config file, calls no Upbit
order/withdrawal/private endpoint, and grants no investable/PAPER/order/
Production/Trading authority. Every `authority` field it emits is hardcoded
`False` except `review_only`.

## Purpose

P3-12's real classifier (`universe/upbit_tradeable_universe.py`) and its
production populate script (`.github/scripts/upbit_universe_populate.py`)
correctly keep every Upbit KRW market at `OBSERVATION_POOL` today, because
the tradeable-universe policy, the exclusion taxonomy, and every per-market
identity mapping are all `PROPOSED_UNRATIFIED`. That is by design, not a bug
-- but it also means nobody can see, ahead of an actual ratification
decision, what the funnel would look like if the CIO said yes. This harness
answers exactly that question, repeatably, without pre-committing to an
answer:

> If today's already-proposed policy, taxonomy, and identity proposals were
> ratified **exactly as currently written** -- no threshold changed, no
> taxonomy category added, no identity guessed -- what would the resulting
> universe/PAPER-eligibility funnel look like, and which markets still
> cannot be judged at all?

## What it is built from

`universe/upbit_shadow_validation_harness.py::evaluate()` reads:

* the same hash-validated raw snapshot `universe/upbit_tradeable_universe.py`
  itself reads (`evidence/crypto/upbit/raw/<date>/`), via that module's own
  `load_snapshot_core()` -- unchanged, no parallel parsing;
* `config/upbit_tradeable_universe_policy.json` and
  `config/upbit_exclusion_taxonomy.json`, exactly as committed;
* `config/upbit_asset_identity_exceptions.json` if present (currently zero
  records);
* `config/crypto_breadth_exclusion_taxonomy.json` -- the Kraken-side
  taxonomy, the **only RATIFIED** broad canonical-asset registry anywhere in
  this repository -- used strictly as an **informational cross-reference
  signal**, never as a promotion/exclusion input (see "Cross-reference
  signal" below).

It never fetches from the network and never reads a config value it then
mutates on disk.

## The two things it evaluates, and the boundary between them

### 1. Primary shadow scenario -- ratify exactly as written

* `shadow_ratify()` makes an **in-memory-only** deep copy of the real policy
  and taxonomy documents with `approval_status` forced to `"RATIFIED"`.
  Every threshold value and every taxonomy record is otherwise byte-for-byte
  identical to the committed file.
* `shadow_identity_registry()` builds an **in-memory-only**
  `{market: canonical_asset_id}` registry from today's own
  `PROPOSED_UNRATIFIED` identity proposals (the same proposals
  `.github/scripts/upbit_identity_review_bundle.py` already persists), minus
  any market with an unresolved `DUPLICATE_CANONICAL_TARGET` collision --
  those are never guessed at, exactly as the real classifier's own
  `IDENTITY_COLLISION` gate requires.
* Both are fed into the exact same, unmodified
  `universe/upbit_tradeable_universe.py::build_classification()` the real
  production populate script calls. **No parallel classification logic
  exists in this harness** -- it cannot silently drift from the real gate.
* Neither in-memory document, nor the identity registry, is ever written to
  any file.

This is reported as `funnel.after_shadow_if_ratified_as_currently_proposed`.

### "Before" baseline label (CIO review, PR #459, P2)

`funnel.before_current_production_mechanical_collision_included` is built
from `ratified_identity_registry={}` (matching real production -- no
ratified registry exists) **plus** `blocked_markets` computed from today's
own identity proposals. This is a deliberate, exact reproduction of what
`.github/scripts/upbit_universe_populate.py::rebuild()` does in real
production today: it always passes today's mechanical
`DUPLICATE_CANONICAL_TARGET` collision set into `build_classification()`,
regardless of ratification status, because collision detection needs no
ratification to be safe to enforce. The field name says so explicitly, and
`packet["funnel_definitions"]` documents it in full, so this baseline is
never mistaken for an idealized zero-collision "before ratification"
picture -- today's real collision count is 0 (no numeric effect), but
`test_upbit_shadow_validation_harness.py::test_before_baseline_includes_todays_mechanical_collision_hold_like_real_production`
proves a collision fixture shows up in this baseline too, not only in the
shadow scenario.

### 2. Supplemental hypothetical scenario -- clearly separate, exploratory only

`config/upbit_exclusion_taxonomy.json` today ships **zero
`eligible_category` records** -- only exclusion records for 6 known
stablecoins. Under `unknown_asset_policy: fail_closed_unknown`, that means
even ratifying the file exactly as written still resolves almost every real
asset to `TAXONOMY_UNKNOWN`, not `TRADEABLE_UNIVERSE`. The primary scenario
above reports this honestly (0 `TRADEABLE_UNIVERSE`, 0 `PAPER_ELIGIBLE` for
today's snapshot). To give the CIO a concrete sense of what closing that gap
would look like, `shadow_taxonomy_with_kraken_corroborated_eligible_records()`
builds a **second, explicitly labeled** hypothetical taxonomy: the same
in-memory `RATIFIED` copy, plus one additional `eligible_crypto` record per
canonical asset id already independently corroborated by the RATIFIED
Kraken breadth taxonomy (never an invented judgment -- the record's evidence
is that external ratification, verbatim). This is reported separately under
`funnel_supplemental_hypothetical`, never merged into the primary funnel
numbers, and is not a ratification proposal -- adding real eligible_crypto
records to `config/upbit_exclusion_taxonomy.json` is a separate, later,
human-ratified change, same discipline as everything else in P3-12.

## Kraken breadth taxonomy loading (fail-closed, shared contract)

`load_kraken_breadth_taxonomy()` does not maintain its own validator.
Following CIO review feedback on PR #459, it delegates entirely to
`identity/candidate_identity_gap_inventory.py::_load_taxonomy()` -- the same
contract already exercised elsewhere in this repository -- which enforces,
fail-closed:

* `approval_status == "RATIFIED"` (a fixture or file merely shaped like a
  ratified document, but not actually marked `RATIFIED`, is rejected, not
  silently treated as trustworthy);
* an exact `policy_version` pin (`crypto_breadth_exclusion_taxonomy/v2`) --
  a schema change to that file must be a deliberate, reviewed harness change,
  never silently absorbed;
* `eligible_category`/`excluded_categories` are present and correctly
  shaped, and every record's `category` is a member of that vocabulary;
* no two records share a `canonical_asset_id` (Kraken's breadth taxonomy has
  exactly one record per asset, ever -- unlike Upbit's own taxonomy, it does
  not use multiple effective-dated records per asset, so any second record
  for the same id is rejected outright, not merged/overwritten);
* every `effective_from`/`effective_to` is a valid ISO date and
  `effective_to` is never before `effective_from`.

`load_kraken_breadth_taxonomy()` returns `(doc, records_by_canonical_id)`;
resolving whether that record is *active* as of a given `evaluation_as_of`
is a separate step, `_active_kraken_record()`, mirroring
`candidate_identity_gap_inventory.py::_taxonomy_diagnostic()`'s own
not-yet-effective/expired handling -- both resolve to "absent", never an
error, since that is normal effective-dated-registry shape, not a fault.
Both `kraken_cross_reference_signal()` and `taxonomy_audit()` consume Kraken
corroboration exclusively through `_active_kraken_record()`, so a
not-yet-effective or expired Kraken record can never corroborate this
harness's supplemental hypothetical scenario or manual-review queue.

## Cross-reference signal (identity)

`.github/scripts/upbit_universe_populate.py` and
`.github/scripts/upbit_identity_review_bundle.py` both deliberately omit
`identity_review_findings()`'s `known_canonical_ids` cross-reference check in
production, because no ratified *broad* canonical registry exists that
covers Upbit's asset scope -- using Kraken's registry there would falsely
flag most legitimate Upbit-only assets as `NO_CANONICAL_CROSS_REFERENCE`.
This harness still surfaces the signal, but narrowly and only two ways,
never as a blocking gate:

* an aggregate count (`identity_review.cross_reference`) of how many
  proposals' candidate ids are/aren't present in the Kraken RATIFIED
  registry at all;
* a **manual-review queue** entry only when the Kraken registry
  independently flags that *exact* canonical id as `unverified_identity`
  (ticker collision) -- e.g. `KRW-RE`, where Kraken's own ratified taxonomy
  already documents a real ticker collision on the same symbol "RE".

The much larger "simply absent from Kraken's registry" tier is never listed
as individual manual-review rows (most legitimate Upbit-only assets hit it)
-- only its aggregate count is reported, so the queue stays actionable.

## Taxonomy audit

For every proposed identity's candidate canonical asset id not already
covered by an effective-dated record in `config/upbit_exclusion_taxonomy.json`,
the harness checks two things, both reused rather than invented:

1. **Name-pattern hints** (`taxonomy_pattern_flags()`) -- coarse, mechanical
   substring/keyword checks for stablecoin, wrapped, leveraged, and
   derivative-like naming, always surfaced as `CANDIDATE_NEEDS_TAXONOMY_REVIEW`,
   never auto-applied. A symbol-suffix leveraged-token heuristic was tried
   and deliberately dropped -- it false-positived on ordinary Kraken-corroborated
   assets like `JUP`/`SYRUP` (both merely end in the letters "UP") with no
   ratified base-asset registry to check the prefix against.
2. **Kraken RATIFIED registry cross-reference** -- if the Kraken breadth
   taxonomy already classifies this exact canonical id into one of Upbit's
   own `excluded_categories`, that's surfaced as a candidate with the
   Kraken record's own reason text as evidence. If Kraken classifies it as
   `eligible_category` (a positive match), that is **not** a review
   candidate -- it is counted in `corroborated_eligible_count` only. If
   Kraken classifies it into a category **absent from Upbit's taxonomy
   schema entirely** (e.g. `commodity_linked` for `KRW-XAUT`/"Tether Gold"),
   that is surfaced as a `schema_gaps` entry -- a decision Upbit's taxonomy
   schema itself needs, before any per-asset ratification.

`category_definition_gaps` separately flags that `leveraged` and
`derivative_like` -- both already listed in
`config/upbit_exclusion_taxonomy.json`'s `excluded_categories` -- have **zero
ratified records or worked criteria anywhere in this repository**. This
harness does not invent a definition for them.

## Reproducibility / evidence

`build_shadow_packet()` is a pure function of its arguments: the parsed core
snapshot, the real policy/taxonomy/exceptions/Kraken-taxonomy documents, the
evaluation date, the resolved code commit SHA, and a source file-hash map.
No wall-clock or random value appears anywhere inside it -- the same inputs
always produce byte-identical output, including `payload_sha256`
(`test_upbit_shadow_validation_harness.py::test_determinism_same_input_twice_identical_output`).

`.github/scripts/upbit_shadow_validation_harness_run.py` is the only impure
entry point: it resolves `git rev-parse HEAD` (fails closed, never a
swallowed exception -- `universe/upbit_shadow_validation_harness.py::git_commit_sha()`)
and persists one append-only, tamper-checked packet per `snapshot_date`
under `data/observations/upbit_p3_12_shadow_validation/<date>/packet.json`.

A rerun against an existing packet performs two independent checks, in
order, both fail-closed:

1. **Self-hash verification** -- the existing file's own declared
   `payload_sha256` is recomputed from its own body (everything except that
   field) and compared. A missing, malformed (not 64 lowercase hex
   characters), or mismatched value raises `EXISTING_PACKET_HASH_INVALID`
   immediately. This exists because comparing only "content minus
   `code_commit_sha`/`payload_sha256`" (step 2) cannot by itself catch a
   file whose `payload_sha256` field alone was tampered with while its body
   stayed byte-identical -- that previously passed silently as
   `verified_existing` (CIO review, PR #459, P1).
2. **Content-drift check** -- with the existing file now known to be
   internally self-consistent, its content (again excluding
   `code_commit_sha`, which legitimately advances as new commits land) must
   still exactly match a freshly recomputed packet for the same
   `snapshot_date`, or `EXISTING_PACKET_DRIFT_OR_TAMPER` is raised.

## Offline commands

```bash
python3 .github/scripts/upbit_shadow_validation_harness_run.py 2026-08-29
```

Reads the already-committed raw snapshot under
`evidence/crypto/upbit/raw/2026-08-29/` (produced separately by
`.github/scripts/upbit_market_capture.py`, unmodified by this harness) and
writes `data/observations/upbit_p3_12_shadow_validation/2026-08-29/packet.json`.
Calls no network endpoint, mutates no canonical config file.

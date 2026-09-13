# Candidate evidence and lifecycle receipt

`candidate_evidence_lifecycle_receipt/1` is a read-only sidecar for the
three-market candidate lookup. It makes already retained evidence queryable
without changing the lookup's owned files or inventing candidate policy.

## Connected evidence

- Inclusion rationale comes verbatim from `_watchlist_rows.json`, the retained
  Notion PM Watchlist snapshot. Every row keeps the Notion row URL, source
  collection, capture time, and exact file hash. The source is explicitly
  review-required and never grants Candidate or Stage authority.
- Candidate validity and expiry come from the existing ratified P8-12
  `candidate_validity_window_assessment/1`. The receipt independently verifies
  its hash, authority identity, 172800-second rule, temporal-only scope, and
  closed downstream locks before exposing a result.
- Lifecycle status is a mechanical comparison of the selected
  `data/stage_history.json` snapshot with its predecessor. It emits `NEW`,
  `MAINTAINED`, `PROMOTED`, `DEMOTED`, or `DROPPED`. These labels describe a
  recorded delta; they are not a rule authorizing that change.

Each lifecycle row exposes the observed date and precision, mechanical rule
version, transition-reason evidence state, exact inclusion source, and P8-12
validity result. A changed Stage whose decision reason is absent from
`stage_history.json` remains `NO_EVIDENCE`; the inclusion rationale is not
silently reused as the change reason.

## PIT boundary

The retained Watchlist snapshot was captured on 2026-08-15. Its text is not
shown in a receipt evaluated before that capture date. The P8-12 assessment is
subject to the same rule using its operational evaluation time. Receipt build
time is recorded separately and never replaces source time.

## Unresolved policy, kept unresolved

The repository currently contains a ratified Korea *sector-series identity* to
theme-id binding. It does not contain a ratified security-symbol to sector or
Theme membership. The cross-market Theme authority registry has zero records,
the US membership sources are empty and unratified, and the P2-05 rotation
ledger contract declares its repository state policy `ABSENT`.

Therefore the receipt reports the following fields as `POLICY_UNDEFINED` and
does not synthesize values:

- security symbol to sector/Theme binding;
- symbol-level rotation-ledger link;
- general Discovery/Candidate/Ready promotion, hold, or exclusion rule.

The gap register distinguishes those policy gaps from two connection gaps now
closed by this sidecar: retained inclusion-reason consumption was
`IMPLEMENTATION_NOT_CONNECTED`, while P8-12 expiry was an existing ratified rule
whose consumer was missing.

## Usage

Build a deterministic receipt for a selected committed evaluation date:

```text
python3 discovery/candidate_evidence_lifecycle_receipt.py \
  --generated-at-utc 2026-09-13T05:30:00Z \
  --as-of-date 2026-09-11
```

Use `--output <path>` only when a caller needs a file. Without it, the receipt
is printed to standard output. The module's `lookup_symbol()` accepts either a
six-digit Korea symbol or its `.KS` form.

No collector, schedule, runner, pin, policy, Stage, Buy, Action, Order,
Production, Trading, or real-capital authority is added by this contract.

## CIO decision packet: removing the Stage adjudication dead end

Status: **proposal only / awaiting CIO ratification**. This section does not
change Stage policy.

The recommended option is **A: Separate System-Evaluated Stage**. The retained
Notion Stage tag remains an immutable source observation, while an independently
versioned system Stage is derived from ratified market-native evaluator fields.
The manual tag is not an input requirement for system promotion. This repairs
the current “strong evidence has nowhere to go” failure and the circular
dependency in which an item must already have a manual Stage tag before it can
be evaluated.

| Policy element | Existing approved or observed basis | New proposal in option A |
| --- | --- | --- |
| Required conditions | Retained source, PIT time, `UNKNOWN` preservation, and no-forced-action are existing boundaries | Require population membership, resolved identity, evaluation coverage, evidence quality, Translation, Expectations Gap, invalidation, freshness, active-veto state, evidence refs, rule version, and effective time |
| Evidence source | Market-native retained evidence remains authoritative in its own scope | Use a common decision wrapper; do not translate market-native facts into a common score |
| Data period | Source times and date precision are retained as recorded | No universal lookback is proposed; the decision must name the exact evidence period it used |
| Missing data | Existing doctrine forbids replacing missing evidence with PASS | `HOLD` or `NOT_COMPUTABLE`, never PASS |
| Validity / expiry | P8-12 ratifies 48-hour trigger freshness only | Do not turn 48 hours into Stage expiry. A review/expiry time must be explicitly supplied and ratified; no default is proposed |
| Promotion | No general ratified promotion policy exists | After CIO ratifies the field vocabulary and each evaluator's PASS semantics, all required current fields must PASS; `UNKNOWN`, missing, stale, or active veto holds. Manual Notion Stage is not required |
| Maintenance | Current same-Stage observations can be mechanically reported | Any required field that is not PASS produces `HOLD` with exact first blocker and evidence refs |
| Demotion / drop | Some symbol prose exists, but no general rule is ratified | Only a ratified market-native invalidation/exit result may derive `DEMOTE` or `DROP`; missing evidence alone does not imply either |
| Re-entry | No general ratified re-entry policy exists | Require a fresh full evaluation and new evidence refs; do not carry prior validity or a manual tag forward |
| Market differences | Korea, US, and Crypto keep separate native classifications | Same wrapper, separate market-native evidence contract; no cross-market rank or threshold |

Option B is the smaller human-decision-only contract: evidence opens a review,
and a PM/CIO receipt changes Stage. It is safer to deploy first but does not
meet the requirement that system evaluation can promote without manually
updating a Notion tag. Option C keeps the evidence-only hold and therefore
preserves the present adjudication dead end.

Option A's `ALL_REQUIRED` expression is a proposed control rule, not an active
policy. The individual PASS semantics for Translation, Expectations Gap,
invalidation, and evidence quality still require CIO ratification. No numeric
score or threshold is supplied by this proposal.

### Current first blockers (evidence generation as of 2026-09-12)

This table describes the current reviewed generation only. It is not a claim
that every name was blocked by the same reason for the whole prior month.

| Item | First blocker | Current data position |
| --- | --- | --- |
| HD Hyundai Heavy Industries (`329180`) | Discovery admission and Stage transition rule absent; an obsolete event-date gate also needs factual correction | Official priority evidence was reported in IC #7, but evidence cannot authorize promotion without a rule |
| Hyosung Heavy Industries (`298040`) | Expectations Gap decision rule absent | Inclusion and invalidation rationale are retained; P8-12 temporal state is connectable |
| SanDisk (`SNDK`) | Discovery-to-Candidate and EXIT rules absent | Current state remains unknown; missing evidence cannot be promoted or dropped |
| Hanwha Aerospace (`012450`) | Discovery-to-Candidate rule and security-to-rotation binding absent | P8-12 temporal evidence exists; security membership cannot be inferred from sector-series taxonomy |
| Stage-null Coverage rows | Discovery admission rule and canonical Stage source mapping absent | Retained reasons are queryable where the 2026-08-15 Watchlist snapshot has a row; full-population evaluation remains a separate owner lane |
| TSM (`TSM`, control case) | Not an observation-to-Candidate case; entry language remains undefined | The already ratified P5-07 degradation condition produced an auditable natural hold, demonstrating the difference a ratified rule makes |

The current KR bounded evaluator selects three `supported_pipeline_subjects`.
Four additional Watchlist records have `stage=null` and are excluded even
though their source rows exist. The US bounded evaluator similarly uses a fixed
18-session price slice. These are evaluation-coverage limits, not evidence
that the excluded names failed. Expanding those evaluators belongs to the
separate population owner; this receipt does not duplicate it.

Raw KIS large/mid/small sector codes and US SEC SIC can be source facts, but
they are not a ratified Atlas investment Theme or rotation membership. Option
A must preserve that distinction and may not infer Theme from either code.

The receipt exposes these records to a Stage4 consumer but reports
`stage4_eligible_record_count=0` and
`NOT_ADMISSIBLE_STAGE_POLICY_UNRATIFIED`. A later handoff must bind the exact
ratified policy id, version, effective time, and decision evidence before any
Stage4 candidate input becomes eligible.

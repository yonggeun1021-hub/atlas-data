# Market Candidate Discovery Lookup (`market_candidate_discovery_lookup/1`)

`discovery/market_candidate_discovery_lookup.py` is a read-only lookup over
already-committed evidence. It explains, per market (KR / US / CRYPTO), why
the current candidate count is what it is, and, per symbol, which existing
conditions the next step still lacks. It creates no candidate, rule,
threshold, ranking, or freshness window and grants no authority.

## Existing modules it reuses (never re-implements)

| Role | Module / packet | What is taken from it |
| --- | --- | --- |
| aggregate coverage receipt | `discovery/three_market_evaluation_coverage.py` | population / bounded-output / crypto funnel counts; the lookup re-derives the same counts from the per-symbol rows and fails closed on any mismatch |
| KR population | `data/observations/krx_global_universe/<date>/packet.json` | asset master records, `effective_interval`, `policy_status` |
| KR evaluation | `data/latest_korea_symbol_market_review.json` (+ `data/latest_korea_market_signals.json`) | bounded review rows, entry state / reasons, price session date |
| KR screening layer (optional) | `data/observations/krx_registry_evaluation_coverage/<date>/packet.json` | aggregate KIS-master screening counts; reported `NOT_AVAILABLE` when the packet is absent |
| US population | `data/observations/us_global_universe/<date>/packet.json` | source attribute rows, `effective_interval`, `policy_status` |
| US evaluation | `data/latest_us_symbol_market_review.json` (+ `data/latest_free_market_data.json`) | bounded review rows, IEX daily-bar coverage |
| KR/US population-level symbol data | newest lookup-time-eligible session under `inputs.{kr,us}_population_observation_root` (`data/observations/{korea,us}_population_symbol_observation/<session>/`, `population_symbol_observation_packet/1`, PR #702) | `data_acquired.population_level_symbol_data`: population-wide `data_observation` / `evaluability` / `evaluation` status counts, read only through the packet's own `decision/population_symbol_observation.py::reverify()` (hash / schema / sidecar-consistency check); never rebuilt, never re-scored |
| CRYPTO population | decision-bound `data/observations/upbit_tradeable_universe/<date>/packet.json` | market states / reasons, candle counts, turnover |
| CRYPTO evaluation | latest `evidence/crypto_paper_decision/<date>/<hhmm>/<gen>/packet.json` | P5-08 criteria, P5-09 presence, funnel counts |
| CRYPTO detail | latest `evidence/crypto_candidate_detail/...` (`crypto_candidate_detail_view/v1`) | per-market price / liquidity / trend / trigger facts, only when bound to the same decision generation |
| CRYPTO identity | `data/observations/upbit_identity_review/<date>/packet.json`, `data/observations/upbit_bounded_identity_registry/<date>/packet.json` | proposal claim, `VERIFIED_CANDIDATE` / `HOLD_*` verdict |
| rotation / sector context | `data/observations/korea_leadership_context`, `data/observations/crypto_leadership`, `config/us_symbol_market_review_contract.json` proxies | index-level or proxy-level evidence only; symbol-to-sector binding is `NO_EVIDENCE` everywhere today |
| pipeline membership | `data/stage_history.json`, review contracts' `supported_pipeline_subjects` | stage, first-seen date, current-stage-since date |
| validity / expiry | `evidence/operational/dynamic_clock/candidate_validity_window_assessment.json` (P8-12) | temporal status per subject; `NO_EVIDENCE` when the subject is not assessed |
| discovery cases | latest `data/observations/event_discovery_cases/<date>/packet-*.json` | per-subject case counts and evidence status; crypto is `FEATURE_NOT_IMPLEMENTED` per the packet's own `source_coverage` |

The coverage receipt asserts that the newest Crypto decision generation
evaluated every admitted market. When P5-08 did not run in that generation
(for example `P5_08_PROMOTION_FUNNEL_UNAVAILABLE:REGIME_PAYLOAD_FUTURE_DATED`),
the receipt refuses; the lookup then reports
`coverage_receipt.status = FAILED_CLOSED` with the exact reason, marks every
`coverage_receipt_cross_check` as `NOT_AVAILABLE`, and still builds the chain
from the per-symbol sources. `build_report(..., strict=True)` re-raises
instead. The Crypto row then carries `evaluated.admitted_not_evaluated`, the
decision's own `derivation_notes`, a `COLLECTION_FAILED` gap, and
`last_generation_with_evaluations` (the newest earlier generation that did
evaluate, reported separately and never substituted for the latest one).

Every packet is loaded with its own hash / validator (`payload_sha256`,
`packet_sha256`, `assessment_sha256`, the existing `validate_output`
functions, the decision module's leadership lineage check). The newest packet
is selected by the packet's internal date, and a directory name that
disagrees with that date fails closed.

`validate_report()` also rebuilds the report from those source packets and
requires exact equality. Recomputing the report's self-hash after changing a
count or classification therefore cannot turn altered output into valid
evidence. A caller validating an older retained report must pass the same
historical `inputs` paths used to build it.

## Market status (`build_report(...)["markets"][]`)

```
population   -> data_acquired -> evaluated -> passed | held | excluded | unevaluated
```

* Each count carries `as_of` (the source's own date), `population_id` (the
  exact population it was counted over) and a `source` reference.
* `reconciliation` proves `population == evaluated-in-population + unevaluated`,
  lists evaluated symbols not found in the population, and records
  `coverage_receipt_cross_check = MATCH` against the three-market receipt.
* `passed.count = 0` is always labelled: `REVIEW_AUTOMATIC_ENTRY_COUNT_FROM_SOURCE`
  with `pass_rule_status = 미정` for KR/US, `FOCUSED_REVIEW_COUNT_FROM_DECISION_SNAPSHOT`
  for CRYPTO.
* `candidate_zero_semantics` separates
  `BOUNDED_REVIEW_ONLY_NO_POPULATION_CANDIDATE_RULE` (KR/US),
  `EVALUATOR_DID_NOT_RUN_IN_LATEST_GENERATION` (CRYPTO when the latest
  decision generation carries no P5-08 rows),
  `CRITERIA_UNKNOWN_NOT_A_NEGATIVE_RESULT` (CRYPTO when every evaluated
  market stayed UNKNOWN) and `EVALUATED_NO_CANDIDATE` (only when every
  criterion was known and failed).

### KR/US `data_acquired.population_level_symbol_data`

Adapts the newest **eligible** `population_symbol_observation_packet/1`
session (PR #702) for the same market, selected under
`default_inputs()`'s `{kr,us}_population_observation_root` -- never an
ambient/global default, so `build_report(inputs=...)` and
`validate_report(report, inputs=...)` stay reproducible from exactly the
inputs they were given. When no eligible session has been retained yet,
this stays the pre-existing placeholder: `count = 미집계`,
`status = NOT_RETAINED_IN_PUBLIC_REPOSITORY` (`evidence` names
`NO_ELIGIBLE_SESSION_ALL_FUTURE` specifically when sessions exist but every
one is future-dated). Point-in-time boundary: a session dated after the
report's own `generated_at` (lookup time), or whose own packet
`generated_at` is after that instant, was not yet available at lookup time
and is skipped when selecting the newest session -- it is never treated as
a reason to report anything invalid. Once an eligible session is selected:

* the packet is read only through its own `reverify()` (persisted-packet
  hash, schema, authority, and `summary.json`-sidecar-consistency check);
  this lookup never re-derives a row or recomputes a status from raw inputs;
* `status = OBSERVATION_PACKET_INVALID` when *that* eligible session fails
  its own `reverify()` (drift/tamper) -- an older, valid session is never
  used as a silent fallback; the exact failure is reported instead of a
  count;
* `status = OBSERVED` when the packet's own `population` (its
  `population_id` / `count` / `as_of`) matches the population this lookup is
  already reporting for that market; `OBSERVED_POPULATION_MISMATCH` when it
  disagrees, with both populations kept side by side in `population_match`
  -- never silently substituted;
* `count`, `data_observed_count`, `evaluable_count`, `evaluated_count`,
  `evaluated_bounded_count`, `evaluated_without_full_inputs_count`,
  `formal_candidate_count`, `not_evaluable_reason_counts` and
  `entry_state_counts` are copied verbatim from the packet's own `summary`;
  `generated_at` is the packet's own input-snapshot time (never this
  report's lookup time -- see `generated_at_semantics`);
* `session_recency` states a purely deterministic date relation between the
  session's own `as_of_session_date` and the report's lookup date --
  `CURRENT_SESSION` (same date) or `HISTORICAL` (earlier date; future is
  already excluded by construction), plus `days_before_lookup_date`. No
  freshness window, staleness policy, or threshold is invented here.

This field never changes `evaluated`/`disposition`/`gap_classification` for
the market: the bounded-review funnel this lookup already reconciles is
unaffected. It only answers, separately, what the full population's own
observation packet currently says about data/evaluability/evaluation
coverage beyond the bounded-review subset.

### Gap classification

| class | meaning | current KR/US/CRYPTO example |
| --- | --- | --- |
| `COLLECTION_FAILED` | a source was asked for and returned nothing usable | US `SNDK`: `PIPELINE_SYMBOL_PRICE_HISTORY_UNAVAILABLE` (IEX-only scope); CRYPTO skipped generation whose `derivation_notes` carry no date-mismatch marker |
| `EVALUATION_HALTED_INPUT_DATE_MISMATCH` | inputs were collected but belong to different dates, so the evaluator halted (`*_DATE_MISMATCH`, `*FUTURE_DATED` in the decision's `derivation_notes`) — not a collection failure | CRYPTO latest generation 2026-09-13 00:22Z: `UPBIT_REALTIME_RUN_DATE_MISMATCH`, `REGIME_PAYLOAD_FUTURE_DATED` |
| `SOURCE_STALE` | the source's **own** `effective_interval` has elapsed at lookup time | KR/US universe packets outside their `valid_from..valid_to` |
| `FEATURE_NOT_IMPLEMENTED` | no connected component produces the figure | KR/US full-population evaluator; KR per-symbol price retention |
| `POLICY_UNDEFINED` (`미정`) | a rule/threshold is not ratified | KRX/US `policy_status` UNRATIFIED entries, `FINAL_*_REGIME_*`, crypto criteria `NO_RATIFIED_*` |
| `EVALUATED_CRITERIA_UNKNOWN` | the evaluator ran but every criterion stayed UNKNOWN | crypto P5-08 held markets |
| `EVALUATED_EXCLUDED_BY_RATIFIED_RULE` | a ratified rule excluded the row | crypto `INVESTMENT_WARNING_ACTIVE` (taxonomy ratified) |
| `EVALUATED_NO_CANDIDATE` | criteria known, none passed | not present today |

### Reader summary (`markets[].summary`)

Each market row also carries a `summary` that separates the population from
the symbols actually evaluated and buckets the outcome into categories a
reader must be able to tell apart (`category_labels` in the report):

| key | label | meaning |
| --- | --- | --- |
| `unevaluated` | 미평가 | population minus evaluated symbols (KR/US: no full-population evaluator); CRYPTO: admitted markets skipped for a non-date reason |
| `evaluation_halted_input_date_mismatch` | 평가 중단(입력 날짜 불일치) | CRYPTO admitted markets the latest generation halted on, with the decision's `derivation_notes` |
| `collection_failed` | 수집 실패 | evaluated subjects whose input data collection failed (US `SNDK`) |
| `policy_undefined` | 정책 미정 | evaluated but held by unratified rules (KR/US regime policy; CRYPTO identity scope + criteria UNKNOWN) |
| `no_evidence` | 근거 없음 | inclusion reason / sector binding / rotation link not recorded |
| `excluded_by_ratified_rule` | 비준 규칙에 의한 제외 | CRYPTO taxonomy exclusions (investment warning) |
| `evaluated_no_candidate` | 정상 평가 후 후보 없음 | `applicable=false` while no pass rule is ratified; the `0` is the absence of a rule, not a negative result |

`summary.explanation` is a Korean sentence built only from those fields. For
CRYPTO it names the latest generation, why it halted, and the last evaluating
generation as a past result (`last_generation_with_evaluations.historical =
true`, never substituted for the latest one).

Each `lookup_symbol` result carries `classification` = one category above
(or `candidate`), its `label`, the driving `reason`, and
`no_evidence_items` (the evidence fields that are `NO_EVIDENCE` for that
symbol).

Freshness for sources without a declared interval is reported as
`policy = 미정` plus the elapsed days; no numeric window is invented. The
report's `generated_at` is labelled `LOOKUP_TIME_ONLY_NEVER_A_SOURCE_DATE`.

## Symbol lookup (`lookup_symbol(market, symbol, generated_at=...)`)

Sections: `population_membership`, `candidate_inclusion` (pipeline subject
status, stage history, `inclusion_reason` -- `NO_EVIDENCE` for KR/US because
`supported_pipeline_subjects` is an explicit list and
`config/rules.candidates.json` is unratified migration evidence),
`sector_rotation_link`, `last_evaluation`, `next_step_unmet_conditions`
(existing reason / criterion codes only; undefined rules stay `미정`),
`exclusion_expiry` (existing rule exclusion, stage status, P8-12 validity
row), `discovery_cases`, `detail_contract_refs`. Crypto adds `detail_view`
copied from the bound `crypto_candidate_detail_view/v1` packet and, for an
admitted market the latest generation skipped, `last_evaluation.status =
ADMITTED_NOT_EVALUATED_IN_LATEST_GENERATION` (or
`ADMITTED_EVALUATION_HALTED_INPUT_DATE_MISMATCH`) with the latest
generation's `derivation_notes` plus `last_evaluated_generation`
(`historical=true`, `evaluated_date_utc`, `label`). An unknown symbol raises
`SYMBOL_NOT_FOUND`. See `docs/market_candidate_discovery_portal_mapping.md`
for the portal field mapping.

## Portal reuse

`report["portal"]` names the compact `markets[].symbols` row contract for a
per-market list and the `#symbol_detail` contract for a detail page, and
lists the existing contracts (three-market coverage, KR/US symbol reviews,
crypto decision snapshot, crypto candidate detail view) with file and packet
hashes so a Portal projection can cite them. Display boundary:
`FACT_ONLY_NO_INFERENCE_UNKNOWN_KEPT_AS_UNKNOWN`. No screen is redesigned by
this contract.

## CLI

```
python3 discovery/market_candidate_discovery_lookup.py --generated-at 2026-09-13T01:27:15Z --compact
python3 discovery/market_candidate_discovery_lookup.py --generated-at 2026-09-13T01:27:15Z --market KR --symbol 012450
```

## Boundaries

Read-only. No scanner, ranking, threshold, candidate promotion, Stage
transition, order, PAPER, Production, or trading authority. Stage3->Stage4
handoff files, account/shadow code, and the coverage receipt itself are not
modified.

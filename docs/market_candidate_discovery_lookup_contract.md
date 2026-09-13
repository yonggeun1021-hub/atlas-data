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
| CRYPTO population | decision-bound `data/observations/upbit_tradeable_universe/<date>/packet.json` | market states / reasons, candle counts, turnover |
| CRYPTO evaluation | latest `evidence/crypto_paper_decision/<date>/<hhmm>/<gen>/packet.json` | P5-08 criteria, P5-09 presence, funnel counts |
| CRYPTO detail | latest `evidence/crypto_candidate_detail/...` (`crypto_candidate_detail_view/v1`) | per-market price / liquidity / trend / trigger facts, only when bound to the same decision generation |
| CRYPTO identity | `data/observations/upbit_identity_review/<date>/packet.json`, `data/observations/upbit_bounded_identity_registry/<date>/packet.json` | proposal claim, `VERIFIED_CANDIDATE` / `HOLD_*` verdict |
| rotation / sector context | `data/observations/korea_leadership_context`, `data/observations/crypto_leadership`, `config/us_symbol_market_review_contract.json` proxies | index-level or proxy-level evidence only; symbol-to-sector binding is `NO_EVIDENCE` everywhere today |
| pipeline membership | `data/stage_history.json`, review contracts' `supported_pipeline_subjects` | stage, first-seen date, current-stage-since date |
| validity / expiry | `evidence/operational/dynamic_clock/candidate_validity_window_assessment.json` (P8-12) | temporal status per subject; `NO_EVIDENCE` when the subject is not assessed |
| discovery cases | latest `data/observations/event_discovery_cases/<date>/packet-*.json` | per-subject case counts and evidence status; crypto is `FEATURE_NOT_IMPLEMENTED` per the packet's own `source_coverage` |

Every packet is loaded with its own hash / validator (`payload_sha256`,
`packet_sha256`, `assessment_sha256`, the existing `validate_output`
functions, the decision module's leadership lineage check). The newest packet
is selected by the packet's internal date, and a directory name that
disagrees with that date fails closed.

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
  `CRITERIA_UNKNOWN_NOT_A_NEGATIVE_RESULT` (CRYPTO today) and
  `EVALUATED_NO_CANDIDATE` (only when every criterion was known and failed).

### Gap classification

| class | meaning | current KR/US/CRYPTO example |
| --- | --- | --- |
| `COLLECTION_FAILED` | a source was asked for and returned nothing usable | US `SNDK`: `PIPELINE_SYMBOL_PRICE_HISTORY_UNAVAILABLE` (IEX-only scope) |
| `SOURCE_STALE` | the source's **own** `effective_interval` has elapsed at lookup time | KR/US universe packets outside their `valid_from..valid_to` |
| `FEATURE_NOT_IMPLEMENTED` | no connected component produces the figure | KR/US full-population evaluator; KR per-symbol price retention |
| `POLICY_UNDEFINED` (`미정`) | a rule/threshold is not ratified | KRX/US `policy_status` UNRATIFIED entries, `FINAL_*_REGIME_*`, crypto criteria `NO_RATIFIED_*` |
| `EVALUATED_CRITERIA_UNKNOWN` | the evaluator ran but every criterion stayed UNKNOWN | crypto P5-08 held markets |
| `EVALUATED_EXCLUDED_BY_RATIFIED_RULE` | a ratified rule excluded the row | crypto `INVESTMENT_WARNING_ACTIVE` (taxonomy ratified) |
| `EVALUATED_NO_CANDIDATE` | criteria known, none passed | not present today |

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
copied from the bound `crypto_candidate_detail_view/v1` packet. An unknown
symbol raises `SYMBOL_NOT_FOUND`.

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

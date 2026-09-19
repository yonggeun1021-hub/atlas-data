# Portal candidate pages ← `market_candidate_discovery_lookup/1` minimum mapping

Read-only mapping between the existing atlas-portal candidate surfaces and
the lookup report produced by `discovery/market_candidate_discovery_lookup.py`
(PR #697). No portal file is modified by this document; it names what the
portal already renders, which lookup field would feed it, and whether the
value exists in committed data today or still needs development.

Portal inputs observed (atlas-portal `origin/main`, read-only inspection):

| Portal surface | Portal input today | Loader |
| --- | --- | --- |
| `/radar` candidate board (`InvestmentPipelineBoard`) and home summary | `InvestmentPipelineRow` = `projectInvestmentPipelineRows({us, korea, crypto, observations, cryptoAsOf})` | `lib/investment-pipeline-view.ts` ← `generated/atlas-evidence-snapshot.json` (`data/latest_{korea,us}_symbol_market_review.json` via `scripts/sync-atlas-evidence.mjs`), `generated/atlas-crypto-decision-snapshot.json` (`scripts/sync-crypto-decision.mjs`), `generated/atlas-public-snapshot.json` `data.candidates[]` (`dynamic_clock` review queue via `scripts/sync-atlas-public.mjs`) |
| `/security/[symbol]` detail | same `InvestmentPipelineRow` (`why`, `blocker`, `stage`, `asOf`) + `koreaMarketReview` / `usMarketReview` panels + `OpportunityCandidate` lineage panel | `app/security/[symbol]/page.tsx` |
| Crypto candidate detail board | `CryptoCandidateDetailEnvelope` (`crypto_candidate_detail_view/v1`) | `scripts/sync-crypto-candidate-detail.mjs` → `generated/atlas-crypto-candidate-detail.json` |

## A. Per-market list (`/radar` rows) ← `markets[].symbols[]` + `markets[].summary`

| Portal field (`InvestmentPipelineRow`) | Lookup field | Fill status |
| --- | --- | --- |
| `id`, `symbol`, `name`, `market` | `symbols[].symbol`, `symbols[].name` (KR/US); `symbols[].symbol` + `canonical_asset_id` (CRYPTO); `market` | **기존 데이터로 채움** (same source packets the portal already syncs) |
| `stage` (WATCHLIST … PAPER_ACTIVE) | not produced. The lookup keeps the source strings `pipeline_stage` (KR/US legacy Discovery/Candidate/Ready) and `funnel_stage` / `decision_state` (CRYPTO) | **개발 필요** — stage promotion is deliberately not inferred; the portal's own `exactFormalStage` rule stays the authority |
| `asOf` | `symbols[].pipeline_as_of` (KR/US); `evaluated.evaluated_at` (CRYPTO) | 기존 데이터로 채움 |
| `why` (편입 근거) | `lookup_symbol().candidate_inclusion.inclusion_reason` — `NO_EVIDENCE` for KR/US, `RULE_RECORDED` (P3-12 state/reason) for CRYPTO | KR/US: **근거 없음으로 표시** (portal already renders "편입 이유 기록 미연결"); CRYPTO: 기존 데이터로 채움 |
| `blocker` | `symbols[].blocking_reasons[]` (KR/US, existing review reason codes); `symbols[].decision_reason` / `universe_reason` (CRYPTO) | 기존 데이터로 채움 |
| `firstSeen`, `ageDays`, `lastChange` | `candidate_inclusion.stage_history.first_seen_date` / `current_stage_since` (from `stage_history.json`); lifecycle events stay with the portal's dynamic-clock observations | first-seen: 기존 데이터로 채움 (KR/US only); lastChange: portal keeps its current source |
| `sourceStage`, `tone` | `pipeline_stage` / `decision_state` | 기존 데이터로 채움 (tone is portal display logic) |
| **new** per-market header: population vs. evaluated | `summary.population_count` (`population_as_of`), `summary.evaluated_symbol_count` (`evaluated_at`), `summary.categories.{unevaluated, policy_undefined, no_evidence, evaluated_no_candidate, collection_failed, evaluation_halted_input_date_mismatch, excluded_by_ratified_rule}` with Korean `label`, `summary.explanation` | **기존 데이터로 채움** — new lookup output; portal needs a small header component (개발 필요, display only) |
| **new** "왜 후보가 적은가" | `gap_classification[]` (`class`, `code`, `affected_count`, `evidence`), `candidate_zero_semantics`, `next_step_conditions[]` | 기존 데이터로 채움 — needs a display component (개발 필요, display only) |

## B. Symbol detail (`/security/[symbol]`) ← `lookup_symbol(market, symbol)`

| Portal element | Lookup field | Fill status |
| --- | --- | --- |
| "ATLAS 상태" (canonical stage) | not produced (see `stage` above) | 개발 필요 (portal rule) |
| "관측·편입 근거" | `candidate_inclusion.inclusion_reason` + `classification.no_evidence_items` | KR/US: 근거 없음 명시; CRYPTO: 기존 데이터 |
| "반대·부족 근거" | `next_step_unmet_conditions[]` (`condition`, `status`, `class`) and `classification.{category,label,reason}` | 기존 데이터로 채움 |
| 한국/미국 "종목 관측 요약" panel | `last_evaluation.{evaluated_at, operational_date_kst, entry_state, price_context, flow_context}` — identical to the review packet the portal already syncs | 기존 데이터로 채움 (no change) |
| 마지막 평가시각 | `last_evaluation.evaluated_at`; CRYPTO adds `last_evaluation.latest_generation` and `last_evaluated_generation` (`historical=true`, `label`, `evaluated_date_utc`) | 기존 데이터로 채움 — when `historical=true` the portal must render it as 과거 결과, never as the latest evaluation |
| 섹터·로테이션 근거 | `sector_rotation_link` (KR: index-level leadership context + UNRATIFIED market claim; US: contract proxies; CRYPTO: leadership status/unknown reason) | symbol→sector binding: **근거 없음** everywhere (개발 필요 upstream, not portal) |
| 기존 규칙 제외·만료 | `exclusion_expiry.{excluded_by_existing_rule, stage_status, validity_window (P8-12 row), source_state_lifetime}` | 기존 데이터로 채움 (validity rows exist for KR subjects and crypto canonical ids; US: NO_EVIDENCE) |
| 발굴 사례 | `discovery_cases` (US subjects have cases; KR none; CRYPTO `FEATURE_NOT_IMPLEMENTED` per source packet) | 기존 데이터로 채움 |
| 기술 근거 (hash/lineage) | `detail_contract_refs[]` (`contract`, `path`, `file_sha256`, `packet_sha256`) | 기존 데이터로 채움 |
| Crypto candidate detail board rows | unchanged — the lookup reuses the same `crypto_candidate_detail_view/v1` packet and reports `reconciliation.detail_view_binding` | 기존 데이터 (no change) |

## C. Minimum remaining work to connect (not done in PR #697)

1. **Publish the lookup report** as a committed packet or a portal sync input
   (e.g. `generated/atlas-candidate-discovery-status.json` via a new
   `scripts/sync-*.mjs` in atlas-portal, or a `data/observations/...` packet in
   atlas-data). Today the report is produced on demand by the CLI only.
2. **Portal header component** for `summary` (population vs. evaluated, four
   categories with labels, `explanation`) on `/radar` per market.
3. **Portal "why few candidates" component** rendering `gap_classification` and
   `next_step_conditions`, keeping `미정` / `NO_EVIDENCE` verbatim.
4. **Symbol detail**: render `classification` and, for CRYPTO,
   `last_evaluated_generation` with the `historical` flag as a past result.
5. Stage mapping (`InvestmentPipelineStage`) stays with the portal's existing
   rule; the lookup does not supply a formal stage.

Upstream (not portal, owned elsewhere): crypto production-path input-date
alignment (Codex Stage3), KR/US full-population evaluator, symbol→sector
binding evidence, ratified pass rules.

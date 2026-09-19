# Population Symbol Observation (`population_symbol_observation_packet/1`)

`decision/population_symbol_observation.py` (core) with
`decision/korea_population_symbol_observation.py` and
`decision/us_population_symbol_observation.py` (market adapters) turns the
committed KR / US source-coverage population into one read-only packet in
which **every population symbol appears exactly once**. It reuses the existing
symbol evaluators through their extracted per-symbol row builders
(`korea_symbol_market_review._symbol_row`, `us_symbol_market_review._symbol_row`)
and never scores, ranks, promotes, or estimates. Design: PR #700
(`docs/kr_us_population_evaluation_design_20260913.md`).

## Four axes per symbol

| axis | values | meaning |
| --- | --- | --- |
| `data_observation.status` | `DATA_OBSERVED` / `DATA_NOT_OBSERVED` | a session price fact exists for the symbol (KR: watchlist confirmed close or the retained KRX information-system all-stock response; US: IEX daily bars) |
| `evaluability.status` | `EVALUABLE` / `NOT_EVALUABLE` + `reasons` | all inputs the existing evaluator needs are present (KR: confirmed close + SMA20 + investor flows; US: daily bars). Missing inputs are listed, e.g. `SMA20_NOT_COMPUTABLE:RETAINED_SESSIONS=2`, `INVESTOR_FLOW_NOT_AVAILABLE`, `PRICE_SOURCE_NOT_CONFIGURED`, `PRICE_SOURCE_NOT_RETAINED`, `SOURCE_ROW_MISSING`, `ROW_BUILD_FAILED:<code>` |
| `evaluation.status` | `EVALUATED_BOUNDED` / `EVALUATED` / `NOT_EVALUATED` | `EVALUATED_BOUNDED`: row copied from the bounded review packet the contract already produces (verified byte-identical); `EVALUATED`: row built by the extracted builder from full inputs (`row_source=extracted_symbol_row`, `evaluated_at` = input snapshot time); `NOT_EVALUATED`: `entry_state=null`, `row=null`, reasons kept. KR `NOT_EVALUATED` rows with a session price also carry `partial_review` (`is_evaluation=false`, `BLOCKED`, the missing-input reasons) |
| `formal_candidate.status` | `PIPELINE_SUBJECT` / `NOT_A_FORMAL_CANDIDATE` | only the existing Notion Atlas-Stage tag (through `data/stage_history.json`) makes a symbol a pipeline subject; `promotion_by_this_packet=false` always |

`observation_status` collapses the axes to one reader code:
`EVALUATED_BOUNDED`, `EVALUABLE_PRICE_FLOW_SMA20` (KR full inputs),
`EVALUABLE_PRICE` (US bars), `EVALUABLE_SESSION_PRICE_ONLY` (KR session
price only), `NOT_EVALUABLE`.

`summary` reports `population_count`, `data_observed_count`, `evaluable_count`,
`evaluated_count` (+ `evaluated_bounded_count`,
`evaluated_without_full_inputs_count` for a bounded subject whose price was
unavailable), `formal_candidate_count`, `not_evaluable_reason_counts`,
`entry_state_counts`, and `passed_count = 0` with
`NO_RATIFIED_PASS_RULE_ZERO_IS_ABSENCE_OF_RULE`. `policy_undefined` lists the
unratified rules verbatim (`미정`).

## Inputs (all re-verified by their own hash / validator)

KR: `data/observations/krx_global_universe/<session>/packet.json`,
`data/latest_korea_market_signals.json`, `data/latest_korea_symbol_market_review.json`
(rebuilt and compared), `data/stage_history.json`, `data/briefing/krx/*.json`,
and the newest `evidence/regime/kr_information_system/<pub>/source-capture`
whose manifest covers the session (validated with
`regime.kr_information_system_runtime_bridge._validate_manifest`, projected with
`regime.krx_information_system_capture._raw_projection`). Session date =
`latest_korea_market_signals.as_of_date` unless `--session-date` is given.

US: `data/observations/us_global_universe/<date>/packet.json`,
`data/latest_free_market_data.json`, `data/latest_us_symbol_market_review.json`
(rebuilt and compared), `data/stage_history.json`; `us_investable_registry`
contract for the list of facts a directory row still lacks (the registry
evaluator is not run with fabricated facts: `registry_evaluation =
NOT_RUN:REQUIRED_FACTS_MISSING`).

### Session ↔ market-data coupling

Both markets refuse a session their market data does not actually describe, but
the test differs because the available field differs.

KR's `operational_date_kst` **is** the session
(`korea_symbol_market_review` sets it from `market.as_of_date`), so KR asserts
exact equality on it (`KR_BOUNDED_REVIEW_SESSION_MISMATCH`).

US has no such field — `us_symbol_market_review` derives `operational_date_kst`
from the observation instant in `Asia/Seoul` — and the capture's *calendar
distance* from the session is not a defect signal at all: over a weekend it is
legitimately 2–3 days. `test/fixtures/rolling_pointer_snapshot_20260913` is
exactly that shape: a Sunday 2026-09-13 capture of the Friday 2026-09-11
session, and it is correct. So US asserts **coverage, not elapsed days**: the
newest session present in the capture's `alpaca.daily_bars` must *be* the
session the packet claims. A capture already holding a later session would
evaluate an older session using data that includes later trading (on 2026-09-18
a session-2026-09-16 packet would be built from a capture whose newest bar is
2026-09-17); a capture that stops earlier does not reach the session at all.
Both are refused with `US_BOUNDED_REVIEW_SESSION_MISMATCH`, and a capture with
no bars at all with `US_MARKET_DATA_NO_SESSION_BARS`.

## Idempotency, chunks, resume

`generation_id = sha256(market, session_date, sorted input file hashes)`.
Rows are built in chunks (`chunk_size`: KR 500, US 2000) under
`<work_dir>/chunks/<index>-<hash>.json`; `progress.json` records completed
chunks per generation. A rerun with unchanged inputs reuses every chunk and
returns `verified_existing`; an interrupted run (`--max-chunks` in tests)
resumes and yields byte-identical `packet.json`. Changed inputs → new
generation (old chunks ignored, never reused). What happens to an already
persisted packet of that session then depends on **where** it is: in a scratch
or rebuild output directory it is replaced (`superseded_generation`), while
inside this repository the supersede is **refused**
(`COMMITTED_PACKET_SUPERSEDE_REFUSED`) — committed evidence is append-only,
scratch space is not. The generation hashes rolling inputs (stage history, the
bounded review pointer, the KRX watchlist), so without that scope distinction a
scheduled rerun would rewrite a committed packet the first time any pointer
moved. An existing packet of the same generation with different bytes →
`EXISTING_PACKET_DRIFT_OR_TAMPER`. The packet's `generated_at` is the newest
source timestamp (never the wall clock); the lookup time is kept in
`<work_dir>/run_receipt.json`.

Persisted files: `packet.json` (or `packet.json.gz` with `--compress`) and a
small `summary.json` sidecar. Default output roots:
`data/observations/korea_population_symbol_observation/<session>/` and
`data/observations/us_population_symbol_observation/<session>/`. `--reverify`
re-reads a persisted packet in a fresh process and checks packet hash,
row/status consistency, authority flags and the sidecar.

## CLI

```
python3 decision/population_symbol_observation.py --market KR --generated-at 2026-09-13T05:35:20Z --compress --summary-only
python3 decision/population_symbol_observation.py --market US --generated-at 2026-09-13T05:35:20Z --compress --summary-only
python3 decision/population_symbol_observation.py --market KR --reverify --output-dir data/observations/korea_population_symbol_observation/2026-09-10
```

## Boundaries

Read-only. No fixed symbol list or stage tag excludes a population symbol
from observation; no observation promotes a symbol. No threshold, pass rule,
scheduler, network call, order, or authority. Collection/retention of source
data (Codex source owner) and inclusion evidence / promotion policy (Stage3
owner) are outside this packet; `policy_undefined` names what they still owe.

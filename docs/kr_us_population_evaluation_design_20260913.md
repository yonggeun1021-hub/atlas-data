# KR/US 전체 모집단 평가 연결 — 첫 구현 설계 (2026-09-13)

기준 원격: `atlas-data` `origin/main` `b1e904ce` 이후(2026-09-13). 조사 근거는 PR #697(`discovery/market_candidate_discovery_lookup.py`, `docs/market_candidate_discovery_portal_mapping.md`)과 Codex PR #680/#681/#682를 재사용했고, 여기서는 제한 지점과 재사용 함수만 추가로 추적했다.

범위: 기존 개별 종목 평가기(`decision/korea_symbol_market_review.py`, `decision/us_symbol_market_review.py`)를 전체 모집단에 재사용하는 **읽기 전용 관측 계층**. 새 평가 기준·스코어·임계값·스케줄러·주문은 만들지 않는다. 포털, 코인 생산 경로, KR Stage3 수신기(`regime/krx_information_system_capture.py` 계열), #189, #697 본체는 수정하지 않는다.

---

## 1. 현재 경로와 제한 지점 (KR 3 · US 2)

```
Notion "PM Watchlist"(SSOT) ──collectors/common.py::_from_notion (Atlas Stage 태그)──▶ data/stage_history.json[latest_date][symbol].stage
   │  (config/universe.json 은 Notion 실패 시 fallback, KR 5종목)
   ├─ KR: collectors/krx.py::collect_payload (pykrx, 종목별 OHLCV+투자자 수급) ─▶ data/latest_krx.json.stocks(7) ─▶ data/briefing/krx/<symbol>.json (7개, sma20·confirmed_row)
   └─ US: collectors/free_market_data.py::fetch_alpaca_daily_bars(contract.alpaca.symbols=18 고정) ─▶ data/latest_free_market_data.json.alpaca.daily_bars
                     │
                     ▼
config/korea_symbol_market_review_contract.json  supported_pipeline_subjects = ["012450","298040","329180"]
config/us_symbol_market_review_contract.json     supported_pipeline_subjects = ["TSM","SNDK"], symbol_leadership_proxies{TSM,SNDK}
                     │
                     ▼
korea_symbol_market_review._compact_source(market, stages, contract, briefing_root)   ← 제한 지점 A
us_symbol_market_review._compact_source(market, stages, contract)                      ← 제한 지점 B
```

| 지점 | 파일:함수 | 제한 내용 (확인된 코드) |
| --- | --- | --- |
| A-1 | `decision/korea_symbol_market_review.py::_compact_source` (`for symbol in contract["supported_pipeline_subjects"]`) | 대상 = 계약의 고정 3종목. 각 종목은 `stage_history` 최신일에 `stage`가 **문자열**이어야 하고(`PIPELINE_SUBJECT_MISSING`), `data/briefing/krx/<symbol>.json`이 `status=ok`·`latest_confirmed_row.confirmed=true`·`source_sha256`을 가져야 한다. 034020·267260·000660·005930은 파일은 있으나 `stage=null`이라 대상이 될 수 없다. |
| A-2 | `korea_symbol_market_review.py::_symbol_reviews` | `sma20`·`외국인합계`·`기관합계`·`개인`이 없으면 `_decimal(...)`이 fail-closed → 결측 상태 분기가 없다(US와 달리 `missing_price_state` 없음). |
| B-1 | `decision/us_symbol_market_review.py::_compact_source` | 대상 = 계약 고정 2종목 + `stage` 문자열 필수. 가격은 `alpaca.daily_bars`를 계약 종목으로만 grouping — SNDK는 free_market_data 계약 18종목 밖이라 `UNAVAILABLE`. |
| B-2 | `collectors/free_market_data.py::fetch_alpaca_daily_bars` | 종목별 `GET /v2/stocks/{symbol}/bars` 1회, 180일·limit 240, IEX feed. 종목 목록은 `config/free_market_data_contract.json.alpaca.symbols`(18개, `ALL_CONFIGURED_SYMBOLS_PRESENT`)와 `test/test_free_market_data.py`가 고정. |
| C | `collectors/common.py` | 대상 선정의 정본은 Notion PM Watchlist의 Atlas Stage 태그(14행, stage 부여 5). 코드에는 선정 규칙이 없다 → **모집단 확장은 "선정"이 아니라 "관측 범위 확장"으로만 설계**해야 한다. |

결론: "KR 3·US 2"는 평가기의 계산 한계가 아니라 (1) 계약의 고정 대상 목록, (2) 종목별 자료가 워치리스트 종목에만 수집됨, (3) KR 평가기의 결측 분기 부재의 결합이다.

## 2. 기존 평가기 재사용 가능성 (구분: 대상 선정 / 자료 / 정책)

### 2-1. 대상 선정
- 재사용: 모집단 정의는 이미 있다 — KR `data/observations/krx_global_universe/<date>/packet.json` (`asset_master.records[].primary_symbol`, 2766, `membership_semantics=exact_trading_date_source_coverage_not_investable`), US `data/observations/us_global_universe/<date>/packet.json` (`packet.source_attribute_rows[]`, 13214).
- 선정 규칙 없음: 모집단 전체를 "관측 대상"으로 두고 종목마다 상태를 남긴다. 워치리스트(Notion)는 그대로 `pipeline_subject` 표식으로만 결합한다(#697 `candidate_inclusion`과 동일).
- 이미 구현된 스크리닝(인계·재사용): `universe/krx_investable_registry.py`(KIS 마스터 4,390 → `CATEGORICAL_CANDIDATE 3,415 / EXCLUDED 944 / UNKNOWN 31`, per-record는 private evidence) + PR #681 공개 집계 패킷. 이 계층은 **재구현하지 않고 집계만 결합**한다.

### 2-2. 가격·유동성·섹터 자료
| 자료 | KR | US |
| --- | --- | --- |
| 모집단 일별 가격·거래대금·시총 | **있음(제한적)**: `evidence/regime/kr_information_system/<pub_date>/source-capture/responses/<YYYYMMDD>-{KOSPI,KOSDAQ}-stock.json` (KRX 정보데이터시스템 전종목 응답, 행당 `ISU_SRT_CD, TDD_CLSPRC, FLUC_RT, ACC_TRDVOL, ACC_TRDVAL, MKTCAP, LIST_SHRS, SECT_TP_NM`; 현재 `20260910`, `20260911` 2세션만 커밋, 생산은 `korea-market-signals.yml` `workflow_dispatch` 수동, Codex KR Stage3 소유). 정규화 스키마 `pykrx_1_2_8_stock_required_projection/1`(`regime/krx_information_system_capture.py::_raw_projection`: close, market_cap, trading_value). | **없음**: 디렉터리(`nasdaqlisted/otherlisted`)에 가격 없음. IEX 일봉은 계약 18종목뿐(2,250행). |
| SMA20 등 이력 지표 | 워치리스트 7종목만(`data/briefing/krx/*.json`, pykrx 기준). 모집단은 2세션뿐이라 **계산 불가(NOT_COMPUTABLE:RETAINED_SESSIONS<20)** | 18종목만(180일 창) |
| 투자자 수급 | 워치리스트 7종목만(pykrx `net_value`). 모집단 수급 원천 없음 | 원천 자체 없음(`latest_sec.json.supply_demand_status`) |
| 유동성 | 모집단 `ACC_TRDVAL`·`MKTCAP` 관측값은 있음. **임계값 정책 없음**(`krx_global_universe.policy_status.liquidity_policy=UNRATIFIED`) | 없음(`us_investable_registry_contract.liquidity.repository_default_policy=ABSENT`) |
| 섹터 | KOSDAQ `SECT_TP_NM`(소속부, 산업 아님)만 응답에 있음. KIS 마스터 `sector_large/medium/small`(지수업종 대/중/소)은 `krx_investable_registry._read_master` 레이아웃에 **파싱되지만 `records`에 보존되지 않음**(private) | 워치리스트 7 CIK의 `sic_description`(`collectors/sec.py`, submissions API) 있음. 모집단 SIC 없음 |

### 2-3. 정책 미확정(코드로 만들지 않음, `미정` 유지)
`FINAL_KOREA_REGIME_POLICY_PENDING`, `FINAL_US_REGIME_NOT_AVAILABLE`, KRX `investable_universe/liquidity/listing_delisting/tradability/theme_taxonomy` UNRATIFIED, US `security_type/listing/delisting/liquidity/source_hierarchy/tradability/investable_universe` UNRATIFIED, 통과 규칙, Stage 전이 규칙. 모두 #697 `next_step_conditions`에 이미 기록된 코드를 그대로 쓴다.

### 2-4. 함수 단위 재사용 판정
| 함수 | 재사용 가능? | 조건 |
| --- | --- | --- |
| `korea_symbol_market_review._five_axis`, `_validate_market_packet`, `load_contract`, `validate_output` | 그대로 | 시장 5축은 종목 수와 무관 |
| `korea_symbol_market_review._symbol_reviews` | **분리 후 재사용** | 종목 1건을 만드는 `_symbol_row(symbol, observed, stage_as_of, contract)`로 추출하고, `observed`에 `sma20`/수급이 없을 때의 결측 분기를 추가(계약 `entry_policy.missing_price_state`·`missing_flow_state` 신설, 값은 US 계약과 같은 `BLOCKED`) |
| `us_symbol_market_review._symbol_reviews` | **분리 후 재사용** | 이미 `price UNAVAILABLE` 분기가 있음. 종목 루프와 `symbol_leadership_proxies[symbol]` 조회를 `contract.get(...)`로 완화(프록시 없는 종목은 `leadership_proxies: NOT_DECLARED`) |
| `us_symbol_market_review._axes`, `_session_return` | 그대로 | |
| `universe/us_investable_registry.evaluate_registry` | 그대로 | 모집단 fail-closed 사실 평가기. 입력 `us_investable_snapshot/1`은 PR #682 `_us_investable_input_readiness`의 `field_source_matrix` 매핑을 재사용해 디렉터리 사실만 채우고 나머지는 결측으로 넘김 → 레코드마다 `UNKNOWN`+누락 사실 코드가 남음 |
| `regime/krx_information_system_capture._raw_projection`, `normalize_projection` | 읽기 전용 재사용 | 응답 바이트 → `{code, close, market_cap, trading_value}` 프레임. 파일은 수정하지 않음 |
| `discovery/three_market_evaluation_coverage.build_report` (#682) | 교차검증 | 모집단 수·bounded 수 일치 확인 |
| `discovery/market_candidate_discovery_lookup` (#697) | 소비자 | 새 패킷을 `screening_layer`/`data_acquired.population_level_symbol_data` 입력으로 연결(후속 PR, #697 본체 변경 아님) |

## 3. 지금 평가 가능한 범위 vs 자료 부족 범위 (2026-09-13 커밋 기준)

| 시장 | 계층 | 종목 수 | 상태 코드(설계) | 근거 |
| --- | --- | --- | --- | --- |
| KR | 워치리스트 + stage 부여 + 확정 가격·수급·SMA20 | 3 | `EVALUATED_BOUNDED` (기존과 동일) | 계약 대상, `data/briefing/krx` |
| KR | 워치리스트, stage 미부여, 파일 있음 | 4 (000660, 005930, 034020, 267260) | `EVALUABLE_PRICE_FLOW_SMA20 / PIPELINE_STAGE_NOT_ASSIGNED` | 파일 존재, `stage=null` |
| KR | 모집단, 세션 가격·거래대금·시총 있음 | 2026-09-10 응답: 2766/2766 (워치리스트 7 제외 2759); 2026-09-11 응답: 2765/2766 | `EVALUABLE_SESSION_PRICE_ONLY` + `SMA20_NOT_COMPUTABLE:RETAINED_SESSIONS=2`, `INVESTOR_FLOW_NOT_AVAILABLE` | 정보시스템 응답 2세션 (실제 교차검증: 20260910 행 2766·고유 2766·모집단 일치 2766·고아 0; 20260911 행 2765·모집단 누락 1) |
| KR | 모집단인데 응답에 없음 / 응답에 있는데 모집단에 없음 | 2026-09-11 세션: 누락 1 (`472220`), 고아 0 | `NOT_EVALUABLE:SOURCE_ROW_MISSING` / `ORPHAN_SOURCE_ROW` | 실제 사례가 이미 존재 → 상태 코드 필수 |
| KR | 소속부 사실(응답 `SECT_TP_NM`, KOSDAQ만) | 관리종목 131 · SPAC 66 · 투자주의환기 40 · 중견 503 · 우량 427 · 벤처 339 · 기술성장 252 (20260910) | `market_segment` 사실 기록(규칙 아님) | KIS 스크리닝 제외 범주(관리종목·SPAC·투자주의)와 대응 가능한 공개 사실 |
| US | 워치리스트 + stage + IEX 가격 | 1 (TSM) | `EVALUATED_BOUNDED` | |
| US | 워치리스트 + stage, 가격 없음 | 1 (SNDK) | `NOT_EVALUABLE:PRICE_SOURCE_NOT_CONFIGURED` (계약 18종목 밖) | B-2 |
| US | 워치리스트 stage 미부여, IEX 가격 있음 | 2 (MSFT, NVDA) | `EVALUABLE_PRICE / PIPELINE_STAGE_NOT_ASSIGNED` | 계약 18종목 포함 |
| US | 워치리스트 stage 미부여, 가격 없음 | 3 (ANET, CRDO, MU) | `NOT_EVALUABLE:PRICE_SOURCE_NOT_CONFIGURED` | |
| US | 모집단 나머지 | 13214 − 7 | `NOT_EVALUABLE:PRICE_SOURCE_NOT_RETAINED` + `us_investable_registry` 결과(`UNKNOWN`, 누락 사실 코드) | 디렉터리 사실만 |
| US | ETF 15종(디렉터리 ETF=Y ∧ IEX 일봉) | 15 | `EVALUABLE_PRICE / ETF_TYPE_PROVEN` (PR #682 `first_bounded_source_aligned_target`) | 재사용 |

"평가 가능"은 관측 사실 생성 가능을 뜻하며 통과 판정이 아니다(통과 규칙 미정). 모든 종목은 위 상태 중 정확히 하나를 갖는다.

## 4. 구현안 (첫 PR 범위)

### 4-1. 파일 범위
| 구분 | 파일 | 변경 |
| --- | --- | --- |
| 수정 | `decision/korea_symbol_market_review.py` | `_symbol_reviews` → `_symbol_row(...)` 추출 + 결측 분기. 기존 3종목 출력 **byte-identical 유지**(회귀 테스트로 고정) |
| 수정 | `decision/us_symbol_market_review.py` | `_symbol_reviews` → `_symbol_row(...)` 추출, 프록시 미선언 분기 |
| 수정 | `config/korea_symbol_market_review_contract.json` | `entry_policy.missing_price_state="BLOCKED"`, `missing_flow_state="BLOCKED"` 추가만. `supported_pipeline_subjects`는 **변경하지 않음**(대상 선정 규칙 아님) |
| 신규 | `decision/korea_population_symbol_observation.py` | KR 모집단 관측 어댑터(아래 4-3) |
| 신규 | `decision/us_population_symbol_observation.py` | US 모집단 관측 어댑터 |
| 신규 | `config/population_symbol_observation_contract.json` | 상태 코드 열거, 입력 스키마 버전, 청크 크기, authority all-false |
| 신규 | `test/test_korea_population_symbol_observation.py`, `test/test_us_population_symbol_observation.py`, `test/test_symbol_review_row_extraction.py` | 아래 4-6 |
| 수정 | `run_all.py` | 위 테스트 등록 + 예상 시간 |
| 신규 | `docs/population_symbol_observation_contract.md` | 계약 문서 |
| 수정 금지 | `regime/krx_information_system_capture.py`, `.github/workflows/korea-market-signals.yml`, `collectors/*`, `universe/krx_investable_registry.py`, `discovery/three_market_evaluation_coverage.py`, `discovery/market_candidate_discovery_lookup.py`, `briefing/rotation_candidate_selection_*`, 코인 경로 | 읽기만 |

### 4-2. 입력 계약 (`population_symbol_observation_input/1`)
```
{
  "market": "KR" | "US",
  "as_of_session_date": "YYYY-MM-DD",                      # 원천 세션일(조회일 아님)
  "population_packet_path": ".../krx_global_universe|us_global_universe/<date>/packet.json",
  "bounded_review_path": "data/latest_{korea|us}_symbol_market_review.json",
  "stage_history_path": "data/stage_history.json",
  "price_sources": {
    "KR": {"information_system_responses": ["evidence/regime/kr_information_system/<pub>/source-capture/responses/<YYYYMMDD>-KOSPI-stock.json", "...-KOSDAQ-stock.json"], "manifest_path": ".../source-capture/manifest.json", "watchlist_files_root": "data/briefing/krx"},
    "US": {"free_market_data_path": "data/latest_free_market_data.json", "raw_snapshot_dir": "evidence/us_breadth/raw/<date>"}
  },
  "screening_layer": {"KR": "data/observations/krx_registry_evaluation_coverage/<date>/packet.json | null"},
  "generated_at": "UTC"                                     # 조회 시각, 원천 날짜를 덮어쓰지 않음
}
```
검증: 모든 입력은 각자의 자체 해시/검증기로 재검증(`payload_sha256`, `manifest.records[].response.sha256` ↔ 파일 sha256, `validate_output`). 세션일이 모집단 `effective_interval` 밖이면 `SOURCE_SESSION_OUTSIDE_POPULATION_INTERVAL`로 fail-closed(조회일이 아니라 원천끼리의 정합).

### 4-3. 처리 (어댑터 — 새 평가 기준 없음)
1. 모집단 열거: KR `asset_master.records[]` → `primary_symbol`(ISU_SRT_CD), US `source_attribute_rows[]` → `primary_symbol`. 중복 심볼은 `DUPLICATE_PRIMARY_SYMBOL`로 별도 목록(#697 `duplicate_primary_symbols`와 동일 기준).
2. 자료 결합:
   - KR: 정보시스템 응답 행을 `ISU_SRT_CD`로 조인(`_raw_projection` 프레임 재사용). 워치리스트 파일이 있으면 `data/briefing/krx/<symbol>.json`의 `latest_confirmed_row`·`confirmed_metrics.sma20`·`net_value`를 우선 사용(기존 3종목과 동일 경로). 응답에 있으나 세션일이 다르면 `SESSION_DATE_MISMATCH`.
   - US: `alpaca.daily_bars`를 symbol로 grouping(기존 `_compact_source` 로직 재사용). 디렉터리 사실(`ETF`, `Test Issue`, `Financial Status`)을 #682 `field_source_matrix` 매핑으로 `us_investable_snapshot/1` 레코드에 넣고 `us_investable_registry.evaluate_registry`를 호출해 레코드별 결과·누락 사실 코드를 보존.
3. 종목 행 생성: `_symbol_row(...)`(추출 함수) 호출. 계산 가능한 `observed_facts`만 기록(`PRICE_ABOVE/BELOW_20_DAY_AVERAGE`는 sma20 있을 때만, 수급 사실은 수급 있을 때만). 결측은 `price_context.status ∈ {OBSERVED_CONFIRMED, OBSERVED_SESSION_ONLY, UNAVAILABLE}`, `flow_context: null` + `flow_status` 코드.
4. 상태 부여(3장의 열거형 그대로). `entry_review.state`는 계약의 기존 정책값(`WAIT`/`BLOCKED`)만 사용, `reasons`는 기존 코드 + 결측 코드(`SMA20_NOT_COMPUTABLE:RETAINED_SESSIONS=<n>`, `INVESTOR_FLOW_NOT_AVAILABLE`, `PRICE_SOURCE_NOT_CONFIGURED`, `PRICE_SOURCE_NOT_RETAINED`, `PIPELINE_STAGE_NOT_ASSIGNED`).
5. 집계·정합: `population_count == Σ status_counts`, `evaluated_bounded_symbols == bounded_review.symbols`(byte 비교), `#682 universe_count` 일치, 워치리스트 `stage` 값은 `stage_history` 최신일과 동일.

### 4-4. 출력 계약 (`population_symbol_observation_packet/1`)
```
{
  "schema_version": "population_symbol_observation_packet/1",
  "market": "KR", "as_of_session_date": "2026-09-11", "generated_at": "...",
  "population": {"count": 2766, "population_id": "P3.03.KRX.20260910", "source": {path,file_sha256,packet_sha256}},
  "sources": {...각 입력의 path/sha256/세션일...},
  "status_counts": {"EVALUATED_BOUNDED": 3, "EVALUABLE_PRICE_FLOW_SMA20": 4, "EVALUABLE_SESSION_PRICE_ONLY": n, "NOT_EVALUABLE": {"SOURCE_ROW_MISSING": m, ...}},
  "reason_counts": {...},
  "symbols": [ {symbol, name, market_membership, pipeline_stage|null, pipeline_as_of, observation_status, price_context, flow_context|null, sma20_status, liquidity_observation{trading_value, market_cap}|null, market_segment(SECT_TP_NM)|null, entry_review{state, reasons}, screening_state(KR: private 집계만 → null + note), evidence_refs[]} ... ],   # 모집단 전 종목, 정확히 1행씩
  "policy_undefined": [...#697 next_step_conditions 코드...],
  "reconciliation": {"population_equals_status_sum": true, "bounded_rows_byte_identical_to_review": true, "coverage_universe_count_match": true, "duplicate_symbols": [], "orphan_source_rows": []},
  "state_lifetime": {"snapshot_only": true, "automatic_carry_forward": false, "reevaluation_required": true},
  "authority": {모두 false},
  "payload_sha256": "..."
}
```
저장: `data/observations/{korea|us}_population_symbol_observation/<as_of_session_date>/packet.json` + 동일 디렉터리 `chunks/`(아래). 크기: KR ≈ 2,766행·행당 ≈ 600B ≈ 1.7MB, US ≈ 13,214행 ≈ 8MB — `run_all` byte-identical 대상에 넣지 않고 관측 패킷으로만 커밋(기존 `data/observations/*` 관례).

### 4-5. 중복 방지·증분 재개
- 생성 ID = `sha256(market, as_of_session_date, 모든 입력 file_sha256 정렬)`. 같은 ID의 패킷이 있고 재빌드 결과가 byte-identical이면 `verified_existing`(쓰기 없음), 다르면 `EXISTING_PACKET_DRIFT_OR_TAMPER`로 fail-closed(PR #189 `populate()` 관례 재사용).
- 청크 처리: 심볼 정렬 후 고정 크기(KR 500, US 2000)로 분할, `chunks/<index>-<sha256(symbols)>.json` + `progress.json{generation_id, completed:[...]}`. 재실행 시 `progress.json.generation_id`가 같고 청크 파일 sha256이 일치하면 건너뜀. 입력이 바뀌면 새 generation → 이전 청크는 무시(삭제하지 않음).
- 심볼 단위 idempotency: 행 키 = `(market, primary_symbol)`; 조립 시 중복 키는 실패. 응답 행이 모집단에 없으면 `orphan_source_rows`에만 기록(행 생성 금지).
- 동시 실행: `progress.json`은 원자적 rename으로만 쓰고, generation_id가 다른 진행 파일이 있으면 중단(`CONCURRENT_GENERATION_IN_PROGRESS`).

### 4-6. 검증 기준
1. 추출 회귀: `_symbol_row` 도입 후 `data/latest_korea_symbol_market_review.json`·`..._us_...`가 **byte-identical**로 재생성됨(기존 `test_korea_symbol_market_review.py`·`test_us_symbol_market_review.py`에 1건씩 추가).
2. 결측 분기: sma20 없음 / 수급 없음 / 가격 없음 / stage null 각각 합성 fixture로 상태·이유 코드 검증, `automatic_entry_generated=false`, `order_draft=null` 유지.
3. 실제 입력: KR 2026-09-11 응답(2세션)·US 2026-09-11 디렉터리로 `population_count == Σ status_counts`, 워치리스트 행이 bounded review와 동일, #682 `universe_count` 일치, 중복·고아 목록 0(또는 실제 값 기록).
4. 변조: 응답 파일 1바이트 변조 → manifest sha 불일치로 fail-closed; 패킷 재서명 변조 → drift 감지.
5. 증분: 청크 2개 완료 후 중단 → 재실행 시 완료 청크 skip, 최종 패킷 sha256이 무중단 실행과 동일. 입력 변경 후 재실행 → 새 generation, 이전 청크 미사용.
6. 권한: 모든 `*_authorized=false`, 네트워크 호출 0(테스트에서 `urllib` 금지 확인), 코인·Stage3 파일 diff 0.
7. `ATLAS_DISPOSABLE_CHECKOUT=1 python3 run_all.py --authoritative` 구조 단계 byte-identical 14/14, CI actions-pass-full.

### 4-7. 착수 순서
1. `_symbol_row` 추출 + 회귀 고정(작은 PR, 출력 불변).
2. KR 어댑터 + 테스트(2세션 자료로 `EVALUABLE_SESSION_PRICE_ONLY` 확정).
3. US 어댑터 + `us_investable_registry` 결합 + 테스트.
4. #697 소비자 연결(별도 PR): `data_acquired.population_level_symbol_data`를 새 패킷 집계로 교체 -- 구현됨 (`discovery/market_candidate_discovery_lookup.py::_population_level_symbol_data`, `docs/market_candidate_discovery_lookup_contract.md`의 "KR/US `data_acquired.population_level_symbol_data`" 절 참고). `summary.categories.unevaluated`를 `NOT_EVALUABLE` 사유별로 세분하는 부분은 아직 미착수.

## 5. 편입 이유·종목→섹터 연결 — 기존 증거 경로

| 항목 | 기존 증거 | 연결안 | 상태 |
| --- | --- | --- | --- |
| 편입 이유 | Notion PM Watchlist "편입 사유" 속성(산문). `config/rules.candidates.json`은 이를 `DRAFT_UNRATIFIED` 이관 증거로 **제외**(규칙 아님) | `collectors/common.py::_from_notion`이 이미 읽는 행에 `inclusion_note`(원문·수집시각·row id)를 **증거 필드로만** 추가 → `stage_history`가 아닌 별도 `data/observations/watchlist_inclusion_note/<date>/packet.json`. 규칙·판정에 쓰지 않음 | 근거 있음(Notion 산문), CIO 결정 필요(공개 저장 여부). 이번 PR 범위 밖 |
| KR 종목→섹터 | (a) KIS 마스터 `sector_large/medium/small`: `krx_investable_registry._read_master` 레이아웃에 파싱되나 `records`에 미보존(private). (b) 정보시스템 응답 `SECT_TP_NM`(KOSDAQ 소속부, 산업 아님). (c) `korea_leadership_context`는 지수 단위 | (a) private 레코드에 3필드 보존 → 공개는 `korea_rotation_sector_identity_taxonomy_binding`(RATIFIED, 지수 섹터)과의 매핑이 비준될 때까지 해시·집계만. (b) 모집단 행의 `market_segment`로 사실 그대로 기록 | (a) 경로 있음·보존 개발 필요(registry 소유자), 비준 미정 (b) 즉시 가능 |
| US 종목→섹터 | `collectors/sec.py` `sic_description`(워치리스트 7 CIK, submissions API) | US 모집단 행에 `sic_description`을 워치리스트 종목만 채우고 나머지는 `NO_EVIDENCE`; 모집단 SIC는 `company_tickers.json`+submissions 13k 호출이라 별도 결정 | 7종목 즉시 가능, 모집단은 개발·비용 결정 필요 |
| 로테이션 연결 | `rotation/rotation_state_ledger.py`는 종목 단위 항목 없음 | 연결 없음으로 유지 | 근거 없음 |

## 6. 인계·재사용 대상 (이미 구현된 것, 재작업 금지)
- PR #680/#682 `discovery/three_market_evaluation_coverage.py`: 집계 영수증·US `investable_input_readiness`(디렉터리→사실 매핑) → 그대로 import.
- PR #681 + `universe/krx_investable_registry.py` + `.github/workflows/krx-investable-registry.yml`: KR 스크리닝 4,390(private per-record) → 공개 집계만 결합, 섹터 3필드 보존은 소유자 인계 항목.
- `regime/krx_information_system_capture.py`, `korea-market-signals.yml`(Codex KR Stage3): 전종목 응답 생산·보존. **일일 자동 보존은 현재 없음(workflow_dispatch)** → 세션 누적(SMA20용 20세션)은 Stage3 담당과 조율 항목. 이 설계는 커밋된 응답만 읽는다.
- `origin/codex/kr-retained-pit-population-20260913` `regime/kr_retained_historical_population.py`: 시장 5축 PIT 모집단(종목 단위 아님) → 세션 달력·가용성 검증 유틸(`_calendar_rows`, `_validate_availability`) 재사용 후보.
- `universe/us_investable_registry.py`: US 모집단 fail-closed 평가기 → 그대로 호출.
- `decision/common_paper_candidate_funnel.py`: 이후 단계(점수·게이트) 리듀서. 이번 설계의 출력은 그 입력이 아니며, 점수 컴포넌트를 만들지 않는다.

## 7. 명시적 미정·비범위
- 통과 규칙, 유동성·투자가능 정책, 섹터 분류 비준, Stage 전이 규칙: `미정` 유지.
- 정보시스템 응답 일일 보존, KIS 레코드 섹터 보존, Notion 편입 사유 공개 저장, US 모집단 SIC 수집: 소유자·CIO 결정 후 착수.
- 새 수집기·스케줄러·임계값·주문: 없음.

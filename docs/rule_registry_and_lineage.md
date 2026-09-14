# 확정 규칙 대장과 판단 계보 (v1, 2026-09-15)

근거: 사용자 확정 `USER_RATIFICATION_RULE_GOVERNANCE_EVIDENCE_GATED_ADJUSTMENT_20260915.json`
("확정 규칙 대장 + PAPER·REAL 거래 전체에 대한 규칙별 성적표"), CLAUDE_CIO P1 지시.

## 한 줄 요약

- **규칙 대장** `config/rule_registry_v1.json`: 사용자가 확정한 규칙 18개(그중 1개는 정정으로 대체된 `SUPERSEDED`)와, 확정 기록이 "이번에 확정하지 않는다"고 명시한 항목 5개(`PENDING_USER_DECISION`)를 고정 ID로 적었습니다. 줄마다 원본 기록(바이트 그대로 복사본과 sha256), 효력 시각, 기계가 읽는 핵심 값, 결정 당시 근거 수준, 사전 등록 트리거, 성적표 유형, 최소 표본이 있습니다.
- **판단 계보** `governance/rule_refs.py`: 판단 하나마다 "어떤 규칙이 적용·차단·크기·청산했는지"를 `rule_refs`로 남기는 형식과 검증기입니다.
- **지금 연결한 곳**: 코인 PAPER 판단 스냅샷(종목별 신선도 관문, 종목별 유동성 하한 관문, 후보 상태)과 PAPER 시장 위험 참고값. 둘 다 **판단 패킷도, 생산자 코드도 1바이트도 바꾸지 않고**, 생산자가 패킷을 쓴 뒤 별도 단계가 옆에 계보 파일(사이드카)을 씁니다.

규칙 대장은 권한이 아닙니다. 주문·자금·REAL 권한을 주지 않으며, 값의 권위는 언제나 원본 기록에 있습니다.

## 1. 규칙 대장 23줄

| 규칙 ID | 버전 | 상태 | 효력 시각(UTC) | 원본 기록 (sha 앞 8자리, +보조 기록 수) | 사전 등록 트리거 | 성적표 유형 | 최소 표본 | 결정 당시 근거 |
|---|---|---|---|---|---|---|---|---|
| RULE.ALLOCATION.V2 | 2 | 확정 | 2026-09-13 14:58 | 시장별 배분 v2 (345801ab) +CIO 보충 1 | 없음(사용자 확정 대기) | allocation | 미정 | 기록에 없음 |
| RULE.HEDGE.INVERSE.V1 | 1 | 확정 | 2026-09-13 14:46 | 적극 인버스 헤지 (b24b38a3) +CIO 보충 1 | 사건 연구 후 1회 통제 수정 | hedge | 미정 | 초기 기본값(미검증) |
| RULE.LIQUIDITY.KRUS.V1 | 1 | 확정 | 2026-09-13 15:35 | 한국·미국 유동성 (1e068439) | 없음(사용자 확정 대기) | liquidity | 미정 | 추정만 |
| RULE.CRYPTO.RUNTIME.V1 | 1 | 확정 | 2026-09-13 17:00 | 코인 시장판정 런타임 (e2f9f694) +CIO 보충 1 | RISK_VOL 1회 통제 수정 / 네 상태 관찰 뒤 재검토 / 검증 실패 시 UNKNOWN 자동 복귀 | gate | 미정 | 잠정 전방 승인 |
| RULE.CRYPTO.FRESHNESS.PER_MARKET.V1 | 1 | 확정 | 2026-09-13 23:25 | 코인 신선도 종목별 (043932a4) +CIO 보충 2 | 없음(사용자 확정 대기) | data-source | 미정 | 기록에 없음 |
| RULE.US.SESSION_CALENDAR.V1 | 1 | 확정 | 2026-09-14 08:25 | 미국 거래일 달력 출처 (50259daf) | 없음(사용자 확정 대기) | data-source | 미정 | 기록에 없음 |
| RULE.CRYPTO.TAXONOMY.ADD_20260914 | 1 | 확정 | 2026-09-14 08:35 | 코인 분류 추가 (6ff7f486) | 없음(사용자 확정 대기) | data-source | 미정 | 기록에 없음 |
| RULE.ROTATION.CRYPTO.V1 | 1 | 확정 | 2026-09-14 15:05 | 자금 이동 판정 규칙 (c6f5dbbe, 시각 수정본) | 확정 사건 10건 후 전방 검토 | entry | 10건 | 기록에 없음 |
| RULE.ROTATION.US.V1P | 1 | **잠정** | 2026-09-14 15:05 | 같은 기록 | 1년 백필 후 재연구 | entry | 미정 | 기록에 없음 |
| RULE.ROTATION.KR.V1T | 1 | **임시** | 2026-09-14 15:05 | 같은 기록 | KRX 업종지수 이력으로 재검증할 때까지 | entry | 미정 | 기록에 없음 |
| RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1 | 1 | 확정 | 2026-09-14 15:05 | 같은 기록 | 없음(사용자 확정 대기) | gate | 미정 | 기록에 없음 |
| RULE.ROTATION.RELEASE_HANDLING.V1 | 1 | 확정 | 2026-09-14 15:05 | 같은 기록 | 없음(사용자 확정 대기) | exit | 미정 | 기록에 없음 |
| RULE.GOVERNANCE.EVIDENCE_GATED.V1 | 1 | 확정 | 2026-09-14 15:40 | 근거 기반 재조정 (c3f1e78c) | 없음(사용자 확정 대기) | governance | 미정 | 기록에 없음 |
| RULE.ENTRY.PAPER_BASELINE_B.V1 | 1 | 확정 | 2026-09-14 22:10 | 진입 B안 기준선 (b2a905c4) | 없음(사용자 확정 대기) | entry | 미정 | 기준선, 진입 우위 주장 아님 |
| RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1 | 1 | 확정 | 2026-09-14 22:40 | B2·B3·크기 조립 (0e2691e0) | 없음(사용자 확정 대기) | gate | 미정 | 기록에 없음 |
| RULE.KR.FIRST_CYCLE_CANARY_V0.V1 | 1 | 확정 | 2026-09-14 22:40 | 같은 기록 | 없음(사용자 확정 대기) | gate | 미정 | 기록에 없음 |
| RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V1 | 1 | **대체됨** | 2026-09-14 22:40 | 같은 기록 | 없음(사용자 확정 대기) | allocation | 미정 | 기록에 없음 |
| RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V2 | 2 | 확정 | 2026-09-15 00:05 | 세션 크기 문구 정정 (8c07a713) + B2·B3·크기 조립 | 없음(사용자 확정 대기) | allocation | 미정 | 기록에 없음 |

### 세션 크기 정정 (V1 → V2)

오전 문구는 글자 그대로 읽으면 "세션 매수 합계 전체"를 NAV 5%로 묶었습니다(CIO 작성 오류, 정정 기록의 `reason`). 사용자 정정 뒤 V2는 둘로 나눕니다.

- **시장별 세션 매수 합계** ≤ 세션 시작 시 시장 몫 남은 여유의 1/3
- **종목별 누적 보유** ≤ NAV 5% 이면서 ≤ 확정 평균 거래대금의 1% (한국·미국 20세션, 코인 30일 평균)
- 부분 수량·다음 세션 보충·후보 균등 분할·옛 미국 1종목/코인 3종목 한도 대체는 그대로(V1 기록 문구를 두 번째 원본으로 가리킴).

V1 줄은 지우지 않고 `status: SUPERSEDED`, `superseded_by`(V2 ID와 정정 기록 해시)로 남깁니다. V2는 `supersedes`로 V1과 V1 기록 해시를 가리키고 버전은 1 → 2로 올라갑니다. 검증기는 세 가지가 서로 맞지 않으면 거부합니다. `in_force_at(row, 시각, 대장)`은 V1을 2026-09-14 22:40 ~ 2026-09-15 00:05 UTC, V2를 그 뒤로 판정하므로, 성적표는 판단 시각에 효력이 있던 버전으로 셉니다. 대체된 규칙도 그 기간의 판단 계보에서는 인용할 수 있습니다.

### 확정하지 않은 항목 (`PENDING_USER_DECISION`, 값 없음)

| 규칙 ID | 뜻 | 근거(기록의 미확정 목록) | 성적표 유형 |
|---|---|---|---|
| RULE.SIZE.PLANNED_LOSS_CAP.PENDING | 계획손실 한도 (0.25%/0.40%) | B2·B3·크기 조립 기록 + 진입 B안 기록 | allocation |
| RULE.EXIT.PENDING | 청산 규칙 (청산 연구 v2 대기) | 두 기록 모두 | exit |
| RULE.EXECUTION.QUALITY_NUMBERS.PENDING | 실행 품질 숫자 (시간 창, 스프레드, 호가 나이) | B2·B3·크기 조립 기록 | gate |
| RULE.CRYPTO.BTC_ETH_NAME_CAP.PENDING | BTC/ETH 종목 한도 (B8) | 두 기록 모두 | allocation |
| RULE.US.LIQUIDITY_IEX_TREATMENT.PENDING | 미국 유동성 IEX 거래량 처리 (B4) | B2·B3·크기 조립 기록 | liquidity |

이 줄들은 `version: 0`, `key_parameters: {}`, `effective_from: null`이고, `pending_basis`가 기록의 미확정 목록 문구를 글자 그대로 가리킵니다. 검증기는 값이 하나라도 들어가면 거부하고, `rule_refs`에 인용하면(`APPLIED` 등) `RULE_NOT_DECIDED`로 거부합니다. 성적표·포털은 이 줄을 "사용자 결정 대기"로 그대로 보여 주면 됩니다. 진입 B안 기록의 다른 미확정 항목 "세션 예산 문구 통일(실행 계약 정본 대기)"은 CIO 지시 목록에 없어 이번에 넣지 않았습니다.

읽는 법:

- **트리거 "없음(사용자 확정 대기)"**: 원본 기록에 재검토 조건이 적혀 있지 않다는 뜻입니다. 대장에서는 `review_triggers: null`, `trigger_pending_user_confirmation: true`로 둡니다. CIO가 조건을 지어내지 않습니다.
- **효력 시각**: 기록의 `ratified_at_utc`를 그대로 쓰거나, 기록이 한국 시각(`recorded_at_kst`)만 가진 경우 UTC로 바꿨습니다(예: 00:05 KST → 전날 15:05 UTC).
- **최소 표본**: 기록에 수가 있는 규칙은 코인 로테이션(확정 사건 10건) 하나뿐입니다. 코인 런타임의 "연속 5일"은 승인 조건이라 핵심 값에만 넣었고 성적표 최소 표본으로 쓰지 않았습니다.
- **REAL**: 모든 줄에 `modes.REAL`이 있습니다. 기록이 REAL을 바꾸지 않았으므로 대부분 `NOT_AUTHORIZED`이고, 거버넌스 규칙만 "PAPER·REAL 거래 모두 성적표에 올린다"는 기록 문구대로 `APPLIES`입니다.

원본 기록 복사: `evidence/authority/` 아래에 바이트 그대로 복사했습니다. 코인 런타임·코인 신선도·미국 달력 기록은 이미 같은 해시로 들어와 있어서 새로 복사하지 않고 그 파일을 가리킵니다. 공개 저장소에 올리기 전에 비밀값 형태가 없는지 확인했습니다(기록에는 `secret_disclosure: false` 같은 권한 표시와 로컬 경로 문자열만 있음).

## 2. 검증기가 막는 것 (`governance/rule_registry.py`)

`python3 governance/rule_registry.py` → `PASS_RULE_REGISTRY_VALID`

- ID 23개가 정확히 고정 목록과 같고 중복이 없어야 합니다.
- `SUPERSEDED` 줄은 `superseded_by`가 있어야 하고, 그 후속 줄의 `supersedes`가 이 줄과 이 줄의 기록 해시를 정확히 가리켜야 합니다.
- 기록 본문이 `rule_id`를 적고 있으면(진입 B안 기록) 대장 줄의 ID와 같아야 합니다. B2·B3·크기 조립 기록의 결정별 `rule_id`는 핵심 값 `record_rule_id`로 묶었습니다.
- 줄마다 사용자 확정 기록이 첫 번째 원본으로 있어야 합니다. 원본이 없는 규칙은 거부합니다.
- 복사본의 sha256과 바이트 길이가 대장과 같아야 합니다. 1바이트만 달라도 거부합니다.
- 기록 안의 ID(`ratification_id`/`addendum_id`/`id`)가 대장과 같아야 합니다.
- CIO 보충 기록은 같은 규칙의 사용자 확정 기록 sha256을 본문에 담고 있어야 합니다.
- **모든 핵심 값·효력 시각·근거 수준·트리거·최소 표본은 기록 안 위치(JSON pointer)를 가리킵니다.** `EXACT`는 기록 값과 똑같아야 하고, `PARSED_FROM_TEXT`는 인용 문구가 기록 문장 안에 글자 그대로 있어야 합니다. 기록에 없는 트리거를 지어 넣으면 거부됩니다.
- 버전은 같은 계열(`lineage_key`) 안에서 효력 순서대로 올라가야 합니다. `supersedes`의 해시는 새 기록 본문에 적혀 있어야 합니다.
- "구현이 이 기록을 해시로 묶고 있다"(`binds_record_sha256: true`)고 적었으면 그 파일에 실제로 해시가 있어야 합니다.

## 3. 판단 계보 형식 (`governance/rule_refs.py`)

### rule_refs 한 항목

```json
{"rule_id": "RULE.CRYPTO.FRESHNESS.PER_MARKET.V1", "version": 1,
 "registry_sha256": "<대장 파일 sha256>", "source_record_sha256": "043932a4…",
 "role": "BLOCKED_BY"}
```

- `role`: `APPLIED`(적용) · `BLOCKED_BY`(차단) · `SIZED_BY`(크기 결정) · `EXITED_BY`(청산 결정)
- 버전·원본 해시는 대장과 정확히 같아야 하고, 목록은 (rule_id, role) 순 정렬, 같은 쌍 중복 금지입니다.

### rule_lineage_event/1

| 필드 | 뜻 |
|---|---|
| `decision_id` | 판단 하나의 ID. 예: `crypto_paper_decision:<generation_id>:KRW-BTC` |
| `market` / `instrument` | US·KR·CRYPTO / 종목(없으면 null) |
| `timestamp_utc` | 생산자 패킷의 판단 시각 |
| `event_type` | DECISION · BLOCK · ORDER · FILL · EXIT |
| `gate` | 관문 이름. 예: `realtime_freshness_per_market` |
| `outcome` | 생산자가 낸 결과를 그대로 옮긴 값(상태, 사유) |
| `rule_refs` | 위 형식 |
| `unapplied_rules` | **이 판단에 해당하지만 생산자가 아직 실행하지 않는 규칙** + 사유 코드 |
| `inputs_sha256` | 그 관문 입력값의 해시 |
| `source_packet` | 원본 패킷 경로·스키마·payload_sha256 |
| `event_id` | 위 전체의 해시(변조 검출) |

BLOCK 이벤트는 차단한 규칙(`BLOCKED_BY`)을 적거나, 등록 규칙이 실행되지 않았음을 `unapplied_rules`로 밝혀야 합니다.

### 사이드카 rule_lineage_sidecar/1

패킷 하나에서 나온 이벤트 묶음입니다. 경로는 패킷의 `payload_sha256`로 정해집니다.

- 코인 판단: `evidence/rule_lineage/crypto_paper_decision/<UTC 날짜>/<HHMM>/<payload_sha256>.json`
- 시장 위험 참고값: `evidence/rule_lineage/paper_regime_reference/<기준일>/<payload_sha256>.json`

한 번 쓰면 다시 쓰지 않습니다. 같은 파일이 있으면 내용이 똑같은지만 확인하고, 다르면 오류입니다(추가 전용). `decision_outcome_changed`는 항상 `false`입니다.

## 4. 지금 연결된 곳과 아직 안 된 곳 (사실 그대로)

### 코인 PAPER 판단 스냅샷 (`decision/crypto_paper_decision_snapshot.py`)

`upbit-realtime-capture.yml`에 새 단계 `crypto_rule_lineage`를 더했습니다. 판단 단계가 EVALUATED로 끝났을 때, 증거 커밋 바로 앞에서
`python3 governance/rule_lineage_producers.py crypto-decision --packet "$DECISION_PATH"`를 실행합니다(`continue-on-error: true`, 계보 문제로 0이 아닌 종료 코드를 내지 않음). 커밋 단계가 `evidence/rule_lineage/crypto_paper_decision`을 함께 올립니다. 후보 종목마다 이벤트 3개:

1. `realtime_freshness_per_market`: 신선도가 FRESH가 아니면 BLOCK + `BLOCKED_BY RULE.CRYPTO.FRESHNESS.PER_MARKET.V1`, 아니면 DECISION + `APPLIED`.
2. `realtime_liquidity_floor`: 30일 평균 거래대금 하한 제외면 BLOCK + `BLOCKED_BY`, 포함이면 `APPLIED`. (이 하한은 신선도 확정 기록의 CIO 동반 결정과 보충 기록에서 옵니다.)
3. `candidate_state`: 패킷의 후보 상태를 그대로 옮기고, 실제로 상태를 WAIT로 낮춘 경우만 BLOCK.

실제 예: 2026-09-14 21:13 UTC 패킷에서 KRW-WLD는 실시간 STALE → BLOCK으로 기록됩니다. 다만 상태가 원래 WATCH(매수 가능 상태 아님)였으므로 `capped_actionable_state: false`입니다.

2026-09-13 23:25 UTC 이전 패킷(`/1`)은 규칙 효력 전이므로 `rule_refs`가 비어 있고 `PACKET_PREDATES_RULE_EFFECTIVE_FROM`으로 표시합니다.

### PAPER 시장 위험 참고값 (`regime/paper_regime_reference.py`)

새 워크플로 `rule-lineage-paper-reference.yml`이 "PAPER Market Risk Reference" 실행이 성공할 때마다 돌아 최근 3일치 보존 패킷에 사이드카를 씁니다(`paper-reference-scan --min-date`, 이미 있으면 확인만). 기존 참고값 워크플로는 바이트가 `config/regime_source_owner_registry_v2.json`에 고정돼 있어 건드리지 않았습니다. 시장마다 이벤트 1개를 씁니다. **이 생산자는 등록 규칙을 실행하지 않습니다.** 그래서 `rule_refs`는 비어 있고 다음을 `unapplied_rules`로 밝힙니다.

- `RULE.ALLOCATION.V2` — `ALLOCATION_MULTIPLIER_NOT_COMPUTED_BY_ANY_PUBLIC_PRODUCER`
- `RULE.HEDGE.INVERSE.V1` — `HEDGE_NOT_COMPUTED_BY_ANY_PUBLIC_PRODUCER`
- (코인만) `RULE.CRYPTO.RUNTIME.V1` — `REFERENCE_USES_DESCRIPTIVE_NORMALIZATION_NOT_RUNTIME_V1`

### 계보를 붙이지 못한 곳

- **배분 v2 배수**: 공개 저장소의 어떤 생산자도 시장 상태 → 배수(1.00/0.70/0.25/0.00) 계산을 하지 않습니다. 참고값 패킷은 `candidate_regime`만 내고 `runtime_regime`은 모두 UNKNOWN입니다. 없는 계산에 `SIZED_BY`를 붙이면 거짓 계보가 되므로 붙이지 않았습니다.
- **한국·미국 유동성 관문(C3)**: 구현된 생산자가 없습니다(`US_LIQUIDITY_POLICY_RATIFIED: UNMET` 등). 계보를 붙일 출력이 없습니다.
- **헤지, 청산**: 판단을 내는 공개 생산자가 아직 없습니다.
- **로테이션 5종**: `rotation/` 아래 기존 모듈(`crypto_rotation.py`, `us_capital_rotation.py`, `korea_capital_rotation_policy_ratified.py` 등)은 있지만, 2026-09-15 자금 이동 판정 확정 기록(D-3/D-2)을 해시로 묶어 실행하는 생산자는 아직 없습니다(연구용 `study/us_sector_rotation_event_study.py`와 백필만 기록 ID를 참조). 그래서 이번 v1에서는 계보를 붙이지 않았습니다. 코인 후보 상태 이벤트에는 `RULE.ROTATION.CRYPTO.V1`, `RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1`, `RULE.CRYPTO.RUNTIME.V1`, `RULE.ALLOCATION.V2`가 미적용으로 표시됩니다.
- **ORDER / FILL / EXIT**: 형식은 있지만 PAPER 주문·체결은 비공개 런타임 소관이라 이번 PR에서 연결하지 않았습니다.

## 5. 왜 패킷 안이 아니라 옆 파일이고, 왜 생산자 코드에 넣지 않았나

- 코인 판단 패킷 검증기(`validate_output`)는 최상위 필드 목록을 닫아 두고(`set(packet) != …` 이면 거부), 발행된 모든 패킷을 원본에서 다시 만들어 바이트 단위로 비교합니다. 필드를 하나 더하면 새 스키마 버전이 필요하고, 48c3faa9에 고정된 서버 런타임은 그 버전을 만들지 못합니다.
- 참고값 검증기(`validate_reference`)도 패킷 전체를 다시 만들어 비교하고, 일일 브리핑·자금 흐름 참고값·로테이션 입력이 그 `payload_sha256`에 묶여 있습니다.
- **생산자 파일 자체도 해시로 고정돼 있습니다.** 처음에는 생산자 안에 "쓴 뒤 계보 호출"을 넣었으나 다음 고정이 깨지는 것을 확인하고 되돌렸습니다.
  - `decision/crypto_paper_decision_snapshot.py` → `test/test_crypto_axis_trade_bridge_explanation.py`의 PRODUCER_PINS
  - `regime/paper_regime_reference.py` → 한국 런타임 자격 기록 `evidence/authority/kr_information_system_runtime_qualification_candidate_20260913.json`의 `implementation_sha256`
  - `.github/workflows/paper-regime-reference.yml` → `config/regime_source_owner_registry_v2.json`의 `status_owner.workflow_sha256`
- 그래서 판단 패킷·생산자 코드·검증기·해시 계약을 모두 그대로 두고, 패킷 해시로 찾아가는 옆 파일을 별도 단계(코인)와 별도 워크플로(참고값)에서 씁니다. 코인 워크플로 `upbit-realtime-capture.yml`은 고정 해시가 없음을 확인했습니다.

## 6. 판단이 바뀌지 않았다는 증명

- 두 생산자 파일과 참고값 워크플로는 **바이트 그대로**입니다. 테스트가 현재 파일 해시가 기존 고정값(PRODUCER_PINS, 한국 런타임 자격 기록, 소스 소유자 대장)과 같은지 확인합니다.
- 계보 단계는 패킷을 읽기만 하고 예외를 절대 밖으로 던지지 않습니다(실패 시 `RULE_LINEAGE_EMIT_FAILED` 한 줄만 stderr에 남기고 종료 코드 0). 코인 단계는 판단·브리지·브리핑·검증 캡처가 모두 끝난 뒤, 증거 커밋 바로 앞에서만 돕니다.
- 계보 단계는 패킷의 `payload_sha256`을 다시 계산해 맞는지, 패킷이 정해진 증거 경로에 있는지(참고값은 latest 파일과 증거 사본 바이트가 같은지) 확인한 뒤에만 씁니다.
- 커밋된 코인 판단 패킷 전부(717개)와 참고값 패킷 전부에서 사이드카를 만들고, 후보 상태·시장 상태가 패킷 값과 같음을 확인합니다. 계보를 쓴 뒤에도 패킷이 `validate_output` / `validate_reference`를 다시 통과합니다.
- 기존 회귀(`test_crypto_paper_decision_snapshot.py`, `test_paper_regime_reference.py`, `test_capital_flow_posture_reference.py` 등)가 그대로 통과합니다.

## 7. P2 규칙 성적표가 이것을 쓰는 방법

1. **대상 모으기**: `evidence/rule_lineage/**/*.json`을 읽고 `validate_sidecar`로 검증합니다. 대장 해시(`registry_sha256`)가 다른 사이드카는 그 시점 대장 버전으로 해석합니다.
2. **규칙별로 묶기**: 이벤트의 `rule_refs[].rule_id`로 묶습니다. 역할별로 셉니다 — `APPLIED`(통과·적용), `BLOCKED_BY`(차단군), `SIZED_BY`(크기), `EXITED_BY`(청산).
3. **효력 기간 안만**: `REG.in_force_at(row, timestamp_utc, registry)`가 참인 이벤트만 셉니다(효력일부터, 대체됐다면 후속 버전 효력일 전까지 — 거버넌스 규칙 문장 "효력일 이후 자료만").
4. **최소 표본 전에는 판정 금지**: 대장의 `minimum_sample`이 있으면 그 수, 없으면 성적표 설계(§6-B)의 유형별 기본값(예: 진입 30/80, 게이트 차단 30/80)을 쓰되 "표본 n/최소"를 항상 함께 표시합니다.
5. **배지**: 유형(`scorecard_metric_family`)별 지표로 정상/관찰/재조정 검토/되돌림 권고를 매깁니다. 게이트는 역해석(차단군 수익이 통과군보다 낮아야 정상)입니다.
6. **사전 등록 트리거가 있는 규칙**: 기록 조건 그대로만 재검토를 켭니다(예: 코인 로테이션은 확정 사건 10건 후, 14일 초과수익 − 비용 0.5% < 0 이면 표시 전용으로 되돌림 권고). 트리거가 없는 규칙은 "트리거 확정 대기"로 표시하고 재조정 검토 배지를 켜지 않습니다.
7. **빈칸을 숨기지 않기**: `unapplied_rules`에만 나오는 규칙은 `NOT_EVALUATED(사유 코드)`로 표시합니다. v1 기준 배분 v2, 헤지, 코인 런타임, 코인 로테이션, 공통 T1/T2 연결이 여기에 해당합니다. 어떤 이벤트에도 나오지 않는 규칙(한국·미국 유동성, 미국·한국 로테이션, 해제 처리, 미국 달력, 코인 분류, 거버넌스)은 `NO_LINEAGE_PRODUCER`로 따로 표시합니다. v1에서 실제 판단 계보가 쌓이는 규칙은 `RULE.CRYPTO.FRESHNESS.PER_MARKET.V1` 하나입니다.
8. **P2에서 더할 것**: 차단된 판단의 기준가와 추적 기간(성적표 설계 §6-B(1))은 이 v1 이벤트에 없습니다. 같은 시각 시장 자료로 P2에서 붙이거나 `rule_lineage_event/2`에서 필드로 더합니다.

## 8. 규칙을 더하거나 바꾸는 절차

1. 사용자 확정 기록을 `evidence/authority/`에 바이트 그대로 복사합니다.
2. 대장에 줄을 더하고(새 버전이면 같은 `lineage_key`에 더 큰 `version`), 값마다 기록 안 위치를 적습니다.
3. `governance/rule_registry.py`의 고정 ID 목록을 같은 PR에서 바꿉니다(말없이 바뀌지 않도록).
4. 생산자가 그 규칙을 실행하게 되면 `governance/rule_lineage_producers.py`에서 `unapplied_rules` 항목을 `rule_refs`로 옮깁니다.

## 9. 작업 중 발견한 것

- `config/kr_rotation_event_study_preregistration.json`은 자금 이동 판정 기록의 **시각 수정 전** 해시(`68ca1573…`)를 가리킵니다. 결정 내용은 같다고 기록돼 있지만(수정 메모), 사전 등록 파일의 해시 고정은 사전 등록 변경이라 이번 PR에서 건드리지 않았습니다. 대장은 수정본(`c6f5dbbe…`)을 씁니다.
- 한국·미국 유동성 기록의 코인 문구("24h traded value >= KRW 5B")는 이후 CIO 보충 기록에서 "30일 평균"으로 바로잡혔습니다. 대장은 두 문구를 각 기록 위치 그대로 담고, 실제 코인 하한은 신선도 규칙 줄의 `liquidity_floor_metric`(30일 평균)입니다.

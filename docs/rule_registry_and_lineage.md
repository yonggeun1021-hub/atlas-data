# 확정 규칙 대장과 판단 계보 (v1, 2026-09-15)

근거: 사용자 확정 `USER_RATIFICATION_RULE_GOVERNANCE_EVIDENCE_GATED_ADJUSTMENT_20260915.json`
("확정 규칙 대장 + PAPER·REAL 거래 전체에 대한 규칙별 성적표"), CLAUDE_CIO P1 지시.

## 한 줄 요약

- **규칙 대장** `config/rule_registry_v1.json` (48줄): 사용자가 결정한 규칙 43줄(그중 1줄은 뒤 결정으로 대체된 `SUPERSEDED`, 1줄은 일부만 대체)과, 확정 기록이 "이번에 정하지 않는다"고 적었다가 뒤에 결정된 항목 5줄(`RESOLVED`). 줄마다 원본 기록(바이트 그대로 복사본과 sha256), 효력 시각, 기계가 읽는 핵심 값, 결정 당시 근거 수준, 사전 등록 트리거, 성적표 유형, 최소 표본, 대체·보완 연결이 있습니다.
- **판단 계보** `governance/rule_refs.py`: 판단 하나마다 "어떤 규칙이 적용·차단·크기·청산했는지"를 `rule_refs`로 남기는 형식과 검증기입니다.
- **지금 연결한 곳**: 코인 PAPER 판단 스냅샷(종목별 신선도 관문, 종목별 유동성 하한 관문, 후보 상태)과 PAPER 시장 위험 참고값. **판단 패킷도, 생산자 코드도 1바이트도 바꾸지 않고**, 생산자가 패킷을 쓴 뒤 별도 단계가 옆에 계보 파일(사이드카)을 씁니다.

규칙 대장은 권한이 아닙니다. 주문·자금·REAL 권한을 주지 않으며, 값의 권위는 언제나 원본 기록에 있습니다.

## 1. 규칙 대장

### 1-A. 결정된 규칙 43줄

효력 시각은 UTC입니다(한국 시각 − 9시간).

| 규칙 ID | 버전 | 상태 | 효력(UTC) | 원본 기록 (sha 앞 8자리, +보조 기록 수) | 사전 등록 트리거 | 성적표 유형 | 최소 표본 | 결정 당시 근거 | 연결 |
|---|---|---|---|---|---|---|---|---|---|
| RULE.ALLOCATION.V2 | 2 | 확정 | 2026-09-13 14:58 | 시장별 배분 v2 (345801ab) +1 | 없음(확정 대기) | allocation | 미정 | 기록에 없음 | 보완됨: NAV_DRAWDOWN_LIFT |
| RULE.HEDGE.INVERSE.V1 | 1 | 확정 | 2026-09-13 14:46 | 적극 인버스 헤지 (b24b38a3) +1 | 사건 연구 후 1회 통제 수정 | hedge | 미정 | 초기 기본값(미검증) | - |
| RULE.LIQUIDITY.KRUS.V1 | 1 | 확정 | 2026-09-13 15:35 | 한국·미국 유동성 (1e068439) | 없음(확정 대기) | liquidity | 미정 | 추정만 | 보완됨: US_SIP_SOURCE |
| RULE.CRYPTO.RUNTIME.V1 | 1 | 확정 | 2026-09-13 17:00 | 코인 시장판정 런타임 (e2f9f694) +1 | RISK_VOL 1회 수정 / 네 상태 관찰 뒤 재검토 / 검증 실패 시 UNKNOWN | gate | 미정 | 잠정 전방 승인 | - |
| RULE.CRYPTO.FRESHNESS.PER_MARKET.V1 | 1 | 확정 | 2026-09-13 23:25 | 코인 신선도 종목별 (043932a4) +2 | 없음(확정 대기) | data-source | 미정 | 기록에 없음 | 보완됨: DATA_FAILURE_PRIORITY |
| RULE.US.SESSION_CALENDAR.V1 | 1 | 확정 | 2026-09-14 08:25 | 미국 거래일 달력 (50259daf) | 없음(확정 대기) | data-source | 미정 | 기록에 없음 | - |
| RULE.CRYPTO.TAXONOMY.ADD_20260914 | 1 | 확정 | 2026-09-14 08:35 | 코인 분류 추가 (6ff7f486) | 없음(확정 대기) | data-source | 미정 | 기록에 없음 | - |
| RULE.ROTATION.CRYPTO.V1 | 1 | 확정 | 2026-09-14 15:05 | 자금 이동 판정 (c6f5dbbe) | 확정 사건 10건 후 전방 검토 | entry | 10건 | 기록에 없음 | - |
| RULE.ROTATION.US.V1P | 1 | **잠정** | 2026-09-14 15:05 | 같은 기록 | 1년 백필 후 재연구 | entry | 미정 | 기록에 없음 | - |
| RULE.ROTATION.KR.V1T | 1 | **임시** | 2026-09-14 15:05 | 같은 기록 | KRX 업종지수 이력 재검증까지 | entry | 미정 | 기록에 없음 | - |
| RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1 | 1 | 확정 | 2026-09-14 15:05 | 같은 기록 | 없음(확정 대기) | gate | 미정 | 기록에 없음 | - |
| RULE.ROTATION.RELEASE_HANDLING.V1 | 1 | 확정 (일부 대체) | 2026-09-14 15:05 | 같은 기록 | 없음(확정 대기) | exit | 미정 | 기록에 없음 | 보유분 처리 부분만 → EXIT.RELEASE_FULL_SELL; 신규 매수 중단은 확정 유지 |
| RULE.GOVERNANCE.EVIDENCE_GATED.V1 | 1 | 확정 | 2026-09-14 15:18 | 근거 기반 재조정 (4e08b945) | 없음(확정 대기) | governance | 미정 | 기록에 없음 | - |
| RULE.ENTRY.PAPER_BASELINE_B.V1 | 1 | 확정 | 2026-09-14 22:10 | 진입 B안 기준선 (b2a905c4) | 없음(확정 대기) | entry | 미정 | 기준선, 우위 주장 아님 | - |
| RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1 | 1 | 확정 | 2026-09-14 22:22 | B2·B3·크기 조립 (6ffeb700, 시각 정정본) | 없음(확정 대기) | gate | 미정 | 기록에 없음 | 구현: universe/crypto_candidate_promotion.py (정정 전 해시로 묶음) |
| RULE.KR.FIRST_CYCLE_CANARY_V0.V1 | 1 | 확정 | 2026-09-14 22:22 | 같은 기록 | 없음(확정 대기) | gate | 미정 | 기록에 없음 | - |
| RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V1 | 1 | **대체됨** | 2026-09-14 22:22 | 같은 기록 | 없음(확정 대기) | allocation | 미정 | 기록에 없음 | → V2 |
| RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V2 | 2 | 확정 | 2026-09-14 22:51 | 세션 크기 문구 정정 (9af25a3b) +1 | 없음(확정 대기) | allocation | 미정 | 기록에 없음 | 대체: V1 |
| RULE.EXEC.DATA_FAILURE_PRIORITY.V1 | 1 | 확정 | 2026-09-14 22:52 | 데이터 장애 우선순위 C (3d07cbf1) | 없음(확정 대기) | gate | 미정 | 기록에 없음 | 보완: FRESHNESS.PER_MARKET (범위 축소) |
| RULE.EXIT.RELEASE_FULL_SELL.V1 | 1 | **잠정** | 2026-09-14 22:57 | 청산 임시값 v1 (47276abe) | 없음(확정 대기) | exit | 미정 | 연구 C등급(해제 청산은 방향만 지지) | 일부 대체: ROTATION.RELEASE_HANDLING의 보유분 처리; 해석됨: INTERPRETATION_OBSERVATION_GAP |
| RULE.EXIT.CRYPTO_TIME_STOP_21D.V1 | 1 | **잠정** | 2026-09-14 22:57 | 같은 기록 | 없음(확정 대기) | exit | 미정 | 연구 C등급(하위 10% 개선, 수익 효과 0과 구별 안 됨) | - |
| RULE.EXIT.SHADOW_CONTROLS.V1 | 1 | 확정 | 2026-09-14 22:57 | 같은 기록 | 없음(확정 대기) | exit | 미정 | 연구 C등급 | - |
| RULE.SIZE.BTC_ETH_PER_NAME_CAP.V1 | 1 | 확정 | 2026-09-14 22:57 | 같은 기록 | 없음(확정 대기) | allocation | 미정 | 기록에 없음 | - |
| RULE.RISK.PLANNED_LOSS_RECORD_ONLY.V1 | 1 | 확정 | 2026-09-14 22:57 | 같은 기록 | 없음(확정 대기) | allocation | 미정 | 기록에 없음 | - |
| RULE.RISK.NAV_DRAWDOWN_LIFT.V1 | 1 | 확정 | 2026-09-14 22:57 | 같은 기록 | 없음(확정 대기) | allocation | 미정 | 기록에 없음 | 보완: ALLOCATION.V2 (해제 조건) |
| RULE.EXEC.TIME_CONTRACT.V1 | 1 | 확정 | 2026-09-14 23:01 | 실행 계약 D1·D3·D5~D11 (10de02bf) | 없음(확정 대기) | gate | 미정 | 기록에 없음 | - |
| RULE.EXEC.QUALITY_LAYERS.V1 | 1 | 확정 | 2026-09-14 23:01 | 같은 기록 | 없음(확정 대기) | gate | 미정 | 기록에 없음 | - |
| RULE.EXEC.ALLOCATION_REDUCTION.V1 | 1 | 확정 | 2026-09-14 23:01 | 같은 기록 | 없음(확정 대기) | allocation | 미정 | 기록에 없음 | - |
| RULE.EXEC.MULTI_MARKET_REALLOCATION.V1 | 1 | 확정 | 2026-09-14 23:01 | 같은 기록 | 없음(확정 대기) | allocation | 미정 | 기록에 없음 | - |
| RULE.EXEC.REENTRY.V1 | 1 | 확정 | 2026-09-14 23:01 | 같은 기록 | 없음(확정 대기) | entry | 미정 | 기록에 없음 | - |
| RULE.EXEC.TOPUP_POSITION_LEVEL.V1 | 1 | 확정 | 2026-09-14 23:01 | 같은 기록 | 없음(확정 대기) | allocation | 미정 | 기록에 없음 | - |
| RULE.EXEC.MONITORED_STOP_FILL_MODEL.V1 | 1 | 확정 | 2026-09-14 23:01 | 같은 기록 | 없음(확정 대기) | exit | 미정 | 기록에 없음 | - |
| RULE.VALIDATION.MECHANICAL_ONLY.V1 | 1 | 확정 | 2026-09-14 23:01 | 같은 기록 | 없음(확정 대기) | governance | 미정 | 기록에 없음 | - |
| RULE.SCORECARD.SINGLE_CONTRACT.V1 | 1 | 확정 | 2026-09-14 23:01 | 같은 기록 | 없음(확정 대기) | governance | 미정 | 기록에 없음 | - |
| RULE.LIQUIDITY.US_SIP_SOURCE.V1 | 1 | 확정 | 2026-09-14 23:06 | 미국 유동성 SIP 출처 (66315067) | 없음(확정 대기) | liquidity | 미정 | 출처 접근 점검(SPY/MSFT 0개 반환, 페이지 넘김 미구현) | 보완: LIQUIDITY.KRUS (자료 출처 지정) |
| RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1 | 1 | 확정 | 2026-09-14 23:13 | 로테이션 해석: 관측 공백 (ed2ca92d) | 없음(확정 대기) | gate | 미정 | 기록에 없음 | 해석: ROTATION.CRYPTO/US/KR, EXIT.RELEASE_FULL_SELL |

### 1-B. 한때 미확정이었다가 결정된 항목 5줄 (`RESOLVED`, 값 없음)

| 규칙 ID | 뜻 | 결정한 줄 |
|---|---|---|
| RULE.SIZE.PLANNED_LOSS_CAP.PENDING | 계획손실 한도 | RULE.RISK.PLANNED_LOSS_RECORD_ONLY.V1 |
| RULE.EXIT.PENDING | 청산 규칙 | RULE.EXIT.RELEASE_FULL_SELL.V1, RULE.EXIT.CRYPTO_TIME_STOP_21D.V1, RULE.EXIT.SHADOW_CONTROLS.V1 |
| RULE.EXECUTION.QUALITY_NUMBERS.PENDING | 실행 품질 숫자 (시간 창, 스프레드, 호가 나이) | RULE.EXEC.TIME_CONTRACT.V1, RULE.EXEC.QUALITY_LAYERS.V1 |
| RULE.CRYPTO.BTC_ETH_NAME_CAP.PENDING | BTC/ETH 종목 한도 (B8) | RULE.SIZE.BTC_ETH_PER_NAME_CAP.V1 |
| RULE.US.LIQUIDITY_IEX_TREATMENT.PENDING | 미국 유동성 IEX 처리 (B4) | RULE.LIQUIDITY.US_SIP_SOURCE.V1 |

이 줄들은 `version: 0`, `key_parameters: {}`, `effective_from: null`이고, `pending_basis`가 원래 기록의 미확정 목록 문구를 글자 그대로 가리키며, `resolved_by`가 결정한 줄과 그 기록 해시를 가리킵니다. `rule_refs`에 인용하면 `RULE_NOT_DECIDED`로 거부됩니다. 지금 `PENDING_USER_DECISION` 상태로 남은 줄은 없습니다.

### 1-C. 읽는 법

- **트리거 "없음(확정 대기)"**: 원본 기록에 재검토 조건이 적혀 있지 않다는 뜻입니다(`review_triggers: null`, `trigger_pending_user_confirmation: true`). CIO가 조건을 지어내지 않습니다.
- **효력 시각**: 기록의 `ratified_at_utc`를 그대로 쓰거나, 기록이 한국 시각(`recorded_at_kst`)만 가진 경우 UTC로 바꿨습니다.
- **기록 시각 정정**: CIO가 2026-09-15 여러 기록의 `recorded_at_kst`를 파일 생성 시각으로 바로잡았습니다(결정 내용은 같고 `correction_note`에 이전 파일 해시가 적혀 있음). 대장은 정정된 파일(새 해시)과 정정된 효력 시각을 씁니다. 뒤 기록이나 구현이 정정 전 해시를 가리키는 경우(예: 세션 크기 정정 기록과 `universe/crypto_candidate_promotion.py`가 B2·B3 기록의 옛 해시 `0e2691e0…`를 가리킴), 검증기는 **그 대상 기록 자신의 `correction_note`에 64자리 전체로 적힌** 이전 해시만 같은 기록으로 인정합니다(목록에 없는 해시·짧은 접두어는 거부).
- **B2·B3 기록 파일 두 개**: main에 이미 들어간 정정 전 파일 `evidence/authority/paper_b2_b3_size_assembly_user_ratification_20260915.json`(0e2691e0)은 코인 후보 승격 구현이 고정해 쓰므로 그대로 두고, 정정본은 `…_20260915_recorded_at_corrected.json`(6ffeb700)으로 따로 두었습니다. 대장은 정정본을 원본으로 씁니다.
- **최소 표본**: 기록에 수가 있는 규칙은 코인 로테이션(확정 사건 10건) 하나뿐입니다.
- **REAL**: 모든 줄에 `modes.REAL`이 있습니다. 기록이 REAL 권한을 바꾸지 않았으므로 대부분 `NOT_AUTHORIZED`이고, 거버넌스 규칙만 "PAPER·REAL 거래 모두 성적표에 올린다"는 문구대로 `APPLIES`입니다.
- **섹터 상태 이름**: 로테이션 공통 규칙과 진입 B안 규칙의 값은 구현 어휘 `STRONG_CONFIRMED` / `STRONG_HELD`로 통일했습니다. 인용 문구(`text`)는 기록 그대로입니다(예: "strong confirmed/held").

### 1-D. 대체와 보완

- **세션 크기 V1 → V2**: 오전 문구는 글자 그대로 읽으면 세션 매수 합계 전체를 NAV 5%로 묶었습니다(CIO 작성 오류). V2는 "시장별 세션 매수 합계 ≤ 시장 몫 남은 여유 1/3"과 "종목별 누적 보유 ≤ NAV 5% 이면서 ≤ 평균 거래대금 1%"로 나눕니다. V1은 2026-09-14 22:22 ~ 22:51 UTC, V2는 그 뒤에 효력이 있습니다.
- **강세 해제 처리 → 전량 매도 (일부 대체)**: 청산 임시값 기록이 로테이션 확정의 "보유분은 기존 손절·익절" 부분만 "첫 허용 체결 시각에 전량 매도"로 바꿨습니다. 그래서 `RULE.ROTATION.RELEASE_HANDLING.V1`은 **확정(RATIFIED) 그대로** 두고, 옛 보유분 문구를 담은 핵심 값 셋(`held_positions`, 기록 원문 `rules_text`, 사용자 문장 `user_sentence`)에 `superseded_parts`(→ `RULE.EXIT.RELEASE_FULL_SELL.V1`, 22:57 UTC부터)를 달았습니다. 그래서 그 뒤 시각에는 `part_in_force_at`이 옛 보유분 문구를 효력 있음으로 보고하지 않습니다. `on_release_new_buys`("신규 매수**만** 중단")도 ONLY 문구가 전량 매도로 일부 대체되었으므로 같은 시각부터 대체 표시하고, 신규 매수 중단 자체는 새 값 `on_release_new_buys_stop`("stop new buys")으로 로테이션 기록의 확정 상태 그대로 계속 효력이 있습니다. 후속 규칙 쪽은 `supersedes_parts`로 같은 연결을 적고, 검증기가 양쪽 일치·대상 값 존재·기록 해시·효력 순서를 확인합니다. 값 하나의 효력은 `REG.part_in_force_at(row, key_parameter, 시각, 대장)`으로 봅니다.
- **로테이션 해석 (관측 공백)**: `RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1`은 로테이션 3개 시장 규칙과 해제 전량 매도 규칙을 `INTERPRETS`로 연결합니다(값 변경 없음). 연속 확인은 허용 공백(코인 2 / 미국 4 / 한국 7일) 안의 연속 관측으로 세고, 데이터 공백으로 강세가 소멸하면 해제가 아니라 보유 유지·신규 매수 중단·"판정 공백" 표시입니다. 데이터 복귀 뒤 첫 판정이 상위권 밖이면 해제로 보고 매도합니다.
- **보완(값은 바꾸지 않음)**:
  - `RULE.EXEC.DATA_FAILURE_PRIORITY.V1` → 코인 신선도 규칙의 STALE 보류를 "일반 청산"으로 좁힘(`NARROWS_SCOPE`). 신선도 규칙의 20초/3초 등 값은 그대로.
  - `RULE.RISK.NAV_DRAWDOWN_LIFT.V1` → 배분 v2의 낙폭 규칙 해제 조건을 채움(`FILLS_CONDITION`). 배분 숫자는 그대로.
  - `RULE.LIQUIDITY.US_SIP_SOURCE.V1` → 한국·미국 유동성 규칙의 미국 자료 출처·대체 규칙을 정함(`SPECIFIES_DATA_SOURCE`). 기준값은 그대로. 근거 점검에서 SPY/MSFT가 0개를 반환했고 점검은 페이지 넘김을 따라가지 않았다는 한계를 `probe_limitation`에 적었습니다.
- **구현 계획 P1~P6 확정(2026-09-15 00:27:55 UTC)과 로테이션 허용 공백 숫자 확정(00:28:45 UTC)**: 7줄을 더했습니다(대장 48줄). `RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1`(P1), `RULE.NAV.KRW_USD_CONVERSION_FRED_DEXKOUS.V1`(P2, 10영업일 넘게 새 값 없으면 "NAV 일부 미검증"), `RULE.EXIT.SHADOW_CONTROLS_KR_US_UNITS.V1`(P3), `RULE.UNIVERSE.US_STOCK_SPDR_SECTOR_MAPPING.V1`(P4), `RULE.HEDGE.KR_STRESS_UNRATIFIED_INTERIM.V1`(P5, 한국 인버스 헤지 끔·한국 STRESS 축소는 고정 입력 재생만), `RULE.GOVERNANCE.COOLING_OFF.V1`(P6), `RULE.ROTATION.MAX_OBSERVATION_GAP.V1`(코인 2·미국 4·한국 7 달력일).
  - P6은 거버넌스 규칙의 냉각기간 조건을 채우고(`FILLS_CONDITION`), 거버넌스 규칙의 `cooling_off_period`(확정 대기 값)는 00:27:55 UTC부터 `superseded_parts`로 P6에 넘어갑니다.
  - 허용 공백 기록은 해석 기록이 이 숫자를 "확정"이라 적은 표기를 바로잡습니다(`FILLS_CONDITION`, `label_correction`). 그 전 시각의 숫자는 구현 설정값이었습니다.
  - 두 기록은 앞 기록을 `파일명.json (sha 앞 8자리…)`로 인용합니다. 검증기는 이 줄임 인용을 **인용한 이름이 대상 기록의 id·원본 파일명과 같고, 앞자리가 대상 해시로 시작할 때만** 인정합니다(전체 64자 해시 인용은 전과 같음).
- **청산 연구 C등급 표시 범위**: 청산 기록의 근거 문장은 청산에 관한 것이라 해제 전량 매도·21일 시간 손절·그림자 비교에만 붙였고, BTC/ETH 한도·계획손실 기록·NAV 낙폭 해제에는 붙이지 않았습니다(기록에 없음).
- **해시로 묶지 못한 연결**: 실행 계약 D5의 "UNKNOWN 상한은 2회 연속 UNKNOWN부터"는 배분 v2의 UNKNOWN 처리 시점에 영향을 주지만, 실행 계약 기록이 배분 기록 해시를 적지 않아 `amends`로 묶지 않았습니다(값만 `RULE.EXEC.ALLOCATION_REDUCTION.V1`에 있음).

원본 기록 복사: `evidence/authority/` 아래에 바이트 그대로 복사했습니다. 코인 런타임·코인 신선도·미국 달력 기록은 이미 같은 해시로 들어와 있어 그 파일을 가리킵니다. 공개 저장소에 올리기 전에 비밀값 형태가 없는지 확인했습니다.

## 2. 검증기가 막는 것 (`governance/rule_registry.py`)

`python3 governance/rule_registry.py` → `PASS_RULE_REGISTRY_VALID`

- ID 48개가 고정 목록과 정확히 같고 중복이 없어야 합니다.
- 줄마다 사용자 확정 기록이 첫 번째 원본이어야 합니다. 원본 없는 규칙은 거부합니다.
- 복사본의 sha256·바이트 길이가 대장과 같아야 합니다.
- 기록 안의 ID가 대장과 같아야 하고, 기록 본문이 `rule_id`를 적고 있으면 줄 ID와 같아야 합니다.
- **상태 대조**: 기록이 이 규칙의 상태를 적고 있으면(`/decision/<rule_id>/status`, `rule_id`를 가진 노드의 `status`) `status_source`가 반드시 그 위치를 가리켜야 하고, 기록 값(`RATIFIED`/`PROVISIONAL`/`TEMPORARY`/`RATIFIED_AS_PAPER_BASELINE`)과 줄 상태가 맞아야 합니다(대체된 줄은 예외). 로테이션 기록처럼 결정 키로 상태를 적은 기록도 `status_source`로 대조합니다.
- **모든 핵심 값·효력 시각·근거 수준·트리거·최소 표본·보완·미확정 근거는 기록 안 위치(JSON pointer)를 가리킵니다.** `EXACT`는 기록 값과 같아야 하고, `PARSED_FROM_TEXT`는 인용 문구가 기록 문장 안에 글자 그대로 있어야 합니다.
- **해석 값 고정(pin)**: 인용 문구는 그대로인데 해석 값만 바뀌는 경우(예: "10 sessions max holding"의 값 10 → 12, "CRYPTO provider age 20s"의 값 20 → 60, 트리거 조건 문장 교체)를 막기 위해, 모든 `PARSED_FROM_TEXT` 항목의 전체 내용(문구+값/조건/단위/관계) 해시를 `config/rule_registry_v1_parsed_pins.json`에 고정했습니다. 그 파일의 해시는 다시 `governance/rule_registry.py`의 `PARSED_PINS_SHA256` 상수로 고정합니다. 값을 바꾸려면 대장·고정 파일·상수 세 곳이 함께 바뀌어 리뷰에서 드러납니다.
- 버전은 같은 계열 안에서 효력 순서대로 올라가야 합니다. `supersedes` / `superseded_by` / `SUPERSEDED` 상태가 서로 맞아야 하고, 대체하는 기록 본문이 대체되는 기록의 해시(또는 정정 전 해시)를 담아야 합니다.
- `amends`와 `amended_by`가 양쪽에서 일치해야 하고, 보완 대상의 기록 해시가 보완 기록 본문에 있어야 합니다.
- `RESOLVED` 줄의 `resolved_by`는 결정된 줄과 그 기록 해시를 가리켜야 합니다.
- "구현이 이 기록을 해시로 묶고 있다"(`binds_record_sha256: true`)고 적었으면 그 파일에 실제로 해시가 있어야 합니다.

### 1-E. 기록 재생용 대장 사본 (`evidence/rule_registry_snapshots/`)

- 판단 기록은 `rule_refs`에 그때의 대장 sha256을 담습니다. 대장에 줄이 더해져도 옛 기록을 다시 계산할 수 있도록, 대장이 바뀔 때마다 그 바이트를 gzip으로 `rule_registry_v1-<sha256>.json.gz`에 추가 전용 저장합니다(sha256은 압축 전 JSON 바이트)(`portfolio/paper_execution_core.py` `write_registry_snapshot`).
- git 기록이 아니라 저장소 사본을 쓰는 이유: CI는 얕은 체크아웃이라 옛 커밋이 없습니다.
- 사본이 없으면 `REGISTRY_SNAPSHOT_UNAVAILABLE`, 바이트가 sha와 다르면 `REGISTRY_SNAPSHOT_SHA_MISMATCH`로 실패합니다(조용히 통과하지 않음). 현재 대장의 사본이 없으면 `test/test_paper_execution_core_v1.py`가 실패하므로 **대장을 바꾸는 PR은 사본도 함께 커밋해야 합니다.**

## 3. 판단 계보 형식 (`governance/rule_refs.py`)

### rule_refs 한 항목

```json
{"rule_id": "RULE.CRYPTO.FRESHNESS.PER_MARKET.V1", "version": 1,
 "registry_sha256": "<대장 파일 sha256>", "source_record_sha256": "043932a4…",
 "role": "BLOCKED_BY"}
```

- `role`: `APPLIED`(적용) · `BLOCKED_BY`(차단) · `SIZED_BY`(크기 결정) · `EXITED_BY`(청산 결정) · `SUPERSEDED_BY`(대체된 옛 문구를 표시할 때 후속 규칙 인용, 판단 적용 아님)
- 버전·원본 해시는 대장과 정확히 같아야 하고, (rule_id, role) 순 정렬, 같은 쌍 중복 금지입니다. `RESOLVED`/`PENDING_USER_DECISION` 줄은 인용할 수 없고, `SUPERSEDED` 줄은 그 효력 기간의 판단에서 인용할 수 있습니다.

### rule_lineage_event/1

| 필드 | 뜻 |
|---|---|
| `decision_id` | 판단 하나의 ID. 예: `crypto_paper_decision:<generation_id>:KRW-BTC` |
| `market` / `instrument` | US·KR·CRYPTO / 종목(없으면 null) |
| `timestamp_utc` | 생산자 패킷의 판단 시각 |
| `event_type` | DECISION · BLOCK · ORDER · FILL · EXIT |
| `gate` | 관문 이름. 예: `realtime_freshness_per_market` |
| `outcome` | 생산자가 낸 결과를 그대로 옮긴 값 |
| `rule_refs` | 위 형식 |
| `unapplied_rules` | 이 판단에 해당하지만 생산자가 아직 실행하지 않는 규칙 + 사유 코드 |
| `inputs_sha256` | 그 관문 입력값의 해시 |
| `source_packet` | 원본 패킷 경로·스키마·payload_sha256 |
| `event_id` | 위 전체의 해시(변조 검출) |

BLOCK 이벤트는 차단한 규칙(`BLOCKED_BY`)을 적거나, 등록 규칙이 실행되지 않았음을 `unapplied_rules`로 밝혀야 합니다.

### 사이드카 rule_lineage_sidecar/1

패킷 하나 × 대장 버전 하나에서 나온 이벤트 묶음입니다. 경로에 **패킷 해시와 대장 해시가 모두** 들어갑니다.

- 코인 판단: `evidence/rule_lineage/crypto_paper_decision/<UTC 날짜>/<HHMM>/<payload_sha256>/registry-<대장 sha256>.json`
- 시장 위험 참고값: `evidence/rule_lineage/paper_regime_reference/<기준일>/<payload_sha256>/registry-<대장 sha256>.json`

한 번 쓰면 다시 쓰지 않습니다(같은 파일이 있으면 내용 확인만). 대장이 바뀌면 같은 패킷 옆에 새 대장 해시 파일이 추가될 뿐 추가 전용 충돌이 나지 않습니다. `decision_outcome_changed`는 항상 `false`입니다.

## 4. 지금 연결된 곳과 아직 안 된 곳 (사실 그대로)

### 코인 PAPER 판단 스냅샷 (`decision/crypto_paper_decision_snapshot.py`)

`upbit-realtime-capture.yml`의 새 단계 `crypto_rule_lineage`가 판단 단계가 EVALUATED로 끝났을 때, 증거 커밋 바로 앞에서
`python3 governance/rule_lineage_producers.py crypto-decision --packet "$DECISION_PATH"`를 실행합니다(`continue-on-error: true`, `timeout-minutes: 5`). 실패하면 종료 코드는 0이지만 `::warning title=Rule lineage sidecar failed::…` 경고 표시가 실행 화면에 뜹니다. 커밋 단계가 `evidence/rule_lineage/crypto_paper_decision`을 함께 올립니다. 후보 종목마다 이벤트 3개:

1. `realtime_freshness_per_market`: FRESH가 아니면 BLOCK + `BLOCKED_BY RULE.CRYPTO.FRESHNESS.PER_MARKET.V1`, 아니면 DECISION + `APPLIED`.
2. `realtime_liquidity_floor`: 30일 평균 거래대금 하한 제외면 BLOCK + `BLOCKED_BY`, 포함이면 `APPLIED`.
3. `candidate_state`: 패킷의 후보 상태를 그대로 옮기고, 실제로 WAIT로 낮춘 경우만 BLOCK.

실제 예: 2026-09-14 21:13 UTC 패킷에서 KRW-WLD는 실시간 STALE → BLOCK. 상태가 원래 WATCH였으므로 `capped_actionable_state: false`입니다. 2026-09-13 23:25 UTC 이전 패킷(`/1`)은 규칙 효력 전이므로 `PACKET_PREDATES_RULE_EFFECTIVE_FROM`으로 표시합니다.

### PAPER 시장 위험 참고값 (`regime/paper_regime_reference.py`)

새 워크플로 `rule-lineage-paper-reference.yml`이 "PAPER Market Risk Reference" 성공 뒤마다 최근 3일치 보존 패킷에 사이드카를 씁니다(실패 시 경고 표시). 이 생산자는 등록 규칙을 실행하지 않으므로 `rule_refs`는 비어 있고 `RULE.ALLOCATION.V2`, `RULE.HEDGE.INVERSE.V1`, (코인만) `RULE.CRYPTO.RUNTIME.V1`을 `unapplied_rules`로 밝힙니다.

### 계보를 붙이지 못한 곳

- **배분 v2 배수**: 공개 생산자가 계산하지 않습니다(참고값 `runtime_regime`은 모두 UNKNOWN). 없는 계산에 `SIZED_BY`를 붙이지 않았습니다.
- **한국·미국 유동성 C3, 헤지, 청산, 실행 계약(D1~D11), 세션 크기**: 이 기록들을 실행하는 공개 생산자가 아직 없습니다.
- **main에 새로 들어온 구현(#751, #752)**: 로테이션 확인 층(`config/rotation_confirmation_policy_v1.json`, 로테이션 기록 c6f5dbbe에 묶임), 진입 기회 장부(`config/paper_entry_opportunity_ledger_v1.json`, 진입 B안 기록 b2a905c4), 코인 후보 승격 v3(`universe/crypto_candidate_promotion.py`, B2·B3 정정 전 해시)가 대장의 `implementation_bindings`로 연결됩니다. 이들에 대한 판단 계보 사이드카는 이번 PR 범위 밖입니다. 참고로 main의 로테이션 확인 층은 "해제 = 신규 매수 중단만(강제 매도 없음)"으로 구현돼 있어, 22:57 UTC부터의 `RULE.EXIT.RELEASE_FULL_SELL.V1`(보유분 전량 매도)과 로테이션 해석(관측 공백) 규칙은 아직 반영되지 않았습니다.
- **ORDER / FILL / EXIT**: 형식은 있지만 PAPER 주문·체결은 비공개 런타임 소관입니다.
- v1에서 실제 판단 계보가 쌓이는 규칙은 `RULE.CRYPTO.FRESHNESS.PER_MARKET.V1` 하나입니다.

## 5. 왜 패킷 안이 아니라 옆 파일이고, 왜 생산자 코드에 넣지 않았나

- 코인 판단 패킷 검증기(`validate_output`)는 최상위 필드 목록을 닫아 두고 발행된 패킷을 원본에서 다시 만들어 바이트 단위로 비교합니다. 필드를 더하면 새 스키마 버전이 필요하고, 48c3faa9에 고정된 서버 런타임은 그 버전을 만들지 못합니다.
- 참고값 검증기(`validate_reference`)도 패킷 전체를 다시 만들어 비교하고, 일일 브리핑·자금 흐름 참고값·로테이션 입력이 그 `payload_sha256`에 묶여 있습니다.
- **생산자 파일도 해시로 고정돼 있습니다.** 처음 넣었던 생산자 내부 호출은 다음 고정을 깨서 되돌렸습니다.
  - `decision/crypto_paper_decision_snapshot.py` → `test/test_crypto_axis_trade_bridge_explanation.py` PRODUCER_PINS
  - `regime/paper_regime_reference.py` → `evidence/authority/kr_information_system_runtime_qualification_candidate_20260913.json` `implementation_sha256`
  - `.github/workflows/paper-regime-reference.yml` → `config/regime_source_owner_registry_v2.json` `status_owner.workflow_sha256`
- **머지 뒤 서버 쪽 조치 필요**: 저장소 밖 서버 디스패처가 `.github/workflows/upbit-realtime-capture.yml`을 **git blob sha로 고정**합니다. 이 PR은 그 파일에 계보 단계를 더하므로 머지 뒤 디스패처 설정의 blob 값을 바꿔야 합니다. 이 PR 최종 머리 기준 blob은 `git hash-object .github/workflows/upbit-realtime-capture.yml`로 다시 계산해 쓰십시오(main 병합 뒤 이 문서 작성 시점 값 `94131f10b699a5a982a8919985a2e3d68e563a98`; 이 PR의 첫 머리 때 값 `ba2602cf…`는 더 이상 맞지 않음).

## 6. 판단이 바뀌지 않았다는 증명

- 두 생산자 파일과 참고값 워크플로는 바이트 그대로이고, 테스트가 세 고정값과 현재 파일 해시가 같음을 확인합니다.
- 계보 단계는 패킷을 읽기만 하고 예외를 밖으로 던지지 않습니다. 코인 단계는 판단·브리지·브리핑·검증 캡처가 모두 끝난 뒤, 증거 커밋 직전에만 돕니다.
- 계보 단계는 패킷의 `payload_sha256`을 다시 계산해 맞는지, 패킷이 정해진 증거 경로에 있는지 확인한 뒤에만 씁니다.
- 커밋된 코인 판단 패킷 전부와 참고값 v2 패킷 전부에서 사이드카를 만들고 상태가 패킷 값과 같음을, 쓰기 뒤에도 패킷이 `validate_output` / `validate_reference`를 통과함을 확인합니다.

## 7. P2 규칙 성적표가 이것을 쓰는 방법

1. **대상 모으기**: `evidence/rule_lineage/**/registry-*.json`을 읽고 `validate_sidecar`로 검증합니다. 같은 패킷에 대장 해시가 여럿이면 판단 시각에 쓰이던 대장(또는 가장 최신 대장)을 고릅니다.
2. **규칙별로 묶기**: `rule_refs[].rule_id`로 묶고 역할별로 셉니다.
3. **효력 기간 안만**: `REG.in_force_at(row, timestamp_utc, registry)`가 참인 이벤트만 셉니다(효력일부터, 대체됐다면 후속 규칙 효력일 전까지).
4. **최소 표본 전에는 판정 금지**: `minimum_sample`이 있으면 그 수, 없으면 성적표 설계(§6-B)의 유형별 기본값을 쓰되 "표본 n/최소"를 함께 표시합니다. `RULE.SCORECARD.SINGLE_CONTRACT.V1`에 따라 최소 표본은 "검토 시작" 기준이고, 강세 에피소드 단위로 묶어 정해진 점검 시점에만 판정합니다.
5. **배지**: `scorecard_metric_family`별 지표로 정상/관찰/재조정 검토/되돌림 권고를 매깁니다. 게이트는 역해석입니다.
6. **사전 등록 트리거가 있는 규칙**: 기록 조건 그대로만 재검토를 켭니다. 트리거가 없는 규칙은 "트리거 확정 대기"로 표시합니다.
7. **빈칸을 숨기지 않기**: `unapplied_rules`에만 나오는 규칙은 `NOT_EVALUATED(사유 코드)`, 어떤 이벤트에도 나오지 않는 결정 규칙은 `NO_LINEAGE_PRODUCER`, `RESOLVED` 줄은 결정한 줄로 안내합니다.
8. **P2에서 더할 것**: 차단된 판단의 기준가·추적 기간은 v1 이벤트에 없습니다. P2에서 붙이거나 `rule_lineage_event/2`에서 더합니다.

## 8. 규칙을 더하거나 바꾸는 절차

1. 사용자 확정 기록을 `evidence/authority/`에 바이트 그대로 복사합니다.
2. 대장에 줄을 더하고(새 버전이면 같은 `lineage_key`에 더 큰 `version`, 대체면 양쪽 연결), 값마다 기록 안 위치를 적습니다.
3. `governance/rule_registry.py`의 고정 ID 목록을 같은 PR에서 바꿉니다.
4. `REG.build_parsed_pins(registry)`로 고정 파일을 다시 만들고 `PARSED_PINS_SHA256` 상수를 바꿉니다.
5. 생산자가 그 규칙을 실행하게 되면 `governance/rule_lineage_producers.py`에서 `unapplied_rules` 항목을 `rule_refs`로 옮깁니다.

## 9. 작업 중 발견한 것

- `config/kr_rotation_event_study_preregistration.json`은 자금 이동 판정 기록의 시각 수정 전 해시(`68ca1573…`)를 가리킵니다. 사전 등록 변경이라 손대지 않았습니다(로테이션 기록의 `correction_note`가 이 해시를 이전 해시로 적고 있어 같은 기록임은 확인 가능).
- 한국·미국 유동성 기록의 코인 문구("24h traded value")는 CIO 보충 기록에서 "30일 평균"으로 바로잡혔습니다. 실제 코인 하한은 신선도 규칙 줄의 `liquidity_floor_metric`(30일 평균)입니다.
- 실행 계약 기록의 `cio_follow_up_no_decision`(기록 문구 정정, 다중 후보 취소 대체, 세션 예산 기록 필드 등)은 결정이 아니라 CIO 후속 작업이라 대장에 넣지 않았습니다.

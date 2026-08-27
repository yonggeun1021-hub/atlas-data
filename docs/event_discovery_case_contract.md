# P3-08 Event Discovery Case contract

`discovery/event_case.py`는 기존 SEC D1 분류 결과를 결정론적
`discovery_case/1` record로 만들고, 호출자가 명시한 evidence lineage를 연결한다.

## 현재 지원 범위

- 입력 분류: `decision/event_classifier.py`의 taxonomy `1.0`, decision `d1_v1`
- source: SEC EDGAR D1 classification
- case 생성: `resolved` 또는 `partial` record의 이미 확정된 `event_types` 각각
- evidence 연결: exact `source_record_key`에 대한 명시적 binding만 사용
- lineage: `event_as_of`, `available_at`, `retrieved_at_utc`, source URL/accession/SHA,
  evidence SHA

DART는 item extraction policy가 미비준이고, news/policy/Crypto source는 구현되지
않았다. 이 상태를 packet의 `source_coverage`에 그대로 노출한다.

2026-08-27부터 `discovery/dart_event_observation.py`가 기존 OpenDART metadata와
P4-03 retained ZIP/member bytes를 provider 호출 없이 재검증해 별도 append-only
관측 packet을 만든다. 이 packet은 공시 접수번호·제목·날짜와 원문 보존 상태만
기록한다. DART 원천에는 정확한 공시 timestamp와 비준된 item/event-type 해석
정책이 없으므로 `event_at`, `event_type`, `direction`, `importance`는 모두 null이고
모든 observation은 `OBSERVED_ESCALATION_BLOCKED`다. 이 관측 packet은 아직
SEC D1 `event_discovery_case_packet/2`에 합쳐지거나 중요도 detector에 투입되지
않으며 Candidate/Stage/Rule/notification/Action/Order/Production/trading 권한을
열지 않는다.

## 권한 경계

case는 “분류된 사건이 관측됐다”는 기록이다. “중요하다”, “긍정/부정이다”, “후보로
승격한다”는 뜻이 아니다.

- `importance_status = IMPORTANCE_UNRATIFIED`
- `interpretation_status = INTERPRETATION_NOT_AUTHORIZED`
- `promotion_status = PROMOTION_NOT_AUTHORIZED`
- `stage_transition = null`
- `investment_action = null`

importance ranking, 자동 Stage 승격, Rule/Production/trading 권한은 모두 false다.

## Evidence binding

```json
{
  "schema_version": "event_case_evidence_bindings/1",
  "binding_set_id": "caller-approved-set",
  "bindings": [
    {
      "source_record_key": "SNDK|0001628280-26-053346|1.0|d1_v1",
      "evidence": {
        "schema_version": "event_source_evidence/1",
        "source_system": "SEC_EDGAR",
        "subject": "SNDK",
        "event_date": "2026-08-05",
        "source_identity": {
          "source_id": "sec_edgar",
          "accession": "0001628280-26-053346",
          "source_url": "https://www.sec.gov/Archives/edgar/data/...",
          "source_sha256": "<64 lowercase hex>",
          "available_at": "2026-08-05",
          "retrieved_at_utc": "2026-08-06T00:00:00Z"
        }
      }
    }
  ]
}
```

binding 부재는 `EVIDENCE_UNRESOLVED`, 필수 lineage 누락·형식 오류는
`EVIDENCE_BLOCKED`, 완전하고 identity가 일치하면 `EVIDENCE_LINKED`다. unknown
record, 중복 record/binding, accession·subject·date·URL 불일치는 fail-closed한다.
SEC URL은 HTTPS `www.sec.gov`만 허용하고 `available_at`은 event date보다 빠를 수
없으며 retrieval date는 availability보다 빠를 수 없다. case 생성 대상이 아닌
`unresolved`/`not_applicable` record에 evidence binding을 붙이는 것도 거부한다.

## Persisted packet validation

`validate_packet()`은 저장된 packet의 case ID, D1 taxonomy/decision identity,
evidence 상태와 lineage 형식·시간 순서, authority 봉쇄값, 동일 source record에서
파생된 복수 case의 공통 분류, 정렬, exclusion, summary를 독립적으로 다시
검증한다. 따라서 값을 바꾸고 `packet_sha256`을 다시 계산해도 semantic drift는
통과하지 않는다. `build_packet()`도 발행 전에 같은 validator를 호출한다.

packet(schema `event_discovery_case_packet/2`)은 `frozen_sources`에 이 packet을
만드는 데 실제로 쓰인 D1 record 전체(case를 만든 것과 제외된 것 모두)와
evidence binding 본문을 최소 충분한 snapshot으로 보존한다. `inputs`의
SHA-256 두 값은 이 `frozen_sources`를 정확히 가리켜야 한다.

`validate_packet()`은 모든 필드 단위 검증을 마친 뒤 마지막 관문으로,
production 조립 로직(`_build_packet_body()` — `build_packet()`이 쓰는 것과
동일한 함수, 복사본이 아니다)을 `frozen_sources`에 대해 다시 호출해 case 집합,
exclusion, summary, `binding_set_id`, `inputs`를 packet 내부 source만으로
독립 재구축하고 저장된 값과 정확히 일치하는지 대조한다. 따라서 결측 입력,
뒤늦게 추가된 event, source binding 교체, `packet_sha256`/`inputs` self-rehash
변조는 필드 단위 검증을 모두 통과하더라도 이 최종 대조에서 fail-closed된다.

기본 CLI는 JSONL record와 binding JSON을 읽어 지정된 `--out`에만 원자적으로 쓴다.

## 운영 population — committed evidence only

`discovery/event_population.py`는 provider를 다시 호출하지 않고 다음의 이미 커밋된
입력만 재사용한다.

1. `data/event_records.jsonl`의 SEC D1 전체 모집단
2. `data/sec_content/<ticker>/<accession>/_manifest.json`
3. manifest가 지목한 primary document의 retained `.gz` bytes

binding은 accession·filing date·primary source URL이 D1 record와 정확히 같고,
gzip 해제 후 실제 byte length와 SHA-256이 manifest와 일치하며,
`retrieved_at_utc <= decision_at`일 때만 생성된다. manifest가 없는 것은 정상적인
`EVIDENCE_UNRESOLVED`이며, manifest 또는 retained bytes가 서로 모순되면 전체
population을 fail-closed한다. filing date를 `available_at`의 date-only 하한으로
사용하며 중요도·방향·후보승격 의미를 부여하지 않는다.

운영 산출물은 아래 content-addressed 경로에 append-only로 보존한다.

```text
data/observations/event_discovery_cases/<KST decision date>/
  packet-<packet_sha256 first 16>.json
```

같은 packet은 byte-identical no-op이고, 같은 content-addressed 경로의 다른 byte는
오염으로 거부된다. `Atlas Daily Collect`는 SEC content capture 뒤 이 population을
실행한다. Daily Orchestrator의 `ROTATION_DISCOVERY`는 더 이상 synthetic empty
records를 넣지 않고 같은 population builder를 호출한다. 그러나 briefing의
`new_candidates`와 `existing_candidate_changes`는 계속 비어 있고, importance,
interpretation, Stage, Rule, action, order, Production, trading 권한은 전부 닫혀 있다.

DART item extraction, news, policy, Crypto source와 importance ranking은 이 slice의
범위가 아니며 기존 `UNRATIFIED`/`NOT_IMPLEMENTED` 상태를 유지한다.

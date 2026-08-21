# P5-03 Rule–Evidence binding contract

`bridge/rule_evidence_binding.py`는 canonical Rule 25건과 명시적으로 지정된
`evidence_envelope/1`을 연결해 감사 가능한 lineage packet을 만든다.

## 보장하는 것

- 모든 canonical Rule이 packet에 정확히 한 번 등장한다.
- 연결은 호출자가 제공한 exact key
  `(subject, measurement_identity, economic_period_end)`만 사용한다.
- 연결된 증거마다 `evidence_as_of`, `available_at`, `retrieved_at_utc`, source URL,
  source SHA-256, envelope SHA-256을 보존한다.
- 명시적 연결이 없으면 `LINK_UNRESOLVED`, 증거/lineage가 불완전하거나 모순이면
  `LINK_BLOCKED`다. 둘 다 0이나 성공으로 바꾸지 않는다.
- 입력 순서와 무관한 정렬 및 canonical JSON SHA-256으로 동일 입력은 동일 packet을
  만든다. CLI 출력은 임시 파일을 거쳐 원자적으로 교체한다.
- `validate_packet()`은 저장된 packet의 Rule 정적 identity, reference 상태 파생,
  lineage 형식, `ALL_REQUIRED` 결합, binding-set hash, summary, authority, packet hash를
  다시 계산한다. 바깥 hash만 다시 만든 의미 변조는 유효해지지 않는다.

packet에는 원본 evidence envelope 본문 전체가 들어 있지 않으므로
`evidence_set_sha256`과 각 `envelope_sha256`의 외부 진위는 standalone validator가
재구성할 수 없다. 그 진위는 envelope 보존·인증 경계의 책임이며, P5-03 validator는
packet 내부 의미와 명시 binding의 일관성을 증명한다.

## 하지 않는 것

- source 검색·ranking·fallback 또는 최신 revision 선택
- 관측값의 의미 해석, threshold 적용, Rule 결과 계산
- evaluator/Production/Stage/Portfolio/trading 연결
- 네트워크 호출 또는 tracked evidence 생성

`rule_result`는 항상 `null`, `evaluation_status`는 항상
`EVALUATION_NOT_AUTHORIZED`다. `LINK_AVAILABLE`은 증거 연결과 lineage가 완전하다는
뜻일 뿐 Rule의 참·거짓이나 투자 판단이 아니다.

## 입력

Bindings 문서는 다음 최소 계약을 따른다.

```json
{
  "schema_version": "rule_evidence_bindings/1",
  "binding_set_id": "caller-approved-binding-set-id",
  "bindings": [
    {
      "rule_id": "RULE-0021",
      "selection_mode": "ALL_REQUIRED",
      "evidence_keys": [
        {
          "subject": "MSFT",
          "measurement_identity": "Azure and other cloud services revenue YoY constant currency",
          "economic_period_end": "2026-06-30"
        }
      ]
    }
  ]
}
```

`ALL_REQUIRED` 외 모드는 허용하지 않는다. 자동 선택을 피하기 위해 중복 Rule,
중복 evidence key, unknown Rule, subject 불일치, 동일 key의 복수 envelope는 모두
fail-closed한다. 실제 binding 정책은 이 저장소에 새로 발명하지 않으며, 별도 승인된
호출자가 제공해야 한다.

## 상태 경계

| 상태 | 의미 |
| --- | --- |
| `LINK_AVAILABLE` | 명시된 모든 envelope가 available이고 필수 lineage가 완전함 |
| `LINK_BLOCKED` | blocked evidence, 상태 모순, lineage 누락/형식 오류가 하나 이상 있음 |
| `LINK_UNRESOLVED` | 명시적 binding 또는 참조한 evidence가 아직 없음 |

이 구현은 P5-03의 연결·추적 capability다. P5-02 source hierarchy, P5-04
deterministic evaluator, Production HOLD를 닫거나 변경하지 않는다.

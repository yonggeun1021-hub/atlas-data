# P5-10 5축 진입·청산 종목별 설명 읽기 모델

`decision/crypto_axis_trade_bridge.py`가 만든 패킷 **한 개**만을 입력으로 받아,
이미 산출된 상태·사유 코드를 사용자 언어로 다시 라벨링하는 읽기 전용 소비자다.
새로운 판단을 만들지 않는다. 모든 단계 라벨은 브리지가 이미 내보낸 코드의
전사(全射) 변환이며, 매핑에 없는 코드는 기본값으로 흘리지 않고 실패한다.

## 입력과 출력

- 입력: `evidence/crypto_axis_trade_bridge/<capture_date>/<capture_hhmm>/<generation_id>/packet.json`
  - CLI `--bridge-packet`로 **명시 지정**한다. 자동 탐색이나 기본 라이브 대상이 없다.
  - 렌더링 전에 생산자 검증기 `crypto_axis_trade_bridge.validate_output`을 통과해야 한다.
    실패하면 `SOURCE_BRIDGE_INVALID:<code>`로 중단하며 어떤 출력도 쓰지 않는다.
- 계약: `config/crypto_axis_trade_bridge_explanation_contract.json`
  - 표현 어휘와 전부 false인 권한 블록만 담는다. 숫자 정책이 없다.
  - 축 순서와 청산 사다리는 이 파일에서 독립 선언하지 않고 생산자 계약과
    바이트 동일한지 대조한다(`AXIS_ORDER_DRIFT`, `EXIT_PRIORITY_DRIFT`).
- 출력(호출자가 `--output-root`로 지정한 위치):
  - `<capture_date>/<capture_hhmm>/<generation_id>/packet.json`
    — 스키마 `crypto_axis_trade_bridge_explanation_packet/1`
  - `<capture_date>/<capture_hhmm>/<generation_id>/briefing.md`
  - 표준출력 JSON과 `GITHUB_OUTPUT` 키
    `outcome`, `path`, `payload_sha256`, `source_generation_id`
  - 정식 커밋 위치는 `evidence/crypto_axis_trade_bridge_explanation/`이다.

JSON과 Markdown은 **같은 조립 구조 하나**에서 렌더링되므로 개수·라벨·사유가
서로 어긋날 수 없다. 같은 세대를 다시 만들면 바이트 동일하며, 기존 파일과
바이트가 다르면 덮어쓰지 않고 `EXISTING_OUTPUT_DRIFT_OR_TAMPER`로 거부한다.

Python API의 `assemble(bridge_packet, contract=None)`과 `explain(bridge_packet)`은
모두 생산자 검증을 먼저 실행한다. 내부 `_assemble_unsigned`는 표현 변환만
검사하는 함수이며 `payload_sha256`이 없는 구조를 반환한다.
`validate_output(explanation, bridge_packet=source)`와
`render_markdown(explanation, bridge_packet=source)`에도 원본 패킷이 필수다.
생산자로 원본을 검증하고 설명을 다시 산출해 전체 바이트를 대조하므로,
해시를 다시 계산한 라벨 위조도 `OUTPUT_DERIVATION_MISMATCH`로 거부한다.

## 단계 어휘

| stage_id | 라벨 | 종류 | 입력 상태 |
| --- | --- | --- | --- |
| `ENTRY_WAIT` | 매수대기 | ENTRY | `entry.state == WAIT` |
| `ENTRY_BLOCKED` | 진입 차단 | ENTRY | `entry.state == BLOCKED` |
| `ENTRY_REVIEW` | 진입검토 | ENTRY | (도달 불가) |
| `HOLDING` | 보유 | POSITION | (도달 불가) |
| `REDUCE` | 축소 | POSITION | (도달 불가) |
| `EXIT_REVIEW` | 청산검토 | POSITION | (도달 불가) |

- `entry.state`가 `WAIT`/`BLOCKED` 외의 값이면 기본 구간으로 분류하지 않고
  `ENTRY_STATE_UNMAPPED:<market>:<state>`로 실패한다. `exit.state`도 같다
  (`EXIT_STATE_UNMAPPED`).
- 도달 불가 단계는 항상 비어 있으며, 비어 있는 이유로 **상류 코드 원문**을
  함께 싣는다.
  - `ENTRY_REVIEW` → `AGGREGATE_POLICY_UNRATIFIED`
  - `HOLDING`/`REDUCE`/`EXIT_REVIEW` → `NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET`
- 그 코드가 실제로 모든 종목의 상류 사유에 존재하는지 확인하며, 없으면
  `EMPTY_STAGE_REASON_UNSUPPORTED:<stage_id>`로 실패한다. 즉 비어 있는 이유도
  발명하지 않는다.

> **보유·축소·청산검토가 비어 있다는 것은 이 입력 패킷에 가상 체결 포지션이
> 없다는 뜻이다. 사용자의 실제 계좌 보유 자산이 없다는 뜻이 아니다.**

## 축 → 종목 연결

- 다섯 축은 브리지의 `status`, `observation_date`, `available_at`, `warnings`와
  계약상 진입/청산 소비자를 그대로 옮긴다. 추론을 더하지 않는다.
- 종목별 보류 축은 **그 종목 자신의 진입 사유** `OFFICIAL_AXES_INCOMPLETE:<axes>`가
  지목한 축만 싣는다. 목록이 생산자의 `five_axis.missing_axes`와 순서까지
  같지 않으면 `AXIS_HOLD_COVERAGE_MISMATCH`로 실패한다.
- `DEFINED` 축은 결코 보류 사유로 렌더링하지 않는다
  (`AXIS_HOLD_STATUS_CONFLICT`). 반대로 `DEFINED`는 데이터 완성도일 뿐
  매수 신호가 아니라고 명시한다.

## 청산 우선순위

`HARD_EXIT → SECURITY_LIQUIDITY → RISK_REGIME → TREND → PROFIT_TRAIL → TIME_REVIEW`

브리지 패킷의 종목별 `exit.priority_categories`를 순서 그대로 복사하며,
0번이 `HARD_EXIT`가 아니거나 생산자 계약 목록과 다르면 `EXIT_PRIORITY_DRIFT`로
빌드를 실패시킨다. 재정렬·점수화·항목 추가를 하지 않는다. P7-13 우선순위는
약화되지도 중복되지도 않는다.

## 명시적 비권한

- 종합 Regime을 계산하지 않는다. 숫자 임계값, 종목 점수, 수량, 가격을 만들지 않는다.
- 시장 편입, 스케줄, 네트워크, 거래소·계좌·키·주문 호출이 없다.
- 출력 `authority`와 `stage_authorized`·`buy_authorized`·`action_authorized`·
  `order_authorized`·`production_authorized`·`trading_authorized`·
  `real_capital_authorized`는 모두 false이며 서명 직전에 자체 검사한다.
- 종합 Regime 정책이 `UNRATIFIED`이거나 축이 하나라도 `UNDEFINED`인 동안
  진입 단계 소속은 매수대기·진입 차단뿐이다. 이 문서는 그 `WAIT`를 **설명**할
  뿐이며 해제 근거로 사용할 수 없다.

## 통합 상태

- Phase 1(모듈·계약·문서·테스트)만 구현돼 있다.
- `run_all.py`에 설명 모델 회귀 검사 파일을 등록했다.
- 워크플로 배선과 Portal 라이브 게시는 이 범위에 포함되지 않는다.
  Portal 게시는 의미 있는 검증 패키지 HOLD 아래 남는다.

## 필수 실제 입력 검증

회귀 검사는 계약에 지정된 `2026-09-06/2349` 커밋 세대의 실제 브리지 패킷과
전체 원본 계보를 사용한다. 7종목의 WAIT 설명, 종목·사유 동일성, 청산 순서,
권한, JSON·Markdown 일치 및 재실행 바이트 동일성을 검사한다.
원본 패킷이나 필수 입력이 없거나 생산자 검증이 실패하면 검사도 실패한다.
누락을 `skip`으로 바꾸지 않는다. 부분 체크아웃에서는 해당 패킷의 보존 원본과
기존 식별 레지스트리가 참조하는 승인 근거 파일도 같은 커밋에서 준비해야 한다.

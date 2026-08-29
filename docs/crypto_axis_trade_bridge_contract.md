# P5-10 Crypto 5-Axis Entry & Exit Policy Bridge

이 모듈은 Crypto 공식 5개 축(`TREND`, `RISK_VOL`, `LIQUIDITY`,
`BREADTH`, `LEADERSHIP`)을 한 세대의 PAPER 의사결정 패킷에서 읽어,
후보 종목마다 진입·청산 규칙의 연결 상태를 보여 주는 읽기 전용 계층이다.

## 현재 동작

1. 원본 `crypto_paper_decision_snapshot`을 원본 validator로 전수 재검증한다.
2. 다섯 축의 `DEFINED/UNDEFINED`, 관측일, 사용 가능 시각, 경고를 그대로 보존한다.
3. 후보 종목마다 P5-08/P5-09 진입 상태를 이어 받되 다음 조건에서는 신규 진입을
   `WAIT`로 제한한다.
   - 공식 다섯 축 중 하나라도 `UNDEFINED`
   - 종합 Regime 계산·방향·임계값 정책이 `UNRATIFIED`
4. 상류 후보가 이미 `BLOCKED`이면 이를 `WAIT`로 완화하지 않고 `BLOCKED`로 보존한다.
5. 가상 체결 전 청산 상태는 `NOT_APPLICABLE_UNTIL_VIRTUAL_FILL`이다. 가상 체결 뒤에는
   기존 P7-13 청산 관리자가 담당하며 우선순위는 아래 순서로 고정한다.
   `HARD_EXIT → SECURITY_LIQUIDITY → RISK_REGIME → TREND → PROFIT_TRAIL → TIME_REVIEW`

## 명시적 비권한

- 이 구현은 5개 축에서 `RISK_ON/NEUTRAL/RISK_OFF/STRESS`를 새로 계산하지 않는다.
- 숫자 임계값, 종목 점수, 주문 수량, 매수가·매도가를 발명하지 않는다.
- 주문안, 주문, 거래소 호출, 계좌 조회, 출금, Production, Trading, REAL 자본 권한을 열지 않는다.
- 펀딩비·미결제약정·청산 규모·선물 베이시스는 P1-CR-09 후속 WBS이며 이 첫 구현의
  완료 조건을 막지 않는다.

## 다음 승인 지점

공식 5개 축이 모두 `DEFINED`가 된 뒤에도 종합 Regime 방향·임계값 정책이 별도로
비준돼야 진입 `WAIT` 제한을 해제할 수 있다. 그 전에는 5/5가 데이터 완성도를 뜻할
뿐 매수 허가를 뜻하지 않는다.

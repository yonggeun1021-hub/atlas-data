# P1-KR-06 Korea Risk / Volatility contract

이 단계는 KOSPI 종가 시계열에서 **원시 realized volatility와 drawdown만**
계산한다. Stress 판정, Regime 점수, Production 배선, 매매 권한은 범위 밖이다.

## Fail-closed input boundary

- 입력은 KRX 지수 수집기가 메모리 또는 stdin으로 넘기는 exact-session envelope다.
- 날짜별 종가 누락, 중복, 정렬 오류, 숫자형 JSON 값은 허용하지 않는다.
- `available_at`은 비준된 `publication_timing_source`와 로컬 cutoff보다 빠를 수 없다.
- source, index identity, calendar, lookback 중 하나라도 미비준이면 계산하지 않는다.
- 기본 정책은 `UNRATIFIED`다. KRX 공식 게시 시각 근거가 확정되기 전에는 live
  producer나 운영 산출물을 만들지 않는다.

## Transform

- return: close-to-close simple return
- realized volatility: `sqrt(mean(return^2) * annualization_sessions)`
- drawdown: 지정 창 안의 close peak-to-trough current/max fraction
- exact expected session dates만 허용하며 휴일 추정이나 forward-fill을 하지 않는다.

결과에는 입력 종가, 일별 수익률, 원시 API row를 넣지 않는다. 따라서 결과만으로
원천 가격을 복원할 수 없으며 vendor/API 데이터는 저장소에 남지 않는다.

`FORWARD_SHADOW`는 `available_at <= fetched_at <= decision_at`을 요구한다.
`HISTORICAL_REPLAY`는 decision을 허용하지 않고 `CAUSAL_REPLAY_ONLY`로 표시한다.
두 모드 모두 `authoritative_historical_pit=false`이며 분류 권한은 모두 false다.

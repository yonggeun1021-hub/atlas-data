# P1-KR-06 Korea Risk / Volatility contract

이 단계는 KOSPI 종가 시계열에서 **원시 realized volatility와 drawdown만**
계산한다. Stress 판정, Regime 점수, Production 배선, 매매 권한은 범위 밖이다.

## Ratified availability boundary (2026-08-26)

KRX 응답에서 공식 publication timestamp를 관측하지 못한다는 사실과 Atlas가
실제로 해당 응답을 받은 시각을 분리한다. `source_publication_time`은 계속 `null`이고
`UNKNOWN_NOT_INFERRED`다. 장 마감 당일 응답은 decision input이 아니다.

정규 아침 KRX collector가 전 거래일을 `confirmed_through`로 확정한 뒤, 동일한
`BAS_DD`의 KRX Open API KOSPI 응답에서 exact `IDX_NM=코스피` 행을 처음 성공적으로
관측한 실제 시각만 `atlas_observed_available_at`으로 기록한다. observation date별
receipt는 append-only다. receipt에는 response hash와 행 수만 남고 지수값·raw row는
남지 않는다.

이 비준은 **temporal input qualification만** 허용한다. 기존
`korea_risk_input_policy.json`의 realized-vol/drawdown lookback은 아직 미비준이고,
Stress·Regime·Production·Stage·Buy·Action·Order·Trading authority는 모두 false다.

운영 receipt 경로:

```text
data/observations/korea_risk_availability/{OBSERVATION_DATE}/receipt.json
```

## Fail-closed transform input boundary

- 입력은 KRX 지수 수집기가 메모리 또는 stdin으로 넘기는 exact-session envelope다.
- 날짜별 종가 누락, 중복, 정렬 오류, 숫자형 JSON 값은 허용하지 않는다.
- `available_at`은 별도 비준된 next-session receipt보다 빠를 수 없다. source의 공식
  publication time으로 표현하거나 추론하지 않는다.
- source, index identity, calendar, lookback 중 하나라도 미비준이면 계산하지 않는다.
- feature parameter 기본 정책은 계속 `UNRATIFIED`다. availability receipt만으로
  live risk feature producer나 운영 Regime 산출물을 만들지 않는다.

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

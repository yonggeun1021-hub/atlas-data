# Crypto 최근 30일 참고 판정 계약

이 산출물은 사용자가 현재 Crypto 시장 흐름을 이해할 수 있도록 최신
Kraken 확정 일봉만 계산한 **read-only 참고 관찰값**이다. 정식 CR-06/07
point-in-time 이력, Regime 판정, 투자 후보 승격, 주문 또는 자동매매 권한이
아니다.

## 계산 범위

- 원천: 같은 날짜에 고정·해시 검증된 Kraken Assets, AssetPairs, OHLC 묶음
- 현재 진행 중인 UTC 일봉: 항상 제외
- 표본: 결정 시점에 이미 비준·유효한 taxonomy를 적용한 USD 거래대금 상위
  100개 eligible crypto
- 기간: 최신 확정 종가와 정확히 7일·30일 전 확정 종가 비교
- 표시값: BTC·ETH·알트 동일가중 평균·알트 중앙값 수익률, 상승 종목 수,
  BTC 수익률을 넘은 종목 수, 30일 수익률 상위 5개
- 해석: 가장 높은 BTC/ETH/알트 동일가중 원시 수익률 그룹만 표시하며 별도
  임계값이나 점수는 만들지 않는다.

## 엄격한 경계

현재 taxonomy를 과거 시점에 소급해 historical replay를 보충하지 않는다.
따라서 이 결과는 `CURRENT_DECISION_TIME_REFERENCE_NOT_PIT_REPLAY`로만
발행되며 Regime·Production·Stage·Buy·Action·Order·Trading 권한은 전부
`false`다. 데이터나 taxonomy가 부족하면 숫자를 추정하지 않고 생성에
실패한다.

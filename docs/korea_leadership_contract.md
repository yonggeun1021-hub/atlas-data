# P1-KR-07 Korea Leadership contract

KRX 지수 종가로 KOSPI/KOSDAQ 및 비준된 sector/theme 지수의 **원시 상대수익률**을
재현한다. 순위, leader 분류, Trend/Breadth 방향, Regime 점수는 만들지 않는다.

## Identity and PIT taxonomy

- series identity는 KRX 분류와 지수명을 결합한 안정 식별자로 비준해야 한다.
- 각 series는 `role`, 비교 benchmark, `effective_from/to`를 명시한다.
- 같은 날짜에 identity가 중복 적용되거나 benchmark가 빠지면 전체 계산을 중단한다.
- 현재 구성 정책은 `UNRATIFIED`다. 공식 sector/theme mapping과 게시 시각 근거가
  확정되기 전에는 현재 분류를 과거에 소급하거나 live producer를 만들지 않는다.

## Transform boundary

입력은 exact-session close envelope이며 원시 row는 메모리/stdin에서만 사용한다.
결과에는 각 series의 cumulative gross return과 benchmark 대비 relative strength만
남겨 원천 종가를 재구성할 수 없게 한다. `available_at` 시간 순서와 KST offset을
검증하며 historical 입력은 `CAUSAL_REPLAY_ONLY`로 격리한다.

Downstream 계약은 packet의 `payload_sha256`, transient 입력 SHA, 비준된
Leadership 정책 파일 SHA, exact session window, effective-dated taxonomy 표시를
검증할 수 있다. 이 lineage 추가는 분류 권한을 열지 않으며 원천 종가도 보존하지
않는다.

모든 classification/ranking/Regime/Production/trading authority는 false다.

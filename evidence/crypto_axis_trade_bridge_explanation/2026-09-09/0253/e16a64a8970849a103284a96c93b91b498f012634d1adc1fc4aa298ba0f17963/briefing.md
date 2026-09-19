# Crypto 5축 진입·청산 설명 (읽기 전용)

- 생성 시각(UTC): 2026-09-09T02:53:45Z
- 운영일(KST): 2026-09-09
- 원본 브리지 생성 ID: e16a64a8970849a103284a96c93b91b498f012634d1adc1fc4aa298ba0f17963
- 원본 브리지 payload_sha256: 3dbf18de02164578396bc90782f390b9c68dfa7c44908c455779e7a0d4e29102
- 종합 Regime 정책: UNRATIFIED (현재 승인된 국면: UNKNOWN)
- 권한: 모든 실행 권한 false. 이 문서는 기존 판정의 설명일 뿐 매수·매도·주문 허가가 아니다.

## 1. 공식 5개 축 현황 (3/5 확보)

| 축 | 상태 | 관측일 | 사용 가능 시각 | 진입 보류 사유 | 경고 |
| --- | --- | --- | --- | --- | --- |
| 추세(TREND) | 확보됨(DEFINED) | 2026-09-08 | 2026-09-09T00:41:37Z | 아니오 | REGIME_INTERPRETATION_UNAUTHORIZED |
| 위험·변동성(RISK_VOL) | 확보됨(DEFINED) | 2026-09-08 | 2026-09-09T00:41:37Z | 아니오 | REGIME_INTERPRETATION_UNAUTHORIZED, STRESS_THRESHOLDS_UNCALIBRATED |
| 유동성(LIQUIDITY) | 미확보(UNDEFINED) | - | - | 예 | LIVE_AXIS_EVIDENCE_UNAVAILABLE |
| 시장 폭(BREADTH) | 확보됨(DEFINED) | 2026-09-08 | 2026-09-09T00:54:06Z | 아니오 | REGIME_INTERPRETATION_UNAUTHORIZED |
| 주도력(LEADERSHIP) | 미확보(UNDEFINED) | - | - | 예 | LIVE_AXIS_EVIDENCE_UNAVAILABLE |

## 2. 단계별 종목 (총 8종목)

### 매수대기 (7종목)

- KRW-BTC
- KRW-ETH
- KRW-LINK
- KRW-SHIB
- KRW-SOL
- KRW-SUI
- KRW-XRP

### 진입 차단 (1종목)

- KRW-WLD

### 진입검토 (0종목)

- (없음) 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.

### 보유 (0종목)

- (없음) 사유 `NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET`: 이 입력 패킷에는 가상 체결 포지션이 없어 이 단계에 표시할 종목이 없다. 실제 계좌의 보유 자산 유무를 뜻하지 않는다.

### 축소 (0종목)

- (없음) 사유 `NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET`: 이 입력 패킷에는 가상 체결 포지션이 없어 이 단계에 표시할 종목이 없다. 실제 계좌의 보유 자산 유무를 뜻하지 않는다.

### 청산검토 (0종목)

- (없음) 사유 `NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET`: 이 입력 패킷에는 가상 체결 포지션이 없어 이 단계에 표시할 종목이 없다. 실제 계좌의 보유 자산 유무를 뜻하지 않는다.

## 3. 종목별 설명

### KRW-BTC (BTC)

- 단계: 매수대기 (`WAIT`)
- 상류 판정: `WATCH` / `CRITERIA_UNKNOWN:MATERIAL_BLOCKER,OVEREXTENSION,REGIME,RELATIVE_STRENGTH,TREND,VOLUME_LIQUIDITY`
- 진입 사유 `UPSTREAM_STATE:WATCH`: 상류 P5-08/P5-09 판정 상태는 WATCH이다.
- 진입 사유 `OFFICIAL_AXES_INCOMPLETE:LIQUIDITY,LEADERSHIP`: 공식 5개 축 가운데 LIQUIDITY,LEADERSHIP이(가) 아직 미확보라 신규 진입이 보류된다.
- 진입 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 진입 보류 축 `LIQUIDITY`: 유동성(LIQUIDITY) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 진입 보류 축 `LEADERSHIP`: 주도력(LEADERSHIP) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 청산 단계: 가상 체결 전이라 청산 단계가 아직 해당되지 않음 (`NOT_APPLICABLE_UNTIL_VIRTUAL_FILL`)
- 청산 사유 `NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET`: 이 입력 패킷에는 가상 체결 포지션이 없어 이 단계에 표시할 종목이 없다. 실제 계좌의 보유 자산 유무를 뜻하지 않는다.
- 청산 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 청산 사유 `P7_13_HARD_EXIT_SECURITY_LIQUIDITY_PRIORITY_PRESERVED`: 가상 체결 이후 청산은 기존 P7-13 관리자가 담당하며 하드 청산·안전성/유동성 우선순위가 그대로 유지된다.
- 청산 우선순위: HARD_EXIT → SECURITY_LIQUIDITY → RISK_REGIME → TREND → PROFIT_TRAIL → TIME_REVIEW (전 종목 동일)

### KRW-ETH (ETH)

- 단계: 매수대기 (`WAIT`)
- 상류 판정: `WATCH` / `CRITERIA_UNKNOWN:MATERIAL_BLOCKER,OVEREXTENSION,REGIME,RELATIVE_STRENGTH,TREND,VOLUME_LIQUIDITY`
- 진입 사유 `UPSTREAM_STATE:WATCH`: 상류 P5-08/P5-09 판정 상태는 WATCH이다.
- 진입 사유 `OFFICIAL_AXES_INCOMPLETE:LIQUIDITY,LEADERSHIP`: 공식 5개 축 가운데 LIQUIDITY,LEADERSHIP이(가) 아직 미확보라 신규 진입이 보류된다.
- 진입 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 진입 보류 축 `LIQUIDITY`: 유동성(LIQUIDITY) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 진입 보류 축 `LEADERSHIP`: 주도력(LEADERSHIP) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 청산 단계: 가상 체결 전이라 청산 단계가 아직 해당되지 않음 (`NOT_APPLICABLE_UNTIL_VIRTUAL_FILL`)
- 청산 사유 `NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET`: 이 입력 패킷에는 가상 체결 포지션이 없어 이 단계에 표시할 종목이 없다. 실제 계좌의 보유 자산 유무를 뜻하지 않는다.
- 청산 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 청산 사유 `P7_13_HARD_EXIT_SECURITY_LIQUIDITY_PRIORITY_PRESERVED`: 가상 체결 이후 청산은 기존 P7-13 관리자가 담당하며 하드 청산·안전성/유동성 우선순위가 그대로 유지된다.
- 청산 우선순위: HARD_EXIT → SECURITY_LIQUIDITY → RISK_REGIME → TREND → PROFIT_TRAIL → TIME_REVIEW (전 종목 동일)

### KRW-LINK (LINK)

- 단계: 매수대기 (`WAIT`)
- 상류 판정: `WATCH` / `CRITERIA_UNKNOWN:MATERIAL_BLOCKER,OVEREXTENSION,REGIME,RELATIVE_STRENGTH,TREND,VOLUME_LIQUIDITY`
- 진입 사유 `UPSTREAM_STATE:WATCH`: 상류 P5-08/P5-09 판정 상태는 WATCH이다.
- 진입 사유 `OFFICIAL_AXES_INCOMPLETE:LIQUIDITY,LEADERSHIP`: 공식 5개 축 가운데 LIQUIDITY,LEADERSHIP이(가) 아직 미확보라 신규 진입이 보류된다.
- 진입 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 진입 보류 축 `LIQUIDITY`: 유동성(LIQUIDITY) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 진입 보류 축 `LEADERSHIP`: 주도력(LEADERSHIP) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 청산 단계: 가상 체결 전이라 청산 단계가 아직 해당되지 않음 (`NOT_APPLICABLE_UNTIL_VIRTUAL_FILL`)
- 청산 사유 `NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET`: 이 입력 패킷에는 가상 체결 포지션이 없어 이 단계에 표시할 종목이 없다. 실제 계좌의 보유 자산 유무를 뜻하지 않는다.
- 청산 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 청산 사유 `P7_13_HARD_EXIT_SECURITY_LIQUIDITY_PRIORITY_PRESERVED`: 가상 체결 이후 청산은 기존 P7-13 관리자가 담당하며 하드 청산·안전성/유동성 우선순위가 그대로 유지된다.
- 청산 우선순위: HARD_EXIT → SECURITY_LIQUIDITY → RISK_REGIME → TREND → PROFIT_TRAIL → TIME_REVIEW (전 종목 동일)

### KRW-SHIB (SHIB)

- 단계: 매수대기 (`WAIT`)
- 상류 판정: `WATCH` / `CRITERIA_UNKNOWN:MATERIAL_BLOCKER,OVEREXTENSION,REGIME,RELATIVE_STRENGTH,TREND,VOLUME_LIQUIDITY`
- 진입 사유 `UPSTREAM_STATE:WATCH`: 상류 P5-08/P5-09 판정 상태는 WATCH이다.
- 진입 사유 `OFFICIAL_AXES_INCOMPLETE:LIQUIDITY,LEADERSHIP`: 공식 5개 축 가운데 LIQUIDITY,LEADERSHIP이(가) 아직 미확보라 신규 진입이 보류된다.
- 진입 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 진입 보류 축 `LIQUIDITY`: 유동성(LIQUIDITY) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 진입 보류 축 `LEADERSHIP`: 주도력(LEADERSHIP) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 청산 단계: 가상 체결 전이라 청산 단계가 아직 해당되지 않음 (`NOT_APPLICABLE_UNTIL_VIRTUAL_FILL`)
- 청산 사유 `NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET`: 이 입력 패킷에는 가상 체결 포지션이 없어 이 단계에 표시할 종목이 없다. 실제 계좌의 보유 자산 유무를 뜻하지 않는다.
- 청산 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 청산 사유 `P7_13_HARD_EXIT_SECURITY_LIQUIDITY_PRIORITY_PRESERVED`: 가상 체결 이후 청산은 기존 P7-13 관리자가 담당하며 하드 청산·안전성/유동성 우선순위가 그대로 유지된다.
- 청산 우선순위: HARD_EXIT → SECURITY_LIQUIDITY → RISK_REGIME → TREND → PROFIT_TRAIL → TIME_REVIEW (전 종목 동일)

### KRW-SOL (SOL)

- 단계: 매수대기 (`WAIT`)
- 상류 판정: `WATCH` / `CRITERIA_UNKNOWN:MATERIAL_BLOCKER,OVEREXTENSION,REGIME,RELATIVE_STRENGTH,TREND,VOLUME_LIQUIDITY`
- 진입 사유 `UPSTREAM_STATE:WATCH`: 상류 P5-08/P5-09 판정 상태는 WATCH이다.
- 진입 사유 `OFFICIAL_AXES_INCOMPLETE:LIQUIDITY,LEADERSHIP`: 공식 5개 축 가운데 LIQUIDITY,LEADERSHIP이(가) 아직 미확보라 신규 진입이 보류된다.
- 진입 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 진입 보류 축 `LIQUIDITY`: 유동성(LIQUIDITY) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 진입 보류 축 `LEADERSHIP`: 주도력(LEADERSHIP) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 청산 단계: 가상 체결 전이라 청산 단계가 아직 해당되지 않음 (`NOT_APPLICABLE_UNTIL_VIRTUAL_FILL`)
- 청산 사유 `NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET`: 이 입력 패킷에는 가상 체결 포지션이 없어 이 단계에 표시할 종목이 없다. 실제 계좌의 보유 자산 유무를 뜻하지 않는다.
- 청산 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 청산 사유 `P7_13_HARD_EXIT_SECURITY_LIQUIDITY_PRIORITY_PRESERVED`: 가상 체결 이후 청산은 기존 P7-13 관리자가 담당하며 하드 청산·안전성/유동성 우선순위가 그대로 유지된다.
- 청산 우선순위: HARD_EXIT → SECURITY_LIQUIDITY → RISK_REGIME → TREND → PROFIT_TRAIL → TIME_REVIEW (전 종목 동일)

### KRW-SUI (SUI)

- 단계: 매수대기 (`WAIT`)
- 상류 판정: `WATCH` / `CRITERIA_UNKNOWN:MATERIAL_BLOCKER,OVEREXTENSION,REGIME,RELATIVE_STRENGTH,TREND,VOLUME_LIQUIDITY`
- 진입 사유 `UPSTREAM_STATE:WATCH`: 상류 P5-08/P5-09 판정 상태는 WATCH이다.
- 진입 사유 `OFFICIAL_AXES_INCOMPLETE:LIQUIDITY,LEADERSHIP`: 공식 5개 축 가운데 LIQUIDITY,LEADERSHIP이(가) 아직 미확보라 신규 진입이 보류된다.
- 진입 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 진입 보류 축 `LIQUIDITY`: 유동성(LIQUIDITY) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 진입 보류 축 `LEADERSHIP`: 주도력(LEADERSHIP) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 청산 단계: 가상 체결 전이라 청산 단계가 아직 해당되지 않음 (`NOT_APPLICABLE_UNTIL_VIRTUAL_FILL`)
- 청산 사유 `NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET`: 이 입력 패킷에는 가상 체결 포지션이 없어 이 단계에 표시할 종목이 없다. 실제 계좌의 보유 자산 유무를 뜻하지 않는다.
- 청산 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 청산 사유 `P7_13_HARD_EXIT_SECURITY_LIQUIDITY_PRIORITY_PRESERVED`: 가상 체결 이후 청산은 기존 P7-13 관리자가 담당하며 하드 청산·안전성/유동성 우선순위가 그대로 유지된다.
- 청산 우선순위: HARD_EXIT → SECURITY_LIQUIDITY → RISK_REGIME → TREND → PROFIT_TRAIL → TIME_REVIEW (전 종목 동일)

### KRW-WLD (WLD)

- 단계: 진입 차단 (`BLOCKED`)
- 상류 판정: `BLOCKED` / `CRITERIA_FAILED:MATERIAL_BLOCKER`
- 진입 사유 `UPSTREAM_STATE:BLOCKED`: 상류 P5-08/P5-09 판정 상태는 BLOCKED이다.
- 진입 사유 `OFFICIAL_AXES_INCOMPLETE:LIQUIDITY,LEADERSHIP`: 공식 5개 축 가운데 LIQUIDITY,LEADERSHIP이(가) 아직 미확보라 신규 진입이 보류된다.
- 진입 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 진입 보류 축 `LIQUIDITY`: 유동성(LIQUIDITY) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 진입 보류 축 `LEADERSHIP`: 주도력(LEADERSHIP) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 청산 단계: 가상 체결 전이라 청산 단계가 아직 해당되지 않음 (`NOT_APPLICABLE_UNTIL_VIRTUAL_FILL`)
- 청산 사유 `NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET`: 이 입력 패킷에는 가상 체결 포지션이 없어 이 단계에 표시할 종목이 없다. 실제 계좌의 보유 자산 유무를 뜻하지 않는다.
- 청산 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 청산 사유 `P7_13_HARD_EXIT_SECURITY_LIQUIDITY_PRIORITY_PRESERVED`: 가상 체결 이후 청산은 기존 P7-13 관리자가 담당하며 하드 청산·안전성/유동성 우선순위가 그대로 유지된다.
- 청산 우선순위: HARD_EXIT → SECURITY_LIQUIDITY → RISK_REGIME → TREND → PROFIT_TRAIL → TIME_REVIEW (전 종목 동일)

### KRW-XRP (XRP)

- 단계: 매수대기 (`WAIT`)
- 상류 판정: `WATCH` / `CRITERIA_UNKNOWN:MATERIAL_BLOCKER,OVEREXTENSION,REGIME,RELATIVE_STRENGTH,TREND,VOLUME_LIQUIDITY`
- 진입 사유 `UPSTREAM_STATE:WATCH`: 상류 P5-08/P5-09 판정 상태는 WATCH이다.
- 진입 사유 `OFFICIAL_AXES_INCOMPLETE:LIQUIDITY,LEADERSHIP`: 공식 5개 축 가운데 LIQUIDITY,LEADERSHIP이(가) 아직 미확보라 신규 진입이 보류된다.
- 진입 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 진입 보류 축 `LIQUIDITY`: 유동성(LIQUIDITY) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 진입 보류 축 `LEADERSHIP`: 주도력(LEADERSHIP) 축이 미확보라 이 종목의 신규 진입이 보류된다.
- 청산 단계: 가상 체결 전이라 청산 단계가 아직 해당되지 않음 (`NOT_APPLICABLE_UNTIL_VIRTUAL_FILL`)
- 청산 사유 `NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET`: 이 입력 패킷에는 가상 체결 포지션이 없어 이 단계에 표시할 종목이 없다. 실제 계좌의 보유 자산 유무를 뜻하지 않는다.
- 청산 사유 `AGGREGATE_POLICY_UNRATIFIED`: 종합 Regime 방향·임계값 정책이 아직 비준되지 않아 신규 진입은 매수대기로 제한된다.
- 청산 사유 `P7_13_HARD_EXIT_SECURITY_LIQUIDITY_PRIORITY_PRESERVED`: 가상 체결 이후 청산은 기존 P7-13 관리자가 담당하며 하드 청산·안전성/유동성 우선순위가 그대로 유지된다.
- 청산 우선순위: HARD_EXIT → SECURITY_LIQUIDITY → RISK_REGIME → TREND → PROFIT_TRAIL → TIME_REVIEW (전 종목 동일)

## 4. 청산 우선순위 사다리

1. `HARD_EXIT` — 하드 청산(최우선)
2. `SECURITY_LIQUIDITY` — 종목 안전성·유동성
3. `RISK_REGIME` — 위험·국면
4. `TREND` — 추세
5. `PROFIT_TRAIL` — 이익 보전
6. `TIME_REVIEW` — 기간 경과 검토


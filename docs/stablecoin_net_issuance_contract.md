# Stablecoin Net Issuance transform contract

Status: P1-CR-03 offline evidence transform v1

Dependency: P1-CR-02 endpoint / revision / PIT contract

## Boundary

이 transform은 Crypto Liquidity의 **core candidate evidence**를 만든다. Regime
score, threshold, weight, Production wiring, trading action을 만들거나 승인하지
않는다. 입력은 검증된 일별 PIT capture 한 개의
`stablecoincharts_all` historical series뿐이다. `stablecoinchains`와
`stablecoins_withprices` 같은 live snapshot을 historical issuance 계산에 쓰지
않는다.

구현은 `.github/scripts/stablecoin_net_issuance.py`이며 기본 실행은 stdout만
사용한다. 저장소의 tracked output으로 자동 publish하는 workflow 배선은 없다.

## Measurement decision

USD-pegged stablecoin의 발행·상환을 가격 변화와 분리하기 위해 공급량은
`totalCirculating.peggedUSD`를 사용한다.

- `S(t,v)`: snapshot vintage `v`가 보고한 observation date `t`의
  `totalCirculating.peggedUSD`
- Daily Net Issuance: `S(t,v) - S(t-1 calendar day,v)`
- Weekly Net Issuance: `S(t,v) - S(t-7 calendar days,v)`
- Unit: `USD_PEGGED_TOKEN`

`totalCirculatingUSD.peggedUSD`는 가격·depeg 영향을 포함할 수 있으므로
`gross_supply_usd_valued_diagnostic`으로만 보존한다. 이 값의 변화는 v1 Net
Issuance가 아니다.

## PIT and revision policy

- 한 결과는 하나의 capture vintage `v` 안에서만 계산한다. 서로 다른 날짜에
  받은 chart row를 섞지 않는다.
- source snapshot date, fetch time, endpoint, response SHA-256,
  `transform_version=stablecoin_net_issuance/v1`을 결과에 보존한다.
- `lineage`에 `vintage_date`, `available_at`, `revision_policy`,
  `point_in_time_required`, verification/evidence grade를 함께 보존한다.
- 과거 chart row가 후속 capture에서 수정되면 후속 vintage의 transform 결과도
  달라질 수 있다. 이전 vintage 결과를 덮어쓰거나 어느 쪽을 임의로 authority로
  고르지 않는다.
- direct append-only capture가 없으면 현재 history 응답으로 과거 vintage를
  재구성하지 않는다.

## Missing-data policy

- T-1 또는 T-7 **정확한 달력 날짜**가 없으면 각각
  `MISSING_EXACT_PRIOR`이며 값은 `null`이다.
- 현재 날짜의 native supply가 없으면 `MISSING_CURRENT`이며 값은 `null`이다.
- 주말·휴일을 건너뛰거나 가장 가까운 날짜를 대신 선택하지 않는다.
- interpolation, forward-fill, zero-fill을 하지 않는다.
- 중복 날짜, 미래 날짜, 음수·비유한·비숫자 공급량은 fail-closed한다.

## Usage

검증된 PIT snapshot에서 stdout으로 계산:

```bash
python .github/scripts/stablecoin_net_issuance.py \
  --snapshot-dir evidence/stablecoin/raw/2026-08-18
```

명시한 임시 경로로 atomic write:

```bash
python .github/scripts/stablecoin_net_issuance.py \
  --snapshot-dir evidence/stablecoin/raw/2026-08-18 \
  --out /tmp/stablecoin_net_issuance.json
```

# P3-09 Supply-Demand / Scarcity radar contract

Status: policy-gated capability; Crypto PIT population is scheduled, while the
candidate policy and Korea/US population are not implemented.

## Boundary

`discovery/supply_demand.py` consumes exactly three caller-declared, ordered
evidence dates for one supply/demand measurement. It records the raw values,
the two consecutive changes, the change in those changes, and every point's
status and source identity. It does not
infer missing dates, fill missing values with zero, choose a measurement, or
decide whether a larger or smaller value means improvement.

The current registered source fragments are intentionally different by market:

- Crypto: DefiLlama stablecoin aggregate native supply; the existing
  `stablecoin_net_issuance/v1` transform validates one append-only PIT vintage.
  `.github/scripts/stablecoin_supply_demand_population.py` selects the three
  exact calendar dates ending on that UTC snapshot date and publishes one
  immutable policy-neutral packet under
  `evidence/supply_demand/crypto/YYYY-MM-DD/rev-001.json`.
- Korea: KRX Information Data System per-security investor net value/volume via
  pykrx, KRX-only. NXT is excluded and primary-source release time remains
  unverified, so an `available_at` claim cannot be inferred from collection time.
- US: SEC EDGAR may evidence security supply, but no default shares-outstanding
  series or comparison basis is selected.

These are registered coverage fragments, not a source hierarchy and not proof
that the three markets are comparable.

## Candidate policy gate

Without a policy, the output is raw observation only and creates zero cases.
An external policy must be structurally valid, effective at the last evidence
date, explicitly `RATIFIED`, and bind the exact market, series, measurement,
metric type, unit, frequency, and comparison basis. The policy alone specifies:

- `HIGHER_IS_IMPROVEMENT` or `LOWER_IS_IMPROVEMENT`;
- the non-negative minimum direction-adjusted latest change; and
- the non-negative minimum direction-adjusted acceleration change.

There is no repository default policy. A matching case preserves all three
source identities plus the policy hash and ratification proof. It still grants
no importance, ranking, investability, Stage transition, Production, rule, or
trading authority.

## Persisted packet validation

`supply_demand_radar_packet/3`는 정규화한 `source_policy` 전체를 보존한다.
`validate_packet()`은 ingestion과 동일한 정책 validator를 소비 시점에 다시
실행하고, 정책 SHA·유효기간·exact series rule·방향·threshold로 policy status와
case 생성을 재계산한다.

또한 저장된 3개 period/status/source lineage, canonical decimal
values, prior/latest/acceleration 산술, missing-evidence 상태, policy 결과와 case
set, summary 및 권한 봉쇄를 다시 검증한다. 생성된 case는 해당 series의 3개
numeric/source evidence에 역대조되고, 보존된 방향·threshold 비교도 다시
수행한다. self-rehashed arithmetic, lineage, case-evidence, authority drift는
실패한다.

full candidate-policy rule 본문은 packet에 포함되지 않고 hash만 남으므로,
standalone validator는 policy hash의 원문 진위를 증명하지 않는다. 그 범위는
원 policy를 직접 검증하는 `build_packet()` 경로에 남는다.

## Operation

The generic helper has no network and only writes to an explicit temporary
`--out` path. The Crypto population adapter is wired immediately after the
existing stablecoin capture. It reuses only validated committed-or-new capture
bytes, records the exact response hash and Atlas fetch time, and never infers a
provider publication timestamp. An existing `rev-001.json` must be byte
identical; it is never overwritten. Missing exact calendar dates remain
`UNKNOWN_EVIDENCE`.

Korea/US population, improvement direction and minimum change, cross-market
comparability, source hierarchy, and candidate ranking remain open WBS gates.

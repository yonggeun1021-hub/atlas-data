# Business Acceleration Radar Contract (P3-05)

Status: comparable published-growth pattern and radar-case capability
implemented. Cross-company coverage, source hierarchy, importance thresholds,
candidate ranking, and live population remain unratified or unimplemented.

## Purpose

`discovery/business_acceleration.py` consumes three consecutive
`evidence_envelope/1` observations for one company and one unchanged published
growth-rate measurement. Supported metric classes are revenue growth, order
growth, and guidance growth; supported periods are monthly and quarterly.

The helper uses the source-published percentage values. It does not parse
management text, infer a metric, convert currency, annualize, fill a missing
period, or compare unlike measurement bases. A caller must supply the exact
measurement identity, comparison basis, frequency, Atlas asset ID, and three
evidence envelopes.

## Pattern contract

The periods must be consecutive calendar month-ends or quarter-ends. For
published growth rates `v1`, `v2`, and `v3`, the helper records:

- prior change: `v2 - v1` percentage points;
- latest change: `v3 - v2` percentage points; and
- change in acceleration: `(v3 - v2) - (v2 - v1)` percentage points.

A `TWO_STEP_ACCELERATION_OBSERVED` radar case is created only when both the
prior and latest changes are strictly positive. `LATEST_STEP_UP_ONLY` records
one latest increase without creating a case. A zero or negative latest change
is `LATEST_STEP_NOT_UP`. These names describe published numeric motion only;
they do not claim business quality, valuation, persistence, materiality, or an
investment opportunity.

All arithmetic uses decimal strings, 50-digit intermediate precision, and
12-decimal HALF_EVEN output. Floats, NaN/infinity, mixed units, missing periods,
non-consecutive dates, subject/measurement drift, and duplicate series fail
closed.

## Evidence and missing data

An available point must be a consistent consumable evidence envelope with
acquisition provenance, a registered partial-coverage source ID, HTTPS URL,
source SHA-256, observed `available_at`, retrieval timestamp, `pct` unit, and
retrieval no later than the packet's `as_of_utc`.

`EVIDENCE_BLOCKED` and `EVIDENCE_UNRESOLVED` remain
`UNKNOWN_EVIDENCE`. They are never replaced with zero, neutral, a previous
period, or another source. The source registry does not establish a hierarchy
and automatic fallback is not performed.

## Case and authority boundary

A created case preserves the three source identities and exact values, plus
the transparent arithmetic that caused it to be recorded. Every case retains:

- `importance = UNRATIFIED`;
- `candidate_rank = null`;
- `candidate_eligible = false`;
- `stage_transition = null`; and
- `action = null`.

Source ranking, importance ranking, cross-company comparison, candidate
ranking, Rule evaluation, Stage promotion, Production, and trading authorities
are false. This capability cannot convert a radar record into Ready, Buy, or an
order.

## Persisted packet validation

`validate_packet()`은 저장된 모든 series result의 3기간 연속성, 12자리 decimal
정규화, prior/latest/acceleration 변화, pattern, case 생성 여부, 요약 카운트를
다시 계산한다. 생성된 case는 보존된 3개 numeric value와 source lineage까지
해당 series result에 역대조하고 모든 권한 봉쇄값을 확인한다. 값을 바꾼 뒤
`payload_sha256`을 다시 계산해도 semantic drift는 통과하지 않는다.

모든 series result(schema `business_acceleration_radar_packet/2`)는 case
생성 여부와 무관하게 `evidence_source`에 3개 기간 각각의 원문 `numeric_value`,
`unit`, `source_identity`를 최소 충분한 frozen snapshot으로 보존한다 (전체
evidence envelope 전체가 아니라, 표준 재구축에 필요한 최소 필드만). `pattern`
이 `UNKNOWN_EVIDENCE`이면 `evidence_source`는 `null`이다.

`validate_packet()`은 매 non-`UNKNOWN_EVIDENCE` series마다 `evidence_source`의
각 항목을 `_validate_source()`로 독립 재검증하고 (host/HTTPS/SHA-256 형식/시간
순서), 렌더링한 numeric_value가 `values_pct`와 정확히 일치하는지 대조한다.
따라서 case가 없는 packet도 standalone validator가 값이 자기일관적인 숫자가
아니라 실제 sourced evidence에서 나왔음을 packet 내부만으로 재증명한다. case로
승격된 series의 `confirmed_evidence`는 동일한 snapshot의 사본이다.

## Offline command

```bash
python3 discovery/business_acceleration.py /tmp/business-acceleration-input.json \
  --out /tmp/business-acceleration.json
```

The module has no network client and writes only the requested atomic output.
It does not publish a tracked radar, modify existing Discovery cases, or wire a
workflow.

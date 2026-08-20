# P3-09 Supply-Demand / Scarcity radar contract

Status: policy-gated offline capability; live population and candidate policy are not implemented.

## Boundary

`discovery/supply_demand.py` consumes exactly three caller-declared, ordered
evidence dates for one supply/demand measurement. It records the raw values,
the two consecutive changes, the change in those changes, and every point's
status and source identity. It does not
infer missing dates, fill missing values with zero, choose a measurement, or
decide whether a larger or smaller value means improvement.

The current registered source fragments are intentionally different by market:

- Crypto: DefiLlama stablecoin aggregate native supply; the existing
  `stablecoin_net_issuance/v1` transform is partial upstream evidence.
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

## Operation

The helper has no network or workflow wiring and only writes to the explicit
`--out` path. A failed run leaves an existing output unchanged. Live radar
population, US metric selection, Korea release-time evidence, cross-market
comparability, source hierarchy, and candidate ranking remain open WBS gates.

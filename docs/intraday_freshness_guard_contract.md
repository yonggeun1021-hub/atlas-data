# P9-01 intraday price / volume freshness guard

This offline guard evaluates caller-supplied price/volume observations against
an external, pre-ratified freshness policy. The repository contains no default
age or transport-delay threshold. A policy must bind all three markets and be
effective at the batch observation time.

For every quote the guard preserves the provider timestamp, Atlas receive time,
price, volume, provider identity, and source SHA. Provider age and transport
delay are integer seconds. A value equal to its approved maximum remains fresh;
only a value above the maximum is stale.

`fresh_for_intraday_consumption` concerns data freshness only. It is not ENTRY
or EXIT eligibility and cannot produce an action, order, broker submission, or
trading authority. Feed/provider selection and live Production wiring remain
outside this capability.

Malformed clocks, future timestamps, duplicate assets, digest drift, unratified
or ineffective policies, and policy/market coverage gaps fail closed. The CLI
has no network imports and writes only outside the repository.

After validating the caller packet's exact digest, lineage uses the semantic
batch SHA with quotes in canonical market/asset order. Input list permutation
therefore cannot change an otherwise identical result.

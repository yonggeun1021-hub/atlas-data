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

## Output schema `intraday_freshness_result/2`

The output packet embeds the full, original ratified policy packet
(`policy_packet`) rather than a shallow subset. `validate_output` re-runs the
same ratified-policy validator the ingestion path uses — RATIFIED status,
`intraday_freshness_guard/1` binding, effective window covering the observed
time, exact market coverage, and the policy packet's own self-consistent
`packet_sha256` are all re-checked at the consumption boundary, not only at
ingestion. `policy_id` is duplicated at the top level and cross-checked
against `policy_packet`, and `lineage.policy_sha256` is cross-checked against
the re-validated policy's own digest. A consumer that only re-hashes the
outer envelope can no longer smuggle a stale or tampered embedded policy past
`validate_output` — every direct or downstream consumer (e.g. P9-03) inherits
this re-validation for free by calling `validate_output` on any packet it
receives.

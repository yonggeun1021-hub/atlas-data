# P2-04 Crypto Rotation contract

Status: BTC/ETH/ALT bucket capability; sector/chain rotation, state ledger,
briefing integration, and live operating evidence remain open.

The transform compares two hash-lineaged `crypto_leadership_contract/v2`
packets using one externally selected, fully observed `pilot_7d` or
`primary_30d` window. It preserves the existing current-candle exclusion,
as-captured manifest lineage, ratified universe and Leadership policies, and
the deterministic `BTC / ETH / every other eligible asset = ALT` mapping.

An external policy binds the exact universe, Leadership, and taxonomy policy
hashes, window, bucket set, TOP/BOTTOM counts, and maximum observation gap. It
must predate the prior packet and be effective across both observations.
Without that gate, raw bucket-relative-strength changes remain visible but no
rank, bucket, or transition is authorized.

Sector/chain groups remain `UNKNOWN`: their taxonomy/group coverage policy is
currently unratified. They are not converted to empty performance, are not
used for ranking, and cannot inherit BTC/ETH/ALT conclusions. Asset ranking,
P2-05 state vocabulary, Regime, Stage, Production, and trading remain false.
The CLI writes only to an explicit path outside the repository.

## Standalone output validation

`validate_packet()` treats a stored packet as untrusted. It validates the exact
identity, selected window and lookback, embedded external policy, BTC reference,
canonical relative-strength changes, ranking, TOP/MIDDLE/BOTTOM buckets,
transitions, summaries, lineage, authority, unresolved boundaries, and packet
digest. It also requires the sector/chain layer to remain exactly `UNKNOWN` and
unauthorized as a ranking input. Recomputing `payload_sha256` after changing a
rank, delta, or sector/chain authority cannot make the packet valid.

The v1 output contains the current `as_of_date`, but deliberately omits the
prior observation date and both upstream `available_at` timestamps. The
standalone validator therefore cannot independently re-prove maximum-gap or
ratified-before-prior availability. Those temporal checks remain enforced when
`build_packet()` validates both upstream packets. The output validator checks
every temporal claim that the retained fields support and does not invent the
omitted evidence.

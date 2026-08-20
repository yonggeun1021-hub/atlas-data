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

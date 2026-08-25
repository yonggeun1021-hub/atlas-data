# P2-04 Crypto Rotation contract

Status: BTC/ETH/ALT bucket capability; sector/chain rotation, state ledger,
briefing integration, and live operating evidence remain open.

## Scheduled source-pair population

`.github/scripts/crypto_rotation_source_pair_populate.py` reuses the existing
P1-CR-07 Leadership builder against the committed P1-CR-06 archive and prepares
two adjacent, fully observed `pilot_7d` packets as one exact
`crypto_rotation_input/1`.  The result is published content-addressed and
append-only below
`data/observations/crypto_rotation_source_pair/{as_of_date}/` by the existing
Crypto Breadth workflow; it adds no provider call or cron.

This population is intentionally one layer below the rotation transform.  The
repository default rotation policy remains `ABSENT`, the transform is not
invoked, and every ranking/state/action authority remains false.  Until two
adjacent seven-day Leadership windows exist, the step returns an expected
`blocked` telemetry result and writes no packet.  Once a pair exists it is
only a reproducible input for a separately supplied, externally ratified
policy -- never evidence that such a policy has been approved.

The integration also pins the shared persisted decimal boundary: P1-CR-07
stores cumulative return and relative strength at 12 decimal places, so
P2-04 re-derives their relationship at that same precision.  Exact unrounded
division of already-rounded fields is not a valid producer/consumer check;
changing one retained decimal unit still fails closed.

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

The output (schema `crypto_rotation_packet/2`) carries an `observation_pair`
with the prior and current observation dates, their calendar gap, and both
upstream `available_at` timestamps -- scalar facts only, not the two full
upstream Leadership packets. `validate_packet()` re-parses both timestamps,
requiring them present, ISO8601, and timezone-aware, and independently
re-derives prior-before-current order, the effective interval covering both
observations, the maximum-calendar-gap bound, and
ratified-before-prior-observation from those persisted values alone -- with
no live source pointer, current file, or monkeypatch -- so a revision's own
packet remains standalone-reprovable even after live source state moves on,
and a self-rehashed tamper of any of these facts (order, gap, ratification
timing) fails closed.

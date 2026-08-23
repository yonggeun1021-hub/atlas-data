# Identity Foundation stage — PR notes

Design source: "Canonical Security Identity / Market Scope Authority" v2
(Notion design packet, CIO-approved 2026-08-24 as this stage's
implementation baseline) and the paired "Dynamic Clock Candidate Validity
Window" v2 packet (combined CIO recommendation section).

## Scope of this PR

Builds the identity-resolution **mechanism** only:

- `identity/canonical_identity.py` — 4-layer (issuer / instrument /
  listing / source_asset_id) resolver, PIT anti-backdating gate
  (`real_usable_from = max(effective_from, ratified_at, first_seen_at)`),
  exact-content provenance verification (sha256 over each row's business
  payload, excluding the authority fields themselves), overlapping-interval
  and layer-confusion detection.
- `config/canonical_security_identity.json` — authority record schema.
  **Zero rows.** No real identity is asserted or ratified by this PR.
- `config/market_account_scope_map.json` — authority record schema.
  **Zero edges.** No real market↔account-scope edge is asserted or
  ratified by this PR.
- `test/test_identity_foundation.py` — the 18 required counter-examples
  plus structural-validation coverage and a direct test that the real
  shipped authority files (not synthetic fixtures) resolve every real
  query to `IDENTITY_NOT_COMPUTABLE_*`.

This PR does **not**:
- wire into the Shadow Matrix (`atlas-private-evidence`) in any way
- change any Dynamic Clock timestamp or `clock/dynamic_clock.py` behavior
- open P8-13 Entry Proposal
- contain any in-code mapping table or hardcoded per-ticker/market
  special-casing
- claim any row `RATIFIED`
- patch `portfolio_risk/portfolio_snapshot.py`'s raw-symbol double-count
  defect

## Dependent defect: `portfolio_risk/portfolio_snapshot.py` raw-symbol double-count

Found during Packet 1 v2 review (counter-example 11): `by_ticker` groups
exposure by `p["symbol"]` (the raw per-source symbol string), not by
`canonical_instrument_id`. Concretely: if the same real BTC position were
ever reported under two Kraken aliases (`BTC` and `XBT`, both real,
documented in `config/crypto_asset_identity_exceptions.json`) within one
snapshot, it would be double-counted as two separate positions.

**This is a dependent defect that cannot be safely resolved until
canonical-instrument adoption actually happens in that file** — fixing it
today with a quick ticker-normalization workaround would either (a)
hardcode exactly the kind of ad-hoc special-casing this stage forbids, or
(b) collide with the separate session already assigned to this file
(background task `task_8dcdbccb`). It is tracked, not fixed, here.
`identity/canonical_identity.py`'s `group_positions_by_instrument` exists
only to demonstrate, in `test_identity_foundation.py`, why
instrument-level grouping is the correct eventual fix — it is not called
from any real portfolio code path by this PR.

## Expected real-world outcome of this PR

Since no real row is `RATIFIED` in either shipped authority file, every
real resolution attempt against them correctly returns
`IDENTITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD` (see
`RealShippedAuthorityFilesAreEmptyTests` in the test file). **This is the
correct outcome for this stage, not a shortfall** — the mechanism is
proven end-to-end using synthetic fixtures in the other test classes,
which include a fully successful `RESOLVED` path once rows are (in
memory, for the test only) genuinely `RATIFIED` with valid provenance.

## Next stage

Only after this PR's independent CIO review completes does the next
stage (timestamp precision improvements) begin.

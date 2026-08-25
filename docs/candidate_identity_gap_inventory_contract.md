# Candidate Identity Authority Gap Inventory /1

## Purpose

This read-only packet makes the unresolved Identity Authority workload
reviewable.  It binds the exact validated Dynamic Clock candidate identity
observation to the existing ratified Crypto exclusion taxonomy and records
only mechanical, exact-symbol adjacency.

It does not turn taxonomy classification into issuer, instrument, listing,
investability, candidate validity, entry eligibility, sizing, or trading
authority.  It creates zero authority rows.

## Inputs

- `dynamic_clock_report.json`
- `candidate_identity_observation.json` (independently rebuilt and validated)
- `canonical_security_identity.json`
- `market_account_scope_map.json`
- `crypto_breadth_exclusion_taxonomy.json` v2, `RATIFIED`

For a diagnostic taxonomy match, the provider pair must be exactly
`kraken_spot_ohlc` + `<canonical_asset_id>/USD`, and the taxonomy record must
be effective on the candidate decision date.  Alias, quote-currency,
provider, rebrand, listing, or market inference is forbidden.

## Output semantics

- `MECHANICAL_TAXONOMY_SYMBOL_MATCH_DIAGNOSTIC`: an exact symbol exists in
  the effective taxonomy.  This is review evidence, not Identity Authority.
- `MECHANICAL_TAXONOMY_EXCLUDED_CATEGORY_DIAGNOSTIC`: an exact symbol exists
  but the taxonomy category is excluded.
- `TAXONOMY_RECORD_NOT_FOUND`: there is no effective exact-symbol record.
- `SOURCE_PAIR_NOT_MECHANICALLY_COMPARABLE`: source/provider/pair form is
  outside the deliberately narrow comparison contract.

Every row is `PROPOSED_UNRATIFIED_NOT_CREATED`; every authority flag remains
false.  The output is a rolling operational inventory.  Exact-run candidate
identity packets remain preserved by the separate content-addressed history
contract.

# Candidate Identity Authority Review Inventory

`candidate_identity_authority_review_inventory/1` is a read-only audit layer
over the existing non-authoritative identity proposal packet. It detects
contradictory issuer, instrument, listing, and provider-alias assignments
across the complete proposal population. It never selects a winning row and
never writes `config/canonical_security_identity.json` or
`config/market_account_scope_map.json`.

`MECHANICALLY_COHERENT_FOR_CIO_REVIEW` means only that no contradiction was
found within the currently retained proposal population. It is not an
identity approval, Candidate Validity result, P8-13 eligibility result, or
money action. A coherent proposal still requires an independently ratified
authority record before the canonical resolver can use it.

Shared issuers are permitted when the complete issuer payload is identical;
common shares, preferred shares, ADRs, and multiple listings can legitimately
share an issuer. A reused identifier with different determining payload, or a
provider alias assigned to different listings, is a conflict and all affected
candidates remain unresolved. The audit chooses no winner.

All authority fields remain false and `canonical_authority_rows_created` is
always zero.

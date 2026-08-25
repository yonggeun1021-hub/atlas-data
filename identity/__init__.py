"""Identity Foundation package.

Shared, dependency-light home for canonical security identity resolution,
importable by both `replay/` and `shadow_matrix/` (in the private evidence
repo) without pulling in either package's other machinery. See
`identity/canonical_identity.py` for the implementation and
`config/canonical_security_identity.json` / `config/market_account_scope_map.json`
for the authority records.

Design source: "Canonical Security Identity / Market Scope Authority" v2
(Notion design packet, CIO-approved 2026-08-24 as the implementation
baseline for this stage). The resolver remains a policy-neutral mechanism;
the separately reviewed authority records contain only the narrow mechanical
pilot documented in `docs/identity_authority_pilot.md`.
"""

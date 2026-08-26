# Candidate Identity Authority Proposal Contract

`candidate_identity_authority_proposal/1` is a non-authoritative CIO review
packet for the identity gaps observed by Dynamic Clock.

The builder independently rebuilds the committed gap inventory from the
Dynamic Clock report and identity observation, both canonical authority
documents, and the exact RATIFIED taxonomy bytes. It then verifies the latest
eligible Kraken asset-pair capture by manifest and decompressed-response hash.
A packet hash by itself is never treated as provenance.

A row is mechanically complete only when one exact provider pair, one active
RATIFIED taxonomy record, and one online Kraken USD pair agree on the relevant
fields. The generated issuer, instrument, listing, and source-alias identifiers
use a mechanical naming convention that is itself
`PROPOSED_UNRATIFIED`. Mechanical completeness means only that the row is ready
for CIO identity review.

The packet cannot:

- write or amend canonical authority configuration;
- label any proposed row RATIFIED or supply `ratified_at`;
- evaluate candidate validity, entry eligibility, sizing, or money action; or
- enable Stage, Buy, Action, Order, Production, or trading authority.

As of the 2026-08-26 evidence set, 56 of 58 gaps are mechanically complete for
review. DOGE/USD fails the exact Kraken identity-field comparison and Korea
subject `034020` has no applicable crypto taxonomy/provider evidence; both stay
`EVIDENCE_INCOMPLETE_NOT_PROPOSED`.

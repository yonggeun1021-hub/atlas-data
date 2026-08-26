# Candidate Identity Authority Proposal Contract

`candidate_identity_authority_proposal/2` is a non-authoritative CIO review
packet for the identity gaps observed by Dynamic Clock.

The builder independently rebuilds the committed gap inventory from the
Dynamic Clock report and identity observation, both canonical authority
documents, and the exact RATIFIED taxonomy bytes. It then verifies the latest
eligible Kraken asset-pair capture by manifest and decompressed-response hash.
A packet hash by itself is never treated as provenance.

For Korea candidates, the builder instead requires same-decision-date official
KRX and OpenDART collector records to agree on the exact six-digit symbol and
issuer name. OpenDART supplies the corp code and KRX supplies the listed symbol.
The retained collector files are bound by byte hash and must have been captured
no later than the decision date. Because those records do not prove share class,
the proposed instrument is explicitly `OTHER_UNCLASSIFIED`; the code never
invents `COMMON_STOCK`.

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

As of the 2026-08-26 evidence set, all 58 gaps are mechanically complete for
review: 57 crypto rows use exact taxonomy/provider evidence and Korea subject
`034020` uses matching official KRX/OpenDART records. This is evidence
completeness only. No proposal is authority and zero RATIFIED rows are created.

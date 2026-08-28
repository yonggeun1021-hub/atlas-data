# KIS 071050 proposal-only identity and alias review

This change prepares two independent CIO review inputs. It grants no authority.

1. `ISSUER_INSTRUMENT_LISTING_IDENTITY` binds the exact 071050 public-master
   observation to `DART:00432102`, `XKRX:071050`,
   `KRX:071050:COMMON`, ISIN `KR7071050009`, the Korean name, and the observed
   common-share code. The master archive, extracted master, row, parser, header,
   and Atlas DART map are content/commit pinned. The proposal carries the exact
   288-byte row as base64, its SHA-256, and its line number. The reviewer
   decodes and parses those bytes independently before accepting the listing,
   ISIN, name, security group, or share class; serialized observation fields
   and a rehashed proposal cannot prove themselves. No derivative Atlas
   universe packet or generic six-digit symbol rule is used.
   `REVIEW_READY_FOR_CIO` additionally requires operator-supplied independent
   archive bytes (or an absolute archive path). The reviewer enforces the size
   cap, archive hash, the single exact `kospi_code.mst` member, master hash,
   line 1035, unique 071050 row membership, embedded-row equality, and parsed
   claim equality. Missing archive reproduction remains review-incomplete.
2. `EXACT_SOURCE_ALIAS` binds only the exact source pair
   `kis_paper_domestic_balance / 071050` to the proposed listing/instrument.
   It cites instrument-specific official KIS static examples at one immutable
   commit. It does not express or imply a generic six-digit PDNO rule.

The attempted live `search-stock-info` read failed once and is recorded only as
`NOT_OBTAINED_FAIL_CLOSED`. It contributes no positive evidence and must not be
retried for this review. `orderSubmissionAttempted` remains false.

Both packets remain `PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY`, carry an all-false
authority object, and set `canonicalAuthorityConfigMutated=false`. Review-ready
means only that the exact cited bytes and semantics were mechanically
reproduced. It is not ratification, broker verification, position valuation,
investment eligibility, or order permission.

## Explicitly outside scope

- no edit to `config/canonical_security_identity.json`
- no edit to `config/data_provider_authority.json` or another authority registry
- no valuation-semantic mapping
- no WBS or Notion update
- no 005930/000660 proposal
- no Stage, Buy, Action, Order, Production, Trading, or REAL authority

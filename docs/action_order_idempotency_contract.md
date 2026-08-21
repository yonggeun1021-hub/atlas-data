# P9-04 duplicate Action / Order guard

This capability validates caller-supplied event, action, order, and idempotency
IDs against an append-only history. It does not generate those IDs, create an
order, submit to a broker, or authorize execution.

The intent is opaque and bound only by SHA-256. The guard applies these rules:

- same idempotency key plus the same event/action/order/market/intent identity:
  `DUPLICATE_RETRY_BLOCKED`;
- same idempotency key with a different identity or intent: hard collision;
- same action or order ID under a different idempotency key: hard collision;
- a novel key with novel action and order IDs:
  `NOVEL_RECORDED_EXECUTION_NOT_AUTHORIZED`.

A single event may legitimately have multiple distinct explicit orders, so
`event_id` alone is not treated as a uniqueness key. This avoids collapsing
multi-leg or separately approved actions while still blocking retries.

The output contains audit decisions and an updated ledger candidate. For every
decision `execution_authorized=false` and `broker_submission=null`; summary
counts for orders created/submitted are zero. The candidate is not written to a
tracked or Production ledger. The CLI is offline and writes only outside the
repository.

## Persisted result validation

Result schema v2 embeds the exact normalized prior ledger and attempt batch under
`source_packets`. `validate_result()` re-runs both production input validators and
rebuilds every decision, duplicate/novel count, matched-record hash, updated ledger
candidate, lineage, authority, unresolved boundary, and packet digest. Recomputing the
outer hash cannot turn a changed duplicate/novel classification or ledger candidate
into a valid result.

The embedded source packets prove internal derivation and preserve their own hashes;
they do not independently prove who externally authorized or supplied a particular
ledger or attempt batch. Production authority and broker submission remain false.

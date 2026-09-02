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

## Evidence-only restart recovery journal

`decision/action_order_recovery_journal.py` is the bounded persistence consumer for
this guard. It may write only to an external root and keeps the guard's candidate
ledger in a simulation/shadow/evidence journal. It does not turn that candidate into
a Production ledger or add Action, Order, cancel, broker, withdrawal, REAL, live,
Production, or Trading authority.

The journal writes content-addressed result and ledger blobs durably before replacing
one hash-bound `head.json`. A crash before the head replacement leaves unreachable
blobs and recovery resumes from the last complete head. Recovery revalidates the
contract, exact JSON field sets and scalar types, head and commit digests, commit
chain, each embedded P9-04 source packet, every guard-result derivation, and the
selected ledger blob. A self-rehashed semantic rewrite therefore remains invalid.

An attempt-batch digest can occur in the commit chain only once. Reapplying that exact
batch returns its existing receipt and performs no JSON write. A different batch is
evaluated against the recovered current ledger, so later retries of an already-seen
intent remain `DUPLICATE_RETRY_BLOCKED`. The writer uses an exclusive process lock,
immutable blob publication, file `fsync`, atomic head replacement, and directory
`fsync`; no repository path, network module, subprocess, credential, or broker path
is permitted.

All journal tests are synthetic mechanism evidence only. They are not a natural
idempotency event, an operational Exit Gate sample, or evidence of an order.

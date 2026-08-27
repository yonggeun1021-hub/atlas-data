# Atlas Briefing SSOT projection contract

`Atlas Briefing SSOT` is the read-only Portal projection of the exact machine
state that Atlas carries forward. It is not a trading or order interface.

## Identity and content

- Data source: `7207d3d2-8806-4aa9-bfe2-cfddc3ea6869`
- Database container: `2ede08c3-1288-47fc-a8e4-0fb63713fa83`
- One current Notion row per `briefing_id` (`YYYY-MM-DD-am|pm`). Duplicate
  rows fail closed.
- Initial finalization is idempotent by `briefing_id` plus its canonical content
  hash. Post-delivery correction is idempotent by
  `briefing_id/post_delivery_change_key`.
- `Canonical JSON` is UTF-8 JSON with sorted keys and compact separators.
  `Content SHA256` is the SHA-256 of those exact bytes.
- The normal projection contains the exact sealed delivery markdown and its
  immutable source identity. A correction contains exactly
  `briefing_finalization.expected_projection_content`, so the rev18 receipt
  verifier derives the same hash independently.
- The one pre-finalization legacy round (`2026-08-28-am`) is sourced from the
  existing Market Dashboard row in
  `data/briefing/portal_bootstrap/2026-08-28-am.json`.  It is explicitly
  `capital_impact=UNKNOWN`, carries no delivery authority, and is
  `redelivery=FORBIDDEN`; a drain-mode canary can project it without creating
  or delivering a second briefing.

## Write and receipt order

1. Validate briefing/date/slot identity and reject any stage, buy, action,
   order, production, trading, or broker-credential authority escalation.
2. Query by `Briefing ID`; more than one result is an incident.
3. Create or update the row only when canonical content differs.
4. Query again to detect a concurrent duplicate-create race.
5. Retrieve and compare every reviewed property, including the full canonical
   JSON, dates, selects, contract version, purpose, and hash.
6. Only after exact readback, atomically publish an append-only local receipt.
   A retry reuses a valid receipt. A bad latest receipt is recovered only by a
   newer good revision; a newer bad revision fails closed.

The workflow runs projection before `Ingest verdicts and deliver`. Once the
adapter is active, Notion failure prevents the one user delivery. The adapter
never sends a briefing itself, and a post-delivery correction never causes
redelivery.

## Activation and live canary

`config/atlas_projection.json` has two independent flags:

- `implemented`: Finalization may recognize this adapter's receipts.
- `verified_against_live_api`: the GitHub Actions identity has completed a real
  write and exact read-after-write against the reviewed data source.

Both remain `false` until the canary succeeds. The manual-only
`portal_canary` workflow input allows that one test without prematurely
activating receipt authority. The canary must demonstrate:

- the Actions `NOTION_TOKEN` can retrieve the exact data-source schema;
- create/update and exact readback succeed;
- a second run is `NO_CHANGE` and reuses the same receipt;
- the Notion row has the expected briefing id and canonical hash;
- no duplicate row exists.

The adapter performs that second run inside the same canary invocation and
fails unless every candidate is read back again without a write and reuses its
append-only receipt.

After the evidence is reviewed, set both flags to `true` in one reviewed
commit. `ATLAS_APPROVAL_PUBKEY_FINGERPRINT` remains required for signed
post-delivery rulings; it is unrelated to ordinary no-authority projections.

## Current boundary

Until both flags are true, the workflow step exits as
`SKIPPED_NOT_LIVE_VERIFIED`, and Finalization continues to report
`PORTAL_ADAPTER_NOT_IMPLEMENTED`. This is a deliberate activation lock, not a
successful projection. The user-reaching FULL delivery/capital-alert adapter
is a later boundary and remains unimplemented.

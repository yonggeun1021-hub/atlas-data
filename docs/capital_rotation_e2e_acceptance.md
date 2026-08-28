# P8-15 Capital Rotation E2E Natural-Chain Acceptance

P8-15 proves an operational chain; it does not classify a market or authorize
capital.  The committed P0-06 retrieval envelope proves immutable briefing
bytes but cannot, by itself, prove whether a workflow was a genuine schedule
or a manual recovery.  Daily Briefing therefore commits a separate append-only
run receipt whose event provenance comes directly from GitHub Actions context.

The evaluator counts a date only when both morning and evening receipts are
`NATURAL_SCHEDULED_RUN`.  `workflow_dispatch`, replay, non-schedule events,
unknown cron expressions, duplicate slots, missing or tampered envelopes, and
mixed source/generation lineage fail closed or remain excluded.

The canonical Exit Gate remains:

1. three distinct KST dates with natural morning and evening receipts;
2. viewer-visible Portal receipts for both slots on all three dates; and
3. one separately attested genuine scheduled fail-closed run.

A successful packet never manufactures either downstream condition.  The
Portal-viewer producer now signs each exact receipt byte sequence with GitHub
artifact attestation.  Atlas-data imports only natural scheduled observations,
records the Portal discovery commit explicitly as discovery-only (not as the
attestation identity), stores the attestation bundle and trusted root, and
replays verification offline with the exact signer workflow, source digest,
source ref, hosted-runner restriction, and a contract-pinned GitHub trusted-root
snapshot.  Import also performs online verification before downloading the
offline bundle.  A GitHub trusted-root rotation therefore fails closed until
the contract pin is independently reviewed and updated; it is never accepted
implicitly.  A self-authored or merely self-hashed JSON receipt is still
rejected.  No natural Portal receipt exists
yet, so the projected-pair count remains zero.  The separate genuine
fail-closed receipt producer is still not implemented.

All Regime, strategy, Stage, Buy, Action, Order, Production, and trading
authority remains false.

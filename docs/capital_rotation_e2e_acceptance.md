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

A successful packet never manufactures either downstream condition.  Trusted
Portal-viewer and genuine fail-closed receipt producers are not implemented in
this slice.  Until later contracts establish their provenance, a self-authored
or merely self-hashed JSON receipt is rejected and both counts remain zero.
The inventory therefore remains `NOT_READY` even as natural AM/PM receipts
begin accumulating.

All Regime, strategy, Stage, Buy, Action, Order, Production, and trading
authority remains false.

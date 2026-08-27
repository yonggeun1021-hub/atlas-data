# P3-11 Wildcard Discovery path contract

Status: offline case-recording capability; live intake and publication are not implemented.

## Purpose and boundary

The wildcard path prevents the current Theme taxonomy from being the only way
an observation can be recorded. It accepts explicit nominations whose Theme
membership is `OUTSIDE_CURRENT_TAXONOMY` or `UNRESOLVED` and whose `theme_ids`
is empty. A submission that already carries a Theme ID must use a normal Theme
or radar path instead.

Nomination reason and hypothesis are always
`UNCONFIRMED_NOMINATION_TEXT`. They are never promoted to confirmed facts. The
path does not decide whether an event is strong or important; both policies are
unratified.

## Evidence gate

At least one market-registered, source-linked evidence item is required to
record a `wildcard_discovery_case/1`. Source availability and retrieval must
precede the nomination and decision timestamp. Blocked or unresolved evidence
has no hidden claim or source payload and remains explicit.

A linked claim is labeled `SOURCE_LINKED_OBSERVATION_NOT_INTERPRETED`; source
linkage does not make the nomination hypothesis or an investment conclusion a
confirmed fact.

If no evidence is linked, the submission is retained as pending with
`NO_SOURCE_LINKED_EVIDENCE`; it is not a case. Mixed linked and unresolved
evidence creates `EVIDENCE_PARTIAL`, preserving both sides without fallback or
zero-fill. A recorded case explains only that it came from an explicit wildcard
nomination with linked evidence. It does not claim algorithmic discovery or
strength.

## Persisted packet validation

`validate_packet()`은 저장된 submission의 identity, nomination authority,
evidence status와 source lineage, linked/unlinked 수, pending/case 분기를 다시
검증한다. 이어서 retained submission만으로 deterministic case ID, partial
evidence projection, unconfirmed-text marker와 모든 authority lock을 재생성해
저장된 case와 exact 비교한다. 따라서 payload hash만 다시 계산한 case ID,
source, count, evidence projection 또는 action 변조도 실패한다.

Source body 자체는 packet에 포함되지 않고 SHA-256 identity만 남는다.
standalone validator는 그 외부 원문의 진위를 인증하지 않으며, 수집·원문 검증은
별도 source acquisition 경계다.

## Authority and operation

Every case keeps strength and importance `UNRATIFIED`, candidate eligibility
false, and rank, Stage transition, Rule evaluation, and action null. Production
and trading authorities are false.

The helper is offline, has no workflow, and writes only to an explicit path
outside the repository. Live nomination, tracked case publication, Theme
taxonomy completion, source hierarchy, ranking, and briefing integration remain
separate WBS gates.

## Operational intake and append-only publication

`wildcard_operational_intake/v1` adds a manual, provider-free operating path.
It accepts only a submission JSON already reviewed and committed under
`data/intake/wildcard/` at one exact full commit SHA. Every linked evidence item
must point to a primary-source body committed in that same immutable revision;
the publisher verifies the real bytes, SHA-256, and exact-content git first-seen
time before rebuilding the existing wildcard packet. Operational availability is
`max(retrieved_at_utc, exact_content_first_seen_at)` and must not exceed the
decision time; a source may be officially observed before its immutable git
capture without being falsely rejected or backdated.

The resulting envelope is content-addressed and append-only under
`evidence/operational/wildcard_discovery/`. Re-dispatching identical input is a
byte-identical no-op; a concurrent main advance fails closed without rebase or
force push. GitHub inputs cannot contain free-form nominations or source claims,
and the workflow makes no external provider call.

This publication authority records cases and pending submissions only. Strength,
importance, ranking, candidate eligibility, Stage, Rule, Action, Proposal, Order,
Production, and trading remain false or null. Until a genuine committed
submission is processed on main, this is an operational capability rather than
an observed live sample.

`rotation_discovery_briefing/3` closes the read-model wiring gate. It validates
eligible envelopes again, selects the latest immutable revision for each
submission path, and exposes evidence-linked cases and pending submissions as
non-promotable observations in the Daily Orchestrator. The read model never
converts them into ranked candidates, READY rows, entry triggers, or actions.
The remaining operational gate is a genuine reviewed main submission followed
by a briefing generation whose packet lineage contains that exact envelope.

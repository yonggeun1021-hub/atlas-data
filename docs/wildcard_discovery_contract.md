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

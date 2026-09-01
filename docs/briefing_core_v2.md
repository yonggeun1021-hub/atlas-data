# Atlas Briefing Core v2

`briefing_core/2` freezes one exact `source_commit` and `generation_id` before
drafting or validation. Later commits may add unrelated PAPER observations,
but they cannot enter the frozen briefing identity.

```text
briefing_input_envelope/2
  -> briefing_handoff/2 + claude_briefing_handoff/1 compatibility
  -> claim_ledger/1
  -> briefing_validation_report/1
  -> portal_projection/2
  -> notion_briefing_receipt/2
```

The legacy finalization and Portal contracts remain valid. The new core emits
their existing `claim_ledger/1` and `portal_projection/2` inputs, so rollout can
remain shadow-first and existing consumers do not need a flag-day migration.

## Failure boundary

Date, account/private-data, order/execution authority, lineage and duplicate-ID
conflicts fail closed. Market, news, Korea, US, Crypto, rotation, future
funding/OI and PAPER-rotation adapters are versioned optional modules. Their
failure becomes `UNKNOWN` / `확인 불가` for that module while other sections
continue.

The PAPER runtime publishes only `atlas_paper_signal/1` or
`atlas_paper_result/1` under `runtime/paper/{signals,results}/v1/`. The publisher
rejects Briefing, Portal, Notion and account paths, refuses authority other than
`PAPER`, and makes identical replay `NO_CHANGE` while rejecting different bytes
at the same path.

## Major-event coverage

Every handoff has a top-level `오늘의 핵심 사건` section. A detected HIGH or
CRITICAL event needs at least one official primary source and one independent
major-media cross-check. Claims remain split into `FACT`, `INFERENCE`, and
`UNKNOWN`; transmission through oil/shipping, Hormuz, dollar/rates, equity risk
appetite and defense is never represented as confirmed price causality without
separate market evidence.

If a detected event is omitted, validation returns
`MAJOR_EVENT_COVERAGE_MISSING`, `portal_allowed=false`, and a source-bound Codex
correction creates a new correction history entry without overwriting the
draft. The chain also renders a new `corrected-briefing.md` plus a hash-bound
`briefing_correction_manifest/1`; the sealed source briefing remains immutable.
Portal validation consumes the corrected revision, not the omitted source. If
news verification itself is unavailable, the briefing continues with
`주요 뉴스 검증 불가`, but complete market, Risk On/Off and capital-allocation
conclusions remain disabled.

The 2026-09-02 AM regression uses the U.S. Central Command announcement that it
completed strikes on IRGC air-defense, radar, maritime, mine-laying and
communications targets, cross-checked against AP reporting of Iranian missile
and drone retaliation. The fact of strikes and retaliation is verified; a
complete restart of full-scale war remains unconfirmed.

- Official: https://www.centcom.mil/MEDIA/PUBLIC-RELEASES/Article/4588389/centcom-completes-strikes-on-irgc-targets-in-iran/
- Independent cross-check: https://apnews.com/article/ad84a64884aedbbd1e5914182bb3cd8d

## Pre-merge acceptance

`validation/tests/test_briefing_core_v2.py` runs a natural-schedule-equivalent
chain in a disposable Git repository. It proves exact commit/generation
freezing, optional-module isolation, event-omission correction, legacy Portal
projection, Notion receipt readback, append-only publication, exact replay
`NO_CHANGE`, and duplicate count zero. This is synthetic acceptance, not a
natural AM/PM observation and never increments P8-15 natural counts.

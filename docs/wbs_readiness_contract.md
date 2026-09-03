# Canonical WBS readiness metrics

## Purpose

`governance/wbs_readiness.py` turns a fresh, read-only export of the live Notion
Master WBS and a separately audited PAPER-gate evidence manifest into one
deterministic report. It does not call Notion, create evidence, change a WBS row,
run a strategy, write an order, or grant any authority.

The contract intentionally keeps five different questions separate:

1. formal WBS completion;
2. weighted WBS stage progress;
3. fixed eight-gate PAPER rotation readiness;
4. natural three-market PAPER lifecycle completion;
5. explicitly scoped small-PAPER and small-live formal readiness.

They have different numerators and denominators and must never be averaged or
shown under a shared “completion” label.

## Canonical sources

- Master WBS database:
  `https://app.notion.com/p/1dc7a235b4284d15b573d2dbc8e7608d`
- Master WBS data source:
  `collection://3b679327-02f3-4d75-8bf1-465eef3a8007`
- CIO Doctrine:
  `https://app.notion.com/p/3c49f2d73c8481649f6de3662e222c3e`
- Cockpit:
  `https://app.notion.com/p/3ba9f2d73c8481d6953ec414dafdbff4`
- Metric and scope contract: `config/wbs_readiness_contract.json`

The checked-in snapshot and PAPER manifest under
`test/fixtures/wbs_readiness/` are dated regression fixtures, not a current
dashboard source. A runtime report must use newly exported pages and a newly
audited gate manifest. Both inputs fail closed when older than the contract’s
36-hour bound relative to the explicit evaluation time.

## Exact formulas

All calculations use integer units. Percentages are decimal strings rounded
half-up to one decimal place. No binary floating-point values enter the report.

| Metric | Formula |
| --- | --- |
| Formal completion | count(`✅ 완료`) / all WBS rows, including `⛔ 금지` |
| Weighted progress | `완료×100 + 관측×80 + 검증×60 + 승인×40 + 개발×20`; all other statuses ×0; divide by `all rows ×100` |
| Forbidden-excluded weighted | same earned units / `(all rows − forbidden) ×100` |
| Non-forbidden rows | all rows − `⛔ 금지` |
| Actionable rows | statuses that are neither `✅ 완료` nor `⛔ 금지` |
| Late-stage entry | count(`완료`, `관측중`, `검증대기`, `승인대기`) / all rows |
| PAPER rotation readiness | eight fixed gates; COMPLETE=2 units, PARTIAL=1, MISSING/BLOCKED=0; divide by 16 units |
| Natural E2E | markets with a reconciled natural BUY→HOLD→SELL chain / fixed markets KRX, US, CRYPTO |
| Small-PAPER formal readiness | completed rows / the 92 explicit `smallPaper` row IDs |
| Small-live formal readiness | completed rows / the 92 small-PAPER IDs plus 15 explicit live-additional IDs |

The scope IDs are data, not title heuristics. Any missing ID, duplicate scope ID,
duplicate Notion URL, duplicate Work Item title, duplicate canonical WBS ID,
unknown/null status, future source, or stale source rejects the report.

## Collection and report flow

The Notion SQL tool returns at most 100 rows per response, so a 148-row export
must be saved as at least two query pages (for example `LIMIT 100 OFFSET 0` and
`LIMIT 100 OFFSET 100`). Normalize them with:

```bash
python3 governance/wbs_readiness.py collect \
  --query-page /tmp/wbs-page-1.json \
  --query-page /tmp/wbs-page-2.json \
  --retrieved-at 2026-09-02T22:07:40Z \
  --out /tmp/wbs-snapshot.json
```

Then compute the report from that fresh snapshot and a fresh, evidence-cited
eight-gate manifest:

```bash
python3 governance/wbs_readiness.py report \
  --snapshot /tmp/wbs-snapshot.json \
  --paper-evidence /tmp/paper-evidence.json \
  --evaluated-at 2026-09-02T22:08:00Z \
  --out /tmp/wbs-readiness-report.json
```

Each report carries SHA-256 hashes for the contract, WBS snapshot, PAPER
evidence, and the report itself. Recomputing from identical inputs produces
identical bytes and hashes.

## 2026-09-03 regression checkpoint

The fixture captures the live 148-row readback at `2026-09-02T22:07:40Z`, after
the concurrent `P10-04`, `P3-12`, `P4-02`, and `P4-04` transitions and the
`P1-KR-03` external-dependency correction:

- formal completion: `36/148 = 24.3%`;
- weighted progress: `7380/14800 = 49.9%`;
- forbidden: `18`; non-forbidden: `130`; actionable: `94`;
- forbidden-excluded weighted progress: `7380/13000 = 56.8%`;
- late-stage entry: `96/148 = 64.9%`;
- PAPER rotation readiness: `7/16 = 43.8%` (gates 1–7 PARTIAL, gate 8 MISSING);
- natural E2E: `0/3`;
- small-PAPER: `29/92 = 31.5%`;
- small-live: `31/107 = 29.0%`.

The original small-live figure `29/107 = 27.1%` first became stale when
`P10-13` completed, then `30/107 = 28.0%` became stale when `P10-04`, also in
the live-additional scope, completed. Small-PAPER remains `29/92` because
neither row is in that scope. Its weighted score did move from `6060/9200` to
`6020/9200` as `P1-KR-03` moved to external wait and `P3-12` moved to validation.

## Authority boundary

Metric computation is evidence-only. Strategy, Stage, Buy, Action, Order,
broker, exchange, REAL/live capital, Production, and Trading authority remain
false. Fixture, replay, manual recovery, canary, NOOP, WAIT, and synthetic
lifecycle evidence never count as a natural E2E completion.

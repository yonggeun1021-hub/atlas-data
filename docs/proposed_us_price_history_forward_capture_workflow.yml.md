# PROPOSAL — US Price-History Forward Capture Schedule (NOT REGISTERED)

**Status: DRAFT / PROPOSAL FOR CIO REVIEW. This is not a live schedule.**
No workflow YAML under this name (or with this content) is registered in
`.github/workflows/`. The draft embedded below is for review only; it does
not run anywhere, is not wired to any trigger, and this PR does not add it
to `.github/workflows/`.

CIO US-DATA-1 item 2 (`CLAUDE_CIO_CANDIDATE_PIPELINE_REBUILD_PLAN_20260913.md`
§2 W1, US subsection). Companion to `collectors/us_price_history_backfill.py`
(the backfill tool, also in this PR) — this document only proposes the
**forward** (daily, going-forward) side of the same `price_history_session/1`
contract for the 22 Alpaca-authorized symbols in
`config/free_market_data_contract.json`'s `alpaca.symbols`.

## Why a proposal, not a workflow

Per the CIO plan's own architecture rule (§1): "KRX 파생 종목별 가격·측정치는
private에 둔다. public에는 계약·코드·해시·집계·허용 필드만 둔다." The same
split applies to the US side via Alpaca — per-symbol daily bars are
private-repo territory (`price_history/US/{YYYY}/{YYYYMMDD}/`), so the actual
forward-capture workflow, if approved, should live in `atlas-private-evidence`
(pinning a `public_code_commit` from this public repo), the same pattern the
plan documents for `private:.github/workflows/price-history-capture.yml`.
Nothing in `atlas-data` (this public repo) should schedule a job that writes
per-symbol US price rows here. This doc exists so the CIO can review and pick
a cron time and pacing before that private workflow is written.

## Existing house convention for US-anchored schedules

Two already-registered `atlas-data` workflows anchor to the US market close
and use the same reasoning this proposal borrows:

- `.github/workflows/free-market-data.yml` — `cron: '35 21 * * 0-5'`
  (06:35 KST Mon–Sat), captures the *current* Alpaca IEX snapshot (not a
  price-history backfill/forward record) shortly after the US close, timed
  so Friday's close is captured ahead of the weekend briefing rather than
  waiting for Monday.
- `.github/workflows/p1-us04-forward-breadth.yml` — `cron: "20 1 * * 2-6"`,
  with the comment *"01:20 UTC Tue-Sat: regular US session date after close
  in both EDT/EST"*. Both existing schedules run after the raw 16:00 ET
  close (20:00 UTC in EDT / 21:00 UTC in EST), but `p1-us04-forward-breadth`
  is the closer analog for this proposal: it produces a **dated, PIT-anchored
  session record** (like `price_history_session/1` would), not a rolling
  current snapshot like `free-market-data.yml`. Its wider buffer past the
  raw close (an extra several hours beyond either DST close time, versus
  `free-market-data.yml`'s much tighter same-day margin) is the more
  conservative choice for a record whose PIT correctness this repo actually
  depends on later, so this proposal follows that slot's pattern rather than
  the tighter current-snapshot one.

## Proposed schedule

Adopt the `p1-us04-forward-breadth.yml` pattern directly, since
`price_history_session/1` forward capture has the exact same PIT
requirement (never observe a bar for a session that has not fully closed
yet, in either DST regime):

```
- cron: "35 1 * * 2-6"   # 01:35 UTC Tue-Sat: US session date after close in
                          # both EDT/EST, 15-minute safety buffer past the
                          # already-proven 01:20 UTC P1-US-04 slot so this
                          # capture never races it for the same Alpaca
                          # credential/rate budget.
```

- `Tue-Sat` (not `Mon-Sat`): the run that lands Tuesday 01:35 UTC captures
  Monday's (US) session date, the first date with a full session after the
  weekend. A Monday 01:35 UTC run would only be capturing the *prior*
  Friday, which `free-market-data.yml`'s existing Saturday-capture-of-Friday
  reasoning already covers via a different mechanism (current snapshot, not
  a dated history row) — kept separate rather than duplicated here.
- `pit_class: FORWARD_CAPTURE` on every row this schedule would produce
  (never `HISTORICAL_BACKFILL` — that is exclusively the backfill script's
  output; see `collectors/us_price_history_backfill.py`).
- A retry slot (second cron entry) mirroring `krx-post-close.yml`'s
  multi-slot pattern is left to the CIO's decision — not proposed here since
  Alpaca's actual availability lag for a freshly closed IEX daily bar is
  UNVERIFIED (same caveat the CIO plan already carries for KRX OpenAPI
  limits).

## Open questions for CIO decision (not decided by this PR)

1. Exact cron minute/hour (`01:35 UTC` above is a proposal, not final).
2. Whether a second (retry) slot is warranted, and its offset.
3. Whether this forward capture should be a new private workflow, or folded
   into the existing `free-market-data.yml`/private equivalent as an
   additional step (this doc assumes a new, separate private workflow to
   avoid entangling the unrelated current-snapshot contract).
4. `us_backfill_sessions` / retention window policy (plan §6 "정책 필드"
   table lists `us_backfill_sessions` as CIO-decided; this doc's own backfill
   script defaults to a trailing 364-day / 251-session range, see PR
   description, but that default is not a ratified policy value).

## Draft workflow (illustrative only — NOT registered, NOT runnable as-is)

The block below is a draft sketch matching this repo's existing workflow
house style (see `.github/workflows/p1-us04-forward-breadth.yml`,
`.github/workflows/free-market-data.yml`). It intentionally omits an actual
job body — the real forward-capture logic and its private-repo commit step
belong in `atlas-private-evidence`, not here, per the storage-boundary
section above. Do not copy this into `.github/workflows/` without CIO
approval and without first writing the private-repo capture script it would
call.

```yaml
# DRAFT — NOT REGISTERED. See docs/proposed_us_price_history_forward_capture_workflow.yml.md
# for the full rationale. This file does not exist under .github/workflows/.
name: "[DRAFT] US Price History Forward Capture"

on:
  schedule:
    # 01:35 UTC Tue-Sat: US session date after close in both EDT/EST.
    # PROPOSAL ONLY -- see doc for reasoning and open questions.
    - cron: "35 1 * * 2-6"
  workflow_dispatch:

permissions:
  contents: read  # a real forward-capture workflow for PRIVATE per-symbol
                   # data belongs in atlas-private-evidence, not here; this
                   # public repo would at most supply pinned code + contract.

concurrency:
  group: atlas-us-price-history-forward-capture
  cancel-in-progress: false

jobs:
  capture:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - name: "NOT IMPLEMENTED -- draft placeholder"
        run: |
          echo "This workflow is a DRAFT proposal, not registered, and not runnable."
          exit 1
```

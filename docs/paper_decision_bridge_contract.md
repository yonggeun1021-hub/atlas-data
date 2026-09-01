# Three-market PAPER decision bridge

This runtime contract connects the canonical Flow-First read model to the KRX,
US, and Crypto PAPER lifecycles without inventing a strategy, score, trade plan,
or order.  It is based on public commit
`7e6021fcb866027b3b6caa28405dd0d9b3e90875`.

Each market is evaluated independently.  There is no cross-market candidate
ranking or shared completion gate.  `Top3` means at most three instruments
inside one market; it never means the three markets.  Existing upstream
Universe/Top10/Top3 observations are shown only when supplied with their exact
source hash.  Scored candidates are passed to the common funnel one market at
a time:

`Universe → Top10 → Top3 → Candidate(≥60) → Ready(≥70) → PAPER_BUY_ELIGIBLE(≥75 + every common Hard Gate literal PASS)`

The full transition is stricter than the common funnel.  Market judgement,
market approval, leadership approval and coverage, completed bar, freshness,
entry eligibility, exit eligibility, risk packet, ledger integrity, every
Flow-First trace edge, natural evidence classification, and exact source hashes
must also be literal PASS.  A fixture is never promotable.  Any missing, null,
stale, TTL-expired, or hash-mismatched evidence yields `action=null`.  With no
upstream decision, or with a blocked `BUY`/`SELL`, the recommendation is
`WAIT`; an explicit upstream non-executing `HOLD` remains `HOLD` and never
increments the PAPER transition count.

Leadership is an observation feeding market judgement, never direct PAPER
authority.  The pinned policies are preserved exactly:

- KRX `korea_leadership/v1`: `RATIFIED`; group coverage not separately applicable.
- US `us_leadership/v1`: `UNRATIFIED` (`us_leadership/draft-v1`).
- Crypto `crypto_leadership/v1`: `RATIFIED`, but group coverage `UNRATIFIED`.

The trace order is Flow-First briefing → three-market Regime header →
leadership → capital rotation → cash/exposure → defensive action → hedge
instrument eligibility → bear hedge budget → strategic capital posture →
common candidate funnel → entry/exit eligibility → ledger.  Every trace row
contains input connectivity, output status, exact sources, component authority,
PAPER transition state, and blocking reasons.

At the pinned commit, only the common candidate funnel owns internal virtual
PAPER eligibility.  All surrounding Flow-First/action/entry-exit contracts
explicitly deny PAPER/action authority.  The bridge therefore exposes connected
observations and broken edges but cannot truthfully produce a PAPER action until
those upstream contracts are ratified and revised.

Candidate output retains display name, ticker/code, market, score and exact
components, every hard gate and missing reason, source timestamp, TTL and
expiry, source refs/hashes, and optional entry/stop/take-profit/quantity/expiry.
Trade-plan values are retained only when their validation gate and exact sources
PASS; otherwise they are replaced by nulls.

Receipts are deterministic and content-addressed by decision identity.  The
first persistence creates a read-only receipt; an identical rerun returns
`NO_CHANGE`; any conflicting bytes at the same identity fail closed.  The CLI
does not call a provider, broker, credential store, OAuth endpoint, network, or
order API.  REAL/live/real-capital/Production/Trading remain false.

Example:

```bash
python3 decision/paper_decision_bridge.py \
  --input /path/to/normalized-input.json \
  --out /outside/repository/run.json \
  --receipt-dir /outside/repository/receipts
```

The read-only Wave 10 adapter consumes the exact KRX/US/Crypto reports and
preserves every absent TTL, Regime header, score, risk packet, and entry/exit
gate as null/WAIT:

```bash
python3 decision/paper_decision_bridge.py \
  --krx-wave10-report /path/to/krx-report.json \
  --us-wave10-report /path/to/us-report.json \
  --crypto-wave10-report /path/to/crypto-report.json \
  --evaluation-at 2026-08-31T09:00:00Z \
  --out /outside/repository/run.json \
  --receipt-dir /outside/repository/receipts
```

Tracked fixtures are synthetic contract tests only and cannot become natural
or performance evidence.

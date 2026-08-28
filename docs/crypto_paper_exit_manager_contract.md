# P7-13 Crypto PAPER exit and position-management mechanism

Status: `PAPER_LAB_ONLY` mechanism. It creates a review packet, not a live
exit, quantity authorization, exchange order, or market judgment.

## Design packet

### Purpose

Fix the exit plan at entry time and evaluate it later in a deterministic
priority order. The mechanism lets the PAPER lab rehearse stop, risk reduction,
partial harvest, trailing, and time-review branches without choosing thresholds
after seeing MFE/MAE and without sending an Upbit order.

### Input SSOT

- `crypto_paper_exit_plan/1` embeds and revalidates the exact P10-11 entry-time
  account packet. Every trigger, threshold, action, fraction, and deterministic
  PAPER order identity is caller supplied; there are no defaults.
- `crypto_paper_exit_observation/1` carries a current price, the prior (not
  hindsight-updated) high watermark, freshness, and explicit security,
  liquidity, risk-budget, Regime, trend, and kill-switch statuses.
- The current `crypto_paper_account_state/1` embeds and revalidates the exact
  current simulator ledger. This is the only position/order state consumed.

### Semantic contract

Planned triggers are sorted by the fixed WBS category order: hard exit;
security/liquidity; risk/Regime; trend; profit/trailing; time review. List order
breaks ties within a category. The first true trigger wins. If a planned
higher-priority trigger needs an `UNKNOWN` input, evaluation stops with
`WAIT_UNKNOWN_EVIDENCE`; it is never interpreted as clear, HOLD, or PASS.

Price/time/fraction thresholds and quantity actions come exclusively from the
entry-time plan. A quantity action uses `initial_quantity × quantity_fraction`,
capped by the current PAPER position. A deterministic exit order identity
already present in the current ledger yields `TRIGGER_ALREADY_APPLIED`, which
prevents a repeated partial harvest or exit from creating another intent.

Trailing evaluates drawdown against `prior_high_watermark`; only after the
decision does the output advance `next_high_watermark` with the current price.
This prevents current/future maxima from being used to choose a historical rule.

### Authority and population

The initial population is frozen/synthetic. Market Regime is an input fact only;
this lane does not calculate or promote it. A plan may omit a Regime trigger for
the explicitly user-scoped mechanism lab, while any planned Regime trigger with
UNKNOWN/NOT_EVALUATED input waits fail closed. Every investment, live exit,
quantity, action, exchange order, broker, withdrawal, Production, Trading, and
REAL-capital authority remains false.

### Failures and counterexamples

The following are rejected or wait explicitly: changed/rehashed entry account,
source entry order mismatch, plan created before the fill, non-canonical values,
duplicate trigger/order identity, category-order tampering, stale price, current
account/plan market mismatch, absent position, observation from the future,
hindsight high watermark, UNKNOWN planned signal, and an already-applied trigger.

Persisted output embeds plan, current account, and observation and re-runs their
production validators plus the full derivation. Rehashing an action, quantity,
blocker, high watermark, or trigger selection cannot make it valid.

### Consumers

P10-11 may consume the deterministic PAPER order identity and target quantity
only when an explicit lab harness supplies a separate PAPER sell intent. Portal
may show the read-only action/status and audit lineage. Neither consumer gains
exchange or live authority.

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

### Order-draft trigger binding (P5-09 to P7-13)

`build_exit_plan_from_order_draft` is the mechanical adapter between a P5-09
order draft and this plan. It takes every `build_exit_plan` identity, account,
and contract argument except `triggers`, plus an explicit `order_draft` and an
explicit `trigger_bindings` list.

Each binding supplies exactly `source_field`, `trigger_id`, `category`,
`action`, `quantity_fraction`, `paper_order_id`, and
`paper_order_idempotency_key`. The caller therefore chooses every category,
action, fraction, and deterministic PAPER order identity; the adapter has no
policy defaults and adds no stop, expiry, or review trigger of its own.

The only thing the adapter derives is the condition and threshold of the
requested draft field:

| `source_field`       | condition           | threshold                     |
| -------------------- | ------------------- | ----------------------------- |
| `planned_stop_price` | `PRICE_AT_OR_BELOW` | exact canonical positive price |
| `expires_at`         | `TIME_AT_OR_AFTER`  | exact validated UTC timestamp  |
| `next_review_at`     | `TIME_AT_OR_AFTER`  | exact validated UTC timestamp  |

Binding order is preserved verbatim; the adapter never sorts. Two bindings may
read the same draft field, because each still carries its own explicit category,
action, fraction, and distinct identities; duplicated identities are rejected.
Fail-closed
rejections include a missing, null, non-string, non-canonical, non-positive, or
malformed requested draft value, an absent/unknown/duplicated binding key, an
unsupported `source_field`, an invalid category/action/fraction, quantity
metadata incompatible with the chosen action, a duplicate trigger or PAPER order
identity, and a category-priority inversion. The plan itself is then built by
the unchanged `build_exit_plan`/`validate_exit_plan` pair, so the source entry
account, positive filled BUY requirement, entry market/time/quantity binding,
and every plan authority flag are enforced exactly as for a direct call. A plan
built through the adapter is byte-identical to the direct call with the same
resulting triggers. Caller objects are never mutated, the returned plan is
detached, and repeated calls are deterministic.

The adapter is an offline parameterized mechanism only. It does not authenticate
the draft, ratify any threshold as policy, or permit runtime activation.
`source_entry_plan_ref` and `source_entry_plan_sha256` keep their existing
entry-plan meaning and are not redefined as draft attestations. Supplying a
trustworthy draft and a reviewed binding table is an explicit caller
prerequisite: the provenance of the draft, ratification of the values inside it,
and operational approval to act on the resulting plan all remain separate,
outside this module.

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

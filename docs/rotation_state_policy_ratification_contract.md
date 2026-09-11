# P2-05 CIO-ratified rotation state policy — identity/evidence

Status: CIO ratification evidence only. This is not a repository default
state policy, does not raise operational readiness by itself, and opens no
Regime/Candidate/Stage/briefing/Production/trading authority. See
`docs/rotation_state_ledger_contract.md` for the ledger mechanics this
evidence is bound to; that contract's `repository_default_policy: "ABSENT"`
invariant is unchanged.

## What CIO ratified

The 9-cell structural-bucket-transition → `EMERGING/STRONG/WEAKENING` mapping:

| prior → current | state |
| --- | --- |
| BOTTOM_TO_BOTTOM | WEAKENING |
| MIDDLE_TO_BOTTOM | WEAKENING |
| TOP_TO_BOTTOM | WEAKENING |
| BOTTOM_TO_MIDDLE | EMERGING |
| MIDDLE_TO_MIDDLE | EMERGING |
| TOP_TO_MIDDLE | WEAKENING |
| BOTTOM_TO_TOP | STRONG |
| MIDDLE_TO_TOP | STRONG |
| TOP_TO_TOP | STRONG |

Semantic definition (also stored verbatim in
`config/rotation_state_policy_ratification_contract.json`'s
`state_semantics`):

- `STRONG` = current bucket is `TOP`.
- `WEAKENING` = current bucket is `BOTTOM`, **or** the entity just fell out of
  `TOP` into `MIDDLE` (`TOP_TO_MIDDLE`).
- `EMERGING` = current bucket is `MIDDLE`, except the `TOP_TO_MIDDLE` case
  above.

**`MIDDLE_TO_MIDDLE = EMERGING` is not "rising."** Its documented meaning is
`NON_WEAKENING_MIDDLE_STATE`: an entity that held its `MIDDLE` position with
no evidence of decline. It is grouped with `EMERGING` only because the
vocabulary has no fourth, neutral word — it must not be read or surfaced
downstream as directional momentum.

Per-market `maximum_ledger_gap_days`:

| Market | Days | Status |
| --- | --- | --- |
| US | 4 | RATIFIED |
| KOREA | 7 | RATIFIED |
| CRYPTO | 2 | RATIFIED |

`ratified_by = "CIO"`, `ratified_at_utc = "2026-09-11T16:36:38Z"`,
`effective_from = "2026-09-11"`, `effective_to = null` (open-ended) for all
three markets. These four fields, the mapping, and the vocabulary are fixed
constants in `config/rotation_state_policy_ratification_contract.json` and
cannot be overridden by any caller of `rotation/rotation_state_policy_
ratification.py::build_policy()` — that function accepts only `market` plus
the two upstream binding fields (`rotation_contract_version`,
`rotation_policy_sha256`) that a real P2-02/03/04 packet already carries.

## Korea gap derivation (mechanical, calendar-sourced)

Source: `evidence/market_calendar/krx_global_holiday/2026-09-09/capture-2026.json`
— the raw KRX Global `GLB99000001.jspx` holiday response for 2026, captured
2026-09-08, `provider_id = KRX_GLOBAL_MARKET_CLOSING_HOLIDAY_01023`.

Assumption: one full P2-03 packet is generated per KRX trading day, post
close (`ONE_FULL_P2_03_PACKET_PER_KRX_TRADING_DAY_POST_CLOSE`, recorded in
`config/rotation_state_policy_ratification_contract.json`'s
`markets.KOREA.operational_assumption`).

- Natural maximum calendar-day gap between two consecutive scheduled trading
  sessions in 2026: **6 days** (2026-02-13 Fri → 2026-02-19 Thu, the Seollal
  block directly abutting a weekend).
- Plus one missed-session grace (the single session immediately following
  that gap is also missed): **7 days** (2026-02-12 Thu → 2026-02-19 Thu).
- Ratified `maximum_ledger_gap_days` for KOREA = **7**.

## Cadence disclaimer — do not confuse ratification with readiness

`.github/workflows/p2-03-korea-observation-pair.yml` — the combined P2-03
full-packet workflow — is **`workflow_dispatch`-only**. It carries no
`schedule:`/cron trigger. Only its Leadership half
(`korea-leadership-live-proof.yml`) is scheduled (weekdays, 09:10/09:25 UTC).

Ratifying `maximum_ledger_gap_days = 7` for Korea is **not** evidence that
Korea's current cadence satisfies it, and does not by itself change Korea's
readiness. It is a ceiling derived from the canonical KRX calendar for the
day scheduling is eventually turned on. This ratification does not add,
modify, or request a schedule/cron change for any workflow — that remains
explicitly out of scope.

## What this evidence does and does not unlock

- `rotation/rotation_state_policy_ratification.py::build_policy(market,
  rotation_contract_version, rotation_policy_sha256)` can construct a
  complete, schema-valid `rotation_state_policy/1` object for US, KOREA, or
  CRYPTO **once a real matching P2-02/03/04 packet exists** to bind to. It
  cannot construct one for US or CRYPTO today because neither market has
  produced a single real rotation packet yet (readiness stays 0/3 — see
  `rotation/rotation_state_ledger_operational_readiness.py`).
- Building a policy object is not itself an append to the ledger. Only
  `rotation_state_ledger.apply_rotation()` actually consuming a real packet
  and this policy produces a natural record. Zero natural records today stay
  zero after this evidence lands; no synthetic or backfilled record is
  created by this change.
- `state_ledger_authorized` / `p2_state_vocabulary_authorized` remain `false`
  for a market until its first real natural record is appended — ratifying
  the policy alone does not flip them (see `_authority()` in
  `rotation/rotation_state_ledger.py`, driven by `bool(records)`).
- Regime, Candidate, Stage, briefing, Production, and trading authority all
  remain `false` for every market regardless of this ratification. Each
  requires its own separate future CIO ratification.

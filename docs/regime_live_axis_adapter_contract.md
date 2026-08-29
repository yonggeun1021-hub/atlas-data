# Regime Live Axis Adapter (`regime_live_axis_adapter/v6`)

`regime/live_axis_adapter.py` proves, for each of the fifteen `market/axis`
combinations in the P1-COM-01 envelope, whether qualified point-in-time
evidence currently exists. It never interprets that evidence: every result is
`DEFINED` (evidence present, PIT-consistent, lineage complete, rebuilt from
raw and cross-checked against the caller's component row) or `UNDEFINED`
(fail-closed for any reason -- missing, stale, taxonomy-incomplete,
policy-unratified, or PIT-inconsistent). No axis or aggregate output from this
module, or from anything downstream that consumes it (`regime/output_contract.py`),
ever carries a Regime, direction, confidence, threshold, weight, ranking, or
action value. All `*_authorized` fields stay `false`.

## Korea five-axis evidence coverage

`regime_live_axis_adapter/v6` binds `KR/TREND`, `KR/BREADTH`, `KR/RISK_VOL`,
`KR/LIQUIDITY`, and `KR/LEADERSHIP` to the same validated official-KRX
`korea_market_signals/1` aggregate packet. `DEFINED` means only that the
measurement exists with point-in-time lineage. It is not a Korea
`RISK_ON`/`RISK_OFF` interpretation.

## P1-CR-08: Crypto axis coverage

As of `regime_live_axis_adapter/v5` (P1-CR-08), all five Crypto axes have a
real binding -- up from three of five before this change:

| Axis | Source component | Status as of this PR |
| --- | --- | --- |
| `CRYPTO/TREND` | `BTC_TREND` | Bound since `v4`; unchanged |
| `CRYPTO/RISK_VOL` | `BTC_RISK` | Bound since `v4`; unchanged |
| `CRYPTO/LIQUIDITY` | `STABLECOIN_NET_ISSUANCE` **and/or** `UPBIT_MARKET_EVIDENCE` | Bound since `v4` (stablecoin); **extended** in this PR to also accept P4-07 Upbit microstructure evidence as a second, independently-qualifying input |
| `CRYPTO/BREADTH` | `CRYPTO_BREADTH` (P1-CR-06) | **Newly bound** in this PR |
| `CRYPTO/LEADERSHIP` | `CRYPTO_LEADERSHIP` (P1-CR-07) | **Newly bound** in this PR, but see the blocker below |

### `CRYPTO/LIQUIDITY`'s two-input rule

`CRYPTO/LIQUIDITY` is `DEFINED` when **at least one** of its two qualifying
inputs has qualified, PIT-valid evidence -- an "any qualifying input present"
rule, still a pure presence check, never a judgment about which input is more
informative:

- Stablecoin net issuance only -> `DEFINED` (unchanged behavior from `v4`;
  cites the stablecoin evidence pointer; warns
  `CRYPTO_LIQUIDITY_UPBIT_MICROSTRUCTURE_INPUT_UNAVAILABLE`)
- Upbit microstructure only -> `DEFINED` (new in this PR; cites the Upbit
  evidence pointer; warns `CRYPTO_LIQUIDITY_STABLECOIN_INPUT_UNAVAILABLE`)
- Both present -> `DEFINED` (cites the stablecoin pointer for deterministic,
  backward-compatible byte-identity with `v4` outputs; no sibling-missing
  warning)
- Neither present -> `UNDEFINED` (`LIVE_AXIS_EVIDENCE_UNAVAILABLE`)

In production today, real Upbit microstructure evidence is not yet
achievable even when its binding logic runs: `config/upbit_market_evidence_policy.json`
carries `approval_status: "PROPOSED_UNRATIFIED"`, and no
`evidence/crypto/upbit/microstructure/` raw capture directory is committed
yet. `CRYPTO/LIQUIDITY` therefore currently resolves via the stablecoin path
alone, exactly as it did before this PR -- fully backward compatible.

### Known blocker: `CRYPTO/LEADERSHIP` and Upbit-liquidity have no
### `daily_orchestrator.py` component-row producer yet

`regime/live_axis_adapter.py`'s bindings only ever *consume* a `component_id`
row handed to `build_axis_factors()`; they never fetch or capture evidence
themselves. `briefing/daily_orchestrator.py` already produces a
`CRYPTO_BREADTH` row (added by P1-CR-06, before this PR), so `CRYPTO/BREADTH`
activates immediately in production with no further wiring.

Neither a `CRYPTO_LEADERSHIP` row nor an `UPBIT_MARKET_EVIDENCE` row is
produced by `daily_orchestrator.py` today. Until a future PR adds that
capture/classification wiring (mirroring `_classify_crypto_breadth` /
`build_crypto_breadth`), both bindings fail closed to `UNDEFINED`
(`COMPONENT_MISSING` -> `LIVE_AXIS_EVIDENCE_UNAVAILABLE`) in every real daily
run, even though the binding logic itself is implemented, tested, and ready.
This is a genuine, expected, and intentionally scoped-out limitation of this
PR, not a bug: P1-CR-08's Step 1 explicitly asked only to wire the adapter
side against the source scripts that already exist, and both
`crypto_leadership.py` and `microstructure/upbit_market_evidence.py` are
purely derivation modules with no `daily_orchestrator.py` capture step of
their own yet.

Independently of that wiring gap, `CRYPTO_LEADERSHIP`'s own dual-window
methodology (a 7-day pilot window and a 30-day primary window, both built
from the same CR-06 Crypto Breadth snapshots) needs a real, unbroken run of
history to observe: as of this PR's evidence, `evidence/crypto/breadth/raw/`
holds roughly nine days of committed snapshots, short of even the 7-day pilot
window's `SOURCE_POINT_UNKNOWN` requirement in the earliest days of that
history and well short of the 30-day primary window. `CRYPTO/LEADERSHIP`
would therefore report `UNDEFINED` today even if the component-row wiring
existed -- a second, independent, and equally genuine reason it is not yet
observable in production, on top of (not instead of) the missing row.

## Known, user-acknowledged scope decision: evidence-presence only, not the Notion "5축 판정" interpreted values

P1-CR-08's Notion Crypto policy document describes a "5축 판정" (five-axis
determination) section whose axis values are **interpreted** market-state
labels (e.g. POSITIVE/NEUTRAL/NEGATIVE, 확산/편중/붕괴-style judgments for
breadth/leadership dispersion or concentration). This PR does **not**
implement that. It implements only `DEFINED`/`UNDEFINED` evidence-presence
per axis, matching the ratified `P1-COM-01` common Regime contract
(`regime/output_contract.py`) exactly, and does not add, weaken, or bypass
that contract's `PRE_SCORE_UNKNOWN_ONLY` enforcement in any way.

This is a real, identified gap between the Notion canon and this repository's
actual ratified authority -- surfaced during P1-CR-08's investigation and
escalated to the user, who explicitly chose evidence-presence-only scope
(2026-08-29) rather than asking this PR to invent an unratified interpretation
policy. It is a known, recorded, user-acknowledged limitation pending a
future policy/contract reconciliation -- not an implementation oversight, and
not something this PR should be read as quietly deciding on its own
authority. Any future work that wants to emit POSITIVE/NEUTRAL/NEGATIVE-style
axis values (or any other interpreted state) needs its own explicit design,
ratification, and a new `regime_output_contract` schema version -- this
module's `EVIDENCE_ONLY_NO_INTERPRETATION` mode and the `REGIME_INTERPRETATION_UNAUTHORIZED`
warning discipline on every `DEFINED` factor exist specifically to keep that
boundary honest until such a decision is made.

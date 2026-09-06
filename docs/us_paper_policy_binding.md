# US PAPER Reference Policy Binding

## The defect this closes

`config/paper_regime_reference_policy_v1.json` publishes a `markets.US` block
with a method name and nine numeric thresholds per axis. Until now
`regime/paper_regime_reference.py::build_us` did not read that block: every
comparison used a literal written into the function body. The two agreed by
coincidence, so the published policy was documentation rather than
configuration. A valid edited `markets.US` — a different BREADTH band, a
different VIX rung — produced an unchanged US classification, and a corrupted
one produced a silently normal result.

`build_us` now resolves `markets.US` before it reads any observation, and
executes the resolved values. The published numbers are unchanged by this
slice; only their causality changes.

## What is bound

| Axis | Configured keys | Executed comparison |
| --- | --- | --- |
| TREND | `positive_min_fraction`, `negative_max_fraction` | positive fraction of the 3 trend ETFs' 20-session returns |
| BREADTH | `positive_min`, `negative_max` | representative-ETF `advance_fraction` |
| RISK_VOL | `positive_below`, `neutral_below`, `negative_below` | FRED VIX level ladder |
| LEADERSHIP | `positive_min_fraction`, `negative_max_fraction` | positive fraction of the 12 group 20-session returns |
| LIQUIDITY | *(no numeric threshold)* | WRESBAL/TOTBKCR change sign pair |

That is nine executed threshold slots. `RISK_VOL.stress_min` is a tenth
published number but not a tenth comparison: it is the republished alias of
`negative_below`, validated equal to it, so the STRESS band entered by the
final `else` branch is exactly `vix >= stress_min`. LIQUIDITY contributes
semantics only — its `positive`/`negative`/`neutral` values must read
`both_positive`/`both_negative`/`mixed_or_zero`, which is the sign-pair rule
`build_us` implements.

## Semantics preserved exactly

- **Strict `<` at every VIX rung.** 15 is NEUTRAL, 25 is NEGATIVE, 30 is
  STRESS: a configured level belongs to the worse band. This is the US rule as
  written today and is deliberately not aligned with `build_kr`'s `<=` ladder.
- **`0.666667` is consumed as configured, not as 2/3.** The rounded threshold
  sits *above* 0.6666…, so a 2-of-3 TREND and an 8-of-12 LEADERSHIP are
  NEUTRAL, not POSITIVE; symmetrically `0.333333` sits *below* 0.3333…, so
  1-of-3 and 4-of-12 are NEUTRAL, not NEGATIVE. In practice only a unanimous
  TREND clears either side. That consequence is surprising but it is the
  published policy; this slice binds it rather than substituting a rational
  threshold or retuning the band.
- Axis directions, scores, `observed_value` shapes, Korean summaries,
  aggregation, confidence, the output schema, the policy bytes, the reference
  status, and the authority boundary are untouched. With the current policy the
  builder reproduces its previous output byte for byte.

## Fail-closed vocabulary

There is no numeric default anywhere in the resolution path: a policy defect
blocks the US reference instead of reverting to a former literal.

| Code | Raised when |
| --- | --- |
| `US_POLICY_MISSING` | `markets` or `markets.US` is absent or not an object |
| `US_POLICY_BLOCK_INVALID:<axis>` | an axis block is absent or not an object |
| `US_POLICY_METHOD_INVALID:<axis>` | the method is not the exact one these comparisons implement |
| `US_POLICY_SIGN_INVALID:<key>` | a LIQUIDITY sign word is not the configured vocabulary |
| `US_POLICY_VALUE_MISSING:<axis>.<key>` | a threshold key is absent |
| `US_POLICY_VALUE_INVALID:<axis>.<key>` | a threshold is non-scalar, boolean, unparsable, or non-finite |
| `US_POLICY_FRACTION_RANGE_INVALID:<axis>` | a fraction bound falls outside `[0, 1]` |
| `US_POLICY_FRACTION_ORDER_INVALID:<axis>` | `negative_max >= positive_min` |
| `US_POLICY_VIX_RANGE_INVALID` | the ladder floor is negative |
| `US_POLICY_VIX_ORDER_INVALID` | the ladder is not strictly increasing |
| `US_POLICY_STRESS_ALIAS_INVALID` | `stress_min != negative_below` |

Because the alias is checked in both directions, the two published STRESS
numbers cannot drift apart: moving the executed rung alone fails just as
moving the alias alone does.

## Scope and authority

This is a US binding only. `build_kr`, `build_crypto`, aggregation,
confidence, the common reference contract, and every retained artifact are
unchanged. The policy file's own status —
`PM_BASELINE_CANDIDATE_NOT_CIO_RATIFIED_SENSOR_POLICY` — is not upgraded by
being executed: binding a candidate policy to its consumer is not ratifying
it. Runtime regime, final regime, strategy, Stage, Buy, Action, Order,
capital, Production, and trading authority all remain false, and the US market
block stays `PAPER_REFERENCE_CLASSIFIED` with `runtime_regime: UNKNOWN`.

## Validation

`test/test_us_paper_policy_binding.py::USPaperPolicyBindingTest` covers, over
fixed synthetic packets rather than the mutable `data/latest_*` snapshots:

- `test_existing_policy_boundaries` — the configured strings resolve to the
  exact Decimals executed, and each axis flips at the published boundary,
  including the rounded-fraction consequence above.
- `test_nine_thresholds_are_causal` — editing any one of the nine slots moves
  that axis and only that axis.
- `test_methods_structures_and_signs_fail_closed` — malformed roots, blocks,
  methods (including a KR method borrowed into the US block), sign words,
  absent keys, booleans, and non-finite values all block the build.
- `test_numeric_order_and_fraction_guards` — fraction range and ordering, VIX
  floor and strict ordering, with the legal endpoints still accepted.
- `test_stress_alias_boundary` — the STRESS boundary tracks the pair, and any
  disagreement between the two published numbers fails closed.
- `test_policy_hash_generation_and_calculation_binding` — the policy bytes,
  `generation_id`, `payload_sha256`, and the US axis result move together, and
  a packet signed under the previous policy is no longer re-derivable.

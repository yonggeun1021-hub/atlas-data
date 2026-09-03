# Regime Decision Authority Boundary (P1-COM-05)

## Current result

`regime_decision_authority/v1` binds one validated `regime_output/v1` packet to
its independently rebuilt `regime_minimum_coverage/v1` result. It distinguishes
two reasons Atlas cannot classify a market yet:

- `BLOCKED_COVERAGE`: one or more of the five required axes are undefined.
- `BLOCKED_POLICY_UNRATIFIED`: all five axes exist, but the decision policy is
  not authorized.

Both results retain `regime=UNKNOWN`, `direction=UNKNOWN`, and
`confidence=null`. `NEUTRAL` is never a missing-data or missing-policy fallback.

## Policy boundary

The repository has no approved decision-policy registry. The following
components are therefore explicitly absent or unratified: factor normalization,
freshness, aggregation weights, classification thresholds, direction,
confidence, stress override, invalidation, and hysteresis. An input packet may
not self-declare any of them approved; this version accepts no external policy
payload and exposes no classification path.

The output binds both source packets by canonical SHA-256. The minimum-coverage
packet is re-derived from the Regime output, so a re-signed or independently
edited gate cannot be substituted.

## Ratified PAPER baseline v1 common aggregation (replay only)

`regime_common_aggregation_replay/v1` implements the already-merged ratified
common v1 values as an executable, hash-bound policy. Nothing in it is authored
here: `RATIFIED_COMMON_V1` is a verbatim transcription of `common_v1_alignment`
in `config/regime_source_owner_registry_v2.json`
(`CIO-GATE2-3MARKET-REGIME-SOURCE-FIRST-B-2026-09-01`, packet
`bdeb9b99…`, `policy_status=RATIFIED_PAPER_BASELINE_V1`). Loading the policy
fails closed when

- the registry alignment block is not canonically byte-identical to the
  transcription (`COMMON_V1_ALIGNMENT_MISMATCH`),
- the ratified decision identity or packet digest changes
  (`RATIFIED_DECISION_BINDING_INVALID`),
- the alignment's pinned digest for
  `config/regime_decision_authority_contract.json` does not match the file on
  disk (`LEGACY_CONTRACT_HASH_MISMATCH`),
- the numeric bands cannot be re-derived from the ratified expression strings
  `S>=3` / `-2<=S<=2` / `S<=-3`, `DELTA_S>=2` / `-1<=DELTA_S<=1` /
  `DELTA_S<=-2` (`COMMON_V1_THRESHOLD_DERIVATION_MISMATCH`), or
- the merged PAPER baseline packet
  `config/paper_regime_reference_policy_v1.json` carries different aggregation
  numbers (`PAPER_BASELINE_THRESHOLD_MISMATCH`).

The replay consumes **already-signed** axis directions only
(`POSITIVE`/`NEUTRAL`/`NEGATIVE`, plus `STRESS` on `RISK_VOL`). Raw market
measurements, floats, duplicate packet ids, and non-chronological packets are
rejected. Market-specific signed-axis normalization and freshness remain
unratified and are not inherited; the ratified market kill-stress condition is
recorded as `UNRATIFIED_NOT_IMPLEMENTED`, so `RISK_VOL=STRESS` is the only
implemented stress trigger.

Each PIT step reports coverage, the score `S`, the raw classification, the
confirmed regime after the ratified hysteresis (ordinary transition needs two
finalized packets, stress entry and UNKNOWN are immediate, stress exit needs two
consecutive non-stress packets with `S > -3` *and* the ordinary confirmation),
`DELTA_S` direction, and confidence. Coverage below 5/5 is `UNKNOWN`
immediately — never `NEUTRAL` — so a 3/5 Crypto sequence stays `UNKNOWN` until
5/5. Reports are re-derived byte-for-byte from the input sequence, so a resigned
or edited report fails closed.

## What this does not authorize

This contract does not normalize factors, assign weights, set thresholds,
classify `RISK_ON`, `NEUTRAL`, `RISK_OFF`, or `STRESS`, calculate confidence,
run an approved replay, select a strategy, change a candidate or Stage, allocate
capital, submit orders, or enable Production/trading. Those require a separately
ratified policy and a future contract version.

The common v1 replay above does not change that boundary. It stays
`SHADOW_PIT_REPLAY_ONLY_RUNTIME_NOT_WIRED`, keeps
`pit_replay_acceptance=NOT_ACCEPTED` (registry `forbidden_promotions` include
`PIT_REPLAY_ACCEPTANCE`), leaves `evaluate_decision_authority` unchanged, and
opens no authority beyond `common_aggregation_replay_authorized`.

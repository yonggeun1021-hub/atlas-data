# Atlas Daily Briefing — 2026-08-25 (morning)

Generated at: 2026-08-25T00:13:58Z
Component status counts: {'POLICY_BLOCKED': 12, 'UNAVAILABLE': 1, 'READY': 7, 'UNKNOWN': 0, 'PENDING': 14, 'DATA_BLOCKED': 4, 'DEGRADED': 1}

No action, order, Production, or trading authority is granted by this briefing. All such fields remain false/null.

## Data / Read-model health
- **STEP0_READ_MODEL_HEALTH**: OK
    - krx: ok=7 failed=0
    - dart: ok=7 failed=0
    - sec: ok=7 failed=0
  - source: `data/briefing_status.json`
- **KRX_PREOPEN_COMPACT**: OK
    - krx: ok=7 failed=0 date=2026-08-25
    - dart: ok=7 failed=0 date=2026-08-25
    - sec: ok=7 failed=0 date=2026-08-25
  - source: `data/latest_krx.json`
  - sha256: `7a81f36064551729054de3eba42272e934c2e1b40d32cfccdb774e8756efc0f9`
- **KRX_POST_CLOSE**: PENDING — MORNING_SLOT_USES_CONFIRMED_HISTORY_ONLY

## Filing & source evidence
- **DART_FILING_CONTENT**: DEGRADED — run_status=DEGRADED
    - records=2 run_status=DEGRADED
  - source: `data/latest_dart_content.json`
  - sha256: `e9d655350eaa6935925fdd573d7cfce6023776a000763d1f76d8f9444277cb85`
- **SEC_FILING_CONTENT**: OK
    - records=11 run_status=OK
  - source: `data/latest_sec_content.json`
  - sha256: `c6ea531b0710891696f04d96031a458bfeba2d63b278b79c3a5591723f6a14c5`
- **KOFIA_FIRST_SEEN**: POLICY_BLOCKED — SOURCE_AVAILABLE_AT_AND_API_UNIT_UNRATIFIED
    - captured_at=2026-08-24T22:48:54Z available_at=None
  - source: `evidence/kofia/first_seen/2026-08-25/run-32786439695-attempt-1`

## Sensors
- **US_BREADTH_MEMBERSHIP**: OK
    - snapshot_date=2026-08-21 members=13169
  - source: `evidence/us_breadth/raw/2026-08-21`
- **FREE_MARKET_DATA**: OK
    - VIXCLS=16.01 as_of=2026-08-20
    - Alpaca IEX partial: MSFT=483.33, NVDA=214.74, TSM=418.9
    - scope: IEX_PARTIAL_EVIDENCE_ONLY_NOT_MARKET_WIDE_OR_TRADE_AUTHORITY
  - source: `data/latest_free_market_data.json`
  - sha256: `a2f365af87dfb7817f8b1e2bd585cc8efb8925516a060dcae6c29e7ed11a99c2`
- **BTC_TREND**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **BTC_RISK**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **STABLECOIN_NET_ISSUANCE**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **CRYPTO_BREADTH**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE

## 3-Market Regime
- **THREE_MARKET_REGIME_HEADER**: PENDING — ALL_MARKETS_UNKNOWN_NO_LIVE_AXIS_ADAPTER_WIRED
    - US: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=0/5
    - KR: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=0/5
    - CRYPTO: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=0/5
  - sha256: `596922c15ced15dcc579040f06062ff91123aa84c3c988be8d264c59886e95aa`

## Rotation / Theme
- **ROTATION_DISCOVERY**: PENDING — NO_RATIFIED_ROTATION_OR_DISCOVERY_POLICY
    - rotation_changes=0 discovery_cases=0 new_candidates=0 existing_candidate_changes=0
  - sha256: `5c54758abd49710672d53bcf7e55c8754bfad1add6fa5dd3e1802326e7c513f6`
- **KOREA_ROTATION**: PENDING — NO_ROTATION_OBSERVATION_FOR_DECISION_DATE
  - source: `data/latest_korea_rotation.json`

## Rule status
- **RULE_EVALUATION**: POLICY_BLOCKED — ZERO_OF_TWENTY_FIVE_RULES_CONSUMABLE_BY_EVALUATOR
    - total_rules=25 PASS=0 FAIL=0 UNKNOWN=22 UNDEFINED=3
  - sha256: `6150de22bdc517aa4e7fe7879ed0fc0b1db424e86db45379e33c1104964fab36`

## Portfolio / Risk
- **PORTFOLIO_BUCKET**: POLICY_BLOCKED — CONSTITUTION_NOT_RATIFIED
- **PORTFOLIO_CURRENCY**: UNAVAILABLE — NO_LIVE_ASSET_MASTER_OR_POSITION_SNAPSHOT
- **CASH_EXPOSURE_US**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `ec1a2e3357dcd448d001853b26bf841916e4927062281ce2c8e8cee2a83b5cd8`
- **CASH_EXPOSURE_KOREA**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `6daa9c6a13a8298ee718d7596176bb5fd083b4d3dca7c08e720c7eaa9917d64a`
- **CASH_EXPOSURE_CRYPTO**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `f7470c9bf1dc652874de05a42d09683217454d510591fee8baec6a78181bbf8b`
- **INVERSE_US**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `15aaea44b154652a655417803ebf9cb6446302c065e505a8dfe7f5de5949ca03`
- **INVERSE_KOREA**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `002be4af4eef6214edcb8204a6cbf2af15a5e2dcd1759f13a2d1928221b85cd1`
- **INVERSE_CRYPTO**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `36a60f8fdcfcf9912ca1c118c0615cd4da674c55ec7e726d913a6909f369d29c`
- **LONG_SHORT_INVARIANT**: PENDING — NO_RULE_PASS_FAIL_TO_EVALUATE
    - long_results={'PASS': 0, 'FAIL': 0, 'UNKNOWN': 22, 'UNDEFINED': 3} short_pass=0 short_not_evaluated=25
  - sha256: `4c3e7a9f6eae5fadb2f1f7c504b5112972b64e03a8458e4f6fb0765e97d6fb9e`
- **HEDGE_ELIGIBILITY**: POLICY_BLOCKED — NO_CIO_RATIFIED_HEDGE_INSTRUMENT_REGISTRY
- **BEAR_HEDGE_BUDGET**: POLICY_BLOCKED — NO_CIO_RATIFIED_BEAR_HEDGE_BUDGET_SET
- **POSITION_SIZING**: POLICY_BLOCKED — NO_CIO_RATIFIED_SIZING_POLICY_OR_CONSTITUTION
- **CONCENTRATION_GUARD**: POLICY_BLOCKED — NO_CIO_RATIFIED_CONCENTRATION_POLICY
- **MARKET_THEME_BUDGET**: POLICY_BLOCKED — NO_CIO_RATIFIED_THEME_BUDGET
- **CRYPTO_EXPOSURE_LIMIT**: POLICY_BLOCKED — NO_CIO_RATIFIED_CRYPTO_LIMIT_POLICY
- **PLANNED_LOSS_BUDGET**: POLICY_BLOCKED — NO_RATIFIED_CONSTITUTION

## Decision Review
- **INVESTMENT_DECISION_REVIEW**: POLICY_BLOCKED — P5_OR_THESIS_AUTHORITY_NOT_AVAILABLE
    - subject=TSM review=BLOCKED trade_proposal=None money_action=NONE
    - blocker=EXTERNALLY_RATIFIED_TSM_RULE_PACKET_NOT_AVAILABLE
    - blocker=P5_DOWNSTREAM_ACTION_NOT_AUTHORIZED
    - blocker=P5_PASS_FAIL_NOT_AUTHORIZED
    - blocker=TSM_THESIS_PACKET_NOT_AVAILABLE
  - sha256: `da9912e5ad2fb11872f89c01c1874cbec2641677158dd96fb1d856a5b5942dec`

## Decision & action boundary
- **ACTION_BOUNDARY**: PENDING — NO_READY_CANDIDATES_FROM_DISCOVERY_OR_RULE_YET
  - sha256: `4c41ac1f99ef26502be8cdc33010cebb770b0f2be117b8547c3fca790c2f5011`
- **UNIFIED_DECISION**: PENDING — 4/6_COMPONENTS_AVAILABLE
    - state=NO_ACTION_AUTHORIZED action=None order_intent=None available_components=4/6
  - sha256: `5a0f92cdf05fddabf6723fecce119e3deb275d9b6e3e20910cabab1fea45f8f3`
- **ACTION_RISK_PORTFOLIO_SUMMARY**: PENDING — MOST_UPSTREAM_SOURCES_NOT_YET_LIVE
    - available_sources=8/15 evaluated_actions=0 risk_breach_sources=0
  - sha256: `c40e645457c90de67dfa7e23a699bdd8fe031ed0c6dbad779f45f9a29468fedd`

## Shadow learning record
- **INVESTMENT_REVIEW_SHADOW**: POLICY_BLOCKED — NO_RATIFIED_PASS_REVIEW_TO_RECORD
    - ledger_record_created=False capital={'authorized': False, 'amount': 0} action=None order=None stage_change=None
  - sha256: `258d800f7042938799a868d395f34dad4a2ef467b9c62adc68cdf9416f667643`

## Forward Alpha Review (Pilot)
- **FORWARD_ALPHA_REVIEW**: OK
    - pilot_subjects=['034020.KS', '267260.KS', '298040.KS', 'TSM']
    - 034020.KS: opportunity_state=BLOCKED shadow_action=REJECT comparison_label=BLOCKED
    - 267260.KS: opportunity_state=REJECTED shadow_action=REJECT comparison_label=REJECT
    - 298040.KS: opportunity_state=WAIT_FOR_PRICE shadow_action=WAIT comparison_label=WAIT
    - TSM: opportunity_state=WAIT_FOR_PRICE shadow_action=WAIT comparison_label=WAIT
  - sha256: `f0227f73a758a5548c93cef6b085c091a49de2bca7378c3c0cdbe9d9eb8367f6`

## Dynamic Clock (Opportunity Trigger / Review Queue)
- **DYNAMIC_CLOCK**: OK
    - policy_approval_status=PROVISIONAL_CIO_MVP
    - BTC: raw_triggers(audit only)=0 immediate_review=0 watch_review=0 observation_only=0 expired=1 calendar_confidence=VERIFIED_24_7 not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FLOW_REVERSAL', 'FUNDAMENTAL_REVISION', 'RELATIVE_STRENGTH_REVERSAL']
    - CRYPTO: raw_triggers(audit only)=83 immediate_review=0 watch_review=57 observation_only=0 expired=34 calendar_confidence=VERIFIED_24_7 not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FLOW_REVERSAL', 'FUNDAMENTAL_REVISION']
      - WATCH_REVIEW AAVE/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW ADA/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW ATOM/USD trigger_types=['PRICE_CONFIRMATION'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BCH/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BLESS/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BNB/USD trigger_types=['PRICE_CONFIRMATION'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW CC/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW COTI/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW CRV/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW DASH/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW DOGE/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW DOT/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW ENA/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW ESPORTS/USD trigger_types=['INVALIDATION_TRIGGER'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW ETH/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-24 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - ... +42 more WATCH_REVIEW candidates (full list: evidence/operational/dynamic_clock/briefing_section.json)
    - KOREA: raw_triggers(audit only)=3 immediate_review=0 watch_review=2 observation_only=0 expired=2 calendar_confidence=UNVERIFIED_NO_HOLIDAY_CALENDAR not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FUNDAMENTAL_REVISION']
      - WATCH_REVIEW 000660 trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=WEAK reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-24T21:57:55Z next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW 005930 trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=MODERATE reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-24T21:57:55Z next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: price_reflection is linked (price_state=MODERATE) but threshold_basis=PROVISIONAL -- PROVISIONAL diagnostics never elevate tier; no thesis linkage exists either
  - sha256: `ae6f80f9769f826f9c7965b11c0c44e6886330fe471f8cb0cfe0412a9694cbb8`

## PENDING / UNKNOWN / DEGRADED / BLOCKED components
KRX_POST_CLOSE, DART_FILING_CONTENT, KOFIA_FIRST_SEEN, BTC_TREND, BTC_RISK, STABLECOIN_NET_ISSUANCE, CRYPTO_BREADTH, THREE_MARKET_REGIME_HEADER, ROTATION_DISCOVERY, KOREA_ROTATION, RULE_EVALUATION, PORTFOLIO_BUCKET, PORTFOLIO_CURRENCY, ACTION_BOUNDARY, UNIFIED_DECISION, INVESTMENT_DECISION_REVIEW, CASH_EXPOSURE_US, CASH_EXPOSURE_KOREA, CASH_EXPOSURE_CRYPTO, INVERSE_US, INVERSE_KOREA, INVERSE_CRYPTO, LONG_SHORT_INVARIANT, HEDGE_ELIGIBILITY, BEAR_HEDGE_BUDGET, POSITION_SIZING, CONCENTRATION_GUARD, MARKET_THEME_BUDGET, CRYPTO_EXPOSURE_LIMIT, PLANNED_LOSS_BUDGET, ACTION_RISK_PORTFOLIO_SUMMARY, INVESTMENT_REVIEW_SHADOW

## Unresolved boundaries
- REGIME_AXIS_LIVE_ADAPTER_NOT_WIRED
- ROTATION_AND_DISCOVERY_POLICY_UNRATIFIED
- RULE_REGISTRY_NOT_CONSUMABLE
- PORTFOLIO_CONSTITUTION_NOT_RATIFIED
- ACTION_AND_ORDER_NOT_AUTHORIZED
- PRODUCTION_NOT_AUTHORIZED
- SAME_DAY_AUTOMATIC_RECOVERY_TRIGGER_NOT_SCHEDULED

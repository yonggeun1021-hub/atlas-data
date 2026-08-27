# Atlas Daily Briefing — 2026-08-27 (evening)

Generated at: 2026-08-27T14:18:23Z
Component status counts: {'DATA_BLOCKED': 4, 'PENDING': 13, 'UNAVAILABLE': 1, 'READY': 10, 'POLICY_BLOCKED': 12, 'UNKNOWN': 1, 'DEGRADED': 0}

No action, order, Production, or trading authority is granted by this briefing. All such fields remain false/null.

## 1. Regime
- status: PENDING
- as_of: 2026-08-27
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- unknown_reason: SOURCE_COMPONENT_NOT_READY
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: THREE_MARKET_REGIME_HEADER=PENDING

## 2. Cross-Market Flow
- status: UNKNOWN
- as_of: 2026-08-25
- evidence_grade: UNKNOWN (CROSS_MARKET_EVIDENCE_GRADE_AGGREGATION_UNRATIFIED)
- unknown_reason: CROSS_MARKET_COMPARISON_POLICY_UNRATIFIED
- invalidation: UNKNOWN (CROSS_MARKET_INVALIDATION_POLICY_UNRATIFIED)
- sources: FREE_MARKET_DATA=AVAILABLE, KRX_POST_CLOSE=UNKNOWN, STABLECOIN_NET_ISSUANCE=UNKNOWN
- evidence_class_counts: {'DIRECT_FLOW': 2, 'MARKET_IMPLIED_FLOW': 1, 'MACRO_CONTEXT': 1, 'UNKNOWN': 0}
- evidence_status_counts: {'UNKNOWN': 3, 'AVAILABLE': 1}
- comparison_observation_dates: ['2026-08-25']
- flow_direction: UNKNOWN (no cross-market comparison authority)

## 3. Theme Rotation
- status: DATA_BLOCKED
- as_of: UNKNOWN
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- unknown_reason: SOURCE_AS_OF_DATE_MISMATCH
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: ROTATION_DISCOVERY=PENDING, KOREA_ROTATION=PENDING

## 4. Capital Action
- status: PENDING
- as_of: 2026-08-27
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- unknown_reason: SOURCE_COMPONENT_NOT_READY
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: ACTION_RISK_PORTFOLIO_SUMMARY=PENDING

## 5. Assets
- status: READY
- as_of: 2026-08-27
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: SHADOW_ENTRY_REVIEW=READY

## 6. Entry / Exit / Size
- status: POLICY_BLOCKED
- as_of: 2026-08-27
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- unknown_reason: SOURCE_COMPONENT_NOT_READY
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: SHADOW_ENTRY_REVIEW=READY, POSITION_SIZING=POLICY_BLOCKED, PLANNED_LOSS_BUDGET=POLICY_BLOCKED

# Supporting Evidence and System Health

## Data / Read-model health
- **STEP0_READ_MODEL_HEALTH**: OK
    - krx: ok=7 failed=0
    - dart: ok=7 failed=0
    - sec: ok=7 failed=0
  - source: `data/briefing_status.json`
- **KRX_PREOPEN_COMPACT**: OK
    - krx: ok=7 failed=0 date=2026-08-27
    - dart: ok=7 failed=0 date=2026-08-27
    - sec: ok=7 failed=0 date=2026-08-27
  - source: `data/latest_krx.json`
  - sha256: `02c7c86a7346d89895ebd44f657a49186f6b011f3438171d49c6abdc26aeee9b`
- **KRX_POST_CLOSE**: UNKNOWN — UNKNOWN
    - observed_unconfirmed: symbols=None decision_eligible=None confirmed_same_day=None
  - sha256: `3f712cb13e9c77619074626a7c0b2bc9ebaf289157fe37567beed686c3cfecf1`

## Filing & source evidence
- **DART_FILING_CONTENT**: OK
    - records=2 run_status=OK
  - source: `data/latest_dart_content.json`
  - sha256: `08d6d4adaa117876f7f50060883a44177220db409a3389a53a533874e5fde450`
- **SEC_FILING_CONTENT**: OK
    - records=15 run_status=OK
  - source: `data/latest_sec_content.json`
  - sha256: `3ebd8ccf75f2195c40b9582467a742e8c2f85f3f3c5eb8c36aada6d882744027`
- **KOFIA_FIRST_SEEN**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE

## Sensors
- **US_BREADTH_MEMBERSHIP**: OK
    - snapshot_date=2026-08-25 members=13173
  - source: `evidence/us_breadth/raw/2026-08-25`
- **FREE_MARKET_DATA**: OK
    - VIXCLS=15.45 as_of=2026-08-25
    - Alpaca IEX partial: MSFT=496.36, NVDA=209.37, TSM=417
    - scope: IEX_PARTIAL_EVIDENCE_ONLY_NOT_MARKET_WIDE_OR_TRADE_AUTHORITY
  - source: `data/latest_free_market_data.json`
  - sha256: `c0e066dbee45bac309beea97392382898f7775b7e1fc997486ef897f766ecc4e`
- **BTC_TREND**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **BTC_RISK**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **STABLECOIN_NET_ISSUANCE**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **CRYPTO_BREADTH**: POLICY_BLOCKED — TAXONOMY_COVERAGE_UNKNOWN
    - status=UNKNOWN selected_assets=None
  - source: `evidence/crypto/breadth/raw/2026-08-27`

## 3-Market Regime
- **THREE_MARKET_REGIME_HEADER**: PENDING — LIVE_AXIS_EVIDENCE_WIRED_REGIME_SCORING_UNRATIFIED
    - US: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=1/5
    - KR: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=0/5
    - CRYPTO: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=0/5
  - sha256: `bb6a99185fd650c248bd8c5959f4286a74201d60976f8c6f98d8e2b49398571c`

## Rotation / Theme
- **ROTATION_DISCOVERY**: PENDING — DART_OBSERVATIONS_PRESENT_ESCALATION_BLOCKED
    - rotation_changes=0 discovery_cases=13 new_candidates=0 existing_candidate_changes=0 signal_observations=74 dart_observations=2 ready=0 entry=0
    - DART observations=2 raw_verified=1 metadata_only=1 source_failed=0 content_failed=0 event_type=UNRATIFIED importance=UNRATIFIED promotion=NOT_AUTHORIZED
    - DART 034020 두산에너빌리티: 단일판매ㆍ공급계약체결 evidence=METADATA_ONLY_STAGE_NOT_ASSIGNED action=null
    - DART 329180 HD현대중공업: 단일판매ㆍ공급계약체결 evidence=RAW_BYTES_VERIFIED_ITEM_EXTRACTION_UNRATIFIED action=null
    - signal_markets={'BTC': 1, 'CRYPTO': 68, 'KOREA': 5} tier_diagnostic_only={'IMMEDIATE_REVIEW': 0, 'WATCH_REVIEW': 74, 'OBSERVATION_ONLY': 0} promotion=NOT_AUTHORIZED
    - wildcard_observations=0 cases=0 pending=0 importance=UNRATIFIED promotion=NOT_AUTHORIZED
  - sha256: `51b7723087ae79b17859d5b92a9a8c233c3e0f0be134266d311e99f7b5d9245e`
- **KOREA_ROTATION**: PENDING — NO_ROTATION_OBSERVATION_FOR_DECISION_DATE
  - source: `data/latest_korea_rotation.json`

## New Discovery / candidate change
- **BUSINESS_ACCELERATION**: PENDING — RADAR_CASE_RECORDED_IMPORTANCE_AND_RANKING_UNRATIFIED
    - scope=TSM_SEC_MONTHLY_REVENUE_ONLY reports=3 series=2 cases=1
    - TSM TSM_CUMULATIVE_REVENUE_YOY_SEC: pattern=TWO_STEP_ACCELERATION_OBSERVED values_pct=['30.000000000000', '35.600000000000', '37.000000000000'] candidate_eligible=False
    - TSM TSM_MONTHLY_REVENUE_YOY_SEC: pattern=LATEST_STEP_NOT_UP values_pct=['30.100000000000', '67.900000000000', '44.700000000000'] candidate_eligible=False
  - sha256: `d58a827b7e153d4efd281efb3ba11f1d77456ff186e17c4d9aed588f4b5f5d68`

## Rule status
- **RULE_EVALUATION**: POLICY_BLOCKED — ZERO_OF_TWENTY_FIVE_RULES_CONSUMABLE_BY_EVALUATOR
    - total_rules=25 PASS=0 FAIL=0 UNKNOWN=22 UNDEFINED=3
  - sha256: `2db86bb38ba3e4004c3199af3efc8c18a53f08c3c53e5d0c2bfdc449ed955dd5`

## Portfolio / Risk
- **PORTFOLIO_BUCKET**: POLICY_BLOCKED — CONSTITUTION_NOT_RATIFIED
- **PORTFOLIO_CURRENCY**: UNAVAILABLE — NO_LIVE_ASSET_MASTER_OR_POSITION_SNAPSHOT
- **CASH_EXPOSURE_US**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `88626ee64ee9424483882c303ee570e15440acdd6b3a1ee203fb0b5ccd59f6f1`
- **CASH_EXPOSURE_KOREA**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `dfc9d9d2704bd68eda1b81945ef21230fae09da127aa0a959af686d4507be43d`
- **CASH_EXPOSURE_CRYPTO**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `a15371e3d05e7d9c7a3905472b1272b0d5dcc3fcb450c0384bea5566294dd490`
- **INVERSE_US**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `a3a8d2ff375d6f9e27bd468e0209633e0b20735d53b88b82926e8ea4fa1c7e07`
- **INVERSE_KOREA**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `3b29853cfe38a45a3663a757b0e585e8f45ccc76dce63614314d07bab260ef33`
- **INVERSE_CRYPTO**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `3adf5ef486d19c9ef790d4d720eaf252464fb8f8f0a8618c3b7398240e724c6c`
- **LONG_SHORT_INVARIANT**: PENDING — NO_RULE_PASS_FAIL_TO_EVALUATE
    - long_results={'PASS': 0, 'FAIL': 0, 'UNKNOWN': 22, 'UNDEFINED': 3} short_pass=0 short_not_evaluated=25
  - sha256: `f2eb3b010c67189d698f6bded22ee98216ac3e4fb003bac95fccca6fdc153b79`
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
  - sha256: `35a92b1d157ba0de62a38b22b3d7eacb84543ab0b652c1942c0b8429fb209254`

## Decision & action boundary
- **ACTION_BOUNDARY**: OK — DYNAMIC_CLOCK_SIGNAL_OBSERVATIONS_BOUND_READY_NOT_EVALUATED_NO_ACTION_AUTHORITY
  - sha256: `ff5011ffda3c1bc80158f1c6ffb8753776617cc93367f83532b88e1dddb9366c`
- **UNIFIED_DECISION**: PENDING — 4/6_COMPONENTS_AVAILABLE
    - state=NO_ACTION_AUTHORIZED action=None order_intent=None available_components=4/6
  - sha256: `0c119bf126598fa9cd3c18ea68fbb02617d0280da3ecd74ba31f3e73317786cb`
- **ACTION_RISK_PORTFOLIO_SUMMARY**: PENDING — MOST_UPSTREAM_SOURCES_NOT_YET_LIVE
    - available_sources=8/15 evaluated_actions=0 risk_breach_sources=0
  - sha256: `004f1eefd1a9183864d148adce15d5d0049a38ea9f6a2bcb9aeee27968c964be`

## Shadow learning record
- **INVESTMENT_REVIEW_SHADOW**: POLICY_BLOCKED — NO_RATIFIED_PASS_REVIEW_TO_RECORD
    - ledger_record_created=False capital={'authorized': False, 'amount': 0} action=None order=None stage_change=None
  - sha256: `b6bd5c6c2058ad74dd8428a00e8a26127176a872359d94dadd2df0534d9cae61`

## Forward Alpha Review (Pilot)
- **FORWARD_ALPHA_REVIEW**: OK
    - pilot_subjects=['034020.KS', '267260.KS', '298040.KS', 'TSM']
    - 034020.KS: opportunity_state=BLOCKED shadow_action=REJECT comparison_label=BLOCKED
    - 267260.KS: opportunity_state=REJECTED shadow_action=REJECT comparison_label=REJECT
    - 298040.KS: opportunity_state=WAIT_FOR_PRICE shadow_action=WAIT comparison_label=WAIT
    - TSM: opportunity_state=WAIT_FOR_PRICE shadow_action=WAIT comparison_label=WAIT
  - sha256: `def75999b3a6f3d75675eda5be6943a09bf5d6169b1dc00c2189da5516e6ebf1`

## Dynamic Clock (Opportunity Trigger / Review Queue)
- **DYNAMIC_CLOCK**: OK
    - policy_approval_status=PROVISIONAL_CIO_MVP
    - BTC: raw_triggers(audit only)=1 immediate_review=0 watch_review=1 observation_only=0 expired=1 calendar_confidence=VERIFIED_24_7 not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FLOW_REVERSAL', 'FUNDAMENTAL_REVISION', 'RELATIVE_STRENGTH_REVERSAL']
      - WATCH_REVIEW BTC trigger_types=['PRICE_CONFIRMATION'] price_state=OVEREXTENDED reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-26T03:39:32Z next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
    - CRYPTO: raw_triggers(audit only)=92 immediate_review=0 watch_review=68 observation_only=0 expired=69 calendar_confidence=VERIFIED_24_7 not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FLOW_REVERSAL', 'FUNDAMENTAL_REVISION']
      - WATCH_REVIEW AAVE/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-27 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW ADA/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW AERO/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW AKE/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-27 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW AKT/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-28 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW APR/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-28 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BCH/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BICO/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-28 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BLESS/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-28 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BMT/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-28 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BNB/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-28 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW CAP/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-27 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW CC/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW COTI/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-28 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW CRV/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - ... +53 more WATCH_REVIEW candidates (full list: evidence/operational/dynamic_clock/briefing_section.json)
    - KOREA: raw_triggers(audit only)=8 immediate_review=0 watch_review=5 observation_only=0 expired=3 calendar_confidence=UNVERIFIED_NO_HOLIDAY_CALENDAR not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FUNDAMENTAL_REVISION']
      - WATCH_REVIEW 000660 trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=STRONG_MOMENTUM reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-26T23:38:52Z next_review_at=2026-08-27 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW 005930 trigger_types=['FLOW_REVERSAL', 'RELATIVE_STRENGTH_REVERSAL'] price_state=STRONG_MOMENTUM reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-26T23:38:52Z next_review_at=2026-08-27 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: price_reflection is linked (price_state=STRONG_MOMENTUM) but threshold_basis=PROVISIONAL -- PROVISIONAL diagnostics never elevate tier; no thesis linkage exists either
      - WATCH_REVIEW 012450 trigger_types=['FLOW_REVERSAL'] price_state=STRONG_MOMENTUM reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-26T23:38:52Z next_review_at=2026-08-28 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW 034020 trigger_types=['FLOW_REVERSAL', 'PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=OVEREXTENDED reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-26T23:38:52Z next_review_at=2026-08-28 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=3 independent trigger types, but capped at WATCH_REVIEW: price_reflection is linked (price_state=OVEREXTENDED) but threshold_basis=PROVISIONAL -- PROVISIONAL diagnostics never elevate tier; no thesis linkage exists either
      - WATCH_REVIEW 267260 trigger_types=['FLOW_REVERSAL'] price_state=MODERATE reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-26T23:38:52Z next_review_at=2026-08-28 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
  - sha256: `19343dbc7bb10c16f73b4cf37c9ae1da7d62a43772b9c96f7d50f778e38c9cdc`

## Zero-capital human review (P5-06 / P7-08 / P8-13)
- **SHADOW_ENTRY_REVIEW**: OK
    - sample_status=NATURAL_OPERATIONAL_SAMPLE candidates=74 zero_capital_review_items=2 probe_reviews=1
    - 000660 (KOREA): review_state=MOMENTUM_PROBE_REVIEW participation=PROBE_REVIEW price_state=STRONG_MOMENTUM review_due=REVIEW_DUE_TODAY next_review_at=2026-08-27 reason=PIT_TRIGGER_WITH_LINKED_MOMENTUM_PRICE_STATE capital=0 trade_proposal=null
    - BTC (BTC): review_state=WAIT_FOR_PULLBACK_REVIEW participation=RADAR price_state=OVEREXTENDED review_due=REVIEW_OVERDUE next_review_at=2026-08-26 reason=OVEREXTENDED_PRICE_STATE_REQUIRES_PULLBACK_REVIEW capital=0 trade_proposal=null
    - why_not_executable=CANDIDATE_VALIDITY_POLICY_UNRATIFIED,ENTRY_POLICY_UNRATIFIED,POSITION_MANAGEMENT_POLICY_UNRATIFIED,POSITION_SIZE_POLICY_UNRATIFIED
  - source: `evidence/operational/dynamic_clock/shadow_entry_review.json`
  - sha256: `f633d2c98445cb35b39a2522bd9cd0fb56328cc57cfe847ff15606747e9f287c`

## PENDING / UNKNOWN / DEGRADED / BLOCKED components
KRX_POST_CLOSE, KOFIA_FIRST_SEEN, BTC_TREND, BTC_RISK, STABLECOIN_NET_ISSUANCE, CRYPTO_BREADTH, THREE_MARKET_REGIME_HEADER, ROTATION_DISCOVERY, BUSINESS_ACCELERATION, KOREA_ROTATION, RULE_EVALUATION, PORTFOLIO_BUCKET, PORTFOLIO_CURRENCY, UNIFIED_DECISION, INVESTMENT_DECISION_REVIEW, CASH_EXPOSURE_US, CASH_EXPOSURE_KOREA, CASH_EXPOSURE_CRYPTO, INVERSE_US, INVERSE_KOREA, INVERSE_CRYPTO, LONG_SHORT_INVARIANT, HEDGE_ELIGIBILITY, BEAR_HEDGE_BUDGET, POSITION_SIZING, CONCENTRATION_GUARD, MARKET_THEME_BUDGET, CRYPTO_EXPOSURE_LIMIT, PLANNED_LOSS_BUDGET, ACTION_RISK_PORTFOLIO_SUMMARY, INVESTMENT_REVIEW_SHADOW

## Unresolved boundaries
- REGIME_AXIS_LIVE_ADAPTER_NOT_WIRED
- ROTATION_AND_DISCOVERY_POLICY_UNRATIFIED
- RULE_REGISTRY_NOT_CONSUMABLE
- PORTFOLIO_CONSTITUTION_NOT_RATIFIED
- ACTION_AND_ORDER_NOT_AUTHORIZED
- PRODUCTION_NOT_AUTHORIZED
- SAME_DAY_AUTOMATIC_RECOVERY_TRIGGER_NOT_SCHEDULED

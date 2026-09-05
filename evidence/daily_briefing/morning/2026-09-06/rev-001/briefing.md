# Atlas Daily Briefing — 2026-09-06 (morning)

Generated at: 2026-09-05T22:19:57Z
Component status counts: {'PENDING': 18, 'UNAVAILABLE': 1, 'POLICY_BLOCKED': 11, 'DEGRADED': 0, 'DATA_BLOCKED': 11, 'UNKNOWN': 0, 'READY': 6}

No action, order, Production, or trading authority is granted by this briefing. All such fields remain false/null.

## 3-market session board
### KRX · 한국
- session: FRESH_CLOSE_PENDING; evidence_date=2026-09-03
- latest_completed_close_date: 2026-09-03
- KOSPI/KOSDAQ close values: pending a same-date validated close; older evidence is not relabelled as today.
- verified sector/event summary: pending same-date KRX source evidence.
### US · 미국
- session: INDEPENDENT_SESSION_PENDING; evidence_date=2026-09-03
- latest_verified_us_evidence_date: 2026-09-03
- US close/sector/event summary: pending independently dated validated US session evidence; no KRX-date substitution.
### Crypto · 코인
- session: CONTINUOUS_EVIDENCE_PENDING; evidence_dates=UNKNOWN,UNKNOWN,UNKNOWN
- continuous_observation_date: PENDING
- Crypto topic/sector/event summary: pending complete continuous source evidence.

## Weekend market session context
- market_session: MARKET_CLOSED
- new_session: NONE
- latest_confirmed_evidence_date: 2026-09-04
- latest_confirmed_evidence_relabelled_as_today: false

## 1. Regime
- status: PENDING
- as_of: 2026-09-05
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- unknown_reason: SOURCE_COMPONENT_NOT_READY
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: THREE_MARKET_REGIME_HEADER=PENDING

## 2. Cross-Market Flow
- status: UNKNOWN
- as_of: 2026-09-03
- evidence_grade: UNKNOWN (CROSS_MARKET_EVIDENCE_GRADE_AGGREGATION_UNRATIFIED)
- unknown_reason: CROSS_MARKET_COMPARISON_POLICY_UNRATIFIED
- invalidation: UNKNOWN (CROSS_MARKET_INVALIDATION_POLICY_UNRATIFIED)
- sources: FREE_MARKET_DATA=AVAILABLE, KRX_POST_CLOSE=UNKNOWN, STABLECOIN_NET_ISSUANCE=UNKNOWN
- evidence_class_counts: {'DIRECT_FLOW': 2, 'MARKET_IMPLIED_FLOW': 1, 'MACRO_CONTEXT': 1, 'UNKNOWN': 0}
- evidence_status_counts: {'UNKNOWN': 3, 'AVAILABLE': 1}
- comparison_observation_dates: ['2026-09-03']
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
- as_of: 2026-09-06
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- unknown_reason: SOURCE_COMPONENT_NOT_READY
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: DEFENSIVE_ACTION_DECISION=PENDING, STRATEGIC_CAPITAL_POSTURE=PENDING, ACTION_RISK_PORTFOLIO_SUMMARY=PENDING

## 5. Assets
- status: DATA_BLOCKED
- as_of: 2026-09-04
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- unknown_reason: SOURCE_COMPONENT_NOT_READY
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: SHADOW_ENTRY_REVIEW=DATA_BLOCKED

## 6. Entry / Exit / Size
- status: DATA_BLOCKED
- as_of: 2026-09-04
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- unknown_reason: SOURCE_COMPONENT_NOT_READY
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: SHADOW_ENTRY_REVIEW=DATA_BLOCKED, POSITION_SIZING=POLICY_BLOCKED, PLANNED_LOSS_BUDGET=POLICY_BLOCKED

# Supporting Evidence and System Health

## Data / Read-model health
- **STEP0_READ_MODEL_HEALTH**: DATA_BLOCKED — dart:collector_date_mismatch;krx:collector_date_mismatch;sec:collector_date_mismatch
    - krx: ok=7 failed=0
    - dart: ok=7 failed=0
    - sec: ok=7 failed=0
  - source: `data/briefing_status.json`
- **KRX_PREOPEN_COMPACT**: DATA_BLOCKED — COLLECTOR_DATA_NOT_READY_FOR_DECISION_DATE
    - krx: ok=7 failed=0 date=2026-09-04
    - dart: ok=7 failed=0 date=2026-09-04
    - sec: ok=7 failed=0 date=2026-09-04
  - source: `data/latest_krx.json`
  - sha256: `e031be01b2df6aa09b5c1221f6600c66dd7adae0d7bb69257678768651ee0439`
- **KRX_POST_CLOSE**: PENDING — WEEKEND_MORNING_MARKET_CLOSED_NO_NEW_SESSION_LATEST_CONFIRMED_EVIDENCE

## Filing & source evidence
- **DART_FILING_CONTENT**: DATA_BLOCKED — NO_CONTENT_STATUS_FOR_DECISION_DATE
  - source: `data/latest_dart_content.json`
- **SEC_FILING_CONTENT**: DATA_BLOCKED — NO_CONTENT_STATUS_FOR_DECISION_DATE
  - source: `data/latest_sec_content.json`
- **KOFIA_FIRST_SEEN**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE

## Sensors
- **US_BREADTH_MEMBERSHIP**: OK
    - snapshot_date=2026-09-04 members=13188
  - source: `evidence/us_breadth/raw/2026-09-04`
- **FREE_MARKET_DATA**: OK
    - US close values withheld: independent session evidence is dated 2026-09-03, not 2026-09-06
    - scope: IEX_PARTIAL_EVIDENCE_ONLY_NOT_MARKET_WIDE_OR_TRADE_AUTHORITY
  - source: `data/latest_free_market_data.json`
  - sha256: `6ecb9afe181285a46f594f903ac1ae146114349e8bab1ace781805235a5f60ee`
- **BTC_TREND**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **BTC_RISK**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **STABLECOIN_NET_ISSUANCE**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **CRYPTO_BREADTH**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **CRYPTO_LEADERSHIP**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **KOREA_MARKET_SIGNALS**: OK
    - 한국 종가 수치 보류: 최신 보존 관측일=2026-09-03; 2026-09-06 종가로 재표기하지 않음
  - source: `data/observations/korea_market_signals/2026-09-03/packet.json`
  - sha256: `7bf5c06627b24e12fbdebc28682593111b9317899a84904100752d205b82f56c`

## 3-Market Regime
- **THREE_MARKET_REGIME_HEADER**: PENDING — LIVE_AXIS_EVIDENCE_WIRED_REGIME_SCORING_UNRATIFIED
    - US: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=1/5
    - KR: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=0/5
    - CRYPTO: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=0/5
  - sha256: `07df27a25e67f8b8fdb41825be94346d7a509334bde1e636976eba6f57385c5f`

## Rotation / Theme
- **ROTATION_DISCOVERY**: PENDING — DART_OBSERVATIONS_PRESENT_ESCALATION_BLOCKED
    - rotation_changes=0 discovery_cases=18 new_candidates=0 existing_candidate_changes=0 signal_observations=84 dart_observations=3 ready=0 entry=0
    - DART observations=3 raw_verified=1 metadata_only=2 source_failed=0 content_failed=0 event_type=UNRATIFIED importance=UNRATIFIED promotion=NOT_AUTHORIZED
    - DART 012450 한화에어로스페이스: 단일판매ㆍ공급계약체결 evidence=RAW_BYTES_VERIFIED_ITEM_EXTRACTION_UNRATIFIED action=null
    - DART 034020 두산에너빌리티: 단일판매ㆍ공급계약체결 evidence=METADATA_ONLY_STAGE_NOT_ASSIGNED action=null
    - DART 034020 두산에너빌리티: [기재정정]단일판매ㆍ공급계약체결 evidence=METADATA_ONLY_STAGE_NOT_ASSIGNED action=null
    - signal_markets={'BTC': 1, 'CRYPTO': 79, 'KOREA': 4} tier_diagnostic_only={'IMMEDIATE_REVIEW': 0, 'WATCH_REVIEW': 84, 'OBSERVATION_ONLY': 0} promotion=NOT_AUTHORIZED
    - wildcard_observations=0 cases=0 pending=0 importance=UNRATIFIED promotion=NOT_AUTHORIZED
  - sha256: `19a8379ac7443e096ac8d1f880d20cf8e8aa0e96ed0048d00a64d41528e226da`
- **KOREA_ROTATION**: PENDING — NO_ROTATION_OBSERVATION_FOR_DECISION_DATE
  - source: `data/latest_korea_rotation.json`

## New Discovery / candidate change
- **BUSINESS_ACCELERATION**: PENDING — RADAR_CASE_RECORDED_IMPORTANCE_AND_RANKING_UNRATIFIED
    - scope=TSM_SEC_MONTHLY_REVENUE_ONLY reports=3 series=2 cases=1
    - TSM TSM_CUMULATIVE_REVENUE_YOY_SEC: pattern=TWO_STEP_ACCELERATION_OBSERVED values_pct=['30.000000000000', '35.600000000000', '37.000000000000'] candidate_eligible=False
    - TSM TSM_MONTHLY_REVENUE_YOY_SEC: pattern=LATEST_STEP_NOT_UP values_pct=['30.100000000000', '67.900000000000', '44.700000000000'] candidate_eligible=False
  - sha256: `d69d747c47513f50bca95cbd1016277492f8a1d452169179df8dfb75c381ecdf`
- **OFFICIAL_RELEASE_SUMMARY**: PENDING — OFFICIAL_FACTS_OBSERVED_INTERPRETATION_AND_RANKING_UNRATIFIED
    - subject=SNDK observed_releases=1 summary_items=5 interpretation=UNDETERMINED ranking=UNRATIFIED
    - SNDK: Sandisk Reports Fiscal Fourth Quarter 2026 Financial Results published_at=2026-08-05
      - official_summary_1: Fiscal fourth quarter revenue was $8.97 billion, up 51% sequentially, with GAAP net income reported at $6.90 billion ($43.97 diluted net income per share). Sequential revenue growth came approximately one-third from higher volumes and two-thirds from higher pricing. Fourth quarter Non-GAAP diluted net income per share was $39.25.
      - official_summary_2: Fiscal year 2026 revenue was $20.25 billion, up 175% year-over-year, with GAAP net income reported at $11.43 billion ($73.76 diluted net income per share). Revenue outperformance was driven by both our mix shift toward higher-value customers, with Datacenter up 437%, and higher pricing. Fiscal year 2026 Non-GAAP diluted net income per share was $70.88.
      - official_summary_3: Since announcing five New Business Model (“NBM”) agreements during our April earnings call, we have signed five additional agreements, including three NBMs with new customers and two deals expanding on previously signed NBMs.
      - official_summary_4: Expanded our share repurchase authorization, with Sandisk’s Board of Directors approving an additional $14 billion buyback program, bringing total remaining authorization to $15.5 billion.
      - official_summary_5: Expect first quarter 2027 revenue to be in the range of $10.30 billion to $10.80 billion, with expected Non-GAAP diluted net income per share to be in the range of $44.00 to $46.00.
  - sha256: `ba9547e9784b577a9a220c71bbe40370dcb75ccde55dea2d16de14b03fd3cc81`

## Rule status
- **RULE_EVALUATION**: POLICY_BLOCKED — ZERO_OF_TWENTY_FIVE_RULES_CONSUMABLE_BY_EVALUATOR
    - total_rules=25 PASS=0 FAIL=0 UNKNOWN=22 UNDEFINED=3
  - sha256: `2db86bb38ba3e4004c3199af3efc8c18a53f08c3c53e5d0c2bfdc449ed955dd5`

## Portfolio / Risk
- **PORTFOLIO_BUCKET**: POLICY_BLOCKED — CONSTITUTION_NOT_RATIFIED
- **PORTFOLIO_CURRENCY**: UNAVAILABLE — NO_LIVE_ASSET_MASTER_OR_POSITION_SNAPSHOT
- **CASH_EXPOSURE_US**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `a4050c0e9ab90cdf34ba8d54d31bea56e6e804de31981176e7d8854931ad02d8`
- **CASH_EXPOSURE_KOREA**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `0658ade377f40d5157136aa058a7eff47818f06393579bfc4b0f182d52d0f0e7`
- **CASH_EXPOSURE_CRYPTO**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `0eb1e4386d3fe9e8fc08ed6f8e49b8d4025bcfc11e016ad29cb66b6b2abca69e`
- **INVERSE_US**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `1f943df83959ce22e498482bcd054045acfa8dcf5e1b6259a8a69909318d8ff0`
- **INVERSE_KOREA**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `c9403b17fdc814ee203ca14e559ba42396fff4c4f46acb162f2de8d036b34c64`
- **INVERSE_CRYPTO**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `797c7ca40d5e920dc43a5fc40787daf4c23ef4e451b8605c6bfa7f4e11dac6a3`
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
- **P2_FLOW_ENGINE**: PENDING — FLOW_REFERENCE_IS_DIAGNOSTIC_NOT_A_DEFENSIVE_ACTION_DECISION
  - sha256: `60bd51ecddadf9e9b29ac2cac4c720c2805acc67dc0c389a08b37df8518b7d1e`
- **STRATEGIC_CAPITAL_POSTURE**: PENDING — 2/9_SOURCES_AVAILABLE_POLICY_NOT_RATIFIED
    - decision_status=BLOCKED available_sources=2/9 market_budget={'CRYPTO': None, 'KOREA': None, 'US': None}
    - cash_reserve=None hedge_budget=None max_gross=None max_net=None theme_headroom=None
  - sha256: `c493cbf775b800471eb75f4fdd73e5f38801a228198fb9a4e61411dae82c8624`

## Decision Review
- **INVESTMENT_DECISION_REVIEW**: POLICY_BLOCKED — P5_OR_THESIS_AUTHORITY_NOT_AVAILABLE
    - subject=TSM review=BLOCKED trade_proposal=None money_action=NONE
    - blocker=EXTERNALLY_RATIFIED_TSM_RULE_PACKET_NOT_AVAILABLE
    - blocker=P5_DOWNSTREAM_ACTION_NOT_AUTHORIZED
    - blocker=P5_PASS_FAIL_NOT_AUTHORIZED
    - blocker=TSM_THESIS_PACKET_NOT_AVAILABLE
  - sha256: `18c68fc10b4850e45d26e3793916539974b616c36360423c447ed9b614987aa2`

## Decision & action boundary
- **ACTION_BOUNDARY**: OK — DYNAMIC_CLOCK_SIGNAL_OBSERVATIONS_BOUND_READY_NOT_EVALUATED_NO_ACTION_AUTHORITY
  - sha256: `aed17fc9a8f4bc90da761e304e53dbea3383334a35576c11830bd437a5eb260f`
- **UNIFIED_DECISION**: PENDING — 4/6_COMPONENTS_AVAILABLE
    - state=NO_ACTION_AUTHORIZED action=None order_intent=None available_components=4/6
  - sha256: `6c34b3d159b12d9d8d79ceaafed27c9013b8126bc59d2356baae9f408ee1b453`
- **DEFENSIVE_ACTION_DECISION**: PENDING — 8/12_SOURCES_AVAILABLE_POLICY_NOT_RATIFIED
    - decision_status=BLOCKED available_sources=8/12 evaluated_decisions=0 no_action=None
    - selected_action=None action_proposal=None orders=0
  - sha256: `b4dcbb711153ef39f6b434bd124a18983a7d0183192edb26beaa7504add820dd`
- **ACTION_RISK_PORTFOLIO_SUMMARY**: PENDING — MOST_UPSTREAM_SOURCES_NOT_YET_LIVE
    - available_sources=10/17 evaluated_actions=0 risk_breach_sources=0
  - sha256: `ddb16aeef64b76e94bd8069f143513907be5e5599fe218d9f87995ed50c310c6`

## Shadow learning record
- **INVESTMENT_REVIEW_SHADOW**: POLICY_BLOCKED — NO_RATIFIED_PASS_REVIEW_TO_RECORD
    - ledger_record_created=False capital={'authorized': False, 'amount': 0} action=None order=None stage_change=None
  - sha256: `eac35a9d24a57144b563fe2ea8c9e040d61e17e417edcb0842f961e27927798a`

## Forward Alpha Review (Pilot)
- **FORWARD_ALPHA_REVIEW**: OK
    - pilot_subjects=['034020.KS', '267260.KS', '298040.KS', 'TSM']
    - 034020.KS: opportunity_state=BLOCKED shadow_action=REJECT comparison_label=BLOCKED
    - 267260.KS: opportunity_state=REJECTED shadow_action=REJECT comparison_label=REJECT
    - 298040.KS: opportunity_state=WAIT_FOR_PRICE shadow_action=WAIT comparison_label=WAIT
    - TSM: opportunity_state=WAIT_FOR_PRICE shadow_action=WAIT comparison_label=WAIT
  - sha256: `541b9bbc08d454935d470995502e0bccbbb31a7612804297ba33f32f772bfb39`

## Dynamic Clock (Opportunity Trigger / Review Queue)
- **DYNAMIC_CLOCK**: OK
    - policy_approval_status=PROVISIONAL_CIO_MVP
    - BTC: raw_triggers(audit only)=1 immediate_review=0 watch_review=1 observation_only=0 expired=3 calendar_confidence=VERIFIED_24_7 not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FLOW_REVERSAL', 'FUNDAMENTAL_REVISION', 'RELATIVE_STRENGTH_REVERSAL']
      - WATCH_REVIEW BTC trigger_types=['PRICE_CONFIRMATION'] price_state=OVEREXTENDED reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-09-05T00:39:00Z next_review_at=2026-09-05 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
    - CRYPTO: raw_triggers(audit only)=105 immediate_review=0 watch_review=79 observation_only=0 expired=236 calendar_confidence=VERIFIED_24_7 not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FLOW_REVERSAL', 'FUNDAMENTAL_REVISION']
      - WATCH_REVIEW AAVE/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-06 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW ACU/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-06 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW ADA/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-06 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW AERO/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-06 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW AKE/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-06 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW ALGO/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-06 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW APR/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-05 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW APT/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-06 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW ARB/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-05 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW ASTER/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-06 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW BABYSHARK/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-05 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BILL/USD trigger_types=['INVALIDATION_TRIGGER'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-06 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BNB/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-05 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW BONK/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-06 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW CAP/USD trigger_types=['INVALIDATION_TRIGGER'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-06 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - ... +64 more WATCH_REVIEW candidates (full list: evidence/operational/dynamic_clock/briefing_section.json)
    - KOREA: raw_triggers(audit only)=4 immediate_review=0 watch_review=4 observation_only=0 expired=15 calendar_confidence=UNVERIFIED_NO_HOLIDAY_CALENDAR not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FUNDAMENTAL_REVISION']
      - WATCH_REVIEW 005930 trigger_types=['FLOW_REVERSAL'] price_state=WEAK reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-09-03T20:59:56Z next_review_at=2026-09-07 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW 012450 trigger_types=['FLOW_REVERSAL'] price_state=MODERATE reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-09-03T20:59:56Z next_review_at=2026-09-04 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW 267260 trigger_types=['INVALIDATION_TRIGGER'] price_state=WEAK reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-09-03T20:59:56Z next_review_at=2026-09-07 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW 329180 trigger_types=['INVALIDATION_TRIGGER'] price_state=WEAK reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-09-03T20:59:56Z next_review_at=2026-09-04 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
  - sha256: `420eece8d032c8cc40450d0fff103215b8705fa8e18fbf145256dcefef70d90b`

## Zero-capital human review (P5-06 / P7-08 / P8-13)
- **SHADOW_ENTRY_REVIEW**: DATA_BLOCKED — SHADOW_ENTRY_REVIEW_DECISION_DATE_MISMATCH
  - source: `evidence/operational/dynamic_clock/shadow_entry_review.json`
  - sha256: `665e9fdf0ef54101a70a8f20fba3e0229b3aced288df1a81ffe4bc2457a88ddf`

## PENDING / UNKNOWN / DEGRADED / BLOCKED components
STEP0_READ_MODEL_HEALTH, KRX_PREOPEN_COMPACT, KRX_POST_CLOSE, DART_FILING_CONTENT, SEC_FILING_CONTENT, KOFIA_FIRST_SEEN, BTC_TREND, BTC_RISK, STABLECOIN_NET_ISSUANCE, CRYPTO_BREADTH, CRYPTO_LEADERSHIP, THREE_MARKET_REGIME_HEADER, ROTATION_DISCOVERY, BUSINESS_ACCELERATION, OFFICIAL_RELEASE_SUMMARY, KOREA_ROTATION, RULE_EVALUATION, PORTFOLIO_BUCKET, PORTFOLIO_CURRENCY, UNIFIED_DECISION, INVESTMENT_DECISION_REVIEW, CASH_EXPOSURE_US, CASH_EXPOSURE_KOREA, CASH_EXPOSURE_CRYPTO, INVERSE_US, INVERSE_KOREA, INVERSE_CRYPTO, LONG_SHORT_INVARIANT, HEDGE_ELIGIBILITY, BEAR_HEDGE_BUDGET, POSITION_SIZING, CONCENTRATION_GUARD, MARKET_THEME_BUDGET, CRYPTO_EXPOSURE_LIMIT, PLANNED_LOSS_BUDGET, P2_FLOW_ENGINE, DEFENSIVE_ACTION_DECISION, STRATEGIC_CAPITAL_POSTURE, ACTION_RISK_PORTFOLIO_SUMMARY, INVESTMENT_REVIEW_SHADOW, SHADOW_ENTRY_REVIEW

## Unresolved boundaries
- REGIME_POLICY_VALUES_UNRATIFIED
- ROTATION_AND_DISCOVERY_POLICY_UNRATIFIED
- RULE_REGISTRY_NOT_CONSUMABLE
- PORTFOLIO_CONSTITUTION_NOT_RATIFIED
- ACTION_AND_ORDER_NOT_AUTHORIZED
- PRODUCTION_NOT_AUTHORIZED
- SAME_DAY_AUTOMATIC_RECOVERY_TRIGGER_NOT_SCHEDULED

# KR first PAPER cycle: evidence-to-decision mechanism

Base: PR614 merge `98537aaf64b3bdcd84d157f9b13841094df2811f`.
Owner: dedicated Codex public Regime lane. Root owns integration and CI
registration. The existing Claude private lane owns multiple-candidate natural
ingress; the existing Ubuntu owner owns real input qualification.

## Implemented slice

`paper_regime_reference.normalize_kr_measurements(packet, policy)` extracts the
existing KR signed-axis arithmetic unchanged. `build_kr` calls it and retains
its existing PAPER reference output. No threshold/config/registry bytes change.

`kr_paper_runtime.evaluate_kr_paper_runtime` consumes an ordered list of exact
KR five-axis observation bytes, an explicit PAPER experiment policy, an
owner-admitted qualification receipt and **externally trusted expected hashes**
for the policy and receipt. It validates the existing KRX source contract,
original response-hash lineage, session dates, closed-session time, availability,
policy effective window and explicit freshness before calling the existing
common-v1 aggregation implementation. That implementation supplies weights,
bands, direction, confidence, two-finalized-packet confirmation and immediate
STRESS entry; no second classifier is introduced.

The same source chain is rebuilt on each invocation. A later packet must name
the prior packet's session as its previous session. Reordering, truncation
against the pinned receipt, duplication, future data and stale data fail
closed. There is no opaque previous-state object that can be forged into a
confirmed regime. The qualification owner must pin the complete intended chain
and its calendar; a caller-selected hash is not an admission decision.

`kr_paper_runtime_decision/1` binds the source hashes, exact source chain,
policy hash, qualification hash, supplied build revision and actual Python
implementation hashes into `decision_id`. `validate_kr_paper_runtime` rebuilds
the full result from the separately supplied inputs and compares canonical
bytes, rejecting rehashed edits and boolean/numeric aliases.

| Evidence class | Calculation succeeds | Natural PAPER consumer |
| --- | --- | --- |
| `SYNTHETIC_OFFLINE_FIXTURE` | `PAPER_SIMULATION_CLASSIFIED` | Rejected; runtime UNKNOWN |
| `HISTORICAL_REPLAY` | `PAPER_SIMULATION_CLASSIFIED` | Rejected; runtime UNKNOWN |
| `LIVE_NATURAL` with externally admitted policy/source bytes | `PAPER_RUNTIME_CLASSIFIED` | Available only after common confirmation |
| Missing/invalid/stale input or pending confirmation | BLOCKED / UNKNOWN | Rejected |

The wrapped common-v1 report retains its original replay-only provenance and
closed authority. This module explicitly applies that *calculation* under the
supplied PAPER experiment scope; it does not relabel the old report as a
ratified operational runtime packet. Legacy `regime_output/v1`, readiness,
registry gates and REAL/order authority remain closed. No default experiment
policy, freshness number or operating approval is installed by this change.

## Explicit inputs and trust boundary

`experiment_policy` is exact JSON bytes with these fields:

- `schema_version: kr_paper_runtime_policy/1`, `market: KR`, `evidence_class`.
- `policy_id`, `effective_from`, `effective_until` (exclusive upper bound).
- `reference_policy_sha256`: existing policy file bytes, not rewritten numbers.
- `common_policy_binding_sha256`: canonical existing common policy binding.
- `source_contract_sha256`: existing KRX observation contract bytes.
- `leadership_policy_sha256`: existing korea_leadership/v1 policy bytes. Its
  exact active sector coverage and earliest usable time are checked through
  the existing krx_market_judgement validators. Its FORWARD_SHADOW authority
  does not independently ratify this PAPER experiment.
- `ttl_seconds`: mandatory positive integer; elapsed time since session close,
  checked at each historical decision and at the current evaluation. Collection
  of an old session cannot reset its age. This mechanism has no default TTL.
- `acceptance_refs` with explicit `normalization`, `freshness`, `pit`, and
  `common_runtime` decision references. These references document the external
  decision; nonempty strings alone do **not** authenticate or ratify it.

The existing policy/source owners must supply `expected_policy_sha256` and
`expected_qualification_sha256` from their approved admission path. Do not
compute those expected hashes from an untrusted submitted document in a natural
entry point. The local pure function is a dependency-injection seam, not an
authorization service. A test can exercise the natural branch with mock
admissions; that is still synthetic test evidence.

`qualification_receipt` uses `kr_paper_runtime_qualification/1`, market and
matching evidence class/policy hash, `calendar_receipt_sha256`, and the complete
ordered `sources` list. Each row binds `source_sha256`, `source_ref`,
`owner_receipt_sha256`, `as_of_date`, `available_at`, `session_close_at`,
`decision_at`. Its owner must verify the source and calendar qualification
before admitting that exact receipt hash. This module checks the supplied
aggregate bytes and owning structural validator; it cannot authenticate raw
KRX/provider bytes. Output explicitly declares
`raw_provider_bytes_authenticated: false`. Never promote an arbitrary rehashed
aggregate or reception-minute count into an owner-qualified source.

The source shape is `korea_market_signals_observation/1`, including both
KOSPI/KOSDAQ benchmark measurements under the existing KR normalization rule.
The first cycle's KOSPI comparison benchmark does not authorize discarding the
KOSDAQ input or replacing this rule with a KOSPI-only rule. Stock-level
15m/1h/1d eligibility is a separate downstream candidate input obligation.

## Existing consumer connection

`reduce_funnel_with_kr_regime(funnel_input, runtime_packet, **runtime_inputs)`
independently rederives the runtime packet, requires natural availability and
exact evaluation-time equality, validates the existing funnel input, and adds
the same `decision_id` to each KOREA candidate's `sourceRefs` before invoking
`common_paper_candidate_funnel.reduce_funnel`. Market identifiers are explicitly
mapped: runtime `KR`, common funnel `KOREA`.

This is the existing reducer's provenance connection. It does **not** calculate
market scores, substitute rotation validity, choose a regime-to-entry policy,
or repair existing hard gates. Those remain market-owned inputs; the private
adapter must bind its actual Regime/rotation eligibility to the existing score
and hard-gate inputs. Attaching provenance alone is not proof of that investment
policy connection. No private ingress, core, writer, briefing or run_all file is
modified here. The current private core/writer are reused, not reimplemented.

Consumer schemas remain:
`common_paper_candidate_funnel_output/1` → `krx_investment_paper_cycle/1` →
`krx_paper_natural_ops_input/1` → `krx_internal_paper_writer_request/1`.
Root/Claude must retain the same sourceRefs and decision identity through
selection, quantity, entry, exit and cost evaluation. Briefing should consume
the same validated decision, not recalculate an independent regime.

## Outstanding operational inputs, not code-wide prerequisites

1. An existing decision adopting the candidate KR normalization values for this
   bounded PAPER experiment, with exact hash/version/effect and scope. PR614
   itself did not make that decision.
2. Existing applicable freshness and PIT/source-acceptance decisions, including
   compatibility with the elapsed-session-close TTL mechanism and common-v1
   runtime use. If the adopted rule instead uses session counts, do not convert
   it to seconds silently: extend the technical adapter for that exact rule.
3. The input owner's admitted real five-axis source/calendar chain. The separate
   four pending stock eligibility/calendar/normalization/daily receipts must
   also reach the private candidate path; they are not fabricated here.
4. Root/Claude integration of the Regime/rotation eligibility and identity with
   their current private adapter, and the existing briefing owner's consumption.
5. Real temporal/reproduction/forward evidence. Fixture PASS, replay, WAIT or
   NOOP is not a completed entry/exit cycle or official PAPER_VALIDATED status.

No new policy numbers are requested by this implementation. First compare
these inputs against existing adopted decisions, then present only genuine
unresolved choices to CIO. Synthetic tests explicitly use test-only timing and
mock receipts; no real data downloads, private keys, orders or ledger writes
occur in this module or its focused tests.

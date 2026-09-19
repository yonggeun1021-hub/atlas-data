# KR PAPER runtime daily publication (KR_PAPER_RUNTIME_ADOPTION_V1)

KC3 of the PAPER execution v1 build plan. Applies CIO decision
`CIO-KR-RUNTIME-DAILY-ADOPTION-20260914` mechanically. Display-only: every
strategy, capital, order, trading and REAL flag stays false.

## Flow (one session D)

1. **Pair**: `resolve-pair` picks the last two completed OPEN_REGULAR sessions
   (P, D) from the committed calendar packets at the run instant (15:30 KST
   close rule, same as the capture producer). Nothing runs before D 18:00 KST
   (the bridge's display floor).
2. **Capture** (workflow `kr-paper-runtime-daily-publish.yml`, capture mode):
   the unchanged `.github/scripts/korea_market_signals_pykrx_candidate.py`
   with at most 3 attempts, retried only on transient provider codes
   (`RESPONSE_ROW_SCHEMA_INVALID`, `RESPONSE_CONTENT_TYPE_INVALID`,
   `SOURCE_SESSION_INITIALIZATION_FAILED`, pykrx frame errors). Artifact mode
   instead admits an existing successful `korea-market-signals.yml` artifact.
   `korea-market-signals.yml` itself is not modified: its sha256 is pinned by
   `config/regime_source_owner_registry_v2.json` (pin K2).
3. **Admission**: the unchanged bridge `validate_natural_evidence` checks the
   raw provider bytes; the pair must be adjacent open sessions; provenance must
   name the scheduled producer with `manual_edits: false`.
4. **History**: the 28-session window ending on P is rederived from the
   accepted root (`2026-09-11/history`, through 09-10) and the committed chain
   of validated observations (`<day>/validation.json` + aggregate reference),
   using `regime/kr_paper_runtime_history_extension.py`. A missing session is a
   permanent `HISTORY_CHAIN_GAP:<day>` (the capture can only observe the last
   completed pair, so a missed day cannot be backfilled; recovery needs a new
   accepted historical range via `kr-contiguous-historical-range.yml` and a new
   adoption root). The rolling window is gated by
   `rolling_history_extension.status == CIO_CONFIRMED` in the adoption record.
5. **Qualification**: `kr_information_system_runtime_qualification/1` derived
   from the adoption record's pinned implementation/policy/contract hashes and
   the bundle hashes (no manual edit). Pinned-byte drift fails closed with
   `ADOPTION_PIN_DRIFT_REQUALIFICATION_REQUIRED`.
6. **Decision**: unchanged `evaluate_kr_paper_runtime` →
   `kr_paper_runtime_decision/5` for execution session E = next OPEN_REGULAR
   after D. Expires at E 15:30 KST (bridge `LATEST_SOURCE_STALE`).
7. **Commit** (bot): `evidence/regime/kr_information_system/<D>/` —
   reference, `source-manifest.json` (hashes only; deliberately not under
   `source-capture/`, which raw-row consumers glob), provenance, validation,
   qualification, history window, decision, publication — and
   `data/latest_kr_paper_runtime_decision.json` only when the decision is
   displayable and newer than the current pointer. Raw provider rows are never
   committed (KRX terms); they are hash-bound in the manifest and validation.

If admission passes but the decision cannot be produced (rolling not
confirmed, chain gap, stale), the observation is still recorded so the chain
continues; the pointer is untouched and consumers see UNKNOWN/expired.

## Dual-source agreement with frozen baseline v0 (receiver #214)

Baseline v0 (private, K12-frozen) derives its market half from the legacy
KRX Open API five-signal runtime (`kr_paper_runtime_decision/1` or the
session-boundary `/4` mode with its own policy/qualification anchors). The
ratified display regime is the `/5` Information Data System decision published
here. The two share the KR normalization and common-v1 aggregation but not the
provider, the history chain or the qualification, so they are separate
sources. Baseline v0 cannot be re-pointed at `/5` without breaking K12.

The path A receiver therefore keeps an agreement check, and this publisher
supplies its `/5` side:

- Same session: `/5 session_boundary_freshness.execution_session_date` equals
  the canary session date (KST) and evaluation is before
  `execution_session_close_at`.
- Regime: `/5 runtime_regime` must equal baseline `marketScore.regime`;
  otherwise `REGIME_SOURCES_DISAGREE` and no canary entry. A baseline market
  half that is not eligible (`BASELINE_MARKET_HALF_INELIGIBLE`) also blocks.
- Consequence: a canary entry needs both sources available and agreeing. v1
  policy (not frozen) should consume `/5` only.

## Operational notes

- **2027 calendar**: packets exist through 2026-12-31. D = 2026-12-30 needs an
  execution session in 2027; without a committed 2027 official capture and
  packets the observation is recorded with `CALENDAR_PACKET_MISSING` and the
  runtime stays UNKNOWN. Commit the 2027 capture/packets before 2026-12-29.
- **Failed capture runs** retain no raw rows anywhere (not committed, not
  uploaded: public-repo artifacts are readable by any reader and KRX
  redistribution rights are not established). Only response hashes, manifest
  identity and the failure reason go to the job summary. Re-capture must
  succeed before the next session close (15:30 KST), after which the pair
  moves on and the session is permanently lost (`HISTORY_CHAIN_GAP`).
- **Rotation ledger proof pin**: `.github/scripts/korea_capital_rotation_ledger_proof.py`
  `REVIEWED_PAPER_RUNTIME_RELEASE` pins the #696 pointer bytes (sha
  `a5f76eb6…`, commit `b08c5db2`). Its PAPER consumption mode fails closed
  (`PAPER_RUNTIME_REVIEWED_PUBLICATION_BINDING_MISMATCH`) for any source commit
  after the first daily publication advances the pointer. Kept intentional:
  each consumed display release needs an independently reviewed pin update
  (`publication_commit`, `publication_merged_at`, `sha256`, `code_revision`,
  `evaluation_at`, `qualification_sha256`, taken from the dated
  `evidence/regime/kr_information_system/<D>/decision.json` and its bot
  commit). The current-ratified rotation mode used by
  `p2-03-korea-observation-pair.yml` does not read the pointer.

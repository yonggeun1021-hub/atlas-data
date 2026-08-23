#!/usr/bin/env python3
"""P8-10 Price Reflection builder — price/volume-only, never fundamentals.

★★★ CIO FINAL INTEGRATION RULING (PR #212) — SCOPE REDUCTION, effective now:

  Integrated review (after round 9's two local fixes were confirmed correct)
  found a further, deeper PIT defect in the Event Evidence Authority engine
  built across rounds 5-9: the ratification-authority lookup parsed
  `ratified_at` but never compared it to `decision_at`, so a rule ratified
  in the FUTURE relative to a historical decision could still be applied
  retrospectively to that decision -- and the "evidence" backing a
  ratification record was only ever hash-checked against an arbitrary repo
  file, never validated as a genuine, structured Rule Authority record. This
  is the SAME class of provenance failure rounds 5-9 kept finding and
  fixing at the EVIDENCE layer, now recurring one layer up, at the POLICY/
  RATIFICATION layer.

  The CIO explicitly declined a round-10 local patch. Per the agreed stop
  rule, an implementation that has needed 9 successive integrity-defect
  rounds is over-scoped for a single PR. PR #212 has been REDUCED to the
  proven P8-10 MVP boundary:

  KEPT: real historical price-series linkage, PIT-safe price endpoints;
  `price_state` structurally separate from `reflection_status`;
  `PRICE_DATA_MISSING`/`PRICE_STALE`/`REFLECTION_UNCERTAIN_WITH_VALID_
  PRICE` states; `PROVISIONAL` threshold-basis exposure; Korea
  market-membership fail-closed behavior; honest BTC/Korea/TSM/Doosan
  outputs; `decision/alpha_review.py`'s fail-closed `WAIT`-style behavior
  and `authority=false` posture.

  REMOVED from this module and this PR entirely: `decision/event_
  evidence.py` (the whole Event Evidence Authority engine -- provenance
  verification, direction-rule implementation tables, the ratification-
  authority registry -- all of it, not patched, DELETED), and this
  module's OWN `event_reaction`/`reflection_reference` citation-input
  parameters and every internal function that only existed to verify or
  classify them (`_validate_event_reaction`, `_validate_reflection_
  reference`, `_has_reference_point`, `_resolve_reflection_basis`,
  `_compute_verified_return`, `_reflection_status`). There is no longer
  ANY code path in this module -- not merely an empty table, an actual
  ABSENT function -- that could ever compute a `reflection_status` other
  than the hardcoded literal `"UNKNOWN"`. `price_state` (the pure,
  price/volume-only momentum read) is completely UNCHANGED and remains
  fully real and informative; only the reflection-VERDICT machinery is
  gone. The historical `★ CIO review round 2` through `round 9` sections
  immediately below are kept as an audit trail of what was built and why
  it was ultimately removed -- none of the machinery they describe exists
  in this file or this repo any more.

  Deferred, NOT abandoned: a future, SEPARATE, dependent PR must design a
  Reflection Evidence Authority together with Atlas P5 Rule Authority --
  append-only per-rule canonical records, `ratified_at`/`effective_from`,
  exact-content provenance, explicit decision-time ordering checks, and a
  structured authority-evidence schema -- and get that DESIGN approved
  BEFORE any implementation is written, not merely before merge. Tracked
  on the existing P8-10 WBS row, not a new/duplicate one.

★ CIO review round 2 (`price_reflection/2`) fixed a real defect in round 1:
  a price rally is PRICE MOMENTUM, not evidence that the market has
  "reflected" anything. Reflection is a claim about a specific expectation
  or event — you cannot judge whether price has caught up to something
  without knowing what that something is. Round 1 conflated the two into one
  `status` field and let momentum alone (>=8% => "FULLY_REFLECTED") stand in
  for a reflection judgment with no event/expectation reference at all. This
  module now keeps the two claims structurally separate:

  * `price_state`      — OVEREXTENDED | STRONG_MOMENTUM | MODERATE | WEAK |
    UNKNOWN. A pure, price/volume-only read on momentum and positioning.
    Momentum alone can never produce a reflection verdict — that's the whole
    point of this field existing.
  * `reflection_status` — UNDER_REFLECTED | PARTIALLY_REFLECTED |
    FULLY_REFLECTED | UNKNOWN. Only ever leaves UNKNOWN when a real
    REFERENCE POINT is present (see `_has_reference_point` below: an
    `event_reaction.event_date`, a `reflection_reference.reference_event_id`,
    a `reflection_reference.expectation_as_of`, or a real, caller-supplied
    P8-09 Expectations Gap status via `reflection_reference.
    expectations_gap_status`) AND a comparable direction + momentum exist.
    Abundant, fresh, valid price data with NO reference point still forces
    `reflection_status=UNKNOWN` / `data_state=
    REFLECTION_UNCERTAIN_WITH_VALID_PRICE` — momentum is never a substitute
    for a reference.
  * `data_state`        — PRICE_DATA_MISSING | PRICE_STALE |
    REFLECTION_UNCERTAIN_WITH_VALID_PRICE | VALID. Tracks the REFLECTION
    judgment specifically (mirrors `reflection_status`): `VALID` iff
    `reflection_status != "UNKNOWN"`. This is now a real, structured
    top-level field (not string-parsed out of `reasons` — round 1's
    `reasons[0]=="DATA_STATE:..."` encoding was an accepted stopgap to avoid
    touching `decision/alpha_review.py`'s own strict field-set check; round 2
    updates that module directly instead, see its own docstring).

★ CIO review round 3 (`price_reflection/3`) fixed four further defects
  round 2 left open:

  1. A bare `reflection_reference.expectations_gap_status` STRING (or a bare
     `event_reaction.direction` with no citation) is not a real reference --
     a caller can type `"POSITIVE"` without any actual P8-09 evidence behind
     it. `reflection_reference.expectations_gap_status` is retired; callers
     now pass `reflection_reference.expectations_gap_packet` (the FULL,
     already-built P8-09 packet), which this module independently
     re-validates via `decision/expectations_gap.py`'s own `validate_packet`
     (hash/tamper/vocab) and cross-checks `subject`/`decision_date` against
     this packet's own -- see `_validate_reflection_reference`. Likewise
     `event_reaction.direction` now requires `event_reaction.source_ref` +
     `event_reaction.source_sha256` (real evidence-lineage citation) before
     it counts as usable for a reflection verdict.
  2. `reflection_status` used to compare a generic, "now"-anchored 1-month
     return against an event that could be dated anywhere up to
     `decision_date` -- almost entirely PRE-event movement, yet still fed
     into a reflection judgment. It now requires a real,
     event/reference-anchored `event_reaction.post_event_return_pct` /
     `reflection_reference.post_reference_return_pct` (a return the CALLER
     computed specifically from the reference date forward) -- never the
     generic `recent_return_windows`/`relative_strength` figures. Without
     one, `reflection_status` stays `UNKNOWN`.
  3. `price_state=UNKNOWN` and a non-`UNKNOWN` `reflection_status` can now
     never coexist -- enforced as a hard structural invariant in both
     `_classify` (forces `reflection_status` back to `UNKNOWN` if
     `price_state` came out `UNKNOWN`) and `validate_packet` (raises
     `OUTPUT_PRICE_STATE_UNKNOWN_REFLECTION_STATUS_CONTRADICTION` on any
     packet, however constructed, that violates it).
  4. `classification_thresholds_approval_status="PROVISIONAL"` now actually
     gates `decision/alpha_review.py`'s operational output, not just this
     module's own diagnostic `threshold_basis` field -- see that module's
     own docstring for the `alpha_review/4` change.

★ CIO review round 4 (`price_reflection/4`) found round 3's "evidence
  verification" was still only a FORMAT check -- `source_ref`/`source_sha256`
  were regex-validated but never cross-checked against a real committed
  file, and `post_event_return_pct`/`post_reference_return_pct` were still
  trusted caller-supplied numbers with no real price lookup or PIT check
  behind them. Confirmed reproducible: `source_ref="MADE-UP"`,
  `source_sha256="a"*64`, `post_event_return_pct="99"` (all fabricated, no
  real evidence anywhere) produced a confident `FULLY_REFLECTED`. Closed:

  1. `_verify_evidence_citation` resolves `event_reaction.source_ref` to a
     real file under this repo and independently recomputes its sha256 --
     `source_ref="MADE-UP"` (or any non-existent path, or a real path with a
     wrong hash) now fails verification and the event path cannot unlock a
     reflection verdict (soft-downgrades to `UNKNOWN`, same fail-closed
     posture as a missing citation).
  2. `post_event_return_pct`/`post_reference_return_pct` are RETIRED as
     accepted input. There is no code path anywhere in this module that
     accepts a return percentage from a caller and uses it. The return is
     always computed internally by `_compute_verified_return`, from two
     real, independently looked-up close prices (`decision/price_evidence.
     py`'s `real_close_on_date`/`latest_real_close_at_or_before`, themselves
     built on `replay/price_series.py`/`replay/evidence_index.py`, PR #210 --
     reused, not reimplemented).
  3. Both endpoint prices must be PIT-live-known as of `decision_date`
     (`PriceSeries.live_known_asof`/`live_trading_dates_at_or_before`,
     unchanged PR #210 discipline) -- an evidence row captured after
     `decision_date` can never be used, matching PR #210's/#211's own
     anti-lookahead gate.
  4. The return's START price is always anchored to a real reference
     timestamp -- `event_reaction.event_date` for the event path, or the
     validated P8-09 packet's own `decision_date` for the expectations_gap
     path (echoed as `reflection_reference.expectations_gap_reference_date`)
     -- never an independently caller-chosen window. The END price is
     always the latest real, PIT-live close at or before this packet's own
     `decision_date`.
  5. Any failure at any step (file doesn't exist, hash mismatch, no real
     price evidence for the subject, price row not yet PIT-eligible, no
     genuine forward date gap between start and end) makes the return
     `None` -- there is no fallback to a caller-supplied number, ever;
     `reflection_status` simply stays `UNKNOWN`.

★ CIO review round 5 (`price_reflection/5`) found round 4's evidence
  verification proved a hash-matching FILE existed, never that it was
  actually evidence OF the claimed event/direction. Confirmed reproducible:
  `data/2026-08-20/krx.json` (a plain KRX price snapshot, zero event
  semantics) was cited as "evidence" of a POSITIVE event on `329180.KS` and
  the hash-only check accepted it -- any tracked file, of any kind, could
  authorize an arbitrary claimed direction as long as its real hash was
  supplied. Closed via `decision/event_evidence.py` (see that module's own
  docstring for full detail):

  1. `event_reaction.source_ref` must now resolve to a real committed file
     whose PARSED CONTENT is itself a structured, closed-vocabulary Event
     Evidence Envelope (`event_evidence_envelope/1`) independently
     asserting the SAME `subject`/`event_at`/`direction`/`source_class` the
     caller claims -- a generic price/config/any-other file can never
     satisfy this, since it has no such fields at all.
  2. The envelope's own `captured_at` must be at-or-before the decision
     instant being evaluated -- a file merely existing in today's checkout
     is not proof it was available at some earlier historical
     `decision_date`; a future-committed envelope fails closed (reusing
     `replay.lookahead_gate.assert_no_signal_lookahead`).
  3. `reflection_reference` no longer accepts a caller-supplied, possibly
     freshly-fabricated-in-memory P8-09 packet dict at all --
     `expectations_gap_packet_ref`/`expectations_gap_packet_sha256` point
     at a REAL COMMITTED wrapper record this module reads and validates
     from disk itself, whose own `captured_at` (independent of anything the
     embedded packet self-reports) must also be at-or-before the decision
     instant. A packet built fresh at runtime with a backdated
     `decision_date` can never satisfy this, because it was never committed
     at all, let alone before that date.
  4. `event_reaction.event_at` is now a full UTC timestamp (not just a
     date), so pre-market/intraday/after-hours events are at least
     distinguishable in principle -- see `decision/event_evidence.py`'s
     `select_pre_event_reference_date` for the daily-granularity-only
     policy this repo's real price evidence can actually support, and why
     a bare midnight-UTC `event_at` keeps timing `NOT_COMPUTABLE`
     (`reflection_status` stays `UNKNOWN`) rather than claiming precision
     this repo's data cannot back.
  5. A SUPPLIED citation (event or reflection-reference) that turns out to
     be unresolvable, hash-mismatched, not a valid envelope, semantically
     mismatched, or not-yet-PIT-available now RAISES `PriceReflectionError`
     -- it no longer silently downgrades to `UNKNOWN`. Only a citation the
     caller never supplied at all is genuine absence (still a soft
     `UNKNOWN`); a citation that was supplied and turns out corrupt is
     surfaced loudly, distinguishable from genuine no-evidence.
  6. None of this unlocks anything for any currently real subject: no
     committed Event Evidence Envelope or P8-09 canonical record exists for
     BTC, any Korea ticker, TSM, or 034020.KS in this repo, and
     `decision/pilot_evidence_intake.py` never supplies `event_reaction`/
     `reflection_reference` for any of them -- every real subject's
     `reflection_status` remains honestly `UNKNOWN`.

★ CIO review round 6 (`price_reflection/6`) found round 5's Event Evidence
  Envelope was still not a real production/test boundary, and its
  `captured_at` was still just a self-declared field the verifier trusted
  outright. Confirmed: `ALLOWED_CAPTURE_KIND` included `REGRESSION_FIXTURE`,
  so the committed `test/fixtures/event_evidence/*.json` files could drive
  a real `build_packet()` call to a non-`UNKNOWN` verdict -- "it's not a
  current Pilot ticker" was never a real authority boundary (`329180.KS` is
  a real listed subject). All fixed in `decision/event_evidence.py` (see
  that module's own docstring for full detail):

  1. `ALLOWED_CAPTURE_KIND` is now `("LIVE_OFFICIAL_CAPTURE",)` only --
     `REGRESSION_FIXTURE` is not a legal envelope value any more.
  2. Independently, the two functions this module's real, operational
     `build_packet()` path calls (`verify_event_reaction_claim`,
     `verify_expectations_gap_canonical_record`) hard-refuse to resolve any
     `source_ref`/`packet_ref` located under this repo's `test/` directory
     at all -- a structural, path-based production/test separation with no
     parameter anywhere that lets a caller opt out of it.
  3. `captured_at` is no longer trusted as PIT-availability proof by
     itself. `decision/event_evidence.py`'s git-history first-availability
     check (hardened round 7 into `_git_exact_content_first_seen` -- see
     that module's own docstring) queries this repo's REAL git history
     (offline, read-only) for a cited file's earliest add-commit, and that
     -- not the self-declared field -- is the authoritative gate: the real
     first-availability must be at-or-before the decision instant, AND the
     self-declared `captured_at` may never precede it. Unavailable git
     history means
     `NOT_COMPUTABLE` (rejected), never a fallback to the self-declared
     value. Applied to both the Event Evidence Envelope and the P8-09
     canonical record.
  4. `citation` is now a CLOSED schema requiring and verifying a real
     primary-source document: `raw_source_ref` + `raw_source_sha256` (a
     real, independently hash-verified raw artifact), `published_at` (the
     raw source's own real announcement timestamp, at-or-before the
     decision instant), `locator` (where in the document the claimed
     language appears), and `observed_fact` (the actual quoted text) --
     which must appear VERBATIM inside the raw source file's real decoded
     content. A bare free-text note is no longer sufficient; `direction`
     is only ever grounded in this observed, hash-verified quotation, never
     a bare assertion.
  5. The output packet now persists `capture_kind`,
     `first_authoritative_seen_at`, and the full raw-source lineage
     (`raw_source_ref`/`raw_source_sha256`/`published_at`/`locator`)
     alongside the verdict -- `validate_packet` re-asserts these as a
     closed vocabulary and an all-or-nothing field group, independent of
     how the packet was constructed, so a loaded packet cannot hide how
     (or whether) the verdict was genuinely obtained.

  Net effect: until a genuine raw primary-source document is committed for
  a real subject, no envelope can pass ALL of real `LIVE_OFFICIAL_CAPTURE`
  classification + real closed-schema citation to a real raw artifact +
  real git-provable first-availability at-or-before the decision instant --
  so `LIVE_OFFICIAL_CAPTURE` remains genuinely unproducible for every real
  subject in this repo today, exactly as required. Positive classifier
  arithmetic (return computation, threshold classification) is still
  exercised directly against this module's lower-level functions in tests
  -- "below the production evidence boundary" -- never by smuggling a test
  fixture through the real `build_packet()` entry point.

★ CIO review round 7 approved the round-6 test-only mock design outright
  ("normal unit-test design... no change needed there") but found 4 P1
  defects and 1 P2 remaining in the PRODUCTION provenance implementation
  itself, entirely inside `decision/event_evidence.py` -- this module's own
  public interface (`verify_event_reaction_claim`/`verify_expectations_
  gap_canonical_record`'s signatures and return shapes) is UNCHANGED by
  round 7. See `decision/event_evidence.py`'s own docstring for full
  detail: (1) first-availability is now content-addressed (the exact
  current bytes, not merely the path's original add-commit) so editing an
  old file today can never inherit its old first-seen date; (2) the raw
  primary-source document now gets its own independent git-availability
  gate, not just the envelope wrapper; (3) a declared timestamp AFTER
  `decision_at` is now rejected everywhere this gate runs, not just a
  declared timestamp preceding first-availability; (4) `direction` must
  now be grounded in the raw source's own explicit, structured
  `observed_direction` field (never a bare co-occurring quotation); (5)
  `locator` must now name a real, resolvable key in the raw source whose
  value genuinely contains the quoted text; (6) the git timestamp basis is
  now committer time, not the freely-backdatable author time.

★ CIO review round 8 approved BOTH the round-6/7 test-only mock design AND
  round 7's exact-content-addressed direction outright, but stress-testing
  round 7's time-ordering rule against real-world evidence collection found
  2 further P1 defects, again entirely inside `decision/event_evidence.py`
  -- this module's own public interface is UNCHANGED by round 8. (1) round
  7's ordering was INVERTED for a raw source's `published_at`: real
  publication always precedes when Atlas commits a copy, so requiring
  `git_first_seen <= published_at` rejected virtually every legitimate
  citation. Replaced with three separately-modeled clocks (`source_
  published_at` <= `captured_at`, then `effective_available_at =
  max(captured_at, exact_content_first_seen_at) <= decision_at`) applied
  identically to the envelope, the raw source citation, and the P8-09 EG
  canonical record. (2) `observed_direction` compared one human-typed
  assertion to another -- never independent verification. Retired; `direction`
  must now come from one of exactly two closed, module-owned routes
  (`direction_origin`: `OFFICIAL_STRUCTURED_FIELD` or `RATIFIED_
  DERIVATION`), both intentionally EMPTY in this module's real, committed
  tables today. `_verify_first_availability`'s NOT_COMPUTABLE error is also
  now explicitly named `..._PROVENANCE_NOT_COMPUTABLE`, distinct from plain
  missing price data.

★ CIO review round 9 approved round 8's 3-clock time model, exact-content
  provenance, and the "mocks live only in test files" principle outright,
  but found 2 narrower authority-boundary defects, again entirely inside
  `decision/event_evidence.py` -- this module's own public interface is
  UNCHANGED by round 9. (1) Round 8's `mocked_ratified_direction_tables()`
  test helper had been added to `decision/event_evidence.py` itself (a
  production module) -- any operational caller could have imported it and
  injected rules at runtime, defeating the empty-table lock. Removed
  entirely from `decision/`, along with the `contextlib` import that only
  existed for it; the equivalent helper now lives ONLY inside `test/
  test_price_reflection.py`, patching that file's own loaded module
  instance. (2) Round 8's comments treated "a developer added a table
  entry" as itself the ratification act -- conflating IMPLEMENTATION (code
  that knows how a mapping/derivation would compute a direction) with
  AUTHORITY (a genuine Rule Authority decision that a rule is approved).
  Split into three independent tables: `OFFICIAL_DIRECTION_FIELD_
  IMPLEMENTATIONS`/`DERIVATION_RULE_IMPLEMENTATIONS` (pure code) and
  `DIRECTION_RULE_AUTHORITY_REGISTRY` (closed-schema, hash-verified
  authority records keyed by `rule_id`/`rule_version`). Operational lookup
  now requires a matching, cross-referenced entry in BOTH an
  implementation table AND the authority registry with `approval_
  status=RATIFIED` -- an implementation with no ratified authority is
  unusable, and a ratified authority record with no matching
  implementation is equally unusable. All three tables remain intentionally
  EMPTY in this module's real, committed source.

Staleness is still the loudest rule: if `price_as_of` is missing or older
than the freshness ceiling relative to `decision_date`, BOTH `price_state`
and `reflection_status` are forced to `UNKNOWN` regardless of every other
input. This check runs first and short-circuits everything else.

`classification_thresholds` (15%/8%/3%/2%-style cutoffs) have never been
CIO-ratified — `classification_thresholds_approval_status` says so explicitly
in the contract (`"PROVISIONAL"`) and every output packet echoes it verbatim
as `price_reflection.threshold_basis`. A `PROVISIONAL` basis is not a defect
in this module (it is the honest, currently-true state of these numbers) but
IS a signal to every downstream consumer: no `PARTIALLY_REFLECTED`/
`FULLY_REFLECTED`/`OVEREXTENDED`/`STRONG_MOMENTUM` verdict this module ever
emits is a CIO-ratified final call — see `authority` below, which already
sets `rule_authority_substitution_authorized: false` and every trading-path
boolean `false`; `threshold_basis` makes that same "review signal, not a
final determination" property visible on the verdict itself, not just in
the authority block.

It is deliberately blind to thesis quality, conviction, or any fundamental
narrative — the public builder below (`build_packet`) accepts **only**
price/volume/valuation-history/reference-point parameters. There is no
"thesis" or "fundamental strength" parameter anywhere in its signature: it
is structurally impossible to feed this module optimism as an input. Good
fundamentals alone can never produce `UNDER_REFLECTED`, because this module
has no channel through which fundamentals could even arrive.

This module does not fetch evidence itself. It assembles whatever price data
the caller already has into a closed-vocabulary, deterministic,
tamper-evident packet.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "price_reflection_contract.json"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ★ SCOPE REDUCTION (see module docstring): this module no longer loads
#   `decision/expectations_gap.py`, `decision/price_evidence.py`, or
#   `decision/event_evidence.py` -- none of them are used anywhere below any
#   more, since the citation-verification machinery that consumed them
#   (`_validate_event_reaction`/`_validate_reflection_reference`/
#   `_compute_verified_return`) has been removed entirely, not merely
#   disconnected. `decision/price_evidence.py` remains fully real and in
#   active use elsewhere in this repo (`decision/pilot_evidence_intake.py`
#   assembles `price_as_of`/`recent_return_windows`/`relative_strength`
#   from it before calling this module's `build_packet` -- this module
#   itself was never the right place for that assembly). `decision/event_
#   evidence.py` no longer exists in this repo at all.

# Parameter-name substrings this module's public builder must never contain.
# Enforced both by construction (see build_packet's signature) and by a
# regression test that inspects the live signature at test time, so a future
# edit cannot silently reintroduce fundamental/thesis-shaped scope creep.
FORBIDDEN_PARAMETER_SUBSTRINGS = (
    "thesis", "fundamental", "quality", "conviction", "narrative", "story",
)


class PriceReflectionError(ValueError):
    """Fail-closed P8-10 Price Reflection contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PriceReflectionError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 6,
        "contract_version": "price_reflection/6",
        "output_schema_version": "price_reflection_packet/6",
        "allowed_price_state": [
            "OVEREXTENDED", "STRONG_MOMENTUM", "MODERATE", "WEAK", "UNKNOWN",
        ],
        "allowed_reflection_status": [
            "UNDER_REFLECTED", "PARTIALLY_REFLECTED", "FULLY_REFLECTED", "UNKNOWN",
        ],
        "allowed_data_state": [
            "PRICE_DATA_MISSING", "PRICE_STALE",
            "REFLECTION_UNCERTAIN_WITH_VALID_PRICE", "VALID",
        ],
        "allowed_confidence": ["LOW", "MEDIUM", "HIGH", "UNKNOWN"],
        "allowed_direction": ["POSITIVE", "NEGATIVE", "NEUTRAL", "UNKNOWN"],
        "allowed_valuation_position": ["LOW", "MID", "HIGH", "UNKNOWN"],
        "allowed_data_source_scope": [
            "IEX_ONLY_PARTIAL_US_MARKET", "KRX_OFFICIAL", "KRAKEN_OHLC", "UNKNOWN",
        ],
        # ★ CIO round 5: closed vocabulary of real evidentiary categories an
        #   Event Evidence Envelope's `source_class` used to be able to
        #   declare. Kept unchanged in the contract dict itself purely to
        #   avoid a contract/schema version bump (see module docstring,
        #   scope reduction) -- no code anywhere in this module reads or
        #   validates against it any more, since `decision/event_
        #   evidence.py` and the `event_reaction` input it backed no longer
        #   exist.
        "allowed_event_source_class": [
            "SEC_FILING_EVENT", "DART_FILING_EVENT", "OFFICIAL_RELEASE_EVENT", "GUIDANCE_CHANGE_EVENT",
        ],
        "allowed_threshold_basis": ["PROVISIONAL", "RATIFIED"],
        "korea_data_source_scope": "KRX_OFFICIAL",
        "default_freshness_ceiling_days": 5,
        "classification_thresholds": {
            "rally_min_1m_return_pct": "15", "near_high_max_distance_pct": "3",
            "strong_momentum_min_pct": "8", "mild_momentum_min_pct": "2",
        },
        # ★ CIO round 2, required item 7: these specific cutoff numbers have
        #   never been CIO-ratified (round 1's docs already said so: "the
        #   spec did not name an exact number"). Declared PROVISIONAL here,
        #   verifiable in this contract, and echoed on every output packet
        #   as `price_reflection.threshold_basis` -- never silently upgraded
        #   to RATIFIED by this module itself.
        "classification_thresholds_approval_status": "PROVISIONAL",
        "confidence_thresholds": {
            "high_min_scored_signal_count": 4, "medium_min_scored_signal_count": 2,
        },
        "authority": {
            "price_reflection_assembly_only": True,
            "rule_authority_substitution_authorized": False,
            "stage_promotion_authorized": False,
            "candidate_ready_buy_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise PriceReflectionError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise PriceReflectionError(f"CONTRACT_FIELD_MISMATCH:{key}")
    if expected["classification_thresholds_approval_status"] not in expected["allowed_threshold_basis"]:
        raise PriceReflectionError("CONTRACT_THRESHOLD_APPROVAL_STATUS_INVALID")
    for bad in ("REJECTED",):
        if bad in expected["allowed_price_state"] or bad in expected["allowed_reflection_status"]:
            raise PriceReflectionError("CONTRACT_VOCABULARY_CONTAINS_REJECTED")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise PriceReflectionError(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise PriceReflectionError(code) from exc


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise PriceReflectionError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise PriceReflectionError(code) from exc
    if parsed.isoformat() != value:
        raise PriceReflectionError(code)
    return parsed


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise PriceReflectionError(code)
    return value


def _pct(value, code: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PriceReflectionError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PriceReflectionError(code) from exc
    if not parsed.is_finite():
        raise PriceReflectionError(code)
    return parsed


def _validate_recent_return_windows(value, contract: dict) -> dict:
    fields = {"1m", "3m", "6m"}
    if value is None:
        return {"1m": None, "3m": None, "6m": None}
    if not isinstance(value, dict) or not set(value).issubset(fields):
        raise PriceReflectionError("RECENT_RETURN_WINDOWS_FIELDS_INVALID")
    return {key: _pct(value.get(key), f"RECENT_RETURN_WINDOWS_{key}_INVALID") for key in fields}


def _validate_relative_strength(value, contract: dict) -> dict:
    fields = {"vs_market", "vs_sector", "volume_change_pct", "position_vs_recent_high_pct"}
    if value is None:
        return {key: None for key in fields}
    if not isinstance(value, dict) or not set(value).issubset(fields):
        raise PriceReflectionError("RELATIVE_STRENGTH_FIELDS_INVALID")
    return {key: _pct(value.get(key), f"RELATIVE_STRENGTH_{key}_INVALID") for key in fields}


def _validate_valuation_context(value, contract: dict) -> dict:
    fields = {"metric_type", "position_in_range"}
    if value is None:
        return {"metric_type": None, "position_in_range": None}
    if not isinstance(value, dict) or not set(value).issubset(fields):
        raise PriceReflectionError("VALUATION_CONTEXT_FIELDS_MISMATCH")
    metric_type = value.get("metric_type")
    if metric_type is not None:
        _token(metric_type, "VALUATION_CONTEXT_METRIC_TYPE_INVALID")
    position = value.get("position_in_range")
    if position is not None and position not in contract["allowed_valuation_position"]:
        raise PriceReflectionError("VALUATION_CONTEXT_POSITION_INVALID")
    return {"metric_type": metric_type, "position_in_range": position}


def _render_or_unknown(value: Decimal | None) -> str:
    return "UNKNOWN" if value is None else str(value)


def _price_state(
    *, m1: Decimal | None, rs_market: Decimal | None, pos_high: Decimal | None,
    volume_change: Decimal | None, val_pos: str | None, contract: dict,
) -> tuple[str, list[str], int]:
    """Pure, price/volume-only momentum read. NEVER produces a reflection
    verdict -- see module docstring for why this is now structurally
    separate from `_reflection_status`."""
    reasons: list[str] = []
    for label, val in (
        ("1m_return", m1), ("vs_market", rs_market),
        ("position_vs_recent_high_pct", pos_high), ("volume_change_pct", volume_change),
    ):
        if val is not None:
            reasons.append(f"{label}:{val}")
    if val_pos is not None:
        reasons.append(f"valuation_position_in_range:{val_pos}")

    scored_signals = sum(
        signal is not None for signal in (m1, rs_market, pos_high, volume_change, val_pos)
    )

    thresholds = contract["classification_thresholds"]
    rally_threshold = Decimal(thresholds["rally_min_1m_return_pct"])
    near_high_threshold = Decimal(thresholds["near_high_max_distance_pct"])
    strong_threshold = Decimal(thresholds["strong_momentum_min_pct"])
    mild_threshold = Decimal(thresholds["mild_momentum_min_pct"])

    rally = m1 is not None and m1 >= rally_threshold
    near_high = pos_high is not None and pos_high <= near_high_threshold
    expensive = val_pos == "HIGH"
    if rally and (near_high or expensive):
        reasons.append("RALLY_AND_STRETCHED_POSITIONING")
        return "OVEREXTENDED", reasons, scored_signals

    momentum_values = [v for v in (m1, rs_market) if v is not None]
    momentum = sum(momentum_values) / len(momentum_values) if momentum_values else None
    if momentum is None or scored_signals < 2:
        reasons.append(f"INSUFFICIENT_PRICE_SIGNALS:scored_count={scored_signals}")
        return "UNKNOWN", reasons, scored_signals

    reasons.append(f"momentum_avg:{momentum}")
    if momentum >= strong_threshold:
        return "STRONG_MOMENTUM", reasons, scored_signals
    if momentum >= mild_threshold:
        return "MODERATE", reasons, scored_signals
    return "WEAK", reasons, scored_signals


# ★ SCOPE REDUCTION (see module docstring): `_reflection_status` (the
#   function that used to compute a real, evidence-verified reference point
#   and threshold-classify a computed return into UNDER_REFLECTED/
#   PARTIALLY_REFLECTED/FULLY_REFLECTED) has been REMOVED, not merely
#   disconnected. `reflection_status` is now the literal constant
#   `"UNKNOWN"` everywhere in this module -- there is no function left
#   anywhere in this file that could compute anything else. This is
#   deliberately NOT a "the current evidence happens to be insufficient"
#   state; it is a structural fact about what code exists.
REFLECTION_STATUS_ALWAYS = "UNKNOWN"


def _classify(
    *,
    price_as_of: str | None,
    decision_date: dt.date,
    freshness_ceiling_days: int,
    windows: dict,
    strength: dict,
    valuation: dict,
    contract: dict,
) -> tuple[str, str, str, str, list[str]]:
    """Pure, deterministic classification. Rule 1 (staleness) always runs
    first and, if triggered, short-circuits everything else -- both
    `price_state` and `reflection_status` are forced UNKNOWN.

    `reflection_status` is unconditionally `REFLECTION_STATUS_ALWAYS`
    ("UNKNOWN") -- see module docstring (scope reduction). `price_state`
    (the pure, price/volume-only momentum read) is fully real and
    unaffected.

    Returns (price_state, reflection_status, confidence, data_state,
    reasons)."""
    if price_as_of is None:
        return "UNKNOWN", REFLECTION_STATUS_ALWAYS, "UNKNOWN", "PRICE_DATA_MISSING", ["PRICE_AS_OF_MISSING"]

    price_as_of_dt = _utc(price_as_of, "PRICE_AS_OF_INVALID")
    if price_as_of_dt.date() > decision_date:
        raise PriceReflectionError("PRICE_AS_OF_IN_FUTURE")
    age_days = (decision_date - price_as_of_dt.date()).days
    if age_days > freshness_ceiling_days:
        return "UNKNOWN", REFLECTION_STATUS_ALWAYS, "UNKNOWN", "PRICE_STALE", [
            f"PRICE_AS_OF_STALE:age_days={age_days}:ceiling_days={freshness_ceiling_days}"
        ]

    m1 = windows["1m"]
    rs_market = strength["vs_market"]
    pos_high = strength["position_vs_recent_high_pct"]
    volume_change = strength["volume_change_pct"]
    val_pos = valuation["position_in_range"] if valuation["position_in_range"] != "UNKNOWN" else None

    price_state, price_reasons, _scored_signals = _price_state(
        m1=m1, rs_market=rs_market, pos_high=pos_high,
        volume_change=volume_change, val_pos=val_pos, contract=contract,
    )

    # reflection_status is always UNKNOWN in this reduced scope -- price_
    # state=UNKNOWN and a non-UNKNOWN reflection_status can therefore never
    # coexist by construction (round-3's structural invariant is now
    # trivially true, not merely enforced case-by-case); still re-asserted
    # unconditionally in validate_packet() below too.
    reflection_status = REFLECTION_STATUS_ALWAYS
    reasons = [f"price_state={price_state}"] + price_reasons + [
        f"reflection_status={reflection_status}",
        "NO_REFLECTION_EVIDENCE_AUTHORITY_EXISTS_IN_THIS_REDUCED_SCOPE",
    ]

    data_state = "REFLECTION_UNCERTAIN_WITH_VALID_PRICE"
    confidence = "UNKNOWN"

    return price_state, reflection_status, confidence, data_state, reasons


# ★ SCOPE REDUCTION (see module docstring): `event_reaction`/`reflection_
#   reference` are no longer accepted parameters -- not merely unused, they
#   do not exist in this function's signature at all. There is no way for
#   any caller (real or a future edit reintroducing an old call site) to
#   pass a citation through this function; Python itself raises `TypeError`
#   on an unexpected keyword argument. The output packet's own `event_
#   reaction`/`reflection_reference` sub-objects are hardcoded, literal
#   constants (`_INERT_EVENT_REACTION`/`_INERT_REFLECTION_REFERENCE` below)
#   -- kept in the output SHAPE unchanged (no contract/schema version bump)
#   purely so every existing downstream consumer (`decision/alpha_review.py`,
#   `shadow/alpha_shadow_ledger.py`, `briefing/alpha_review_briefing.py`)
#   keeps working against the exact same packet shape it always has; their
#   values can now never be anything but "UNKNOWN".
_INERT_EVENT_REACTION = {
    "event_at": "UNKNOWN", "direction": "UNKNOWN", "reaction_magnitude_pct": "UNKNOWN",
    "source_class": "UNKNOWN", "source_ref": "UNKNOWN", "source_sha256": "UNKNOWN",
    "verified_post_event_return_pct": "UNKNOWN", "capture_kind": "UNKNOWN",
    "first_authoritative_seen_at": "UNKNOWN", "raw_source_ref": "UNKNOWN",
    "raw_source_sha256": "UNKNOWN", "published_at": "UNKNOWN", "locator": "UNKNOWN",
}
_INERT_REFLECTION_REFERENCE = {
    "reference_event_id": "UNKNOWN", "expectation_as_of": "UNKNOWN",
    "expectations_gap_status": "UNKNOWN", "expectations_gap_packet_sha256": "UNKNOWN",
    "expectations_gap_reference_date": "UNKNOWN",
    "expectations_gap_first_authoritative_seen_at": "UNKNOWN",
    "verified_post_reference_return_pct": "UNKNOWN",
}


def build_packet(
    *,
    subject: str,
    decision_date: str,
    generated_at: str,
    price_as_of: str | None = None,
    freshness_ceiling_days: int | None = None,
    relative_strength: dict | None = None,
    recent_return_windows: dict | None = None,
    valuation_context: dict | None = None,
    data_source_scope: str | None = None,
    contract: dict | None = None,
) -> dict:
    """Build a Price Reflection packet.

    Every parameter above is price, volume, relative-strength, or
    valuation-history data (or plumbing: subject/dates/contract). There is
    no thesis-quality or fundamental-strength parameter — see
    FORBIDDEN_PARAMETER_SUBSTRINGS and test_price_reflection.py for the
    signature-inspection regression that guards this. There is also no
    event-reaction or reflection-reference-point parameter any more (scope
    reduction, see module docstring) — `reflection_status` is always
    `"UNKNOWN"` in this reduced scope.
    """
    contract = _validate_contract(contract) if contract is not None else load_contract()
    subject_checked = _token(subject, "SUBJECT_INVALID")
    decision_date_checked = _date(decision_date, "DECISION_DATE_INVALID")
    _utc(generated_at, "GENERATED_AT_INVALID")

    scope = data_source_scope if data_source_scope is not None else "UNKNOWN"
    if scope not in contract["allowed_data_source_scope"]:
        raise PriceReflectionError("DATA_SOURCE_SCOPE_INVALID")

    ceiling = (
        contract["default_freshness_ceiling_days"]
        if freshness_ceiling_days is None else freshness_ceiling_days
    )
    if type(ceiling) is not int or ceiling < 0:
        raise PriceReflectionError("FRESHNESS_CEILING_DAYS_INVALID")

    if price_as_of is not None:
        _utc(price_as_of, "PRICE_AS_OF_INVALID")

    windows = _validate_recent_return_windows(recent_return_windows, contract)
    strength = _validate_relative_strength(relative_strength, contract)
    valuation = _validate_valuation_context(valuation_context, contract)

    price_state, reflection_status, confidence, data_state, reasons = _classify(
        price_as_of=price_as_of,
        decision_date=decision_date_checked,
        freshness_ceiling_days=ceiling,
        windows=windows,
        strength=strength,
        valuation=valuation,
        contract=contract,
    )

    # event_reaction/reflection_reference are structurally always absent in
    # this reduced scope -- always reported as missing, matching the literal
    # truth that no caller can ever supply them.
    missing_inputs = sorted(name for name, val in (
        ("price_as_of", price_as_of),
        ("relative_strength", relative_strength),
        ("recent_return_windows", recent_return_windows),
        ("event_reaction", None),
        ("reflection_reference", None),
        ("valuation_context", valuation_context),
    ) if val is None)

    price_reflection = {
        "price_state": price_state,
        "reflection_status": reflection_status,
        "confidence": confidence,
        "data_state": data_state,
        "threshold_basis": contract["classification_thresholds_approval_status"],
        "price_as_of": price_as_of if price_as_of is not None else "UNKNOWN",
        "relative_strength": {
            "vs_market": _render_or_unknown(strength["vs_market"]),
            "vs_sector": _render_or_unknown(strength["vs_sector"]),
            "volume_change_pct": _render_or_unknown(strength["volume_change_pct"]),
            "position_vs_recent_high_pct": _render_or_unknown(strength["position_vs_recent_high_pct"]),
        },
        "recent_return_windows": {
            "1m": _render_or_unknown(windows["1m"]),
            "3m": _render_or_unknown(windows["3m"]),
            "6m": _render_or_unknown(windows["6m"]),
        },
        "event_reaction": dict(_INERT_EVENT_REACTION),
        "reflection_reference": dict(_INERT_REFLECTION_REFERENCE),
        "valuation_context": {
            "metric_type": valuation["metric_type"] or "UNKNOWN",
            "position_in_range": valuation["position_in_range"] or "UNKNOWN",
        },
        "reasons": reasons,
        "missing_inputs": missing_inputs,
        "data_source_scope": scope,
    }

    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "generated_at": generated_at,
        "subject": subject_checked,
        "decision_date": decision_date,
        "price_reflection": price_reflection,
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "generated_at", "subject",
        "decision_date", "price_reflection", "authority", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise PriceReflectionError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("authority") != contract["authority"]
    ):
        raise PriceReflectionError("OUTPUT_IDENTITY_INVALID")
    _utc(packet.get("generated_at"), "OUTPUT_GENERATED_AT_INVALID")
    _token(packet.get("subject"), "OUTPUT_SUBJECT_INVALID")
    _date(packet.get("decision_date"), "OUTPUT_DECISION_DATE_INVALID")

    pr = packet.get("price_reflection")
    pr_fields = {
        "price_state", "reflection_status", "confidence", "data_state", "threshold_basis",
        "price_as_of", "relative_strength", "recent_return_windows", "event_reaction",
        "reflection_reference", "valuation_context", "reasons", "missing_inputs",
        "data_source_scope",
    }
    if not isinstance(pr, dict) or set(pr) != pr_fields:
        raise PriceReflectionError("OUTPUT_PRICE_REFLECTION_FIELDS_MISMATCH")

    price_state = pr.get("price_state")
    reflection_status = pr.get("reflection_status")
    confidence = pr.get("confidence")
    data_state = pr.get("data_state")
    threshold_basis = pr.get("threshold_basis")

    if price_state not in contract["allowed_price_state"]:
        raise PriceReflectionError("OUTPUT_PRICE_STATE_INVALID")
    if reflection_status not in contract["allowed_reflection_status"]:
        raise PriceReflectionError("OUTPUT_REFLECTION_STATUS_INVALID")
    if confidence not in contract["allowed_confidence"]:
        raise PriceReflectionError("OUTPUT_CONFIDENCE_INVALID")
    if reflection_status == "UNKNOWN" and confidence != "UNKNOWN":
        raise PriceReflectionError("OUTPUT_UNKNOWN_REFLECTION_STATUS_REQUIRES_UNKNOWN_CONFIDENCE")
    if "REJECTED" in contract["allowed_price_state"] or "REJECTED" in contract["allowed_reflection_status"]:
        raise PriceReflectionError("OUTPUT_VOCABULARY_CONTAINS_REJECTED")  # defensive

    if data_state not in contract["allowed_data_state"]:
        raise PriceReflectionError("OUTPUT_DATA_STATE_INVALID")
    if (reflection_status == "UNKNOWN") != (data_state != "VALID"):
        raise PriceReflectionError("OUTPUT_DATA_STATE_REFLECTION_STATUS_MISMATCH")

    # ★ CIO round 3, required item 3: structural invariant, re-asserted here
    #   independent of _classify's own enforcement -- price_state=UNKNOWN
    #   and a non-UNKNOWN reflection_status can never coexist in ANY packet
    #   this function accepts, however it was constructed or tampered with.
    if price_state == "UNKNOWN" and reflection_status != "UNKNOWN":
        raise PriceReflectionError("OUTPUT_PRICE_STATE_UNKNOWN_REFLECTION_STATUS_CONTRADICTION")

    if threshold_basis != contract["classification_thresholds_approval_status"]:
        raise PriceReflectionError("OUTPUT_THRESHOLD_BASIS_MISMATCH")

    data_source_scope = pr.get("data_source_scope")
    if data_source_scope not in contract["allowed_data_source_scope"]:
        raise PriceReflectionError("OUTPUT_DATA_SOURCE_SCOPE_INVALID")

    reasons = pr.get("reasons")
    if not isinstance(reasons, list) or not reasons or any(
        not isinstance(item, str) or not item.strip() for item in reasons
    ):
        raise PriceReflectionError("OUTPUT_REASONS_INVALID")

    missing = pr.get("missing_inputs")
    allowed_missing = {
        "price_as_of", "relative_strength", "recent_return_windows",
        "event_reaction", "reflection_reference", "valuation_context",
    }
    if (
        not isinstance(missing, list)
        or missing != sorted(set(missing))
        or any(item not in allowed_missing for item in missing)
    ):
        raise PriceReflectionError("OUTPUT_MISSING_INPUTS_INVALID")
    if ("price_as_of" in missing) != (pr.get("price_as_of") == "UNKNOWN"):
        raise PriceReflectionError("OUTPUT_MISSING_INPUTS_PRICE_AS_OF_MISMATCH")
    if "price_as_of" in missing and data_state != "PRICE_DATA_MISSING":
        raise PriceReflectionError("OUTPUT_MISSING_PRICE_AS_OF_MUST_BE_PRICE_DATA_MISSING")

    rs = pr.get("relative_strength")
    if not isinstance(rs, dict) or set(rs) != {
        "vs_market", "vs_sector", "volume_change_pct", "position_vs_recent_high_pct"
    }:
        raise PriceReflectionError("OUTPUT_RELATIVE_STRENGTH_FIELDS_MISMATCH")
    for value in rs.values():
        if value != "UNKNOWN":
            _pct(value, "OUTPUT_RELATIVE_STRENGTH_VALUE_INVALID")

    rw = pr.get("recent_return_windows")
    if not isinstance(rw, dict) or set(rw) != {"1m", "3m", "6m"}:
        raise PriceReflectionError("OUTPUT_RECENT_RETURN_WINDOWS_FIELDS_MISMATCH")
    for value in rw.values():
        if value != "UNKNOWN":
            _pct(value, "OUTPUT_RECENT_RETURN_WINDOWS_VALUE_INVALID")

    # ★ SCOPE REDUCTION (see module docstring): `event_reaction`/
    #   `reflection_reference` can no longer legitimately carry ANY value
    #   other than the fully-inert, all-"UNKNOWN" constant -- there is no
    #   code path left anywhere in this module that could produce anything
    #   else. Rather than format-validating fields that can only ever be
    #   "UNKNOWN" (dead validation logic for a dead capability), this
    #   asserts EXACT equality to the inert constant -- a single check that
    #   is simultaneously stricter (rejects ANY deviation, not just
    #   malformed ones) and simpler than the field-by-field format checks
    #   rounds 3-9 built up. A loaded/tampered packet claiming a real
    #   citation (e.g. `capture_kind="LIVE_OFFICIAL_CAPTURE"`) is rejected
    #   outright, regardless of how well-formed the rest of it looks.
    er = pr.get("event_reaction")
    if er != _INERT_EVENT_REACTION:
        raise PriceReflectionError("OUTPUT_EVENT_REACTION_MUST_BE_INERT_IN_THIS_REDUCED_SCOPE")

    rr = pr.get("reflection_reference")
    if rr != _INERT_REFLECTION_REFERENCE:
        raise PriceReflectionError("OUTPUT_REFLECTION_REFERENCE_MUST_BE_INERT_IN_THIS_REDUCED_SCOPE")

    vc = pr.get("valuation_context")
    if not isinstance(vc, dict) or set(vc) != {"metric_type", "position_in_range"}:
        raise PriceReflectionError("OUTPUT_VALUATION_CONTEXT_FIELDS_MISMATCH")
    if vc["position_in_range"] not in contract["allowed_valuation_position"] + ["UNKNOWN"]:
        raise PriceReflectionError("OUTPUT_VALUATION_CONTEXT_POSITION_INVALID")

    if pr.get("price_as_of") != "UNKNOWN":
        _utc(pr["price_as_of"], "OUTPUT_PRICE_AS_OF_INVALID")

    digest = packet.get("packet_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise PriceReflectionError("OUTPUT_SHA_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise PriceReflectionError("OUTPUT_SHA_MISMATCH")
    return copy.deepcopy(packet)


def assert_no_fundamental_parameters() -> None:
    """Structural guard: the public builder must never accept a thesis/
    fundamental-strength parameter. Used by the CIO-facing regression test."""
    params = list(inspect.signature(build_packet).parameters)
    offending = [
        name for name in params
        if any(bad in name.lower() for bad in FORBIDDEN_PARAMETER_SUBSTRINGS)
    ]
    if offending:
        raise PriceReflectionError(f"FORBIDDEN_PARAMETER_PRESENT:{offending}")


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise PriceReflectionError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run(input_path: Path, output_path: Path) -> int:
    try:
        envelope = _read_json(input_path)
        if not isinstance(envelope, dict):
            raise PriceReflectionError("INPUT_ENVELOPE_NOT_OBJECT")
        packet = build_packet(**envelope)
        write_json_atomic(output_path, packet)
        return 0
    except (PriceReflectionError, OSError, TypeError, ValueError) as exc:
        print(f"Price Reflection build failed: {exc}")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.input, args.out)


if __name__ == "__main__":
    raise SystemExit(main())

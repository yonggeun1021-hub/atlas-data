# P8-10 Price Reflection Contract

`decision/price_reflection.py` builds a **Price Reflection** packet: a
structurally separated read on (1) price/momentum and (2) whether the
market's price already reflects a specific, real expectation or event,
based strictly on price, volume, relative-strength, and valuation-history
evidence the caller supplies.

## ★★★ SCOPE REDUCTION — CIO final integration ruling on PR #212 (2026-08-23)

**Read this section first.** Everything below it (rounds 2-9) is kept as an
audit trail of what was built and why it was ultimately removed — the
`event_reaction`/`reflection_reference`-citation-verification machinery
those sections describe **no longer exists in this repository**.

Round 9 fixed the two local defects the CIO had requested, but integrated
review then found a further, deeper PIT defect in the Event Evidence
Authority engine built across rounds 5-9: the ratification-authority lookup
parsed `ratified_at` but never compared it to `decision_at`, so a rule
ratified in the FUTURE relative to a historical decision could still be
applied retrospectively to that decision, and the "evidence" backing a
ratification record was only ever hash-checked against an arbitrary repo
file, never validated as a genuine, structured Rule Authority record. This
is the same class of provenance failure rounds 5-9 kept finding and fixing
at the evidence layer, recurring one layer up at the policy/ratification
layer.

The CIO explicitly declined a round-10 local patch — an implementation that
needed 9 successive integrity-defect rounds is over-scoped for one PR. PR
#212 was reduced to the proven P8-10 MVP boundary instead:

**Kept:** real historical price-series linkage and PIT-safe price endpoints
(`decision/price_evidence.py`, untouched); `price_state` structurally
separate from `reflection_status`; `PRICE_DATA_MISSING`/`PRICE_STALE`/
`REFLECTION_UNCERTAIN_WITH_VALID_PRICE` data states; `PROVISIONAL`
threshold-basis exposure; Korea market-membership fail-closed behavior;
honest BTC/Korea/TSM/Doosan outputs; `decision/alpha_review.py`'s
fail-closed entry-blocking behavior and `authority=false` posture.

**Removed from this module and this PR entirely:** `decision/event_
evidence.py` (the whole Event Evidence Authority engine — provenance
verification, direction-rule implementation tables, the ratification-
authority registry — deleted, not patched); this module's own
`event_reaction`/`reflection_reference` build_packet parameters and every
internal function that only existed to verify or classify them. There is
no code path left in this module — not merely an empty table, an actually
absent function — that could compute `reflection_status` as anything other
than the hardcoded literal `"UNKNOWN"`. `price_state` (the pure,
price/volume-only momentum read) is completely unaffected and remains
fully real.

**Deferred, not abandoned:** a future, separate, dependent PR must design a
Reflection Evidence Authority together with Atlas P5 Rule Authority —
append-only per-rule canonical records, `ratified_at`/`effective_from`,
exact-content provenance, explicit decision-time ordering checks, and a
structured authority-evidence schema — and get that design approved BEFORE
any implementation is written, not merely before merge. Tracked on the
existing P8-10 WBS row.

## `price_reflection/2` (CIO review round 2 on PR #212): the core fix

Round 1 conflated momentum and reflection into a single `status` field and
let a large price move alone (e.g. 1-month return ≥ 8%) produce
`FULLY_REFLECTED`, with no event or expectation reference at all. The CIO
correctly flagged this: **a price having risen is not, by itself, evidence
that expectations/events/a thesis have been "reflected" — reflection
requires a reference point for WHAT is supposed to be reflected in the
price.** This module now keeps two claims structurally separate:

## `price_reflection/3` (CIO review round 3): four further defects closed

Round 2's reference-point requirement was necessary but not sufficient —
round 3 closed four remaining holes CI/the test suite alone didn't catch:

1. **A bare `direction`/`expectations_gap_status` string was still not a
   real reference.** A caller could type `direction="POSITIVE"` with zero
   evidence behind it, or a bare `expectations_gap_status="POSITIVE"`
   string with no actual P8-09 packet. `reflection_reference.
   expectations_gap_status` is retired; callers now pass `reflection_
   reference.expectations_gap_packet` (the FULL, already-built P8-09
   packet), independently re-validated via `decision/expectations_gap.py`'s
   own `validate_packet` (hash/tamper/vocab) with `subject`/`decision_date`
   cross-checked against this packet's own. `event_reaction.direction` now
   requires `event_reaction.source_ref` + `source_sha256` (a real evidence
   citation) before it counts toward a reflection verdict — still accepted
   as plain input without them (a caller may legitimately record an
   observed direction it can't yet cite), just never sufficient alone.
2. **Reflection was graded off a generic, "now"-anchored return that could
   be almost entirely PRE-event movement.** `reflection_status` now
   requires a real, caller-computed `event_reaction.post_event_return_pct`
   / `reflection_reference.post_reference_return_pct` — a return measured
   specifically from the reference date forward — never the generic
   `recent_return_windows`/`relative_strength` figures `price_state` uses.
3. **`price_state=UNKNOWN` + a non-`UNKNOWN` `reflection_status` could
   coexist** — CIO's exact reproduction: 1-month return +10%, one positive
   event/reference point, no other price signal, produced `price_state=
   UNKNOWN` / `reflection_status=FULLY_REFLECTED` / `data_state=VALID`, a
   contradiction `alpha_review.py` assumed was structurally impossible. Now
   a hard invariant in both `_classify` (forces `reflection_status` back to
   `UNKNOWN` whenever `price_state` came out `UNKNOWN`) and
   `validate_packet` (`OUTPUT_PRICE_STATE_UNKNOWN_REFLECTION_STATUS_
   CONTRADICTION`, unconditional on any packet however constructed).
4. **`threshold_basis="PROVISIONAL"` didn't actually gate anything
   operational.** `decision/alpha_review.py` now treats a non-`RATIFIED`
   `threshold_basis` as an independent trigger blocking any positive/
   differentiated `opportunity_state` — no such state is reachable while
   thresholds remain provisional, regardless of what `price_state`/
   `reflection_status` value they produced. `price_reflection.py` itself
   still computes and surfaces real values under provisional thresholds
   (diagnostic output); `alpha_review.py` is the fail-closed operational
   boundary (round 4, `alpha_review/5`: reported as the dedicated
   `WAIT_FOR_RULE_RATIFICATION` state rather than the same `WAIT_FOR_PRICE`
   label a genuine `reflection_status=="UNKNOWN"` uses — see the "Threshold
   approval status" section below).

## `price_reflection/4` (CIO review round 4): evidence verification made real

Round 3's "evidence verification" was still only a FORMAT check —
`source_ref`/`source_sha256` were regex-validated but never cross-checked
against a real committed file, and `post_event_return_pct`/
`post_reference_return_pct` were still trusted caller-supplied numbers with
no real price lookup or PIT check behind them. Confirmed reproducible:
`source_ref="MADE-UP"`, `source_sha256="a"*64` (an arbitrary 64-hex-char
string), `post_event_return_pct="99"` (all fabricated, no real evidence
anywhere) produced a confident `FULLY_REFLECTED`. Even the test suite itself
used a fake `"a"*64` hash and a hand-written return figure — proof the gap
was structural, not a test-coverage gap. Round 4 closes it completely:

1. **`_verify_evidence_citation`** resolves `event_reaction.source_ref` to a
   real file under this repo's root and independently recomputes its
   sha256 with `hashlib.sha256(path.read_bytes()).hexdigest()` — a
   non-existent path, a path that escapes the repo root (checked via
   `Path.relative_to`), or a real path with a wrong hash all fail closed
   (soft-downgrade to `reflection_status=UNKNOWN`, never a crash).
2. **`post_event_return_pct`/`post_reference_return_pct` are RETIRED as
   accepted input entirely.** Supplying either field now raises
   `EVENT_REACTION_FIELDS_MISMATCH`/`REFLECTION_REFERENCE_FIELDS_MISMATCH`
   — there is no remaining code path anywhere in this module that accepts a
   return percentage from a caller and uses it.
3. **The return is always computed internally**, from two real,
   independently looked-up close prices —
   `decision/price_evidence.py`'s `real_close_on_date`/
   `latest_real_close_at_or_before`, themselves built on
   `replay/price_series.py`/`replay/evidence_index.py` (PR #210, reused
   unchanged, not reimplemented).
4. **Both endpoint prices must be PIT-live-known as of `decision_date`**
   (`PriceSeries.live_known_asof`/`live_trading_dates_at_or_before`) — an
   evidence row captured after `decision_date` can never be used, matching
   PR #210's/#211's own anti-lookahead discipline. A real event whose price
   data was not yet knowable as of the decision timestamp still leaves
   `reflection_status=UNKNOWN`, even though the event itself is real and
   correctly cited.
5. **The return's START price is anchored to a real reference timestamp** —
   `event_reaction.event_date` for the event path, or the validated P8-09
   packet's own `decision_date` for the expectations_gap path (echoed as
   `reflection_reference.expectations_gap_reference_date`) — never an
   independently caller-chosen window. The END price is always the latest
   real, PIT-live close at or before this packet's own `decision_date`.
6. **Any failure at any step makes the return `None`** — file doesn't
   exist, hash mismatch, no real price evidence for the subject, price row
   not yet PIT-eligible, no genuine forward date gap between start and end
   — there is no fallback to a caller-supplied number, ever;
   `reflection_status` simply stays `UNKNOWN`/data effectively
   `NOT_COMPUTABLE`. `event_reaction.verified_post_event_return_pct` /
   `reflection_reference.verified_post_reference_return_pct` (renamed from
   round 3's `post_event_return_pct`/`post_reference_return_pct` — these are
   now OUTPUT-only, compute-derived fields, never accepted as input) render
   `"UNKNOWN"` whenever the corresponding path wasn't the one that actually
   produced the verdict.

## `price_reflection/5` (CIO review round 5): evidence CONTENT, not just existence

Round 4's evidence verification proved a hash-matching FILE existed, never
that it was actually evidence OF the claimed event/direction. Confirmed
reproducible: `data/2026-08-20/krx.json` (a plain KRX price snapshot, zero
event semantics) was cited as "evidence" of a POSITIVE event on `329180.KS`
and the hash-only check accepted it — any tracked file, of any kind, could
authorize an arbitrary claimed direction as long as its real hash was
supplied. Closed via `decision/event_evidence.py` (see that module's own
docstring for full detail):

1. **A hash-matching file is not proof of the claimed event.**
   `event_reaction.source_ref` must now resolve to a real committed file
   whose PARSED CONTENT is itself a structured, closed-vocabulary **Event
   Evidence Envelope** (`event_evidence_envelope/1`) independently
   asserting the SAME `subject`/`event_at`/`direction`/`source_class` the
   caller claims. A generic price/config/any-other file has no such fields
   at all and can never satisfy this, regardless of a correct hash.
2. **Current-file existence is not PIT availability.** The envelope's own
   `captured_at` must be at-or-before the decision instant being evaluated
   — a file merely existing in today's checkout is not proof it was
   available at some earlier historical `decision_date`; a future-committed
   envelope fails closed (reusing
   `replay.lookahead_gate.assert_no_signal_lookahead`).
3. **The P8-09 path is no longer retrospectively synthesizable.**
   `reflection_reference.expectations_gap_packet` (a caller-supplied,
   possibly freshly-fabricated-in-memory dict) is retired entirely.
   `expectations_gap_packet_ref`/`expectations_gap_packet_sha256` point at
   a REAL COMMITTED wrapper record this module reads and validates FROM
   THAT FILE itself — never trusting a caller-supplied dict for this path
   at all — whose own `captured_at` (independent of anything the embedded
   packet self-reports) must also be at-or-before the decision instant. A
   packet built fresh at runtime with a backdated `decision_date` can never
   satisfy this, because it was never committed at all, let alone before
   that date.
4. **Event timing needs a real timestamp, not just a date.**
   `event_reaction.event_date` is replaced by `event_reaction.event_at` (a
   full UTC timestamp). This repo's real price evidence is DAILY-
   granularity only for every subject (KRX/BTC/US) — there is no intraday
   series anywhere, and no CIO-ratified market-session-boundary rule
   exists. Rather than fabricate an unratified session table, a genuinely
   time-stamped `event_at` (any time-of-day other than the `00:00:00Z`
   sentinel for "date only") rolls the reference date back to the latest
   REAL, PIT-live trading date STRICTLY BEFORE `event_at`'s own calendar
   date — guaranteeing the reference close can never accidentally already
   reflect the event's own trading session. A bare, midnight-UTC
   `event_at` keeps timing `NOT_COMPUTABLE` (`reflection_status` stays
   `UNKNOWN`), exactly per the CIO's explicit fallback instruction.
5. **A supplied-but-corrupt citation now RAISES.** Missing evidence (the
   caller never supplied a citation at all) is still genuine absence — a
   soft `UNKNOWN`. A SUPPLIED citation that turns out unresolvable,
   hash-mismatched, not a valid envelope, semantically mismatched, or
   not-yet-PIT-available now raises `PriceReflectionError` — it no longer
   silently blends into the same `UNKNOWN` bucket as "nothing was ever
   cited", so tampering attempts surface loudly and distinguishably.
6. **No currently real subject is unlocked by any of this.** No committed
   Event Evidence Envelope or P8-09 canonical record exists for BTC, any
   Korea ticker, TSM, or 034020.KS in this repo, and
   `decision/pilot_evidence_intake.py` never supplies `event_reaction`/
   `reflection_reference` for any of them — every real subject's
   `reflection_status` remains honestly `UNKNOWN`.

- **`price_state`** — `OVEREXTENDED | STRONG_MOMENTUM | MODERATE | WEAK |
  UNKNOWN`. A pure, price/volume-only read on momentum and positioning.
  Momentum alone can never produce a reflection verdict.
- **`reflection_status`** — `UNDER_REFLECTED | PARTIALLY_REFLECTED |
  FULLY_REFLECTED | UNKNOWN`. Only ever leaves `UNKNOWN` when a real
  **reference point** is present (see below) AND a comparable direction AND
  real momentum exist. Abundant, fresh, valid price data with NO reference
  point still forces `reflection_status=UNKNOWN` /
  `data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE` — momentum magnitude,
  however large, is never a substitute for a reference.

## `price_reflection/6` (CIO review round 6): production/test isolation and real provenance

Round 5 built a real, structured, content-verified Event Evidence Envelope,
but two production-boundary defects and one provenance gap remained:

1. **`REGRESSION_FIXTURE` was an accepted production `capture_kind`
   value.** `ALLOWED_CAPTURE_KIND` included it, so the committed
   `test/fixtures/event_evidence/*.json` files could drive a real
   `build_packet()` call to a non-`UNKNOWN` verdict. "It's not a current
   Pilot ticker" was never a real authority boundary — `329180.KS` is a
   real listed subject. Closed TWO independent ways: `ALLOWED_CAPTURE_KIND`
   is now `("LIVE_OFFICIAL_CAPTURE",)` only (not even a legal envelope
   value any more), AND, independently, `decision/event_evidence.py`'s
   `verify_event_reaction_claim`/`verify_expectations_gap_canonical_record`
   (the two functions the real `build_packet()` path calls) hard-refuse to
   even resolve a `source_ref`/`packet_ref` located under this repo's
   `test/` directory — a structural, path-based production/test boundary
   with no parameter anywhere that lets a caller opt out.
2. **`captured_at` was a self-declared backdate, not proven PIT
   availability.** Every fixture committed in the round-5 PR was first
   added to this repo's git history on 2026-08-23 yet declared
   `captured_at=2026-08-14` — the verifier trusted that field outright,
   the exact retroactive-creation problem this workstream exists to
   prevent. `_git_first_commit_timestamp` now queries this repo's REAL git
   history (`git log --follow --diff-filter=A`, offline, read-only) for
   the earliest commit that actually added the cited file, and THAT — not
   the self-declared field — is the authoritative gate:
   `first_authoritative_seen_at <= decision_at` is required, AND the
   self-declared `captured_at` may never precede
   `first_authoritative_seen_at`. Unavailable git history means
   `NOT_COMPUTABLE` (rejected), never a fallback to the self-declared
   value. Applied to both the Event Evidence Envelope and the P8-09
   canonical record.
3. **`citation` was an unconstrained dict that could contain only a free
   note.** An envelope asserting subject/event_at/direction was still just
   a newly-typed assertion, never evidence of one. `citation` is now a
   CLOSED schema: `raw_source_ref` + `raw_source_sha256` (a real,
   independently hash-verified raw artifact), `published_at` (the raw
   source's own real announcement timestamp, at-or-before the decision
   instant), `locator` (where in the document the claimed language
   appears), and `observed_fact` (the actual quoted text) — which must
   appear VERBATIM inside the raw source file's real decoded content. A
   bare free-text note no longer suffices; `direction` is only ever
   grounded in this observed, hash-verified, location-anchored quotation.
4. **The output packet now persists** `capture_kind`,
   `first_authoritative_seen_at`, and the full raw-source lineage
   (`raw_source_ref`/`raw_source_sha256`/`published_at`/`locator`)
   alongside the verdict — `validate_packet` re-asserts these as a closed
   vocabulary and an all-or-nothing field group, independent of how the
   packet was constructed, so a loaded packet cannot hide how (or whether)
   the verdict was genuinely obtained.

**Net effect**: until a genuine raw primary-source document is committed
for a real subject, no envelope can pass ALL of real `LIVE_OFFICIAL_CAPTURE`
classification + real closed-schema citation to a real raw artifact + real
git-provable first-availability at-or-before the decision instant — so
`LIVE_OFFICIAL_CAPTURE` remains genuinely unproducible for every real
subject in this repo today. Positive classifier arithmetic (return
computation, threshold classification) is still exercised in tests, but
strictly "below the production evidence boundary" — directly against
`decision/event_evidence.py`'s lower-level functions, or via an explicit,
test-only mock of the citation-verification step on a test file's own
loaded module instance — never by smuggling a test fixture through the
real `build_packet()` entry point, which structurally cannot be reached
with a `test/`-rooted citation at all.

## `price_reflection/6` provenance hardening (CIO review round 7)

Round 7 approved the round-6 test-only mock design outright ("normal
unit-test design... no change needed there") but found 4 P1 defects and 1
P2 remaining in the PRODUCTION provenance implementation itself — entirely
inside `decision/event_evidence.py`; this module's own public interface
(`verify_event_reaction_claim`/`verify_expectations_gap_canonical_record`'s
signatures and return shapes) is unchanged by round 7:

1. **Path-level first-add was insufficient.** A path added before
   `decision_at` could be MODIFIED after `decision_at`, and the verifier
   would read today's edited content while retaining the old file's
   original first-seen date. `_git_exact_content_first_seen` replaces the
   retired path-level function: it walks every commit that ever touched
   the path and finds the EARLIEST one whose git-recorded content is
   byte-for-byte identical to what's on disk right now. Editing a file
   always produces a brand-new first-seen date.
2. **The raw primary-source document had no git-availability check at
   all.** `_verify_raw_source_citation` now runs the SAME
   `_verify_first_availability` gate on the raw source file itself,
   treating `published_at` as its own "declared_at" subject to the
   identical real, content-addressed ordering check.
3. **A declared timestamp AFTER `decision_at` could still pass.** Round
   6's gate only checked that the declared value didn't precede the real
   first-seen time. Now enforces the full `first_seen <= declared_at <=
   decision_at` chain everywhere this gate runs (envelope, raw source, EG
   canonical record).
4. **A quoted phrase anywhere in the raw text was accepted regardless of
   its actual meaning** — an envelope could claim `direction=POSITIVE`
   while citing a "revenue decline" quotation and nothing caught it. There
   is no ratified NLP/sentiment derivation rule in this repo, so per the
   CIO's explicit alternative, the raw source document is now required to
   be real structured JSON carrying its own explicit, human-curated
   `observed_direction` field — an authoritative source schema field, not
   a free assertion — which must literally equal the envelope's claimed
   `direction`.
5. **`locator` was checked for non-emptiness only.** It must now name a
   real top-level key in the raw source's parsed JSON whose value
   genuinely contains `observed_fact`.
6. **Author time (`%aI`) was used**, a field freely backdatable by whoever
   writes the commit. Replaced with committer time (`%cI`) everywhere —
   still only an offline, local-repository signal, not a third-party-
   observed timestamp; a genuinely tamper-resistant bound would come from
   a server-side-observed timestamp (e.g. GitHub's own recorded commit
   time, or a signed append-only ingestion manifest) this module does not
   have offline access to.

## `price_reflection/6` provenance hardening (CIO review round 8)

Round 8 approved BOTH the round-6/7 test-only mock design AND round 7's
exact-content-addressed first-seen direction outright, but stress-testing
round 7's time-ordering rule against how evidence is actually collected in
the real world found 2 further P1 defects, again entirely inside
`decision/event_evidence.py` — this module's own public interface is
unchanged by round 8:

1. **Round 7's ordering was inverted for a raw source's `published_at`.**
   `_verify_first_availability` required `first_seen(raw file's OWN git
   history) <= published_at`, but a raw source's `published_at` is an
   external, real-world publication instant that legitimately, ALWAYS
   precedes when Atlas ingests/commits a copy of it — `published_at`
   (external) → `captured_at` (Atlas fetches/observes it) → git commit.
   Requiring `published_at >= git_first_seen` rejected virtually every
   genuinely legitimate citation. Fixed by modeling three separate clocks:
   `source_published_at` (external, self-declared, never git-checked
   directly), `captured_at` (Atlas's own fetch/observation time — this IS
   the value checked against git), and `exact_content_first_seen_at` (the
   conservative git-provable floor). `_verify_first_availability` now
   computes `effective_available_at = max(captured_at,
   exact_content_first_seen_at)` and only requires `effective_available_at
   <= decision_at` — a self-declared earlier `captured_at` can never make
   the effective availability earlier than git's real, provable floor
   (backdating is still structurally impossible), but a `captured_at` that
   is honestly later than the commit (the normal case) is no longer falsely
   rejected. Applied identically to the Event Evidence Envelope's own
   `captured_at`, the raw source citation's `captured_at`, and the P8-09 EG
   canonical record's `captured_at`.
2. **`observed_direction` compared one human-typed assertion to another.**
   The envelope's claimed `direction` was checked against a raw document's
   own `observed_direction` field — both ultimately typed by a human, never
   independent verification. Retired entirely. `citation.direction_origin`
   is now a closed, two-member vocabulary, and the raw source document must
   carry one of two closed, module-owned structures:
   - `OFFICIAL_STRUCTURED_FIELD` — `official_direction_field:
     {"provider_field", "provider_value"}`, looked up against
     `RATIFIED_OFFICIAL_DIRECTION_FIELDS` (keyed by `(source_class,
     provider_field, provider_value)`), a closed table this module owns —
     adding a real entry (naming a genuine official provider schema field)
     IS the ratification act. Starts empty: no real official-provider
     structured-field integration exists in this repo.
   - `RATIFIED_DERIVATION` — `direction_derivation: {"rule_id",
     "rule_version", "inputs"}`, looked up against `RATIFIED_DIRECTION_
     RULES` (keyed by `(rule_id, rule_version)`), each entry a pure
     function of real, structured numeric inputs. Same empty starting
     state.

   Both tables are intentionally empty in this module's real, committed
   source; positive-path mechanics are exercised only via
   `mocked_ratified_direction_tables()`, a test-only context manager that
   temporarily overlays entries onto a specific test-loaded module
   instance's tables, restored via `finally` — the same scoping discipline
   as `mocked_event_evidence_verification()`.

Additionally: `_verify_first_availability`'s NOT_COMPUTABLE error code is
now explicitly named `..._PROVENANCE_NOT_COMPUTABLE`, distinct from any
plain missing-price-data code — this feature structurally requires full git
history, and any operational workflow invoking it must use a full-history
checkout (`fetch-depth: 0`). `.github/workflows/actions-pass.yml` already
sets `fetch-depth: 0` (established for `replay/asset_identity.py`'s own
git-history backdating check), so this module rides the same gate with no
workflow change needed. A new, analogous ordering guard was also added at
the envelope level: `captured_at` may never precede `event_at` (mirroring
the citation's `published_at <= captured_at` check one level up).

## `price_reflection/6` authority-boundary hardening (CIO review round 9)

Round 9 approved round 8's 3-clock time model, exact-content provenance,
and the "mocks live only in test files" principle outright, but found 2
narrower authority-boundary defects, again entirely inside
`decision/event_evidence.py` — this module's own public interface is
unchanged by round 9:

1. **The test-bypass helper had leaked into production code.**
   `mocked_ratified_direction_tables()` (round 8) lived inside
   `decision/event_evidence.py` itself — a production module. Any
   operational caller could import it and inject a temporary official-field
   mapping or derivation rule at runtime, defeating the empty-table
   production lock. Removed entirely from `decision/`, along with the
   `contextlib` import that existed only for it. The equivalent helper now
   lives ONLY inside `test/test_price_reflection.py`, patching that file's
   own loaded module instance, restored via `finally` — the same scope as
   `mocked_event_evidence_verification()`. A structural test (`dir()`-based
   scan) confirms the production module exports no `mock`/`override`-like
   name and no public function accepts a rule-table-injection parameter.
2. **"Code was added" was being treated as equivalent to "CIO ratified this
   rule."** Round 8's own comments stated that adding an entry to a
   `RATIFIED_*` table WAS the ratification act — conflating IMPLEMENTATION
   (code that knows how a mapping/derivation would compute a direction)
   with AUTHORITY (a genuine Rule Authority decision that a rule is
   approved for operational use), bypassing the established Atlas P5 Rule
   Authority model. Split into three independent, all-empty tables:
   - `OFFICIAL_DIRECTION_FIELD_IMPLEMENTATIONS` / `DERIVATION_RULE_
     IMPLEMENTATIONS` — pure code, keyed the same way round 8's tables
     were, but each entry now also names the `(rule_id, rule_version)` it
     belongs to. Never itself authority.
   - `DIRECTION_RULE_AUTHORITY_REGISTRY` — keyed by `(rule_id,
     rule_version)`, a closed-schema authority record
     (`approval_status`/`ratified_at`/`evidence_ref`/`evidence_sha256`).
     `approval_status` is closed to `("RATIFIED", "PROPOSED")` — only
     `RATIFIED` unlocks operational use. `evidence_ref`/`evidence_sha256`
     are hash-verified against a real committed file exactly like every
     other citation in this module (tamper-evident).

   Operational lookup now requires a matching, cross-referenced entry in
   BOTH the relevant implementation table AND the authority registry — an
   implementation with no ratified authority record is unusable, and a
   ratified authority record with no matching implementation is equally
   unusable. No rule may become ratified merely because a developer commits
   code. All three tables remain intentionally empty in this module's real,
   committed source, so both `direction_origin` routes remain genuinely
   unproducible for any real subject today.

## Structurally price/volume/reference-point only — never fundamentals

The public builder, `build_packet(...)`, is a keyword-only function whose
entire parameter list is: `subject`, `decision_date`, `generated_at`,
`price_as_of`, `freshness_ceiling_days`, `relative_strength`,
`recent_return_windows`, `event_reaction`, `reflection_reference`,
`valuation_context`, `data_source_scope`, `contract`. There is **no**
"thesis quality" or "fundamental strength" parameter anywhere in that list,
and there never can be by accident: `test_price_reflection.py` inspects the
live function signature and fails the build if any parameter name contains
`thesis`, `fundamental`, `quality`, `conviction`, `narrative`, or `story`
(`FORBIDDEN_PARAMETER_SUBSTRINGS` in the module). Good fundamentals alone can
never produce `UNDER_REFLECTED` — the module has no channel through which
fundamentals could even arrive.

## Staleness overrides everything (Rule 1)

`price_as_of` plus a freshness ceiling is the load-bearing input. **Chosen
default: `price_as_of` older than 5 calendar days relative to
`decision_date` is STALE** (`default_freshness_ceiling_days: 5` in
`config/price_reflection_contract.json`). Callers may override per-call via
`freshness_ceiling_days`.

If `price_as_of` is missing, in the future relative to `decision_date`
(rejected outright as an anti-lookahead violation — this one raises, it does
not silently downgrade), or older than the ceiling, **both `price_state` AND
`reflection_status`** are forced to `UNKNOWN` and `confidence` is forced to
`UNKNOWN` — unconditionally, regardless of how strong every other input
looks. This check runs first and short-circuits everything else.

## The reference point requirement (Rule 2 — REMOVED, see scope reduction above)

Rounds 3-5 tightened this into a full evidence-verification apparatus
(`event_reaction.event_at`/`reflection_reference.expectations_gap_packet_
ref` reference points, `_resolve_reflection_basis`, `_compute_verified_
return`, `decision/event_evidence.py`'s Event Evidence Envelope
verification) — all of it has been **removed entirely** per the CIO's final
integration ruling on PR #212 (2026-08-23). There is no longer any
`event_reaction`/`reflection_reference` input parameter, and no reference
point of any kind can ever unlock a non-`UNKNOWN` `reflection_status` in
this module today.

`reflection_status` is the literal constant `"UNKNOWN"` in every packet
`build_packet()` can produce, unconditionally — `data_state` is
`REFLECTION_UNCERTAIN_WITH_VALID_PRICE` whenever price data is present and
fresh (never `VALID`, which remains a legal vocabulary member but is now
structurally unreachable through this module's own builder). This is the
round-2 fix taken to its current logical conclusion: BTC rallying hard is
real `price_state=OVEREXTENDED` evidence, but momentum alone was never a
reflection verdict, and this reduced scope no longer has ANY machinery that
could turn a momentum read into one — only a future, separately-designed
Reflection Evidence Authority (see scope-reduction section) can reintroduce
that capability.

Korea (`298040`/`267260`/`005930`/`000660`) and all 4 real Pilot subjects
(`TSM`/`298040.KS`/`267260.KS`/`034020.KS`) report real, honest
`price_state` from real momentum/relative-strength inputs, and honestly,
structurally `reflection_status=UNKNOWN` — not because no reference point
happens to exist today, but because there is no code path in this module
that could ever compute anything else.

## `data_source_scope` propagation

This module never claims market-wide price authority. `data_source_scope` is
a closed enum (`IEX_ONLY_PARTIAL_US_MARKET | KRX_OFFICIAL | KRAKEN_OHLC |
UNKNOWN`) that the **caller** declares — this module does not infer it. When
the caller's price input traces back to Alpaca/IEX (see
`config/free_market_data_contract.json`, scoped
`"IEX_ONLY_PARTIAL_US_MARKET"`) or Kraken OHLC, the caller must pass that
scope through verbatim; the module propagates it into the output rather than
silently dropping it or upgrading it to an implied market-wide claim.

## Vocabularies

- `allowed_price_state`: `OVEREXTENDED | STRONG_MOMENTUM | MODERATE | WEAK |
  UNKNOWN`. A rally alone (large 1-month return near a recent high, or
  paired with an expensive valuation-history position) produces
  `OVEREXTENDED` — entry-timing risk, not a rejection.
- `allowed_reflection_status`: `UNDER_REFLECTED | PARTIALLY_REFLECTED |
  FULLY_REFLECTED | UNKNOWN`.

There is no `REJECTED` value in either vocabulary — Rule/Portfolio rejection
is a different system's job.

**`price_state=OVEREXTENDED` means entry-timing risk is elevated. It does not mean the underlying business is bad, and it does not by itself mean `reflection_status=FULLY_REFLECTED`.** A company can be an excellent
business and still be `OVEREXTENDED` on price after a sharp run — this
status is about *when* to buy, not *whether* the company is good, and not a
claim about whether the market has priced in any specific expectation.

## `data_state`: real, distinct reasons behind a blanket `UNKNOWN`

`data_state` is a real, structured top-level field (round 1 encoded this as
a `reasons[0]=="DATA_STATE:..."` string marker as a stopgap to avoid
touching `decision/alpha_review.py`'s strict field-set check; round 2
updates that module directly instead — see its own docstring — so this is a
proper field now). Tracks `reflection_status` specifically: `VALID` iff
`reflection_status != "UNKNOWN"`.

- **`PRICE_DATA_MISSING`** — no price evidence exists for this subject/period
  at all (`price_as_of` was never supplied).
- **`PRICE_STALE`** — a `price_as_of` exists but is older than
  `freshness_ceiling_days` relative to `decision_date`.
- **`REFLECTION_UNCERTAIN_WITH_VALID_PRICE`** — `price_as_of` is fresh and
  valid, but either there is no reference point (see Rule 2 above) or not
  enough real momentum signal to render a reflection judgment even with one.
- **`VALID`** — `reflection_status` is one of the confident values. Remains
  a legal vocabulary member (no contract bump) but is currently
  **structurally unreachable** through this module's own `build_packet()`
  — see the scope-reduction section at the top of this document: the
  reference-point/citation-verification machinery that used to produce a
  confident `reflection_status` has been removed entirely. Reserved for a
  future, separately-designed Reflection Evidence Authority workstream.

## Threshold approval status (Rule 7)

`classification_thresholds` (the 15%/8%/3%/2%-style cutoffs) have never been
CIO-ratified. `classification_thresholds_approval_status` in the contract
says so explicitly (`"PROVISIONAL"`, one of `allowed_threshold_basis`), and
every output packet echoes it verbatim as `price_reflection.threshold_basis`
— tamper-evident via the packet hash, so no downstream consumer can silently
treat a provisional-threshold verdict as ratified. A `PROVISIONAL` basis is
not a defect (it is the honestly-true current state) but is a visible signal
that no `price_state`/`reflection_status` value this module emits is a
CIO-ratified final call — consistent with `authority.
rule_authority_substitution_authorized: false` below. Promoting
`classification_thresholds_approval_status` to `RATIFIED` requires an actual
CIO ratification decision on the specific cutoff numbers, not a code change.

**Round 3**: this used to be diagnostic-only in practice — `threshold_basis`
was surfaced but nothing downstream actually refused to act on a
provisional-threshold verdict. `decision/alpha_review.py` now gates its OWN
operational `opportunity_state` on it directly: a non-`RATIFIED`
`threshold_basis` is an independent trigger blocking any positive/
differentiated review state, so no such state can ever be unlocked by a
provisional-threshold `price_state`/`reflection_status` value. This
module's own output is unaffected — it still computes and reports the real
value either way.

**Round 4** (`alpha_review/5`, required item 6): a non-`RATIFIED`
`threshold_basis` used to collapse into the SAME `WAIT_FOR_PRICE` label as a
genuine `reflection_status=="UNKNOWN"` real-evidence gap — two structurally
different problems (no real price data yet vs. real price data with
unratified cutoffs) reported under one name. `decision/alpha_review.py` now
reports them as two distinguishable states: `WAIT_FOR_PRICE` remains
reserved exclusively for `reflection_status=="UNKNOWN"`; a non-`RATIFIED`
`threshold_basis` with a genuinely known `reflection_status` now reports as
the dedicated `WAIT_FOR_RULE_RATIFICATION` state instead. See that module's
own docstring and `docs/alpha_review_contract.md` for the full detail.

## Never a Rule verdict

No field in this module's output is named or shaped like a P5 Rule
PASS/FAIL result. `price_state`/`reflection_status`/`confidence` use a
vocabulary disjoint from `PASS`/`FAIL`/`REJECTED`/`BLOCKED`, and
`validate_packet` asserts neither vocabulary ever gains a `REJECTED`-shaped
value.

## Authority

```json
{
  "price_reflection_assembly_only": true,
  "rule_authority_substitution_authorized": false,
  "stage_promotion_authorized": false,
  "candidate_ready_buy_promotion_authorized": false,
  "rule_pass_fail_authorized": false,
  "action_authorized": false,
  "order_authorized": false,
  "production_authorized": false,
  "trading_authorized": false
}
```

## Real evidence sources (`decision/price_evidence.py`)

This builder never fetches evidence itself (see top of this doc); real
subjects are fed by `decision/price_evidence.py`, which assembles genuine
committed-repo evidence into `build_packet()` kwargs, reusing existing
collectors rather than inventing new external calls:

- **KRX daily closes** — `replay/price_series.py` + `replay/evidence_index.py`
  (built for PR #210's PIT replay audit, reused unchanged), merging every
  committed `data/<date>/krx.json` snapshot's embedded multi-week `daily`
  window. Covers `298040`/`267260`/`005930`/`000660`; `034020` has zero KRX
  evidence anywhere in this repo (confirmed, not assumed) and honestly
  returns `PRICE_DATA_MISSING`.
- **Korea KOSPI/KOSDAQ composite benchmark** — chain-linked from the real,
  committed `data/observations/korea_leadership_context/<date>/packet.json`
  `KOSPI_BENCHMARK`/`KOSDAQ_BENCHMARK` day-over-day `cumulative_gross_return`
  facts (P1-KR-07 real KRX Open API index data). This repo has never
  committed a raw KOSPI/KOSDAQ index price series (`korea_leadership_live_
  fetch.py` deliberately never persists raw index closes, only the outcome),
  so this chain-linked proxy is the only real, non-fabricated market-index
  series this repo's own evidence can support.
- **Korea market (KOSPI/KOSDAQ) membership** — `config/
  korea_market_membership.json`, an explicit, auditable canonical mapping
  with `source`/`observation_date`/`source_sha256`/`approval_status` per
  entry. Only `approval_status == "RATIFIED"` entries are ever used for
  `relative_strength.vs_market`; as of this build every entry is
  `UNRATIFIED` (no committed, hash-verified KRX Open API stock-master lookup
  exists confirming market venue per code yet), so `vs_market` is currently
  `None` for every Korea subject regardless of code. This replaced a round-1
  hardcoded `KOREA_STOCK_MARKET_MEMBERSHIP` dict the CIO correctly rejected
  as "a code comment is not real evidence."
- **BTC** — `replay/price_series.py`'s `build_btc_series` (Kraken OHLC, PR
  #210, unchanged) — ~720 real calendar days, genuinely supporting 1m/3m/6m
  windows. No separate crypto market-index series exists in this repo
  distinct from BTC's own price, so `relative_strength.vs_market` is left
  `None` rather than fabricated or made tautological (BTC vs BTC).
- **US single-name price** — `evidence/free_market_data/raw/<date>/
  manifest.json` (Alpaca IEX). Each day is a single most-recent-bar
  snapshot; with only one day committed as of this module's build,
  return-window/relative-strength fields are honestly left `None` rather
  than computed from one point — this widens automatically as the existing
  daily cron commits more days.

Every figure's evidence dates are checked with
`replay.lookahead_gate.assert_no_signal_lookahead` (reused unchanged from
PR #210) before being returned — see `test/test_price_evidence_lookahead.py`.

## Event Evidence Envelope verification (REMOVED — see scope reduction above)

`decision/event_evidence.py` (the Event Evidence Authority engine this
section used to document across rounds 5-9: Event Evidence Envelope
verification, raw-source citation schemas, the exact-content git-provenance
gate, the direction-origin implementation/authority-registry split) has
been **deleted from this repository entirely** per the CIO's final
integration ruling on PR #212 (2026-08-23) — see the "SCOPE REDUCTION"
section near the top of this document for why, and what a future,
separately-designed and separately-approved Reflection Evidence Authority
workstream will need to cover. `decision/price_reflection.py` no longer has
an `event_reaction`/`reflection_reference` input parameter at all.

## CLI

```bash
python decision/price_reflection.py /tmp/p8-10-input.json --out /tmp/p8-10-out.json
```

The input JSON is read as a single envelope and unpacked directly as
`build_packet(**envelope)` keyword arguments. Output is allowed only outside
the tracked repository.

# P8-08 Forward Thesis / Earnings Conversion Contract

This is a pure evidence-assembly and forward-inference packet builder. It sits
upstream of the P8-07 `Evidence -> Thesis -> Buy Review` slice
(`decision/investment_decision_review.py`). It does not import from or modify
that module, and a later PR — not this one — decides how (or whether) a
Forward Thesis Packet feeds into that review as one input among several.

This module does not decide Stage, does not produce a Rule PASS/FAIL result,
and does not authorize any trade, order, or production action. It also does
not perform ticker identity mapping — `atlas_linked_ticker` is validated only
as a non-empty string; resolving it to the canonical asset/security master
entry is the Global Asset Master's job, not this module's.

## Fact / inference separation

This is the load-bearing rule. `observed_facts[]` entries must carry a
`source_ref` that resolves to an entry in `evidence_lineage[]`, and their
`as_of` date must never be after `decision_date`. A statement about the
future belongs in `forward_inferences[]`, never in `observed_facts[]`. A fact
with no source, or with an `as_of` in the future, is rejected as a disguised
inference (`OBSERVED_FACT_SOURCE_REF_INVALID`,
`OBSERVED_FACT_SOURCE_REF_DANGLING`, `OBSERVED_FACT_AS_OF_AFTER_DECISION_DATE`).

## No future timestamps (anti-lookahead)

`generated_at`, every `observed_facts[].as_of`, and every
`evidence_lineage[].filing_date` must be `<=` a caller-supplied
`as_of_ceiling` (an optional field on the raw input document only — it is
**not** a persisted packet field) or `<=` `decision_date` when no ceiling is
given. `validate_packet()` re-derives the packet from its own content using
`decision_date` as the ceiling; this is always sound because the
as_of-vs-decision_date check is enforced unconditionally at build time
regardless of what ceiling was originally supplied.

## Earnings-conversion vocabulary is closed, and open-ended by design

`earnings_conversion.status` must be one of:

```
PRE_REVENUE_SIGNAL, BACKLOG_BUILDING, REVENUE_CONVERSION_EXPECTED,
MARGIN_CONVERSION_EXPECTED, CONVERSION_CONFIRMED, CONVERSION_DISAPPOINTED,
UNKNOWN
```

There is **no gate** requiring `CONVERSION_CONFIRMED` (or any other status)
for a packet to build successfully. Early-stage, low-confidence, or even
disappointing theses build exactly the same as a confirmed one — this module
labels state honestly and has zero opinion on what is "allowed" downstream.
`earnings_conversion.confidence` (and every `forward_inferences[].confidence`)
must be one of `LOW/MEDIUM/HIGH/UNKNOWN` and is a required field — there is no
default, so confidence can never be silently upgraded to `HIGH`.

## Capital commitment: never a fabricated precise number

`capital_commitment.amount_range_or_unknown` must be either the literal
string `"UNKNOWN"`, a value containing a visible range separator (`-`, `~`,
`..`, `" to "`, `±`, …), or accompanied by a non-null `source_ref`. A bare
precise figure with no source and no range/UNKNOWN marker is rejected
(`CAPITAL_COMMITMENT_PRECISE_FIGURE_WITHOUT_SOURCE`).

## Invalidation conditions must be non-empty

A thesis with zero `invalidation_conditions` is a contract violation
(`INVALIDATION_CONDITIONS_EMPTY`), regardless of how strong the rest of the
evidence is.

## Evidence lineage

`evidence_lineage[]` entries reuse the same field shape as
`bridge/evidence_envelope.py`'s `SOURCE_IDENTITY_FIELDS` (`accession`,
`filing_date`, `exhibit_type`, `exhibit_document`, `source_sha256`) for
`SEC_EXHIBIT`/`DART_EXHIBIT` sources — all five fields required, and
`filing_date` is future-checked against `decision_date`/`as_of_ceiling` the
same way `observed_facts[].as_of` is. `NARRATIVE_SOURCED`/`PRICE_FEED`/`OTHER`
sources use a simpler shape: the four SEC/DART identity fields must be null,
`source_sha256` is optional, and `filing_date` — if present — is still
future-checked. Every `observed_facts[].source_ref` must resolve to some
`evidence_lineage[].source_ref`; a dangling reference is rejected.

## Tamper detection and determinism

`validate_packet()` independently rebuilds the packet from its own declared
content (see the anti-lookahead note above) and rejects any packet whose
content doesn't match the fresh rebuild, or whose `packet_sha256` doesn't
match a recomputed hash of the unsigned payload — recomputing the hash alone
can never legitimize a changed result. `canonical_json()` is
`json.dumps(..., sort_keys=True, separators=(",", ":"))`, so byte-identical
input always produces byte-identical output.

## Authority

```json
{
  "thesis_assembly_only": true,
  "forward_inference_generation_authorized": true,
  "stage_promotion_authorized": false,
  "candidate_ready_buy_promotion_authorized": false,
  "rule_pass_fail_authorized": false,
  "rule_result_generation_authorized": false,
  "ticker_identity_mapping_authorized": false,
  "action_authorized": false,
  "order_authorized": false,
  "production_authorized": false,
  "trading_authorized": false
}
```

This module *does* generate forward-looking inferences
(`forward_inference_generation_authorized: true`) — but only as clearly
labeled inference in `forward_inferences[]`, never as fact. Every other
authority flag stays closed: no Stage/Candidate/Ready/Buy promotion, no Rule
PASS/FAIL, no ticker mapping, no action/order/production/trading.

## CLI

The CLI accepts one input JSON document (the caller-assembled thesis: facts,
inferences, earnings-conversion narrative, evidence lineage, and an optional
`as_of_ceiling`) and `--out` for the output packet path. This module does not
fetch evidence itself — only assembles and validates what it is given. Output
is allowed only outside the tracked repository:

```bash
python decision/forward_thesis.py /tmp/p8-08-input.json \
  --out /tmp/p8-08-forward-thesis.json
```

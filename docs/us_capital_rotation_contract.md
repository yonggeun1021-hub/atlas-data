# P2-02 US Capital Rotation contract

Status: offline transform capability; live US price/breadth, ratified operating
taxonomy population, state ledger, and briefing integration remain open.

## Reused upstream observation

The transform consumes two exact `us_leadership/v1` derived packets. It does
not receive or retain vendor price rows and does not recalculate Leadership.
Both packets must be forward-PIT-qualified, use the same benchmark, lookback,
effective Theme set, and exact upstream taxonomy policy hash, and keep every
upstream classification and action authority false.

Before extracting Theme rows, P2-02 invokes the P1-US-06 production
`validate_output()` helper. That validator recomputes asset/group relative
strength, daily participation fractions, group minimum coverage, temporal
status, retention, lineage shape, and closed authority from the fields retained
in each non-reconstructive Leadership packet. A structurally plausible but
semantically inconsistent upstream packet therefore fails before rotation.

## Taxonomy and policy binding

No US Theme, benchmark, lookback, ranking count, or transition cadence is a
repository default. An external policy must identify the exact P2-01 taxonomy
decision and packet SHA, the upstream Leadership taxonomy-policy SHA, the full
Theme set, TOP/BOTTOM counts, and maximum observation gap. The policy must have
been ratified before the prior observation was available and be effective over
both observations.

An unratified or not-yet-effective policy preserves the two raw group-relative
strength values and their change, but emits no rank, bucket, or transition.

## Deterministic rotation output

With an effective policy, Themes are ordered by group relative strength versus
the common benchmark, descending, with Theme ID ascending as the explicit tie
break. TOP and BOTTOM sets are disjoint. Every Theme receives prior/current
rank and bucket plus a structural `PRIOR_BUCKET_TO_CURRENT_BUCKET` transition.
The output is alphabetical by Theme ID; ranking is exposed only through
explicit rank fields and TOP/BOTTOM lists.

`validate_packet()` independently rechecks the output policy/taxonomy binding,
effective interval, Theme set, numeric change, rank and tie-break order,
TOP/MIDDLE/BOTTOM assignment, transitions, summary lists, closed authority,
lineage, retention, and packet hash. A self-rehashed rank, bucket, delta, or
authority mutation therefore fails closed. The packet does not embed the two
complete Leadership inputs -- retaining full upstream rows would violate this
module's own `output_retention_policy` -- but `observation_pair` (schema
`us_capital_rotation_packet/2`) persists each observation's own `available_at`
alongside its date. `validate_packet()` re-parses both timestamps, requiring
them present, ISO8601, and timezone-aware, and independently re-derives
prior-before-current order, the effective interval covering both
observations, and ratified-before-prior-observation from those persisted
values alone -- with no live source pointer, current file, or monkeypatch --
so a revision's own packet remains standalone-reprovable even after live
source state moves on, and a self-rehashed tamper of any of these facts
(order, gap, ratification timing) fails closed.

These bucket transitions are not `EMERGING`, `STRONG`, or `WEAKENING`. The P2
state vocabulary and transition ledger belong to P2-05 and remain undefined.

## Authority and operation

The capability does not authorize benchmark selection, lookback selection,
Theme inference, state vocabulary, Regime input, candidate ranking, Stage,
Production, or trading. It is offline and writes only to an explicit path
outside the repository. Live source population and briefing use remain later
gates.

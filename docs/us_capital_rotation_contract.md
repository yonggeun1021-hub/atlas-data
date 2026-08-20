# P2-02 US Capital Rotation contract

Status: offline transform capability; live US price/breadth, ratified operating
taxonomy population, state ledger, and briefing integration remain open.

## Reused upstream observation

The transform consumes two exact `us_leadership/v1` derived packets. It does
not receive or retain vendor price rows and does not recalculate Leadership.
Both packets must be forward-PIT-qualified, use the same benchmark, lookback,
effective Theme set, and exact upstream taxonomy policy hash, and keep every
upstream classification and action authority false.

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

These bucket transitions are not `EMERGING`, `STRONG`, or `WEAKENING`. The P2
state vocabulary and transition ledger belong to P2-05 and remain undefined.

## Authority and operation

The capability does not authorize benchmark selection, lookback selection,
Theme inference, state vocabulary, Regime input, candidate ranking, Stage,
Production, or trading. It is offline and writes only to an explicit path
outside the repository. Live source population and briefing use remain later
gates.

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

With an effective policy -- and, on the `theme_taxonomy/2` path, an
effective-dated taxonomy source behind it -- Themes are ordered by group
relative strength versus
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

## Existing Theme taxonomy v2 consumer path

The legacy `theme_taxonomy/1` opaque binding remains byte-compatible: it still
carries four unresolvable taxonomy identity strings, still refuses a graph, and
still produces identical packets.

A binding for the existing producer's `theme_taxonomy/2` contract now requires
the real `theme_taxonomy_input/1` source through `--taxonomy-graph` (or the
`taxonomy_source_bytes` build argument). The consumer rebuilds that source with
`rotation.theme_taxonomy.build_packet`, including its existing independent
Git-provenance authority resolution, and compares taxonomy identity, decision
identity, decision hash, packet hash and decision date. The producer's accepted
contract version is read from that producer's own committed contract, never
duplicated here. Nothing the caller declares about authority, hash or status is
trusted: relabelling a `/1` binding as `/2`, editing the declared identity or
packet digest, tampering with the graph semantically, or changing only the
source bytes are each rejected.

Rotation policy Theme ids must exist as active nodes in that exact graph on
*both* observation dates, using the producer's own interval semantics
cross-checked against its own `active_node_count` on the decision date. A node
that only became valid after the prior observation did not exist as a Theme
identity on the date the prior rank and bucket are read from, so it fails
closed as `TAXONOMY_THEME_NODE_NOT_ACTIVE_AT_PRIOR`; the decision-date verdict
keeps its original `TAXONOMY_THEME_NODE_NOT_ACTIVE` code unchanged. In US the
rotation Theme id *is* the upstream
Leadership `group_id`, so this is direct referential integrity with no
Korea-style series-to-Theme proxy mapping; US keeps its own single common
benchmark, group relative-strength metric, `THEME_ID_ASC` tie break and
`GROUP_RELATIVE_STRENGTH_VS_BENCHMARK` ranking. It infers no security
membership and changes no market-native classification.

`upstream_taxonomy_policy_sha256` is a separate US-native fact from the P2-01
graph -- it is the effective-dated taxonomy policy hash each `us_leadership/v1`
packet declares -- and both Leadership observations are still bound to it
exactly as before. The P1-US-06 production `validate_output()` re-derivation
still runs ahead of rotation on the v2 path.

The v2 binding retains the exact public source JSON text and SHA-256, graph
status, authority-resolution status and membership-authorization result.
Packet-only validation, including the common Rotation State Ledger consumer,
rebuilds the embedded source and rechecks those derived fields; an external
source supplied at validation must match the embedded bytes exactly. Re-signing
a false membership/authority assertion, a false source digest, or another day's
graph is rejected. No file path is trusted as the graph, and the CLI still
refuses tracked output.

The empty repository authority registry remains non-authorized, so a
structurally valid ratification claim is recorded exactly as
`STRUCTURALLY_VALID_RATIFICATION_CLAIM_NOT_AUTHORIZED` with
`theme_membership_authorized` false, and is never upgraded.

### Taxonomy source effectivity is a separate, subtractive gate

Ranking is granted solely by the externally supplied rotation policy, and the
policy is never widened: what is ranked, the metric, tie break, TOP/BOTTOM
counts and the maximum observation gap all still come only from it, and an
unratified or not-yet-effective rotation policy emits no rank, bucket or
transition even when a real graph was consumed.

An effective rotation policy is nevertheless not sufficient on the v2 path. A
`theme_taxonomy/2` binding whose re-derived producer verdict is
`DRAFT_OR_NOT_EFFECTIVE_GRAPH` has no effective-dated taxonomy source behind
the Theme vocabulary at all -- the source's own approval is explicitly
`UNRATIFIED`, or is ratified but not yet in force or already expired on this
decision date. Such a packet emits no rank, bucket, transition, TOP/BOTTOM
list or `ranking_method`, and `theme_ranking_authorized`,
`top_bottom_bucket_authorized` and `bucket_transition_authorized` are all
false, however ratified and covering the rotation policy is. Its status is
`TAXONOMY_SOURCE_NOT_EFFECTIVE`, and the shared Rotation State Ledger -- which
admits only `ROTATION_BUCKETS_OBSERVED` -- therefore refuses it as well.

### The taxonomy source must be a fact on both observations, not only today

The producer resolves a graph on one decision date, and that date is this
rotation's *current* observation. A source can therefore be a fully effective
document on the decision date and still be a strictly later fact than the prior
observation -- which is the date the prior rank, the prior bucket and the whole
`PRIOR_BUCKET_TO_CURRENT_BUCKET` transition are read from. Using it there would
let a later taxonomy fact classify an earlier operational observation, which
the point-in-time control forbids outright.

So the consumed source's own approval must additionally be in force on the
prior observation date, and -- exactly as the rotation policy has always been
required to be -- an approval window that does cover the prior observation must
have been ratified no later than that observation's own `available_at`. Both
reuse facts that already exist: the source approval's own effectivity interval
and this module's existing prior-available ratification boundary. Neither
introduces a TTL, a freshness window, or any new policy, source or authority.

A source whose window simply starts after the prior observation is an honest
document that did not yet exist then, so it withholds ranking through the same
subtractive gate and the same `TAXONOMY_SOURCE_NOT_EFFECTIVE` status. A source
that claims to have covered the prior observation but was ratified after it was
already available is incoherent backdated evidence, so it fails closed as
`TAXONOMY_RATIFIED_AFTER_PRIOR_OBSERVATION` -- the same distinction, and the
same instant-granularity comparison, that `POLICY_RATIFIED_AFTER_PRIOR_
OBSERVATION` already draws for the rotation policy. A ratification later on the
same calendar day as the prior observation's availability is refused.

`validate_packet()` re-derives all of this for itself, from the approval window
and node intervals of the embedded source it re-ran the real producer over and
from the packet's own persisted `prior_date` and `prior_available_at`. A packet
carrying prior ranks, prior buckets or transitions over a source that was not a
fact on its own prior observation therefore fails closed even when every digest
in it, including `payload_sha256`, has been re-signed. As before, the packet
does not retain the two upstream Leadership packets, so the observation pair a
packet declares is the pair every re-derivation is proved against; that
retention boundary is unchanged and is shared with the existing rotation-policy
re-proof.

This gate is only ever subtractive: it withholds ranking and never grants it,
never upgrades the producer's verdict, and creates no membership. It is exactly
the producer's not-effective verdict plus that verdict's own point-in-time
extension to the prior observation, and nothing wider -- so a real
effective-dated document that covers both observations and that the separate
approval-authority registry does not authorize -- the current repository state
-- still ranks under an effective policy exactly as before. Authorization
remains a different question from effectivity and is still never claimed here.
The decision-date verdict is taken from the producer's own `graph_status`,
cross-proved against its own `structurally_eligible_ratification_claim`, so an
unrecognised or internally inconsistent status fails closed rather than
defaulting to effective; a producer verdict presented without the source facts
it was derived from fails closed as `TAXONOMY_SOURCE_FACTS_MISSING`.

`rotation_policy_effective` keeps its original meaning -- the rotation policy's
own ratification and coverage -- and stays true when only the taxonomy source
withheld ranking, so the two gates remain separately auditable. When both are
shut the pre-existing `POLICY_NOT_EFFECTIVE` status is the one reported.
`validate_packet()` re-derives this gate for itself from the re-run producer,
so a packet carrying ranks over an ineffective source fails closed even when
every digest in it, including `payload_sha256`, has been re-signed.

Because the packet field set does not change, the output stays
`us_capital_rotation_packet/2` with an optional v2 binding variant, preserving
existing packets; `validate_packet()` enforces exactly one of the two binding
shapes. The legacy `/1` binding carries no producer verdict to check -- exactly
the opacity the v2 path exists to close -- so the gate is inert for it and `/1`
packets are unchanged. `status`/missing/empty/unratified/future fail-close is
otherwise unchanged.

This does not migrate the default `/1` configuration or any source-population
registry pin, and it does not ratify graphs, populate memberships, ingest Global
Asset Master data, claim a natural sample, or unlock candidate/Stage/Regime/
Production/order/trading authority.

Validation uses synthetic graph and Leadership fixtures through the real
producer and US consumer. The synthetic positive graph's `ratified_at_utc`
moved from `2026-08-19T12:00:00Z` to `2026-08-18T12:00:00Z`. That fixture
always declared a taxonomy already ratified and in force before both
observations -- its `effective_from` is `2026-08-01`, and the rotation policy
fixture beside it states the same intent with `2026-08-17T12:00:00Z` -- but the
instant it carried was later than the prior observation's own `available_at`
(`2026-08-19T00:20:00Z`), so it silently contradicted that intent and only
passed because the prior observation was never checked. Only the instant moved,
and only far enough to satisfy the temporal intent already declared; the
approval window, decision identity, nodes, edges, memberships, evidence and
every US-native leadership/benchmark/grouping value are unchanged, and the
existing negative fixtures (`UNRATIFIED`, not-yet-effective, expired, missing
node, lapsed node, another day's graph, semantic and byte-only source tamper,
relabelled `/1`, forged authority) reproduce exactly as before. Operational
completion still requires an actual canonical graph/source and an existing
ratified rotation policy to pass this path in a natural run; engineering
integration is not that completion.

## Authority and operation

The capability does not authorize benchmark selection, lookback selection,
Theme inference, state vocabulary, Regime input, candidate ranking, Stage,
Production, or trading. It is offline and writes only to an explicit path
outside the repository. Live source population and briefing use remain later
gates.

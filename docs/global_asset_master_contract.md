# Global Security / Asset Master Contract (P3-01)

Status: cross-market identity capability implemented; market-specific universe
policies, the theme taxonomy, and a populated live master remain unratified or
unimplemented. There is no universe approval, investability, Production, or
trading authority.

## Purpose and boundary

`universe/global_asset_master.py` validates one caller-supplied identity packet
for US equities, Korea equities, and Crypto assets. It gives those records one
common shape: stable Atlas asset ID, market, asset class, primary symbol,
exchange, quote currency, source identifiers, aliases, and memberships.

This is an identity registry, not an asset-selection engine. The helper does
not read `config/universe.json`, call a provider, discover an asset, infer that
two records are the same, infer a theme, approve a universe, rank candidates,
promote an Atlas stage, or create an action. Every identity and membership must
be supplied explicitly by the caller with source lineage.

The legacy Korea watch list in `config/universe.json` and the effective-dated
Kraken exception table remain unchanged. Migrating either one into a global
master requires a separate, reviewed population decision.

## Identity and temporal contract

The stable `asset_id` is opaque to the validation logic. Human-readable
attributes can change without silently changing that identity. A record keeps:

- `primary_symbol`, `exchange_id`, and `quote_currency`;
- namespaced external identifiers;
- symbol aliases with explicit exchange and effective range;
- `MARKET`, `THEME`, and `UNIVERSE` memberships with effective range; and
- immutable source ID, URL, body SHA-256, source availability, and retrieval
  time for the record and every effective-dated assertion.

All ranges use `[valid_from, valid_to)`. The output preserves history and emits
the aliases and memberships active on the packet's explicit `as_of_date`.
The primary symbol/exchange and matching market membership must be active on
that date. No current catalog is carried backward to invent historical
membership.

## Fail-closed conflicts

The builder rejects rather than resolves:

- duplicate Atlas asset IDs;
- two assets with the same primary exchange/symbol;
- one namespaced external identifier assigned to two assets;
- overlapping ownership of the same exchange/symbol alias;
- overlapping duplicate alias or membership ranges within a record;
- missing, unknown, malformed, or temporally impossible source lineage; and
- a market/asset-class mismatch.

Non-overlapping alias reuse is representable because both ownership ranges are
preserved. Automatic merging is prohibited: a collision is evidence that a
human-reviewed identity decision is still required.

## Determinism and output safety

Input records, identifiers, aliases, and memberships are canonicalized by
stable keys. Equivalent permutations produce the same packet and packet
SHA-256. The CLI writes only a caller-requested `--out` path, using a staged
file and atomic replace. Validation completes before publication, so an invalid
input cannot overwrite an existing output.

`validate_packet()` also validates a persisted output independently of the
builder call. Because every source identity, alias interval, and membership
interval is retained in the packet, it re-runs the production record and
cross-record validators to derive active aliases and memberships, closed
authority fields, canonical ordering, and collision decisions. Recomputing the
packet SHA-256 after changing one of those values does not make the packet
valid. This is a structural and semantic integrity check of retained evidence;
it does not prove that a provider response was complete or authorize a live
master population.

Example (input intentionally omitted because no live master is approved):

```bash
python3 universe/global_asset_master.py /tmp/asset-master-input.json \
  --out /tmp/asset-master.json
```

Every output record fixes these values:

- `universe_approved = false`;
- `investable_eligible = false`; and
- `stage_transition = null`.

The packet also preserves the contract's `UNRATIFIED` universe and theme
boundaries. A later populated master or market policy must be reviewed as a
separate WBS change; this capability cannot grant that authority itself.

## Optional explicit THEME source-binding check

`validate_theme_source_binding()` is an optional, read-only pre-ingestion
check. It answers one narrow question: does a caller-named THEME membership in
a Global Asset Master document bind exactly to a caller-named membership and
evidence row in an externally ratified Theme taxonomy graph? It is not
ingestion, migration, population, or a new CLI. The builder, `validate_packet`,
the input and output schemas, and the existing command line are unchanged, and
the rotation module is imported only when this function is called.

The caller supplies the original master (input or packet), the original
taxonomy graph document, and one explicit reference per binding:

```text
{"asset_id", "gam_membership_id", "taxonomy_membership_id", "evidence_id"}
```

Every reference must be complete; an empty or partial binding list is rejected.
Nothing is matched by symbol, display name, or resemblance. Both sides are
re-derived here by their own production validators — the master through
`build_master()` or `validate_packet()`, the graph through
`theme_taxonomy.build_packet()` against the committed authority registry at an
immutable `trusted_commit`. A `status`, `payload_sha256`, `approval`, or
authority flag carried inside a caller document is never accepted as truth, so
a rehashed forgery on either side fails.

That `trusted_commit` is a required caller input, not a convenience. The check
never falls back to the working tree's current `HEAD`: the authority boundary a
binding is judged against must be named by the caller and must not move between
calls. A missing pin is `BINDING_TRUSTED_COMMIT_REQUIRED`, and anything that is
not a full lowercase 40- or 64-hex object name — `HEAD`, a branch, a tag, an
abbreviated SHA — is `BINDING_TRUSTED_COMMIT_INVALID`. Both are raised before
either caller document is examined.

A binding is positive only when all of these hold on one shared `as_of_date`:

- the taxonomy graph is currently effective *and* independently authorized by
  the committed registry;
- the master record's `asset_id` and `market` equal the taxonomy membership's;
- the master THEME `membership_id` equals the taxonomy `theme_id`;
- both `[valid_from, valid_to)` intervals are identical and active;
- the master membership's `source_url` and `source_sha256` equal the named
  evidence row's; and
- the two source-identity labels were actually comparable, so
  `source_id_comparison` is `COMPARED`.

Everything else fails closed and is reported with an exact reason: a missing
asset, membership, or evidence row; an unratified, empty, expired, backdated,
or point-in-time-violating authority; a future or lapsed membership; and any
asset, market, theme, interval, or source-document mismatch. One
`membership_id` repeated across non-overlapping master history is reported as
`GAM_THEME_MEMBERSHIP_AMBIGUOUS` rather than resolved to a winner.

Where the two contracts do not define a comparison, the report says so instead
of inventing a conversion. `THEME_IDENTITY_COMPARISON_UNDEFINED` is returned
when a taxonomy `theme_id` cannot even be expressed as a master membership ID,
and `EFFECTIVE_INTERVAL_SEMANTICS_UNCOMPARABLE` when the two contracts stop
declaring the same interval convention. The taxonomy's evidence `role_id`,
`claim_text`, `audit_provenance`, and the full `evidence_ids` list are carried
into the report rather than dropped, and `comparison_basis` names every field
that was compared and every field that was deliberately preserved without
comparison.

One such field is `source_id`. The master's `source_coverage` and the
taxonomy's `market_sources` are disjoint retrieval-channel registries with no
ratified cross-mapping, so requiring them to be equal would itself be an
invented conversion — and so would treating two labels from unrelated
registries as agreeing. Document identity is therefore still decided by the
absolute `source_url` and `source_sha256`, both channel labels are preserved
verbatim, and each binding records `source_id_comparison`. When that comparison
is undefined, the binding reports
`SOURCE_ID_COMPARISON_UNDEFINED:<master_label>:<evidence_label>` as an
unresolved reason and is never `verified`, exactly as an undefined theme
identity or interval convention is. Under the currently ratified pair of
contracts the shared registry is empty, so the strongest available answer for
an otherwise exact binding is `THEME_SOURCE_BINDING_UNRESOLVED`: every defined
comparison held and the undefined one is named rather than assumed. If the two
registries ever ratify a shared source ID, that shared vocabulary is compared
for equality and a positive result becomes reachable.

A non-negative result means an exact binding was checked and nothing else. The
report fixes `master_population_authorized = false`, repeats the contract's
unchanged all-false authority block, and keeps
`THEME_MEMBERSHIP_INGESTION_NOT_IMPLEMENTED` and
`SOURCE_ID_REGISTRY_CROSS_MAPPING_UNRATIFIED` in its unresolved boundaries. It
does not populate the master, create a membership, approve a universe, grant
investability, or move a Stage. Live membership migration remains a separate
reviewed decision that this check does not close.

## Committed population readiness

`universe/global_asset_master_population_readiness.py` inventories the latest
committed market population on or before an explicit date. It independently
rebuilds the US population from its immutable Nasdaq raw archive and invokes
the existing Crypto population builder against the latest Kraken archive. It
does not treat a directory or self-rehashed JSON as proof: the retained packet
must equal the production builder's fresh re-derivation.

Directory dates are never treated as historical knowledge time. The exact
current bytes of the US population packet or Crypto raw manifest must appear in
real git history by the end of the requested UTC `as_of_date`; later backfills
are excluded. Each eligible market row retains that exact-content first-seen
commit and committer timestamp. Full git history is therefore a fail-closed
runtime prerequisite for this audit/readiness command.

As of 2026-08-26 the repository truth is intentionally incomplete: US source
coverage is populated, Crypto remains blocked by the existing breadth/taxonomy
coverage gate, and no exact KRX Global Master population packet is committed.
The readiness result is therefore `BLOCKED_SOURCE_COVERAGE_INCOMPLETE`. This is
not a claim that US rows are investable, nor permission to infer a freshness
window. Universe approval, investability, Stage, action, order, Production,
and trading authority remain false.

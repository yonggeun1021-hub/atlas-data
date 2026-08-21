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

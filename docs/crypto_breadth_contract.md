# Crypto Breadth / Alt Participation Contract (P1-CR-06)

Status: source, replay, and daily capture contracts implemented; the universe
rule is ratified effective 2026-08-19; the first-qualified-live-Top-100 Exit
Gate is satisfied by the committed natural 2026-08-30 and 2026-08-31 capture
chains.  There is no classification, Regime, Production, or trading authority.

The Exit Gate is capability evidence, not a promise that every later daily
snapshot will remain qualified.  The 2026-09-01 and 2026-09-02 chains returned
to `TAXONOMY_COVERAGE_UNKNOWN` as new cutoff-relevant identities appeared. That
point-in-time result does not revoke the already-proven capture/validation
capability, and it must not be relabeled as a neutral or complete market view.

## Purpose

This contract makes one narrow observation reproducible: for an exact UTC day,
did BTC advance while the assets admitted by the same-day Crypto breadth
universe advanced, declined, or stayed unchanged?  The output is raw counts and
fractions.  It is not a breadth regime, risk-on/risk-off label, score, or order
input.

The source is Kraken Spot public market data:

- `Assets?assetVersion=1` captures the asset catalog and status;
- `AssetPairs?assetVersion=1&aclass_base=currency` captures tradable pairs;
- `OHLC?pair={PAIR}&interval=1440&since={SINCE}&assetVersion=1` captures each
  candidate USD pair's daily close, VWAP, and base volume series.

Primary source documentation:

- https://docs.kraken.com/api-reference/market-data/get-asset-info
- https://docs.kraken.com/api-reference/market-data/get-tradable-asset-pairs
- https://docs.kraken.com/api-reference/market-data/get-ohlc-data

Kraken documents that OHLC returns no more than 720 recent entries and always
includes the current, not-yet-committed timeframe.  The helper therefore
removes the final row for every pair.  It never treats the current candle as
evidence.

Kraken can represent an interval with no trades as a flat OHLC row whose VWAP,
base volume, and trade count are all zero.  Atlas accepts that sentinel only
when all four OHLC prices are identical and all three activity fields are zero.
A zero VWAP with any reported activity, a partially zero activity tuple, or a
non-flat no-trade row fails closed.  This source-shape rule does not change the
existing rule that the final current row is always excluded from observations.

## Point-in-time universe boundary

`Assets` and `AssetPairs` are current catalogs, not a historical membership
service.  A catalog fetched today must never be used to reconstruct yesterday's
eligible assets.  A replay point is valid only when that point has its own
append-only date directory:

```text
{snapshot_root}/{UTC_VINTAGE_DATE}/
  _downloaded_at.txt
  _sha256.txt
  _manifest.json
  kraken_assets.json.gz
  kraken_asset_pairs.json.gz
  kraken_ohlc_responses.ndjson.gz
```

The OHLC bundle has one sorted NDJSON record per pair containing the pair ID,
the source-body SHA-256, and the exact source body in base64.  This preserves
every response byte while avoiding roughly 630 separate Git files per day.
The outer checksum and every inner checksum are verified.  The manifest binds
the exact raw bytes, catalog counts, OHLC pair list, source semantics, and
identity-exception policy.  Re-running `manifest` for the same snapshot is an
append-only violation.  `replay` reads independent daily snapshots; it does
not carry the newest catalog backward.

## Identity and rename/reuse

Pair display text and ticker aliases are not identity.  The effective-dated
table `config/crypto_asset_identity_exceptions.json` maps a Kraken
`source_asset_id` to a stable `canonical_asset_id`.  Overlapping ranges fail.
If two simultaneously selected source assets resolve to one canonical asset,
the transform also fails instead of double-counting it.  A later rename or
ticker reuse requires a new non-overlapping record and a new immutable policy
version; silent edits are forbidden.

Kraken's `assetVersion=1` returns display identifiers such as BTC and USD while
the source documentation also shows legacy XXBT/XBT identifiers.  The v1 table
records that known BTC alias boundary explicitly.  It does not infer identity
by removing X/Z prefixes or by parsing a pair string.

## Ratified universe rule

`config/crypto_breadth_universe_policy.json` is `RATIFIED` effective
2026-08-19.  For an observation at T, membership is selected as follows:

1. use enabled assets and online Kraken Spot pairs quoted in USD;
2. for each candidate, sum `daily VWAP × base volume` over the exact 30
   finalized UTC days ending at T-1;
3. rank descending by that USD turnover, with canonical asset ID and pair ID
   as deterministic tie-breakers;
4. apply the versioned taxonomy in
   `config/crypto_breadth_exclusion_taxonomy.json` and exclude fiat,
   stablecoin, wrapped, staked, commodity-linked, and unverified-identity
   assets;
5. select the first 100 eligible assets.  BTC participates in selection but is
   emitted only as a reference and is excluded from the Alt breadth fraction.

The T-1 ranking endpoint prevents the T price move being measured from choosing
its own membership.  A pair without exact 30-day ranking history is explicitly
rank-ineligible.  An unclassified asset encountered before the 100th eligible
member makes the whole result `UNKNOWN`; it is never included by default. An
explicitly `unverified_identity`-classified asset (policy_version v2,
2026-08-22 -- canonical on-chain/project identity could not be confirmed by
two independent sources) is structurally different: it is a real, ratified
exclusion, so the ranking loop skips it and keeps going, exactly like any
other excluded category -- it does not itself make the whole result
`UNKNOWN`, and it is never a claim that the asset is investment-unsuitable,
only that it is conservatively out of the source-coverage universe.

A newly listed online pair can legitimately have only the current row, or only
one finalized row plus the current row.  Atlas preserves that complete source
response but keeps the pair rank-ineligible until all 30 required finalized
ranking dates exist.  Short listing history is not a partial-source failure and
is never padded or backfilled.

This universe is expressly `breadth_source_coverage_not_investable`.  It does
not claim liquidity, capacity, tradability for an Atlas portfolio, or exchange
coverage beyond Kraken.

### Cutoff-aware scan audit (2026-08-22)

An audit confirmed `qualified_members()` already implements exactly the
algorithm above, not a "classify the provider's entire universe" policy.
The ranking loop's own `if len(selected) == target: break` means a
candidate ranked below the point the target-th `eligible_crypto` asset is
found is never visited at all, let alone required to carry a taxonomy
record — proven directly in `test_crypto_breadth_cutoff_aware_scan.py`
(an asset with no taxonomy record whatsoever, ranked below a satisfied
target, never appears in `taxonomy_unknown_before_cutoff`; an `EXCLUDED`
asset within the scan range is skipped and backfilled from the next
rank; an `UNKNOWN` asset *within* the scan range still blocks, because
its resolution could change which asset actually fills a slot; a
mutation that promotes a below-cutoff unknown into the scan range flips
the result to blocked). No code change to the scan's own logic was made
because none was needed.

The real 2026-08-22 snapshot's own `TAXONOMY_COVERAGE_UNKNOWN` result is
not evidence against this: `known_eligible_count_so_far` (see below)
reports **87** — only 87 assets have ever been individually ratified
`eligible_crypto` in this repository's history, 13 short of
`target_asset_count=100`. Because the scan cannot reach target, it is
structurally forced to walk the full ranked list looking for enough
eligible candidates, correctly treating every `UNKNOWN` it passes along
the way as selection-relevant (any one of them could supply one of the
missing 13 slots). This is a genuine ratification-coverage shortfall —
more crypto assets need to be individually taxonomy-ratified — not a
scan-order defect and not evidence that a "100% provider universe"
policy is required.

`known_eligible_count_so_far` is included in `qualified_members()`'s
`TAXONOMY_COVERAGE_UNKNOWN` diagnostics (and surfaced in the committed
`universe` output) specifically so this distinction — real shortfall vs.
a specific blocking candidate near the cutoff — is visible directly in
committed evidence, without needing to re-derive it by hand.

The scheduled capture also publishes an append-only
`data/observations/crypto_taxonomy_gap/<source_date>/packet.json`. This is a
`REVIEW_INVENTORY_ONLY` artifact rebuilt from the same production transform:
it binds the raw manifest, universe policy and taxonomy hashes and preserves
the ranked UNKNOWN, EXCLUDED and rank-ineligible rows. It does not add a
taxonomy category, ratify a record, reduce the Top-100/90% gates, or authorize
investability, Stage, Production, or trading. Its purpose is to turn the live
coverage blocker into a deterministic review queue, not to decide the queue.

### Effective-dated cutoff Slice (2026-08-27)

The 2026-08-27 review inventory put 42 previously unclassified assets at ranks
69 through 141 before the taxonomy cutoff. Each exact Kraken identity was
confirmed against the retained, enabled Assets catalog and online USD pair
catalog, then independently matched to a project/foundation protocol document,
token contract, migration notice, or exact-contract asset report. The retained
pair-catalog body is
`evidence/crypto/breadth/raw/2026-08-27/kraken_asset_pairs.json.gz` with SHA-256
`90d105c571b464ffea2a1a21a814f5f5ae3da9f63086e49e49fee55c53ec1a61`.
Ticker text alone was not accepted; the reason field records the disambiguating
identity for migration/collision-sensitive cases such as LIT, NIL, M, DOG,
BABYSHARK, SYRUP, POPCAT, EIGEN, and ETHFI.

The resulting source-coverage records are effective 2026-08-27 for exactly:

```text
ACU APT ARB ASTER BABY BABYSHARK BONK CVX DCR DOG EIGEN ESP ETHFI FLOKI
ICNT JASMY JTO KNTQ KTA LIT M MANA MELANIA MINA NIL OP PENDLE PLUME POPCAT
PYTH RIZE SCRT STRK STX SYRUP TIA VIRTUAL WIF XAN XNY XPL ZRO
```

They are all `eligible_crypto` only in the narrow breadth source-coverage
taxonomy. This is not an investability, capacity, venue-selection, security,
or trading judgment. `target_asset_count=100` and
`minimum_observation_coverage_bps=9000` are unchanged, as are all false
classification/threshold/Regime/Production/trading authority flags.

### Effective-dated BTR cutoff slice (2026-08-29)

The natural 2026-08-29 review inventory reduced the cutoff-relevant taxonomy
gap to one asset, `BTR/USD` at rank 91.  The retained Kraken Assets catalog
marks `BTR` enabled and its AssetPairs catalog marks `BTR/USD` online.  Kraken's
official [listing notice](https://blog.kraken.com/product/asset-listings/btr-is-available-for-trading)
identifies the pair as Bitlayer, and Bitlayer's official
[BTR token documentation](https://docs.bitlayer.org/docs/Learn/Introduction/BTRToken/en/)
independently identifies BTR as the ecosystem governance token.  The exact
record is therefore source-identity classified `eligible_crypto` effective
2026-08-29.

The retained snapshot evaluates at 2026-08-28, so it remains byte-for-byte and
semantically blocked by `TAXONOMY_COVERAGE_UNKNOWN`; the new record is not
backdated.  A test-only replay that changes only the temporary effective date
shows the existing algorithm would select and observe 100/100 with zero
cutoff-relevant unknowns.  That counterfactual is not natural evidence.  The
next capture whose own `as_of_date` is on or after 2026-08-29 must prove the
result independently.  Top-100, 90% coverage, investability, Regime,
Production, and trading rules and authorities are unchanged.

The retained 2026-08-27 capture has `as_of_date=2026-08-26`, so replaying it
with the production policy still returns the original
`TAXONOMY_COVERAGE_UNKNOWN` (87 known eligible assets, 515 unknown rows). This
is the required non-retroactive PIT result. A test-only, non-persisted replay
that moves only these 42 effective dates back one day isolates the existing
gate logic: the same raw snapshot selects 100 assets, observes all 100, leaves
zero unknown rows before the cutoff, and emits only
`OBSERVED_UNCLASSIFIED`. That counterfactual is not historical evidence and
cannot close the operational Gate; the first natural capture whose
`as_of_date` is on or after 2026-08-27 must do that independently.

## Output and missing policy

For each included member, the helper emits canonical/source identity, exact
pair, T-1 and T dates, both closes, and `ADVANCE`, `DECLINE`, or `UNCHANGED`.
BTC is kept as a separate reference.  `alt_participation` excludes BTC and
contains only asset counts and fractions.

The collector must capture every matching USD candidate pair; a missing pair,
source error, partial catalog, checksum mismatch, or identity collision fails
the atomic capture.  After a deterministic Top 100 exists, T/T-1 direction is
calculated only for members with both closes.  Coverage of 90% or more remains
an explicitly labeled raw observation with the missing members listed.  Below
90%, or with a missing BTC reference, the breadth output is `UNKNOWN` and both
BTC and Alt measurements are null.  Missing data is never converted to zero or
neutral.

All outputs keep these authorities false:

- breadth classification;
- threshold;
- Regime score;
- Production wiring;
- trading action.

## Capture and offline commands

The scheduled workflow runs at 00:40 UTC, uses no key or paid service, and
paces Kraken public OHLC calls at 1.05 seconds per request.  Roughly 630 current
USD pairs take about 11 minutes.  It stages the entire snapshot outside the
final evidence path, validates every response and hash, then performs one
append-only move.  A failed or partial run is not committed.

The offline transform helper itself never calls the network:

```bash
python3 .github/scripts/crypto_breadth.py manifest \
  --snapshot-dir /tmp/crypto-breadth/raw/2026-08-20 \
  --capture-version crypto-breadth-capture/v2

python3 .github/scripts/crypto_breadth.py validate \
  /tmp/crypto-breadth/raw/2026-08-20

python3 .github/scripts/crypto_breadth.py transform \
  /tmp/crypto-breadth/raw/2026-08-20 \
  --universe-policy /tmp/ratified-crypto-breadth-policy.json \
  --exclusion-taxonomy /tmp/ratified-crypto-breadth-taxonomy.json \
  --out /tmp/crypto-breadth.json

python3 .github/scripts/crypto_breadth.py replay \
  /tmp/crypto-breadth/raw \
  --universe-policy /tmp/ratified-crypto-breadth-policy.json \
  --exclusion-taxonomy /tmp/ratified-crypto-breadth-taxonomy.json \
  --out /tmp/crypto-breadth-replay.json
```

No command writes a tracked breadth factor by default.


### Official identity slice (2026-09-06)

QUID, SN8, CHIP and NPC are `eligible_crypto` effective 2026-09-06 in the
existing breadth source-coverage taxonomy. Each literal Kraken identity was
matched to independently maintained official project documentation:

- QUID is Squid's native Base token, contract
  `0x1a44233fae8d50f1aeb3a5d58dd426ff4814cb53`:
  [Kraken Launch](https://support.kraken.com/fi/articles/squid-quid-token-sale)
  and [Squid](https://www.squidrouter.com/quid-token).
- SN8 is Taoshi Vanta's native Bittensor subnet-8 token (project name Theta),
  distinct from the unrelated Theta Network asset THETA:
  [Kraken](https://blog.kraken.com/product/asset-listings/sn8-is-available-for-trading)
  and [Taoshi](https://www.taoshi.io/theta), which links subnet 8.
- CHIP is USD.AI's utility/governance token, with native Arbitrum contract
  `0x0C1c1C109FE34733fca54b82d7B46B75CFb71F6e`. It is distinct from USDai,
  sUSDai and sCHIP:
  [Kraken](https://blog.kraken.com/product/asset-listings/chip-is-available-for-trading),
  [governance](https://docs.usd.ai/governance/chip) and
  [deployed contracts](https://docs.usd.ai/technical-overview/contract-addresses).
- NPC is Non-Playable Coin's original Ethereum meme/NFT hybrid, token contract
  `0x8ed97a637a790be1feff5e888d43629dc05408f6`:
  [Kraken](https://www.kraken.com/prices/non-playable-coin),
  [project](https://www.npc.com/) and
  [contracts](https://docs.npc.com/important-links-and-info/contract-addresses).
  Its native token and own NFT are interchangeable forms of the same asset.
  This differs from the existing TBTC/WBTC `wrapped` records, which represent
  a separate underlying crypto asset. The NFT form is disclosed explicitly;
  the classification creates no general exemption for wrapped assets.

The official documents were actually captured on 2026-09-06; document
publication dates are not substituted for Atlas's observation time. The
capture receipt preserves original response hashes, URLs and actual captured
times, and the decision receipt separately records CIO consumption. A newly
generated current-decision-time reference may consume the classification only
after those events. The September 6 raw vintage measures September 5 for PIT
breadth, where these September 6 records remain inapplicable; existing blocked
historical receipts are unchanged. Top-100 selection, 90% observation coverage,
all existing exclusions and all authority flags remain unchanged. This is
source coverage, not investment eligibility, Regime activation or trading.


### Official identity slice (2026-09-08)

RAY and DRV are `eligible_crypto` effective 2026-09-08 in the existing
breadth source-coverage taxonomy. Each literal Kraken identity was matched to
an official project source and is not inferred from ticker text alone:

- RAY is Raydium's native SPL token, as stated by the official
  [Raydium brand documentation](https://docs.raydium.io/resources/brand-kit),
  and independently identified as Raydium's token by
  [Kraken](https://www.kraken.com/learn/what-is-raydium-ray).
- DRV is Derive's token on Ethereum Mainnet and Derive L2, with ticker `DRV`,
  as stated by the official [Derive token documentation](https://docs.derive.xyz/docs/token)
  and independently identified as Derive on Ethereum (ERC-20) by
  [Kraken](https://support.kraken.com/hc/articles/360000678446-cryptocurrencies-available-on-kraken).

The official pages were captured on 2026-09-08 at 12:42:49Z. Retained content
SHA256 values are `f84759e6c1251a8bff3a403af6a78f417fdafec50aa7926113674301f01e56ce`
(Raydium) and `4170941f75c0d131e63dda7283fd906d82e409e29be675a15c984d0d8ce05cc3`
(Derive). The effective date does not backfill historical PIT output.
Top-100 selection, coverage requirements, existing exclusions, all authority
flags and fail-closed behavior remain unchanged. This is source coverage only;
it does not authorize investment eligibility, Regime activation, candidate
promotion or trading.


### Ratified coverage additions (2026-09-14)

User ratification `CRYPTO-BREADTH-TAXONOMY-ADDITIONS-20260914` (file SHA256
`6ff7f4865db1dde6f61d40ada5c4971ef46f547f30bdab9f635415f0e6e8e931`) adds eight
`eligible_crypto` records to the existing breadth source-coverage taxonomy.
LSK is effective 2026-09-14. SUSHI, VSN, TRIA, ZORA, XTZ, KII and 0G are
effective 2026-09-15. Each literal Kraken identity (enabled asset and online
USD pair in the retained 2026-09-14 Kraken snapshot) was matched to an official
Kraken source, and to official project documentation where retained. None is
inferred from ticker text alone:

- LSK is Lisk's token after the official ERC-20 migration. Kraken's
  [Lisk migration notice](https://support.kraken.com/articles/notice-of-support-for-lisk-migration)
  states Kraken supported the new ERC-20 LSK 1:1 and does not support Klayr.
  Kraken's [Lisk asset page](https://www.kraken.com/prices/lisk) binds shortcode
  `LSK`. [Lisk documentation](https://docs.lisk.com/guides/import-lsk) gives
  the LSK token contract.
- SUSHI is the SushiSwap governance token:
  [Kraken](https://www.kraken.com/prices/sushi) (shortcode `SUSHI`) and
  [Sushi tokenomics](https://docs.sushi.com/dao/tokenomics), Ethereum contract
  `0x6B3595068778DD592e39A122f4f5a5cF09C90fE2`.
- VSN is Vision, the Bitpanda Web3 ecosystem token:
  [Kraken listing](https://blog.kraken.com/product/asset-listings/vsn-is-available-for-trading),
  [Kraken asset page](https://www.kraken.com/prices/vision) (shortcode `VSN`)
  and [Bitpanda](https://www.bitpanda.com/en/prices/vision-vsn).
- TRIA is the Tria self-custodial neobank token, distinct from Trias (TRIAS):
  [Kraken listing](https://blog.kraken.com/product/asset-listings/tria-is-available-for-trading),
  [Kraken asset page](https://www.kraken.com/prices/tria) (shortcode `TRIA`)
  and [Tria tokenomics](https://www.tria.so/en/blogs/tria-tokenomics).
- ZORA is the Zora onchain media protocol token:
  [Kraken](https://www.kraken.com/prices/zora) (shortcode `ZORA`) and
  [Zora Coins documentation](https://docs.zora.co/coins).
- XTZ is tez, the native Tezos token:
  [Kraken](https://www.kraken.com/prices/tezos) (shortcode `XTZ`) and
  [Tezos](https://tezos.com/).
- KII is KiiChain's native gas and utility token:
  [Kraken listing](https://blog.kraken.com/product/asset-listings/kii-is-available-for-trading),
  [Kraken asset page](https://www.kraken.com/prices/kiichain) (shortcode `KII`)
  and [KiiChain documentation](https://docs.kiiglobal.io/docs/learn/tokenomics/utility).
- 0G is the 0G Chain native token:
  [Kraken listing](https://blog.kraken.com/product/asset-listings/0g-is-available-for-trading),
  [Kraken asset page](https://www.kraken.com/prices/0g) (shortcode `0G`) and
  [0G chain documentation](https://docs.0g.ai/concepts/chain).

The pages were captured on 2026-09-14 between 08:42Z and 08:49Z. Retained
content SHA256 values are listed below. Kraken asset pages embed live prices,
so a later capture of the same URL is expected to hash differently; the binding
fields are the page's `field_crypto_shortcode`, name and description.

| Source | SHA256 |
|---|---|
| Kraken Lisk migration notice | `8cb561b5229f32716ca528b745b21e3c3a7f6f0b0304656f093a58aa2fbefa1a` |
| Kraken asset page lisk | `ee7d466e418b19ec78d90fd01748b3fc263f2b52480efe1cf5bf79448f288702` |
| Lisk import-lsk guide | `4e272103d78fe15cc1fe4eae59b634b6d11a1c5630dd318c67fe7ec849227963` |
| Kraken asset page sushi | `d5d91422274a37d94704ff8244e3cfcddcfaa4b0f174a6a40ac3ff65150179b0` |
| Sushi tokenomics | `aa46eeb03537de181dff6fbe980bb03293c8b506ae9537bbd95b470e09cf5703` |
| Kraken VSN listing | `e0f5e8cbc2a20c886c64dfdf7a696a306255a26d9278bb9ea24ac50860f104df` |
| Kraken asset page vision | `f8b6848c178833837202d1973de71e95a998c866f7d62a0ceb1d048c2d4c3822` |
| Bitpanda Vision (VSN) | `aed8db658cf9a46f6d61608265bd2b91fc35f052195f104d3e48695a2753f4cf` |
| Kraken TRIA listing | `2cca4649aeb485380cb97db09af42e8740c0dd98b13dea6891fcbb1f72952f85` |
| Kraken asset page tria | `1243a64a2972f49f4e0d443bfc793eddbf0ba751765d060fe3f16909f878eceb` |
| Tria tokenomics | `3ee4a32b0093809c4df1c6d0dac9a548bbdc09f7ef194372d6923b94dd73f0d3` |
| Kraken asset page zora | `d9307945fd32b1784d3191d677225b20af82a62820caa760c768e8c9e0f5a754` |
| Zora Coins documentation | `8c663318d758ee7962c5370d8e3a29316f275b0892657b68c1dabeef2078e1e8` |
| Kraken asset page tezos | `9f73dd0ffef64867b80b7decc33cc8d62a52540304609d5bdb6ead362e69186d` |
| Tezos home | `068119ec39a402450a4cdf02577c85a2afbffb2345d831df77dcbdd23b166206` |
| Kraken KII listing | `61e1a0a4e882233486789661b8e5a9e08d83f823b58188b97f4ff93783ca2835` |
| Kraken asset page kiichain | `e99cdeeea2b88880976f0635f94e145c03a493e789cda913915e3bca1a6b1010` |
| KiiChain tokenomics utility | `e98f6616f3cf4e4223f39f094ef1fee9b461160176493ab1ba6199dd09717932` |
| Kraken 0G listing | `be8a9642efdea5da7757fd4d83f2b77cc6eaabbe511b8a72521d0afc67b94406` |
| Kraken asset page 0g | `d38464b74042fc6c3b506f526bd61153da26c4807359b3232fdbf2781abd53a9` |
| 0G chain documentation | `4e4583533af575fbd99c14cd30e91a883851d88a3b8e09f75ea72825799311fb` |

Effective dates follow the ratification and are not backdated. Retained
vintages 2026-09-08..2026-09-14 produce the same qualified members, status and
reason as before. Vintage 2026-09-15 (as of 2026-09-14) is no longer blocked by
LSK. Top-100 selection, the 30-day turnover rule, 90% observation coverage,
existing exclusions, fail-closed `TAXONOMY_COVERAGE_UNKNOWN` behavior and all
authority flags remain unchanged. LIGHTER is not part of this slice. This is
source coverage only; it does not authorize investment eligibility, Regime
activation, candidate promotion or trading.


### Conditional LIGHTER identity (2026-09-14)

The same ratification made LIGHTER conditional on confirming the Kraken
identity from an official Kraken source. That confirmation was made, so
LIGHTER is `eligible_crypto` effective 2026-09-16.

- Kraken's own [Lighter asset page](https://www.kraken.com/prices/lighter)
  carries CMS asset metadata that fixes the binding:
  - `field_crypto_shortcode` is `LIGHTER` and the name is `Lighter`.
  - The trading link is `https://pro.kraken.com/app/trade/LIGHTER-usd`.
  - `field_supported_kraken_asset` is `true`.
  - The description says `$LIGHTER` is the native token of Lighter, a
    decentralized perpetual futures exchange built as a zero-knowledge rollup
    on Ethereum. It adds that the project calls the token the Lighter
    Infrastructure Token.
- The official [Lighter documentation](https://docs.lighter.xyz/about-lighter/lit-utility.md)
  names the same Lighter Infrastructure Token, ticker LIT, as the native
  infrastructure token of the Lighter ecosystem.
- Kraken lists this asset under `LIGHTER`. The existing Kraken `LIT` record
  is Litentry, a different asset, and is unchanged.
- The retained 2026-09-14 Kraken snapshot shows asset `LIGHTER` enabled and
  pair `LIGHTER/USD` online.

The pages were captured on 2026-09-14 at 08:42Z and 08:45Z. Their retained
content SHA256 values are:
- Kraken asset page: `b8796f5c33db269628cab166c2473ad73a33a540d5b3700d1ab7f4c50c29836b`.
  It embeds live prices, so a later capture will hash differently.
- Lighter documentation: `4fca009e2d2671a9ecb7812c771496717eea1494891638ce0eebe8548c3b3d0c`.

The Kraken blog listing URL returned 404 and is not used. The effective date is
not backdated. Thresholds, fail-closed behavior and authority flags are
unchanged. This is source coverage only.


### Cutoff-band identity slice (2026-09-18)

The 2026-09-18 eligibility scan stopped at rank 112 — 100 eligible members,
12 excluded rows, zero unknowns — while the nearest unclassified asset sat at
rank 124. That 12-rank margin is the whole protection against a
`TAXONOMY_COVERAGE_UNKNOWN` day, and every historical gap so far
(2026-08-28..09-07: BTR, HNT+SKR, SN8, CHIP+QUID, NPC, DRV+RAY) was an
existing asset climbing in turnover rather than a new listing. This slice
classifies the fourteen assets in the 40-rank band above the cutoff (ranks
124 through 152), effective 2026-09-18.

Each exact Kraken identity was confirmed against the retained, enabled Assets
catalog and the online USD pair catalog in
`evidence/crypto/breadth/raw/2026-09-18` (`kraken_assets.json.gz` SHA-256
`ddbd9d0b1874d5991906812543c01ab87db86009d1032ad5621ff186a00b1c81`,
`kraken_asset_pairs.json.gz` SHA-256
`1ddc8c4dae4a43049297b6af0337622d189bb1c959f80e364bb79983aa97a478`), then
independently matched to a project/foundation protocol document, token
contract, migration notice or exact-contract asset report. Ticker text alone
was not accepted. Thirteen cleared that bar:

```text
BAT CAKE CFG ENS ETC GRT MNT PEAQ SAND SHAPE SHX SN51 VET
```

The disambiguating identity for each collision-sensitive case is recorded in
the taxonomy's own `reason` field and, at source-URL and content-hash
granularity, in
`evidence/crypto/identity/crypto_breadth_band_source_facts_20260918.json`.
The cases that needed disambiguation rather than confirmation were:

- **CFG** — Kraken reports CFG on Ethereum (ERC-20), and Centrifuge's own
  CP149 migration notice gives the new Ethereum contract
  `0xcccccccccc33d538dbc2ee4feab0a7a1ff4e8a94`, a 1:1 conversion of legacy
  Centrifuge Chain CFG and wCFG, with the window closed 2025-12-03. That
  resolves the legacy-CFG / wCFG / V3-CFG ambiguity to the post-migration
  token.
- **SHX** — Stronghold's published `stellar.toml` declares asset code `SHX`
  with issuer `GDSTRSHXHGJ7ZIVRBXEYE5Q74XUVCUSEKEBR7UCHEUUEK72N7I7KJ6JH`,
  while Kraken reports SHX on Ethereum (ERC-20). Stronghold's own SHx Bridge
  notice states the bridge maintains "a 1:1 total supply across both chains",
  so the Ethereum form is the same SHx asset in a second representation, not a
  token representing a separate underlying crypto asset. The existing
  `wrapped` records (TBTC/WBTC) are the latter; this disclosure creates no
  general wrapped-asset exemption.
- **SHAPE** — Kraken's 2026-03-19 listing notice and Shape's own token page
  use the same self-description ("the network for onchain objects"), and the
  project page publishes contract
  `0x360aAC543A23dbcefA8049d4C4d8B18dA1CCa360`. SHAPE is the Structura DUNA
  governance token; the Shape L2 (chain ID 360) uses ETH for gas, so this
  record makes no native-gas-coin claim.
- **SN51** — Kraken's listing notice states "Lium operates as subnet 51
  (SN51) within the Bittensor ecosystem"; Lium's own repository under the
  Datura-ai organisation independently describes lium.io as "powered by
  Bittensor Subnet 51". Same source shape as the ratified SN8 record, and
  distinct from Bittensor TAO and other `SN<n>` subnet tokens.
- **MNT** — the post-migration Mantle token (BIT converted 1:1 under
  BIP-21/MIP-22), Ethereum L1 contract
  `0x3c3a81e81dc49A522A592e7622A7E711c06bf354`.
- **ETC**, **VET** — native-coin claims taken from the projects' own
  documentation, and in VET's case explicitly distinguished from the VTHO gas
  token of the same network.

**MOODENG is classified `unverified_identity`, effective 2026-09-18.** This is
the fail-closed branch working, not a failure of the batch. Kraken publishes
no contract, mint or genesis identity for it — the asset page describes only a
memecoin named after a pygmy hippopotamus, with no stated utility, and no
Kraken listing notice or support article for MOODENG exists. No project or
foundation protocol document, token contract publication or migration notice
exists to match independently: the asset has no official issuer, and
unrelated tokens have traded as MOODENG across chains. As with the existing
`PLAY` and `RE` records, this is a real, ratified exclusion — the ranking loop
skips it and keeps going, it never makes a whole result `UNKNOWN` — and it is
not an investability claim.

All fourteen records are effective 2026-09-18. Every retained vintage
evaluates at `as_of = vintage - 1 day`, so the latest committed snapshot
(2026-09-18, `as_of` 2026-09-17) is strictly before the effective date:
snapshots 2026-09-12..2026-09-18 return byte-identical members, excluded rows
and unknown rows with and without these records. None of the fourteen appears
in the scanned range of any retained vintage. This batch buys headroom below
the cutoff; it does not change, and must not be read as changing, any
committed result. `target_asset_count=100`,
`minimum_observation_coverage_bps=9000`, the 30-day turnover ranking rule,
every existing exclusion and all classification/threshold/Regime/Production/
trading authority flags are unchanged. This is breadth source coverage only.

### 2026-09-18 headroom slice (second batch, eleven assets)

The cutoff-band batch above classified a list read off one day's snapshot.
Turnover ranks drift, so a fixed list develops holes: an asset sitting at
rank 180 on the day the list was written can be at rank 163 a day later,
and a hole inside the block is exactly the `TAXONOMY_COVERAGE_UNKNOWN`
day the exercise exists to prevent. This slice therefore re-derives the
block instead of copying it. Each candidate is ranked in all seven
committed vintages 2026-09-12…2026-09-18, classified at the effective
date so the cutoff-band records are already in force, and selected by its
**minimum** rank across those vintages.

Measured that way the earlier 35-asset target list was both too wide and
incomplete: reaching rank 170 needs only 16 assets, while the list itself
omitted five assets that have been seen inside its own span — `DOS`
(seen at 163), `ROBO` (172), `AVNT` (178), `MOVR` (182) and `XMN` (191).
`DOS` sits below 170 and is carried here.

Eleven assets are recorded effective 2026-09-18: `AR`, `CRO`, `DOS`,
`FHE`, `GHST`, `IDOS`, `KSM`, `TRAC`, `TRUST` and `ZIG` as
`eligible_crypto`, and `AUSD` as `stablecoin` — Agora publishes it as a
fully reserved stablecoin, so it lands in the already-ratified exclusion
category rather than as a new kind of record.

Two assets were captured through a rendered page rather than a raw HTTP
body, and this is disclosed per asset in the receipt: `zigchain.com` and
`docs.zigchain.com` return HTTP 403 to a plain client, and `dappos.com`
serves a client-rendered shell containing no token text. For those the
retained content hash is over the retained rendered text, not over the
raw response bytes.

Assets whose identity could not be confirmed by two independent official
sources within this batch were left **unrecorded** rather than written
either way. `STORJ` (min rank 159), `MET` (161), `RIVER` (166), `DENT`
(167) and `GALA` (168) are all in the block by rank, but a speculative
`eligible_crypto` would fabricate an identity and a speculative
`unverified_identity` would fabricate an exclusion that removes a likely
eligible asset from the source-coverage universe. Neither is the fail-
closed branch; leaving the asset unclassified is, because an unclassified
asset that reaches the scanned range fails the scan closed. The
`unverified_identity` category stays reserved for the `PLAY` / `RE` /
`MOODENG` case — a genuine, evidenced identity failure — and a partial
finding is recorded for `DENT`, whose Kraken-published domain
`dentwireless.com` now redirects to an unrelated eSIM business (`tunz.io`)
publishing no token identity.

The block is consequently contiguous through rank **158**: no asset has
ever been seen unclassified at or below that rank in any retained
vintage. Against the rank-112 cutoff that is a 49-rank margin at the
2026-09-18 vintage, up from 41 after the cutoff-band batch.

Every record is effective 2026-09-18 and every retained vintage evaluates
at `as_of = vintage - 1 day`, so the latest retained as_of (2026-09-17) is
strictly before the effective date and no committed result moves.
`target_asset_count=100`, `minimum_observation_coverage_bps=9000`, the
30-day turnover ranking rule, every existing exclusion and all
classification/threshold/Regime/Production/trading authority flags are
unchanged. This is breadth source coverage only.

### 2026-09-18 deferred five (third batch)

The headroom slice above stopped at rank 158 and said exactly why: five
assets sat inside its own block — `STORJ` (min rank 159), `MET` (161),
`RIVER` (166), `DENT` (167) and `GALA` (168) — but two independent
official sources were not obtained for them inside that batch, so no
record was written either way. That refusal was correct. This batch
resolves all five on evidence rather than by assumption, and the
`unverified_identity` category is not used: every one of the five clears
the bar, so none of them is an exclusion.

All five are recorded `eligible_crypto`, effective 2026-09-18. Four are
bound to an exact on-chain identifier the project publishes itself:

- **STORJ** — Kraken publishes Storj on Ethereum (ERC-20) and describes
  payment in "the Ethereum-based ERC20 STORJ token"; Storj's own node
  documentation on `storj.dev` publishes the contract
  `0xB64ef51C888972c908CFacf59B47C1AfBC0Ab8aC` and a second official FAQ
  titled "ERC20-compatible wallet address for STORJ tokens".
- **MET** — Kraken publishes Meteora on Solana; Meteora's own
  documentation publishes "MET SPL Address:
  METvsvVRapdj9cFLzq4Tr43xK4tAjQfwX76z3n6mWQL", a 23 October 2025 TGE and
  a fixed 1,000,000,000 supply, and its MET FAQ states the chain is
  Solana on the SPL standard.
- **RIVER** — Kraken publishes River on BNB Chain and describes the
  chain-abstraction stablecoin system, satUSD, PrimeVault and SmartVault;
  River's own documentation publishes the RIVER token address
  `0xdA7AD9dea9397cffdDAE2F8a052B82f1484252B3` on BNB Chain (also
  Ethereum and Base) alongside the same product set. satUSD is a separate
  asset and no native-gas-coin claim is made.
- **GALA** — the strongest case in the batch: Kraken's own migration
  notice and Gala's own Help Center publish the *identical* Ethereum
  contract `0xd1d2Eb1B1e90B638588728b4130137D262C87cae` for GALA v2.
  GALA v1 is deprecated and no GalaChain native-coin claim is made.

**DENT is a chain-level identity only, and the record says so.** Kraken
publishes Dent on Ethereum (ERC-20) and describes a mobile operator and
data exchange whose DENT token "powers the platform". Independently, the
project's own DENTNet documentation publishes the DENT token as "the core
of the mobile data ecosystem" with a total supply of "100B" that "will
not change", deposited into DENTNet "from ERC20 wallets" and, in the
bridge guide, "from the Ethereum network". Two independent organisations
therefore agree on ticker, chain and platform. What no live official page
publishes any more is a contract address: `dentwireless.com` and
`dent-app.com` now redirect to the successor brand Tunz (`tunz.io`, part
of DT One), whose October 2025 notice states "Dent is now Tunz" and that
"the DENT Token will continue to serve as a payment option within the
app", the app site still links `www.dentnet.io` as the project's
blockchain, and the bridge UI at `main.dentnet.io` returns 404. The
record and the receipt both disclose this, and the test pins the
disclosure so the record cannot later be read as an exact-contract claim.
This is the reverse of the `PLAY` / `RE` / `MOODENG` case: there the
identity could not be established at all, here it is established at the
chain level and the missing detail is named.

Measured the same drift-robust way as the previous slice — minimum rank
across the seven committed vintages 2026-09-12…2026-09-18, classified at
the effective date — the block is now contiguous through rank **171**,
up from 158. The next unclassified asset is `ROBO` at min rank 172.
Against the rank-112 cutoff that is a 59-rank margin at the 2026-09-18
vintage.

The retained source bodies for this batch are held **outside any session
scratchpad**. On 2026-09-18 a disk sweep deleted the 149 MB of bodies
behind the two earlier batches' receipts, so those hashes can no longer
be re-derived locally; that weakness is unchanged for those receipts and
is not claimed to be fixed. This batch's bodies (34 MB) survive such a
sweep, they are still not committed to this repository, and permanent
published retention remains undecided.

Every record is effective 2026-09-18 and every retained vintage evaluates
at `as_of = vintage - 1 day`, so the latest retained as_of (2026-09-17) is
strictly before the effective date and no committed result moves.
`target_asset_count=100`, `minimum_observation_coverage_bps=9000`, the
30-day turnover ranking rule, every existing exclusion and all
classification/threshold/Regime/Production/trading authority flags are
unchanged. This is breadth source coverage only.

### 2026-09-19 rank-200 push, slice A (ranks 172–185)

The 2026-09-18 deferred-five batch left the block contiguous through rank
**171** and named `ROBO` at minimum rank 172 as the next unclassified
asset. This is the first of three stacked slices that carry the block to
rank **201**, ahead of the 2026-10-07 latch. Before the latch a
classification costs nothing; after it, one day with an unclassified asset
inside the scan range costs roughly 35 days (30-day window plus
`MINIMUM_CONSECUTIVE_COMPLETE_DAYS=5` re-acceptance). Thirteen assets are
recorded effective 2026-09-19 — eleven `eligible_crypto` and two
`unverified_identity` — and the block runs contiguous through rank
**186**, measured as minimum rank across the seven committed vintages
2026-09-12…2026-09-18 rather than from one day's snapshot.

Six records bind an exact on-chain identifier the project publishes
itself: the ADI Foundation's ERC-20 contract
`0x8b1484d57abbe239bb280661377363b03c89caea`, the Avantis Base contract
`0x696F9436B67233384889472Cd7cD58A6fB5DF4f1`, the Moonbeam Foundation's
new Base MOVR contract `0x43fEB74608334DDa8c1a6500D185cFC3Ea962B83`,
MegaETH's native protocol token `0x28B7E77f82B25B95953825F1E3eA0E36c1c29861`,
VeChain's built-in VTHO Energy contract
`0x0000000000000000000000000000456E65726779` and Ponke's Solana mint
`5z3EqYQo9HiCEs3R84RCDMu2n7anpDMxRhdK8PSWmrRC`. `EGLD` and `OKB` are
native-coin claims on their projects' own chains, taken from those
projects' own documentation, in the same shape as the existing `ETC` and
`VET` records.

Two findings are worth stating rather than burying.

**MOVR is not a Kraken catalog quirk.** Kraken publishes Moonriver on
**Base**, not on its Kusama parachain, which is exactly what a bad catalog
row looks like. It is not one. The Moonbeam Foundation itself announced
and executed a full migration of MOVR to Base — "the migration is 1:1
every MOVR you hold today becomes one Base ERC-20 MOVR", with a
2026-07-31 bridging deadline and the new Base token deployed to
`0x43fEB74608334DDa8c1a6500D185cFC3Ea962B83`. Kraken's network is
correct and the record binds the Base contract.

**`okx.com` cannot be captured with a rendered-DOM fetch.** Every headless
render of an OKX URL returned an empty body, including URLs that answer
HTTP 200. The three OKB source bodies were therefore retained over a
plain HTTP request with a browser user agent instead, and the receipt says
so per source rather than presenting them as rendered captures.

`ROBO`, `GRASS` and `AXS` are recorded as **chain-level identities only**,
and the records say so, exactly as the `DENT` record does. Each project
publishes the chain and the token standard — Fabric Foundation's
whitepaper says `$ROBO` "initially launched as an ERC20 token on Ethereum
mainnet", grass.io says its wallet is "running on Solana", Axie Infinity's
whitepaper says "the $AXS and $SLP ERC-20 contract has been audited by
Quantstamp" — but none publishes a contract address on any officially
reachable page. Every address that exists in public for those three lives
on third-party explorers, which the ratified bar does not accept. A test
pins each disclosure so the record cannot later be read as an
exact-contract claim.

**Two assets are classified `unverified_identity`, effective 2026-09-19.**
This is the fail-closed branch working, not a failure of the batch. As
with the existing `PLAY`, `RE` and `MOODENG` records, each is a real,
ratified exclusion — the ranking loop skips it and keeps going, it never
makes a whole result `UNKNOWN` — and neither is an investability claim.

`ZEREBRO`: Kraken publishes it as Zerebro on Solana and its asset page
describes the project, but that is one organisation and Kraken's own
Assets/AssetPairs metadata carries no chain/contract/genesis field. No
project publication exists to match independently: `zerebro.org` serves a
Next.js page whose entire body is a single animated canvas with no token,
chain, mint or contract text anywhere in the retained DOM; the
research-paper path cited for the project returns HTTP 404; and the ZerePy
repository documents an agent framework built from the Zerebro backend
without publishing any token identity. Every mint claim traces to
third-party explorers and aggregators.

`TURBO`: the only page carrying a contract address, `turbotoken.io`,
**declines to be an authoritative source for the asset**. It states
verbatim that "This website is an independent, community-driven platform
and does NOT officially represent TURBO" and that "The TURBO community
operates as a decentralized entity". A page that explicitly disowns
representing the asset cannot be the second independent source the bar
requires, however accurate its contract string may be. That TURBO has no
issuer is a real fact about TURBO, and the honest way to record a token
whose identity nobody authoritative publishes is to say it could not be
confirmed — not to accept the nearest available page because nothing
better exists. The contract string and the disclaimer text are both
retained in the receipt, so this record can be overturned if a genuine
issuer source ever appears.

Every record is effective 2026-09-19 and every retained vintage evaluates
at `as_of = vintage - 1 day`, so the latest retained `as_of` (2026-09-17)
is strictly before the effective date and no committed result moves:
snapshots 2026-09-12…2026-09-18 return byte-identical members, excluded
rows and unknown rows with and without these records, and none of the
thirteen appears in the scanned range of any retained vintage.
`target_asset_count=100`, `minimum_observation_coverage_bps=9000`, the
30-day turnover ranking rule, every existing exclusion and all
classification/threshold/Regime/Production/trading authority flags are
unchanged. This is breadth source coverage only.

The retained source bodies for this batch are held **outside any session
scratchpad**. On 2026-09-18 a disk sweep deleted the 149 MB of bodies
behind two earlier batches' receipts, so those hashes can no longer be
re-derived locally; that weakness is unchanged for those receipts and is
not claimed to be fixed. These bodies survive such a sweep, they are still
not committed to this repository, and permanent published retention
remains undecided.

### 2026-09-19 rank-200 push, slice B (ranks 187–193)

Second of three stacked slices. Slice A carried the block to rank 186 and
named `CPOOL` at minimum rank 187 as the next unclassified asset. Ten
assets are recorded effective 2026-09-19 — nine `eligible_crypto` and one
`stablecoin` — and the block runs contiguous through rank **193**, again
measured as minimum rank across the seven committed vintages
2026-09-12…2026-09-18. The next unclassified asset is `SC` at minimum
rank 194, which slice C picks up.

Seven records bind an exact identifier the project publishes itself:
Clearpool's Ethereum contract, Bio Protocol's Solana mint, Katana's native
`KAT` contract, xMoney's Sui object address, the BRL1 Polygon contract,
STBL's BNB Chain contract and the SuperVerse Ethereum contract. `S` is a
native-coin claim on Sonic's own chain and `TAC` a native gas-coin claim on
TAC Protocol's own chain, both in the same shape as the existing `ETC` and
`VET` records.

Three assets needed real disambiguation, and the records carry it.

**`TAC` is the one Kraken publishes with a blank network.** The catalog leg
alone therefore offers nothing to match, and several unrelated tokens trade
as TAC. Kraken's own listing notice of 2025-07-15 resolves it: it states
that TAC "is a purpose-built blockchain that enables EVM dApps to tap into
the TON and Telegram user base" and "brings EVM compatibility and liquidity
to the TON ecosystem". Independently, TAC Protocol's own blog publishes
that `$TAC` "is the exclusive gas token for executing transactions and
smart contracts on the TAC EVM". Two organisations describe the same
TON-integrated EVM layer-1 and the same native token. The blank network is
correct rather than missing — an L1 native coin has no host chain to name.

**`BRL1` is not `eligible_crypto`.** Its issuing consortium's own site
describes BRL1 as backed 1:1 by Brazilian public securities indexed to the
Selic rate, repurchase agreements and reserves at regulated financial
institutions, with regular external certification and published Proof of
Reserves. That is a fiat-pegged, fully reserved stablecoin, and the record
takes the ratified `stablecoin` exclusion on the same basis as `AUSD`. It
is a source-coverage classification, not an investability claim.

**`STBL` is the mirror case, and is not a stablecoin.** The project's own
documentation separates two tokens: `USST` is its over-collateralised,
dollar-pegged stablecoin, while `STBL` is the governance and value-capture
token layered on top through staking, buybacks and voting. The BNB Chain
contract is also what ties Kraken's listing to the `stbl.com` Stablecoin
2.0 project rather than to the unrelated `stbl.io` precious-metals project
that shares the ticker.

`AIN` is recorded as a **chain-level identity only**, and the record says
so. Kraken's asset page and its 2025-11-04 listing notice both describe an
agentic IDE whose AIN token "powers fees, payments, and governance", and
Infinity Ground's own whitepaper independently publishes exactly that
utility for `[AIN]` — payments and transaction fees, membership, launchpad
access, governance and staking. Two organisations therefore agree on
ticker, project and token role. But the project's whitepaper never states
a blockchain or a contract address, and its website and blog both refuse
automated capture, so the BNB Chain representation rests on Kraken's
listing alone. That limit is named in the record, in the receipt and in a
test, in the same shape as the ratified `ZIG` record. No exact-contract
claim is made.

Every record is effective 2026-09-19, the latest retained `as_of`
(2026-09-17) is strictly before it, and snapshots 2026-09-12…2026-09-18
return byte-identical members, excluded rows and unknown rows with and
without these records. `target_asset_count=100`,
`minimum_observation_coverage_bps=9000`, the 30-day turnover ranking rule,
every existing exclusion and all classification/threshold/Regime/
Production/trading authority flags are unchanged. This is breadth source
coverage only.

### 2026-09-19 rank-200 push, slice C (ranks 194–200) — target reached

Last of three stacked slices. Slice A carried the block from rank 171 to
186 and slice B to 193; this slice carries it to **201**. That is the
number the 2026-10-07 leadership-axis latch made worth paying for: after
that date the axis moves irreversibly from a 7-day to a 30-day window, and
one day with an unclassified asset inside the scan range costs roughly 35
days (30-day window plus `MINIMUM_CONSECUTIVE_COMPLETE_DAYS=5`
re-acceptance). Before the latch it costs nothing. The next unclassified
asset is now `PIEVERSE` at minimum rank 202.

Five assets, all `eligible_crypto`, effective 2026-09-19: `FUN`, `HONEY`,
`RED`, `RUNE`, `SC`.

`RUNE` and `SC` are native-coin claims on their projects' own layer-1
chains — THORChain's own technical documentation states that "THORChain's
native token is called RUNE", and Sia's own site states that siacoins are
"the primary currency of the Sia network" — in the same shape as the
existing `ETC` and `VET` records. The other three bind an exact identifier
the project publishes itself: Sport.fun's Base contract
`0x16EE7ecAc70d1028E7712751E2Ee6BA808a7dd92`, Hivemapper's Solana mint
`4vMsoUT2BWatFweudnQM1xedRLfJgJ7hswhcpz4xgBTy` and RedStone's Ethereum
contract `0xc43c6bfeda065fe2c4c11765bf838789bd0bb5de`.

**`FUN` is the ticker-collision case, and the record says so.** Kraken
publishes this asset as **Sport.fun on Base** — not as the older,
unrelated FunFair ERC-20 on Ethereum that carried this ticker for years.
Sport.fun's own documentation publishes an official contract-address table
giving Chain = Base with the `$FUN` address, its whitepaper states the
blockchain layer operates on Base, and its tokenomics page describes `$FUN`
as an ERC-20 with a fixed 1,000,000,000 supply. FunFair was deliberately
not used as a source: it would have matched the ticker while contradicting
both the name and the network Kraken publishes. The record binds only the
Sport.fun Base contract.

Every record is effective 2026-09-19, the latest retained `as_of`
(2026-09-17) is strictly before it, and snapshots 2026-09-12…2026-09-18
return byte-identical members, excluded rows and unknown rows with and
without these records. `target_asset_count=100`,
`minimum_observation_coverage_bps=9000`, the 30-day turnover ranking rule,
every existing exclusion and all classification/threshold/Regime/
Production/trading authority flags are unchanged. This is breadth source
coverage only.

Across the three slices, 28 assets were classified: 25 `eligible_crypto`,
one `stablecoin` (`BRL1`) and two `unverified_identity` (`TURBO` and
`ZEREBRO`). The block moved from rank 171 to rank 201 — a 30-rank gain,
against a rank-112 scan cutoff at the 2026-09-18 vintage. Both exclusions
are ratified categories, so neither opens a coverage hole: the frontier is
201 with them exactly as it would be without them.

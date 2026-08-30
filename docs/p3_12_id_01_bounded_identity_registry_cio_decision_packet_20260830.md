# P3-12-ID-01 -- Upbit Bounded Identity Registry -- CIO decision packet -- 2026-08-30

> **Ratification addendum (2026-08-30):** CIO ratified exactly the 55
> `VERIFIED_CANDIDATE` mappings in this historical packet, together with
> the stated exclusion taxonomy and PAPER-only thresholds, effective
> 2026-08-30. The executable registry is
> `config/upbit_asset_identity_registry.json`, which pins this packet and
> its curated evidence by SHA-256. All Exchange, REAL, Production, Trading,
> order, and `paper_exit_authorized` flags remain false. The original draft
> text and 2026-08-29 shadow figures below are retained verbatim as the
> pre-ratification record; they are not current natural-run results.

Status: **REVIEW MATERIAL, DRAFT ONLY**. No identity registry is ratified.
`config/upbit_exclusion_taxonomy.json` and `config/upbit_tradeable_universe_policy.json`
`approval_status` fields are unchanged by this work. This PR is a **draft
PR** -- not merged, not marked ready.

Evidence packet: `data/observations/upbit_bounded_identity_registry/2026-08-29/packet.json`.
Curated research evidence: `config/upbit_bounded_identity_evidence.json` (80
assets). Builder source: `identity/upbit_bounded_identity_registry.py` +
`.github/scripts/upbit_bounded_identity_registry_build.py`. Contract:
`docs/upbit_bounded_identity_registry_contract.md`. This follows up
directly on the CIO-approved P3-12-TAX-01 (PR #463, merged).

## 1. The question this closes

P3-12-TAX-01 drafted 75 taxonomy records purely from **ticker match**
against the RATIFIED Kraken breadth taxonomy. Matching tickers does not
prove Upbit's market is the same real-world project. This work
independently researched, for each of the 81 taxonomy-covered Upbit
markets, whether an official, independent source (CoinGecko, official
project docs/sites, chain explorers) confirms that identity -- and holds,
fail-closed, whenever it cannot.

## 2. Starting scope, verified, held

| | Count |
|---|---|
| Starting scope (taxonomy-covered Upbit markets) | **81** |
| `VERIFIED_CANDIDATE` | **55** |
| Held | **26** |

Held breakdown:

| Verdict | Count |
|---|---|
| `HOLD_TICKER_COLLISION` | 24 |
| `HOLD_MISSING_SECOND_SOURCE` | 2 |

(`HOLD_CONTRACT_MISMATCH`, `HOLD_REBRAND_UNRESOLVED`, and
`HOLD_SOURCE_STALE` mechanisms all exist and are tested, but no real asset
in today's data actually hit them -- see §6.)

## 3. Two operationally important findings

* **`KRW-RE`** -- forced-held per your 2026-08-30 directive, regardless of
  evidence. Consistent with Kraken's own RATIFIED `unverified_identity`
  classification for this exact symbol.
* **`KRW-LIT`** -- Upbit's own `english_name` is **"Lighter"**, a zk-rollup
  perpetuals-DEX protocol that listed on Upbit around 2026-08-24. This is a
  **completely different, unrelated project from Litentry**, which is what
  the P3-12-TAX-01 taxonomy record's `reason` text (sourced from Kraken's
  own citation) actually describes -- Litentry itself rebranded and
  token-swapped 1:1 to Heima (HEI) in Feb 2025 and no longer uses the LIT
  ticker at all. Both candidates were researched independently; the ticker
  collision correctly holds `KRW-LIT` out of the registry regardless. **The
  taxonomy record's `reason` text for LIT (drafted in P3-12-TAX-01) is
  factually describing the wrong project** -- this PR does not correct it
  (out of this WBS's scope: only the identity registry, not taxonomy
  content, per your instructions), but flags it for a follow-up correction.
  No safety impact: `KRW-LIT` was never eligible in either version.

## 4. Verified candidates (55)

AAVE, ADA, AERO, AKT, ALGO, APT, ATOM, AVAX, BCH, BONK, BTC, CC, CRV, DOGE,
DOT, ENA, ETH, ETHFI, EUL, EURC, FIL, HBAR, ICP, INJ, JTO, KAITO, LINK,
MANA, MINA, MORPHO, NEAR, ONDO, PENDLE, POL, PYTH, RENDER, RLUSD, SHIB,
SOL, SUI, SYRUP, TAO, TRX, USDC, USDE, USDG, USDT, VIRTUAL, VVV, WLD, XAUT,
XLM, XRP, ZAMA, ZRO.

Every one has: an official-source citation, "high" name-match confidence,
no found ticker collision, and (where applicable) a confirmed chain/
platform and/or a confirmed-resolved rebrand history (e.g. `AAVE`
ETHLend->Aave, `INJ` ERC-20->native-chain->native-EVM, `POL` MATIC->POL,
`RENDER` RNDR->RENDER, `SYRUP` MPL->SYRUP -- all independently confirmed
via official sources, not merely asserted by the taxonomy's own Kraken
citation).

## 5. Shadow-recomputed funnel -- exact 5-market drop, explained

Re-running the P3-12 shadow-apply mechanism with this 55-market registry
substituted for identity ratification (2026-08-29 snapshot, same
unmodified classifier):

| Scenario | market_count | TRADEABLE_UNIVERSE | PAPER_ELIGIBLE |
|---|---|---|---|
| P3-12-TAX-01 taxonomy-only supplemental (all 72 `eligible_crypto` treated as identity-ratified) | 282 | 1 | 28 |
| **P3-12-ID-01 (only 55 identity-verified markets ratified)** | 282 | 0 | 24 |

**5 markets dropped, exact cause each:**

| Market | Was | Now | Why |
|---|---|---|---|
| `KRW-PEPE` | PAPER_ELIGIBLE | OBSERVATION_POOL (`IDENTITY_UNRATIFIED`) | `HOLD_TICKER_COLLISION` -- CoinGecko lists several distinct Pepe-branded projects; this is the dominant/original one but the family has real naming collision risk |
| `KRW-PROS` (Pharos) | TRADEABLE_UNIVERSE | OBSERVATION_POOL (`IDENTITY_UNRATIFIED`) | `HOLD_MISSING_SECOND_SOURCE` -- name-match confidence only "medium" (newer/smaller project, "PROS" also a near-homograph of an unrelated NYSE ticker) |
| `KRW-SEI` | PAPER_ELIGIBLE | OBSERVATION_POOL (`IDENTITY_UNRATIFIED`) | `HOLD_TICKER_COLLISION` -- short, common-word ticker flagged out of caution, no exhaustive collision search completed |
| `KRW-TRUMP` | PAPER_ELIGIBLE | OBSERVATION_POOL (`IDENTITY_UNRATIFIED`) | `HOLD_TICKER_COLLISION` -- a separate project literally called "MAGA" and historical imposter tokens also trade under/near the TRUMP ticker |
| `KRW-UNI` | PAPER_ELIGIBLE | OBSERVATION_POOL (`IDENTITY_UNRATIFIED`) | `HOLD_TICKER_COLLISION` -- an unrelated Sui-chain meme project also uses the exact ticker UNI |

No number was adjusted to preserve the prior ~28 estimate, per your
instruction. The remaining 24 previously-eligible markets are unaffected.

## 6. Methodology note on `HOLD_TICKER_COLLISION` (24 of 26 holds)

Most held markets fall under `HOLD_TICKER_COLLISION`. Reviewing the
underlying research notes, these fall into two tiers:

* **Specific, named competing project found** (high-value findings): `LIT`
  (see §3), `ARB` (a 2023 scam-token pump mistaken for Arbitrum), `CAP`
  (other unrelated Etherscan-labeled "Cap" contracts), `BABY` (four other
  unrelated BABY-ticker tokens), `MON` (Pixelmon's "MON Protocol"), `SKY`
  (the older, unrelated "Skycoin"), `PUMP` (an unrelated Blast-ecosystem
  PUMP token), `JUP` (a small unrelated "Jupiter Project"), `TIA`
  (deprecated "Tiamonds [OLD]"), `TRUMP`/`UNI`/`WIF`/`WLFI`/`XPL` (specific
  named collisions per §5/evidence file).
* **Precautionary flag, no specific competing project confirmed**: `OP`,
  `SEI`, `STX`, `SPX`, `PENGU`, `PEPE` -- the researcher's own notes say
  "flagging out of caution... no exhaustive search performed" for these
  short/generic tickers, without naming an actual comparably-relevant
  competing project.

This PR does not collapse that distinction -- both tiers hold identically
under today's strict rule (`ticker_collision_risk: true` => hold, full
stop), consistent with your "don't loosen evidence to hit a number"
instruction. If you want a two-tier treatment (e.g. promote the
precautionary-only tier after a second research pass), that is a
follow-up decision, not something this PR decided unilaterally.

## 7. Mapping decisions for the CIO

1. **`KRW-LIT`'s taxonomy `reason` text** (drafted in P3-12-TAX-01, factually
   describes Litentry, not Lighter) -- accept a follow-up correction PR, or
   leave as-is since it has no safety effect (LIT was never eligible).
2. **The 6 "precautionary-only" `HOLD_TICKER_COLLISION` markets** (§6) --
   accept as held, or commission a second, more exhaustive research pass
   specifically for `OP`/`SEI`/`STX`/`SPX`/`PENGU`/`PEPE` to see if any
   should move to `VERIFIED_CANDIDATE`.
3. **`KRW-PROS`/`KRW-ESP`/`KRW-BABY`/`KRW-PLUME`** (all "medium" confidence,
   `HOLD_MISSING_SECOND_SOURCE`) -- newer/smaller projects where a deeper
   research pass might resolve to "high" confidence.
4. **Whether to proceed to `ratified identity registry` preparation** using
   exactly these 55 `VERIFIED_CANDIDATE` markets, or wait for #2/#3 above.

## 8. Safety boundary confirmation

* No identity registry was ratified; every registry candidate stays
  `PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY`.
* `config/upbit_exclusion_taxonomy.json` and
  `config/upbit_tradeable_universe_policy.json` `approval_status` fields
  are unchanged.
* This PR is a draft PR -- not merged, not marked ready.
* No identity registry and policy were ratified together.
* No Upbit order/withdrawal/private endpoint was called; all research used
  public, read-only sources (CoinGecko, official project docs/sites, chain
  explorers).
* Every `authority` field in the evidence packet is hardcoded `false`
  except `review_only`.
* KIS/Portal work areas were not touched.
* No new strategy, market-judgment logic, or yield optimization was
  developed. Candidate NONE and all order authority remain unchanged.

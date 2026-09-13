# KR PAPER market context -> P2-03 consumption receipt

This opt-in developer path consumes the immutable Stage1
`data/latest_kr_paper_runtime_decision.json` through the existing
`rotation/korea_capital_rotation.py` consumer and current-ratified proof
adapter. It does not fetch data, submit orders, change a policy, write the
briefing pointer, or register a schedule.

## Run

Use a full reviewed source commit containing the canonical Stage1 decision
and P2-03 inputs. The current-ratified policy, binding, calendar and existing
Leadership/Breadth files must match that commit's exact bytes locally.

```sh
python3 .github/scripts/korea_capital_rotation_ledger_proof.py \
  --current-ratified-policy \
  --prior-date 2026-09-10 --current-date 2026-09-11 \
  --paper-runtime-source-commit b1e904ce9af380f73fc7d0a54496907523d39180 \
  --evaluation-at 2026-09-13T03:00:00Z \
  --paper-consumer-out /absolute/external/path/consumer.json
```

The example time is a reproducible historical consumption time, not today's
time. A current invocation must use its actual timezone-aware evaluation
time; the canonical decision's original evaluation time is retained
separately. Before publication or at/after the supplied E-session close,
the display input fails closed. No new TTL policy is introduced.

## Output and authority boundary

Output schema: `korea_capital_rotation_paper_consumption/1`.

- `market_context`: confirmed regime, direction/confidence, separate raw
  candidate and hysteresis, and the supplied D/E display validity boundary.
- `rotation`: the unmodified, independently validated packet/4 when one can
  be built, or null plus exact missing-input/policy/validation reasons.
- `lineage`: immutable source commit, exact runtime file hash, original
  decision/qualification/manifest/history hashes, pinned P2 input files
  (including explicit absence), policy/packet hashes and consumer code hashes.
- `payload_sha256`: canonical JSON SHA-256 excluding this field. The
  formatted on-disk file has a distinct file SHA-256.
- `stage3_handoff`: readiness **for subsequent contract validation**, never
  candidate eligibility or entry authority. All order, capital, strategy,
  Stage, Buy, Action, Production, trading and REAL authorities remain false.

An authorized display is not a ranking input. Its aggregate leadership
count, e.g. 17 positive sectors out of 46, cannot reconstruct each sector's
relative strength or an exact Theme TOP bucket. Changing the displayed
regime cannot change the nested rotation packet or its hash.

## Actual 2026-09-11 boundary

The #696 decision supplies NEUTRAL / DETERIORATING; RISK_OFF is still a raw
candidate with confirmation 1/2. At source commit `b1e904ce...`, the existing
`data/observations/korea_leadership_context/2026-09-11/packet.json` and
same-date Breadth context packet are absent. The current ratified rotation
policy starts on 2026-09-14 and must cover **both** observation dates. Thus
the 2026-09-10 -> 2026-09-11 pair cannot yield eligible rotation buckets,
even if its missing historical packet were later supplied.

The real producer already exists: `korea_leadership_live_fetch.py` ->
`korea_leadership.py::build_transform`, with the dependency-ordered
Breadth -> Leadership -> current-ratified proof workflow. Required outputs
include each sector's identity, own benchmark, relative strength, exact
lookback/session window, forward availability, source lineage and policy
hash; the Stage1 display aggregate is not this producer's contract.
The existing 18:10/18:25 KST scheduler owns subsequent natural source pairs.
No source history is requalified or historical policy backdated here.

## Stage3 handoff

The receipt itself is **not** an accepted `rotationPacket`. The existing
Stage3 gate requires a `korea_capital_rotation_packet/4`, its independently
expected hash, and independently bound asset/universe and D/E profile
inputs. Once available, only `rotation.packet` can be supplied to that
existing validator; `rotation_packet_ready_for_contract_validation=true`
does not mean the Stage3 gate passed. Actual D/E identity, independently
authorized same-Theme membership, exact TOP bucket, price/cost and forward
evidence remain the existing downstream checks. This change does not edit
Stage3's contract, candidate lookup, reviewed implementation pins or files.

## Verification

```sh
python3 -m unittest discover -s test -p test_korea_capital_rotation_paper_consumption.py -v
```

Tests separate actual committed-source consumption from synthetic-only
packet-present branches. They cover integrity/authority/time/date failures,
immutable policy drift, the existing transform's byte preservation, CLI
boundaries, external storage/readback, and unchanged briefing-pointer bytes.

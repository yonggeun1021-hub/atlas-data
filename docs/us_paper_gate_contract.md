# US PAPER Gate contract — US 1-1

Status: implemented Gate/state-contract mechanism; current operational state
`LOCKED`. No broker, account, capital, Production, or Trading authority is
granted.

As-of: 2026-08-30 23:52 UTC

Audited baseline revisions:

- public `atlas-data`: `eb831846c825cc8e6a97957688ec84283a5e8909`
- private `atlas-private-evidence`: `c1ca4da7614e6d399a5f86287e976d32b7f17a3c`

Exact rebase baseline (append-only follow-up):

- public `atlas-data`: `dce5f0b72be89bfd1e1023bf74ec605f524d7a8a`
- private baseline remains `c1ca4da7614e6d399a5f86287e976d32b7f17a3c`
- the rebase changed no KRX, Crypto, common, or private-owned path

Final green-baseline rebase (append-only follow-up):

- 9-0 rolling-evidence recovery merge: `9392666fb92ba310e02f4b9ba6d9ee004c0f5149`
- public `atlas-data` rebase base: `a15dbfa50ca2b7a9126ac7376646d34e1604b5f6`
- private `atlas-private-evidence` audit head: `6b28629beab066912c71c572c303bd51d581f893`
- private #123 internal US PAPER ledger and KRX/Crypto-owned changes remain
  separate; no public US 1-1 path is shared

Final common-funnel integration rebase (append-only follow-up):

- public `atlas-data` rebase base: `7e6021fcb866027b3b6caa28405dd0d9b3e90875`
- this base includes merged #504 common PAPER candidate funnel; US 1-1
  consumes no #504 path and changes none of its files
- private audit head remains `6b28629beab066912c71c572c303bd51d581f893`

## Outcome

US 1-1 introduces a market-isolated US lifecycle:

```text
LOCKED
  -> SHADOW
  -> PAPER_CANARY
  -> PAPER_ACTIVE
  -> PAPER_VALIDATED
  -> LIVE_REVIEW
```

`COMMON_SAFETY` and the US market Gate are independent contracts.  The common
contract is applied only to the US PAPER lane; KRX and Crypto results are
diagnostic context and cannot change the US state.  Any `COMMON_SAFETY` result
other than `PASS` keeps US at `LOCKED`.

The committed assessment is `LOCKED / NONE`.  `COMMON_SAFETY` is `UNKNOWN`
because natural US internal-PAPER kill-switch and restart reconciliation
receipts do not exist.  `US_SHADOW` passes its own bounded zero-capital checks
but is effectively `UNKNOWN` behind that prerequisite.  `US_PAPER_CANARY_START`
independently fails because candidate, entry, hold/exit, size, and non-NONE
P8-13 US proposal authorities are absent or unratified.

## Permanent authority boundary

All six states, including `LIVE_REVIEW`, enforce:

- PAPER-only;
- broker POST count `0` and `broker_post_authorized=false`;
- REAL authority `false`;
- live-account order authority `false`;
- real-capital authority `false`;
- Production authority `false`; and
- Trading authority `false`.

`PAPER_CANARY` may eventually authorize only one bounded Atlas internal US
PAPER-ledger plan.  `PAPER_ACTIVE` may eventually authorize only scheduled
internal simulation.  No state in this v1 contract contains a broker adapter,
endpoint, credential, order request, or account write.

`LIVE_REVIEW` means review-only.  It is not `LIVE` and cannot be interpreted as
permission to create a live route or use real capital.

## CI semantics

Focused US Gate CI verifies contract parsing, fail-closed evaluation,
cross-market isolation, tamper rejection, and the permanent authority envelope.

**전체 저장소 CI는 회귀검사일 뿐 운용승인이 아니다.** Full-repository CI is
regression evidence only, not operational approval.  Neither focused CI nor
full CI may advance a Gate, create an approval reference, authorize a ledger
mutation, or grant broker/capital authority.  State advancement requires the
exact evidence and approval authorities named in the market contract.

## State contract and WBS

| State | Opens only after | Maximum permitted effect | Exit evidence owner |
| --- | --- | --- | --- |
| `LOCKED` | default/fail-closed | assessment only | US 1-1 owner |
| `SHADOW` | `COMMON_SAFETY` + US zero-capital Shadow PASS | no PAPER mutation | US Shadow owner + independent validator |
| `PAPER_CANARY` | ratified candidate/entry/exit/size + non-NONE P8-13 + exact internal-ledger approval/restart receipt | one bounded internal US PAPER ledger | CIO PAPER canary approver + independent validator |
| `PAPER_ACTIVE` | terminal canary + ratified internal schedule + kill-path evidence | scheduled internal US PAPER simulation | US PAPER operations + security review |
| `PAPER_VALIDATED` | 30 natural calendar days + complete lineage + pre-ratified criteria + independent PASS | validated internal US PAPER only | independent validator |
| `LIVE_REVIEW` | exact review packet + user acceptance | review only; every REAL authority remains false | CIO review + user final-capital approver |

The 30-day window does not start at merge or CI green.  It starts only after
`PAPER_ACTIVE` is evidence-derived from exact approved inputs.  Replay, manual,
synthetic, and backfilled days do not count.

## Canonical-source and ownership audit

Before implementation, the CIO Doctrine, Master WBS Tracker, Cockpit, Master
Map, public/private main, and all open PRs were read.  No existing `US 1-1`,
`COMMON_SAFETY + US_PAPER`, or US market lifecycle implementation was found.
The closest analogue is the KRX Gate, which remains untouched and is not
imported by the US evaluator.

Open public PR ownership at the audit baseline:

| PR | Exact head | Owner scope |
| --- | --- | --- |
| #506 | `000df84d1f40732c147997040cf3ffbb442ff686` | US investable registry and completed market-data contracts; no lifecycle Gate paths |
| #504 | `278cbfea8a2c5cf205b74165bd24982df7078e29` | Common three-market candidate funnel; no US market lifecycle files |
| #501 draft | `c638210bbbd6d22590fce30b65219267f9d73f38` | Crypto/Upbit identity, taxonomy, decision, tests, and `run_all.py` |
| #475 draft | `f8d22522bf7d8ff3df237995a6ceed2668767087` | Upbit microstructure timestamp evidence |

Open private PR #121 owns the three-market hedge PAPER lane and #122 owns the
Crypto runtime pin; neither modifies this public repository.  Private #120
merged as `c1ca4da7614e6d399a5f86287e976d32b7f17a3c` and owns external US PAPER
broker compatibility under `config/us_paper_broker_compat/`,
`private_evidence/us_paper_broker_compat/`, and its private docs/tests.  It
keeps admission `CLOSED`, GET/POST `0`, and is contractually separate from
transport-free `US_INTERNAL_PAPER`.

KRX Wave work owns existing
`config/krx_*`, `shadow/krx_*`, `docs/krx_*`, private KIS/ledger/runtime paths,
and their tests.  Crypto work owns existing Upbit/Crypto modules, observations,
workflows, and tests.  US 1-1 owns only the newly added `us_paper_*` paths and
the dedicated `us-paper-gate.yml` workflow.

## Current Gate results

| Gate | Result | Basis |
| --- | --- | --- |
| `COMMON_SAFETY` | `UNKNOWN` | natural US runtime kill/restart receipts absent |
| `US_SHADOW` | effective `UNKNOWN`, own `PASS` | bounded zero-capital mechanism exists; common prerequisite not PASS |
| `US_PAPER_CANARY_START` | `FAIL` | candidate/entry/exit/size/P8-13 authorities absent or unratified |
| `US_PAPER_ACTIVE` | `FAIL` | canary prerequisite fails; schedule and kill evidence unknown |
| `US_PAPER_VALIDATED_30D` | `FAIL` | active prerequisite fails; natural day count is zero |
| `US_LIVE_REVIEW` | `FAIL` | validation prerequisite fails; review packet and acceptance unknown |

## Follow-up sequence

1. Complete upstream US PIT membership/delisted-price blockers and ratify exact
   candidate, entry, hold/exit, size, and cost rules outside this Gate layer.
2. Produce and independently rederive one non-NONE P8-13 US internal-PAPER
   proposal with no private account facts and all REAL authority false.
3. Approve one exact internal-ledger canary; prove simulated lifecycle,
   idempotency, tamper rejection, kill behavior, and restart reconciliation.
4. Separately ratify the internal schedule and begin the forward-only 30-day
   observation window only after `PAPER_ACTIVE` is reached.
5. Grade against outcome-independent criteria ratified before the window and
   prepare `LIVE_REVIEW` only after independent PASS.

No common-file patch is required for US 1-1.  If a future integration needs
`run_all.py` or the shared `actions-pass.yml` to invoke this focused test, make
that as a separate follow-up after PR #501 (which currently owns `run_all.py`)
lands or closes.  The exact follow-up is: add
`python3 validation/tests/test_us_paper_gate.py` to the shared regression list
without changing any authority text or state transition.

## 2026-08-31 append-only policy decision

The user ratified the following decision for **internal virtual US PAPER only**:

- `humanApprovalRequired=false`;
- `userReceiptRequired=false`;
- every Hard Gate must be present and explicitly `PASS` before automatic
  transition; null, missing, `UNKNOWN`, or `FAIL` is fail-closed; and
- broker POST remains `0`, while REAL, live-account, and real-capital authority
  remain `false`.

This removes a human/user receipt as an internal virtual-PAPER transition
condition; it does not weaken candidate, entry, exit, size, PIT, kill,
reconciliation, natural-sample, or independent-validation Hard Gates.  It also
does not apply to the later review-only `US_LIVE_REVIEW_USER_ACCEPTED` check and
cannot create live authority.

## 2026-08-31 pre-PR canonical readback

The audit was repeated after both repositories advanced.  Public #502 and #505
merged; scheduled/data commits advanced public main to `eb83184…`.
Private #118/#119/#120 advanced private main to `c1ca4da…`, including the
separate external-broker compatibility boundary described above.  No
`us_paper_*` public lifecycle path existed on the new main.  Public #506 owns
the upstream US registry/completed-bar contracts, public #504 owns the common
candidate funnel, and #501/#475 plus private #121/#122 own disjoint files.  The
US 1-1 file boundary therefore remains non-duplicative.

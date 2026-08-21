# P8-06 Action / Bear-Hedge / Portfolio Summary Contract

`briefing/action_risk_portfolio_summary.py` assembles a read-only daily view
from the exact P8-02 unified decision, P6 action/invariant/hedge sources, and
P7 portfolio risk packets. Each source is self-hash checked, matched to its
contract identity and authority map, and recursively checked for unauthorized
action, target, size, hedge, or order content. Contract v2 invokes the
production validators for P7-02 position sizing, P7-03 concentration, and P7-06
planned loss. P7-02 position sizing is fully
revalidated with its embedded Constitution, bucket membership, sizing input,
and external policy. P7-03 and P7-06 are re-derived from their exact embedded
inputs and policy/Constitution. Maximum/target weights, binding limits, and
breaches are evidence only.

The same production-validation boundary now covers the P6 sources: the three
market Cash/Exposure packets, Long/Short invariant, three market Regime/Inverse
packets, Hedge Eligibility, and Bear/Hedge Budget. Their action boundaries,
active records, derived rows, summaries, lineage, and closed authorities are
validated before presentation. Cash and inverse source slots also require their
exact US/Korea/Crypto market identity. A recomputed source hash cannot hide a
mutation to those derived or authority-bearing fields; the validators do not
claim to reconstruct source inputs that their v1 output schemas do not embed.

P7-04 Market/Theme Budget and P7-05 Crypto Exposure Limit also pass through
their production output validators. Assessment structure, totals, risk result
and breach derivation, summaries, action closures, authority, and lineage are
checked before their findings enter the briefing summary.

The output embeds the exact 15-source bundle and unavailable-reason map, then
rebuilds itself during `validate_packet()`. A self-rehashed summary mutation or
a self-rehashed P7 semantic mutation therefore fails closed.

The six briefing categories are fixed: Buy, Watch, Reduce, Hedge, Exit, and
Nothing. Under the current upstream contracts every category remains
`NOT_EVALUATED` with `action=null`. In particular, an absence of authorized
actions is never relabeled as a positive “Nothing” action.

Available P7 sizing and breach evidence is displayed as risk findings. A
calculated size or a concentration, market/theme, Crypto, or planned-loss
breach remains evidence, not a buy, reduction, or exit instruction. Unavailable
optional policies stay explicit with reason codes; the unified daily decision
is the only required source.

This read model cannot interpret a Rule, select a hedge, generate an exit,
adjust a position, create an order, or authorize Production/trading. CLI output
inside the repository tree is forbidden.

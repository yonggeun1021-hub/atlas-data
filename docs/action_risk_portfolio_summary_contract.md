# P8-06 Action / Bear-Hedge / Portfolio Summary Contract

`briefing/action_risk_portfolio_summary.py` assembles a read-only daily view
from the exact P8-02 unified decision, P6 action/invariant/hedge sources, and
P7 portfolio risk packets. Each source is self-hash checked, matched to its
contract identity and authority map, and recursively checked for unauthorized
action, target, size, hedge, or order content. P7-02 position sizing is fully
revalidated with its embedded Constitution, bucket membership, sizing input,
and external policy; maximum/target weights and binding limits are evidence
only.

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

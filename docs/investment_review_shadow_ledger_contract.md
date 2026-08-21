# P10-06 Investment Review Shadow Ledger

This append-only record stores the exact P8-07 review packet and hash chain for
learning and audit. It records PASS, REJECTED, and BLOCKED reviews alike.

`proposal_observed=true` means only that a zero-capital review draft existed. It
does not grant Shadow eligibility, change Atlas Stage, allocate capital, create
an action, or create an order. Capital is always `{authorized:false, amount:0}`
and action/order/stage change are always null.

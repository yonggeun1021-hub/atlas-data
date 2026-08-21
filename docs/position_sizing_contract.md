# P7-02 Position Sizing

This offline engine calculates a candidate's maximum and target NAV weight only
when both the Portfolio Constitution and a separate sizing policy are externally
CIO-ratified and effective for the requested date.

The maximum is the minimum of seven independently displayed limits: remaining
Constitution deployment capacity, available cash, remaining bucket capacity,
the Constitution position maximum, the evidence-state maximum, remaining
Portfolio planned-loss capacity divided by stop distance, and the ratified
per-position planned-loss allowance divided by stop distance. The target is the
maximum multiplied by the policy's target-utilization fraction.

The candidate must have exactly one active P7-01 `CANDIDATE` membership with
matching market and identity/discovery/Rule SHA lineage. A planned stop at or
above entry is invalid. A stop distance beyond Constitution B5 or any exhausted
limit produces `SIZING_BLOCKED` and zero maximum/target size.

The repository has no default sizing policy or parameters. A calculated size
does not select a candidate, authorize ENTRY, create an action/order, or grant
broker, Production, or trading authority. The output embeds and revalidates all
source packets and may be written only outside the repository.

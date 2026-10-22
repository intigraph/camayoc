# Competency questions — event-derived WorkItem burn-down

1. For one protected board, assignee and priority, how many active items were in
   each status on each UTC day after an explicitly pinned opening balance?
2. How many native create and close transitions entered that cohort each day?
   A baseline or deletion must never invent a creation or a closure.
3. Is the event consumer caught up, or are its numbers stale or indeterminate?
4. Does one bounded stored aggregate reproduce an independent scoped tracker
   count, within the measured read-cost budget?

Use the existing governed Observation class, canonical Seeds Action identities
and public schema.org/Seeds predicates. Event contributions are immutable observed
facts, not another current tracker or an inferred historical status timeline.
DORA and burn-down share the `queries/` JSON stored-query layout; their event
families and custody remain distinct.

# Directive traceability deadlines

Implementation status: source candidate; installation and natural alert proof pending.

CANDIDATE: Which loaded Directive shape accepts a deferral date, and where
are overdue untraced directives surfaced to administrators?

Question mapped by Jev on 2026-11-14: abstained (suite123, confidence0.86).
This is a candidate competency, not an accepted mapping or coverage claim.
The new public term is owned by Quechua; Camayoc only consumes it.

The executable read is `camayoc_overdue_directive_traceability`, evaluated
against ROOT plus the configured trusted declared plane. The existing
`directive-lapse` adapter uses the explicit date as its persisted next check;
when due it emits LAPSED through the existing administrator alert path.
Publishing source is not installation or scheduled-delivery evidence.

## Deadline cardinality shape (aegis-b9k)

Implementation status: source only, not loaded in any Quipu. The Quipu
shape-set proposal, the Gold declaration, acceptance and a live check are still
to do. Until then the shape has no live effect.

`shapes/directive-deadline.shapes.ttl` is a standalone REJECT shape: at most one
`traceabilityDueAt` across the stable and legacy predicates, typed
`xsd:dateTime`, with a timezone. Its bytes are pinned, so the proposal and the
Gold declaration pin the same file. It is defense in depth. It does not fix the
batch failure on its own, because it cannot see two of the ways a conflict
arises:

- **A later write.** Quipu validates the incoming write, not the stored state.
  A second deadline added by a later write is accepted (measured on quipu
  0.19.5). The writer guard and Quipu's merged-state cardinality work
  (aegis-ew90) cover this case.
- **Two graphs.** `directive_deadline.deadline()` unions ROOT and the declared
  graph. If each graph holds one different deadline, every write conforms and
  the reader still reports a conflict.
  `tests/test_directive_deadline_shape.py` pins both halves of this.

What carries both cases is the adapter isolating the conflicting Directive per
item (camayoc#299), so the rest of the batch is still evaluated.

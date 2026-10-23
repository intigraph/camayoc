# Review ages on entity IRIs

Three entities (a decision, directive and count claim) declare an elapsed review
age and an owner. Before, all three read UNKNOWN and a matching change routes
nothing. After, all three read DUE with the owner and the change routes to its
exact entity. A legacy entity stays DUE; an entity without an age stays absent.

The same probe uses in-memory RDFLib graphs on base `135d92a` and the candidate.
The output binds each arm to the SHA256 of both executed source modules. This
is source behavior, not installed event, seed, Brief or notification acceptance.
No live graph request or write is made. RDFLib is the existing test dependency.

Run from the repository root:

```sh
bash docs/demos/review-entity-iris/run.sh
```

The retained output and timing form an output-only terminal recording. Replay:

```sh
scriptreplay --log-out docs/demos/review-entity-iris/output.log \
  --log-timing docs/demos/review-entity-iris/timing.log
```

The small recording lives beside this reproducer with normal Git retention;
no separate upload or inline playback service is required.

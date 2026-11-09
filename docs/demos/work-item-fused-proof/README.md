# Same-version work-item proof

A repeated source version retains its fresh canonical read. Its metadata effect
previously needed a separate type ASK and comment SELECT. The new bounded proof
uses one SELECT, while new writes and pending effects retain the old readback.

The [recorded lab output](output.json) uses a fresh disposable loopback server
with a clean, explicitly checked revision. Eight cases agree with the legacy
verifier, including missing/wrong types, escaped literals and the 128/129-comment
boundary. Ten paired samples require 20 legacy proof requests versus 10 fused
requests. The binary build profile was not independently established. These paired
small-fixture timings compare the same binary and do not predict production
cursor recovery.

Twenty reads overlapped an atomic writer alternating two invalid states. No read
accepted a valid type/comment combination; the output records how many commits
actually occurred during those reads. The actual query execution profile is
retained separately from a representative bound-comment SQLite EXPLAIN. That
EXPLAIN covers the visitor access shape, not the complete physical SPARQL trace.

Run with a reviewed server binary and its exact clean source revision:

```sh
PYTHONPATH=scripts python3 scripts/qualify_work_item_fused_proof.py \
  --server /path/to/reviewed/quipu-server --expected-sha REVIEWED_FULL_SHA
```

The helper uses a temporary database and loopback address. It copies no store or
credentials and refuses a revision mismatch. Run it under the deployment's test
HOME guard. The same-version tick still admits at most 12 logical deliveries;
spare requests do not increase the 57-request, 40-second or 256-item limits.
The 60-second schedule and the unconditional admission floor are unchanged.

SELECT diagnostics use `fused_control.schema=workitem-fused-readback/v1`. Existing
ASK `control` receipts retain their original meanings; a SELECT is never reported
as an ASK result. No cursor reset, repair, replay or stored-effect resend is added.

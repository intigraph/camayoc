# Transcript pending-write recovery

Status: source implementation; installed scheduled acceptance remains required.

Before `/knot`, the producer atomically journals the canonical JSON wire bytes,
SHA-256, byte count, original record and task set, and creation time. The private
journal is fsynced before the POST; a nonblocking process lock serializes state
and journal transitions. Recovery never rebuilds the original payload from a
changed corpus or current shapes.

A pending snapshot is read in the configured observed plane. Its owned digest,
reference, byte count, transcript links, activity time and complete declared task
set must match before state is committed and the journal removed. A partial or
different snapshot is superseded, not absent. An empty result requires a positive
Session control; errors and missing response fields remain UNKNOWN.

Only two absent observations, separated by one second, permit a retry of the
retained canonical bytes. The retry still requires complete read-back. Transport
errors, explicit refusal and unproven read-back preserve the pending journal.
Legacy journals without the original payload refuse automatic replay.

Superseded or legacy journals require explicit owner adjudication. The separate
`--archive-pending REASON_FILE` mode preserves the exact journal bytes in a private
content-addressed archive with a reason and timestamp, verifies the archive, and
then removes the pending marker. It never posts or runs a fresh scan. A later
scheduled run may publish the fresh corpus only after that explicit disposition.
This command is not authorization to archive a particular production journal.

Fault tests cover a committed POST with a lost response, no duplicate resend on
landed recovery, two absent reads and an identical retry, partial/different task
sets, unknown reads, corrupt/legacy journals, refusal, and exact archive retention.

## Demo

[Replayable terminal output](assets/transcript-pending-output.txt) and
[original timing](assets/transcript-pending-timing.txt) show the same isolated
committed-POST/lost-response fixture. Base `ca4164f84bd1` refuses recovery and
leaves state uncommitted. Candidate `7642ad9f16e1` retains the payload, confirms
complete read-back and commits state without a second POST. The producer and
tests are byte-identical to the demonstrated candidate after the caption and
recording commit; later CI and installed acceptance remain separate.

This is a mocked transport calling the actual producer functions, not a live
store or deployment. Repeat with
`python3 docs/design/assets/transcript-pending-demo.py`. Download the two text
files and run `scriptreplay --log-out transcript-pending-output.txt --log-timing
transcript-pending-timing.txt` for playback; an inline browser player is not
claimed. The small source fixture and recording are retained in this design
folder with the PR, under the repository's ordinary Git history.

Recovery compares `STR()` projections of subjects, predicates and values, so
loaded shape prefixes cannot turn an identical committed snapshot into a false
supersession. A native disposable-server control loads a prefix and verifies
both compact wire results and successful string-projection recovery.

A definite validation refusal is retained with `outcome: refused`. Later runs
stop before graph reads or another POST; an operator must adjudicate and archive
that journal before a new attempt. Transport uncertainty remains `attempted`
and follows the read-back reconciliation rules above.

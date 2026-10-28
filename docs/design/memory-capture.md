# Design: memory capture and promotion

Status: BUILT behind an opt-in switch. Tracked as aegis-gshu.4 under the epic
aegis-gshu. This note settles where capture hooks in and how promotion runs.
The entity contract is
[agent-memory-entities.md](agent-memory-entities.md) §5.

Code captures and the agent does not. Capture is cheap and never refused.
Promotion is the only step that has to pass the shapes.

## 1. Where capture hooks in

| Harness event | Hook | What it captures |
|---|---|---|
| `UserPromptSubmit` | camayoc plugin `hooks/hooks.json` → `cy hook user-prompt-submit` | the prompt (`captureSource` prompt) |
| `Stop` | same file → `cy hook stop` | the final assistant answer (`answer`) |
| `SessionEnd` | same file → `cy hook session-end` | nothing new; flushes the spool and promotes this session's candidates |
| none | `cy note "<text>" [--kind directive\|decision] [--about X]` | an explicit capture (`explicit`) |

**Why the camayoc plugin hook file and not bobbin.** The two hook paths
already installed are bobbin's (Rust: `bobbin hook inject-context` on
UserPromptSubmit, `session-context` on compact) and camayoc's plugin
(`hooks/hooks.json`, SessionStart today). Bobbin has no Stop or SessionEnd
handler, and adding capture there means Rust and a bobbin release for what is
a camayoc concern. Bobbin's job here is recall (gshu.2). So this extends
camayoc's existing hook file with three entries that share one Python entry
point (`cy hook <event>`). No new hook system and no new binary. tambo
installs the same three commands for Codex, whose Stop payload carries
`last_assistant_message` and is read first (gshu.9).

**Cost.** A hook durably appends one JSON line under a short local mutation
lock, then starts a detached `cy capture flush` unless a replayer is running.
Appending checks sizes and at most one tail byte; it never parses the pending
spool. Replay rotates the active file into an older immutable prefix under
that lock, then performs all scanning, acknowledgment rewrites and network work
outside the hook's mutation lock. A torn append is separated from the next
capture by a newline. Hooks exit zero and print nothing. `cy flush --hook`
starts a detached bounded worker (20 visited records, 750 ms network budget),
so SessionStart does not scan the spool either. Local worker failures do not
block the harness.

The capture FIFO acknowledges successful heads and quarantines refused,
malformed/torn, destination-mismatched or unbound heads before continuing.
Network failures and uncertain RPC results retain the head and its tail for
ordered retry. Quarantine archival or acknowledgment errors also retain the
head; a stable archive ID avoids duplicate archives after a local failure.
Every visited record or malformed fragment counts against the replay budget.
New entries record destination and origin without credentials; they never get
sent to another endpoint or plane. Current credentials preserve recorded origin.
Files and locks are private; the state directory is forced to `0700`.

**Retention.** Captures older than seven days are removed from pending replay
into local quarantine, never sent as fresh captures. Expiry is checked by the
bounded background replayer, including during an outage. Pending active and
older-prefix files share a 16 MiB cap, with a 1 MiB limit per new entry; at
capacity only the new entry is refused and maintenance is requested. Existing
`capture-spool.inflight.jsonl` remains the older prefix. Stop old-version
flushers before upgrading: they do not honor this version's rotation protocol.

**Quarantine.** `cy capture quarantine --json` shows a count, retention limits,
entry IDs, reason categories and timestamps, without raw text or a network call.
`cy backlog` includes `quarantined_captures` separately from pending `queued`.
An unreadable archive gives an UNKNOWN count, not zero. Original bytes (even
torn or invalid UTF-8 fragments) are private in `capture-quarantine/`.

Restore the original `QUIPU_SERVER` and plane, then run
`cy capture quarantine --requeue` to move eligible captures to the pending tail.
This is a local operation; run `cy capture flush` afterward to attempt delivery.
Original timestamp, agent, session, hash and frozen destination are preserved.
No endpoint or plane is rewritten. Expired captures, malformed fragments and
unbound or still-mismatched destinations remain quarantined with skipped reason
counts. A previously refused valid capture can retry after its cause is fixed.
Clear and requeue are mutually exclusive. `cy capture quarantine --clear`
deliberately discards remaining local archives.

Requeue holds the nonblocking capture replay lock and the archive mutation lock.
It durably appends before removing the archive; a full pending spool leaves the
archive intact. A marker recognizes a pending copy after a failed local unlink.
If that copy already replayed before retry, the same deterministic capture IRI
may replay again: this remains at-least-once delivery. Requeue never reports
that the graph accepted a capture.

Quarantine retains at most 16 MiB of encoded archive files and 1,024 entries.
Archives older than seven days **since archival** expire. Malformed archive
metadata uses file mtime for retention and remains ineligible for replay. Background capture
flush, shared replay maintenance and explicit requeue enforce age; every archive
insertion also enforces size/count, evicting oldest archives first. Maintenance
runs even when the typed FIFO is blocked or captures are otherwise empty.
These are lazy bounds: an idle installation needs its next maintenance run to
remove aged archives. Read-only inspection does not prune. Retention reports
expired/capacity/orphaned discard counts and bytes, and cleans interrupted
private staging files. Cumulative counts and the last eviction time persist in
`capture-quarantine-retention.json` (0600, metadata only), so discarded worker
output cannot hide data loss. `cy capture quarantine` shows these totals in
text and JSON, and `cy backlog` includes the same `retention_totals`. Reads do
not acknowledge them; `cy capture quarantine --clear` explicitly clears both
archives and retention totals. A pending durable receipt survives interruption
between unlink and counter commit; corrupt or changed receipt state is UNKNOWN.
Back up evidence before it expires or reaches capacity. Requeue never refreshes
the original capture's separate seven-day pending age.

Ordinary capture flush exits 1 while quarantine needs attention, even after
valid later entries replay. The typed `cy record` FIFO keeps its existing
stop-on-refusal policy; this quarantine policy is capture-only.

**What is skipped.** Prompts under 20 characters, and harness-written prompts
(`<task-notification>`, `<command-name>`, and the like). Text is cut to 4,000
characters. Secrets: matches of the §3 named rules are replaced with
`[redacted:<rule>]`. When a high-entropy string is left over, the capture is
dropped, because it cannot be cut out reliably.

**Opt-in.** Hooks do nothing unless `CAMAYOC_CAPTURE=1`. The plugin is public,
and shipping every prompt to a store is the operator's choice. tambo sets the
switch (gshu.9).

## 2. The capture record

Each capture goes to the `crew:inferred` plane, with IRI
`aegis:mem/capture/<yyyymmddThhmmssZ>-<hash12>`:

| Field | Value |
|---|---|
| `rdfs:comment` | the verbatim text (redacted, clipped) |
| `rdfs:label` | `capture <source>: <first 80 chars>` |
| `aegis:captureSource` | prompt, answer, explicit, stop-check, file-redirect, import |
| `aegis:capturedAt` | xsd:dateTime |
| `aegis:contentHash` | sha256 of the whitespace-normalized text |
| `aegis:recordedBy`, `prov:wasAssociatedWith` | agent; session `urn:camayoc:session:claude:<id>` |
| `aegis:groupId` | `camayoc-capture` (the episode convention) |
| `aegis:kindHint` | optional, see below |

**No `rdf:type`.** This differs from §5 of the entity design, which wrote
`aegis:MemoryCapture, a prov:Activity`. A store with shapes loaded refuses any
class that no loaded shape sanctions. The test store refused `prov:Activity`
with exactly that error. A capture must never be refused for its kind, so it
carries no class and is found by `aegis:captureSource`. A `MemoryCapture`
class can be added through the gshu.12 schema proposal later, and nothing
here depends on it.

**Kind hints are regex, not judgement.** Only a capture with a hint is a
promotion candidate. A plain prompt or answer stays evidence, at one write
and no follow-up. Only words the person wrote promote:

- **Relayed prompts never promote.** A prompt that opens with a relay
  envelope is evidence only, whatever `DECIDED:` or `Stiwi:` lines it quotes:
  - shantytown's `[from <agent>]` signature, including `[from st fleet tend]`
  - `[job ...]`, `[st ...]` and similar headers
  - `<agent-message>`, `<tool_result>` and `<function_results>` blocks
  - `from <agent>:`

  Promotion refuses one too, even with `prompt_author` set.
- **For a prompt, a marker or issuer prefix counts only on its first
  line.** A marker further down is quoted text, such as a pasted handoff or
  a memory line. A `cy record` line counts anywhere in the person's own
  prompt.
- **An `answer` is never a candidate.** The agent quoting a ruling does not
  make it one.
- **Explicit notes and producers are scanned whole.** An agent or producer
  chose to file them.

| Hint | Rule |
|---|---|
| `cy:<entity>` | a `cy record <entity> ...` (or `camayoc record`) call at a line start or after whitespace or a backtick |
| `directive` | a line starting `DIRECTIVE`, or an issuer prefix from config (`Stiwi:` / `Stiwi (10/27):`) plus never/always/must not/do not |
| `decision` | a line starting `DECIDED`, `DECISION` or `RULING`, or an issuer prefix without those words |

The issuer map lives in `~/.config/camayoc/capture.json`
(`{"issuers": {"Stiwi": "person-stiwi"}, "prompt_author": "person-stiwi"}`),
never in this public repository.

## 3. Promotion (`cy promote`)

Candidates are processed oldest first, with no model involved:

1. **Dedupe.** If the same `contentHash` was captured within 30 days before
   this capture, it gets `aegis:duplicateOf` that earlier capture and is
   settled.
2. **Fill.**
   - A `cy record` line is parsed by cy's own argparse parser, so flags,
     sourceKind flags (`--noticed`, `--probed`) and `--supersede` match a
     direct call exactly.
   - A marker is mapped to the entity's fields. The text after the marker is
     the body (`rule`, `chose`). `(key=value, ...)` right after the marker
     gives the other fields by their flag names
     (`DIRECTIVE (applies-to=crew, topic=deploy-window): ...`). The issuer
     comes from the issuer prefix or, for a `prompt` capture, from
     `prompt_author`. `verbatim`, `issuedOn` and `about` are filled from the
     capture. An unknown key refuses.
3. **Record** through `memory.record()`. That applies the same precheck,
   secret rules, key resolution, sourceKind rule, supersession guards and
   quipu shapes as `cy record`. The record gets `prov:wasDerivedFrom` the
   capture.
   - Same key, same body: `unchanged`.
   - Same key, new body: supersedes the old version.
   - Inferred over declared, or another issuer's ruling without
     `--supersede`: refused.

   So promotion never piles up.
4. **Mark the capture.**
   - Promoted: `aegis:promotedTo` the record, plus `promotionOutcome` and
     `promotedAt`.
   - Refused: `aegis:promotionBlocked` (the plain-word refusal lines) plus
     `aegis:suggestedCall`, the exact `cy record` call with
     `<placeholders>` for what is missing.
   - A partial write (the supersession back-link failed) is not marked and
     retries next run.

`cy promote --pending` lists blocked captures with their finishing call; the
stop check (gshu.3) can show the same line. `cy promote --retry-blocked`
retries them, for example after `cy topic add`. A settled capture is never
pending again, so every run is idempotent.

## 4. When promotion runs (no agent polling)

| Trigger | Mechanism |
|---|---|
| A capture lands | chaski `trigger: changes` emitter (`config/chaski/memory-promotion.emitters.yaml`). chaski follows quipu's `/changes`. The adapter `cy promote adapter` subscribes to `kindHint`, `promotedTo`, `promotionBlocked` and `duplicateOf` in the inferred plane, so the adapter runs only when one of those changes. A pending capture is verdict `PENDING` (`baseline_emits`). The command sink runs `cy promote --event`, which promotes that one capture, exits 0 as the receipt, and is idempotent on redelivery. |
| Session end | `cy hook session-end` starts a detached `cy promote --session <id>` |
| Schedule | the emitter's `PT24H` catalogue reconciliation re-discovers any pending capture a missed change left behind |
| On demand | `cy promote` |

No agent is woken by any of these. The event receiver is code. The design's
"every 150 tool calls" trigger is dropped: the change feed already reacts per
capture.

## 5. The extractor hook (OFF by default; never Claude)

Promotion of free-prose rulings needs judgement, and the deterministic rules
do not try it. A configured extractor (`"extractor": {"argv": [...]}` in
`capture.json`) is called only for a capture whose deterministic fill is
blocked or incomplete. It gets `{capture, kind, text, missing, partial,
entities}` on stdin and answers `{entity, args}`. Its answer only ever becomes
a `suggestedCall` marked `suggested by extractor (inferred)`. It never
records.

`CAMAYOC_PROMOTE_EXTRACT=0` disables it even when it is configured. An argv
that names `claude` or `anthropic` is refused, and `ANTHROPIC_*`/`CLAUDE*`
variables are stripped from its environment. Automation and benchmarks never
spend Claude, so use codex or a local model.

## 6. Not done here

- Importing the 34 legacy `stiwi-ruling*` episodes as captures belongs to the
  migration (§6, `cy import-memory`). Once imported, they promote through
  this same path.
- The memory-file redirect (gshu.1) and the stop check (gshu.3) are producers
  that call `cy note` with `captureSource` file-redirect or stop-check. They
  are wired in their own items.
- Production use needs the gshu.12 schema proposal (plane graphs, terms). No
  quipu change is required for capture or promotion.

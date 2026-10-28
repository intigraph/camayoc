<p align="center">
  <img src="https://raw.githubusercontent.com/intigraph/camayoc/main/assets/logo.svg" width="200" alt="Camayoc logo — loose gray strands enter from the left, the keeper's yupana counting board hangs from the main cord at center, and ordered, colored, knotted quipu pendants emerge on the right"/>
</p>

<h1 align="center">camayoc</h1>

<p align="center">
  <em>🧶 Agents that check what was already decided, and record new decisions with how they know</em>
</p>

<p align="center">
  <a href="https://github.com/intigraph/camayoc/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache-2.0"/></a>
  <a href="https://github.com/intigraph/camayoc/actions/workflows/ci.yml"><img src="https://github.com/intigraph/camayoc/actions/workflows/ci.yml/badge.svg" alt="CI"/></a>
  <a href="https://github.com/scbrown/caboodle"><img src="https://img.shields.io/badge/stack-quipu-8B5E3C.svg" alt="Part of the caboodle stack"/></a>
</p>

[Watch the 90-second native bootstrap recording](https://github.com/intigraph/camayoc/blob/main/docs/demos/bootstrap-v0.1.2/README.md).

**Camayoc gives AI coding agents a governed memory: a starter vocabulary for
work, decisions and outcomes, loaded into a [quipu](https://github.com/scbrown/quipu)
knowledge graph that refuses any record that does not say how it was learned
(observed, declared or inferred).** It is for teams whose agents keep
re-deciding things a past session already settled. It comes as a Claude Code
plugin (a skill, a session-start status hook and a bootstrap command), and as
plain scripts any agent that speaks HTTP can use.

The *quipucamayoc* was the Inca official who kept the quipus and certified what
was read back out of them.

## Why you would want it

- **Agents ask before they re-decide.** The skill has them query what was
  already decided, and why, before choosing again.
- **Every fact says how it was learned.** A record without an honest
  `observed` / `declared` / `inferred` tag is refused at write, so a guess
  cannot pass as an observation.
- **It proves its own gate.** Bootstrap sends records that must be refused and
  fails loudly if the store accepts one.

The long form (its place in the stack, what runs today, what it deliberately is
not): [Why camayoc](https://intigraph.github.io/camayoc/why-camayoc.html).

## Install

As a Claude Code plugin, in Claude Code:

```text
/plugin marketplace add intigraph/camayoc
/plugin install camayoc@camayoc
```

From a shell, for any other agent or for scripts:

```bash
git clone --depth 1 https://github.com/intigraph/camayoc
```

There is nothing else to install first: bootstrap brings its own quipu.

## First success in three commands

From an empty directory, bootstrap a governed memory and watch it prove its gate:

```bash
git clone -q --depth 1 https://github.com/intigraph/camayoc
QUIPU_SERVER=http://127.0.0.1:3030 bash camayoc/scripts/bootstrap.sh > bootstrap.log 2>&1
grep -E '^(ontology|shapes|gate: PROVEN — an untagged|camayoc:)' bootstrap.log
```

```text
ontology: loaded (core.ttl)
shapes: loaded (camayoc-core)
gate: PROVEN — an untagged Decision refused, as it must be.
camayoc: governed memory ready. Query first; record at the moment; tag honestly.
```

With no quipu on your machine, bootstrap downloaded one (sha256-checked),
started it, loaded the vocabulary and shapes, and then tried to write a
Decision with no provenance tag. The store refused it. That refusal is the
guarantee everything else rests on. Stop the server with
`kill "$(cat .quipu/server.pid)"`; the full log is in `bootstrap.log`.

## On your own data

| you want to | run |
|---|---|
| check whether a question is covered by the competency suite | `python3 camayoc/scripts/competency.py "<question>"` |
| map a question to a stored query with Jev (needs a TypeSafe key) | `python3 camayoc/scripts/competency.py --map "<question>"` |
| preview an advisory ordered-rubric benchmark without a key | `just jev-bench bench/ordinal/na-htm.jsonl --slot ordinal` ([pilot and replay](https://github.com/intigraph/camayoc/blob/main/bench/ordinal/README.md)) |
| see which competency questions have a stored query | `python3 camayoc/scripts/query_coverage.py` |
| check a proposal against decisions already made | `python3 camayoc/scripts/settled_decisions.py "<proposal>" --declared <file>` |
| see the quarantine planes and their labels | `python3 camayoc/scripts/planes.py status` |

Every script: [Reference](https://intigraph.github.io/camayoc/reference.html).

## Wire it into your agent

With the plugin installed, run this once in Claude Code:

```text
/camayoc:bootstrap
```

It does what the shell bootstrap does, then offers the rest of the stack.
From then on every session opens with a memory status line (`ACTIVE`,
`reachable but gate not proven`, or `unreachable`), and the skill teaches the
agent to query first, record at the moment, and tag honestly. Without the
plugin, `bash camayoc/scripts/bootstrap.sh --with-claude-hooks` adds the same
status hook to `.claude/settings.json`. The plugin also declares a `jev` MCP
server for typed decisions. Details: [Using it with agents](https://intigraph.github.io/camayoc/agents.html).

### Agent-memory CLI (`camayoc`, alias `cy`)

One purpose-built command per memory entity (Decision, Directive, Preference,
Handoff, Reference), generated from `shapes/agent-memory.shapes.ttl`
([design](design/agent-memory-entities.md)). It needs only the Python standard
library and is installed with:

```bash
uv tool install git+https://github.com/intigraph/camayoc
```

This installs `camayoc` and `cy` (the same entry point). `cy prime` prints
the agent primer and live state, `cy record <entity> --help` lists the flags
taken from the shape, and `cy recall <entity>` runs the recall stored queries.
Writes go to `$QUIPU_SERVER` (default `http://localhost:3030`) with the bearer
from `QUIPU_AUTH_TOKEN`, then `QUIPU_AUTH_TOKEN_FILE`, then
`~/.config/quipu/token`. The bearer is never printed. The target store needs
the agent-memory shapes and the registered planes (`scripts/planes.py ensure`).
When Quipu is unreachable, a record is queued under
`$XDG_STATE_HOME/camayoc/` and `cy flush` replays it.

### Typed decisions with jev-mcp

The plugin registers the stdio server in
[scripts/jev_mcp.py](https://github.com/intigraph/camayoc/blob/main/scripts/jev_mcp.py). It exposes these tools:

| Tool | Input and result |
| --- | --- |
| `jev_noul` | `state`, `instructions`; returns a yes/no probability. Optional `true` and `false` describe the criteria. |
| `jev_choice` | `state`, `instructions`, `criteria` (option ID to text); returns a choice and probabilities. |
| `jev_score` | `state`, `instructions`, `levels` (2–10 ordered strings); returns an ordered-level score. |
| `map_question` | `question`; reports a competency-question match and its stored-query coverage, or `abstained` / `human_reads`. |
| `jev_dry_run` | `kind` (`noul`, `choice` or `score`) and that tool's inputs; returns the request without sending it. |

For an agent that reads `.mcp.json`, register the server directly:

```json
{
  "mcpServers": {
    "jev": {
      "command": "python3",
      "args": ["/path/to/camayoc/scripts/jev_mcp.py"]
    }
  }
}
```

Live calls use `TYPESAFE_API_KEY`, then the file named by
`TYPESAFE_API_KEY_FILE`, then the default in
[`jev.resolve_key`](https://github.com/intigraph/camayoc/blob/main/scripts/jev.py). Keep a key file private (`0600`);
do not put a key in the registration file. Dry runs need no key. A missing
or malformed key produces a readable tool error, with no lexical fallback.

Choice adds a none-of-these option by default and reports `chose_none`;
setting `none_text` to `""` deliberately removes that escape hatch.
Treat decisions as **inferred**, retain their request, usage and model, and
check confidence before acting. The server does not retry failed calls.
Usage is logged to `JEV_USAGE_LOG`, otherwise
`SHANTY_ROOT/jev-usage.jsonl`, otherwise the local state path declared in
`scripts/jev_mcp.py`. Logging failure does not retry a decision.

## Deployment events

Camayoc records deployments from the tools that make them, as CDEvents mapped
to `DeploymentEvent`. Each emitter reports what the tool itself says
happened, including failures that a surrounding job reports as green.

| Tool | How | Standard |
| --- | --- | --- |
| GitHub or Forgejo Actions | [reusable Action](https://github.com/intigraph/camayoc/blob/main/actions/deployment-event/README.md) | CDEvents `pipelineRun.finished` |
| Ansible | [callback plugin](https://github.com/intigraph/camayoc/blob/main/emitters/ansible/README.md) | CDEvents `pipelineRun.finished`, one per host |
| Terraform | [`apply -json` wrapper](https://github.com/intigraph/camayoc/blob/main/emitters/terraform/README.md) | CDEvents `pipelineRun.finished` |

## Before you start

**Platforms.** Bootstrap downloads a quipu release on Linux x86_64. On other
platforms it builds quipu with `cargo`, or uses a `quipu-server` already on
your `PATH`. The scripts need `bash`, `curl` and `python3`.

**Exit codes.** Bootstrap exits 1 if nothing could be installed, 2 if the store
accepted a record it must refuse, and 3 if no verdict could be reached. See
[Getting started](https://intigraph.github.io/camayoc/getting-started.html#when-something-fails).

## What's next

- [The camayoc book](https://intigraph.github.io/camayoc/): getting started, agents, reference and the design notes, in reading order
- [Docs map](https://intigraph.github.io/camayoc/docs-map.html): every document in this repository, routed
- [Ingress](https://intigraph.github.io/camayoc/design/ingress.html): how knowledge earns its way into the graph

## 🧺 The stack

Caboodle installs these together and proves each one works; every tool also stands alone.

## Contributing

```bash
just test
just check   # the local quality gate
```

## 📜 License

[Apache-2.0](https://github.com/intigraph/camayoc/blob/main/LICENSE)

### Incremental verdict adapters

`blocked_by.py` and `review_due.py` also accept `--describe` and `--changes`
for Chaski's incremental protocol. `--describe` prints watched predicates, type
IRIs and trusted planes without querying the store. `--changes` consumes one
JSON request with `now` and either `discover: true`, `route: [fact changes]`, or
`items: [local keys]`. Responses carry protocol `version: 1` and respectively
`items`, `items`, or `scope`/`records`/`next_checks`.

Routing follows changed observations to their WorkItems, then reverse blocker
edges to dependent work. Review routing follows verification links, including
retracted `old_value` links. Existing graph-plane and legacy/public vocabulary
rules apply unchanged. Discovery reads the candidate catalogue only; Chaski
queues bounded evaluation batches instead of scanning verdicts in one process.
The command budgets graph reads at eight per second and caches identical reads
within a request. The ordinary full-evaluation CLI remains the parity oracle.

Future review ages and date blockers supply exact deadlines. Only external
probes and arbitrary resolution queries retain individual 15-minute checks,
because their dependencies cannot be enumerated from graph changes. Missing
facts and unanchored ages remain UNKNOWN and await a relevant change; they do
not cause a repeated population scan. Transport failures fail the invocation,
so the consumer retains pending work. This protocol performs no graph writes.

### Reflection cycle and lane expiry

Tracker ingress classifies a `dream-cycle` label as `DreamCycle`. Its ordinary
idle limit follows tracker activity. A crew cycle also has the `dream` label and
the controller's `DREAM consolidate:` or `DREAM propose:` title. Its creation records an immutable
observation of the reflection lane, typed `Service`, with kind `DreamLane` and a
12-hour idle limit. The lane clock uses cycle creation time; later comments,
completion, retries and out-of-order ingress cannot postpone or rewind it.

The existing change adapter subscribes to kind changes, observations and age
attributes. Persisted deadlines drive lapses without adding a periodic scanner.
Review verdicts carry a unique kind as receiver context; ambiguous kinds omit it
and retain the receiver's ordinary policy. Routing severity belongs to reviewed
receiver configuration. Load the expanded core shape set before enabling ingress
that writes `DreamLane`; this feature adds a kind value, not a new RDF class.

Lane signals measure absence of cycle creation, not successful completion or
available capacity. Shantytown retains its capacity gates and rotates past stale
queued cycles; this projection never closes a claimed item or starts a crew pane.

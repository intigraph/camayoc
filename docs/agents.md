# Using it with agents

## The Claude Code plugin

Camayoc ships as a **Claude Code plugin**. In Claude Code:

```text
/plugin marketplace add intigraph/camayoc
/plugin install camayoc@camayoc
```

That gets you, immediately:

- **The skill** — auto-triggers when memory matters; teaches the four moves.
- **A SessionStart hook** — every session opens knowing the truth about its
  memory: `ACTIVE (N facts, gate loaded)`, `reachable but gate not proven`,
  or `unreachable` — and unreachable is reported as *"could not look"*,
  never as *"nothing exists"*.
- **`/camayoc:bootstrap`** — from nothing to governed memory, idempotently:
  if no quipu is reachable it **installs one** (writes `.bobbin/config.toml`
  with `validate_on_write = true`, downloads the latest
  [quipu release](https://github.com/scbrown/quipu/releases) binary —
  sha256-checked — or cargo-installs it, starts it against
  `.quipu/store.db`, gitignores `.quipu/`), loads the core ontology +
  SHACL shapes, registers and labels the quarantine planes (both or
  neither — see "What runs today" above), and then **proves the gate**: it sends a
  deliberately untagged probe and requires the store to refuse it — one
  probe per refusal arm, each omitting exactly one required property, so a
  passing probe proves its own shape and nothing else. A store that accepts
  a probe is reported, loudly, not ingested into.
- **The ontology and shapes themselves** (`ontology/core.ttl`,
  `shapes/core.shapes.ttl`) — work items, decisions, outcomes, and the
  mandatory `sourceKind` provenance tag.

There is no prerequisite beyond Claude Code itself: the bootstrap brings its
own server, config, and gate — and tells you honestly when it can't. (For
setups that skip the plugin, `scripts/bootstrap.sh --with-claude-hooks` also
merges the session status hook into `.claude/settings.json`.)

After the core succeeds, **bootstrap offers the rest of the stack** — your
choice, per component, never assumed:

- **[bobbin](https://github.com/scbrown/bobbin)** — semantic code search +
  context bundles; installed from crates.io, project indexed, `bobbin serve`
  added to `.mcp.json`.
- **[Yupana](https://github.com/scbrown/yupana)** — defs/refs, call graph,
  blast radius, and change-time policy checks; installed from git
  (pre-release), `yupana serve` added to `.mcp.json` (`hank` remains a
  compatibility alias).
- **[beads](https://github.com/steveyegge/beads)** — the agent-first
  work-item tracker (`bd`). Dual role: shantytown's first-class tracker
  backend, and a deterministic observed-tier **ingress path** — a bead is a
  `WorkItem` record camayoc can govern into the graph.
- **[shantytown](https://github.com/scbrown/shantytown)** — the crew
  harness; pip-installed, then `st init` is *left to you* — it asks its five
  questions itself and shows every path before writing.

…and offers to **seed the graph's anchors** from a codebase + docs (this
project, a path, or a git URL): a deterministic, SHACL-gated walk mints the
modules, symbols, documents and sections that decisions anchor to — because
"what did we decide about X" needs X to exist before it can be answered.

## How you use it: the skill is the interface

Camayoc's primary consumer is an agent in a session, so camayoc **ships a
skill** ([skills/camayoc/SKILL.md](https://github.com/intigraph/camayoc/blob/main/skills/camayoc/SKILL.md)) that teaches any
agent the four moves: **bootstrap** a bare store (load ontology + shapes,
*prove* the SHACL gate is live), **query first** (ask the competency
questions before re-deciding anything), **record at the moment** (decisions
as episodes when they happen, not in a wrap-up), and **tag honestly**
(`observed` / `declared` / `inferred` — never up-tagged). The skill guides;
the shapes enforce — delete the skill and the store is exactly as safe, just
harder to use well. No harness is required: any agent that can speak HTTP to
a quipu can follow it. [docs/design/skill.md](design/skill.md)

**First domain: agentic coding.** The crew ontology + the task-lifecycle
vertical slice, recorded by skill-guided agents; harness record parsers
(shantytown, git) are optional `observed`-tier enrichment, never a
dependency.
[docs/design/task-lifecycle-slice.md](design/task-lifecycle-slice.md)

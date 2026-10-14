---
description: Bootstrap governed memory from nothing — install/start quipu if needed, write the gate config, load the camayoc ontology + shapes, prove the SHACL gate, then offer the optional stack (bobbin, hank, shantytown).
---

## Step 1 — core: quipu + ontology + gate

Run the camayoc bootstrap script and report its output faithfully:

```!
${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap.sh
```

For a fresh loopback server that it starts itself, bootstrap creates a private
random credential at `~/.config/quipu/token` (mode 0400) and configures the
matching server to require it. Its new `.bobbin/config.toml` is mode 0600
and ignored by git because it contains that private credential.
Existing credentials follow `QUIPU_AUTH_TOKEN` → `QUIPU_AUTH_TOKEN_FILE` →
`~/.config/quipu/token`; none is replaced. An already running or remote server
requires an accepted credential from its administrator. A failed health check
alone does not authorize creating one: existing server data/configuration and
an occupied local port prevent fresh provisioning. Bearers are kept out of
bootstrap output and curl arguments.

The script is idempotent and does, in order, only what is missing: reach the
quipu at $QUIPU_SERVER (default localhost:3030) — or install one (write
.bobbin/config.toml with validate_on_write = true, download the latest quipu
release binary — sha256-checked — or cargo-install it, start it against
.quipu/store.db, gitignore .quipu/); load ontology/core.ttl and
shapes/core.shapes.ttl; then PROVE the gate by sending an untagged probe the
store must refuse.

If it reports the gate NOT PROVEN or nothing could be installed, relay that
verbatim and STOP — do not ingest into an ungated store, do not offer step 2,
and do not treat "could not reach quipu" as "no knowledge exists".

## Step 2 — offer the optional stack (only after step 1 succeeds)

Ask the user (use the question tool, multi-select) which optional components
to set up, briefly describing each:

- **bobbin** — semantic code search + task-aware context bundles, exposed to
  agents over MCP (`cargo install bobbin-ai`, indexes this project).
- **hank** — structural code facts: defs/refs, call graph, blast radius,
  over MCP (pre-release; installs from git, can take minutes).
- **beads (bd)** — the agent-first work-item tracker (graph-based,
  git-backed, JSON out). Two roles here: shantytown's first-class tracker
  backend, and a deterministic observed-tier **ingress path** — bead
  records are WorkItem records camayoc can govern into the graph.
- **shantytown (st)** — a crew harness for running multiple coding agents
  (Python + tmux; its own `st init` stays interactive and is left to the
  user). Pairs naturally with beads; works without it (files tracker).

For each selected component run, one at a time, reporting output faithfully:

```text
${CLAUDE_PLUGIN_ROOT}/scripts/setup_component.sh <bobbin|hank|beads|st>
```

If the user picks both beads and st, run beads first so st finds bd and
selects it as its tracker.

Cargo installs take minutes — say so up front rather than looking hung. A
component that fails to install is reported and skipped, never retried in a
loop, and never blocks the others. After st installs, remind the user to run
`st init` themselves — it asks five questions and shows every path before
writing; the script deliberately does not answer them on the user's behalf.

## Step 3 — seed the graph's anchors (offer after step 1 succeeds)

Ask the user whether to seed the graph from a codebase + docs — the default
being **this project**, or a path, or a git URL (shallow-cloned into
`.quipu/seed/`). Explain why in one line: the competency questions hang off
entities — "what did we decide about X" needs X to exist — and a
deterministic walk of code and docs mints the modules, symbols, documents
and sections that decisions anchor to.

```text
${CLAUDE_PLUGIN_ROOT}/scripts/seed_knowledge.sh [path-or-git-url]
```

The walk is SHACL-gated (code-entities shapes load first, so a malformed
seed is refused, not absorbed) and observed-tier by construction. Report
the ingested count. Re-running after big refactors is safe — the ingest is
idempotent at the fact level.

## After

Governed memory is ready: query before re-deciding, record decisions at the
moment they happen, tag sourceKind honestly (observed | declared |
inferred). If MCP servers were added to .mcp.json, mention that Claude Code
picks them up on next session start (or /mcp reconnect).

For installs that skipped the plugin, `bootstrap.sh --with-claude-hooks`
additionally merges the SessionStart status hook into the project's
.claude/settings.json (non-clobbering, idempotent).

#!/usr/bin/env bash
# camayoc component setup — optional siblings, one per invocation:
#   setup_component.sh bobbin   # code search/context (MCP: bobbin serve)
#   setup_component.sh hank     # code structure engine (MCP: hank serve)
#   setup_component.sh st       # shantytown crew harness (CLI: st)
#
# Idempotent; honest about what it did, skipped, and what remains manual.
# Cargo builds are slow — this script says so instead of appearing hung.
set -u

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
say() { printf '%s\n' "$*"; }

# Merge one stdio MCP server into the project's .mcp.json, non-clobbering.
merge_mcp() { # $1=name $2=command $3...=args
  python3 - "$PROJECT_DIR/.mcp.json" "$@" <<'PY'
import json, sys, os
path, name, command = sys.argv[1], sys.argv[2], sys.argv[3]
args = sys.argv[4:]
cfg = {}
if os.path.exists(path):
    with open(path) as f: cfg = json.load(f)
servers = cfg.setdefault("mcpServers", {})
if name in servers:
    print(f"mcp: '{name}' already in .mcp.json — leaving it alone")
else:
    servers[name] = {"command": command, "args": args}
    with open(path, "w") as f: json.dump(cfg, f, indent=2); f.write("\n")
    print(f"mcp: added '{name}' ({command} {' '.join(args)}) to .mcp.json")
PY
}

setup_bobbin() {
  if ! command -v bobbin >/dev/null 2>&1; then
    command -v cargo >/dev/null 2>&1 || { say "bobbin: needs cargo (install Rust first) — skipped"; return 1; }
    say "bobbin: installing from crates.io (cargo install bobbin-ai — takes several minutes)..."
    # The crate is bobbin-ai; plain `bobbin` on crates.io is someone else's.
    cargo install bobbin-ai --locked --quiet || { say "bobbin: cargo install FAILED"; return 1; }
  fi
  say "bobbin: binary ready ($(command -v bobbin))"
  if [ ! -e "$PROJECT_DIR/.bobbin" ] || ! ls "$PROJECT_DIR/.bobbin"/bobbin* >/dev/null 2>&1; then
    ( cd "$PROJECT_DIR" && bobbin init ) || { say "bobbin: init failed"; return 1; }
  fi
  say "bobbin: indexing the project (first index can take a while on large repos)..."
  ( cd "$PROJECT_DIR" && bobbin index ) || say "bobbin: index reported errors — search may be partial; re-run 'bobbin index' later"
  merge_mcp bobbin bobbin serve
  say "bobbin: done — semantic search + context bundles available to agents via MCP."
}

setup_hank() {
  if ! command -v hank >/dev/null 2>&1; then
    command -v cargo >/dev/null 2>&1 || { say "hank: needs cargo (install Rust first) — skipped"; return 1; }
    say "hank: installing from git (cargo install --git ... — takes several minutes)..."
    cargo install --git https://github.com/intigraph/yupana --locked --features "mcp langs-extra" --quiet \
      || { say "hank: cargo install FAILED (hank is pre-release; building from a clone may be needed)"; return 1; }
  fi
  say "hank: binary ready ($(command -v hank))"
  merge_mcp hank hank serve
  say "hank: done — structural code facts (defs/refs/call graph) available via MCP."
  say "hank: optional — wire 'hank hook post-edit' into a PostToolUse hook for edit-reactive freshness."
}

setup_st() {
  if command -v st >/dev/null 2>&1; then
    say "st: already installed ($(command -v st))"
  else
    command -v python3 >/dev/null 2>&1 || { say "st: needs python3 — skipped"; return 1; }
    say "st: installing shantytown (pip install from git)..."
    python3 -m pip install --quiet "git+https://github.com/scbrown/shantytown" \
      || { say "st: pip install FAILED"; return 1; }
  fi
  command -v tmux >/dev/null 2>&1 || say "st: WARNING — tmux not found, and shantytown runs its crew in tmux panes."
  st doctor || true
  say "st: installed. NOT running 'st init' for you — it asks five questions"
  say "st: (administrator, workers, workdir, startup mode, hibernate) and shows"
  say "st: every path before writing. Run it yourself in the project:  st init"
}

setup_beads() {
  if command -v bd >/dev/null 2>&1; then
    say "beads: already installed ($(command -v bd))"
  else
    say "beads: installing bd (trying npm, brew, go — in that order)..."
    if command -v npm >/dev/null 2>&1 && npm install -g @beads/bd --silent >/dev/null 2>&1; then
      say "beads: installed via npm"
    elif command -v brew >/dev/null 2>&1 && brew install beads >/dev/null 2>&1; then
      say "beads: installed via brew"
    elif command -v go >/dev/null 2>&1 && go install github.com/steveyegge/beads/cmd/bd@latest >/dev/null 2>&1; then
      say "beads: installed via go (ensure ~/go/bin is on PATH)"
    else
      say "beads: could not install — need one of npm, brew, or go."
      say "  https://github.com/steveyegge/beads"
      return 1
    fi
  fi
  command -v bd >/dev/null 2>&1 || { say "beads: bd not on PATH after install — check your PATH"; return 1; }
  if [ ! -d "$PROJECT_DIR/.beads" ]; then
    ( cd "$PROJECT_DIR" && bd init ) || { say "beads: bd init failed"; return 1; }
    say "beads: initialized in project (.beads/)"
  else
    say "beads: project already initialized"
  fi
  say "beads: done — work items live in bd, agent-first (JSON out, dependency graph)."
  say "beads: shantytown uses bd as its first-class tracker automatically when present,"
  say "beads: and bead records are a deterministic (observed-tier) ingress source for"
  say "beads: camayoc work-item knowledge."
}

case "${1:-}" in
  bobbin) setup_bobbin ;;
  hank)   setup_hank ;;
  st)     setup_st ;;
  beads)  setup_beads ;;
  *) say "usage: setup_component.sh bobbin|hank|st|beads"; exit 64 ;;
esac

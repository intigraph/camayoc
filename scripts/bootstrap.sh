#!/usr/bin/env bash
# camayoc bootstrap — from nothing to governed memory, then PROVE the gate.
#
# Phases (each idempotent, each skipped when already satisfied):
#   1. reach a quipu, or install one: config → binary → start
#   2. load ontology + shapes
#   3. prove the gate: an untagged probe MUST be refused
#   4. (--with-claude-hooks) merge the SessionStart hook into project
#      .claude/settings.json for installs that skipped the plugin
#
# Exit codes: 1 nothing could be installed; 2 the store ACCEPTED what it must
# refuse; 3 no verdict could be reached at all. A gate that is off is a report,
# never a workaround — and a gate we could not question is neither a pass nor a
# failure, so it gets its own code rather than being rounded to either.
set -u

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
SERVER="${QUIPU_SERVER:-http://localhost:3030}"; SERVER=${SERVER%/}
QUIPU_DIR="$PROJECT_DIR/.quipu"
AUTH=()

say() { printf '%s\n' "$*"; }
reachable() { curl -sf -m 2 "$SERVER/health" >/dev/null 2>&1; }

# ---------------------------------------------------------------- phase 1
ensure_config() {
  local cfg="$PROJECT_DIR/.bobbin/config.toml"
  local port; port=$(printf '%s' "$SERVER" | sed -n 's/.*:\([0-9]*\)$/\1/p'); port=${port:-3030}
  if [ ! -f "$cfg" ]; then
    python3 "$PLUGIN_ROOT/scripts/bootstrap_credentials.py" provision "$PROJECT_DIR" "$SERVER" || return 1
    say "config: wrote .bobbin/config.toml (validate_on_write = true)"
  elif ! grep -q '\[quipu\.shacl\]' "$cfg"; then
    say "config: existing config lacks [quipu.shacl]; preserving it. Set validate_on_write = true before continuing."
    return 1
  elif grep -A3 '\[quipu\.shacl\]' "$cfg" | grep -q 'validate_on_write[[:space:]]*=[[:space:]]*false'; then
    say "config: WARNING — your .bobbin/config.toml sets validate_on_write = false."
    say "  Not editing inside your existing section. Set it true or the gate proof below will fail."
  else
    say "config: existing .bobbin/config.toml has a [quipu.shacl] section — leaving it alone"
  fi
}

find_or_install_binary() {
  if command -v quipu-server >/dev/null 2>&1; then command -v quipu-server; return 0; fi
  if [ -x "$QUIPU_DIR/bin/quipu-server" ]; then echo "$QUIPU_DIR/bin/quipu-server"; return 0; fi

  local os arch
  os=$(uname -s); arch=$(uname -m)
  if [ "$os" = "Linux" ] && [ "$arch" = "x86_64" ]; then
    say "binary: downloading latest quipu release (linux x86_64)..." >&2
    local api tag asset url
    api=$(curl -sf -m 15 https://api.github.com/repos/scbrown/quipu/releases/latest) || { say "binary: release API unreachable" >&2; return 1; }
    tag=$(printf '%s' "$api" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
    asset="quipu-${tag}-x86_64-unknown-linux-gnu.tar.gz"
    url="https://github.com/scbrown/quipu/releases/download/${tag}/${asset}"
    mkdir -p "$QUIPU_DIR/bin"
    curl -sfL -m 300 -o "$QUIPU_DIR/$asset" "$url" || { say "binary: download failed: $url" >&2; return 1; }
    curl -sfL -m 30 -o "$QUIPU_DIR/$asset.sha256" "$url.sha256" 2>/dev/null && \
      ( cd "$QUIPU_DIR" && sha256sum -c "$asset.sha256" >/dev/null 2>&1 ) \
      && say "binary: sha256 verified" >&2 \
      || say "binary: sha256 not verified (checksum missing or mismatch tool) — proceeding on TLS alone" >&2
    tar -xzf "$QUIPU_DIR/$asset" -C "$QUIPU_DIR" && \
      cp "$QUIPU_DIR"/quipu-${tag}-*/quipu-server "$QUIPU_DIR/bin/" && \
      cp "$QUIPU_DIR"/quipu-${tag}-*/quipu "$QUIPU_DIR/bin/" 2>/dev/null
    rm -rf "$QUIPU_DIR/$asset" "$QUIPU_DIR/$asset.sha256" "$QUIPU_DIR"/quipu-${tag}-*/
    [ -x "$QUIPU_DIR/bin/quipu-server" ] && { echo "$QUIPU_DIR/bin/quipu-server"; return 0; }
    say "binary: extraction failed" >&2; return 1
  fi
  if command -v cargo >/dev/null 2>&1; then
    say "binary: no prebuilt release for $os/$arch — building via cargo (this takes a while)..." >&2
    # The crate is quipu-ai; plain `quipu` on crates.io is someone else's.
    cargo install quipu-ai --locked --features full --quiet >&2 && command -v quipu-server && return 0
  fi
  say "binary: could not install quipu-server for $os/$arch." >&2
  say "  Install Rust, then: cargo install quipu-ai --locked --features full" >&2
  return 1
}

ensure_server() {
  reachable && { say "server: already running at $SERVER"; return 0; }
  ensure_config || return 1
  local bin
  bin=$(find_or_install_binary) || return 1
  say "server: starting $bin (db: .quipu/store.db)"
  mkdir -p "$QUIPU_DIR"
  # Starting a background job inside a command/subshell still makes bash wait
  # for that job before the subshell exits. Spawn a new session directly so
  # bootstrap can continue while the server owns its own lifetime.
  python3 - "$bin" "$PROJECT_DIR" "$QUIPU_DIR" <<'PY'
import os
import subprocess
import sys

binary, project_dir, quipu_dir = sys.argv[1:]
log_path = os.path.join(quipu_dir, "server.log")
with open(log_path, "ab", buffering=0) as log:
    process = subprocess.Popen(
        [binary, "--db", os.path.join(quipu_dir, "store.db")],
        cwd=project_dir,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
with open(os.path.join(quipu_dir, "server.pid"), "w", encoding="utf-8") as pid_file:
    pid_file.write(f"{process.pid}\n")
PY
  local i=0
  while [ $i -lt 30 ]; do reachable && break; sleep 0.5; i=$((i+1)); done
  reachable || { say "server: did not come up — see .quipu/server.log"; return 1; }
  say "server: up at $SERVER (pid $(cat "$QUIPU_DIR/server.pid"))"
  if [ -f "$PROJECT_DIR/.gitignore" ] && ! grep -q '^\.quipu/' "$PROJECT_DIR/.gitignore"; then
    printf '\n# camayoc local store (server state, never committed)\n.quipu/\n' >> "$PROJECT_DIR/.gitignore"
    say "gitignore: added .quipu/"
  fi
}

# ---------------------------------------------------------------- phase 4
install_claude_hooks() {
  local settings="$PROJECT_DIR/.claude/settings.json"
  mkdir -p "$PROJECT_DIR/.claude"
  python3 - "$settings" "$PLUGIN_ROOT/scripts/session_start.sh" <<'PY'
import json, sys, os
path, script = sys.argv[1], sys.argv[2]
cfg = {}
if os.path.exists(path):
    with open(path) as f: cfg = json.load(f)
hooks = cfg.setdefault("hooks", {}).setdefault("SessionStart", [])
cmd = {"type": "command", "command": script}
if not any(h.get("command") == script for grp in hooks for h in grp.get("hooks", [])):
    hooks.append({"hooks": [cmd]})
    with open(path, "w") as f: json.dump(cfg, f, indent=2); f.write("\n")
    print(f"hooks: merged camayoc SessionStart into {path}")
else:
    print("hooks: already installed")
PY
}

# ================================================================= main
say "camayoc bootstrap → $SERVER"
ensure_server || {
  say "REFUSING TO PROCEED: no quipu and none could be installed."
  say "This is 'could not look', not 'nothing exists'."
  exit 1
}

# Every writer uses the canonical resolver; a reachable server does not grant
# permission to invent a credential for it. Fresh local provisioning above is
# the only mint path, and configures the matching server to require the token.
AUTH_CONFIG=$(python3 "$PLUGIN_ROOT/scripts/bootstrap_credentials.py" curl-config) || exit 1
trap 'rm -f "$AUTH_CONFIG"' EXIT
AUTH=(-K "$AUTH_CONFIG")

ONTO="$PLUGIN_ROOT/ontology/core.ttl"
SHAPES="$PLUGIN_ROOT/shapes/core.shapes.ttl"
[ -f "$ONTO" ] && [ -f "$SHAPES" ] || { say "missing $ONTO / $SHAPES"; exit 1; }

# actor AND source: ingress rule 1 narrows to knot writes carrying both, and
# this load named only its actor (camayoc-99t).
# RE-RUNNABLE ON PURPOSE. Quipu's vocabulary gate is inactive until shapes are
# loaded, so the first run's load succeeds; this script then loads shapes, and
# every later load of the same file is refused (HTTP 400, "unknown rdf:type
# IRIs: rdf:Property, rdfs:Class"). caboodle's resume re-runs this script as its
# functional verification, so that refusal failed every resumed install. A
# refusal says nothing about whether the ontology is already there — ask the
# store. The body is captured, not discarded: `curl -f` printed an empty
# "FAILED:" here, which cannot tell a 401 from a 400 from a timeout.
KNOT_BODY=$(mktemp)
KNOT_CODE=$(python3 -c 'import json,sys; print(json.dumps({"turtle": open(sys.argv[1]).read(), "actor": "camayoc-bootstrap", "source": sys.argv[2]}))' "$ONTO" "ontology/core.ttl" \
    | curl -s -m 10 -o "$KNOT_BODY" -w '%{http_code}' -X POST "$SERVER/knot" -H 'Content-Type: application/json' ${AUTH[@]+"${AUTH[@]}"} -d @- 2>/dev/null) || true
KNOT_MSG=$(head -c 400 "$KNOT_BODY"); rm -f "$KNOT_BODY"
case "$KNOT_CODE" in
  2??) say "ontology: loaded (core.ttl)" ;;
  *)
    if PRESENT=$(python3 "$PLUGIN_ROOT/scripts/ontology_present.py" "$ONTO" "$SERVER"); then
      say "ontology: already present ($PRESENT declared terms typed in the store); re-post refused with HTTP $KNOT_CODE, not needed"
    else
      say "ontology load FAILED: HTTP ${KNOT_CODE:-none} ${KNOT_MSG}"
      say "  store presence check: $PRESENT"
      exit 1
    fi ;;
esac

# Quarantine planes (camayoc-s0h). Registered AND labelled, or neither: routing
# without labels yields separate graphs every query still reads at equal trust,
# which is the appearance of quarantine without its substance. A quipu without
# the /graph routes fails here rather than silently writing everything to ROOT.
R=$(python3 "$PLUGIN_ROOT/scripts/planes.py" ensure 2>&1) \
  && say "planes: registered and labelled" \
  || { say "plane setup FAILED: $R"; say "Refusing to proceed: unrouted writes would land in ROOT alongside observed facts."; exit 1; }

# The tracker profile remains Seeds-owned and checksum-pinned, never a copy in
# Camayoc. Resolve/load it before retiring the old core WorkItem target.
R=$(python3 "$PLUGIN_ROOT/scripts/seed_profile.py" "$SERVER" 2>&1) \
  && say "shapes: $R" || { say "Seeds profile load FAILED: $R"; exit 1; }

R=$(python3 -c 'import json,sys; print(json.dumps({"action":"load","name":"camayoc-core","turtle":open(sys.argv[1]).read()}))' "$SHAPES" \
    | curl -sf -m 10 -X POST "$SERVER/shapes" -H 'Content-Type: application/json' ${AUTH[@]+"${AUTH[@]}"} -d @- 2>&1) \
  && say "shapes: loaded (camayoc-core)" || { say "shapes load FAILED: $R"; exit 1; }

for query in "$PLUGIN_ROOT"/queries/*.json; do
  [ -e "$query" ] || continue
  R=$(curl -sf -m 10 -X POST "$SERVER/queries" -H 'Content-Type: application/json' \
      ${AUTH[@]+"${AUTH[@]}"} --data-binary @"$query" 2>&1) \
    && say "query: loaded ($(basename "$query" .json))" \
    || { say "query load FAILED ($query): $R"; exit 1; }
done

# The gate proof lives in its own script so it can be run and TESTED without
# an install (camayoc-045). tests/test_gate_probe.py drives run_gate_probes
# against stub servers that refuse, accept, echo, fault and vanish.
. "$PLUGIN_ROOT/scripts/gate_probe.sh"
run_gate_probes

[ "${1:-}" = "--with-claude-hooks" ] && install_claude_hooks

say "camayoc: governed memory ready. Query first; record at the moment; tag honestly."

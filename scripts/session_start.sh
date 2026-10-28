#!/usr/bin/env bash
# camayoc SessionStart hook — probe governed memory, report honestly.
# Emits hookSpecificOutput.additionalContext. Never blocks a session:
# every path exits 0 with a one-line truthful status. "Could not reach
# quipu" is NOT "no knowledge" — the two answers are kept distinct.
set -u

# Ordered local replay is bounded and cannot block session startup on an error.
# Missing CLI is allowed: the plugin can be installed before the memory tools.
if command -v cy >/dev/null 2>&1; then
  cy flush --hook >/dev/null 2>&1 || :
fi

# Prints "<url> <source>". The source is part of the answer: a server the
# operator named is a claim about THEIR store, the default port is not. Once,
# an orphaned test store sat on :3030 for ~5 days and this hook
# reported it as ACTIVE governed memory to every unconfigured session
# (aegis-p1twft). quipu's /stats carries no store identity, so the only
# honest thing to say about an unconfigured responder is that it is
# unverified.
resolve_server() {
  if [ -n "${QUIPU_SERVER:-}" ]; then echo "$QUIPU_SERVER QUIPU_SERVER"; return; fi
  for f in "${CLAUDE_PROJECT_DIR:-.}/env.json" "./env.json"; do
    if [ -f "$f" ]; then
      s=$(sed -n 's/.*"quipu_server"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$f" | head -1)
      if [ -n "$s" ]; then echo "$s env.json"; return; fi
    fi
  done
  # bootstrap.sh writes [quipu.server] bind here; the skill documents it as a
  # resolution step, and until aegis-p1twft this hook skipped it.
  for f in "${CLAUDE_PROJECT_DIR:-.}/.bobbin/config.toml" "./.bobbin/config.toml"; do
    if [ -f "$f" ]; then
      b=$(awk '/^[[:space:]]*\[/{sec=$0} sec~/^[[:space:]]*\[quipu\.server\]/ && /^[[:space:]]*bind[[:space:]]*=/{
             sub(/^[^=]*=[[:space:]]*"/,""); sub(/".*/,""); print; exit}' "$f")
      if [ -n "$b" ]; then echo "http://$b .bobbin/config.toml"; return; fi
    fi
  done
  # Overridable only so tests need not own port 3030; it stays "default".
  echo "${CAMAYOC_DEFAULT_QUIPU:-http://localhost:3030} default"
}

emit() {
  # $1 = context string (single line). JSON-escape the minimum.
  esc=$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')
  printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' "$esc"
}

read -r SERVER SOURCE <<EOF_RESOLVE
$(resolve_server)
EOF_RESOLVE
SERVER=${SERVER%/}
if [ "$SOURCE" = default ]; then
  WHERE="the DEFAULT $SERVER (nothing configured: no QUIPU_SERVER, no env.json quipu_server, no .bobbin/config.toml [quipu.server] bind)"
else
  WHERE="$SERVER (from $SOURCE)"
fi

STATS=$(curl -sf -m 2 "$SERVER/stats" 2>/dev/null) || {
  emit "camayoc: could not reach quipu at $WHERE — governed memory UNAVAILABLE this session (this is 'could not look', not 'nothing exists'). To enable: start quipu-server (cargo install quipu-ai --locked --features full; quipu-server --db .quipu/store.db) then run /camayoc:bootstrap."
  exit 0
}

FACTS=$(printf '%s' "$STATS" | sed -n 's/.*"facts"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p')
SHAPES=$(curl -sf -m 2 -X POST "$SERVER/shapes" -H 'Content-Type: application/json' \
  ${QUIPU_AUTH_TOKEN:+-H "Authorization: Bearer $QUIPU_AUTH_TOKEN"} \
  -d '{"action":"list"}' 2>/dev/null)

if [ "$SOURCE" = default ]; then
  # Never certify an unconfigured responder: anything can hold a default port.
  emit "camayoc: a quipu answered at $WHERE (${FACTS:-?} facts) but it is UNVERIFIED — governed memory is NOT established this session. Any process can hold a default port (a stale test store answered here for days, aegis-p1twft). If this is your store, set QUIPU_SERVER or run /camayoc:bootstrap; otherwise treat its answers as unknown provenance."
elif printf '%s' "${SHAPES:-}" | grep -q 'camayoc-core'; then
  emit "camayoc: governed memory ACTIVE at $WHERE (${FACTS:-?} facts, camayoc shapes loaded). Query before re-deciding (competency questions in the camayoc skill); record decisions as tagged episodes AT THE MOMENT they happen; sourceKind observed|declared|inferred, tagged honestly."
else
  emit "camayoc: quipu reachable at $WHERE (${FACTS:-?} facts) but camayoc shapes are NOT loaded — the gate is not proven. Run /camayoc:bootstrap before ingesting; reads are fine."
fi
exit 0

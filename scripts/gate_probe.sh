#!/usr/bin/env bash
# The gate proof — extracted from bootstrap.sh so it can be run, and TESTED,
# on its own (camayoc-045).
#
# The gate is the claim that the store REFUSES what the ontology says is
# meaningless: an untagged Decision, a Verification with no falsifier, an
# ExecutionPath missing its repository source, a Blocker that does not say
# whether it is stated or built. Each arm writes a probe that violates exactly
# one shape, and asserts the store said no.
#
# Three outcomes, deliberately kept apart, because they call for different
# actions and the old grep collapsed them:
#
#   REFUSED        the gate works. Exit 0.
#   ACCEPTED       the gate is OFF. Exit 2. Fix the config; do not route around.
#   COULD NOT LOOK no answer came back. Exit 3. This is not evidence either way,
#                  and reporting it as PROVEN is how a gate quietly stops
#                  guarding. ("could not look" is not "nothing exists" — the
#                  same distinction the installer makes about a missing quipu.)
#
# ## What was wrong before
#
# The old arms grepped the raw curl output for
# `conforms.*false|violation|refus|<term>` and called any hit PROVEN. Two ways
# that lies:
#
#   (a) any transport-layer prose containing "refus" was taken as a refusal.
#       camayoc-045 blamed curl's stderr for this; that half is wrong, because
#       `curl -s` prints no error message at all (verified: exit 7, zero bytes
#       on stderr), so `2>&1` captured nothing. The reachable path is the
#       response BODY: a proxy or gateway between us and the store answers 502
#       with a page reading "connect() failed (111: Connection refused) while
#       connecting to upstream". That goes to stdout, it matches, and the store
#       was never reached at all. Verified against the old arm — it printed
#       PROVEN and exited 0.
#
#       The down-server case was mis-reported too, just less spectacularly: the
#       old arm called it "the store ACCEPTED" and told the operator to fix
#       validate_on_write, which is not the problem.
#   (b) each arm's alternation included the term the probe was named after, and
#       the probe body contains that term — `camayoc-falsifier-probe`,
#       "deliberately lacks its falsifier". A store that ACCEPTED the write and
#       echoed the node back matched, and reported the gate as PROVEN.
#
# Both are the same mistake: reading a verdict out of unstructured text that the
# caller partly wrote. So the verdict is now read from designated JSON fields
# and the HTTP status, curl's stderr is discarded rather than parsed, and the
# term the arm is named after is a diagnostic note, never the evidence.
set -u

SERVER="${QUIPU_SERVER:-http://localhost:3030}"; SERVER=${SERVER%/}
GATE_AUTH=()
if declare -p AUTH >/dev/null 2>&1; then
  GATE_AUTH=("${AUTH[@]}")
else
  [ -n "${QUIPU_AUTH_TOKEN:-}" ] && GATE_AUTH=(-H "Authorization: Bearer $QUIPU_AUTH_TOKEN")
fi

# bootstrap.sh defines `say`; standing alone we need our own.
if ! declare -F say >/dev/null 2>&1; then
  say() { printf '%s\n' "$*"; }
fi

#: Set by probe_write to the human-readable basis for its verdict.
PROBE_REASON=""

# Read a verdict out of a response body.
#
# STRUCTURED, not textual: only fields that carry a verdict are consulted.
# Content the caller sent, echoed back anywhere in the document, is not one of
# them — which is what makes defect (b) unrepresentable rather than merely
# fixed.
#
# stdout: the violation text, when refused.
# exit:   0 refused | 1 accepted | 2 no verdict could be read
read_verdict() {
  python3 -c '
import json, sys

try:
    doc = json.loads(sys.argv[1])
except Exception:
    sys.exit(2)
if not isinstance(doc, dict):
    sys.exit(2)

conforms = doc.get("conforms")
if isinstance(conforms, str):
    conforms = {"true": True, "false": False}.get(conforms.lower())

# The SHACL failure body from quipu is
#   {"conforms": false, "violations": N, "warnings": N, "issues": [...], "hint": "..."}
# returned as HTTP 200 (src/mcp/mod.rs:471-477). Note `violations` is a COUNT,
# not a list — reading it as one is why `issues` is checked first here. The
# other key names are kept so a differently-shaped SHACL report still parses.
reports = []
for key in ("issues", "violations", "results", "validation_results", "errors"):
    value = doc.get(key)
    if isinstance(value, list):
        reports.extend(item for item in value if item)
    elif isinstance(value, str) and value:
        reports.append(value)

# A nonzero violation COUNT is a refusal even when the detail list is absent.
counted = doc.get("violations")
counted = counted if isinstance(counted, int) and not isinstance(counted, bool) else 0


def text(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("message", "resultMessage", "detail", "reason", "sourceShape", "path"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    return json.dumps(item)


if conforms is False or reports or counted:
    summary = "; ".join(text(r) for r in reports[:4])
    if not summary:
        hint = doc.get("hint")
        summary = hint if isinstance(hint, str) and hint else (
            f"{counted} violation(s)" if counted else "conforms: false"
        )
    print(summary)
    sys.exit(0)
if conforms is True or doc.get("ok") is True or any(k in doc for k in ("id", "episode", "nodes")):
    sys.exit(1)
sys.exit(2)
' "$1"
}

# Write one probe and classify the answer.
# exit: 0 refused | 1 accepted | 2 could not look
probe_write() {
  local payload=$1 out rc body code reason vrc

  # 2>/dev/null, not 2>&1: curl's own failure prose is not the store's answer,
  # and mixing the two is defect (a). Transport failure shows up as a non-zero
  # exit status, which cannot be confused with anything the store said.
  out=$(curl -s -m 10 -w '\n%{http_code}' -X POST "$SERVER/episode" \
        -H 'Content-Type: application/json' ${GATE_AUTH[@]+"${GATE_AUTH[@]}"} -d "$payload" 2>/dev/null)
  rc=$?
  if [ $rc -ne 0 ]; then
    PROBE_REASON="curl exit $rc — the request did not complete, so no answer was received"
    return 2
  fi

  code=${out##*$'\n'}
  body=${out%$'\n'*}
  case $code in
    [0-9][0-9][0-9]) ;;
    *) PROBE_REASON="no HTTP status came back"; return 2 ;;
  esac

  reason=$(read_verdict "$body"); vrc=$?
  case $vrc in
    0) PROBE_REASON="HTTP $code — $reason"; return 0 ;;
    1) PROBE_REASON="HTTP $code — the store reported the write succeeded"; return 1 ;;
  esac

  # No verdict in the body. The status line is then the only structured thing
  # we have, and it still separates a decline from a success from a fault.
  case $code in
    4??) PROBE_REASON="HTTP $code — the store declined the write"; return 0 ;;
    2??) PROBE_REASON="HTTP $code with no violation report — the store took it"; return 1 ;;
    *)   PROBE_REASON="HTTP $code — a server fault, which is not a verdict"; return 2 ;;
  esac
}

# One arm of the proof.
#   $1 what the probe violates, for the operator
#   $2 the term the store SHOULD name — a diagnostic note, never the evidence
#   $3 the probe body
#   $4 the remedy to print when the gate is off
gate_arm() {
  local label=$1 expect=$2 payload=$3 remedy=$4 rc
  probe_write "$payload"; rc=$?

  case $rc in
    0)
      say "gate: PROVEN — $label refused, as it must be."
      case $PROBE_REASON in
        *"$expect"*) ;;
        *) say "  note: refused, but the report did not name $expect — $PROBE_REASON" ;;
      esac
      return 0
      ;;
    1)
      say "gate: NOT PROVEN — the store ACCEPTED $label."
      say "  $PROBE_REASON"
      say "  $remedy"
      say "  Not retrying, not routing around. Fix the gate first."
      exit 2
      ;;
    *)
      say "gate: NOT PROVEN — could not reach a verdict on $label."
      say "  $PROBE_REASON"
      say "  This is 'could not look', not 'the gate is off' and not 'the gate works'."
      say "  Check that $SERVER is up and answering, then run this again."
      exit 3
      ;;
  esac
}

run_gate_probes() {
  gate_arm "an untagged Decision" "sourceKind" '{
  "name": "camayoc-gate-probe-untagged",
  "nodes": [{"name": "camayoc-gate-probe", "type": "Decision",
             "properties": {"chose": "nothing — this is a gate probe"}}]
}' "Enable [quipu.shacl] validate_on_write (bootstrap writes this into .bobbin/config.toml for servers it starts; a pre-existing server needs its own config fixed and a restart)."

  # A verification that is otherwise well-formed but cannot say what would have
  # disproved it must also be refused. This is deliberately a separate arm from
  # the untagged Decision above: otherwise a sourceKind failure could make a
  # missing-falsifier shape look enforced when it never ran.
  gate_arm "a Verification with no falsifier" "falsifier" '{
  "name": "camayoc-gate-probe-no-falsifier",
  "nodes": [{"name": "camayoc-falsifier-probe", "type": "Verification",
             "description": "deliberately lacks its falsifier",
             "properties": {"sourceKind": "observed"}}]
}' "Load the current camayoc core ontology and shapes, then enable write-time SHACL validation."

  # Execution-path provenance is also a gate, not a checklist someone remembers:
  # the executed artifact, repository source, and refresh mechanism are the facts
  # a later reader needs to compare. Omit only repositorySource so this is a
  # discriminating M3 arm rather than another sourceKind test.
  gate_arm "an ExecutionPath with no repository source" "repositorySource" '{
  "name": "camayoc-gate-probe-incomplete-execution-path",
  "nodes": [{"name": "camayoc-execution-path-probe", "type": "ExecutionPath",
             "description": "deliberately lacks its repository source",
             "properties": {"sourceKind": "observed",
                            "executesArtifact": "installed-probe",
                            "refreshedBy": "refresh-probe"}}]
}' "Load the current camayoc core ontology and shapes, then enable write-time SHACL validation."

  # A blocker that does not say whether it is merely stated or actually built is
  # the M4 category error. Omit only blockerEvidence; a sourceKind failure here
  # would prove the wrong gate.
  gate_arm "a Blocker with no evidence kind" "blockerEvidence" '{
  "name": "camayoc-gate-probe-unclassified-blocker",
  "nodes": [{"name": "camayoc-blocker-probe", "type": "Blocker",
             "description": "deliberately lacks its blocker evidence kind",
             "properties": {"sourceKind": "observed"}}]
}' "Load the current camayoc core ontology and shapes, then enable write-time SHACL validation."

  # A golden path that cannot cite the concrete success justifying it is an
  # opinion wearing a blessing. Omit only prunedFrom so this discriminates the
  # exemplar-provenance shape, not sourceKind.
  gate_arm "a GoldenPath with no exemplar trajectory" "prunedFrom" '{
  "name": "camayoc-gate-probe-unprovenance-path",
  "nodes": [{"name": "camayoc-golden-path-probe", "type": "GoldenPath",
             "description": "deliberately lacks the trajectory it was pruned from",
             "properties": {"sourceKind": "declared"}}]
}' "Load the current camayoc core ontology and shapes, then enable write-time SHACL validation."

  # An omission that cannot say who cut it is a silent edit of history. Omit
  # only omissionAuthority; the omitted step itself is supplied.
  gate_arm "a PathOmission with no authority" "omissionAuthority" '{
  "name": "camayoc-gate-probe-unauthorized-omission",
  "nodes": [{"name": "camayoc-path-omission-probe", "type": "PathOmission",
             "description": "deliberately lacks its omission authority",
             "properties": {"sourceKind": "declared",
                            "omittedStep": "step-probe"}}]
}' "Load the current camayoc core ontology and shapes, then enable write-time SHACL validation."

  # A promotion without its promoting principal is not a human act, and
  # nothing above verified happens without one. Omit only promotedBy; level,
  # target, and time are all supplied and valid.
  gate_arm "a PathPromotion with no promoting principal" "promotedBy" '{
  "name": "camayoc-gate-probe-unattributed-promotion",
  "nodes": [{"name": "camayoc-path-promotion-probe", "type": "PathPromotion",
             "description": "deliberately lacks its promoting principal",
             "properties": {"sourceKind": "declared",
                            "promotes": "path-probe",
                            "blessingLevel": "advisory",
                            "promotedAt": "2026-08-20T00:00:00Z"}}]
}' "Load the current camayoc core ontology and shapes, then enable write-time SHACL validation."
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  run_gate_probes
fi

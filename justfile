# camayoc task entry points

default:
    just --list

# Run all quality checks (markdown lint; grows an eval gate with the first slice)
check:
    npx --yes markdownlint-cli2 "**/*.md" "!node_modules"

test:
    python3 -m unittest discover -s tests -v

# Regenerate the agent-memory tool surfaces from shapes/agent-memory.shapes.ttl
# and tools/memory/*.tool.json (docs/design/agent-memory-entities.md §3).
# `just memory-tools --check` is the drift gate the test suite also runs.
memory-tools *args:
    @python3 scripts/memory_tools.py {{args}}

# Benchmark competency or ordered rubrics; ordinal defaults to a keyless dry run.
jev-bench *args:
    @python3 scripts/jev_bench.py {{args}}

# Emit the work-item provenance chain from git history (docs/design/ingress.md
# §3.3). Deterministic and abstaining: a commit naming no recognised work item
# emits nothing. Pass --project for every tracker prefix in play — an
# undeclared one is silently unlinked, and the summary is where you see it.
ingest-git *args:
    @python3 scripts/ingest_git_provenance.py {{args}}

# Emit the §D cost vocabulary from a harness's own session logs
# (docs/design/incident-corpus.md §5.3). Deterministic and abstaining: the
# unit of consumption is the API request, not the log entry, and a record that
# cannot be counted honestly is dropped and said out loud. --principal is
# required — the harness records a session, not who the crew calls the agent
# that ran it.
ingest-usage *args:
    @python3 scripts/ingest_session_usage.py {{args}}

# Extract entity mentions from markdown prose (docs/design/entity-mentions.md).
# Deterministic — gazetteer from the graph's own labels plus explicit
# --patterns, no model — and still quarantined: everything routes to the
# inferred plane, never ROOT. Refuses loudly when the store is unreachable or
# the plane is not provisioned; --dry-run prints the would-be triples.
extract-entities *args:
    @python3 scripts/extract_entities.py {{args}}

# The refused-write denominator (docs/design/incident-corpus.md §4.2, §5.1):
# quipu's durable write.refused stream joined to the accepted Verification
# population. Reports, never writes — a refusal RATE is a judgment computed at
# read time, not a fact true at write time. The share it prints is a FLOOR
# three times over and says so on every run. An unreachable store or a quipu
# predating the stream exits 3: could not look is not zero.
refusal-rate *args:
    @python3 scripts/refusal_denominator.py {{args}}

# Assess a question against the competency suite. NO COVERAGE is an outcome:
# an ontology gap is reported as itself, never answered from the nearest term.
competency question:
    @python3 scripts/competency.py "{{question}}"

# The parsed suite, with its watermark.
competency-list:
    @python3 scripts/competency.py --list

# Per-question stored-query coverage for the competency suite (camayoc-102).
# A question with no stored query is an ontology GAP reported as itself.
query-coverage *args:
    @python3 scripts/query_coverage.py {{args}}

# Ship the example query library (examples/) in a qpack, import it into a fresh
# store and ask every case back (quipu aegis-fxpbys.2). Needs quipu + quipu-server
# on PATH or QUIPU_BIN / QUIPU_SERVER_BIN; exits 3 on a quipu that predates
# queries.ttl, which is a dependency state, not a pass.
examples-qpack *args:
    @python3 scripts/examples_qpack.py {{args}}

# Verify the standalone installer using private release fixtures.
installer-check:
    sh -n install.sh
    python3 scripts/test-install-release.py

# Copied pointer-only repository-pendant controls; no publication or live store.
repository-pendant-tests:
    python3 scripts/test-repository-knowledge.py
    python3 scripts/test-repository-share-query.py
    python3 scripts/test-repository-share-publication.py

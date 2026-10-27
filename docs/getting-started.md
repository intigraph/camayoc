# Getting started

The README's first success, with what each step does and what to do when it
does not go to plan.

## Bootstrap from a shell

```bash
git clone -q --depth 1 https://github.com/intigraph/camayoc
QUIPU_SERVER=http://127.0.0.1:3030 bash camayoc/scripts/bootstrap.sh > bootstrap.log 2>&1
grep -E '^(ontology|shapes|gate: PROVEN — an untagged|camayoc:)' bootstrap.log
```

`scripts/bootstrap.sh` runs four idempotent phases, each skipped when it is
already satisfied:

1. **Reach a quipu, or install one.** It writes `.bobbin/config.toml` with
   `validate_on_write = true`. If no `quipu-server` is on your `PATH`, it
   downloads the latest quipu release (sha256-checked) into `.quipu/bin/`, or
   falls back to `cargo install quipu-ai`. It then starts the server against
   `.quipu/store.db`.
2. **Load the ontology and shapes** (`ontology/core.ttl`,
   `shapes/core.shapes.ttl`), register and label the quarantine planes, and
   load the stored competency queries.
3. **Prove the gate.** It sends records that each omit exactly one required
   property and requires the store to REFUSE every one. A store that accepts
   one is reported loudly, never ingested into.
4. With `--with-claude-hooks`, merge the SessionStart status hook into
   `.claude/settings.json` (for installs that skip the plugin).

## When something fails

| exit | meaning | what to do |
|---|---|---|
| 1 | nothing could be installed (no release for your platform, no cargo) | install a Rust toolchain, or put a `quipu-server` on your `PATH` |
| 2 | the store ACCEPTED a record it must refuse | the gate is off: check `validate_on_write` in `.bobbin/config.toml`; do not ingest into this store |
| 3 | no verdict could be reached | the server did not answer; read `.quipu/server.log` |

The server keeps running for your agents. Stop it with
`kill "$(cat .quipu/server.pid)"`.

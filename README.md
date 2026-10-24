<p align="center">
  <img src="assets/logo.svg" width="200" alt="Camayoc logo — loose gray strands enter from the left, the keeper's yupana counting board hangs from the main cord at center, and ordered, colored, knotted quipu pendants emerge on the right"/>
</p>

# camayoc

**Camayoc gives AI coding agents a governed memory: a starter vocabulary for
work, decisions and outcomes, loaded into a [quipu](https://github.com/scbrown/quipu)
knowledge graph that refuses any record that does not say how it was learned
(observed, declared or inferred).** It is for teams whose agents keep
re-deciding things a past session already settled. It comes as a Claude Code
plugin (a skill, a session-start status hook and a bootstrap command), and as
plain scripts any agent that speaks HTTP can use.

The *quipucamayoc* was the Inca official who kept the quipus and certified what
was read back out of them.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/scbrown/camayoc/main/install.sh | sh
```

Python 3.9+, curl, and a Linux or macOS shell. Downloads and verifies the latest source package into `~/.local/share/camayoc/TAG`.
No native executable, server or agent enrollment is installed.

Set `STACK_INSTALL_ROOT` on the receiving shell for another prefix.
Pinned releases, alternatives, configuration and removal are in the
[installation and usage reference](docs/installation-reference.md).

## Try it

From an empty directory, bootstrap a governed memory and watch it prove its gate:

```bash
git clone -q --depth 1 https://github.com/scbrown/camayoc
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

## Next steps

- [Tambo](https://github.com/intigraph/tambo): guided installation of the complete stack.
- [Detailed examples and agent setup](docs/installation-reference.md).

**Load this repository’s graph:** public repository-pendant assets have not yet been verified. When a release supplies the archive, checksum and provenance, follow the [verified import workflow](https://scbrown.github.io/quipu/sharing/index.html): extract the share directory and use `quipu import SHARE_DIR --db repository.db`. Import stages knowledge; ROOT stays unchanged.

## The stack

<!-- stack-table:start -->

The Quipu stack is built around a governed, bitemporal knowledge store: shared
facts, decisions and work history your agents can query. Quipu can check writes
against your rules, records where each fact came from, and answers what was true
when.

| tool | what it gives your agents |
|---|---|
| [tambo](https://github.com/intigraph/tambo) | 🧰 Agent Platform Toolkit - installs and configures the quipu stack |
| [quipu](https://github.com/scbrown/quipu) | stores shared facts with provenance and history; can check writes against your rules |
| [camayoc](https://github.com/scbrown/camayoc) | prepares knowledge and vocabulary for Quipu, with checks before it is admitted |
| [quechua](https://github.com/scbrown/quechua) | shared vocabulary and shapes for interoperable knowledge |
| [chaski](https://github.com/scbrown/chaski) | reacts to graph changes and delivers governed notifications |
| [bobbin](https://github.com/scbrown/bobbin) | indexes your codebase and injects context from that index and Quipu knowledge |
| [yupana](https://github.com/scbrown/yupana) | shows code impact and can read policies from Quipu to govern agent actions |
| [desire-path](https://github.com/scbrown/desire-path) | turns agent mistakes into feature requests; works on its own without Quipu |
| [seeds](https://github.com/scbrown/seeds) | a work item tracker backed by the Quipu knowledge base |
| [shantytown](https://github.com/scbrown/shantytown) | runs crews of AI agents on work items, triggered by hand, on a schedule or by events; can share work and memory through Quipu |
| [rikuy](https://github.com/scbrown/rikuy) | live brief: status, graphs, watches and signed decisions over Quipu |
| [shuttle](https://github.com/scbrown/shuttle) | runs signed agent workflows and keeps their records in Quipu |

<!-- stack-table:end -->

[License](LICENSE).

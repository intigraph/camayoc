# Domain ownership and Seeds validation

The recording uses source fixtures and a private copy of the installed CLI; it
does not open a store or change production. The base is
`769a333ec5a654449720d7b1d84d85c0ba49178f`. Camayoc's core target for the old
WorkItem class goes from one to zero. The candidate resolves the full profile
from Seeds at its pinned source revision: the synthetic canonical Action
fixture validates, while removing a required date is refused. The CLI digest
is retained in the actual output. The PR caption identifies the source head.

```sh
uv run --no-project --with rdflib==7.6.0 python docs/demos/domain-profiles/reproduce.py
scriptreplay --log-out docs/demos/domain-profiles/output.log --log-timing docs/demos/domain-profiles/timing.log
```

The [recording](output.log) and [timing](timing.log) are small sanitized terminal
artifacts retained with this source. Playback was checked with util-linux
`scriptreplay`; download both files to replay. No inline video playback is
promised. The source resolver can use `CAMAYOC_SEEDS_SOURCE` for an existing
checkout and verifies its bytes; otherwise it reads the immutable public source.
This demo does not establish deployed pins, live alias equivalence or migration
acceptance. Original Quipu domain constraints remain pending load-before-remove.

## Compatibility composition

The compatibility recording demonstrates the next source phase: restored
original targets, an RDF-isomorphic union, identical bytes from two builds, and
refusal of a changed upstream source. It produces no store writes or activation.
The original source hash, shape count and individual artifact hashes are in the
recording. The retained engine fragment is a separate upstream candidate.

```sh
uv run --no-project --with rdflib==7.6.0 python docs/demos/domain-profiles/compatibility.py
scriptreplay --log-out docs/demos/domain-profiles/compatibility-output.log --log-timing docs/demos/domain-profiles/compatibility-timing.log
```

Download the [recording](compatibility-output.log) and
[timing](compatibility-timing.log) to replay. These demonstrate constraint-source
equivalence only. Loaded-set counts, inference, alias-query equivalence and
current production data validation are still separate, unmeasured rollout gates.

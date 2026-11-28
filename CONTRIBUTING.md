# Contributing

Run `just check` and `just test` before submitting a change. `just check` lints
the Markdown and `just test` runs the unit suite. [AGENTS.md](AGENTS.md) has
the conventions.

## RDF review environment

Use the locked project environment when reviewing RDF, SHACL or stored queries.
It declares RDFLib, pySHACL, pytest and the existing test-suite dependencies;
it does not install packages into the system interpreter.

```bash
uv sync --frozen --python 3.11 --group review
just review-preflight
just review-test tests/test_dual_read_queries.py
```

The preflight prints the selected interpreter and installed dependency versions.
It refuses missing imports before the review test recipe runs. Both steps use
Python 3.11 and the same frozen dependency group. Use that interpreter for
ad-hoc review scripts too:

```bash
uv run --frozen --python 3.11 --group review python -c \
  'import pyshacl, rdflib; print(pyshacl.__version__, rdflib.__version__)'
uv run --frozen --python 3.11 --group review python -c \
  'from rdflib import Graph, URIRef; from rdflib.namespace import RDF, SKOS'
```

For a script outside the repository, add `--project /path/to/camayoc` to `uv run`
and pass the script's path after `python`. Do not bootstrap an unrelated global
Python or interpret skipped query suites as review success. The existing
`just test` and CI's missing-RDFLib refusal remain available and unchanged;
this environment is for review, not for deployed adapters.

## Repository pendant provider

Repository pendant builders require explicitly pinned model and tokenizer bytes
and an embedding dimension. The input contract is in
[`repository_share_embedding.py`](scripts/repository_share_embedding.py). This
source plumbing does not enable publication or qualify a provider.

## Diagrams

`python3 scripts/generate-doc-diagrams.py docs/diagrams` writes the SVG
diagrams the README and the book embed.

# Compatibility release demonstration

The trigger is packaging one immutable source revision containing the reviewed
compatibility inputs. The base producer archives the source but carries no built
compatibility manifest. The candidate producer carries seven generated files,
including a 120-shape, graph-isomorphic manifest. Both archives retain the same
positive bootstrap-file control.

Repeat from the repository with RDFLib 7.6.0:

```sh
uv run --no-project --with rdflib==7.6.0 python docs/demos/compatibility-release/run.py
```

The script prints the base producer revision, the exact shared input revision and
the candidate producer SHA256. It executes both producers against that same local
Git revision. No release is published, no service is contacted and no shapes are
loaded. The recording and its output transcript are added after the actual run.

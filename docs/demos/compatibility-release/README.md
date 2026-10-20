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
loaded. Recorded packaging source: `af388d436ebb676cf9dd41f7e07a2e15e21dd76a`.
Its producer SHA256 is
`4f39037d917b759b9f48f1bda704eb6b60aa506b115d0ee3c55d2f87865b047a`.
The final caption/recording commit does not change that implementation.

[Terminal recording](recording.cast), [readable transcript](transcript.txt).
This is a replayable terminal recording, not an inline video. Playback was
verified with:

```sh
python3 docs/demos/compatibility-release/replay.py docs/demos/compatibility-release/recording.cast
```

Observed: before `0` compatibility files, after `7`; the positive bootstrap
member exists in both. The new manifest reports `120` shapes and union
isomorphism, with digest
`0d93ddab56ccd217d47f1e1f837d124b45f775f19ce86befe536e33095ff83b3`.
The asciicast preserves the exact timed terminal bytes; the readable transcript
normalizes PTY CRLF to LF. Round-trip reconstruction and playback were verified.
The original util-linux output/timing pair is retained in the evidence archive.

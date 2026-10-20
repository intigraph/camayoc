# Source releases

Camayoc releases contain the tracked source tree used by the plugin and shell
bootstrap, plus deterministic compatibility files built from that archived source. They do not contain a native Camayoc executable.

A stable version tag matching both plugin metadata files produces three assets:

- `camayoc-vMAJOR.MINOR.PATCH.tar.gz`: tracked source under one enclosing directory.
- `SHA256SUMS`: the archive's SHA-256 digest and exact filename.
- `source.json`: repository, tag, full source revision, artifact name and digest.

Download all three from the selected release and verify before extracting:

```bash
sha256sum -c SHA256SUMS
tar -xzf camayoc-v0.1.0.tar.gz
```

The checksum establishes agreement with the published asset. Automated consumers
must also pin the independently reviewed digest and revision; a checksum fetched
from the same release is not an independent source of trust.

The Release workflow accepts an existing stable tag on main, either from its tag
push or an explicit dispatch. Before publication, the shared source-release proof
runs lint, private-key checks, the full test suite and packaging controls. It then
extracts the actual archive and runs bootstrap, repeat-bootstrap and ingest/refusal
controls against disposable loopback Quipu using the same reviewed dependency as
the existing integration job. A missing dependency or failed proof stops publication.
The same proof runs on packaging pull requests without publication permissions.

Packaging reads an exact Git revision. Untracked files and modified checkout bytes
never enter the bundle. Stable versions must agree with both metadata files, and
the output directory must be empty. Repeating the same revision with the workflow's
Python 3.11 runtime produces the same archive bytes. Existing releases are not
overwritten. Publishing a source bundle does not install it or activate a service.

## Domain compatibility artifact

When the archived revision carries the pinned original domain ontology and its
compiler, the same producer adds `compatibility/aegis/` inside the existing source
tarball. Its `manifest.json` names every per-domain file, SHA256, byte count and
shape count. `source.json` adds a `compatibility` descriptor with that manifest's
path and SHA256. No extra release, publisher or automatic loader is introduced.
Older revisions without those inputs retain their original source-only format.

The build uses only archived Git objects, including the frozen upstream fixture;
dirty working files cannot alter its inputs. The compiler requires RDFLib 7.6.0,
restores original target IRIs and proves the independently parsed serialized
union RDF-isomorphic to the pinned upstream graph: 120 NodeShapes. Existing
legacy aliases and rule/property exclusions are not changed by packaging.

A consumer must independently pin the normal archive digest, full source revision
and compatibility manifest digest, verify regular members and all carried file
hashes, then use its normal scoped loader and rollback. A successful package does
not prove live loaded counts, current-data validation, alias-query equivalence or
removal of the old source copies. No tag, release or installed pin is selected by
this source change.

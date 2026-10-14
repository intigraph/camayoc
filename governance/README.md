# Governance resources

This optional module implements the retained governance specification as a new
source proposal. It makes no claim to recover a lost source tree, original
implementation, commit history or certification. The specification is the
reviewed 0.7.0 catalogue and single combined SHACL profile. Their content identity
is `sha256:e3952ce1919300af934e0a2846836e559d3d900652ecbbf7cbbd9889ba056e17`.

The catalogue contains 449 direct class/property declarations (1,418 triples).
The profile contains 2,133 triples and directly targets Directive, Policy,
TextRule, InternalIdentifierPattern, OperatingPoint, Reaction, AdmissionPolicy,
Decision and DecisionRecord. AdmissionPolicy examples remain proposed policies;
validation does not activate enforcement or resolve instruction conflicts.

The manifest describes specification data. Its historical source graph is not a
default destination or permission to load ROOT. Optional alignments are separate:
AdmissionPolicy specializes ODRL Policy, `refines` specializes PROV revision only
for substantial revisions preserving or narrowing obligations, and `contradicts`
has no external equivalence. These are newly adjudicated meanings, not inferred
from the artifact or proof of ODRL conformance.

## Explicit offline staging

`scripts/governance_loader.py` is a new optional local resource stage. Ordinary
bootstrap is unchanged. Run it with `uv run`; its script metadata pins the RDF
parser. All arguments are required:

- `--artifact`: reviewed local pendant, with its exact `--file-sha256`.
- `--content-hash`, `--version`, `--shape`: exact native manifest identity.
- `--catalog-path`, `--catalog-sha256`, `--profile-path`, `--profile-sha256`:
  reviewed resource files and exact byte hashes.
- `--graph`: explicit absolute nonROOT destination graph IRI.
- `--output`: new owned scratch database in an existing owned directory.
- `--binary`: explicitly selected native Quipu executable.

The stage copies pinned bytes into private temporary storage, runs native pack
verification, checks the manifest and catalog version, unpacks into the selected
graph, then independently exports the entire catalog and reads the named shape
resource. Every direct class/property declaration must be present with its
exact type; both RDF graphs must match their reviewed resources. Only after
these checks does an atomic no-replace link publish the new database.

Repeating against an existing private regular database requires full exact
readback and reports `verified-existing`. A foreign, unreadable, symlinked,
shared-permission or sidecar-backed destination refuses. No existing database
is overwritten. This is a standalone resource store, not a refresh or merge
into an existing consumer.

There are no HTTP calls, automatic alignment loads, native policy adapters,
cache replacements, hook activation or promotion commands. Server acceptance,
mixed-policy import refusal, projection/cache controls, source review and
certification remain required under the consumer contract. Offline stage
success alone does not fulfill those gates.

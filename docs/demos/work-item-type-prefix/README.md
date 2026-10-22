# Work-item type verification

This offline fixture calls the actual verifier on base
`769a333ec5a654449720d7b1d84d85c0ba49178f` and the candidate source.
The same WorkItem IRI is rendered with an alternative RDF prefix: the base
reports UNKNOWN and the candidate verifies it. A wrong type remains UNKNOWN.
The fixture uses no credentials, journal, network or running producer.

Run `python3 docs/demos/work-item-type-prefix/probe.py` from the repository root.

[Recording output](output.log) and [timing](timing.log) are retained with this
demonstration. Download both and replay with
`scriptreplay --log-out output.log --log-timing timing.log`.
These are terminal recording files; no inline video playback is claimed.

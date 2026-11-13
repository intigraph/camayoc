# Lost knot response replay

Run `python3 docs/demos/knot-response-loss/demo.py`. The same synthetic committed
write loses its response on base `0204212749b3a3730c18a304338e029ebd4c96cd` and the
candidate (the containing source revision). Base leaves the effect pending with
an `unclassified` error; the candidate resolves a conforming `finished` receipt
and exact effect readback with one write. The `unknown` completion control stays
pending with one write. This uses private SQLite fixtures, not a live service;
it does not prove installed or scheduled acceptance or measure network latency.

[Output transcript](output.log) and [timing](timing.log) form an output-only
terminal recording. Download both and replay with
`scriptreplay --log-out output.log --log-timing timing.log`.
No inline playback is claimed. These small sanitized files are retained in this
existing demo directory with the source history.

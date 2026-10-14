# Camayoc 0.1.2: native bootstrap (90 seconds)

[Watch the video](demo.mp4) · [Replay the terminal recording](demo.cast)

An actual `claude -p '/camayoc:bootstrap'` run in a fresh Ubuntu 24.04.5 VM,
using Claude Code 2.1.287 and the **released Camayoc 0.1.2** plugin. No existing
Quipu store or homelab configuration was present. Bootstrap downloaded and
checksum-verified released Quipu 0.13.0, loaded the ontology, planes and
shapes, then refused seven invalid writes with HTTP 400. The process exited 0.

This is an **edited replay of captured native output**, paced to 90 seconds;
the underlying run took 23.12 seconds. Repeated query-loading lines and the
optional-components interview are omitted. No callbacks or successful tool
results were simulated. The reports did not identify each missing property;
the recording preserves that limitation. Optional components were not installed.

To reproduce with the same release, download and verify the
[0.1.2 source bundle](https://github.com/scbrown/camayoc/releases/tag/v0.1.2),
extract it, and register the extracted marketplace:

```text
claude plugin marketplace add /path/to/camayoc-v0.1.2
claude plugin install camayoc@camayoc
```

Authenticate Claude Code using your own supported account, then start it in
an empty project and run `/camayoc:bootstrap`. The recording uses the native
headless invocation of that same plugin command. Existing endpoints are not
assumed to be owned merely because they fail a health check.

Exact versions and release identity are in [recording.json](recording.json).

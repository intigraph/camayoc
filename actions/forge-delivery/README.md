# Merge and publication events

This action converts a `pull_request: closed` delivery that actually merged,
or a `release: published` delivery, into CDEvents 0.4.1 on CloudEvents 1.0.
It calls the same governed `ingest_cdevents.py` as other emitters. Unmerged
closes and drafts write nothing; malformed actor, time or SHA refuses.

Use a reviewed immutable action revision, in a trusted workflow that has no
checkout or execution of pull-request code. Configure the two event triggers,
then supply `quipu-url` and a scoped `quipu-token` secret. No polling, catch-up
job or historical coverage is implied. Deploy the reviewed delivery shapes and
admit the producer before enabling a workflow; source support alone is not
runtime admission. Each repository and forge needs its own coverage check.

For releases, the runner's `GITHUB_SHA` must resolve the published tag. The
event never treats `target_commitish` (which can be a branch) as a SHA. A
non-Actions caller must resolve the tag itself and supply `--release-commit`.
The actor is the release delivery's sender; for merges it is `merged_by`.

The CLI is portable to a compatible Forgejo payload:

```sh
python3 emitters/github/delivery.py release payload.json \
  --forge forge.example --release-commit 0123456789abcdef0123456789abcdef01234567
```

It prints a CloudEvent, or nothing for a delivery that is not a publication or
merge. Pipe it to `scripts/ingest_cdevents.py --actor <writer> --post` only
after the producer and schema are admitted. The record's `eventActor` names
the acting forge principal; the episode actor names the ingress writer.
Source + delivery identity makes redelivery deterministic. Publications map
to `ReleaseEvent` in the `release/YYYY-MM` window; merges keep the existing
`ChangeEvent` mapping in `change/YYYY-MM`. The artifact identifier is a
generic repository release package URL, not a claim about individual assets.

The type and artifact subject follow the [CDEvents CI specification](https://github.com/cdevents/spec/blob/v0.4.1/continuous-integration.md).

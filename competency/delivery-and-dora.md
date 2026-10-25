# Competency questions — software delivery and DORA

The questions that earn the delivery vocabulary: deployment outcomes ingested
as CDEvents (CD Foundation) on the CloudEvents envelope
(`scripts/ingest_cdevents.py`, mapping data in `mappings/cdevents.json`). Per
the house rule, every term minted for delivery is owed to a question here.

Conventions: deployment outcomes are `aegis:DeploymentEvent` records in
time-windowed operational graphs (`{WINDOW_NS}deployment/{YYYY-MM}`,
`scripts/windows.py`), one per CloudEvent (`aegis:cloudEventSource` +
`aegis:cloudEventId` is the identity, so redelivery is idempotent). The
outcome is a closed set — `succeeded`, `rolledback`, `failed` — so change
failure rate is a count, not a pattern match. The service deployed is the
existing service entity (`aegis:deployedService`), not a new kind.

## Deployments

1. Which deployments of each service happened on day `<YYYY-MM-DD>` (UTC),
   and with what outcome? (the `camayoc_deployments_by_day` stored query; its
   first acceptance is reproducing an independent deploy-log count exactly)
2. What was serving on environment `<env>` at instant `<t>` — the artifact of
   the latest `succeeded` or `rolledback` event at or before `<t>`?
3. Which artifact did deployment `<event>` attempt, and which was serving
   after it? (`aegis:attemptedArtifactId` vs `aegis:artifactId` on rollback)

## DORA

4. Deployment frequency: how many `succeeded` deployments did service `<s>`
   have per week?
5. Change failure rate: of the deployments of `<s>` in week `<w>`, what
   fraction were `rolledback` or `failed`?
6. Lead time for changes: for each artifact first `succeeded` on an
   environment, how long after its change was merged did that happen?
7. Time to restore: after a `failed` or `rolledback` deployment, or an
   `aegis:Incident`, how long until the next `succeeded` deployment of the
   same service?

## Supported emitters

8. Which source adapters support delivery events, which standard do they use,
   how are they invoked, and where are their documented contracts? The public
   catalog is `catalog/delivery-emitters.json`: typed `Tool` records with
   `emitterCatalog`, `emitterOrder`, `emitterTool`, `emitterStandard`,
   `emitterHow`, `emitterStatus` and `emitterDocs`. The README renderer queries
   the exact catalog identities through Quipu; source support does not prove
   an installed or armed producer, or coverage of a deployment window.

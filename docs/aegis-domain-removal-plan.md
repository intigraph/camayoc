# Domain ownership: source to acceptance

The staged constraint packs, property/rule placement and raw alias CQs are
source artifacts. They do not establish a running migration. Quipu retains its
compatibility copies until destination serving and stored-data checks pass.
Seeds remains the work-item validation owner. The only retained engine shape
candidate is `InternalIdentifierPatternShape`, whose exact code reference is
recorded in `shapes/aegis/placement.json`; do not reclassify domain constraints as
engine guarantees to keep them upstream.

1. Merge reviewed source with complete CI and one independent peer. Publish
   through the existing release lane. Record exact producer SHA, artifact
   hashes, manifest and deployment pin candidate; do not substitute branch HEAD.
2. Name the rollback **before** rehearsal: record every prior exact producer and
   consumer pin, verified artifacts and loaded set contents. Inverse deployment
   is restore those pins and re-apply the scoped `shacl` tags. Test this inverse
   in the lab; a source revert alone does not remove installed data.
3. Rehearse the exact same deployment SHA against a lab Quipu configured with
   production's pinned shape sets. Record the complete expected registry names
   and hashes. Assert set equality **by name**, target presence and all unrelated
   hashes unchanged; a matching count alone is insufficient. Do not unload the
   old location before the destination serves the verified candidate.
4. Revalidate a complete stored-data snapshot using the composed candidate and
   compare violations and populated vocabulary with the prior pins. Run the raw
   alias CQs on that same snapshot with compatibility rewriting disabled. Empty,
   truncated, unknown-scope or rewritten results do not pass. Preserve explicit
   differences for disposition; do not rewrite live legacy types to make a test
   pass. Keep rules/inference activation distinct from SHACL loading.
5. Use a separately admitted operator operation, deployment window and scoped
   apply, after successful same-SHA rehearsal and rollback proof. Read the actual
   recap, serving identity and named-set inventory; run one smoke check. Retain
   the prior artifacts and restore pins/re-apply `shacl` if acceptance fails.
6. Only after the new location is proven serving, propose a **separate** Quipu
   compatibility-copy removal change. Update its fixture includes and book
   pointers while preserving engine-owned guarantees. That change needs its own
   CI, review and deployment safety checks. This plan authorizes no removal.

No production load, unload, restart, data rewrite or pin change is performed by
these source files. Lab success is not production acceptance, and prior
activation grants are not reusable.

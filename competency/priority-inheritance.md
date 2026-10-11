# Priority inheritance

1. Which active work items block a P0, directly or through a chain?
2. What is each item's minimum effective priority without changing its declared priority?
3. Does closing a root or removing its blocking edge withdraw the inherited priority?
4. Can another project or a related/shared-verification edge affect this project's rank?
5. Can a workflow run or other non-work entity masquerade as an active work item?
6. Which Quipu workflow run explains a rank change and its owner notification?
7. Which P0 acceptance checks are deferred until after installation, and which remain prerequisites?

Questions 1–5 require the graph-scoped rule and priority query. Questions 6–7
require the direct Quipu workflow and deployment-policy integration; they are
not answered by this source catalog alone.

# GitHub ingress: one reader, typed facts, events

**Status: proposed (2026-10-28).** Shapes and the producer are source only.
Nothing is deployed, scheduled or registered with GitHub by these changes.

## 1. Problem

GitHub activity reaches the stack through four separate readers, and none of
them writes facts that another tool can query:

| reader | what it reads | where the result goes |
|---|---|---|
| `aegis/scripts/gh-ci-watch.sh` (cron) | `gh run list` per repo and workflow | Prometheus metrics, crew inbox, escalation items |
| `aegis/scripts/github-issues-patrol.sh` | `gh issue list` on one repo | tracker items |
| participation watch (aegis-2no) | GitHub notification mail over IMAP | `st inbox hammond` |
| `camayoc/emitters/github/changes.py` | GraphQL, PRs updated since a time | CDEvents for DORA lead time |

The shared REST quota ran out twice in one hour on 2026-10-21 (aegis-izs). On
that day 1,310 of 1,402 sampled `gh` processes came from cron, and
`gh-ci-watch.sh` alone made 27% of them. Pull request, review and merge work
cannot react to GitHub: no reader turns a review or a green check into
something chaski can route.

This design replaces those readers with ONE ingress. It writes each GitHub
activity into Quipu as typed facts. The write's own transaction emits the Quipu
events that chaski routes on.

```text
GitHub --webhook--> receiver --+
                               +--> spool (dedup by delivery) --> mapper --> Quipu /episode
GitHub <--poll (budgeted)-- poller --+                                      |
                                                    entity.added/updated, episode.ingested
                                                                            v
                                                     chaski (c01c.4 rules) --> st jobs (c01c.2)
```

## 2. Entities and natural keys

All terms are in the `aegis:` namespace, as for the delivery vocabulary.
Classes are prefixed `GitHub`. This avoids a clash with shuttle's
`aegis:WorkflowRun` and keeps the forge's meaning out of generic names. The
shapes are in `shapes/github.shapes.ttl`.

| class | GitHub object | natural key | node name |
|---|---|---|---|
| `GitHubRepository` (`rdfs:subClassOf schema:SoftwareSourceCode`) | repository | `node_id` | `github.<node_id>` |
| `GitHubPullRequest` | pull request | `node_id` | `github.<node_id>` |
| `GitHubIssue` | issue | `node_id` | `github.<node_id>` |
| `GitHubComment` | issue comment, review comment | `node_id` | `github.<node_id>` |
| `GitHubReview` | pull request review | `node_id` | `github.<node_id>` |
| `GitHubWorkflowRun` | Actions workflow run | `node_id` | `github.<node_id>` |
| `GitHubCheckRun` | check run | `node_id` | `github.<node_id>` |
| `GitHubRelease` | release | `node_id` | `github.<node_id>` |
| `GitHubPush` | push (no node id) | repository `node_id` + ref + before + after | `github.push.<digest>` |
| `GitHubActivity` | one observed change to any of the above | activity key (below) | `github.activity.<digest>` |

GitHub node ids are global, opaque and stable across renames and transfers.
Within one id format they are unique across object types. Older repositories
can return legacy base64 ids, which may contain `=`, `+` or `/`. Those ids are
hashed (`github.b<sha256[:32]>`) instead of being sanitized. Sanitizing could
merge two ids. The exact id is always kept in `aegis:githubNodeId`.

**Entities carry only facts that never change**: node id, database id,
number, full name at first sight, URL, author and creation time. **Changing
state goes on `GitHubActivity`**: PR open, closed or merged, head commit,
draft, review verdict, run status and conclusion, attempt. This follows the
rule that a fact must be true when written. A PR's state is "the state on its
latest activity", which is a stored query evaluated when read. It also means
re-asserting an entity is always byte-identical, so a `maxCount 1` never sees
two values.

Not modeled: comment and review bodies, check logs and commit messages. Only
identifiers, times, verdict words and `@` mentions are stored. Mentions are
extracted from the text, which is then discarded. The `changes.py` rule applies
here too.

### 2.1 GitHubActivity

One record per distinct change, never updated:

- `githubEventKind`: a closed set, `<event>.<action>` (for example
  `pull_request.closed` or `workflow_run.completed`), or `push`.
- `githubActivityKey`: sha256 of the activity key. `occurredAt` (RFC 3339)
  comes from the payload, never the time the record was received.
- `aboutGitHubEntity`: the entity it changes. `inGitHubRepository`: the
  repository.
- `githubActorLogin`: who acted. `commitSha` (reused): the head commit the
  activity concerns.
- State, where the kind has it: `githubState`, `githubMerged`,
  `githubMergeCommitSha`, `githubDraft`, `githubBaseRef`, `reviewState`
  (reused), `githubStatus`, `githubConclusion`, `githubRunAttempt`,
  `githubMentions`, and `githubPrerelease`.

Activity keys are built from payload fields that both transports carry. The
same change therefore gets the same key whether a webhook or the poller
reported it. Time is used only where an action can repeat:

| kind | key fields |
|---|---|
| `pull_request.opened` | PR node id |
| `pull_request.closed` | PR node id, `closed_at` |
| `pull_request.synchronize` | PR node id, new head sha |
| other `pull_request.*` | PR node id, `updated_at` |
| `pull_request_review.submitted`, `.dismissed` | review node id |
| `*_comment.created` | comment node id |
| `*.edited` (comments, reviews) | node id, `updated_at` (`submitted_at` for reviews) |
| `issues.opened` / `.closed` / other | as for pull requests |
| `check_run.*` | check node id, status, conclusion |
| `workflow_run.*` | run node id, `run_attempt`, status, conclusion |
| `push` | repository node id, ref, before, after |
| `release.*` | release node id |

Every key also includes the kind.

## 3. Graph placement and events

Each activity is one `/episode` with `group_id: "github"`, written to that
month's operational window graph `{WINDOW_NS}github/{YYYY-MM}` (see
`scripts/windows.py`). The month comes from `occurredAt`. This is how the
deployment events work: a completed month can be frozen whole, and entity IRIs
stay valid across windows because they do not depend on the graph.

The episode name is `github:<digest>`. Its source is
`github:mapping=<sha16>:<kind>:<digest>`. Every byte of the body comes from the
delivery and the mapping, so re-sending one delivery sends an identical
episode. Quipu's content hash (hq-fhc) then makes the re-ingest a no-op: no new
transaction and no new events. This was measured with quipu 0.16.0: the second
ingest reported `0 facts (tx 0)`.

The two transports can describe one change with slightly different bytes. A
webhook's `pushed_at` and the Events API's `created_at` can differ by a
second, for example. So the spool records each written activity key, and the
first transport to write an activity wins. A later delivery with the same key
is marked `duplicate` and is not written. Without that, one activity node
could end up with two `occurredAt` values.

The write lands as facts on the change feed in the same transaction. With a
quipu that has registered named-graph events (`event-push.md` §4), the same
transaction also emits these events:

- `episode.ingested` for `github:<digest>`, group `github`
- `entity.added` for the new `GitHubActivity`, and for any entity seen for the
  first time in that window
- `entity.updated` for entities that already exist

Chaski does not route on Quipu event names alone. Their payload carries only
the subject, so a rule would have to read the facts anyway. The routing
contract for c01c.4 is an incremental adapter (`trigger: changes`) subscribed
to `types: [aegis:GitHubActivity]` and to the current and next month's
`github` window graphs. It routes on `githubEventKind`. The kinds chaski needs
first:

| kind | rule it feeds (c01c.4) |
|---|---|
| `pull_request.opened`, `.synchronize`, `.ready_for_review` | review job when CI is green and no non-author review exists after N min |
| `pull_request_review.submitted` (`reviewState` approved) | merge job through the per-repo serialized merge lane |
| `workflow_run.completed` / `check_run.completed` on the default branch, `githubConclusion` failure | main-CI-red triage job |
| `release.published` | install / announce jobs |
| `issues.opened`, `issue_comment.created`, `pull_request_review_comment.created` with `githubMentions` containing the watched login, or on a thread the login participates in | participation notice to the administrator (aegis-2no) |
| `push` to the default branch | bobbin reindex for GitHub-sourced repositories (now pull-only) |

Chaski's adapter `--describe` names graphs exactly (`incremental.py`). Window
rollover therefore needs the adapter to describe two months at once. If chaski
caches the description for its lifetime, it needs a restart each month or
prefix matching instead. **Open for c01c.4.**

## 4. Transport

### 4.1 Webhook receiver (primary)

A small stdlib Python service, `scripts/github_ingress.py serve`. It follows
the pattern of bobbin's `webhook-index.py`:

- **Authentication.** The receiver reads `X-Hub-Signature-256`
  (`sha256=<hex>`, an HMAC-SHA256 of the exact body) and checks it with a
  constant-time compare before parsing any JSON. The secret comes from an
  owner-only file (`--secret-file`), which Ansible renders from Infisical. It
  is read once at startup. An empty or missing secret refuses every delivery,
  so the receiver fails closed. The secret never appears in logs, the spool
  or Quipu.
- **Limits.** Bodies over 5 MiB get 413. Repositories outside `--repo`
  (`owner/name`) are acknowledged and ignored, never written. `ping` is
  acknowledged.
- **Spool first.** The receiver writes `(delivery id, event name, body)` to a
  SQLite spool (WAL, `synchronous=FULL`), then answers 202. GitHub expects an
  answer within 10 s, and nothing slow happens before the answer. A delivery
  id already in the spool gets 200 `duplicate`. GitHub keeps
  `X-GitHub-Delivery` on manual and automatic redelivery, so this is the
  first idempotency layer.
- **Drain.** A worker thread maps each pending entry and posts it with
  `planes._post("/episode")`. Before the first write to a month it calls
  `windows.ensure_window`. It then marks the entry `written`. A failed post
  stays pending with exponential backoff. A response that never arrives is
  safe to resend, because the episode is byte-identical (§3). An event the
  mapping cannot place is marked `quarantined` with its reason, never
  dropped. The receiver's metrics report it, and it never blocks the queue.

**Hosting.** The receiver runs on the chaski host as a systemd user unit next
to chaski. The order is GitHub, then Quipu, then chaski: facts land in Quipu
before anything reacts. Chaski's `external_event.py` queues reactions; it does
not write facts. The ingress is camayoc's job, as it is for CDEvents.

**Reachability, measured.** There is no public HTTPS route into the LAN today.
The bobbin webhook takes Forgejo pushes inside the LAN only. goldblum's
`bobbin_server` defaults say GitHub-sourced repositories are "pull-only (no
webhook)". GitHub cannot reach traefik. The webhook path needs one outward
change, which Steve approves (§7). Until then the poller is the only
transport.

### 4.2 Budgeted poller (fallback, and the only path until §7)

`scripts/github_ingress.py poll` puts entries into the same spool as the
receiver, and the same mapper drains them. It calls the API only through `gh`,
which holds the credential. That way the aegis-izs `gh` shim attributes and
meters every call.

- **Sources per repository.** It reads `GET /repos/{r}/events`, which covers
  pushes, pull requests, reviews, review comments, issues, issue comments and
  releases. It also reads `GET /repos/{r}/actions/runs?per_page=20` for
  workflow runs. Check runs are not polled: one request per commit is too
  expensive, and `workflow_run` covers CI for Actions repositories.
- **Conditional requests.** Every read sends the stored ETag. A 304 costs no
  primary quota. On a quiet repository nearly every request is a 304.
- **Budget.** A local hourly token bucket, `--budget-per-hour` (default 300,
  6% of 5,000), counts 200 responses and hydration requests. Before each
  cycle the poller reads `X-RateLimit-Remaining` and stops spending below
  `--reserve` (default 1,000). The interactive crew keeps that reserve. When
  the poller is over budget it skips the cycle and says so in its receipt. It
  never sleeps inside a request.
- **Cadence.** It waits at least `X-Poll-Interval`, which GitHub sets (usually
  60 s), and never less than `--interval`.
- **Hydration.** Events API payloads are smaller than webhook payloads. This
  was measured on `intigraph/camayoc` on 2026-10-28. A `PullRequestEvent`
  carries only `id`, `number`, `url`, `head` and `base`. It spells a merge as
  action `merged`, and a review as `created`. `PushEvent` has `head` instead
  of `after`. The `repo` object has no `node_id`.

  The poller handles each of these:
  - It fetches the repository object once and caches it in the spool.
  - It fetches a trimmed pull request once, by its API URL, and counts the
    request in the budget.
  - It keeps the event's own head, base and action, because the fetched
    object shows the PR as it is now, not as it was at the event. It drops
    `draft`.
  - It renames the actions through `mappings/github.json`.

  A fetch that fails leaves the trimmed object in place. The mapper then
  quarantines the entry with the missing field named, and the poller does not
  retry it.
- **Spool keys.** `poll:<events-api id>` and
  `poll:run:<run id>:<attempt>:<status>:<conclusion>`. These differ from
  webhook delivery ids, but the activity key is the same for the same change.
  The spool's activity ledger therefore writes it once (§3).

### 4.3 Threads outside watched repositories

aegis-2no also covers threads in repositories Steve does not own, such as
upstream issues where he commented. Webhooks cannot be registered there.
Phase 2 adds a third poller source:
`GET /notifications?participating=true` (ETag'd), which re-uses hydration to
fetch the subject thread. That replaces the Gmail IMAP path. It is not in the
first producer.

## 5. What happens to the existing readers

| reader | keeps | moves |
|---|---|---|
| `gh-ci-watch.sh` | Its transition state machine (BROKE / RECOVERED / TERMINAL-CANCELLED), metrics, and auto-deploy P1 promotion, until c01c.4 ships the main-CI-red rule. | Its `gh run list` polling moves to the ingress. The watcher reads the latest `workflow_run.completed` activity per (repository, workflow) from Quipu. This drops about 27% of the measured `gh` burn. Once chaski's triage rule is live, the script keeps only the Prometheus exporter, or retires. |
| aegis-2no participation watch | Discord half, unchanged (not GitHub). Its acceptance contract: one delivery per natural event, no duplicates on replay. | The GitHub half (IMAP of notification mail) moves to `GitHubActivity` plus a chaski participation rule. The "natural comment, review and mention samples" its acceptance waits on can then be counted in Quipu. |
| `github-issues-patrol.sh` | Nothing. | It becomes a chaski rule: `issues.opened`, then a tracker item. |
| `emitters/github/changes.py` | The CDEvents output contract for DORA. | Its GraphQL reads become a projection of GitHub activities, with no API calls (follow-up). |
| bobbin webhook (Forgejo) | Unchanged. | GitHub-sourced repositories gain push reindexing through chaski (`push` to the default branch). |
| `shantytown/pr_binding_check.py` | Unchanged (on-demand read). | Nothing. |

## 6. Idempotency and failure, end to end

1. **Same delivery twice.** The spool refuses it by delivery id and answers
   200.
2. **Same change from webhook and poller.** The activity key is the same, and
   the spool's activity ledger marks the second one `duplicate`. Where the
   two transports disagree on the timestamp a key uses (a reopen, an edit),
   both are written. Consumers treat activities as at-least-once.
3. **Lost Quipu response.** Resend. The body is byte-identical, so Quipu's
   content hash makes the second write a no-op.
4. **Mapping change.** The mapping sha in `source` changes, so a re-mapped
   activity is a NEW episode over the same activity node. It is replayable
   from the spool; the spool keeps bodies for `--retain-days` (default 14).
5. **Unknown event or action.** The entry is quarantined with a reason and
   counted. It is never guessed or dropped.
6. **Receiver down.** GitHub marks the delivery failed and does not retry
   automatically. The poller's next cycle covers the gap from the Events API,
   which keeps 30 days and 300 events. A long outage can be replayed from the
   GitHub "recent deliveries" API (follow-up).

## 7. Registration Steve approves (not done here)

Both of these are outward changes and wait for approval:

1. **A public HTTPS route** to the receiver, and only to its one path. There
   are two options. One is a Cloudflare Tunnel or a port-forwarded traefik
   entrypoint carrying only `PathPrefix(/hooks/github)`. The other is GitHub's
   webhook forwarding to an existing public host. The receiver checks every
   delivery's HMAC either way.
2. **One webhook per watched repository.** An org-level hook on `intigraph`
   works too, plus per-repo hooks for `scbrown/*`. Settings:
   - Payload URL: `https://<public host>/hooks/github`
   - Content type: `application/json`
   - Secret: a new random 32-byte value, stored in Infisical as
     `GITHUB_WEBHOOK_SECRET`. Ansible renders it to the receiver's secret
     file. It is never pasted anywhere else.
   - SSL verification: on
   - Events: Pushes, Pull requests, Pull request reviews, Pull request review
     comments, Issues, Issue comments, Check runs, Workflow runs, Releases.
     Not "send me everything": unmapped events would only be quarantined.

## 8. Stored queries (written with the producer)

- `camayoc_github_pr_current_state`: per PR, the state, head and draft flag
  of its latest `pull_request.*` activity.
- `camayoc_github_reviews_on_head`: reviews whose `commitSha` equals the PR's
  current head, with verdict and author. Chaski uses it for "no non-author
  review".
- `camayoc_github_default_branch_ci`: latest `workflow_run.completed` per
  (repository, workflow name) on the default branch.
- `camayoc_github_participating_threads`: threads where a login authored,
  acted or was mentioned. Participation is decided when read, never stored.

## 9. Related

aegis-c01c (epic), c01c.4 (chaski rules), c01c.5 (tambo verify), aegis-2no,
aegis-izs, aegis-j09 (bobbin webhook), `docs/design/ingress.md`,
`scripts/ingest_cdevents.py`, quipu `docs/design/event-push.md`.

# Event-armed review deadline

Implementation status: source library; scheduler, authority adapters, signed
transport binding and installation are not connected. Live recruitment remains
separately gated.

`github_review_deadline.Deadlines` accepts an exact repository, PR and head from
an authenticated event. `verify_event` must verify the local trusted-origin
contract; an event body or a caller boolean is not authority. Repositories require
an explicit allowlist. Only the review job is represented by this interface.

On a green wake, one authoritative `resolve_ready(repo, pr, head)` read must
confirm the current exact head, complete required checks, absence of an accepted
non-author verdict, the continuous green-unreviewed start, observation freshness,
and the native bound work item. The payload cannot supply those facts. At most
one deadline per repository/PR/head is persisted. The returned `due_at` is two
hours after that authoritative start, not two hours after delivery receipt.

A separately reviewed scheduler must register one persisted callback for that
exact key. It must not periodically scan Quipu or GitHub for stale PRs. This
library exposes neither a due-list scan nor a service, timer or CLI installer.
An early callback makes no authority read and does not consume the deadline.

Push events cancel armed deadlines for other heads of the PR; review events
cancel the matching head. A delayed push for the current head preserves its
deadline and does not advance its cancellation fence. Persistent cancellation
markers also fence a late arm or a callback that
has already reserved its launch. A review of a different head cannot cancel the
current head. Cancelled deadlines do not rearm for the same head. Cancellation
and delivery receipts are retained; deleting them requires a separate policy.

`fire(repo, pr, head)` claims that exact persisted deadline before callbacks.
It uses the existing `github_recruitment.Attempts` ledger and the same
repository/PR/head/job key as webhook recruitment. A fresh resolver check and
native custody/holds/governor admission run after reservation. Only the existing
signed transport may supply `spawn`; this library grants no controller, UID,
key, credential, listener or launcher authority. A lost reply, refusal or process
death cannot authorize automatic replay. Rollback reverts source while retaining
deadline, delivery and attempt tombstones.

The installed caller must open the same private spool database as the webhook
consumer. This library accepts a connection; it never selects a new store or
asserts that two arbitrary database paths share replay protection.

The Quipu read budget is at most one bounded resolver read per green event,
zero for cancellation, and one resolver plus one native custody read per fired
deadline. Callbacks must enforce their own IO timeouts; this library does not
interrupt a blocked callback. A resolver that needs more requests must first
change the reviewed budget. GitHub and work-item state must be complete; missing
or partial checks are unknown. Authentication is local and has no Quipu budget.

The test suite states that budget and exercises real SQLite persistence, two
connections, cancellation interleavings, restart/crash tombstones, two-hour
boundaries, actual fixture HTTP request counts and the shared webhook key.
All authority and spawn collaborators
are synthetic; no real review job or production request is used.

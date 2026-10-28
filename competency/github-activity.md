# Competency questions — GitHub activity

The questions that earn the GitHub vocabulary (`shapes/github.shapes.ttl`,
design `docs/design/github-ingress.md`, aegis-c01c.3). One ingress writes
GitHub activity as typed facts; these are what chaski's rules and the crew
ask of it. Per the house rule, every GitHub term is owed to a question here.

Conventions: entities (`aegis:GitHubPullRequest`, `aegis:GitHubReview`,
`aegis:GitHubWorkflowRun`, ...) carry only facts that never change and are
keyed by GitHub node id. Every changing state rides on an append-only
`aegis:GitHubActivity` (`aegis:githubEventKind`, `aegis:occurredAt`), so
"current" is always the latest activity, judged at read time.

## Pull requests

1. What is the current state of pull request `<repo>#<n>`: open or closed,
   merged, draft, and its head commit, as left by its latest
   `pull_request.*` activity?
2. Which reviews of pull request `<pr>` were submitted on its current head
   commit, by whom and with what verdict, and is any of them by someone other
   than the author? (the "no non-author review" input to the review job)
3. Which open, non-draft pull requests have a green `workflow_run.completed`
   on their head commit and no non-author review submitted after it?

## CI

4. For repository `<r>` and workflow `<w>`, what was the conclusion of the
   latest completed run on the default branch, and since when has it been
   failing? (gh-ci-watch's question, answered from facts instead of polling)

## Releases and pushes

5. Which releases were published in repository `<r>` since `<t>`, and which
   pushes reached its default branch since then?

## Participation

6. On which pull requests and issues did login `<l>` author, act or get
   @-mentioned, and which of those threads had activity by someone else after
   `<l>`'s latest activity? (aegis-2no's GitHub half)

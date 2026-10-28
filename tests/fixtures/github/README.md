# GitHub ingress fixtures

Recorded on 2026-10-28 from the public repository `intigraph/camayoc` with
`gh api`. They are used by `tests/test_github_shapes.py` and
`tests/test_github_ingress.py`.

- `deliveries/*.json`: one webhook delivery each, as `{headers, body}`. The
  bodies are built from recorded REST objects (pull requests 172 and 177, a
  review, an issue comment, a check run, a workflow run, a push event and
  release v0.1.3) wrapped in GitHub's webhook envelope. `pull_request.opened`
  is PR 177 with its state rolled back to open time. The push's
  `repository.pushed_at` is epoch seconds, as in real push webhooks. `ping`
  and `star.created` test the acknowledge path and the quarantine path.
- `poll/events-page.json`: a recorded Events API page. It carries the trimmed
  pull request objects (`id`, `number`, `url`, `head`, `base`) and the
  `merged` / `created` action spellings the poller has to normalize.
  `pull-172.json`, `pull-177.json`, `repo.json` and `runs-page.json` are the
  objects the poller's hydration and Actions reads fetch. PR 140 has no file
  on purpose, so its hydration fails.
- `example.ttl`: hand-written example records for the shapes. The identifiers
  are synthetic.

Edits made to the recordings:

- Comment, review and PR bodies were replaced with short text that carries
  test mentions. One mention sits inside backticks and must not count.
- One review's state was set to `approved`.
- `*_url` link fields, avatars and reactions were dropped.

No headers carry a real signature. Tests sign with a fixed test secret. No
token or secret was recorded.

# Scheduled-work advisory benchmark

The `scheduled-work` slot of the existing `scripts/jev_bench.py` asks two atomic
Noul questions through the existing sole Jev client: whether the work proposes
an operational recurring/scheduled job, and whether its trigger is explicitly a
Chaski reaction on a Quipu event/deadline. Thresholds were frozen before scoring:
flag when the first probability is at least 0.8 and the second at most 0.2.
Intermediate results remain `uncertain`. This implementation has no refusal mode.

`labels.jsonl` contains eight sanitized operational paraphrases and 24 synthetic
controls. Labels were frozen by the task author before any response. They are
author agent-reference judgments: **zero human or independent labels**. The
operational records are paraphrases, not an untouched natural-board cohort.
Their original provenance and label-freeze record are retained in the private
operational evidence. These limits preclude a claim of calibrated production
accuracy or permission to refuse writes.

Only title and body enter the model state. Labels, ids, notes and provenance do
not. Exact request and dataset hashes bind replay to the frozen inputs and labels.
The model is pinned to `jev-1.14.0`, rather than a moving alias.

## One measured run

One separately admitted run on 2026-11-04 made 32 requests, with two atomic
questions per request, no retries, a USD 0.01 ceiling and stop-on-first-error.
All responses named the pinned model; none were unavailable. Existing usage
accounting independently recorded the same 32 calls and 14,068 input tokens.
The calculated input list price is USD 0.000590856; invoice debit is unverified.
The [official model documentation](https://docs.typesafe.ai/models), read that
day, lists USD 0.042 per million input tokens and free output tokens.

| Population | Samples | Flag decisions agreeing with author labels | Uncertain |
| --- | ---: | ---: | ---: |
| Operational paraphrases | 8 | 8 | 1 |
| Synthetic controls | 24 | 24 | 3 |

There were 13 proposed flags and no mismatched flag decisions. Atomic labels
also agreed at the separate 0.5 analysis threshold; that is descriptive analysis,
not the flag threshold. The [atomic analysis](run-20261104/atomic-analysis.json)
retains Brier scores. Do not pool controls with operational cases as reliability
calibration. Four uncertain rows remain explicit: case-04, case-25, case-28 and
case-31. In particular, a safe trigger at probability 0.78 is not called a
confident clear. The recommendation remains **flag-only**.

The [response archive](run-20261104/responses.jsonl) retains actual questions,
raw answers, pinned model and usage. Only the internal budget-reference field
was removed for publication; request/response content is unchanged. Every model
answer remains inferred/quarantined, not a trusted fact. The
[summary](run-20261104/summary.json) retains all rows and misses, including the
empty miss list, rather than selecting favorable examples.

## Preview and replay

Both commands below are keyless and make no model requests:

```sh
python3 scripts/jev_bench.py bench/scheduled-work/labels.jsonl --slot scheduled-work
python3 scripts/jev_bench.py bench/scheduled-work/labels.jsonl --slot scheduled-work \
  --responses bench/scheduled-work/run-20261104/responses.jsonl
```

Live collection requires a **new, separately authorized** run and a new output
path. The original run's authority is exhausted. The runner requires an exact
call ceiling, explicit dollar cap and recorded approval reference. It reserves
bounded input cost before sending, counts reported usage afterward, flushes every
response/failed attempt, never overwrites an output and never retries. Failed
or timed-out calls may be billed; unknown accounting is not reported as zero.
No live call runs in CI.

## Seed preflight boundary

The source MCP tool `check_scheduled_work(title, body)` returns probabilities,
the exact request and a proposed `needs-chaski` label. It does not write a
tracker, automatically alter an existing seed or promote model facts. The caller
must have an admitted inference budget. This source adds the advisory surface;
normal reviewed tool-manifest projection and actual seed create/update producer
adoption remain separate. Existing native writer authority and lifecycle checks
still apply. Refusal mode requires a separate owner ruling on measured evidence.

## Source demonstration

The [terminal recording](output.log) and its [timing](timing.log) show the same
hourly-cron and explicit Chaski-trigger inputs. Base
`088c403d7541eca1acad9a112aa8ab35d5410134` reports an unknown tool; the candidate
adapter returns a proposed flag for cron and a clear result for the trigger
control. Both retain inferred/quarantined status and `refuses_write: false`.
This is a source-checkout replay of the actual archived responses, with zero
network calls; it does not prove tracker adoption or an installed MCP surface.
The recording describes the adapter and dataset committed with this caption.

```sh
python3 bench/scheduled-work/demo.py
scriptreplay --log-out bench/scheduled-work/output.log \
  --log-timing bench/scheduled-work/timing.log
```

Playback was verified with util-linux `scriptreplay`; download both files for
terminal replay. The output file is also a readable transcript. Retain this
small recording beside the benchmark for the lifetime of this source change.

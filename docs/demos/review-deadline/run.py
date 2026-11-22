"""Real persisted-event semantics, deterministic clock, synthetic authority/spawn."""
from dataclasses import replace
from pathlib import Path
import sqlite3
import subprocess
import sys

root = Path(__file__).resolve().parents[3]
base = 'f712c17ca785f44766bcfdf28606f7375c129aa7'
before = subprocess.run(['git', 'cat-file', '-e', base + ':scripts/github_review_deadline.py'],
                        cwd=root, capture_output=True)
assert before.returncode != 0
print('BEFORE', base, 'has no persisted review-deadline adapter')
sys.path.insert(0, str(root / 'scripts'))
from github_recruitment import Candidate
from github_review_deadline import Deadlines, Readiness, Wake

print('AFTER: real SQLite/ledger; synthetic authenticated source, authority and launcher')
conn = sqlite3.connect(':memory:', isolation_level=None)
store = Deadlines(conn, repos=frozenset({'intigraph/widget'}))
now, jobs, reads = [100.], [], []
wake = Wake('intigraph/widget', 1, 'a' * 40, 'green-1', 'green')


def resolve(repo, pr, head):
    reads.append('resolver')
    return Readiness(Candidate(repo, pr, head, 'review', 'task-1'), 100., now[0], True, True)


def admit(candidate):
    reads.append('native-custody')


def fire():
    return store.fire(wake.repo, wake.pr, wake.head, resolve_ready=resolve, admit=admit,
                      spawn=lambda c: jobs.append(c) or 'fixture-receipt', clock=lambda: now[0])


print('green event:', store.on_event(wake, verify_event=lambda _: None,
                                    resolve_ready=resolve, clock=lambda: now[0]))
now[0] = 7299.
print('before two hours:', fire(), 'launcher calls:', len(jobs))
now[0] = 7300.
print('at two hours:', fire(), 'launcher calls:', len(jobs))
print('replay:', fire(), 'launcher calls:', len(jobs))
print('authority reads:', reads)
second = replace(wake, pr=2, delivery='green-2')
store.on_event(second, verify_event=lambda _: None, resolve_ready=resolve, clock=lambda: now[0])
print('review event:', store.on_event(replace(second, delivery='review-2', kind='review'),
    verify_event=lambda _: None, resolve_ready=resolve, clock=lambda: now[0]))
print('cancelled fire:', store.fire(second.repo, second.pr, second.head,
    resolve_ready=resolve, admit=admit, spawn=lambda c: jobs.append(c) or 'bad', clock=lambda: now[0]))
assert len(jobs) == 1
print('No HTTP, scheduler installation or actual job launch.')
conn.close()

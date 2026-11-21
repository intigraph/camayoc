"""Durable recruitment delivery/attempt ledger (aegis-cj5).

This module launches nothing by itself. A separate authenticated drain supplies
an authoritative resolver and the scoped st spawn transport. The webhook's
claimed status/verdict is never an admission decision. Attempts survive a lost
response and have no automatic retry/reset operation.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    repo: str
    pr: int
    head: str
    job: str
    item: str

    def validate(self):
        if (not re.fullmatch(r"intigraph/[A-Za-z0-9_.-]+", self.repo)
                or self.repo.split("/")[1] in (".", "..")
                or type(self.pr) is not int or self.pr < 1
                or not re.fullmatch(r"[0-9a-f]{40}", self.head)
                or self.job not in ("review", "merge")
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.item)):
            raise ValueError("invalid exact recruitment candidate")


SCHEMA = """
CREATE TABLE IF NOT EXISTS recruitment_attempts (
 repo TEXT NOT NULL, pr INTEGER NOT NULL, head TEXT NOT NULL, job TEXT NOT NULL,
 item TEXT NOT NULL, delivery TEXT NOT NULL, event_at REAL NOT NULL,
 attempted_at REAL NOT NULL, finished_at REAL, state TEXT NOT NULL,
 receipt TEXT, reason TEXT,
 PRIMARY KEY (repo,pr,head,job)
);
"""


class Attempts:
    """Use the private shared spool connection; caller serializes access.

    INSERT is committed before the transport is called. Concurrent drains use
    SQLite uniqueness rather than a check-then-act lock. An interrupted attempt
    remains 'attempted', which is terminal for automatic recruitment.
    """
    def __init__(self, conn: sqlite3.Connection):
        if conn.in_transaction:
            raise ValueError("attempt ledger needs an independent transaction boundary")
        self.conn = conn
        conn.executescript(SCHEMA)

    def reserve(self, candidate: Candidate, delivery: str, event_at: float, now: float) -> bool:
        candidate.validate()
        if self.conn.in_transaction:
            raise ValueError("reserve must commit before spawn")
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO recruitment_attempts "
            "(repo,pr,head,job,item,delivery,event_at,attempted_at,state) VALUES (?,?,?,?,?,?,?,?, 'attempted')",
            (candidate.repo, candidate.pr, candidate.head, candidate.job, candidate.item,
             delivery, event_at, now))
        self.conn.commit()
        return cur.rowcount == 1

    def finish(self, candidate: Candidate, state: str, *, receipt: str | None = None,
               reason: str | None = None, now: float):
        if state not in ("spawned", "refused", "unknown"):
            raise ValueError("invalid terminal recruitment state")
        if state == "spawned" and (not isinstance(receipt, str) or not receipt.strip()):
            raise ValueError("spawned requires a positive receipt")
        cur = self.conn.execute(
            "UPDATE recruitment_attempts SET state=?,receipt=?,reason=?,finished_at=? "
            "WHERE repo=? AND pr=? AND head=? AND job=? AND state='attempted'",
            (state, receipt, reason, now, candidate.repo, candidate.pr, candidate.head, candidate.job))
        self.conn.commit()
        if cur.rowcount != 1:
            raise ValueError("attempt absent or already terminal")


def attempt(ledger: Attempts, candidate: Candidate, *, delivery: str, event_at: float,
            admit, spawn, clock=time.time) -> str:
    """Admit against fresh authority, then spawn exactly once per candidate.

    `admit` rechecks head/checks/verdict/item/holds after reservation. It returns
    None on admission or a bounded reason on refusal. Both collaborators are
    supplied by the authenticated drain, never by payload command strings.
    Unknown or interrupted calls retain the key; another event cannot retry it.
    """
    if not ledger.reserve(candidate, delivery, event_at, clock()):
        return "duplicate"
    try:
        refusal = admit(candidate)
        if refusal is not None:
            ledger.finish(candidate, "refused", reason=str(refusal)[:500], now=clock())
            return "refused"
        receipt = spawn(candidate)
        if not isinstance(receipt, str) or not receipt.strip():
            raise ValueError("transport supplied no confirmed spawn receipt")
    except Exception as exc:
        # Do not retain transport output, credentials, or exception messages.
        ledger.finish(candidate, "unknown", reason=type(exc).__name__, now=clock())
        return "unknown"
    ledger.finish(candidate, "spawned", receipt=receipt, now=clock())
    return "spawned"

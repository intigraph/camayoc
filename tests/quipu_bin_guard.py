"""Where the quipu binaries are — and the rule that says when absence is allowed.

Three suites need a quipu binary: `test_rml_shapes.py` and `test_certify_pack.py`
want the `quipu` CLI, `test_metrics_slice.py` wants `quipu-server`. Before this
module each of them resolved the binary for itself, and the three rules did not
agree:

    test_metrics_slice.py   QUIPU_SERVER_BIN or PATH   plain skip when absent
    test_rml_shapes.py      QUIPU_BIN        or PATH   refuse when promised
    test_certify_pack.py    PATH only                  plain skip when absent

The third one is the reason this module exists. The `integration` job of
`.github/workflows/ci.yml` unpacks the release tarball into the workspace and
exports `QUIPU_BIN`; it never puts that directory on `PATH`. So the SHACL
certification gate — which the job had already downloaded the CLI for — looked
for a `quipu` on `PATH`, did not find one, and skipped, on every run, while the
job reported `OK (skipped=1)`. Measured 2026-09-05 as a control pair on
`test_certify_pack.py`: with a quipu on PATH, 0 skips and 8 tests pass; with
PATH stripped, exactly 1 skip, and it is the SHACL integration test. That 1 is
the whole of the `skipped=1` in the last healthy integration run.

A gate that cannot see the binary its own job fetched is the same green check
over an unrun suite that `rdflib_guard.py` and camayoc-045 both record. The fix
is not a third rule; it is one rule, here, used by all three.

THE RULE: an env var is a PROMISE by the job that set it — "I fetched this and
put it there."

* the binary is there              -> run
* promised, and it is NOT there    -> REFUSE. The job that set the variable did
                                      not deliver it. That is the job's fault,
                                      and a red test is how it says so.
* nothing promised                 -> skip, and say where the gate does run.

Keying on the promise rather than on `CI` is deliberate, and this repo paid for
the distinction. `rdflib_guard.py` keys on `CI` and is right to: the `test` job
`pip install`s rdflib, so a refusal there can only mean a real regression. The
same shape copied onto the quipu CLI asserted something that was never true —
that every CI job provides one. None did, so `test_rml_shapes.py` raised on
every run of every job, main was red from 2026-08-31 to 2026-09-02 with 360
tests passing behind one unconditional error, and the gate itself never executed
anywhere. A gate that cannot pass is not stricter than one that skips; it is a
gate nobody can read.

So the boundary is not "is this a machine?" but "did something claim to have put
a binary here?" — which is exactly the claim that can be wrong.
"""

from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path

#: The `quipu` CLI, and the `quipu-server` binary, or None if neither the
#: promise nor PATH produced one.
QUIPU = os.environ.get("QUIPU_BIN") or shutil.which("quipu")
QUIPU_SERVER = os.environ.get("QUIPU_SERVER_BIN") or shutil.which("quipu-server")
#: A `quipu` CLI new enough for `validate --format json` (0.16+). No PATH
#: fallback: an older CLI on PATH prints text, which would read as a broken test.
QUIPU_REPORT = os.environ.get("QUIPU_REPORT_BIN")

_WHERE = (
    "The {program} gates run in the `integration` job of "
    ".github/workflows/ci.yml, which fetches the binaries from the quipu "
    "release tarball and exports {env_var}. To run this suite here, put a "
    "`{program}` on PATH or set {env_var}."
)


def _guard(target, resolved, env_var, program, where=_WHERE):
    """Gate a suite OR a single test method.

    Applied to a class it gates every test in it; applied to one method it
    gates only that method. The distinction matters: `CertificationEvidenceTests`
    has six tests and only one of them shells out to the CLI, so gating the
    class would skip five tests that need nothing — trading a dark gate for a
    dark suite, which is the same defect pointing the other way. Measured while
    writing this module: decorating that class took the laptop skip count from
    1 to 6.
    """
    if resolved and Path(resolved).exists():
        return target
    if env_var in os.environ:
        promised = os.environ[env_var]
        name = getattr(target, "__name__", str(target))
        message = (
            f"{env_var} is set to {promised!r} but no {program} is there. "
            f"The job that set it did not deliver it; this is that job's "
            f"fault, not a missing optional dependency. (gated: {name})"
        )

        # Not a skip: something that FAILS, so the run goes red and the reason
        # is the failure message rather than a line nobody reads. Same choice,
        # for the same reason, as `rdflib_guard.requires_rdflib`.
        if isinstance(target, type):

            class PromiseBroken(unittest.TestCase):
                def test_the_promised_binary_must_be_there(self):
                    self.fail(message)

            PromiseBroken.__name__ = f"{name}_PromiseBroken"
            PromiseBroken.__qualname__ = PromiseBroken.__name__
            return PromiseBroken

        def promise_broken(self):
            self.fail(message)

        promise_broken.__name__ = name
        promise_broken.__qualname__ = getattr(target, "__qualname__", name)
        promise_broken.__doc__ = target.__doc__
        return promise_broken
    return unittest.skip(where.format(program=program, env_var=env_var))(target)


def requires_quipu(target):
    """Gate a suite or test on the `quipu` CLI, honouring the QUIPU_BIN promise."""
    return _guard(target, QUIPU, "QUIPU_BIN", "quipu")


def requires_quipu_server(target):
    """Gate a suite or test on `quipu-server`, honouring QUIPU_SERVER_BIN."""
    return _guard(target, QUIPU_SERVER, "QUIPU_SERVER_BIN", "quipu-server")


def requires_quipu_report(target):
    """Gate on a JSON-reporting `quipu` CLI, honouring the QUIPU_REPORT_BIN promise."""
    return _guard(target, QUIPU_REPORT, "QUIPU_REPORT_BIN", "quipu",
                  where="The JSON report gates run in the `namespace-reports` job of "
                        ".github/workflows/ci.yml, which exports {env_var}. To run them "
                        "here, set {env_var} to a {program} 0.16 or newer.")

"""The schema proposal bodies stay generated from shapes/agent-memory.shapes.ttl."""
import pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_proposal_bodies_are_current():
    run = subprocess.run([sys.executable, "scripts/memory_schema_proposal.py", "--check"],
                         cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr

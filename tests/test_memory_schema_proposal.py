"""The schema proposal bodies stay generated from shapes/agent-memory.shapes.ttl,
and every property the shapes use is sanctioned on the target store."""
import importlib.util, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/memory_schema_proposal.py"
spec = importlib.util.spec_from_file_location("memory_schema_proposal", SCRIPT)
msp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(msp)


def test_proposal_bodies_are_current():
    run = subprocess.run([sys.executable, str(SCRIPT), "--check"],
                         cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr


def test_every_shape_property_is_declared_or_already_live():
    # A property in neither list would be refused by quipu's vocabulary gate
    # in production (the camayoc#185 supersededAt gap).
    assert msp.vocabulary_gaps(msp.SHAPES.read_text()) == []


def test_01_does_not_redeclare_live_properties():
    assert not set(msp.PROPS) & msp.ALREADY_SANCTIONED_LIVE


def test_a_new_shape_property_is_caught():
    text = msp.SHAPES.read_text() + "\naegis:X a sh:NodeShape ; sh:property [ sh:path aegis:brandNew ] .\n"
    assert msp.vocabulary_gaps(text) == ["brandNew"]

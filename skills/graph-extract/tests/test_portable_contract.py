import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "outcome-is-write-verdict",
    "reader-path-readback",
    "indeterminate-transport",
    "stable-node-identity",
    "corrections-use-set",
    "standard-predicates-use-knot",
    "every-node-is-typed",
}


def test_portable_contract_is_complete_and_referenced():
    contract = json.loads(
        (ROOT / "references/portable-safety-contract.json").read_text()
    )
    ids = [item["id"] for item in contract["invariants"]]
    assert contract["schema_version"] == 1
    assert len(ids) == len(set(ids))
    assert set(ids) == REQUIRED
    assert all(item["requirement"].strip() for item in contract["invariants"])
    assert "portable-safety-contract.json" in (ROOT / "SKILL.md").read_text()


def test_count_is_not_a_success_gate():
    contract = (ROOT / "references/portable-safety-contract.json").read_text()
    assert "count is transaction write volume, not a presence or success gate" in contract

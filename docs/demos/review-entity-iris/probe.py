"""Same review-age fixture on a base or candidate checkout; no live requests."""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

from rdflib import Graph, Literal, URIRef

source = Path(sys.argv[1])
sys.path.insert(0, str(source / "scripts"))
import change_adapter as ca  # noqa: E402
import planes  # noqa: E402
import review_due as rd  # noqa: E402

now = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)
graph = Graph()
entities = ["https://example.test/" + kind for kind in ("decision", "directive", "count-claim")]
for entity in entities + [rd.A + "legacy-control"]:
    graph.add((URIRef(entity), URIRef(rd.A + "reviewAfter"), Literal("2026-10-19T00:00:00Z")))
    graph.add((URIRef(entity), URIRef(rd.A + "ownedBy"), URIRef(rd.A + "review-owner")))
graph.add((URIRef("https://example.test/no-age"), URIRef(rd.A + "ownedBy"), URIRef(rd.A + "review-owner")))


def post(endpoint, body):
    assert endpoint == "/query"
    rows = []
    if body.get("graph") == planes.PLANES["crew:declared"]["iri"]:
        rows = [{str(k): str(v) for k, v in row.asdict().items()} for row in graph.query(body["query"])]
    return {"rows": rows, "truncated": False}


records = rd.evaluate(post, now=now)
route = ca.evaluate("review", post, {"now": now.timestamp(), "route": [
    {"entity": entities[0], "attribute": rd.A + "reviewAfter",
     "graph": planes.PLANES["crew:declared"]["iri"]}]})
result = {
    "python_version": sys.version.split()[0],
    "source_digest": hashlib.sha256(b"".join((source / "scripts" / p).read_bytes()
                                             for p in ("review_due.py", "change_adapter.py"))).hexdigest(),
    "due_to_owner": sum(r["entity"] in entities and r["verdict"] == "DUE" and
                        r["owner"] == "review-owner" for r in records),
    "expected_due": 3,
    "matching_change_routed": route["items"] == [entities[0]],
    "legacy_positive_control": any(r["entity"] == "legacy-control" and r["verdict"] == "DUE" for r in records),
    "no_age_control": all("no-age" not in r["entity"] for r in records),
    "live_requests": 0,
}
print(json.dumps(result, indent=2))
assert result["legacy_positive_control"] and result["no_age_control"]
if "--require-fixed" in sys.argv:
    assert result["due_to_owner"] == 3 and result["matching_change_routed"]

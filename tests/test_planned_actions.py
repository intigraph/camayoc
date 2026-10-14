import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import planned_actions as reader
from rdflib_guard import HAVE_RDFLIB, requires_rdflib
if HAVE_RDFLIB:
    import rdflib
    from unittest.mock import patch


def row(**changes):
    return {"item": "https://seeds.test/item/plan", "id": "plan", "status": "deferred",
            "title": "Role redesign: keeper routes work; lead does review/design",
            "description": "A stronger model for review work.", "owner": "https://seeds.test/principal/wu",
            "modified": "2026-10-08T23:40:47.015686843Z", "label": "stiwi-directive", "type": "https://schema.org/Action", **changes}


class NativePlannedReader(unittest.TestCase):
    def test_reader_question_returns_deferred_with_exact_topic_only(self):
        calls = []
        def post(path, body):
            calls.append((path, body))
            if len(calls) == 1:
                return {"rows": [{"item": "positive"}]}
            if path == "/ask":
                return {"rows": [row(), row(label="design")]}
            self.assertNotIn("observedStatus", body["query"])
            return {"rows": [{"id": "plan", "about": "aegis:lead"}]}
        answer = reader.read("review role for verification", "https://seeds.test/board",
                             "https://camayoc.test/records", post, ["plan"])
        item, = answer["items"]
        self.assertEqual(item["status"], "deferred")
        self.assertEqual(item["kind"], "Directive")
        self.assertEqual(item["matched_terms"], ["review", "role"])
        self.assertEqual(item["about"], [reader.A + "lead"])
        self.assertEqual(len(calls), 3)

    def test_invalid_closed_unknown_conflict_and_overflow_refuse(self):
        for rows in [[row(status="unknown-new-status")], [row(label="task")],
                     [row(), row(status="open")], [row(owner="not-an-iri")],
                     [row(modified="tomorrow")], [row(type=None)], [row(description=[])],
                     [row(item=f"https://seeds.test/item/{i}", id=str(i)) for i in range(26)]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                reader.candidates({"rows": rows}, ["review"])
        for payload in [{"rows": None}, {"rows": [row()], "truncated": True},
                        {"rows": [row()] * 101}]:
            with self.assertRaises(ValueError):
                reader.candidates(payload, ["review"])

    def test_native_failure_and_unproven_control_never_reads_legacy(self):
        calls = []
        def post(path, body):
            calls.append(path)
            return {"rows": []}
        with self.assertRaises(ValueError):
            reader.read("role", "https://seeds.test/board", "https://camayoc.test/records", post, ["plan"])
        self.assertEqual(calls, ["/query"])

    def test_empty_native_result_after_control_is_bounded_zero(self):
        calls = []
        def post(path, body):
            calls.append(path)
            return {"rows": [{"item": "positive"}]} if len(calls) == 1 else {"rows": []}
        result = reader.read("role", "https://seeds.test/board", "https://camayoc.test/records", post, ["plan"])
        self.assertEqual(result["items"], [])
        self.assertEqual(calls, ["/query", "/ask"])

    def test_no_empty_or_silently_truncated_question(self):
        for question in ["", "the and for", "one two three four", "x" * 257]:
            with self.assertRaises(ValueError):
                reader.terms(question)

    def test_invalid_and_duplicate_id_budget_refuses_before_native_lookup(self):
        for ids in [[], ["plan", "plan"], ["bad\"id"], list(map(str, range(26)))]:
            with self.assertRaises(ValueError):
                reader.read("role", "https://seeds.test/board", "https://camayoc.test/records",
                            lambda *args: {"rows": [{"item": "positive"}]}, ids)

    def test_typed_nanosecond_timestamp_retained_and_invalid_calendar_refuses(self):
        item, = reader.candidates({"rows": [row(modified={"datatype": "xsd:dateTime",
                                         "value": row()["modified"]})]}, ["review"])
        self.assertEqual(item["modified"], row()["modified"])
        with self.assertRaises(ValueError):
            reader.candidates({"rows": [row(modified="2026-02-30T00:00:00Z")]}, ["review"])

    def test_injected_graph_and_unscoped_enrichment_refuse(self):
        with self.assertRaises(ValueError):
            reader.iri('https://example/> ?s ?p ?o')
        calls = []
        def post(path, body):
            calls.append(path)
            return {"rows": ([{"item": "positive"}] if len(calls) == 1 else
                             [row()] if path == "/ask" else
                             [{"id": "other", "about": reader.A + "lead"}])}
        with self.assertRaises(ValueError):
            reader.read("role", "https://seeds.test/board", "https://camayoc.test/records", post, ["plan"])


@requires_rdflib
class NativeStoredQuery(unittest.TestCase):
    def test_actual_query_native_status_labels_and_exact_id(self):
        ds = rdflib.Dataset()
        board = ds.graph(rdflib.URIRef("https://seeds.test/board"))
        item = rdflib.URIRef("https://seeds.test/item/plan")
        schema = rdflib.Namespace("https://schema.org/")
        seeds = rdflib.Namespace("https://seeds.local/ontology/")
        for predicate, value in [(rdflib.RDF.type, schema.Action),
                                 (schema.identifier, rdflib.Literal("plan")),
                                 (schema.name, rdflib.Literal("Review role")),
                                 (schema.dateModified, rdflib.Literal(row()["modified"])),
                                 (schema.keywords, rdflib.Literal("design")),
                                 (seeds.status, rdflib.Literal("deferred"))]:
            board.add((item, predicate, value))
        template = json.loads((ROOT / "queries/camayoc_planned_actions.json").read_text())["template"]
        def run(identifier="plan"):
            query = template.replace("{board}", "https://seeds.test/board").replace("{identifier}", identifier)
            with patch("rdflib.plugins.sparql.SPARQL_LOAD_GRAPHS", False):
                return list(ds.query(query))
        result, = run()
        self.assertEqual(str(result.status), "deferred")
        self.assertEqual(run("other"), [])
        board.add((item, seeds.status, rdflib.Literal("closed")))
        conflicting = run()
        self.assertEqual({str(r.status) for r in conflicting}, {"deferred", "closed"})
        actual_rows = [{str(k): str(v) for k, v in r.asdict().items()} for r in conflicting]
        with self.assertRaises(ValueError):
            reader.candidates({"rows": actual_rows}, ["review"])
        board.set((item, seeds.status, rdflib.Literal("closed")))
        closed, = run()
        self.assertEqual(str(closed.status), "closed")
        self.assertEqual(reader.candidates({"rows": [row(status="closed")]}, ["review"]), [])
        board.set((item, seeds.status, rdflib.Literal("unknown-new-status")))
        unknown, = run()
        self.assertEqual(str(unknown.status), "unknown-new-status")
        with self.assertRaises(ValueError):
            reader.candidates({"rows": [{str(k): str(v) for k, v in unknown.asdict().items()}]}, ["review"])
        board.add((item, seeds.status, rdflib.Literal("open")))
        open_unknown = run()
        self.assertEqual({str(r.status) for r in open_unknown}, {"open", "unknown-new-status"})
        with self.assertRaises(ValueError):
            reader.candidates({"rows": [{str(k): str(v) for k, v in r.asdict().items()}
                                        for r in open_unknown]}, ["review"])
        board.set((item, seeds.status, rdflib.Literal("open")))
        for predicate in [schema.name, schema.dateModified, seeds.status, rdflib.RDF.type]:
            originals = list(board.triples((item, predicate, None)))
            board.remove((item, predicate, None))
            missing, = run()
            missing_rows = [{str(k): str(v) for k, v in missing.asdict().items()}]
            with self.subTest(predicate=predicate), self.assertRaises(ValueError):
                reader.candidates({"rows": missing_rows}, ["review"])
            for triple in originals:
                board.add(triple)
        board.set((item, schema.keywords, rdflib.Literal("ordinary")))
        self.assertEqual(run(), [])


if __name__ == "__main__":
    unittest.main()

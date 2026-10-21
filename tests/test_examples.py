"""The example query library runs green, and ships in a qpack (quipu aegis-fxpbys.2).

Three layers, each able to fail on its own:

* the LIBRARY is coherent — every query is a well-formed stored-query
  definition, every setup names files and queries that exist, and every query
  is reachable from a setup and exercised by at least one case;
* every CASE runs against `examples/fixtures/examples.ttl` with rdflib and
  returns exactly the rows `examples/cases.json` names, in order. A "find the
  bad ones" query that returned every row would fail here, because each case
  names a strict subset of its query's candidate rows;
* the DOGFOOD — `scripts/examples_qpack.py` — ships the library in a real
  `quipu share`, imports it into a fresh store and asks every case back. It
  needs a quipu CLI and server, and a quipu that writes `queries.ttl`; one that
  predates it is SKIPPED BY NAME, never passed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from quipu_bin_guard import QUIPU, QUIPU_SERVER, requires_quipu, requires_quipu_server
from rdflib_guard import HAVE_RDFLIB, requires_rdflib

if HAVE_RDFLIB:
    import rdflib

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
FIXTURE = EXAMPLES / "fixtures" / "examples.ttl"


def definitions() -> dict[str, dict]:
    return {
        path.stem: json.loads(path.read_text())
        for path in sorted((EXAMPLES / "queries").glob("*.json"))
    }


def setups() -> dict:
    return json.loads((EXAMPLES / "setups.json").read_text())


def cases() -> list[dict]:
    return json.loads((EXAMPLES / "cases.json").read_text())


class ExampleLibraryTests(unittest.TestCase):
    def test_every_definition_is_a_loadable_stored_query(self):
        for stem, definition in definitions().items():
            with self.subTest(query=stem):
                self.assertEqual("load", definition["action"])
                self.assertEqual(stem, definition["name"])
                self.assertTrue(definition["name"].startswith("camayoc_ex_"))
                self.assertTrue(definition["description"])
                head = definition["template"].split("WHERE")[0].upper()
                self.assertRegex(head, r"\b(SELECT|CONSTRUCT|ASK|DESCRIBE)\b")
                for param in definition["params"]:
                    self.assertEqual({"name", "type", "required", "description"}, set(param))
                    self.assertIn(param["type"], {"iri", "text"})
                declared = {p["name"] for p in definition["params"]}
                used = set(re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}", definition["template"]))
                self.assertEqual(declared, used)

    def test_setups_name_real_files_and_real_queries(self):
        known = set(definitions())
        reached = set()
        for name, setup in setups().items():
            with self.subTest(setup=name):
                self.assertTrue(setup["description"])
                self.assertTrue(setup["shapes"], "a setup that installs no shapes governs nothing")
                for rel in setup["shapes"] + setup["ontology"]:
                    self.assertTrue((ROOT / rel).is_file(), rel)
                self.assertTrue(set(setup["queries"]) <= known, set(setup["queries"]) - known)
                reached |= set(setup["queries"])
        self.assertEqual(known, reached, "every example belongs to a setup")
        self.assertEqual({"work-ledger", "code-graph", "directive-graph"}, set(setups()))

    def test_every_query_has_a_case_and_every_case_a_query(self):
        exercised = {case["query"] for case in cases()}
        self.assertEqual(set(definitions()), exercised)
        for case in cases():
            with self.subTest(case=case["query"]):
                declared = {p["name"] for p in definitions()[case["query"]]["params"]}
                self.assertEqual(declared, set(case["params"]))
                self.assertTrue(case["expect"], "an empty expectation cannot fail")


def run(graph, definition: dict, params: dict) -> list[str]:
    template = definition["template"]
    for key, value in params.items():
        template = template.replace("{" + key + "}", value)
    return [str(row[0]) for row in graph.query(template)]


@requires_rdflib
class ExampleCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = rdflib.Graph()
        cls.graph.parse(FIXTURE)

    def test_every_case_returns_exactly_its_expected_rows(self):
        for case in cases():
            with self.subTest(query=case["query"], params=case["params"]):
                rows = run(self.graph, definitions()[case["query"]], case["params"])
                self.assertEqual(case["expect"], rows)

    def test_the_negative_arms_are_real(self):
        """Each filter query must EXCLUDE something the fixture holds."""
        work = {str(s) for s in self.graph.subjects(
            rdflib.RDF.type, rdflib.URIRef("https://schema.org/Action"))}
        ready = set(run(self.graph, definitions()["camayoc_ex_ready_work"], {}))
        self.assertTrue(ready and ready < work, "ready work must be a strict subset")
        directives = {str(s) for s in self.graph.subjects(
            rdflib.RDF.type, rdflib.URIRef("http://aegis.gastown.local/ontology/Directive"))}
        active = set(run(self.graph, definitions()["camayoc_ex_active_directives"], {}))
        self.assertTrue(active and active < directives)
        labelled = {str(s) for s in self.graph.subjects(rdflib.RDFS.label, None)}
        orphans = set(run(self.graph, definitions()["camayoc_ex_orphaned_entities"], {}))
        self.assertEqual(1, len(orphans))
        self.assertTrue(orphans < labelled)


@requires_quipu
@requires_quipu_server
class ExamplesShipInAQpackTests(unittest.TestCase):
    def test_share_import_ask_returns_the_producers_answers(self):
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "examples_qpack.py"),
             "--quipu", QUIPU, "--quipu-server", QUIPU_SERVER, "--json"],
            capture_output=True, text=True, timeout=600,
        )
        if done.returncode == 3:
            self.skipTest(
                "this quipu predates qpack stored queries (queries.ttl, quipu "
                "aegis-fxpbys.2); the dogfood runs once the pinned quipu carries it"
            )
        self.assertEqual(0, done.returncode, done.stderr + done.stdout)
        report = json.loads(done.stdout)
        self.assertEqual(len(definitions()), report["queries"])
        self.assertEqual(len(cases()), len(report["cases"]))
        self.assertTrue(all(case["ok"] for case in report["cases"]), report)
        self.assertTrue(report["queries_hash"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()

"""Repository file metadata must not constrain every shared Document."""
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "@prefix a: <http://aegis.gastown.local/ontology/> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"


def validation_report(output):
    """Read the shared 0.14/0.15 text contract; unknown output is an error."""
    lines = output.strip().splitlines()
    if lines == ["valid (0 warnings)"]:
        return {"conforms": True, "violations": 0, "warnings": 0, "results": []}
    header = re.fullmatch(r"invalid: (\d+) violation\(s\), (\d+) warning\(s\)", lines[0] if lines else "")
    if header is None:
        raise ValueError(f"unrecognized validation report: {output!r}")
    rows = []
    for line in lines[1:]:
        row = re.fullmatch(r"  \[\s*(Violation|Warning)\] (.+) -- (.+) \(path: ([^)]+)\)", line)
        if row is None:
            raise ValueError(f"unrecognized validation result: {line!r}")
        rows.append({"severity": row[1], "path": row[4]})
    violations, warnings = map(int, header.groups())
    if (sum(row["severity"] == "Violation" for row in rows),
            sum(row["severity"] == "Warning" for row in rows)) != (violations, warnings):
        raise ValueError("validation counts do not match result rows")
    return {"conforms": False, "violations": violations, "warnings": warnings, "results": rows}


class ValidationReportContract(unittest.TestCase):
    def test_unknown_or_incomplete_report_is_not_success(self):
        for output in ("", "validation error: parse failed", "invalid: 1 violation(s), 0 warning(s)\n",
                       "valid (1 warnings)", "valid (0 warnings)\nextra"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                validation_report(output)

    def test_warning_and_violation_counts_preserve_paths(self):
        for severity in ("Warning", "Violation"):
            violations = int(severity == "Violation")
            warnings = int(severity == "Warning")
            report = validation_report(f"invalid: {violations} violation(s), {warnings} warning(s)\n"
                                       f"  [{severity:>9}] urn:doc -- constraint failed (path: urn:filePath)\n")
            self.assertEqual(report["results"], [{"severity": severity, "path": "urn:filePath"}])
            self.assertEqual((report["violations"], report["warnings"]), (violations, warnings))


@requires_quipu
class CodeDocumentScope(unittest.TestCase):
    def validate(self, body, policy="reject"):
        source = (ROOT / "shapes/code-entities.ttl").read_text()
        prefix = "\n".join(line for line in source.splitlines() if line.startswith("@prefix")) + "\n"
        blocks = re.split(r"(?m)(?=^bobbin:\w+Shape a sh:NodeShape ;)", source)[1:]
        emit = [block for block in blocks if 'quipu:onViolation "emit"' in block]
        self.assertEqual(len(emit), 4)
        selected = emit if policy == "emit" else [block for block in blocks if block not in emit]

        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data.ttl"
            data.write_text(PREFIX + body)
            shapes = Path(directory) / "shapes.ttl"
            shapes.write_text(prefix + "\n".join(selected))
            result = subprocess.run([str(QUIPU), "validate", "--shapes",
                                     str(shapes),
                                     "--data", str(data)],
                                    capture_output=True, text=True)
            self.assertIn(result.returncode, (0, 1), result.stderr)
            return validation_report(result.stdout)

    def test_ordinary_document_is_not_repository_metadata(self):
        self.assertTrue(self.validate('a:doc a a:Document ; rdfs:label "write-up" .')["conforms"])

    def test_membership_or_derivation_does_not_require_document_filepath(self):
        for relation in ("member_of", "derived_from"):
            with self.subTest(relation=relation):
                data = 'a:doc a a:Document ; rdfs:label "write-up" . a:rule a:' + relation + ' a:doc .'
                self.assertTrue(self.validate(data)["conforms"])

    def test_document_subclass_without_filepath_is_untouched(self):
        for kind in ("DesignDoc", "DecisionRecord"):
            with self.subTest(kind=kind):
                data = f'a:{kind} <http://www.w3.org/2000/01/rdf-schema#subClassOf> a:Document . a:doc a a:{kind} ; rdfs:label "write-up" .'
                self.assertTrue(self.validate(data)["conforms"])

    def test_path_bearing_non_document_without_ownership_is_untouched(self):
        result = self.validate('a:entry a:filePath 42 ; rdfs:label "entry" .')
        self.assertTrue(result["conforms"])

    def test_only_declared_debt_counts_are_routed_out_of_reject(self):
        cases = [
            ('a:doc a a:Document ; rdfs:label "first", "second" ; a:filePath "README.md" .', "label"),
            ('a:section a a:Section ; rdfs:label "heading" ; a:heading "first", "second" ; a:headingDepth 1 .', "heading"),
            ('a:commit a a:Commit ; rdfs:label "commit" ; a:author "author" ; rdfs:comment "message" .', "sha"),
            ('a:module a a:CodeModule ; rdfs:label "module" ; a:repo "repo" ; a:language "rust" .', "filePath"),
        ]
        for data, path in cases:
            with self.subTest(path=path):
                result = self.validate(('a:span a:inDocument a:doc . ' if 'a:doc ' in data else '') + data)
                self.assertEqual(result["violations"], 0, result)
                self.assertTrue(result["conforms"], result)
                debt = self.validate(('a:span a:inDocument a:doc . ' if 'a:doc ' in data else '') + data, policy="emit")
                # Inline property Warning is explicit, not inherited.
                self.assertEqual(debt["violations"], 0, debt)
                self.assertGreater(debt["warnings"], 0)
                self.assertTrue(any(row["path"].endswith(path) for row in debt["results"]))

    def test_other_constraints_still_reject(self):
        cases = [
            'a:doc a a:Document ; a:filePath "README.md" .',
            'a:doc a a:Document ; rdfs:label 42 ; a:filePath "README.md" .',
            'a:module a a:CodeModule ; rdfs:label "module" ; a:filePath 42 ; a:repo "repo" ; a:language "rust" .',
            'a:module a a:CodeModule ; rdfs:label "module" ; a:repo "repo" .',
            'a:commit a a:Commit ; rdfs:label "commit" ; a:author "author" ; rdfs:comment "message" ; a:sha 42 ; a:in_repo "repo" .',
            'a:section a a:Section ; rdfs:label "heading" ; a:heading 42 ; a:headingDepth 1 .',
        ]
        for data in cases:
            with self.subTest(data=data):
                self.assertGreater(self.validate(('a:span a:inDocument a:doc . ' if 'a:doc ' in data else '') + data)["violations"], 0)

    def test_valid_repository_document_conforms(self):
        self.assertTrue(self.validate('a:span a:inDocument a:doc . a:doc a a:Document ; rdfs:label "README" ; a:filePath "README.md" .')["conforms"])

    def test_repository_filepath_datatype_is_still_checked(self):
        result = self.validate('a:span a:inDocument a:doc . a:doc a a:Document ; rdfs:label "README" ; a:filePath 42 .')
        self.assertFalse(result["conforms"])
        self.assertTrue(any(row["path"].endswith("filePath") for row in result["results"]))

    def test_repository_file_without_document_type_still_requires_label(self):
        result = self.validate('a:span a:inDocument a:doc . a:doc a:filePath "README.md" .')
        self.assertFalse(result["conforms"])
        self.assertTrue(any(row["path"].endswith("label") for row in result["results"]))

    def test_two_repository_paths_still_refused(self):
        self.assertFalse(self.validate('a:span a:inDocument a:doc . a:doc a a:Document ; rdfs:label "README" ; a:filePath "a.md", "b.md" .')["conforms"])


if __name__ == "__main__":
    unittest.main()

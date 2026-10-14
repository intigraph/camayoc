"""Retired keys must clear old verdicts without poisoning live evaluations."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import change_adapter as adapter
from test_directive_edges import NOW, board


class RetiredScope(unittest.TestCase):
    def test_mixed_requested_scope_preserves_retired_removal(self):
        for kind in ("directive", "directive-lapse"):
            with self.subTest(kind=kind):
                result = adapter._directive(
                    kind, board(), {"items": ["retired", "old"]}, NOW, NOW.timestamp())
                self.assertEqual(set(result["scope"]), {"retired", "old"})
                self.assertEqual([r["entity"] for r in result["records"]], ["old"])
                self.assertEqual(set(result["next_checks"]), {"old"})
                self.assertEqual(result["records"][0]["verdict"],
                                 "TRACED" if kind == "directive" else "NOT_LAPSED")

    def test_retired_only_scope_is_explicit_empty_replacement(self):
        for kind in ("directive", "directive-lapse"):
            result = adapter._directive(
                kind, board(), {"items": ["retired"]}, NOW, NOW.timestamp())
            self.assertEqual(result["scope"], ["retired"])
            self.assertEqual(result["records"], [])
            self.assertEqual(result["next_checks"], {})

    def test_native_query_error_is_not_a_retirement(self):
        def unavailable(*args):
            raise ConnectionError("cannot establish current type")
        for kind in ("directive", "directive-lapse"):
            with self.assertRaises(ConnectionError):
                adapter._directive(kind, unavailable, {"items": ["old"]}, NOW, NOW.timestamp())


if __name__ == "__main__":
    unittest.main()

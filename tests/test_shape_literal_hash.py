"""No '#' inside a string literal in the shape sets bootstrap loads together.

quipu splits the COMBINED loaded shapes into reject and emit documents by
reading the text. Released quipus before scbrown/quipu#726 take a '#' inside a
string literal for the start of a comment, lose the end of that shape, and
route the shapes after it into emit, where they stop refusing. Measured on
quipu 0.14.1 with an emit set loaded beside camayoc-core: a Blocker with no
evidence kind was ACCEPTED (aegis-g51c C10). Write the character as the Turtle
escape \\u0023 instead; the parsed value is identical.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOADED_TOGETHER = ["shapes/core.shapes.ttl", "shapes/observation-debt.shapes.ttl"]
LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')


def literal_hashes(text):
    """(line, literal) for every quoted literal containing '#'. IRIs and whole
    comment lines are skipped; anything else over-counts, which is safe."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for match in LITERAL.finditer(re.sub(r"<[^>]*>", "<>", line)):
            if "#" in match.group(1):
                found.append((number, match.group(0)))
    return found


class ShapeLiteralHashTests(unittest.TestCase):
    def test_no_literal_hash_in_sets_loaded_with_an_emit_set(self):
        for name in LOADED_TOGETHER:
            with self.subTest(name):
                self.assertEqual(literal_hashes((ROOT / name).read_text()), [])

    def test_the_scanner_finds_what_it_must(self):
        # Without this arm a scanner that finds nothing would pass the test above.
        self.assertEqual(len(literal_hashes('x sh:pattern "a#b" .')), 1)
        self.assertEqual(literal_hashes('x sh:pattern "a\\u0023b" .'), [])
        self.assertEqual(literal_hashes("x sh:path <http://e.org/a#b> ."), [])
        self.assertEqual(literal_hashes('# a "comment#" line'), [])


if __name__ == "__main__":
    unittest.main()

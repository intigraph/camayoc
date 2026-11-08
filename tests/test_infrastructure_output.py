"""Optional source output contract against the native SHACL engine.

These fixture probes do not load shapes, run connectors or write a store.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profiles" / "infrastructure-output"
EXAMPLE = (PROFILE / "example.ttl").read_text()


@requires_quipu
class InfrastructureOutput(unittest.TestCase):
    def conforms(self, body: str) -> bool:
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data.ttl"
            data.write_text(body)
            result = subprocess.run(
                [str(QUIPU), "validate", "--shapes", str(PROFILE / "shapes.ttl"),
                 "--data", str(data)], capture_output=True, text=True, check=False,
            )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            self.assertTrue(result.stdout.lstrip().startswith("valid"), output)
            return True
        self.assertIn("violation", output.lower(), output)
        return False

    def test_complete_control(self):
        self.assertTrue(self.conforms(EXAMPLE))

    def test_cli_rejects_reference_statements_that_native_targeting_does_not(self):
        for extra in [
            '',
            '\nex:host <https://example.org/unapproved> "fixture-only" .',
            '\n<https://example.org/repository/inventory.yaml> '
            '<https://example.org/unapproved> "fixture-only" .',
        ]:
            with self.subTest(extra=extra):
                body = EXAMPLE + extra
                self.assertTrue(self.conforms(body))
                with tempfile.TemporaryDirectory() as directory:
                    data = Path(directory) / "data.ttl"
                    data.write_text(body)
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "scripts/admit_infrastructure_output.py"),
                         str(data), str(PROFILE / "example.manifest.json"),
                         "--quipu", str(Path(QUIPU).resolve())],
                        capture_output=True, text=True, timeout=40, check=False,
                    )
                self.assertEqual(2 if extra else 0, result.returncode,
                                 result.stdout + result.stderr)
                self.assertIn('"REFUSED"' if extra else '"ADMITTED_OFFLINE"', result.stdout)

    def test_guest_without_host_refuses_even_when_also_typed_host(self):
        missing = EXAMPLE.replace(" ; core:runs_on ex:host", "")
        self.assertFalse(self.conforms(missing))
        self.assertFalse(self.conforms(missing.replace(
            "a core:LXCContainer", "a core:LXCContainer, core:Host")))

    def test_wrong_host_kind_refuses(self):
        self.assertFalse(self.conforms(EXAMPLE.replace(
            "ex:host a core:ProxmoxNode", "ex:host a core:Service")))

    def test_missing_provenance_and_ambiguous_identity_refuse(self):
        for before, after in [
            (" ; prov:wasGeneratedBy ex:extraction", ""),
            ('schema:identifier "cluster-a/lxc/101"',
             'schema:identifier "cluster-a/lxc/101", "cluster-b/lxc/101"'),
            ('core:sourceKind "declared"', 'core:sourceKind "inferred"'),
            ('dct:identifier "extractor-0123456789abcdef"',
             'dct:identifier ""'),
        ]:
            with self.subTest(field=before):
                self.assertFalse(self.conforms(EXAMPLE.replace(before, after)))

    def test_unexpected_fields_refuse_on_every_record_layer(self):
        for subject in ["record", "guest", "source", "extraction"]:
            with self.subTest(subject=subject):
                extra = f'\nex:{subject} <https://example.org/secret> "fixture-only" .\n'
                self.assertFalse(self.conforms(EXAMPLE + extra))

    def test_route_needs_service_and_control_passes(self):
        route = EXAMPLE.replace("a core:LXCContainer", "a infra:ReverseProxyRoute")
        route = route.replace(" ; infra:ctId 101 ; core:runs_on ex:host", "")
        self.assertFalse(self.conforms(route))
        route = route.replace('rdfs:label "ct-101"',
                              'rdfs:label "route" ; core:routes_to ex:service')
        self.assertTrue(self.conforms(route + "\nex:service a core:Service .\n"))
        self.assertFalse(self.conforms(route + "\nex:service a core:Host .\n"))

    def test_marker_removal_is_outside_shacl_scope_not_admission(self):
        # Demonstrate this limit; adapter batch admission must reject it.
        unmarked = EXAMPLE.replace(" ; dct:conformsTo infra:ConnectorRecordShape", "")
        self.assertTrue(self.conforms(unmarked))


if __name__ == "__main__":
    unittest.main()

"""Exact batch and IP parsing controls, separate from native SHACL checks."""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

from rdflib import Graph

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "infrastructure_admission", ROOT / "scripts/admit_infrastructure_output.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PROFILE = ROOT / "profiles/infrastructure-output"
DATA = (PROFILE / "example.ttl").read_text()
EXPECTED = json.loads((PROFILE / "example.manifest.json").read_text())
ADDRESS = '''
@prefix crtv: <http://open-services.net/ns/crtv#> .
ex:guest crtv:ipAddress ex:address .
ex:address a crtv:IPAddress ; crtv:address "%s" ;
    crtv:contextAddressSpace ex:network .
'''


class InfrastructureAdmission(unittest.TestCase):
    def admit(self, body=DATA, expected=None):
        return MODULE.admit(Graph().parse(data=body, format="turtle"),
                            EXPECTED if expected is None else expected)

    def test_complete_control(self):
        self.assertEqual(1, self.admit())

    def test_omitted_marker_or_extra_subject_refuses(self):
        for body in [
            DATA.replace(" ; dct:conformsTo infra:ConnectorRecordShape", ""),
            DATA + '\nex:unclaimed a core:Host .\n',
            DATA + '\nex:extra dct:conformsTo infra:ConnectorRecordShape .\n',
        ]:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    self.admit(body)

    def test_subject_key_source_and_unknown_type_refuse(self):
        for body in [
            DATA.replace("schema:about ex:guest", "schema:about ex:other"),
            DATA.replace('"cluster-a/lxc/101"', '"cluster-b/lxc/101"'),
            DATA.replace("prov:used ex:source", "prov:used ex:other"),
            DATA + '\nex:guest a ex:UnknownKind .\n',
        ]:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    self.admit(body)

    def test_ipv4_ipv6_canonical_and_scope_controls(self):
        for address in ["192.0.2.1", "2001:db8::1", "::1", "0.0.0.0", "::ffff:192.0.2.1"]:
            with self.subTest(address=address):
                self.assertEqual(1, self.admit(DATA + ADDRESS % address))
        for address in ["999.1.2.3", "192.00.2.1", "2001:DB8::1", "1::2::3",
                        "fe80::1%eth0", "not-an-address"]:
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    self.admit(DATA + ADDRESS % address)
        with self.assertRaises(ValueError):
            self.admit((DATA + ADDRESS % "192.0.2.1").replace(
                "crtv:contextAddressSpace ex:network", 'crtv:contextAddressSpace "network"'))

    def test_empty_manifest_cannot_establish_coverage(self):
        with self.assertRaises(ValueError):
            self.admit(expected={})


if __name__ == "__main__":
    unittest.main()

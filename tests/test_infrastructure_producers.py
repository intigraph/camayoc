"""Offline producer fixtures, explicit identity mappings and disclosure controls."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
import subprocess
import tempfile

from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("infra_producer", ROOT / "scripts/produce_infrastructure.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def mapping(producer="ansible"):
    return dict(producer=producer, source_revision="a" * 40, extractor_revision="b" * 40,
                source_iri="https://example.org/source", activity_iri="https://example.org/activity",
                source_locator="https://example.org/repo/inventory.json", entities={
                    "node-a": dict(record="https://example.org/record/host", subject="https://example.org/host",
                                   identifier="fixture/host/a", type=str(M.CORE.ProxmoxNode)),
                    "guest-a": dict(record="https://example.org/record/guest", subject="https://example.org/guest",
                                    identifier="fixture/lxc/101", type=str(M.CORE.LXCContainer), host="node-a")})


class Producers(unittest.TestCase):
    def ansible(self, config=None, extra=None):
        source = {"_meta": {"hostvars": {"node-a": {}, "guest-a": {"password": "DO-NOT-EMIT", **(extra or {})}}}}
        return M.project(json.dumps(source).encode(), config or mapping())

    def test_inventory_projects_declared_host_guest_and_provenance(self):
        graph, manifest, report = self.ansible()
        self.assertEqual(2, M.admit(graph, manifest))
        self.assertEqual(2, report["mapped"])
        self.assertIn((M.iri("https://example.org/guest"), M.CORE.runs_on, M.iri("https://example.org/host")), graph)
        self.assertNotIn("DO-NOT-EMIT", graph.serialize())
        self.assertEqual({"declared"}, {str(v) for v in graph.objects(None, M.CORE.sourceKind)})

    def test_projection_is_idempotent_and_arbitrary_source_values_are_not_copied(self):
        graph, manifest, _ = self.ansible()
        again, expected, _ = self.ansible()
        self.assertEqual(set(graph), set(again))
        self.assertEqual(manifest, expected)
        changed, _, _ = self.ansible(extra={"arbitrary": "SECRET-OTHER"})
        self.assertNotIn("SECRET-OTHER", changed.serialize())
        self.assertNotEqual(set(graph), set(changed))  # input digest records a changed source

    def test_group_members_without_hostvars_are_not_silently_lost(self):
        raw = json.dumps({"_meta": {"hostvars": {}}, "guests": {"hosts": ["node-a", "guest-a"]}}).encode()
        graph, expected, report = M.project(raw, mapping())
        self.assertEqual(2, M.admit(graph, expected))
        self.assertEqual(2, report["mapped"])

    def test_only_explicitly_selected_declarative_service_variable_is_read(self):
        config = mapping()
        config["service_variables"] = {"node-a": "declared_units"}
        config["entities"]["node-a/service/demo.service"] = dict(
            record="https://example.org/record/service", subject="https://example.org/service",
            identifier="fixture/host/a/unit/demo.service", type=str(M.CORE.SystemdService), host="node-a")
        source = {"_meta": {"hostvars": {"node-a": {"declared_units": ["demo.service"],
                    "password": "DO-NOT-EMIT"}, "guest-a": {}}}}
        graph, expected, report = M.project(json.dumps(source).encode(), config)
        self.assertEqual(3, M.admit(graph, expected))
        self.assertEqual(3, report["mapped"])
        self.assertNotIn("DO-NOT-EMIT", graph.serialize())
        self.assertIn((M.iri("https://example.org/service"), M.CORE.runs_on, M.iri("https://example.org/host")), graph)
        source["_meta"]["hostvars"]["node-a"]["declared_units"] = {"arbitrary": "not-a-unit-list"}
        with self.assertRaises(ValueError):
            M.project(json.dumps(source).encode(), config)

    def test_unknown_hosts_and_unexplained_exclusions_refuse(self):
        for mutate in [lambda c: c["entities"].pop("node-a"),
                       lambda c: c.update(excluded={"node-a": ""})]:
            config = mapping()
            mutate(config)
            with self.assertRaises(ValueError):
                self.ansible(config)

    def test_guest_placement_requires_explicit_host_mapping(self):
        config = mapping()
        config["entities"]["guest-a"]["host"] = "unknown"
        with self.assertRaises(ValueError):
            self.ansible(config)

    def test_relative_addresses_and_duplicate_records_refuse(self):
        for field, value in [("subject", "relative"), ("record", "https://example.org/record/host")]:
            config = mapping()
            config["entities"]["guest-a"][field] = value
            with self.assertRaises(ValueError):
                self.ansible(config)

    def test_terraform_child_modules_are_declarations_not_observed_guests(self):
        resources = {"format_version": "1.0", "values": {"root_module": {"child_modules": [{"resources": [
            {"address": "module.lab.proxmox_lxc.guest", "mode": "managed", "values": {"password": "DO-NOT-EMIT"}},
            {"address": "data.lookup", "mode": "data"}]}]}}}
        config = mapping("terraform")
        config["entities"] = {"module.lab.proxmox_lxc.guest": dict(
            record="https://example.org/record/terraform", subject="https://example.org/terraform",
            identifier="fixture/state/module.lab.proxmox_lxc.guest", type=str(M.INFRA.TerraformResource))}
        graph, expected, report = M.project(json.dumps(resources).encode(), config)
        self.assertEqual(1, M.admit(graph, expected))
        self.assertEqual(1, report["mapped"])
        self.assertNotIn("DO-NOT-EMIT", graph.serialize())
        self.assertFalse(list(graph.subjects(M.RDF.type, M.CORE.LXCContainer)))

    def test_terraform_cannot_claim_a_running_container(self):
        config = mapping("terraform")
        resources = {"format_version": "1.0", "values": {"root_module": {"resources": [
            {"address": key, "mode": "managed"} for key in config["entities"]]}}}
        with self.assertRaises(ValueError):
            M.project(json.dumps(resources).encode(), config)

    def test_state_subjects_have_separate_identities_and_verified_placement(self):
        config = mapping("terraform")
        address = "module.lab.proxmox_lxc.guest"
        config["entities"] = {address: dict(record="https://example.org/record/resource",
            subject="https://example.org/resource", identifier="fixture/state/resource/guest",
            type=str(M.INFRA.TerraformResource))}
        config["state_subjects"] = {
            "host": dict(record="https://example.org/record/host", subject="https://example.org/host",
                identifier="fixture/host/node-a", type=str(M.CORE.ProxmoxNode), resource=address,
                label_field="node_name"),
            "guest": dict(record="https://example.org/record/guest", subject="https://example.org/guest",
                identifier="fixture/lxc/101", type=str(M.CORE.LXCContainer), resource=address,
                label_field="hostname", host="host", host_field="node_name")}
        resource = dict(address=address, mode="managed", values={"hostname": "guest-a", "node_name": "node-a",
                                                               "password": "DO-NOT-EMIT"})
        source = {"format_version": "1.0", "values": {"root_module": {"resources": [resource]}}}
        graph, expected, report = M.project(json.dumps(source).encode(), config)
        self.assertEqual(3, M.admit(graph, expected))
        self.assertEqual(3, report["mapped"])
        self.assertEqual(2, len(list(graph.triples((None, M.CORE.sourceKind, M.Literal("observed"))))))
        self.assertNotIn("DO-NOT-EMIT", graph.serialize())
        config["state_subjects"]["host"]["label_field"] = "hostname"
        with self.assertRaises(ValueError):
            M.project(json.dumps(source).encode(), config)
        config["state_subjects"]["host"]["label_field"] = "password"
        with self.assertRaises(ValueError):
            M.project(json.dumps(source).encode(), config)

    def test_distinct_mapping_keys_cannot_alias_one_subject(self):
        fixtures = ROOT / "profiles/infrastructure-output/fixtures"
        for same_identifier in (True, False):
            config = json.loads((fixtures / "terraform.mapping.json").read_text())
            source = json.loads((fixtures / "terraform.json").read_text())
            address = next(iter(config["entities"]))
            declaration = config["entities"][address]
            guest = config["state_subjects"]["guest"]
            guest["subject"] = declaration["subject"]
            if same_identifier:
                guest["identifier"] = declaration["identifier"]
            source["values"]["root_module"]["child_modules"][0]["resources"][0]["values"]["hostname"] = address
            with self.subTest(same_identifier=same_identifier):
                with self.assertRaisesRegex(ValueError, "duplicate subject address"):
                    M.project(json.dumps(source).encode(), config)
        config = mapping()
        config["entities"]["guest-a"]["subject"] = config["entities"]["node-a"]["subject"]
        config["entities"]["guest-a"]["identifier"] = config["entities"]["node-a"]["identifier"]
        with self.assertRaisesRegex(ValueError, "duplicate subject address"):
            self.ansible(config)

    def test_exact_source_and_extractor_revisions_required(self):
        for field in ["source_revision", "extractor_revision"]:
            config = mapping()
            config[field] = "main"
            with self.assertRaises(ValueError):
                self.ansible(config)

    @requires_quipu
    def test_generated_inventory_passes_native_output_shapes(self):
        graph, _, _ = self.ansible()
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "output.ttl"
            data.write_text(graph.serialize(format="turtle"))
            result = subprocess.run([str(QUIPU), "validate", "--shapes",
                                     str(ROOT / "profiles/infrastructure-output/shapes.ttl"),
                                     "--data", str(data)], capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertTrue(result.stdout.lstrip().startswith("valid"))


if __name__ == "__main__":
    unittest.main()

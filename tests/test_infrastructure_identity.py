"""Governed immutable key contract; all stores/data are disposable fixtures."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from rdflib import Graph, Literal, Namespace, RDF, URIRef
from rdflib.collection import Collection
from pyshacl import validate

from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
SHAPES = ROOT / "profiles/infrastructure-identity/keys.ttl"
CORE = Namespace("https://w3id.org/intigraph/camayoc/core/ns#")
INFRA = Namespace("https://w3id.org/intigraph/camayoc/homelab-infra/ns#")
CRTV = Namespace("http://open-services.net/ns/crtv#")
SH = Namespace("http://www.w3.org/ns/shacl#")
QP = Namespace("https://quipu.dev/ontology/")
SUBJECT = URIRef("urn:fixture:identity")
CASES = {
    CORE.Host: {INFRA.identitySite: Literal("fixture-site"), INFRA.identityAsset: Literal("asset-a")},
    CORE.LXCContainer: {INFRA.identityCluster: Literal("fixture-cluster"),
        INFRA.identityGuestKind: Literal("lxc"), INFRA.identityVmid: Literal(101),
        INFRA.identityIncarnation: Literal("creation-a")},
    CORE.SystemdService: {INFRA.identityHost: URIRef("urn:fixture:host"),
        INFRA.identityUnit: Literal("demo.service"), INFRA.identityIncarnation: Literal("creation-a")},
    INFRA.TerraformResource: {INFRA.identityStateAuthority: Literal("fixture-state"),
        INFRA.identityWorkspace: Literal("default"), INFRA.identityResourceAddress: Literal("module.lab.guest"),
        INFRA.identityIncarnation: Literal("declaration-a")},
    CRTV.IPAddress: {CRTV.contextAddressSpace: URIRef("urn:fixture:network"),
        CRTV.address: Literal("192.0.2.1")},
    CORE.Document: {INFRA.identitySite: Literal("fixture-site"), INFRA.identityDocument: Literal("document-a")},
}


def fixture(kind, values=None):
    graph = Graph()
    graph.add((SUBJECT, RDF.type, kind))
    for field, value in (values or CASES[kind]).items():
        graph.add((SUBJECT, field, value))
    return graph


class InfrastructureIdentity(unittest.TestCase):
    def setUp(self):
        self.shapes = Graph().parse(SHAPES)

    def conforms(self, graph):
        return validate(graph, shacl_graph=self.shapes)[0]

    def test_exactly_one_hash_key_shape_per_allocation_type(self):
        selected = {}
        for shape, fields in self.shapes.subject_objects(QP.keyFields):
            kinds = list(self.shapes.objects(shape, SH.targetClass))
            self.assertEqual(1, len(kinds))
            kind = kinds[0]
            self.assertNotIn(kind, selected)
            selected[kind] = list(Collection(self.shapes, fields))
            self.assertEqual([Literal("hash")], list(self.shapes.objects(shape, QP.keyStrategy)))
        self.assertEqual(set(CASES), set(selected))
        for kind, fields in selected.items():
            self.assertEqual(list(CASES[kind]), fields)
        self.assertNotIn(CORE.ProxmoxNode, selected)
        self.assertNotIn(CORE.BareMetalHost, selected)

    def test_each_complete_typed_identity_conforms(self):
        for kind in CASES:
            with self.subTest(kind=kind):
                self.assertTrue(self.conforms(fixture(kind)))

    def test_missing_duplicate_or_wrong_kind_keys_refuse(self):
        for kind, values in CASES.items():
            for field, value in values.items():
                for mutation in ("missing", "duplicate", "wrong_kind"):
                    graph = fixture(kind)
                    if mutation != "duplicate":
                        graph.remove((SUBJECT, field, None))
                    if mutation == "duplicate":
                        graph.add((SUBJECT, field, Literal("another-key")))
                    elif mutation == "wrong_kind":
                        wrong = Literal("not-an-iri") if isinstance(value, URIRef) else URIRef("urn:fixture:wrong")
                        graph.add((SUBJECT, field, wrong))
                    with self.subTest(kind=kind, field=field, mutation=mutation):
                        self.assertFalse(self.conforms(graph))

    def test_hostname_and_title_are_mutable_non_key_values(self):
        for kind, predicate in [(CORE.Host, INFRA.hostname), (CORE.Document, URIRef("http://www.w3.org/2000/01/rdf-schema#label"))]:
            graph = fixture(kind)
            for value in ["before", "after"]:
                graph.set((SUBJECT, predicate, Literal(value)))
                self.assertTrue(self.conforms(graph))

    def test_physical_host_classifications_keep_one_allocation_type(self):
        graph = fixture(CORE.Host)
        for classification in (CORE.ProxmoxNode, CORE.BareMetalHost):
            graph.add((SUBJECT, RDF.type, classification))
        self.assertTrue(self.conforms(graph))

    def test_container_vmid_is_an_integer_and_kind_is_lxc(self):
        for field, value in [(INFRA.identityVmid, Literal("101")),
                             (INFRA.identityVmid, Literal(0)),
                             (INFRA.identityGuestKind, Literal("ip"))]:
            graph = fixture(CORE.LXCContainer)
            graph.set((SUBJECT, field, value))
            self.assertFalse(self.conforms(graph))

    def test_declared_resource_and_observed_guest_cannot_share_an_identity(self):
        graph = fixture(INFRA.TerraformResource)
        graph += fixture(CORE.LXCContainer)
        self.assertFalse(self.conforms(graph))

    def test_address_keys_refuse_noncanonical_and_unimplemented_lexical_forms(self):
        for value in ["192.0.2.01", "0192.0.2.1", "256.0.2.1", "192.0.2.1 ",
                      "2001:db8::1", "fe80::1%eth0"]:
            graph = fixture(CRTV.IPAddress)
            graph.set((SUBJECT, CRTV.address, Literal(value)))
            with self.subTest(value=value):
                self.assertFalse(self.conforms(graph))

    @requires_quipu
    def test_native_shacl_accepts_each_type_and_refuses_missing_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "identity.ttl"
            for kind in CASES:
                for positive in (True, False):
                    graph = fixture(kind)
                    if not positive:
                        graph.remove((SUBJECT, next(iter(CASES[kind])), None))
                    data.write_text(graph.serialize(format="turtle"))
                    result = subprocess.run([str(QUIPU), "validate", "--shapes", str(SHAPES),
                        "--data", str(data)], capture_output=True, text=True, timeout=30)
                    with self.subTest(kind=kind, positive=positive):
                        if positive:
                            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                            self.assertTrue(result.stdout.lstrip().startswith("valid"))
                        else:
                            self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

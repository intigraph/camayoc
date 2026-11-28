#!/usr/bin/env python3
"""Produce a Quipu pack and its Camayoc certification envelope."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ACCESS_VIA = {"mcp", "rest", "sparql-federated", "file", "promql"}
FRESHNESS_RE = re.compile(r"^(live|snapshot\([^()]+\)|frozen\([^()]+\))$")
PRIVATE_HOST_RE = re.compile(rb"(?i)(?:[a-z0-9-]+\.)+(?:lan|svc)\b")
PRIVATE_IPV4_RE = re.compile(
    rb"(?<![0-9])(?:10(?:\.[0-9]{1,3}){3}|192\.168(?:\.[0-9]{1,3}){2}|"
    rb"172\.(?:1[6-9]|2[0-9]|3[01])(?:\.[0-9]{1,3}){2})(?![0-9])"
)
HOME_PATH_RE = re.compile(rb"/home/[A-Za-z0-9._-]+(?:/|\b)")


class PackCertificationError(ValueError):
    """A pack or certification input cannot satisfy the governed contract."""


@dataclass(frozen=True)
class PackManifest:
    pack_format: str
    name: str
    version: str
    content_hash: str
    source_graph: str


@dataclass(frozen=True)
class Certification:
    bundle_iri: str
    publisher_claim_iri: str
    certifier_claim_iri: str
    publisher_key_iri: str
    certifier_key_iri: str
    publisher_public_key: str
    certifier_public_key: str
    publisher_signature: str
    certifier_signature: str
    shapes_version: str
    shacl_report_hash: str
    provenance_manifest_iri: str
    source_uri: str
    access_via: str
    freshness: str
    verified_by: str
    frozen_window_iri: str | None = None
    shuttle_derived: bool = False


def verify_shacl_report(report_path: Path) -> str:
    """Require a machine-readable conforming report and hash its exact bytes."""
    try:
        payload = report_path.read_bytes()
        report = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise PackCertificationError(f"cannot read SHACL JSON report: {exc}") from exc
    if not isinstance(report, dict) or report.get("conforms") is not True:
        raise PackCertificationError("SHACL report must contain boolean conforms=true")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def scrub_pack(pack_path: Path) -> None:
    """Refuse a pack whose complete artifact bytes contain private infrastructure markers."""
    try:
        payload = pack_path.read_bytes()
    except OSError as exc:
        raise PackCertificationError(f"cannot scrub pack: {exc}") from exc
    findings = []
    for name, pattern in (
        ("private hostname", PRIVATE_HOST_RE),
        ("private IPv4 address", PRIVATE_IPV4_RE),
        ("home-directory path", HOME_PATH_RE),
    ):
        if pattern.search(payload):
            findings.append(name)
    if findings:
        raise PackCertificationError("artifact scrub failed: " + ", ".join(findings))


# The file suffix of a certified pack. Renamed from ".qpack.db" (aegis-fxpbys.3):
# on a khipu, pendant cords hang from the primary cord and each carries a
# self-contained record. New publications use only PENDANT_SUFFIX;
# LEGACY_SUFFIX is read for one release so an existing durable root still
# collision-checks, then it is removed.
PENDANT_SUFFIX = ".pendant.db"
LEGACY_SUFFIX = ".qpack.db"


def publish_pack(pack_path: Path, manifest: PackManifest, publish_dir: Path) -> Path:
    """Atomically publish a content-addressed durable copy and return its path."""
    digest = manifest.content_hash.removeprefix("sha256:")
    destination = publish_dir / "sha256" / f"{digest}{PENDANT_SUFFIX}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A root published before the qpack -> pendant rename holds the same digest
    # under the old name. It is still READ here, so the content-addressed
    # collision check covers it; new publications are written as .pendant.db.
    legacy = publish_dir / "sha256" / f"{digest}{LEGACY_SUFFIX}"
    if legacy.exists() and legacy.read_bytes() != pack_path.read_bytes():
        raise PackCertificationError("content-addressed publication collision")
    if destination.exists():
        if destination.read_bytes() != pack_path.read_bytes():
            raise PackCertificationError("content-addressed publication collision")
        return destination
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        shutil.copyfile(pack_path, temporary)
        temporary.replace(destination)
    except OSError as exc:
        raise PackCertificationError(f"cannot publish pack: {exc}") from exc
    return destination


def publish_pack_s3(
    pack_path: Path,
    manifest: PackManifest,
    bucket: str,
    endpoint_url: str | None = None,
    client=None,
) -> str:
    """Publish to an S3-compatible store and verify the retained object metadata."""
    if not bucket or any(char in bucket for char in "/\\"):
        raise PackCertificationError("invalid S3 bucket")
    digest = manifest.content_hash.removeprefix("sha256:")
    key = f"sha256/{digest}{PENDANT_SUFFIX}"
    try:
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint_url)
        with pack_path.open("rb") as body:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                Metadata={"canonical-graph-hash": manifest.content_hash},
            )
        retained = client.head_object(Bucket=bucket, Key=key)
        expected_size = pack_path.stat().st_size
    except (OSError, Exception) as exc:
        raise PackCertificationError(f"S3 publication failed: {exc}") from exc
    metadata = retained.get("Metadata", {})
    if retained.get("ContentLength") != expected_size:
        raise PackCertificationError("S3 retained-object size verification failed")
    if metadata.get("canonical-graph-hash") != manifest.content_hash:
        raise PackCertificationError("S3 retained-object hash metadata verification failed")
    return f"s3://{bucket}/{key}"


def publisher_message(manifest: PackManifest, certification: Certification) -> bytes:
    fields = (
        "camayoc-publisher-attestation-v1",
        manifest.content_hash,
        certification.bundle_iri,
        certification.provenance_manifest_iri,
    )
    return "|".join(fields).encode()


def certifier_message(manifest: PackManifest, certification: Certification) -> bytes:
    fields = (
        "camayoc-knowledge-certification-v1",
        manifest.content_hash,
        certification.shapes_version,
        certification.shacl_report_hash,
        "true",
        certification.provenance_manifest_iri,
        certification.frozen_window_iri or "",
    )
    return "|".join(fields).encode()


def verify_signatures(manifest: PackManifest, certification: Certification) -> None:
    """Verify two independent Ed25519 attestations over domain-separated claims."""
    if certification.publisher_key_iri == certification.certifier_key_iri:
        raise PackCertificationError("publisher and certifier key IRIs must be distinct")
    if certification.publisher_public_key == certification.certifier_public_key:
        raise PackCertificationError("publisher and certifier public keys must be distinct")
    claims = (
        ("publisher", certification.publisher_public_key, certification.publisher_signature,
         publisher_message(manifest, certification)),
        ("certifier", certification.certifier_public_key, certification.certifier_signature,
         certifier_message(manifest, certification)),
    )
    for role, public_key, signature, message in claims:
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(
                bytes.fromhex(signature), message
            )
        except (ValueError, InvalidSignature) as exc:
            raise PackCertificationError(f"invalid {role} Ed25519 signature") from exc


def _iri(value: str) -> str:
    if not value or any(char.isspace() or char in '<>"{}|' for char in value):
        raise PackCertificationError(f"invalid absolute IRI: {value!r}")
    if ":" not in value:
        raise PackCertificationError(f"IRI has no scheme: {value!r}")
    return f"<{value}>"


def _literal(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _require_hash(value: str, field: str) -> None:
    if not HASH_RE.fullmatch(value):
        raise PackCertificationError(f"{field} must be sha256:<64 lowercase hex>")


def read_manifest(pack_path: Path) -> PackManifest:
    """Read the one authoritative manifest row from a `.pendant.db`."""
    try:
        with sqlite3.connect(f"file:{pack_path}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT pack_format, name, version, content_hash, source_graph "
                "FROM pack_manifest ORDER BY id"
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        raise PackCertificationError(f"cannot read pack manifest: {exc}") from exc
    if len(rows) != 1:
        raise PackCertificationError(f"pack_manifest must contain exactly one row, found {len(rows)}")
    manifest = PackManifest(*rows[0])
    _require_hash(manifest.content_hash, "pack_manifest.content_hash")
    return manifest


def build_envelope(manifest: PackManifest, certification: Certification) -> str:
    """Render deterministic Turtle for the governed two-claim envelope."""
    _require_hash(manifest.content_hash, "pack_manifest.content_hash")
    _require_hash(certification.shacl_report_hash, "shacl_report_hash")
    if certification.access_via not in ACCESS_VIA:
        raise PackCertificationError(f"unsupported access_via: {certification.access_via}")
    if not FRESHNESS_RE.fullmatch(certification.freshness):
        raise PackCertificationError(f"invalid freshness: {certification.freshness}")
    if certification.shuttle_derived and not certification.frozen_window_iri:
        raise PackCertificationError("a Shuttle-derived bundle requires a frozen-window IRI")
    verify_signatures(manifest, certification)

    bundle = _iri(certification.bundle_iri)
    publisher = _iri(certification.publisher_claim_iri)
    certifier = _iri(certification.certifier_claim_iri)
    publisher_key = _iri(certification.publisher_key_iri)
    certifier_key = _iri(certification.certifier_key_iri)
    provenance = _iri(certification.provenance_manifest_iri)
    window = (
        f" ;\n    aegis:frozenWindow {_iri(certification.frozen_window_iri)}"
        if certification.frozen_window_iri
        else ""
    )

    # VOCABULARY only (aegis:CertifiedShareBundle and its properties); every
    # instance IRI here is caller-supplied. Held on the namespace the loaded
    # shapes gate on until the shapes move (aegis-m5s.3).
    return f"""@prefix aegis: <http://aegis.gastown.local/ontology/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

{bundle} a aegis:CertifiedShareBundle ;
    rdfs:label {_literal(manifest.name + PENDANT_SUFFIX)} ;
    aegis:canonicalGraphHash {_literal(manifest.content_hash)} ;
    aegis:shapesBundleVersion {_literal(certification.shapes_version)} ;
    aegis:provenanceManifest {provenance} ;
    aegis:publisherAttestation {publisher} ;
    aegis:certificationSeal {certifier} ;
    aegis:source_uri {_literal(certification.source_uri)} ;
    aegis:access_via {_literal(certification.access_via)} ;
    aegis:freshness {_literal(certification.freshness)} ;
    aegis:verified_by {_literal(certification.verified_by)} .

{publisher} a aegis:PublisherAttestation ;
    rdfs:label "publisher attestation" ;
    aegis:attestsBundle {bundle} ;
    aegis:signingKey {publisher_key} ;
    aegis:attestationSignature {_literal(certification.publisher_signature)} .

{certifier} a aegis:KnowledgeCertificationSeal ;
    rdfs:label "knowledge certification seal" ;
    aegis:certifiesBundle {bundle} ;
    aegis:canonicalGraphHash {_literal(manifest.content_hash)} ;
    aegis:shapesBundleVersion {_literal(certification.shapes_version)} ;
    aegis:shaclReportHash {_literal(certification.shacl_report_hash)} ;
    aegis:scrubCheckPass true ;
    aegis:provenanceManifest {provenance} ;
    aegis:signingKey {certifier_key} ;
    aegis:attestationSignature {_literal(certification.certifier_signature)}{window} .
"""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def run_quipu_pack(
    quipu_bin: str,
    db_path: Path,
    graph_iri: str,
    out_path: Path,
    name: str,
    version: str,
    shapes: Sequence[str],
    queries: Sequence[str],
    runner: Runner = subprocess.run,
) -> PackManifest:
    """Build then verify one pack, using argument arrays and no shell."""
    command = [
        quipu_bin,
        "pack",
        graph_iri,
        "--out",
        str(out_path),
        "--name",
        name,
        "--version",
        version,
        "--db",
        str(db_path),
    ]
    for shape in shapes:
        command.extend(["--shapes", shape])
    for query in queries:
        command.extend(["--queries", query])
    built = runner(command, check=False, capture_output=True, text=True)
    if built.returncode != 0:
        raise PackCertificationError(f"quipu pack failed: {built.stderr.strip()}")
    verified = runner(
        [quipu_bin, "pack", "--verify", str(out_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if verified.returncode != 0:
        raise PackCertificationError(f"quipu pack verification failed: {verified.stderr.strip()}")
    return read_manifest(out_path)


def certify_existing_pack(
    pack_path: Path, shacl_report_path: Path, certification: Certification
) -> tuple[PackManifest, str]:
    """Certify a verified pack only after locally deriving all boolean/hash evidence."""
    manifest = read_manifest(pack_path)
    scrub_pack(pack_path)
    report_hash = verify_shacl_report(shacl_report_path)
    if certification.shacl_report_hash != report_hash:
        raise PackCertificationError("SHACL report hash does not match the supplied report")
    return manifest, build_envelope(manifest, certification)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("graph_iri")
    result.add_argument("--db", type=Path, required=True)
    result.add_argument("--out", type=Path, required=True)
    result.add_argument("--name", required=True)
    result.add_argument("--version", required=True)
    result.add_argument("--shape", action="append", default=[])
    result.add_argument("--query", action="append", default=[])
    result.add_argument("--quipu-bin", default="quipu")
    result.add_argument("--envelope-out", type=Path)
    publication = result.add_mutually_exclusive_group(required=True)
    publication.add_argument("--publish-dir", type=Path)
    publication.add_argument("--s3-bucket")
    result.add_argument("--s3-endpoint")
    for field in [
        "bundle-iri",
        "publisher-claim-iri",
        "certifier-claim-iri",
        "publisher-key-iri",
        "certifier-key-iri",
        "publisher-public-key",
        "certifier-public-key",
        "publisher-signature",
        "certifier-signature",
        "shapes-version",
        "provenance-manifest-iri",
        "source-uri",
        "access-via",
        "freshness",
        "verified-by",
    ]:
        result.add_argument(f"--{field}", required=True)
    result.add_argument("--frozen-window-iri")
    result.add_argument("--shacl-report", type=Path, required=True)
    result.add_argument("--shuttle-derived", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = run_quipu_pack(
            args.quipu_bin,
            args.db,
            args.graph_iri,
            args.out,
            args.name,
            args.version,
            args.shape,
            args.query,
        )
        report_hash = verify_shacl_report(args.shacl_report)
        certification = Certification(
            bundle_iri=args.bundle_iri,
            publisher_claim_iri=args.publisher_claim_iri,
            certifier_claim_iri=args.certifier_claim_iri,
            publisher_key_iri=args.publisher_key_iri,
            certifier_key_iri=args.certifier_key_iri,
            publisher_public_key=args.publisher_public_key,
            certifier_public_key=args.certifier_public_key,
            publisher_signature=args.publisher_signature,
            certifier_signature=args.certifier_signature,
            shapes_version=args.shapes_version,
            shacl_report_hash=report_hash,
            provenance_manifest_iri=args.provenance_manifest_iri,
            source_uri=args.source_uri,
            access_via=args.access_via,
            freshness=args.freshness,
            verified_by=args.verified_by,
            frozen_window_iri=args.frozen_window_iri,
            shuttle_derived=args.shuttle_derived,
        )
        scrub_pack(args.out)
        if args.publish_dir:
            published_uri = publish_pack(args.out, manifest, args.publish_dir).resolve().as_uri()
        else:
            published_uri = publish_pack_s3(
                args.out, manifest, args.s3_bucket, args.s3_endpoint
            )
        if args.source_uri != published_uri:
            raise PackCertificationError(
                f"source_uri must identify published content-addressed copy: {published_uri}"
            )
        envelope = build_envelope(manifest, certification)
        envelope_path = args.envelope_out or Path(f"{args.out}.cert.ttl")
        envelope_path.write_text(envelope)
    except PackCertificationError as exc:
        print(f"certify-pack refused: {exc}", file=sys.stderr)
        return 2
    print(f"certified {args.out}: {manifest.content_hash} -> {envelope_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

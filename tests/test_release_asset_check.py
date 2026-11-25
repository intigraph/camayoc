"""One deterministic asset check per release (aegis-7zkl).

Offline mapping tests against a release shaped like quipu-ai-v0.19.4, plus,
where QUIPU_BIN is promised, the written record validated against
shapes/release-assets.shapes.ttl by quipu's SHACL engine.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from quipu_bin_guard import QUIPU, requires_quipu

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_asset_check as rc  # noqa: E402
from ingest_cdevents import Quarantined  # noqa: E402

TAG = "quipu-ai-v0.19.4"
PUBLISHED = "2026-11-24T23:29:40Z"
PUBLISHED_EPOCH = 1795562980.0
NAMES = [f"quipu-{TAG}-{s}" for s in (
    "x86_64-unknown-linux-gnu.tar.gz", "x86_64-unknown-linux-gnu.tar.gz.sha256",
    "repository.pendant.tar.gz", "repository.pendant.tar.gz.sha256",
    "repository.pendant.tar.gz.provenance.json",
    "repository.qpack.tar.gz", "repository.qpack.tar.gz.sha256",
    "repository.qpack.tar.gz.provenance.json")]
RELEASE = {"node_id": "RE_kwDOExample0", "tag_name": TAG, "published_at": PUBLISHED, "draft": False,
           "assets": [{"name": n} for n in NAMES]}
COMPLETED = "2026-11-25T03:31:02Z"  # the producer's verification check run, ~4h after publish


def release(drop: tuple[str, ...] = (), **changes: object) -> dict:
    body = copy.deepcopy(RELEASE)
    body["assets"] = [a for a in body["assets"] if not any(a["name"].endswith(d) for d in drop)]
    body.update(changes)
    return body


def check(body: dict, **kw: object) -> dict:
    kw.setdefault("trigger", "check-run")
    kw.setdefault("as_of", COMPLETED)
    return rc.episode_for("intigraph/quipu", body, actor="t", **kw)


class Judgment(unittest.TestCase):
    def props(self, body: dict, **kw: object) -> dict:
        return check(body, **kw)["nodes"][0]["properties"]

    def test_rule_file_is_the_reviewed_quipu_rule(self) -> None:
        rules, _ = rc.load_rules()
        rule = rules["repositories"]["intigraph/quipu"]
        self.assertEqual(rule["trigger_check_run"], "Trusted repository share")
        self.assertEqual(rule["backstop_seconds"], 180 * 60)
        self.assertEqual(sorted(rule["required_suffixes"]), sorted([
            "-repository.pendant.tar.gz", "-repository.pendant.tar.gz.sha256",
            "-repository.qpack.tar.gz", "-repository.qpack.tar.gz.sha256"]))

    def test_v0194_shape_is_complete_and_writes_one_fact(self) -> None:
        body = check(release())
        self.assertEqual(len(body["nodes"]), 1)
        props = body["nodes"][0]["properties"]
        self.assertEqual(props["assetCheckOutcome"], "complete")
        self.assertNotIn("missingAsset", props)
        self.assertEqual(props["occurredAt"], COMPLETED)
        self.assertEqual(props["assetCheckTrigger"], "check-run")
        self.assertEqual(body["edges"][0]["relation"], "assetCheckOf")
        self.assertEqual(body["edges"][0]["target"], "github.RE_kwDOExample0")

    def test_provenance_is_not_required(self) -> None:
        self.assertEqual(self.props(release(drop=(".provenance.json",)))["assetCheckOutcome"], "complete")

    def test_missing_pendant_is_incomplete_and_named(self) -> None:
        props = self.props(release(drop=("repository.pendant.tar.gz", "repository.pendant.tar.gz.sha256")))
        self.assertEqual(props["assetCheckOutcome"], "incomplete")
        self.assertEqual(props["missingAsset"], [f"quipu-{TAG}-repository.pendant.tar.gz",
                                                 f"quipu-{TAG}-repository.pendant.tar.gz.sha256"])

    def test_each_required_asset_is_load_bearing(self) -> None:
        for suffix in ("repository.pendant.tar.gz", "repository.qpack.tar.gz"):
            for ext in ("", ".sha256"):
                name = f"quipu-{TAG}-{suffix}{ext}"
                body = release()
                body["assets"] = [a for a in body["assets"] if a["name"] != name]
                with self.subTest(missing=name):
                    self.assertEqual(self.props(body)["missingAsset"], [name])

    def test_rerun_of_the_same_trigger_is_byte_identical(self) -> None:
        a = json.dumps(check(release(), now=PUBLISHED_EPOCH + 99999), sort_keys=True)
        b = json.dumps(check(release(), now=PUBLISHED_EPOCH + 5), sort_keys=True)
        self.assertEqual(a, b)

    def test_key_is_per_release_and_rule(self) -> None:
        a = self.props(release())["assetCheckKey"]
        b = self.props(release(node_id="RE_kwDOOther"))["assetCheckKey"]
        self.assertNotEqual(a, b)

    def test_backstop_then_late_check_run_are_two_judgments(self) -> None:
        # v0.19.4's assets landed ~4h after publish: the 180m backstop judges
        # incomplete, then the producer's check run judges complete. Two facts
        # on two nodes; neither overwrites the other.
        early = check(release(drop=("repository.pendant.tar.gz",)), trigger="backstop", as_of=None,
                      now=PUBLISHED_EPOCH + 4 * 3600)
        late = check(release())
        (a,), (b,) = early["nodes"], late["nodes"]
        self.assertNotEqual(a["name"], b["name"])
        self.assertNotEqual(early["name"], late["name"])
        self.assertEqual((a["properties"]["assetCheckOutcome"], b["properties"]["assetCheckOutcome"]),
                         ("incomplete", "complete"))
        # The current verdict is the latest judgment, chosen at read time.
        latest = max((a, b), key=lambda n: n["properties"]["occurredAt"])
        self.assertIs(latest, b)
        self.assertEqual(early["edges"][0]["target"], late["edges"][0]["target"])

    def test_two_check_runs_at_different_instants_are_two_judgments(self) -> None:
        a = self.props(release())
        b = self.props(release(), as_of="2026-11-25T04:00:00Z")
        self.assertNotEqual(a["assetCheckKey"], b["assetCheckKey"])

    def test_backstop_waits_180_minutes_then_judges_at_its_deadline(self) -> None:
        deadline = PUBLISHED_EPOCH + 180 * 60
        with self.assertRaises(rc.TooEarly):
            check(release(drop=("repository.qpack.tar.gz",)), trigger="backstop", as_of=None, now=deadline - 1)
        props = self.props(release(drop=("repository.qpack.tar.gz",)), trigger="backstop", as_of=None,
                           now=deadline + 7200)
        self.assertEqual((props["assetCheckOutcome"], props["assetCheckTrigger"]), ("incomplete", "backstop"))
        self.assertEqual(props["occurredAt"], "2026-11-25T02:29:40Z")

    def test_quarantine_never_guesses(self) -> None:
        cases = {
            "unknown repo": lambda: rc.episode_for("intigraph/other", release(), actor="t", trigger="check-run",
                                                   as_of=COMPLETED),
            "draft": lambda: check(release(draft=True)),
            "no node id": lambda: check(release(node_id="")),
            "assets not a list": lambda: check(release(assets={"a": 1})),
            "unnamed asset": lambda: check(release(assets=[{"size": 1}])),
            "bad publish time": lambda: check(release(published_at="2026-02-30T00:00:00Z")),
            "check run before publish": lambda: check(release(), as_of="2026-11-24T23:00:00Z"),
            "check run without time": lambda: check(release(), as_of=None),
            "unknown trigger": lambda: check(release(), trigger="cron"),
        }
        for name, call in cases.items():
            with self.subTest(name), self.assertRaises(Quarantined):
                call()

    def test_cli_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ok, bad = Path(d, "ok.json"), Path(d, "bad.json")
            ok.write_text(json.dumps(release()))
            bad.write_text(json.dumps(release(drop=("repository.pendant.tar.gz",))))
            base = ["--repo", "intigraph/quipu", "--actor", "t", "--trigger", "check-run", "--as-of", COMPLETED]
            self.assertEqual(rc.main(base + ["--release-json", str(ok)]), 0)
            self.assertEqual(rc.main(base + ["--release-json", str(bad)]), 3)
            early = ["--repo", "intigraph/quipu", "--actor", "t", "--trigger", "backstop",
                     "--now", str(PUBLISHED_EPOCH), "--release-json", str(ok)]
            self.assertEqual(rc.main(early), 4)


def turtle(body: dict) -> str:
    out = ["@prefix aegis: <http://aegis.gastown.local/ontology/> .",
           "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> ."]
    for edge in body["edges"]:
        out.append(f"aegis:{edge['target']} a aegis:GitHubRelease .")
    for node in body["nodes"]:
        out.append(f"aegis:{node['name']} a aegis:{node['type']} ; rdfs:label {json.dumps(node['description'])} .")
        for key, value in node["properties"].items():
            for v in value if isinstance(value, list) else [value]:
                out.append(f"aegis:{node['name']} aegis:{key} {json.dumps(v)} .")
    for edge in body["edges"]:
        out.append(f"aegis:{edge['source']} aegis:{edge['relation']} aegis:{edge['target']} .")
    return "\n".join(out) + "\n"


@requires_quipu
class RecordConforms(unittest.TestCase):
    def report(self, ttl: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".ttl", delete=False) as fh:
            fh.write(ttl)
        try:
            out = subprocess.run([str(QUIPU), "validate", "--shapes", str(ROOT / "shapes/release-assets.shapes.ttl"),
                                  "--data", fh.name], capture_output=True, text=True, timeout=60)
        finally:
            Path(fh.name).unlink()
        return out.stdout

    def test_complete_and_incomplete_records_conform(self) -> None:
        for body in (check(release()), check(release(drop=("repository.pendant.tar.gz",)))):
            out = self.report(turtle(body))
            self.assertTrue(out.lstrip().startswith("valid"), out)

    def test_outcome_and_missing_assets_must_agree(self) -> None:
        good = check(release())
        lie = copy.deepcopy(good)
        lie["nodes"][0]["properties"]["missingAsset"] = ["x"]
        self.assertTrue(self.report(turtle(lie)).lstrip().startswith("invalid"))
        silent = check(release(drop=("repository.pendant.tar.gz",)))
        silent["nodes"][0]["properties"].pop("missingAsset")
        self.assertTrue(self.report(turtle(silent)).lstrip().startswith("invalid"))

    def test_backstop_and_late_check_run_conform_together(self) -> None:
        # Both judgments of one release in one graph: no maxCount collision.
        early = check(release(drop=("repository.pendant.tar.gz",)), trigger="backstop", as_of=None,
                      now=PUBLISHED_EPOCH + 4 * 3600)
        ttl = turtle(early) + turtle(check(release())).split("\n", 2)[2]
        out = self.report(ttl)
        self.assertTrue(out.lstrip().startswith("valid"), out)

    def test_check_must_point_at_a_release(self) -> None:
        body = check(release())
        ttl = turtle(body).replace("a aegis:GitHubRelease", "a aegis:GitHubIssue")
        self.assertTrue(self.report(ttl).lstrip().startswith("invalid"))


if __name__ == "__main__":
    unittest.main()

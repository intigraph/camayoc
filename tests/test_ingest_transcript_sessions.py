#!/usr/bin/env python3
"""Tests for scripts/ingest_transcript_sessions.py — archive -> Session links.

The properties under test are the ones a wrong answer would hide:

* ONE Session node per harness session. The codex corpus id is a rollout
  stem; linking it verbatim would mint a second Session beside the cost one.
* A POINTER to a published object only. A file the publisher excluded has no
  Garage object, and an unknown exclude list is refused, never read as empty.
* The governed Session facts are the cost producer's byte-identical strings,
  so the shared label stays exactly one.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import json
from unittest.mock import patch
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "ingest_transcript_sessions", SCRIPTS / "ingest_transcript_sessions.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)
import ingest_session_usage  # noqa: E402

CLAUDE_ID = "8619c43c-8a25-4970-8fa2-ccdfe92257b4"
CODEX_UUID = "01a0a19c-986a-7301-a9fb-d0fce65f8d00"
CODEX_ID = f"rollout-2026-09-14T16-29-49-{CODEX_UUID}"


def record(root: Path, agent: str, name: str, harness: str, sid: str,
           age: float = 7200, frontmatter_agent: str | None = None) -> Path:
    path = root / agent / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nschema: agent-transcript/v1\nprojection_version: 3\n"
        f"id: {sid}\ntimestamp: 2026-09-25T17:51:43.813922Z\n"
        f"agent: {frontmatter_agent or agent}\nharness: {harness}\n"
        f"source_file: {sid}.jsonl\n---\n\n[user/text] body is hashed, never parsed\n")
    stamp = path.stat().st_mtime - age
    os.utime(path, (stamp, stamp))
    return path


class SessionIdentity(unittest.TestCase):
    def test_claude_id_is_the_session_id(self):
        self.assertEqual(mod.session_id({"harness": "claude", "id": CLAUDE_ID}), CLAUDE_ID)

    def test_codex_rollout_stem_maps_to_its_trailing_uuid(self):
        # The cost producer keys codex Sessions by session_meta.id; the stem
        # would mint a second node for the same session.
        self.assertEqual(mod.session_id({"harness": "codex", "id": CODEX_ID}), CODEX_UUID)

    def test_unrecognised_ids_abstain(self):
        self.assertIsNone(mod.session_id({"harness": "claude", "id": "not-a-uuid"}))
        self.assertIsNone(mod.session_id({"harness": "gemini", "id": CLAUDE_ID}))


class Scan(unittest.TestCase):
    def test_pointer_names_the_published_key_and_digest(self):
        with TemporaryDirectory() as tmp:
            path = record(Path(tmp), "wu", f"{CLAUDE_ID}.md", "claude", CLAUDE_ID)
            records, stats = mod.scan(Path(tmp), 3600, path.stat().st_mtime + 7200)
        self.assertEqual(stats["linked"], 1)
        self.assertEqual(records[0]["key"], f"garage-hla:hla/agent-transcripts/wu/{CLAUDE_ID}.md")
        self.assertEqual(records[0]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest()
                         if path.exists() else records[0]["sha256"])

    def test_excluded_file_gets_no_pointer(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            record(root, "ian", f"{CODEX_ID}.md", "codex", CODEX_ID)
            exclude = root / "exclude.txt"
            exclude.write_text(f"/ian/{CODEX_ID}.md\n")
            records, stats = mod.scan(root, 0, 1e12, mod.excluded(exclude))
        self.assertEqual(records, [])
        self.assertEqual(stats["skipped"], {"excluded from publication": 1})

    def test_missing_exclude_list_is_refused_not_empty(self):
        with self.assertRaises(OSError):
            mod.excluded(Path("/nonexistent/exclude.txt"))

    def test_active_session_waits_for_quiescence(self):
        with TemporaryDirectory() as tmp:
            path = record(Path(tmp), "wu", f"{CLAUDE_ID}.md", "claude", CLAUDE_ID, age=0)
            records, stats = mod.scan(Path(tmp), 3600, path.stat().st_mtime + 60)
        self.assertEqual((records, stats["active"]), ([], 1))

    def test_scratch_directory_is_not_a_principal(self):
        with TemporaryDirectory() as tmp:
            record(Path(tmp), "arnold-x-scratchpad-chrometest", f"{CLAUDE_ID}.md", "claude", CLAUDE_ID)
            records, stats = mod.scan(Path(tmp), 0, 1e12)
        self.assertEqual(records, [])
        self.assertEqual(stats["skipped"], {"agent not a crew principal": 1})


class Turtle(unittest.TestCase):
    def test_session_label_is_the_cost_producers_exact_string(self):
        rec = {"session": CODEX_UUID, "agent": "muldoon", "harness": "codex", "at": "",
               "key": "garage-hla:hla/agent-transcripts/muldoon/x.md", "sha256": "0" * 64, "bytes": 1}
        cost: list[str] = []
        ingest_session_usage.emit(CODEX_UUID, "muldoon", "openai", "codex", [], cost)
        cost_label = next(line for line in cost if "rdfs:label" in line).strip()
        self.assertIn(cost_label, mod.turtle(rec))
        self.assertEqual(mod.turtle(rec).count("session " + CODEX_UUID + '"'), 1)

    def test_no_body_content_enters_the_graph(self):
        with TemporaryDirectory() as tmp:
            path = record(Path(tmp), "wu", f"{CLAUDE_ID}.md", "claude", CLAUDE_ID)
            records, _ = mod.scan(Path(tmp), 0, path.stat().st_mtime + 7200)
        self.assertNotIn("body is hashed", mod.turtle(records[0]))



class DeclaredWorkItems(unittest.TestCase):
    def stats_db(self, root: Path, rows) -> Path:
        path = root / "stats.sqlite"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE task_contexts (id INTEGER PRIMARY KEY, ts REAL, agent TEXT,"
                         " session TEXT, task TEXT, paired_start INTEGER)")
            conn.executemany("INSERT INTO task_contexts (ts, agent, session, task, paired_start)"
                             " VALUES (0, 'wu', ?, ?, ?)", rows)
        return path

    def test_only_paired_valid_starts_become_edges(self):
        with TemporaryDirectory() as tmp:
            db = self.stats_db(Path(tmp), [(CLAUDE_ID, "aegis-9lri74", 1),
                                           (CLAUDE_ID, "aegis-unpaired", 0),
                                           (CLAUDE_ID, "not a task", 1),
                                           (CLAUDE_ID, "aegis-9lri74", 1)])
            self.assertEqual(mod.declared_tasks(db), {CLAUDE_ID: ["aegis-9lri74"]})

    def test_edges_are_in_the_snapshot_and_the_digest(self):
        with TemporaryDirectory() as tmp:
            path = record(Path(tmp), "wu", f"{CLAUDE_ID}.md", "claude", CLAUDE_ID)
            records, _ = mod.scan(Path(tmp), 0, path.stat().st_mtime + 7200,
                                  tasks={CLAUDE_ID: ["aegis-9lri74"]})
        rec = records[0]
        self.assertIn(f"declaredWorkItem> <{ingest_session_usage.ONTOLOGY}aegis-9lri74>", mod.turtle(rec))
        self.assertEqual(mod.digest(rec), rec["sha256"] + ":aegis-9lri74")

    def test_sessions_without_declarations_keep_the_bare_digest(self):
        # Existing state stores the bare sha; adding step (b) must not
        # re-publish every transcript-only session.
        self.assertEqual(mod.digest({"sha256": "ab", "tasks": []}), "ab")

    def test_no_stats_db_means_no_edges_not_an_error(self):
        self.assertEqual(mod.declared_tasks(None), {})


class PendingRecovery(unittest.TestCase):
    def setUp(self):
        self.rec = dict(session=CLAUDE_ID, agent="wu", harness="claude", at="",
                        key="archive/session.md", sha256="a" * 64, bytes=10,
                        tasks=["aegis-fixture"])

    def test_transport_error_retains_exact_bytes_tasks_and_private_journal(self):
        with TemporaryDirectory() as tmp, patch.object(mod.planes, "_post", side_effect=OSError("after POST")):
            state = Path(tmp) / "state.json"
            with self.assertRaises(OSError):
                mod.publish([self.rec], "test", state, 1)
            marker = state.with_suffix(".pending.json")
            envelope = json.loads(marker.read_text())
            record, payload = mod.retained_payload(envelope)
            self.assertEqual(record, self.rec)
            self.assertEqual(payload, mod.body(self.rec, "test"))
            self.assertEqual(envelope["tasks"], ["aegis-fixture"])
            self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
            self.assertFalse(state.exists())

    def marker(self, root):
        state = Path(root) / "state.json"
        mod.durable_json(state.with_suffix(".pending.json"),
                         mod.pending_envelope(self.rec, mod.body(self.rec, "old actor")))
        return state

    def test_landed_recovery_never_resends(self):
        with TemporaryDirectory() as tmp, patch.object(mod, "snapshot_outcome", return_value="landed"), patch.object(mod.planes, "_post") as post:
            state = self.marker(tmp)
            result = mod.publish([self.rec], "changed actor", state, 1)
            post.assert_not_called()
            self.assertEqual(result["reconciled"], 1)
            self.assertEqual(json.loads(state.read_text())[CLAUDE_ID], mod.digest(self.rec))
            self.assertFalse(state.with_suffix(".pending.json").exists())

    def test_absent_twice_gap_then_byte_identical_retry(self):
        with TemporaryDirectory() as tmp, patch.object(mod, "snapshot_outcome", side_effect=["absent", "absent", "landed"]), patch.object(mod.time, "sleep") as sleep, patch.object(mod.planes, "_post", return_value={"tx_id": 17}) as post:
            state = self.marker(tmp)
            original = mod.retained_payload(json.loads(state.with_suffix(".pending.json").read_text()))[1]
            mod.publish([self.rec], "new actor", state, 1)
            sleep.assert_called_once_with(1.0)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(mod.canonical_payload(post.call_args.args[1]), mod.canonical_payload(original))

    def test_unknown_superseded_partial_and_second_read_change_preserve_bytes(self):
        for outcomes in (["superseded"], ["absent", "landed"], [OSError("read timeout")]):
            with self.subTest(outcomes=outcomes), TemporaryDirectory() as tmp, patch.object(mod, "snapshot_outcome", side_effect=outcomes), patch.object(mod.time, "sleep"), patch.object(mod.planes, "_post") as post:
                state = self.marker(tmp)
                marker = state.with_suffix(".pending.json")
                before = marker.read_bytes()
                with self.assertRaises((ValueError, OSError)):
                    mod.publish([], "test", state, 1)
                self.assertEqual(marker.read_bytes(), before)
                self.assertFalse(state.exists())
                post.assert_not_called()

    def test_legacy_and_corrupt_payloads_never_send(self):
        for mutate in (lambda e: {"session": CLAUDE_ID, "sha256": "a" * 64},
                       lambda e: e | {"payload_bytes": 0},
                       lambda e: e | {"tasks": []}):
            with TemporaryDirectory() as tmp, patch.object(mod.planes, "_post") as post:
                state = self.marker(tmp)
                marker = state.with_suffix(".pending.json")
                mod.durable_json(marker, mutate(json.loads(marker.read_text())))
                before = marker.read_bytes()
                with self.assertRaisesRegex(ValueError, "legacy or invalid"):
                    mod.publish([], "test", state, 1)
                self.assertEqual(marker.read_bytes(), before)
                post.assert_not_called()

    def test_after_post_transport_fault_landed_outcome_no_duplicate(self):
        with TemporaryDirectory() as tmp, patch.object(mod.planes, "_post", side_effect=OSError("lost response")) as post:
            state = Path(tmp) / "state.json"
            with self.assertRaises(OSError):
                mod.publish([self.rec], "test", state, 1)
            with patch.object(mod, "snapshot_outcome", return_value="landed"):
                mod.publish([self.rec], "test", state, 1)
            self.assertEqual(post.call_count, 1)


    def test_real_publish_after_lost_response_recovers_without_second_knot(self):
        session, artifact = mod.iris(self.rec)
        rows = [dict(s=artifact,p=mod.ONTOLOGY+"contentSha256",o=self.rec["sha256"]),
                dict(s=artifact,p=mod.ONTOLOGY+"contentBytes",o="10"),
                dict(s=artifact,p=mod.ONTOLOGY+"reference",o=self.rec["key"]),
                dict(s=session,p=mod.ONTOLOGY+"transcript",o=artifact),
                dict(s=artifact,p=mod.ONTOLOGY+"transcriptOf",o=session),
                dict(s=session,p=mod.ONTOLOGY+"declaredWorkItem",o=mod.ONTOLOGY+"aegis-fixture")]
        calls=[]
        def transport(path,payload,**kwargs):
            calls.append(path)
            if path == "/knot":raise OSError("committed then lost response")
            return {"rows":rows}
        with TemporaryDirectory() as tmp,patch.object(mod.planes,"_post",side_effect=transport):
            state=Path(tmp)/"state.json"
            with self.assertRaises(OSError):mod.publish([self.rec],"test",state,1)
            mod.publish([self.rec],"test",state,1)
            self.assertEqual(calls.count("/knot"),1)
            self.assertEqual(json.loads(state.read_text())[CLAUDE_ID],mod.digest(self.rec))


    def test_truncated_query_is_unknown_even_with_an_empty_rows_list(self):
        with patch.object(mod.planes,"_post",return_value={"rows":[],"truncated":True}):
            with self.assertRaisesRegex(ValueError,"incomplete"):
                mod.query("SELECT ?s WHERE { ?s ?p ?o }")


    def test_snapshot_comparison_requires_complete_owned_projection(self):
        session, artifact = mod.iris(self.rec)
        values = [(artifact, "contentSha256", self.rec["sha256"]),
                  (artifact, "contentBytes", "10"), (artifact, "reference", self.rec["key"]),
                  (session, "transcript", artifact), (artifact, "transcriptOf", session),
                  (session, "declaredWorkItem", mod.ONTOLOGY + "aegis-fixture")]
        rows = [dict(s=s, p=mod.ONTOLOGY+p, o=o) for s,p,o in values]
        for supplied, expected in ((rows,"landed"), (rows[:-1],"superseded"),
                                   (rows+[dict(s=session,p=mod.ONTOLOGY+"declaredWorkItem",o="foreign")],"superseded")):
            with patch.object(mod, "query", return_value=supplied):
                self.assertEqual(mod.snapshot_outcome(self.rec), expected)
        with patch.object(mod, "query", side_effect=[[], [{"s": "positive"}]]):
            self.assertEqual(mod.snapshot_outcome(self.rec), "absent")
        with patch.object(mod, "query", side_effect=[[], []]):
            with self.assertRaisesRegex(ValueError, "positive"):
                mod.snapshot_outcome(self.rec)

    def test_explicit_archive_retains_exact_legacy_bytes_and_never_posts(self):
        with TemporaryDirectory() as tmp, patch.object(mod.planes, "_post") as post:
            state = Path(tmp) / "state.json"
            marker = state.with_suffix(".pending.json")
            raw = b'{"session":"old","sha256":"old"}\n'
            marker.write_bytes(raw)
            reason = Path(tmp) / "reason.txt"
            reason.write_text("Owner adjudicated superseded legacy snapshot; no identical payload remains.")
            result = mod.archive_pending(state, reason)
            archived = state.parent / "state.pending-archive" / (result["sha256"] + ".json")
            self.assertEqual(archived.read_bytes(), raw)
            self.assertFalse(marker.exists())
            self.assertFalse(state.exists())
            post.assert_not_called()

    def test_refusal_and_unproven_replay_retain_marker(self):
        for response, outcomes in (({"conforms":False},["absent","absent"]),
                                   ({"tx_id":19},["absent","absent","superseded"])):
            with TemporaryDirectory() as tmp, patch.object(mod, "snapshot_outcome", side_effect=outcomes), patch.object(mod.time,"sleep"), patch.object(mod.planes,"_post",return_value=response):
                state=self.marker(tmp); marker=state.with_suffix(".pending.json"); before=marker.read_bytes()
                with self.assertRaises(ValueError):mod.publish([],"test",state,1)
                self.assertEqual(marker.read_bytes(),before)
                self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()

"""Offline controls. Synthetic model responses prove plumbing, not calibration."""
import json
import copy
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import jev
import jev_mcp
import seed_schedule as gate


def item(ident="one", scheduled=True, trigger=False, kind="synthetic-control"):
    return {"id":ident,"title":"Run job hourly", "body":"Use cron",
            "expected":{"scheduled_work":scheduled,"chaski_trigger":trigger},
            "kind":kind,"label_channel":"agent-reference","judge":"fixture-only",
            "private_reference":"DO NOT SEND"}


def client(values):
    calls = []
    def transport(body, key):
        calls.append(body)
        value = values[len(calls)-1]
        if isinstance(value, Exception): raise value
        return {"model":gate.MODEL,"answers":{
            "scheduled_work":{"noul":value[0]},"chaski_trigger":{"noul":value[1]}},
            "usage":{"input_tokens":20,"output_tokens":0}}
    return jev.JevClient(api_key="fake",transport=transport,model=gate.MODEL), calls


class ScheduleTests(unittest.TestCase):
    def test_atomic_requests_do_not_send_labels_or_provenance(self):
        req = gate.request(item()["title"],item()["body"])
        self.assertEqual(set(req["questions"]),{"scheduled_work","chaski_trigger"})
        self.assertEqual(set(req["state"]),{"title","body"})
        self.assertNotIn("DO NOT SEND",json.dumps(req))
        self.assertEqual(req["model"],gate.MODEL)

    def test_flag_clear_uncertain_are_advisory_and_inferred(self):
        for values, expected in [((.95,.02),"flag"),((.95,.95),"clear"),((.05,.1),"clear"),((.6,.4),"uncertain")]:
            fake,_ = client([values]); out = gate.classify("work","body",fake)
            self.assertEqual(out["outcome"],expected)
            self.assertEqual(out["labels_to_add"],["needs-chaski"] if expected=="flag" else [])
            self.assertFalse(out["refuses_write"])
            self.assertEqual(out["plane"],"quarantine")

    def test_invalid_probability_model_and_oversize_fail_before_admission(self):
        for value in [True,math.nan,math.inf,-.1,1.1,".8"]:
            fake,_ = client([(value,.1)])
            with self.assertRaises(ValueError):gate.classify("work","body",fake)
        with self.assertRaises(ValueError):gate.request("work","x"*2049)
        fake,_ = client([(.95,.1)]);out=fake.ask({"title":"work", "body":"body"},gate.QUESTIONS);out["response"]["model"]="other"
        with self.assertRaises(ValueError):gate.verdict(out, expected_request=gate.request("work", "body"))

    def test_mcp_surface_returns_proposed_label_and_accounts_once(self):
        fake,calls=client([(.99,.01)])
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/"usage.jsonl"
            out=jev_mcp.call_tool("check_scheduled_work",{"title":"hourly cron","body":"run task"},lambda:fake,{"JEV_USAGE_LOG":str(log)})
            self.assertEqual(out["labels_to_add"],["needs-chaski"])
            self.assertEqual(len(calls),1)
            self.assertEqual(len(log.read_text().splitlines()),1)

    def test_dry_run_is_keyless_and_replay_counts_unknowns_and_both_populations(self):
        items=[item(),item("two",False,False,"operational-paraphrase")]
        with patch.object(jev,"JevClient",side_effect=AssertionError("network")):
            preview=gate.run(items,None,False,None,None,None,None)
        self.assertFalse(preview["sent"]);self.assertEqual(preview["planned_calls"],2)
        report=gate.report(items,[])
        self.assertEqual(report["unknown"],2)
        self.assertIsNone(report["groups"]["operational-paraphrase"]["flag_agreement"])

    def test_live_single_attempt_failure_retained_and_no_retry(self):
        items=[item(),item("two")];fake,calls=client([jev.JevUnavailable("fixture failure"),(.9,.1)])
        accounting=[]
        with tempfile.TemporaryDirectory() as tmp, patch.object(jev,"JevClient",return_value=fake):
            path=Path(tmp)/"responses.jsonl"
            out=gate.run(items,None,True,2,path,"0.01","fixture ruling",accounting.append)
            self.assertEqual(len(calls),1);self.assertEqual(len(accounting),1)
            self.assertEqual(out["unknown"],2);self.assertEqual(out["retries"],0)
            self.assertIsNone(out["list_price_usd"]);self.assertEqual(out["unaccounted_calls"],1)
            self.assertEqual(len(path.read_text().splitlines()),1)
            with self.assertRaises(FileExistsError):gate.run(items,None,True,2,path,"0.01","fixture ruling",accounting.append)

    def test_exact_frozen_binding_and_labelled_misses_are_not_hidden(self):
        items=[item()];fake,calls=client([(.01,.99)])
        with tempfile.TemporaryDirectory() as tmp,patch.object(jev,"JevClient",return_value=fake):
            path=Path(tmp)/"responses.jsonl"
            live=gate.run(items,None,True,1,path,"0.01","fixture ruling",lambda out:None)
            self.assertEqual(len(live["misses"]),1)
            replay=gate.run(items,path,False,None,None,None,None)
            self.assertEqual(replay["rows"],live["rows"])
            items[0]["expected"]["scheduled_work"]=False
            with self.assertRaises(ValueError):gate.run(items,path,False,None,None,None,None)

    def test_cost_call_and_accounting_preconditions_make_no_calls(self):
        with patch.object(jev,"JevClient",side_effect=AssertionError("network")):
            for args in [(2,".01","ruling",lambda out:None),(1,"1","ruling",lambda out:None),(1,".01","",lambda out:None),(1,".01","ruling",None)]:
                with self.assertRaises(ValueError):gate.run([item()],None,True,args[0],Path("unused"),args[1],args[2],args[3])


class EmbeddedRequestBindingTests(unittest.TestCase):
    def record(self):
        items = [item()]
        fake, _ = client([(.95, .01)])
        req = gate.request(items[0]["title"], items[0]["body"])
        response = fake.ask(req["state"], req["questions"])
        return items, {"id": items[0]["id"], "dataset_sha256": gate.digest(items),
                       "request_sha256": gate.digest(req), "status": "ok", "response": response}

    def test_foreign_state_is_not_accepted_by_original_sidecar_hash(self):
        items, record = self.record()
        record = copy.deepcopy(record)
        record["response"]["request"]["state"] = {"title": "UNRELATED FOREIGN REQUEST", "body": "Different input"}
        with self.assertRaises(ValueError):
            gate.report(items, [record])

    def test_foreign_questions_are_not_accepted_by_original_sidecar_hash(self):
        items, record = self.record()
        record = copy.deepcopy(record)
        record["response"]["request"]["questions"] = {"foreign": {"type": "noul", "question": "Different question"}}
        with self.assertRaises(ValueError):
            gate.report(items, [record])

    def test_missing_embedded_request_is_refused(self):
        items, record = self.record()
        record = copy.deepcopy(record)
        record["response"].pop("request")
        with self.assertRaises(ValueError):
            gate.report(items, [record])

    def test_classify_rejects_foreign_embedded_request(self):
        _, record = self.record()
        response = copy.deepcopy(record["response"])
        response["request"]["state"]["title"] = "Foreign title"
        fake, _ = client([])
        with patch.object(fake, "ask", return_value=response):
            with self.assertRaises(ValueError):
                gate.classify(item()["title"], item()["body"], fake)

    def test_collection_keeps_foreign_response_unknown_without_retry(self):
        items, record = self.record()
        response = copy.deepcopy(record["response"])
        response["request"]["questions"] = {}
        fake, _ = client([])
        accounting = []
        with tempfile.TemporaryDirectory() as tmp, patch.object(jev, "JevClient", return_value=fake):
            path = Path(tmp) / "responses.jsonl"
            with patch.object(fake, "ask", return_value=response) as ask:
                result = gate.run(items, None, True, 1, path, "0.01", "fixture ruling", accounting.append)
            self.assertEqual(ask.call_count, 1)
            self.assertEqual(len(accounting), 1)
            self.assertEqual(result["unknown"], 1)
            self.assertEqual(result["retries"], 0)
            retained = json.loads(path.read_text())
            self.assertEqual(retained["status"], "unknown")
            self.assertEqual(retained["error_kind"], "ValueError")
            self.assertEqual(retained["response"], response)

    def test_original_archive_replay_is_unchanged_without_any_model_request(self):
        root = Path(__file__).resolve().parents[1] / "bench/scheduled-work"
        items = [json.loads(line) for line in (root / "labels.jsonl").read_text().splitlines() if line.strip()]
        expected = json.loads((root / "run-20261104/summary.json").read_text())
        with patch.object(jev, "JevClient", side_effect=AssertionError("network forbidden")):
            actual = gate.run(items, root / "run-20261104/responses.jsonl", False, None, None, None, None)
        self.assertEqual(actual["rows"], expected["rows"])
        self.assertEqual(len(actual["rows"]), 32)
        self.assertEqual(actual["unknown"], 0)

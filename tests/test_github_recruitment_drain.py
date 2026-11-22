"""Offline verified-inbox controls. No live controller, network or key install."""
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import github_ingress as gi
import github_recruitment_drain as drain
from github_recruitment import Candidate


class DrainTests(unittest.TestCase):
    def setUp(self):
        self.spool=gi.Spool(':memory:')
        self.addCleanup(self.spool.conn.close)
        self.candidate=Candidate('intigraph/widget',42,'a'*40,'review','task-1')
        self.payload={'repository':{'full_name':'intigraph/widget'},
                      'pull_request':{'number':42,'head':{'sha':'a'*40}}}
        self.spawns=[]

    def add(self, delivery='event-1', *, verified=True, payload=None, now=1):
        return self.spool.add(delivery,'pull_request',self.payload if payload is None else payload,
                              verified_webhook=verified,now=now)

    def run_drain(self, spool=None, **options):
        args=dict(repos=frozenset({'intigraph/widget'}),resolve_ready=lambda e,p:self.candidate,
                  admit=lambda c:None,spawn=lambda c:self.spawns.append(c) or 'fixture-receipt',clock=lambda:2.)
        args.update(options)
        return drain.drain_once(spool or self.spool,**args)

    def test_verified_only_and_independent_of_graph_writer(self):
        self.add('poll',verified=False)
        self.assertEqual({},self.run_drain())
        self.add()
        self.assertEqual({'spawned':1},self.run_drain())
        self.assertEqual(2,self.spool.pending())
        self.assertEqual({},self.run_drain())
        self.add('later-delivery')
        self.assertEqual({'duplicate':1},self.run_drain())
        self.assertEqual(1,len(self.spawns))

    def test_not_ready_does_not_spend_candidate_key(self):
        self.add()
        self.assertEqual({'ignored':1},self.run_drain(resolve_ready=lambda e,p:None))
        self.assertEqual(0,self.spool.conn.execute('SELECT count(*) FROM recruitment_attempts').fetchone()[0])
        self.add('green-event')
        self.assertEqual({'spawned':1},self.run_drain())

    def test_old_foreign_stale_and_merge_never_admitted(self):
        self.add(now=0)
        self.assertEqual({'expired':1},self.run_drain(clock=lambda:drain.TWO_DAYS+1))
        self.add('foreign',payload={'repository':{'full_name':'outside/widget'}})
        self.assertEqual({'foreign':1},self.run_drain())
        self.add('stale')
        other=Candidate('intigraph/widget',42,'b'*40,'review','task-1')
        self.assertEqual({'stale':1},self.run_drain(resolve_ready=lambda e,p:other))
        self.add('merge')
        merge=Candidate('intigraph/widget',42,'a'*40,'merge','task-1')
        self.assertEqual({'merge-held':1},self.run_drain(resolve_ready=lambda e,p:merge))
        self.assertFalse(self.spawns)

    def test_callbacks_outside_receiver_lock_and_claim_committed(self):
        self.add()
        def resolver(event,payload):
            self.assertTrue(self.spool.lock.acquire(blocking=False))
            try:
                self.assertFalse(self.spool.conn.in_transaction)
                self.assertEqual('attempted',self.spool.conn.execute('SELECT state FROM recruitment_events').fetchone()[0])
            finally:self.spool.lock.release()
            return self.candidate
        def admit(candidate):
            self.add('arrived-during-admission')
            return None
        self.assertEqual({'spawned':1},self.run_drain(resolve_ready=resolver,admit=admit,batch=1))
        self.assertEqual(1,self.spool.recruitment_pending())

    def test_crash_keeps_both_claim_and_candidate_across_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'spool.db');spool=gi.Spool(path)
            spool.add('crash','pull_request',self.payload,verified_webhook=True,now=1)
            class Crash(BaseException):pass
            def lost(candidate):
                self.spawns.append(candidate);raise Crash()
            with self.assertRaises(Crash):self.run_drain(spool,spawn=lost)
            spool.conn.close();again=gi.Spool(path);self.addCleanup(again.conn.close)
            self.assertEqual({},self.run_drain(again))
            again.add('next','pull_request',self.payload,verified_webhook=True,now=1)
            self.assertEqual({'duplicate':1},self.run_drain(again))
            self.assertEqual(1,len(self.spawns))
            self.assertEqual('attempted',again.conn.execute('SELECT state FROM recruitment_attempts').fetchone()[0])

    def test_lost_reply_and_refusal_terminal_no_secret_reason(self):
        self.add()
        def lost(c):self.spawns.append(c);raise TimeoutError('secret should not persist')
        self.assertEqual({'unknown':1},self.run_drain(spawn=lost))
        self.add('retry')
        self.assertEqual({'duplicate':1},self.run_drain())
        reasons=str(self.spool.conn.execute('SELECT reason FROM recruitment_events').fetchall())
        reasons+=str(self.spool.conn.execute('SELECT reason FROM recruitment_attempts').fetchall())
        self.assertNotIn('secret',reasons)

    def test_two_consumers_one_candidate_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'spool.db');a=gi.Spool(path);b=gi.Spool(path)
            self.addCleanup(a.conn.close);self.addCleanup(b.conn.close)
            for name in ('one','two'):a.add(name,'pull_request',self.payload,verified_webhook=True,now=1)
            barrier=threading.Barrier(2)
            def resolve(e,p):barrier.wait(timeout=3);return self.candidate
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda s:self.run_drain(s,batch=1,resolve_ready=resolve),(a,b)))
            self.assertEqual([{'duplicate':1},{'spawned':1}],sorted(results,key=lambda r:sorted(r)[0]))
            self.assertEqual(1,len(self.spawns))

    def test_retention_clears_bodies_only_not_tombstones_or_keys(self):
        self.add();self.run_drain();self.add('pending')
        self.assertEqual(1,drain.purge_terminal_bodies(self.spool,now=drain.TWO_DAYS+2))
        self.assertEqual((b'', 'spawned'),self.spool.conn.execute("SELECT body,state FROM recruitment_events WHERE delivery='event-1'").fetchone())
        self.assertGreater(len(self.spool.conn.execute("SELECT body FROM recruitment_events WHERE delivery='pending'").fetchone()[0]),0)
        self.assertEqual(1,self.spool.conn.execute('SELECT count(*) FROM recruitment_attempts').fetchone()[0])
        self.assertEqual(0,drain.purge_terminal_bodies(self.spool,now=drain.TWO_DAYS+2))

    def test_redelivery_after_graph_retention_keeps_terminal_tombstone(self):
        self.add();self.run_drain()
        self.spool.conn.execute("DELETE FROM deliveries WHERE id='event-1'")
        self.assertEqual('duplicate',self.add())
        self.assertEqual({},self.run_drain())
        self.assertEqual(1,len(self.spawns))

    def test_refusal_is_terminal_and_free_text_is_not_retained(self):
        self.add()
        self.assertEqual({'refused':1},self.run_drain(admit=lambda c:'secret free-text refusal'))
        self.add('second-wake')
        self.assertEqual({'duplicate':1},self.run_drain())
        self.assertFalse(self.spawns)
        self.assertEqual('authority-refused',self.spool.conn.execute('SELECT reason FROM recruitment_attempts').fetchone()[0])

    def test_check_suite_wake_survives_redaction_without_payload_authority(self):
        payload={'repository':{'full_name':'intigraph/widget'},
                 'check_suite':{'head_sha':'a'*40,'status':'completed','conclusion':'success'}}
        self.spool.add('suite','check_suite',payload,verified_webhook=True,now=1)
        self.assertEqual({'spawned':1},self.run_drain())
        self.assertEqual(1,len(self.spawns))
        # A second suite on the same key cannot replay, even when it asserts success.
        self.spool.add('suite2','check_suite',payload,verified_webhook=True,now=1)
        self.assertEqual({'duplicate':1},self.run_drain())

    def test_policy_and_batch_bounds(self):
        for options in ({'repos':frozenset()}, {'repos':frozenset({'intigraph/..'})},
                        {'batch':True}, {'batch':51}, {'max_age':float('inf')}):
            with self.assertRaises(ValueError):self.run_drain(**options)
        self.add('one');self.add('two')
        self.assertEqual({'spawned':1},self.run_drain(batch=1))
        self.assertEqual(1,self.spool.recruitment_pending())

if __name__=='__main__':unittest.main()

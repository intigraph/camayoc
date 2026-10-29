import json
import tempfile
import unittest
from pathlib import Path
from test_seed_work_items import GRAPH,ID,SUBJECT
import seed_work_items as nw
import seed_change_custody as sc
import sqlite3

PEER='https://peer-one.example.test'
OTHER='https://peer-two.example.test'

def page(tx=10,subject=SUBJECT,**changes):
    result={'records':[{'graph':GRAPH,'tx':tx,'sequence':1,'entity':subject,
                       'attribute':nw.SCHEMA+'name','op':'assert'}],
            'next_tx':tx,'watermark_tx':tx,'capture':'new_values'}
    result.update(changes);return result

class Custody(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'journal.db'
        self.board=nw.Board(GRAPH,'demo');self.j=sc.Journal(self.path,self.board,server=PEER,initial_tx=9)
    def tearDown(self):self.j.db.close();self.tmp.cleanup()
    def test_atomic_retention_and_restart_pending_body(self):
        self.j.retain(page());self.assertEqual(self.j.cursor,10)
        self.j.prepare(ID,10,{'exact':'body'});self.j.db.close()
        self.j=sc.Journal(self.path,self.board,server=PEER)
        self.assertEqual(json.loads(self.j.next()[2]),{'exact':'body'})
        with self.assertRaises(sc.CustodyUnknown):self.j.prepare(ID,10,{'changed':'body'})
        self.j.acknowledge(ID,10,3);self.assertIsNone(self.j.next())
    def test_initial_cursor_explicit_no_reset_or_authority_swap(self):
        with self.assertRaises(sc.CustodyUnknown):sc.Journal(Path(self.tmp.name)/'new.db',self.board,server=PEER)
        with self.assertRaises(sc.CustodyUnknown):sc.Journal(self.path,self.board,server=PEER,initial_tx=0)
        with self.assertRaises(sc.CustodyUnknown):sc.Journal(self.path,nw.Board('urn:other','demo'),server=PEER)
    def test_changed_receiving_server_refused_and_custody_kept(self):
        self.j.retain(page());self.j.prepare(ID,10,{'exact':'body'});self.j.db.close()
        with self.assertRaises(sc.CustodyUnknown):sc.Journal(self.path,self.board,server=OTHER)
        for bad in ('',None,'  '):
            with self.subTest(server=bad),self.assertRaises(sc.CustodyUnknown):sc.Journal(self.path,self.board,server=bad)
        self.j=sc.Journal(self.path,self.board,server=PEER)
        self.assertEqual(self.j.cursor,10);self.assertEqual(json.loads(self.j.next()[2]),{'exact':'body'})
    def test_legacy_custody_without_server_is_refused_unchanged(self):
        self.j.db.close();legacy=Path(self.tmp.name)/'legacy.db';db=sqlite3.connect(legacy)
        db.execute('CREATE TABLE authority(graph TEXT,prefix TEXT,cursor INTEGER)')
        db.execute('CREATE TABLE pending(id TEXT PRIMARY KEY,tx INTEGER,body TEXT,effect_tx INTEGER,last_attempt REAL)')
        db.execute('INSERT INTO authority VALUES(?,?,?)',(GRAPH,'demo',10))
        db.execute('INSERT INTO pending VALUES(?,?,?,?,?)',(ID,10,'{"exact":"body"}',None,None));db.commit()
        before=list(db.iterdump());db.close()
        with self.assertRaises(sc.CustodyUnknown):sc.Journal(legacy,self.board,server=PEER)
        db=sqlite3.connect(legacy);self.assertEqual(list(db.iterdump()),before);db.close()
        self.j=sc.Journal(self.path,self.board,server=PEER)
    def test_empty_page_does_not_use_global_watermark(self):
        self.j.retain(page(records=[],next_tx=9,watermark_tx=1000))
        self.assertEqual(self.j.cursor,9)
        with self.assertRaises(sc.CustodyUnknown):self.j.retain(page(records=[],next_tx=1000,watermark_tx=1000))
        self.assertEqual(self.j.cursor,9)
    def test_comment_entity_maps_to_its_exact_parent(self):
        # aegis-ksd: a Comment entity is "<item>/comment/<n>"; it must not reject the page.
        comment=SUBJECT+'/comment/50'
        end,items=sc.candidates(page(subject=comment),self.board,9)
        self.assertEqual((end,items),(10,{ID:10}))
        self.j.retain(page(subject=comment));self.assertEqual(self.j.cursor,10)

    def test_ambiguous_comment_spellings_still_refuse(self):
        encoded=SUBJECT.replace(ID,ID.replace('-','%2D'))+'/comment/50'
        for bad in (encoded,):
            with self.subTest(ref=bad),self.assertRaises(sc.CustodyUnknown):
                sc.candidates(page(subject=bad),self.board,9)
        # Other sub-paths are not comments and keep the existing exact-ID refusal.
        for bad in (SUBJECT+'/comment/x',SUBJECT+'/note/1',SUBJECT+'/comment/1/2'):
            with self.subTest(ref=bad),self.assertRaises((sc.CustodyUnknown,nw.NativeUnknown)):
                sc.candidates(page(subject=bad),self.board,9)
        self.assertEqual(self.j.cursor,9)

    def test_bad_scope_partial_order_and_boundary_keep_cursor(self):
        for data in (page(truncated=True),page(records=[{**page()['records'][0],'graph':'ROOT'}]),
                     page(next_tx=11,watermark_tx=11),page(records=page()['records']*2),
                     page(subject=nw.ITEM+'demo-one%2E')):
            with self.subTest(data=data):
                with self.assertRaises((sc.CustodyUnknown,nw.NativeUnknown)):self.j.retain(data)
                self.assertEqual(self.j.cursor,9);self.assertIsNone(self.j.next())
    def test_feed_scope_ignores_other_rig_but_exact_comment_parent_routes(self):
        row=page()['records'][0]
        data=page(subject='urn:comment:one',records=[{**row,'entity':'urn:comment:one',
                  'attribute':nw.SCHEMA+'parentItem','value':{'ref':SUBJECT}}])
        self.j.retain(data);self.assertEqual(self.j.next()[0],ID)
    def test_new_candidate_during_pending_does_not_erase_body(self):
        self.j.retain(page());self.j.prepare(ID,10,{'old':3})
        self.j.retain(page(11));self.assertEqual(json.loads(self.j.next()[2]),{'old':3})
        self.assertEqual(self.j.next()[1],10)
        with self.assertRaises(sc.CustodyUnknown):self.j.acknowledge(ID,11,4)
        self.j.acknowledge(ID,10,3)
        self.assertEqual(self.j.next(),(ID,11,None))
    def test_pending_budget_rolls_back_entire_transaction(self):
        for n in range(sc.MAX_PENDING):self.j.db.execute('INSERT INTO pending(id,tx) VALUES(?,9)',('demo-'+str(n),))
        with self.assertRaises(sc.CustodyUnknown):self.j.retain(page())
        self.assertEqual(self.j.cursor,9)
        self.assertEqual(self.j.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0],sc.MAX_PENDING)
    def test_unproved_pending_effect_transaction_refuses_without_losing_body(self):
        self.j.retain(page());self.j.prepare(ID,10,{'old':3})
        stored=self.j.next()[2]
        for bad in (None,-1,'unproved'):
            with self.subTest(effect_tx=bad):
                self.j.db.execute('UPDATE pending SET effect_tx=? WHERE id=?',(bad,ID))
                with self.assertRaises(sc.CustodyUnknown):self.j.next()
                self.assertEqual(self.j.db.execute('SELECT body FROM pending WHERE id=?',(ID,)).fetchone()[0],stored)
    def test_unknown_item_does_not_starve_other_candidates(self):
        self.j.retain(page())
        self.j.mark_attempt(ID,100)
        other='demo-other'
        self.j.retain(page(11,subject=nw.ITEM+other))
        self.assertEqual(self.j.next()[0],other)
        self.j.mark_attempt(other,101)
        self.assertEqual(self.j.next()[0],ID)

    def test_source_version_regression_refuses(self):
        self.j.retain(page());self.j.prepare(ID,10,{'body':3});self.j.acknowledge(ID,10,3)
        self.j.retain(page(11));self.j.prepare(ID,11,{'body':2})
        with self.assertRaises(sc.CustodyUnknown):self.j.acknowledge(ID,11,2)
        self.assertIsNotNone(self.j.next()[2])

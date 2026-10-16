import json
import tempfile
import unittest
from pathlib import Path
from test_seed_work_items import GRAPH,ID,rows
from test_seed_change_custody import page
import seed_work_items as nw
import seed_change_custody as sc
import seed_work_item_sync as sync

class Tick(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'journal.db'
        self.reader=nw.Reader(lambda *args:None,nw.Board(GRAPH,'demo'))
        self.j=sc.Journal(self.path,self.reader.board,server='https://peer-one.example.test',initial_tx=9)
        self.writes=0;self.description=None;self.unavailable=False;self.drop=False
    def tearDown(self):self.j.db.close();self.tmp.cleanup()
    def post(self,endpoint,body):
        if endpoint=='/knot':
            self.writes+=1
            stored=json.loads(self.j.next()[2]);self.description=stored['description']
            if self.drop:raise TimeoutError('committed, response lost')
            return {'replaced':True,'tx_id':99,'conforms':True}
        if self.unavailable:raise TimeoutError('readback unavailable')
        query=body['query']
        if 'BIND(' in query:return {'rows':rows(),'truncated':False}
        if 'SELECT ?t' in query:return {'rows':[{'t':'aegis:WorkItem'}],'truncated':False}
        return {'rows':[{'v':self.description}] if self.description else [],'truncated':False}
    def tick(self):
        return sync.tick(self.reader,self.j,self.post,changes=lambda **args:page() if args['since']<10 else page(records=[],next_tx=args['since'],watermark_tx=args['since']),plane='urn:private:knowledge',actor='fixture')
    def test_full_tick_five_calls_one_effect_and_ack(self):
        # Full IRI control; CURIE expansion is namespace-specific.
        original=self.post
        def post(endpoint,body):
            value=original(endpoint,body)
            if 'SELECT ?t' in body.get('query',''):value['rows']=[{'t':'http://aegis.gastown.local/ontology/WorkItem'}]
            return value
        self.post=post
        result=self.tick()
        self.assertEqual((result['status'],result['writes'],result['requests'],result['verified']),('OK',1,5,1))
        self.assertIsNone(self.j.next())
    def test_commit_response_loss_freezes_without_resend_across_restart(self):
        self.drop=True;self.assertEqual(self.tick()['status'],'UNKNOWN')
        self.assertEqual(self.writes,1);self.assertIsNotNone(self.j.next()[2])
        self.j.db.close();self.j=sc.Journal(self.path,self.reader.board,server='https://peer-one.example.test')
        self.unavailable=True;self.assertEqual(self.tick()['status'],'UNKNOWN');self.assertEqual(self.writes,1)
        self.unavailable=False
        original=self.post
        def post(endpoint,body):
            value=original(endpoint,body)
            if 'SELECT ?t' in body.get('query',''):value['rows']=[{'t':'http://aegis.gastown.local/ontology/WorkItem'}]
            return value
        self.post=post
        self.assertEqual(self.tick()['verified'],1);self.assertEqual(self.writes,1)
    def test_same_source_version_repeated_candidate_is_verified_without_write(self):
        self.assertEqual(self.tick()['verified'],1)
        self.j.retain(page(11))
        result=self.tick()
        self.assertEqual((result['status'],result['writes'],result['verified']),('OK',0,1))
        self.assertEqual(self.writes,1)

    def test_unproven_control_never_acknowledges(self):
        original=self.post
        def bad_control(endpoint,body):
            result=original(endpoint,body)
            if 'SELECT ?t' in body.get('query',''):result['rows']=[{'t':'unknown:WorkItem'}]
            return result
        self.post=bad_control
        result=self.tick();self.assertEqual(result['status'],'UNKNOWN')
        self.assertIsNotNone(self.j.next()[2]);self.assertEqual(self.writes,1)
    def test_bad_native_identity_does_not_prepare_or_write(self):
        def wrong(endpoint,body):return {'rows':rows(**{nw.SCHEMA+'identifier':'demo-wrong'}),'truncated':False}
        self.post=wrong;result=self.tick();self.assertEqual(result['status'],'UNKNOWN')
        self.assertEqual(self.writes,0);self.assertIsNone(self.j.next()[2])


class SerialTicks(unittest.TestCase):
    """Up to MAX_DELIVERIES items per tick, inside one 20-request budget (aegis-ksd)."""
    ITEMS=['demo-a','demo-b','demo-c','demo-d','demo-e']
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'journal.db'
        self.reader=nw.Reader(lambda *args:None,nw.Board(GRAPH,'demo'))
        self.j=sc.Journal(self.path,self.reader.board,server='https://peer-one.example.test',initial_tx=9)
        self.writes=[];self.descriptions={};self.drop=set();self.calls=0
        self.j.retain({'records':[{'graph':GRAPH,'tx':10,'sequence':n+1,'entity':nw.ITEM+item,
                                   'attribute':nw.SCHEMA+'name','op':'assert'}
                                  for n,item in enumerate(self.ITEMS)],
                       'next_tx':10,'watermark_tx':10,'capture':'new_values'})
    def tearDown(self):self.j.db.close();self.tmp.cleanup()
    def item_of(self,query):
        return next(item for item in self.ITEMS if (item+'>') in query)
    def post(self,endpoint,body):
        self.calls+=1
        if endpoint=='/knot':
            item,stored=next((i,b) for i,b in self.j.db.execute('SELECT id,body FROM pending WHERE body IS NOT NULL')
                             if json.loads(b)['request']==body)
            self.writes.append(item);self.descriptions[item]=json.loads(stored)['description']
            if item in self.drop:raise TimeoutError('committed, response lost')
            return {'replaced':True,'tx_id':99,'conforms':True}
        query=body['query'];item=self.item_of(query)
        if 'BIND(' in query:
            found=rows(**{nw.SCHEMA+'identifier':item})
            return {'rows':[dict(row,s=nw.ITEM+item) for row in found],'truncated':False}
        if 'SELECT ?t' in query:return {'rows':[{'t':'aegis:WorkItem'}],'truncated':False}
        if item in self.drop:raise TimeoutError('readback unavailable')
        value=self.descriptions.get(item)
        return {'rows':[{'v':value}] if value else [],'truncated':False}
    def tick(self):
        idle=lambda **args:{'records':[],'next_tx':args['since'],'watermark_tx':args['since'],'capture':'new_values'}
        return sync.tick(self.reader,self.j,self.post,changes=idle,plane='urn:private:knowledge',actor='fixture')
    def backlog(self):return self.j.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0]

    def test_budget_constant_stays_within_the_acknowledged_ceiling(self):
        self.assertEqual(sync.TICK_REQUESTS,1+sync.MAX_DELIVERIES*sync.ITEM_REQUESTS)
        self.assertLessEqual(sync.TICK_REQUESTS,20)

    def test_one_tick_delivers_four_serially_then_the_rest_next_tick(self):
        result=self.tick()
        self.assertEqual((result['status'],result['verified'],result['writes'],result['backlog']),('OK',4,4,1))
        self.assertEqual(result['requests'],1+4*4)
        self.assertEqual(self.calls,4*4)  # the feed read is the injected changes callable
        self.assertEqual(len(set(self.writes)),4)
        result=self.tick()
        self.assertEqual((result['status'],result['verified'],result['writes'],result['backlog']),('OK',1,1,0))
        self.assertEqual(sorted(self.writes),self.ITEMS)

    def test_first_doubt_stops_the_tick_and_keeps_earlier_acknowledgements(self):
        first=self.j.next()[0]
        second=self.ITEMS[self.ITEMS.index(first)+1]
        self.drop.add(second)
        result=self.tick()
        self.assertEqual((result['status'],result['verified'],result['writes']),('UNKNOWN',1,2))  # 2nd sent, response lost
        self.assertEqual(self.writes,[first,second])  # no third item after the doubt
        self.assertIsNone(self.j.db.execute('SELECT 1 FROM pending WHERE id=?',(first,)).fetchone())
        self.assertIsNotNone(self.j.db.execute('SELECT body FROM pending WHERE id=?',(second,)).fetchone()[0])
        self.assertEqual(self.backlog(),4)

    def test_a_preserved_effect_is_never_resent_by_a_later_serial_tick(self):
        self.drop.add(self.ITEMS[0])
        lost=self.tick()
        self.assertEqual((lost['status'],lost['writes'],lost['verified']),('UNKNOWN',1,0))  # lost response still counted
        self.assertEqual(self.writes,[self.ITEMS[0]])
        self.drop.clear()
        for _ in range(3):
            result=self.tick()
            self.assertLessEqual(result['requests'],sync.TICK_REQUESTS)
        self.assertEqual(self.writes.count(self.ITEMS[0]),1)  # verified from custody, never resent
        self.assertEqual(sorted(self.writes),self.ITEMS)
        self.assertEqual(self.backlog(),0)


class SlowReadStop(SerialTicks):
    """A request over READ_BUDGET_SECONDS ends the tick after the item in flight."""
    def setUp(self):
        super().setUp();self.now=0.0;self.slow_on=set()
    def clock(self):return self.now
    def post(self,endpoint,body):
        # Each request advances the clock; a chosen request number is slow.
        self.now+=5.0 if self.calls+1 in self.slow_on else 0.01
        return super().post(endpoint,body)
    def tick(self,feed_seconds=0.01):
        def feed(**args):
            self.now+=feed_seconds
            return {'records':[],'next_tx':args['since'],'watermark_tx':args['since'],'capture':'new_values'}
        return sync.tick(self.reader,self.j,self.post,changes=feed,plane='urn:private:knowledge',
                         actor='fixture',clock=self.clock)

    def test_fast_reads_keep_four_per_tick(self):
        result=self.tick()
        self.assertEqual((result['status'],result['verified']),('OK',4))
        self.assertNotIn('slow_read',result)

    def test_slow_read_finishes_the_item_in_flight_then_stops(self):
        self.slow_on={2}  # the first item's /knot
        result=self.tick()
        self.assertEqual((result['status'],result['verified'],result['writes']),('OK',1,1))
        self.assertGreater(result['slow_read'],sync.READ_BUDGET_SECONDS)
        self.assertEqual(self.backlog(),4)
        self.assertEqual(self.j.db.execute('SELECT COUNT(*) FROM pending WHERE body IS NOT NULL').fetchone()[0],0)

    def test_slow_feed_still_attempts_one_item(self):
        result=self.tick(feed_seconds=5.0)
        self.assertEqual((result['status'],result['verified']),('OK',1))
        self.assertIn('slow_read',result)

    def test_slow_read_on_a_later_item_keeps_earlier_ones(self):
        self.slow_on={sync.ITEM_REQUESTS+1}  # second item's native read
        result=self.tick()
        self.assertEqual((result['status'],result['verified']),('OK',2))
        self.assertEqual(self.backlog(),3)

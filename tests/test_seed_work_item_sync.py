import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
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
        if query.startswith('SELECT ?has_type ?v '):
            return {'rows':[dict(has_type=True,**({'v':self.description} if self.description else {}))],'truncated':False}
        if 'BIND(' in query:return {'rows':rows(),'truncated':False}
        if query.startswith('ASK '):return {'result':True}
        return {'rows':[{'v':self.description}] if self.description else [],'truncated':False}
    def tick(self):
        return sync.tick(self.reader,self.j,self.post,changes=lambda **args:page() if args['since']<10 else page(records=[],next_tx=args['since'],watermark_tx=args['since']),plane='urn:private:knowledge',actor='fixture')
    def test_pending_completion_summary_is_receipt_only_without_extra_calls(self):
        self.drop=True
        self.assertEqual(self.tick()['status'], 'UNKNOWN')
        pending=self.j.next()
        envelope=json.loads(pending[2]);envelope['write_id']='a'*64
        self.j.db.execute('UPDATE pending SET body=? WHERE id=?', (json.dumps(envelope),pending[0]))
        self.j.db.commit()
        frozen=self.j.next()
        calls=[]
        def post(route,body):
            calls.append(route)
            if route=='/knot/status':
                return {'contract':'quipu.knot-completion/v1','write_id':'a'*64,'state':'unknown'}
            return {'result':False}
        result=sync.tick(self.reader,self.j,post,changes=None,plane='urn:private:knowledge',actor='fixture')
        self.assertEqual((result['status'],result['writes'],result['verified'],result['requests']),('UNKNOWN',0,0,2))
        self.assertEqual(calls,['/query','/knot/status'])
        self.assertEqual(result['completion_disposition']['state'],'unknown')
        self.assertEqual(self.j.next(),frozen)
        self.assertEqual(self.writes,1)
    def test_full_tick_five_calls_one_effect_and_ack(self):
        # The control tests the full IRI without consuming compact SELECT terms.
        original=self.post
        def post(endpoint,body):
            value=original(endpoint,body)
            if body.get('query','').startswith('ASK '):value={'result':True}
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
            if body.get('query','').startswith('ASK '):value={'result':True}
            return value
        self.post=post
        self.assertEqual(self.tick()['verified'],1);self.assertEqual(self.writes,1)
    def test_same_source_version_repeated_candidate_is_verified_without_write(self):
        self.assertEqual(self.tick()['verified'],1)
        self.j.retain(page(11))
        result=self.tick()
        self.assertEqual((result['status'],result['writes'],result['verified']),('OK',0,1))
        self.assertEqual(self.writes,1)
        self.assertEqual(result['requests'],3)
        self.assertNotIn('control',result)
        self.assertEqual(result['fused_control'],dict(schema='workitem-fused-readback/v1',shape='object',result='missing',truncated='false',type_witness='true',description='one'))

    def test_new_feed_transaction_keeps_lost_response_effect_custody(self):
        self.drop=True
        self.assertEqual(self.tick()['status'],'UNKNOWN')
        self.j.retain(page(11))
        original_body=self.j.next()[2]
        self.j.db.close()
        self.j=sc.Journal(self.path,self.reader.board,server='https://peer-one.example.test')
        self.drop=False
        self.unavailable=True
        self.assertEqual(self.tick()['status'],'UNKNOWN')
        self.assertEqual(self.j.next()[2],original_body)
        self.assertEqual(self.writes,1)
        self.unavailable=False
        result=self.tick()
        self.assertEqual((result['status'],result['writes'],result['verified']),('OK',0,1))
        # The old effect was read back, not resent; the newer candidate survives.
        self.assertEqual(self.j.next(),(ID,11,None))
        self.assertEqual(self.writes,1)
        self.assertEqual(self.tick()['verified'],1)
        self.assertIsNone(self.j.next())
        self.assertEqual(self.writes,1)

    def test_unproven_control_never_acknowledges(self):
        original=self.post
        def bad_control(endpoint,body):
            result=original(endpoint,body)
            if body.get('query','').startswith('ASK '):result={'result':False}
            return result
        self.post=bad_control
        result=self.tick();self.assertEqual(result['status'],'UNKNOWN')
        self.assertIsNotNone(self.j.next()[2]);self.assertEqual(self.writes,1)
    def test_control_shapes_remain_unknown_and_payload_free_without_resend(self):
        cases=[({'result':False},'object','false','absent'),
               ({},'object','missing','absent'),
               ({'result':1},'object','nonboolean','absent'),
               ({'result':True,'truncated':True},'object','true','true'),
               ({'result':True,'truncated':'must-not-appear'},'object','true','nonboolean'),
               ([{'result':True}],'array','missing','absent')]
        original=self.post
        body=None
        for control,shape,result,truncated in cases:
            with self.subTest(control=control):
                def post(endpoint,request):
                    if request.get('query','').startswith('ASK '):return control
                    return original(endpoint,request)
                self.post=post
                receipt=self.tick()
                self.assertEqual(receipt['status'],'UNKNOWN',receipt)
                self.assertEqual(receipt['error'],'CustodyUnknown')
                self.assertNotIn('must-not-appear',json.dumps(receipt))
                self.assertEqual(receipt['attempted_item'],ID)
                self.assertEqual(receipt['control'],dict(shape=shape,result=result,truncated=truncated))
                current=self.j.next()[2]
                if body is None:body=current
                self.assertEqual(current,body)
                self.assertEqual(self.writes,1)
                self.assertEqual(self.j.cursor,10)

    def test_bad_native_identity_does_not_prepare_or_write(self):
        def wrong(endpoint,body):return {'rows':rows(**{nw.SCHEMA+'identifier':'demo-wrong'}),'truncated':False}
        self.post=wrong;result=self.tick();self.assertEqual(result['status'],'UNKNOWN')
        self.assertEqual(self.writes,0);self.assertIsNone(self.j.next()[2])


class SerialTicks(unittest.TestCase):
    """Up to MAX_DELIVERIES items per tick, inside one 20-request budget (aegis-ksd)."""
    ITEMS=['demo-a','demo-b','demo-c','demo-d','demo-e']
    #: These serial-semantics tests keep the aegis-ksd budget (one page, four
    #: deliveries, 17 requests) so their expectations stay meaningful. The raised
    #: aegis-etv defaults are tested by Throughput below.
    LEGACY={'MAX_DELIVERIES':4,'MAX_FEED_PAGES':1,'TICK_REQUESTS':1+4*4}
    def setUp(self):
        patcher=mock.patch.multiple(sync,**self.LEGACY);patcher.start();self.addCleanup(patcher.stop)
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
        if query.startswith('SELECT ?has_type ?v '):
            if item in self.drop:raise TimeoutError('readback unavailable')
            value=self.descriptions.get(item)
            return {'rows':[dict(has_type=True,**({'v':value} if value else {}))],'truncated':False}
        if 'BIND(' in query:
            found=rows(**{nw.SCHEMA+'identifier':item})
            return {'rows':[dict(row,s=nw.ITEM+item) for row in found],'truncated':False}
        if query.startswith('ASK '):return {'result':True}
        if item in self.drop:raise TimeoutError('readback unavailable')
        value=self.descriptions.get(item)
        return {'rows':[{'v':value}] if value else [],'truncated':False}
    def tick(self):
        idle=lambda **args:{'records':[],'next_tx':args['since'],'watermark_tx':args['since'],'capture':'new_values'}
        return sync.tick(self.reader,self.j,self.post,changes=idle,plane='urn:private:knowledge',actor='fixture')
    def backlog(self):return self.j.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0]

    def test_legacy_budget_is_pinned_for_these_serial_tests(self):
        self.assertEqual((sync.MAX_DELIVERIES,sync.MAX_FEED_PAGES,sync.TICK_REQUESTS),(4,1,17))

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

    def test_control_doubt_names_attempted_item_without_losing_prior_success(self):
        first,second=self.ITEMS[:2]
        original=self.post
        def post(endpoint,body):
            result=original(endpoint,body)
            if body.get('query','').startswith('ASK ') and self.item_of(body['query'])==second:
                return {'result':False,'private-response':'must-not-appear'}
            return result
        self.post=post
        result=self.tick()
        self.assertEqual((result['status'],result['item'],result['attempted_item']),('UNKNOWN',first,second))
        self.assertEqual(result['control'],{'shape':'object','result':'false','truncated':'absent'})
        self.assertNotIn('must-not-appear',json.dumps(result))
        self.assertEqual((result['requests'],result['writes'],result['verified']),(8,2,1))
        self.assertEqual(self.writes,[first,second])
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


class PlaneError(RuntimeError):
    """Stands in for planes.PlaneError, matched by class name as in production."""


class DiagnosticReceipt(SerialTicks):
    """A failed tick says why, without payload or auth (aegis-wu1s9ti4d)."""
    def setUp(self):
        super().setUp();self.partial=set();self.plane_error=None
    def post(self,endpoint,body):
        if endpoint=='/query' and not body['query'].startswith('ASK ') and 'BIND(' not in body['query']:
            item=self.item_of(body['query'])
            if self.plane_error and item==self.plane_error:
                raise PlaneError("/query failed: HTTP 408 b'secret-bearer-xyz body'")
            if item in self.partial:return {'rows':[],'truncated':True}
        return super().post(endpoint,body)

    def test_a_custody_doubt_names_its_reason_and_the_readback_it_rests_on(self):
        self.partial.add(self.j.next()[0])
        result=self.tick()
        self.assertEqual((result['status'],result['error'],result['reason']),('UNKNOWN','CustodyUnknown','metadata readback partial'))
        self.assertEqual(result['last_request']['endpoint'],'/query')
        self.assertIsInstance(result['last_request']['seconds'],float)
        self.assertIn('slowest_read',result)

    def test_a_plane_error_is_reduced_to_a_fixed_class_and_never_logs_its_body(self):
        self.plane_error=self.j.next()[0]
        result=self.tick()
        self.assertEqual((result['status'],result['error'],result['reason']),('UNKNOWN','PlaneError','HTTP 408'))
        self.assertNotIn('secret-bearer-xyz',json.dumps(result))

    def test_any_other_error_keeps_only_its_type(self):
        self.drop.add(self.j.next()[0])
        result=self.tick()
        self.assertEqual((result['status'],result['error']),('UNKNOWN','TimeoutError'))
        self.assertNotIn('reason',result)
        self.assertNotIn('readback unavailable',json.dumps(result))
        self.assertIn('last_request',result)

    def test_a_clean_tick_records_its_slowest_read_and_no_failure_fields(self):
        result=self.tick()
        self.assertEqual(result['status'],'OK')
        self.assertIsInstance(result['slowest_read'],float)
        self.assertNotIn('reason',result);self.assertNotIn('last_request',result)

    def test_plane_error_classes_are_bounded(self):
        for text,expected in (("x unreachable: timed out — this is not evidence",'unreachable'),
                              ('Response exceeds explicit byte budget','response budget'),
                              ('Unreadable or invalid bearer credential; help','auth refused'),
                              ('something new','unclassified')):
            self.assertEqual(sync.failure_reason(PlaneError(text)),expected)
        self.assertIsNone(sync.failure_reason(ValueError('payload text')))


class ExactTypeControl(unittest.TestCase):
    def envelope(self):
        return {'request':{'graph':'urn:private:knowledge'},'description':'exact description'}

    def test_false_missing_partial_and_nonboolean_controls_preserve_pending(self):
        for control in ({'result':False}, {}, {'result':1}, {'result':'true'},
                        {'result':True,'truncated':True}, {'rows':[{'t':'a:WorkItem'}]},
                        {'rows':[{'t':'unknown:WorkItem'}]}, None):
            with self.subTest(control=control):
                calls=[]
                def post(endpoint,body):
                    calls.append((endpoint,body));return control
                with self.assertRaises(sc.CustodyUnknown):
                    sync.verify(post,self.envelope(),ID)
                self.assertEqual(len(calls),1)
                self.assertEqual(calls[0][0],'/query')

    def test_exact_type_is_namespace_specific_with_actual_rdf(self):
        from rdflib import Dataset, URIRef, RDF
        for term,expected in ((f'<{sync.projection.A}WorkItem>',True),
                              ('a:WorkItem',True),('aegis:WorkItem',True),
                              ('other:WorkItem',False),('a:OtherClass',False),
                              ('a:WorkItems',False),('<unknown:WorkItem>',False)):
            with self.subTest(term=term):
                data=Dataset();g=data.graph(URIRef('urn:private:knowledge'))
                g.parse(data=(f'@prefix a: <{sync.projection.A}> . '
                              f'@prefix aegis: <{sync.projection.A}> . '
                              '@prefix other: <https://example.test/other/> . '
                              f'<{sync.projection.A+ID}> a {term} .'),format='turtle')
                calls=[]
                def post(endpoint,body):
                    self.assertEqual(endpoint,'/query');calls.append(body['query'])
                    if body['query'].startswith('ASK '):
                        return {'result':bool(data.query(body['query']).askAnswer)}
                    return {'rows':[{'v':'exact description'}],'truncated':False}
                try:
                    if expected:
                        self.assertTrue(sync.verify(post,self.envelope(),ID))
                        self.assertEqual(len(calls),2)
                    else:
                        with self.assertRaises(sc.CustodyUnknown):sync.verify(post,self.envelope(),ID)
                        self.assertEqual(len(calls),1)
                finally:data.close()


class Throughput(unittest.TestCase):
    """aegis-etv: the raised defaults (pages, deliveries, wall clock, cursor age)."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'journal.db'
        self.reader=nw.Reader(lambda *args:None,nw.Board(GRAPH,'demo'))
        self.j=sc.Journal(self.path,self.reader.board,server='https://peer-one.example.test',initial_tx=9)
        self.now=0.0;self.feed_calls=0
    def tearDown(self):self.j.db.close();self.tmp.cleanup()
    def clock(self):return self.now
    def feed(self,head,per_page=4,full_at=None):
        """A feed with transactions 10..head, one item per transaction."""
        def changes(**args):
            self.feed_calls+=1;since=args['since']
            if full_at is not None and self.feed_calls==full_at:
                return {'records':[{'graph':GRAPH,'tx':tx,'sequence':1,'entity':nw.ITEM+'demo-%d'%tx,
                                    'attribute':nw.SCHEMA+'name','op':'assert'}
                                   for tx in range(since+1,since+1+per_page)],
                        'next_tx':since+per_page,'watermark_tx':head,'capture':'new_values','_full':True}
            end=min(head,since+per_page)
            records=[{'graph':GRAPH,'tx':tx,'sequence':1,'entity':nw.ITEM+'demo-%d'%tx,
                      'attribute':nw.SCHEMA+'name','op':'assert'} for tx in range(since+1,end+1)]
            return {'records':records,'next_tx':end,'watermark_tx':head,'capture':'new_values'}
        return changes
    def tick(self,changes,**kw):
        # No deliveries: posts are never reached when pending items cannot resolve,
        # so these tests measure the feed alone; deliveries are covered above.
        return sync.tick(self.reader,self.j,lambda *a:(_ for _ in ()).throw(AssertionError('no post')),
                         changes=changes,plane='urn:private:knowledge',actor='fixture',clock=self.clock,**kw)

    def test_budget_formula_and_ceiling(self):
        self.assertEqual(sync.TICK_REQUESTS,sync.MAX_FEED_PAGES+1+sync.MAX_DELIVERIES*sync.ITEM_REQUESTS)
        self.assertLessEqual(sync.TICK_REQUESTS,60)
        self.assertLess(sync.TICK_SECONDS,60)

    def test_measured_inflow_is_below_capacity_so_lag_shrinks(self):
        # aegis-etv measurement, seeds graph, 10:55-11:45Z 2026-11-04: 10.0 tx/min
        # and 256 distinct items in 50 min (about 307/h). A one-minute tick must
        # out-run both, or the cursor falls behind without bound (it was 15 days).
        self.assertGreater(sync.MAX_FEED_PAGES*sc.MAX_TRANSACTIONS,10.0*2)
        self.assertGreater(sync.MAX_DELIVERIES*60,307*2)

    def test_reads_several_pages_and_advances_the_cursor(self):
        with mock.patch.object(sync,'MAX_DELIVERIES',0):
            result=self.tick(self.feed(head=9+4*20))
        self.assertEqual(self.feed_calls,sync.MAX_FEED_PAGES)
        self.assertEqual(self.j.cursor,9+4*sync.MAX_FEED_PAGES)
        # Absolute, not relative to the constant: one tick must drain several pages.
        self.assertGreaterEqual(self.feed_calls,4)
        self.assertNotIn('feed_error',result)

    def test_stops_when_caught_up(self):
        with mock.patch.object(sync,'MAX_DELIVERIES',0):
            self.tick(self.feed(head=9+4*2+2))
        self.assertEqual(self.j.cursor,9+4*2+2)
        self.assertEqual(self.feed_calls,3)

    def test_later_page_custody_full_stops_quietly_keeping_earlier_pages(self):
        first=self.feed(head=9+4*20)
        def changes(**args):
            if self.feed_calls>=2:
                self.feed_calls+=1;raise sc.CustodyFull('pending custody is full; keep prior cursor')
            return first(**args)
        with mock.patch.object(sync,'MAX_DELIVERIES',0):
            result=self.tick(changes)
        self.assertEqual(self.j.cursor,9+4*2)
        self.assertNotIn('feed_error',result)
        self.assertEqual(result['status'],'OK')

    def test_feed_stops_after_half_the_wall_clock_budget(self):
        base=self.feed(head=9+4*20)
        def changes(**args):
            self.now+=sync.TICK_SECONDS/2+1;return base(**args)
        with mock.patch.object(sync,'MAX_DELIVERIES',0):
            self.tick(changes)
        self.assertEqual(self.feed_calls,1)

    def test_cursor_age_is_zero_only_when_the_feed_proves_caught_up(self):
        # aegis-etv review (arnold): the cursor only advances on board records,
        # so a caught-up lane on a quiet board has an OLD cursor transaction.
        # Reading time(cursor) there would age forever and false-fire the alarm.
        old=lambda tx:1000.0
        with mock.patch.object(sync,'MAX_DELIVERIES',0):
            # Caught up: the journal is at 9 and the feed has nothing newer.
            result=self.tick(self.feed(head=9),cursor_time=old,wall=lambda:4600.0)
        self.assertEqual(result['cursor_age_seconds'],0)
        # The production shape: a graph-filtered empty page answers next_tx == since
        # while watermark_tx is GLOBAL and far ahead (other graphs kept writing).
        quiet=lambda **a:{'records':[],'next_tx':a['since'],'watermark_tx':a['since']+5000,
                          'capture':'new_values'}
        with mock.patch.object(sync,'MAX_DELIVERIES',0):
            result=self.tick(quiet,cursor_time=old,wall=lambda:4600.0)
        self.assertEqual(result['cursor_age_seconds'],0)
    def test_cursor_age_is_reported_while_lagging_and_omitted_when_unproved(self):
        # Lagging: more board transactions than one tick's pages can read.
        with mock.patch.object(sync,'MAX_DELIVERIES',0),mock.patch.object(sync,'MAX_FEED_PAGES',1):
            result=self.tick(self.feed(head=200),cursor_time=lambda tx:1000.0,wall=lambda:4600.0)
        self.assertEqual(result['cursor_age_seconds'],3600)
        def broken(tx):raise ValueError('cursor transaction not found')
        with mock.patch.object(sync,'MAX_DELIVERIES',0),mock.patch.object(sync,'MAX_FEED_PAGES',1):
            result=self.tick(self.feed(head=200),cursor_time=broken,wall=lambda:4600.0)
        self.assertNotIn('cursor_age_seconds',result)
        self.assertEqual(result['cursor_age_error'],'ValueError')
    def test_cursor_age_is_never_zero_after_a_feed_error(self):
        # A failed feed proves nothing about being caught up: no 0, ever.
        def failing(**a):raise OSError('feed unreachable')
        with mock.patch.object(sync,'MAX_DELIVERIES',0):
            result=self.tick(failing,cursor_time=lambda tx:1000.0,wall=lambda:4600.0)
        self.assertNotEqual(result.get('cursor_age_seconds'),0)


class AdaptiveFeed(unittest.TestCase):
    """Whole pages shrink under pressure; custody and all tick budgets stay fixed."""
    setUp=Throughput.setUp
    tearDown=Throughput.tearDown
    clock=Throughput.clock
    tick=Throughput.tick

    def seed(self,count):
        self.j.retain(self.page(10,10,count,'old'))

    def page(self,start,end,items_per_tx,prefix='new'):
        return dict(records=[dict(graph=GRAPH,tx=tx,sequence=i,
                    entity=nw.ITEM+f'demo-{prefix}-{tx}-{i}',
                    attribute=nw.SCHEMA+'name',op='assert')
                    for tx in range(start,end+1) for i in range(items_per_tx)],
                    next_tx=end,watermark_tx=14,capture='new_values')

    def test_measured_171_plus_100_falls_back_without_replacing_exact_effect(self):
        self.seed(171)
        self.j.prepare('demo-old-10-0',10,{'retained':'exact pending effect'})
        before=self.j.db.execute('SELECT body,effect_tx FROM pending WHERE body IS NOT NULL').fetchall()
        calls=[]
        def changes(**args):
            calls.append((args['since'],args['limit']))
            return self.page(args['since']+1,min(14,args['since']+args['limit']),25)
        with mock.patch.object(sync,'MAX_DELIVERIES',0):result=self.tick(changes)
        self.assertEqual(calls,[(10,4),(10,2),(12,2),(12,1),(13,1)])
        self.assertEqual(self.j.cursor,13)
        self.assertEqual(self.j.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0],246)
        self.assertEqual(self.j.db.execute('SELECT body,effect_tx FROM pending WHERE body IS NOT NULL').fetchall(),before)
        self.assertEqual((result['status'],result['writes'],result['requests']),('OK',0,5))

    def test_single_transaction_over_cap_never_advances_or_splits(self):
        calls=[]
        def changes(**args):
            calls.append((args['since'],args['limit']))
            return self.page(10,10,sc.MAX_PENDING+1)
        with mock.patch.object(sync,'MAX_DELIVERIES',0):result=self.tick(changes)
        self.assertEqual(calls,[(9,4),(9,2),(9,1)])
        self.assertEqual(self.j.cursor,9)
        self.assertEqual(self.j.db.execute('SELECT COUNT(*) FROM pending').fetchone()[0],0)
        self.assertEqual((result['status'],result['error'],result['writes']),('UNKNOWN','CustodyFull',0))

    def test_retries_consume_existing_feed_read_budget_and_remain_unknown(self):
        self.seed(171);calls=[]
        def changes(**args):
            calls.append(args['limit']);return self.page(11,11,100)
        with mock.patch.object(sync,'MAX_DELIVERIES',0),mock.patch.object(sync,'MAX_FEED_PAGES',2):
            result=self.tick(changes)
        self.assertEqual(calls,[4,2])
        self.assertEqual(self.j.cursor,10)
        self.assertEqual((result['requests'],result['status'],result['feed_error']),(2,'UNKNOWN','CustodyFull'))

    def test_slow_overflow_does_not_start_another_feed_read(self):
        self.seed(171);calls=[]
        def changes(**args):
            calls.append(args['limit']);self.now+=sync.TICK_SECONDS/2+1
            return self.page(11,11,100)
        with mock.patch.object(sync,'MAX_DELIVERIES',0):result=self.tick(changes)
        self.assertEqual(calls,[4]);self.assertEqual(self.j.cursor,10)
        self.assertEqual((result['status'],result['feed_error']),('UNKNOWN','CustodyFull'))

    def test_malformed_retry_is_not_capacity_or_success(self):
        self.seed(171);calls=[]
        def changes(**args):
            calls.append(args['limit']);p=self.page(11,11,100)
            if len(calls)>1:p['truncated']=True
            return p
        with mock.patch.object(sync,'MAX_DELIVERIES',0):result=self.tick(changes)
        self.assertEqual(calls,[4,2]);self.assertEqual(self.j.cursor,10)
        self.assertEqual((result['status'],result['feed_error'],result['writes']),('UNKNOWN','CustodyUnknown',0))

    def test_delivery_uncertainty_stays_unknown_after_successful_fallback(self):
        self.seed(171)
        self.j.prepare('demo-old-10-0',10,{'retained':'exact pending effect'})
        before=self.j.db.execute('SELECT body,effect_tx FROM pending WHERE body IS NOT NULL').fetchall()
        def changes(**args):return self.page(args['since']+1,min(14,args['since']+args['limit']),25)
        with mock.patch.object(sync,'deliver',side_effect=sc.CustodyUnknown('readback uncertain')):
            result=self.tick(changes)
        self.assertEqual(self.j.cursor,13)
        self.assertEqual((result['status'],result['error'],result['writes']),('UNKNOWN','CustodyUnknown',0))
        self.assertEqual(self.j.db.execute('SELECT body,effect_tx FROM pending WHERE body IS NOT NULL').fetchall(),before)

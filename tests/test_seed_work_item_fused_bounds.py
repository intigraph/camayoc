"""arming: ci/library -- spare requests do not raise logical admission bounds."""
import contextlib
import unittest
import seed_work_item_sync as sync
import seed_change_custody as custody
import seed_work_items as native


class FusedBounds(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self):
        from test_seed_work_item_sync import SlowReadStop
        cls=type('FusedFixture',(SlowReadStop,),dict(
            ITEMS=[f'demo-{i:02d}' for i in range(20)],
            LEGACY={'MAX_DELIVERIES':12,'MAX_FEED_PAGES':8,'TICK_REQUESTS':57}))
        fixture=cls('test_slow_read_finishes_the_item_in_flight_then_stops')
        fixture.setUp()
        try:
            for item in fixture.ITEMS:
                canonical=native.Reader(fixture.post,fixture.reader.board).get(item)
                fixture.descriptions[item]=sync.projection.description(canonical)
                fixture.j.db.execute('INSERT INTO verified VALUES(?,?)',(item,canonical.version))
            fixture.j.db.commit();fixture.calls=0;fixture.now=0
            yield fixture
        finally:fixture.tearDown();fixture.doCleanups()

    def test_freed_requests_do_not_admit_a_thirteenth_same_version_item(self):
        with self.fixture() as fixture:
            self.assertEqual((sync.MAX_DELIVERIES,sync.TICK_REQUESTS,sync.TICK_SECONDS,custody.MAX_PENDING,sync.READ_BUDGET_SECONDS),(12,57,40,256,2.5))
            receipt=fixture.tick()
            self.assertEqual((receipt['status'],receipt['verified'],receipt['writes'],receipt['backlog']),('OK',12,0,8))
            self.assertEqual(receipt['requests'],25)  # one feed + twelve fresh canonical/fused pairs
            self.assertLess(receipt['requests'],sync.TICK_REQUESTS)
            self.assertEqual(fixture.writes,[])

    def test_slow_combined_read_finishes_one_item_then_stops(self):
        with self.fixture() as fixture:
            fixture.slow_on={2}  # first canonical read, then the combined proof
            receipt=fixture.tick()
            self.assertEqual((receipt['status'],receipt['verified'],receipt['writes'],receipt['backlog']),('OK',1,0,19))
            self.assertGreater(receipt['slow_read'],2.5)
            self.assertEqual(receipt['requests'],3)
            self.assertNotIn('control',receipt)
            self.assertEqual(receipt['fused_control']['schema'],'workitem-fused-readback/v1')

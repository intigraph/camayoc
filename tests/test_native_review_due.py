"""Native authority overrides retired observations without erasing other ages."""
import datetime as dt
import unittest
from test_review_due import Store
from test_seed_work_items import rows, GRAPH, ID
import review_due as rd
import seed_work_items as nw

NOW=dt.datetime(2026,10,12,tzinfo=dt.timezone.utc)

class NativeReview(unittest.TestCase):
    def judge(self,status='deferred',extra=(),failed=False):
        legacy=Store([(ID,'identifier',f'"{ID}"'),(ID,'idleLimit','"P3D"'),
                      (ID,'ownedBy','old-owner'),(ID,'observes','old'),
                      ('old','observedAt','"2026-09-01T00:00:00Z"'),
                      ('old','observedStatus','"closed"'),*extra])
        def post(endpoint,body):
            if 'BIND(' in body.get('query',''):
                if failed:raise TimeoutError('unavailable native authority')
                return {'rows':rows(**{nw.SEEDS+'status':status}),'truncated':False}
            if 'ownedBy' in body.get('query',''):
                raise AssertionError('native authority must not consult retired owner')
            return legacy.post(endpoint,body)
        return rd.judge(post,ID,NOW,native_reader=nw.Reader(post,nw.Board(GRAPH,'demo')))

    def test_deferred_and_closed_resolve_only_idle(self):
        for status in ('deferred','closed'):
            result=self.judge(status)
            self.assertEqual((result['verdict'],result['owner']),('NOT_DUE','worker'))
            self.assertEqual(result['native_source']['version'],3)

    def test_native_open_uses_native_activity_not_retired_closed(self):
        result=self.judge('open')
        self.assertEqual(result['verdict'],'NOT_DUE')
        self.assertIn('2026-10-14',result['due_at'])

    def test_failure_is_unknown_even_when_retired_status_closed(self):
        result=self.judge(failed=True)
        self.assertEqual((result['verdict'],result['owner']),('UNKNOWN',None))
        self.assertEqual(result['native_authority'],'UNKNOWN')

    def test_foreign_identifier_on_this_boards_canonical_item_is_unknown(self):
        legacy=Store([(ID,'identifier','"other-one"'),(ID,'idleLimit','"P3D"'),
                      (ID,'observes','old'),('old','observedAt','"2026-09-01T00:00:00Z"'),
                      ('old','observedStatus','"closed"')])
        result=rd.judge(legacy.post,ID,NOW,native_reader=nw.Reader(legacy.post,nw.Board(GRAPH,'demo')))
        self.assertEqual(result['verdict'],'UNKNOWN')
        self.assertEqual(result['native_authority'],'UNKNOWN')

    def test_independent_review_after_remains_due(self):
        self.assertEqual(self.judge(extra=[(ID,'reviewAfter','"2026-10-01T00:00:00Z"')])['verdict'],'DUE')

    def test_independent_verified_max_age_remains_due(self):
        extra=[(ID,'maxAge','"P1D"'),('proof','verifies',ID),
               ('proof','verifiedAt','"2026-10-01T00:00:00Z"')]
        self.assertEqual(self.judge(extra=extra)['verdict'],'DUE')

if __name__=='__main__':unittest.main()

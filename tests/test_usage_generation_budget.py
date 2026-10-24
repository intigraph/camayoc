import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import usage_generation_budget as b


class BudgetTests(unittest.TestCase):
    def test_part_boundaries_and_no_input_mutation(self):
        old=[];got=b.admit('part',65536,40,3,100,old,staged_bytes=64*1024*1024)
        self.assertEqual(old,[]);self.assertEqual(len(got),1)
        for args in [('part',65537,40,3),('part',65536,41,3),('part',1,1,4),
                     ('marker',2049,0,2),('marker',2048,0,3),('graph',1,0,3)]:
            with self.subTest(args=args),self.assertRaises(ValueError): b.admit(*args,100,[])
        with self.assertRaises(ValueError): b.admit('part',1,1,3,100,[],staged_bytes=64*1024*1024+1)

    def test_C_and_A_share_one_minute(self):
        ledger=b.admit('whole',100,1,3,100,[])
        with self.assertRaises(ValueError): b.admit('part',100,1,3,159,ledger)
        self.assertEqual(len(b.admit('part',100,1,3,160,ledger)),2)
        with self.assertRaises(ValueError): b.admit('marker',100,0,2,99,ledger)

    def test_sixty_writes_and_hour_bytes(self):
        ledger=[]
        for n in range(60): ledger=b.admit('part',65536,40,3,n*60,ledger)
        # At the hour boundary, the oldest entry expires; there is no burst allowance.
        self.assertEqual(len(b.admit('part',65536,40,3,3600,ledger)),60)
        with self.assertRaises(ValueError): b.admit('part',1,1,3,3599,ledger)
        full=[{'at':100,'bytes':b.TOTAL_BYTES_HOUR,'kind':'whole'}]
        with self.assertRaisesRegex(ValueError,'C\+A'): b.admit('marker',1,0,2,160,full)
        full=[{'at':100,'bytes':b.A_BYTES_HOUR,'kind':'part'}]
        with self.assertRaisesRegex(ValueError,'A body'): b.admit('marker',1,0,2,160,full)

    def test_unknown_ledgers_never_reset(self):
        for ledger in [None,{},[{}],[{'at':float('nan'),'bytes':1,'kind':'part'}],
                       [{'at':200,'bytes':1,'kind':'part'}],
                       [{'at':20,'bytes':1,'kind':'part'},{'at':10,'bytes':1,'kind':'part'}]]:
            with self.subTest(ledger=ledger),self.assertRaises(ValueError): b.admit('part',1,1,3,100,ledger)

    def test_session_scope_and_staging(self):
        kw={'c_items':9,'source_digest':'a'*64,'previous':{},'staged_bytes':0,'body_bytes':65536}
        self.assertTrue(b.admit_session(**kw))
        for change in [{'c_items':8},{'source_digest':'wrong'},
                       {'previous':{'selected':{'source_digest':'a'*64}}},
                       {'previous':{'in_progress':{'status':'staging'}}},
                       {'previous':{'foreign':{}}},
                       {'staged_bytes':64*1024*1024}]:
            with self.subTest(change=change),self.assertRaises(ValueError): b.admit_session(**(kw|change))
        self.assertTrue(b.admit_session(**(kw|{'previous':{'in_progress':{'status':'abandoned'}}})))

    def test_one_graph_read_per_build_five_minutes(self):
        kw={'now':100,'previous':None,'graph':'https://example.org/g'}
        first=b.admit_read(**kw)
        with self.assertRaises(ValueError): b.admit_read(**(kw|{'now':399,'previous':first}))
        self.assertEqual(b.admit_read(**(kw|{'now':400,'previous':first}))['at'],400)
        for change in [{'requests':2},{'graph':'ROOT'},{'response_bytes':8*1024*1024+1},{'previous':{}}]:
            with self.subTest(change=change),self.assertRaises(ValueError): b.admit_read(**(kw|change))

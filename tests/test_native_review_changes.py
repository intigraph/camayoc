import unittest
from test_seed_work_items import GRAPH,ID,SUBJECT
import seed_work_items as nw
import change_adapter as ca

class ReviewChanges(unittest.TestCase):
    def setUp(self):self.reader=nw.Reader(lambda *args:None,nw.Board(GRAPH,'demo'))
    def test_native_activity_routes_existing_exact_policy_identity(self):
        for attribute in (nw.SEEDS+'status',nw.SCHEMA+'dateModified',nw.SCHEMA+'agent',nw.SCHEMA+'version'):
            row={'entity':SUBJECT,'graph':GRAPH,'attribute':attribute}
            self.assertEqual(ca.targets(lambda *args:None,'review',[row],[],native_reader=self.reader),[ID])
            self.assertIn(attribute,ca.description('review')['attributes'])
    def test_wrong_graph_and_other_rig_do_not_invalidate_this_policy(self):
        for subject,graph in ((SUBJECT,'urn:other'),(nw.ITEM+'other-one',GRAPH)):
            self.assertEqual(ca.targets(lambda *args:None,'review',[{'entity':subject,'graph':graph,'attribute':nw.SEEDS+'status'}],[],native_reader=self.reader),[])
    def test_ambiguous_action_spelling_refuses(self):
        with self.assertRaises(nw.NativeUnknown):
            ca.targets(lambda *args:None,'review',[{'entity':SUBJECT+'%2E','graph':GRAPH,'attribute':nw.SEEDS+'status'}],[],native_reader=self.reader)
    def test_previously_cached_native_item_keys_are_normalized_exactly(self):
        self.assertEqual(ca.targets(lambda *args:None,'review',[],[SUBJECT],native_reader=self.reader),[ID])

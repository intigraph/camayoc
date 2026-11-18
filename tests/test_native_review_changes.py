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

    def test_native_comment_changes_route_exact_parent_without_reads(self):
        def no_http(*args):self.fail('routing must not query or write')
        changes=[{'entity':SUBJECT+'/comment/36','graph':GRAPH,'attribute':nw.SCHEMA+'text'},
                 {'entity':SUBJECT,'graph':GRAPH,'attribute':nw.SCHEMA+'dateModified'}]
        self.assertEqual(ca.targets(no_http,'review',changes,[],native_reader=self.reader),[ID])
        self.assertEqual(ca.targets(no_http,'review',[],[SUBJECT+'/comment/36'],native_reader=self.reader),[ID])
    def test_comment_graph_and_parent_authority_are_preserved(self):
        for subject,graph in ((SUBJECT+'/comment/36','urn:other'),(nw.ITEM+'other-one/comment/36',GRAPH)):
            self.assertEqual(ca.targets(None,'review',[{'entity':subject,'graph':graph,'attribute':nw.SCHEMA+'text'}],[],native_reader=self.reader),[])
    def test_malformed_comment_or_aliased_parent_still_refuses(self):
        for suffix in ('/comment/x','/comment/1/extra','%2E/comment/1','%2Fcomment%2F1'):
            with self.subTest(suffix=suffix),self.assertRaises(nw.NativeUnknown):
                ca.targets(None,'review',[{'entity':SUBJECT+suffix,'graph':GRAPH,'attribute':nw.SCHEMA+'text'}],[],native_reader=self.reader)

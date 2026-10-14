import unittest
from test_seed_work_items import rows,GRAPH
import seed_work_items as nw
import seed_metadata_projection as mp

class Projection(unittest.TestCase):
    def record(self,**changes):
        reader=nw.Reader(lambda *args:{'rows':rows(**changes),'truncated':False},nw.Board(GRAPH,'demo'))
        return reader.get('demo-one')
    def test_native_current_metadata_exact_identity_and_no_fake_policy(self):
        value=mp.body(self.record(),plane='urn:private:knowledge',actor='fixture')
        self.assertTrue(value['replace_snapshot'])
        text=value['turtle']
        for word in ('Current native title','Authoritative native body','demo-one','source_graph','source_version'):
            self.assertIn(word,text)
        for forbidden in ('Observation','idleLimit','maxAge','verifiedAt','PT12H','ownedBy','observedStatus'):
            self.assertNotIn(forbidden,text)
        self.assertIn(mp.A+'demo-one',text)
    def test_revision_changes_content_not_snapshot_identity(self):
        before=mp.body(self.record(),plane='urn:private:knowledge',actor='fixture')
        after=mp.body(self.record(**{nw.SCHEMA+'version':4,nw.SCHEMA+'description':'new current body'}),
                      plane='urn:private:knowledge',actor='fixture')
        self.assertEqual(before['snapshot'],after['snapshot'])
        self.assertNotEqual(before['turtle'],after['turtle'])
    def test_strings_escaped_not_turtle_injection(self):
        value=mp.body(self.record(**{nw.SCHEMA+'description':'quote"\n<urn:evil> a <urn:unwanted> .'}),
                      plane='urn:private:knowledge',actor='fixture')
        self.assertNotIn('\n<urn:evil>',value['turtle'])
    def test_unknown_destination_or_oversized_record_refuses(self):
        with self.assertRaises(nw.NativeUnknown):mp.body(self.record(),plane='urn:bad> {',actor='fixture')
        with self.assertRaises(nw.NativeUnknown):mp.body(self.record(),plane='urn:ok',actor='')

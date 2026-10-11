"""Native identity and authority controls; no live credentials or writes."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import seed_work_items as nw

GRAPH='urn:private:board'
ID='demo-one'
SUBJECT=nw.ITEM+ID


def rows(**changes):
    fields={nw.RDF+'type':'schema:Action',nw.SCHEMA+'identifier':ID,
            nw.SCHEMA+'name':'Current native title',nw.RDFS+'label':'Current native title',
            nw.SEEDS+'status':'deferred',nw.SCHEMA+'agent':nw.PRINCIPAL+'worker',
            nw.SCHEMA+'dateCreated':{'value':'2026-10-01T00:00:00Z','datatype':'xsd:dateTime'},
            nw.SCHEMA+'dateModified':{'value':'2026-10-11T13:00:00.123456789Z','datatype':'xsd:dateTime'},
            nw.SCHEMA+'version':{'value':'3','datatype':'xsd:integer'},nw.SEEDS+'priority':2,
            nw.SEEDS+'issueType':'bug',nw.SCHEMA+'description':'Authoritative native body'}
    fields.update(changes)
    return [{'s':SUBJECT,'p':p,'o':v} for p,v in fields.items() if v is not None]


class NativeReader(unittest.TestCase):
    def read(self,data=None,**response):
        requests=[]
        def post(endpoint,body):
            requests.append((endpoint,body))
            return {'rows':rows() if data is None else data,'truncated':False,**response}
        reader=nw.Reader(post,nw.Board(GRAPH,'demo'))
        return reader,requests

    def test_exact_positive_and_one_run_cache(self):
        reader,requests=self.read()
        result=reader.get(ID)
        self.assertEqual((result.id,result.graph,result.status,result.owner,result.version),
                         (ID,GRAPH,'deferred','worker',3))
        self.assertEqual(result.tracker_record()['description'],'Authoritative native body')
        self.assertEqual(reader.get(ID),result)
        self.assertEqual(len(requests),1)
        self.assertIn('GRAPH <urn:private:board>',requests[0][1]['query'])
        self.assertIn('<'+SUBJECT+'>',requests[0][1]['query'])

    def test_different_subject_or_stored_identifier_never_binds(self):
        for data in ([{**r,'s':nw.ITEM+'demo-other'} for r in rows()],
                     rows(**{nw.SCHEMA+'identifier':'demo-other'})):
            with self.subTest(data=data):
                reader,_=self.read(data)
                with self.assertRaises(nw.NativeUnknown):reader.get(ID)

    def test_missing_and_partial_are_unknown(self):
        for data,response in (([],{}),(rows(),{'truncated':True}), (rows()*10,{}),
                              (rows(),{'rows':{}})):
            with self.subTest(response=response):
                reader,_=self.read(data,**response)
                with self.assertRaises(nw.NativeUnknown):reader.get(ID)

    def test_status_conflict_and_unknown_refuse(self):
        for status in ('unknown','closed,open',None):
            reader,_=self.read(rows(**{nw.SEEDS+'status':status}))
            with self.assertRaises(nw.NativeUnknown):reader.get(ID)
        reader,_=self.read(rows()+[{'s':SUBJECT,'p':nw.SEEDS+'status','o':'open'}])
        with self.assertRaises(nw.NativeUnknown):reader.get(ID)

    def test_closed_and_deferred_and_active_native_states(self):
        for status in nw.STATES:
            reader,_=self.read(rows(**{nw.SEEDS+'status':status}))
            self.assertEqual(reader.get(ID).status,status)

    def test_unassigned_is_not_missing_authority(self):
        reader,_=self.read(rows(**{nw.SCHEMA+'agent':None}))
        self.assertIsNone(reader.get(ID).owner)

    def test_owner_is_exact_principal_not_label_guess(self):
        for owner in ('worker',nw.PRINCIPAL,'urn:someone'):
            reader,_=self.read(rows(**{nw.SCHEMA+'agent':owner}))
            with self.assertRaises(nw.NativeUnknown):reader.get(ID)

    def test_dates_and_versions_are_required(self):
        for field,value in ((nw.SCHEMA+'dateModified','not-a-date'),
                            (nw.SCHEMA+'dateModified','2026-09-01T00:00:00Z'),
                            (nw.SCHEMA+'dateCreated',None), (nw.SCHEMA+'version',0),
                            (nw.SCHEMA+'version',True), (nw.SCHEMA+'version','NaN')):
            reader,_=self.read(rows(**{field:value}))
            with self.assertRaises(nw.NativeUnknown):reader.get(ID)

    def test_title_conflict_is_unknown(self):
        reader,_=self.read(rows(**{nw.RDFS+'label':'Old legacy title'}))
        with self.assertRaises(nw.NativeUnknown):reader.get(ID)

    def test_wrong_or_unregistered_board_is_not_empty(self):
        reader,requests=self.read()
        with self.assertRaises(nw.NativeUnknown):reader.get('other-one')
        self.assertEqual(requests,[])
        for graph in ('','urn:bad> UNION {','https://example.test/b\n'):
            with self.assertRaises(nw.NativeUnknown):nw.Board(graph,'demo')

    def test_explicit_links_and_blockers_only(self):
        data=rows()+[{'s':SUBJECT,'p':nw.SCHEMA+'about','o':'urn:explicit:target'},
                     {'s':SUBJECT,'p':nw.QUECHUA+'blockedOn','o':nw.ITEM+'demo-blocker'}]
        reader,_=self.read(data)
        result=reader.get(ID)
        self.assertEqual(result.about,('urn:explicit:target',))
        self.assertEqual(result.blocked_on,('demo-blocker',))

    def test_http_failure_does_not_cache_absence(self):
        def failed(*args):raise TimeoutError('fixture timeout')
        reader=nw.Reader(failed,nw.Board(GRAPH,'demo'))
        with self.assertRaises(nw.NativeUnknown):reader.get(ID)
        self.assertEqual(reader.cache,{})

    def test_response_byte_budget_refuses_without_truncation(self):
        reader,_=self.read(rows(**{nw.SCHEMA+'description':'x'*nw.MAX_BYTES}))
        with self.assertRaises(nw.NativeUnknown):reader.get(ID)

if __name__=='__main__':unittest.main()

class AuthorityProjection(unittest.TestCase):
    def profile(self):
        return {'format':'camayoc-tracker-authority-v1','backend':'seeds',
                'server':'https://tracker.example.test','graph':GRAPH,'prefix':'demo'}

    def test_exact_peer_and_scope(self):
        result=nw.from_authority(lambda *args:None,self.profile(),server='https://tracker.example.test/')
        self.assertEqual(result.board,nw.Board(GRAPH,'demo'))

    def test_backend_peer_and_unknown_fields_refuse(self):
        for change in ({'backend':'br'},{'server':'https://other.example.test'},
                       {'server':'https://user:secret@tracker.example.test'},
                       {'graph':'urn:bad> UNION {'},{'extra':'ambient-override'},
                       {'format':'unversioned'}):
            with self.subTest(change=change):
                with self.assertRaises(nw.NativeUnknown):
                    nw.from_authority(lambda *args:None,{**self.profile(),**change},
                                      server='https://tracker.example.test')

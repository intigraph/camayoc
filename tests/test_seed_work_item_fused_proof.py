"""arming: ci/library -- same-version proof contract, no live service required."""
import json
import unittest
import seed_work_item_sync as sync
import seed_change_custody as custody

ID='demo-proof'
GRAPH='urn:private:projection'
DESCRIPTION='exact description'


class FusedProof(unittest.TestCase):
    def check(self, response, expected=True):
        calls=[]
        def post(endpoint,body):
            calls.append((endpoint,body))
            if body['query'].startswith('ASK '):return {'result':True}
            return response
        envelope={'request':{'graph':GRAPH},'description':DESCRIPTION}
        method=getattr(sync,'verify_fused',sync.verify)
        if expected is None:
            with self.assertRaises(custody.CustodyUnknown):method(post,envelope,ID)
        else:self.assertEqual(method(post,envelope,ID),expected)
        self.assertEqual(len(calls),1,'same-version proof must use one request')
        self.assertTrue(calls[0][1]['query'].startswith('SELECT ?has_type ?v '))
        self.assertIn(f'GRAPH <{GRAPH}>',calls[0][1]['query'])
        self.assertIn(f'<{sync.projection.A+ID}>',calls[0][1]['query'])
        self.assertIn(f'a <{sync.projection.A}WorkItem>',calls[0][1]['query'])
        self.assertNotIn('EXISTS',calls[0][1]['query'])

    def response(self, rows, **kwargs):
        return dict(rows=rows,truncated=False,**kwargs)

    def test_positive_type_and_exact_comment(self):
        self.check(self.response([{'has_type':True,'v':DESCRIPTION}]))

    def test_unrelated_comments_do_not_replace_exact_comment(self):
        self.check(self.response([{'has_type':True,'v':'other'},{'has_type':True,'v':DESCRIPTION}]))

    def test_typed_escaped_literal(self):
        self.check(self.response([{'has_type':True,'v':{'value':DESCRIPTION,'datatype':'http://www.w3.org/2001/XMLSchema#string'}}]))

    def test_missing_comment_sentinel_cannot_verify(self):
        self.check(self.response([{'has_type':True}]),False)

    def test_wrong_comment_cannot_verify(self):
        self.check(self.response([{'has_type':True,'v':'other'}]),False)

    def test_duplicate_exact_comment_cannot_verify(self):
        self.check(self.response([{'has_type':True,'v':DESCRIPTION}]*2),False)

    def test_missing_false_wrong_and_inconsistent_type_witnesses_refuse(self):
        for witness in [None,False,1,'true',{'value':'true','datatype':'http://www.w3.org/2001/XMLSchema#boolean'}]:
            with self.subTest(witness=witness):
                row={'v':DESCRIPTION}
                if witness is not None:row['has_type']=witness
                self.check(self.response([row]),None)
        self.check(self.response([{'has_type':True,'v':DESCRIPTION},{'has_type':False,'v':'other'}]),None)

    def test_unknown_row_fields_and_malformed_literals_refuse(self):
        for row in [None,{},'row',{'has_type':True,'v':DESCRIPTION,'foreign':'x'},
                    {'has_type':True,'v':{'value':DESCRIPTION,'foreign':'x'}}]:
            with self.subTest(row=row):self.check(self.response([row]),None)

    def test_partial_missing_or_nonboolean_truncation_refuses(self):
        for marker in [None,True,0,'false']:
            response={'rows':[{'has_type':True,'v':DESCRIPTION}]}
            if marker is not None:response['truncated']=marker
            with self.subTest(marker=marker):self.check(response,None)

    def test_empty_or_nonobject_response_refuses(self):
        for response in [None,[],{},self.response([]),self.response('rows')]:
            with self.subTest(response=response):self.check(response,None)

    def test_128_comments_valid_but_129_is_unknown(self):
        rows=[{'has_type':True,'v':DESCRIPTION}]+[{'has_type':True,'v':str(i)} for i in range(127)]
        self.check(self.response(rows))
        self.check(self.response(rows+[{'has_type':True,'v':'overflow'}]),None)

    def test_response_byte_ceiling_is_not_relaxed(self):
        self.check(self.response([{'has_type':True,'v':DESCRIPTION},{'has_type':True,'v':'x'*262144}]),None)

    def test_safe_iri_is_required_before_request(self):
        method=getattr(sync,'verify_fused',sync.verify)
        for graph in ['urn:bad> . ?s ?p ?o','relative']:
            with self.subTest(graph=graph):
                calls=[]
                with self.assertRaises(ValueError):
                    method(lambda *a:calls.append(a),{'request':{'graph':graph},'description':DESCRIPTION},ID)
                self.assertEqual(calls,[])

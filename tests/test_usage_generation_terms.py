import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import usage_generation_terms as t


class TermTests(unittest.TestCase):
    def payload(self,**changes):
        row={'s':'https://example.org/s','p':'https://example.org/p','lex':'https://example.org/value',
             'iri':False,'datatype':t.XSD+'string','lang':''}
        return {'rows':[row|changes],'truncated':False,'count':1}

    def test_iri_looking_literal_is_not_iri(self):
        literal=t.parse_response(self.payload(),'https://example.org/g')[0][-1]
        raw=self.payload();raw['rows'][0]={'s':'https://example.org/s','p':'https://example.org/p',
                                         'lex':'https://example.org/value','iri':True}
        real=t.parse_response(raw,'https://example.org/g')[0][-1]
        self.assertNotEqual(literal,real)

    def test_kind_language_datatype_and_duplicate_refusals(self):
        for changes in [{'iri':None},{'iri':True},{'lang':'en'},
                        {'datatype':t.XSD+'double'},{'lex':12},{'g':'ROOT'},
                        {'extra':'bad'}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                t.parse_response(self.payload(**changes),'https://example.org/g')
        duplicate=self.payload();duplicate['rows']*=2;duplicate['count']=2
        with self.assertRaises(ValueError):t.parse_response(duplicate,'https://example.org/g')

    def test_unknown_empty_and_truncated_are_not_complete(self):
        for response in [{}, {'rows':[],'truncated':False,'count':0},
                         self.payload()|{'truncated':True},self.payload()|{'count':False}]:
            with self.subTest(response=response),self.assertRaises(ValueError):
                t.parse_response(response,'https://example.org/g')

    def test_one_query_names_scope_and_has_no_root_fallback(self):
        q=t.snapshot_query('https://example.org/control','https://example.org/selector')
        self.assertTrue(q['verbose']);self.assertIn('GRAPH <https://example.org/control>',q['query'])
        self.assertIn('GRAPH ?g',q['query']);self.assertIn('STR(?o)',q['query'])
        with self.assertRaises(ValueError):t.snapshot_query('ROOT','relative')

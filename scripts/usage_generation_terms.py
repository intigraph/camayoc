"""Lossless-term projection for a single bounded generation query.

STR/isIRI/DATATYPE/LANG are explicit in the one normal JSON response, retaining
its truncation flag; standard SPARQL JSON silently drops that server flag.
No selected generation or completeness verdict is cached across requests here.
"""
from usage_generation_codec import iri, ONTOLOGY

MAX_ROWS = 10000
XSD = 'http://www.w3.org/2001/XMLSchema#'


def snapshot_query(control_graph, selector):
    control=iri(control_graph); subject=iri(selector)
    return {'query': (
        'SELECT ?g ?s ?p (STR(?o) AS ?lex) (isIRI(?o) AS ?iri) '
        '(DATATYPE(?o) AS ?datatype) (LANG(?o) AS ?lang) WHERE { '
        '{ GRAPH <'+control+'> { ?s ?p ?o } } UNION { '
        'GRAPH <'+control+'> { <'+subject+'> <'+ONTOLOGY+'usageSelectedGeneration> ?g } '
        'GRAPH ?g { ?s ?p ?o } } } LIMIT '+str(MAX_ROWS)), 'verbose':True}


def parse_response(response, control_graph):
    """Return deduplicated graph/subject/predicate/term tuples, or UNKNOWN error."""
    if (not isinstance(response,dict) or response.get('truncated') is not False
            or not isinstance(response.get('rows'),list)
            or type(response.get('count')) is not int
            or response['count']!=len(response['rows'])
            or len(response['rows'])>=MAX_ROWS or not response['rows']):
        raise ValueError('UNKNOWN: empty/truncated/noncanonical generation response')
    control=iri(control_graph); result=[];seen=set()
    for row in response['rows']:
        if not isinstance(row,dict) or set(row)-{'g','s','p','lex','iri','datatype','lang'}:
            raise ValueError('UNKNOWN: malformed term row')
        graph=iri(row['g']) if 'g' in row else control
        subject=iri(row.get('s'));predicate=iri(row.get('p'))
        lexical=row.get('lex')
        if not isinstance(lexical,str) or type(row.get('iri')) is not bool:
            raise ValueError('UNKNOWN: missing term kind/lexical value')
        lexical.encode('utf-8','strict')
        if row['iri']:
            if 'datatype' in row or 'lang' in row:
                raise ValueError('UNKNOWN: contradictory IRI metadata')
            term=('uri',iri(lexical),None)
        else:
            datatype=iri(row.get('datatype'))
            if row.get('lang','')!='':
                raise ValueError('UNKNOWN: language-tagged accounting term')
            if datatype not in (XSD+'string',XSD+'integer'):
                raise ValueError('UNKNOWN: unsupported accounting literal type')
            term=('literal',lexical,datatype)
        triple=(graph,subject,predicate,term)
        if triple in seen:
            raise ValueError('UNKNOWN: duplicate response tuple')
        seen.add(triple);result.append(triple)
    return result

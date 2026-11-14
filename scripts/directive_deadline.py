"""Read explicit traceability deadlines without treating malformed data as absence."""
from __future__ import annotations

STABLE = 'https://w3id.org/intigraph/quechua/ns#traceabilityDueAt'
LEGACY = 'https://scbrown.github.io/quechua/ns#traceabilityDueAt'
ROOT_GRAPH = 'urn:quipu:graph:root'
XSD_DATETIME = 'http://www.w3.org/2001/XMLSchema#dateTime'


def deadline(rows, entity_iri, declared_graph, parse):
    query = (f'SELECT ?due ?datatype WHERE {{ <{entity_iri}> '
             f'(<{STABLE}>|<{LEGACY}>) ?due . '
             'BIND(DATATYPE(?due) AS ?datatype) } LIMIT 3')
    values = set()
    for graph in (ROOT_GRAPH, declared_graph):
        found = rows(query, graph)
        if len(found) >= 3:
            raise ValueError('traceability deadline read reached its bound')
        for row in found:
            if set(row) != {'due', 'datatype'} or row['datatype'] != XSD_DATETIME:
                raise ValueError('traceability deadline requires one typed xsd:dateTime')
            cell = row['due']
            if isinstance(cell, dict):
                if (set(cell) != {'value', 'datatype'} or
                        cell['datatype'] != XSD_DATETIME or not isinstance(cell['value'], str)):
                    raise ValueError('malformed typed deadline cell')
                cell = cell['value']
            if not isinstance(cell, str):
                raise ValueError('deadline lexical value must be a string')
            values.add(cell)
    if not values:
        return None
    if len(values) != 1:
        raise ValueError('conflicting traceability deadlines; no grace fallback')
    result = parse(next(iter(values)))
    if result is None or result.tzinfo is None:
        raise ValueError('invalid traceability deadline; no grace fallback')
    return result

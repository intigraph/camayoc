"""Read explicit traceability deadlines without treating malformed data as absence."""
from __future__ import annotations

STABLE = 'https://w3id.org/intigraph/quechua/ns#traceabilityDueAt'
LEGACY = 'https://scbrown.github.io/quechua/ns#traceabilityDueAt'
ROOT_GRAPH = 'urn:quipu:graph:root'
XSD_DATETIME = 'http://www.w3.org/2001/XMLSchema#dateTime'


class DeadlineUnknown(ValueError):
    """ONE Directive's stored deadline cannot be read as a single instant.

    This is a fact about that item's data, not about the store: the caller may
    report that item as UNKNOWN and judge the rest (aegis-b9k). A failed read
    (the `rows` callable raising) is NOT this class and still fails the batch,
    because it says nothing about any one item."""


def deadline(rows, entity_iri, declared_graph, parse):
    query = (f'SELECT ?due ?datatype WHERE {{ <{entity_iri}> '
             f'(<{STABLE}>|<{LEGACY}>) ?due . '
             'BIND(DATATYPE(?due) AS ?datatype) } LIMIT 3')
    values = set()
    for graph in (ROOT_GRAPH, declared_graph):
        found = rows(query, graph)
        if len(found) >= 3:
            raise DeadlineUnknown('traceability deadline read reached its bound')
        for row in found:
            if set(row) != {'due', 'datatype'} or row['datatype'] != XSD_DATETIME:
                raise DeadlineUnknown('traceability deadline requires one typed xsd:dateTime')
            cell = row['due']
            if isinstance(cell, dict):
                if (set(cell) != {'value', 'datatype'} or
                        cell['datatype'] != XSD_DATETIME or not isinstance(cell['value'], str)):
                    raise DeadlineUnknown('malformed typed deadline cell')
                cell = cell['value']
            if not isinstance(cell, str):
                raise DeadlineUnknown('deadline lexical value must be a string')
            values.add(cell)
    if not values:
        return None
    instants = {}
    for lexical in sorted(values):
        result = parse(lexical)
        if result is None or result.tzinfo is None:
            raise DeadlineUnknown('invalid traceability deadline; no grace fallback')
        instants.setdefault(result, lexical)
    # Two spellings of ONE instant (e.g. Z and +00:00, or the same value in
    # ROOT and the declared plane) are one deadline, not a conflict.
    if len(instants) != 1:
        raise DeadlineUnknown('conflicting traceability deadlines; no grace fallback: '
                              + ', '.join(sorted(instants.values())))
    return next(iter(instants))

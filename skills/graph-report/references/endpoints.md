# Quipu read endpoints (live, `${GRAPH_URL}` — verified against quipu 0.3.0)

All POST bodies are JSON; `/stats` is GET.

| Endpoint | Method | Body | Returns |
|----------|--------|------|---------|
| `/stats` | GET | — | `{entities, facts, predicates}` |
| `/project` | POST | `{"algorithm":"pagerank","max_iters":20,"persist":false}` | `{results:[{entity, score}, ...]}` ranked |
| `/query` | POST | `{"query":"<SPARQL>"}` | `{rows:[...], variables:[...]}` |
| `/search_nodes` | POST | `{"query":"<text>","limit":N}` | hybrid entity hits (needs embeddings configured) |
| `/episode` | POST | (see graph-extract) | ingest |

Personalized PageRank (hub-adjacency from seeds):
`POST /project {"algorithm":"ppr","seeds":["<IRI>", ...],"persist":false}`.

## Schema-node exclusion (apply in step 2)

Exclude these namespaces when reporting god nodes — they're structural, not domain content:

```
http://www.w3.org/ns/prov#          (prov:Activity, prov:wasGeneratedBy, …)
http://www.w3.org/1999/02/22-rdf-syntax-ns#   (rdf:)
http://www.w3.org/2000/01/rdf-schema#         (rdfs:)
http://www.w3.org/2002/07/owl#                (owl:)
http://www.w3.org/ns/shacl#                   (sh:)
http://purl.org/dc/terms/                     (dcterms:)
```

Also exclude **class nodes** — anything returned by:
```json
{"query": "SELECT DISTINCT ?c WHERE { ?x a ?c }"}
```
(these are the `Container` / `WebApplication` / `SystemdService` … type nodes, which top PageRank
because every instance `rdf:type`-points to them). Your own ontology base is `${GRAPH_NS}` — classes
and instances typically **share** it, so filter your own base by the rdf:type query above, NOT by
namespace prefix.

## Useful orientation queries

Recent episodes (the changelog):
```sparql
PREFIX prov: <http://www.w3.org/ns/prov#>
SELECT ?a ?t WHERE { ?a a prov:Activity ; prov:atTime ?t } ORDER BY DESC(?t) LIMIT 10
```

Predicate-diversity per entity (v1 "surprising connection" heuristic — bridges):
```sparql
SELECT ?e (COUNT(DISTINCT ?p) AS ?np) WHERE { ?e ?p ?o . FILTER(isIRI(?o)) }
GROUP BY ?e ORDER BY DESC(?np) LIMIT 15
```
(then drop schema/class nodes; a high-diversity domain entity ties many relation types together.)

In-degree among domain nodes (primary hub signal at current scale — PageRank is flat on a young,
episode-dense graph):
```sparql
SELECT ?o (COUNT(?s) AS ?indeg) WHERE { ?s ?p ?o . FILTER(isIRI(?o)) }
GROUP BY ?o ORDER BY DESC(?indeg) LIMIT 25
```
(then drop classes, episodes (`?o a prov:Activity`), and schema namespaces; survivors are the
most-referenced real entities.)

Neighbourhood of a hub (for suggested-question follow-ups):
```sparql
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?p ?o ?ol WHERE { <HUB_IRI> ?p ?o . OPTIONAL { ?o rdfs:label ?ol } }
```

## Note

`regex(str(?l))` FILTERs on `rdfs:label` are unreliable on this Quipu — they can return 0 for
labels that exist. Prefer structural queries (by predicate / type / IRI) over label-regex.

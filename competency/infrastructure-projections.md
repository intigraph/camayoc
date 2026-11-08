# Infrastructure projection competency questions

1. Which exact source bytes/revision and extractor revision produced a record?
2. Does the record describe declared configuration or an observed resource?
3. Does each guest have a typed host, and each route a typed service target?
4. Which record has ambiguous scoped identity or an unadjudicated alias?
5. Can an undeclared field carry a credential into a producer projection?

The optional `profiles/infrastructure-output` contract answers structural parts
of these questions using existing PROV-O, Dublin Core and Schema.org terms plus
accepted canonical infrastructure terms. Its shape identifiers are validation
contracts, not new entity classes. Identity adjudication, exact batch membership,
address parsing and cross-record source equality belong to adapter admission.

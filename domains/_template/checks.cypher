// Sanity queries for the template backbone.
//
// Run with: graphpack backbone check _template
//
// Format, which is stricter than it looks:
//
//   * Queries are separated by a blank line and preceded by a `//` title.
//   * A query ending in a semicolon is one statement. Keep them single.
//   * `[must be empty]` marks an assertion: the query must return zero rows or
//     the check fails. It is read from the FIRST title line only — put it on a
//     second comment line and the assertion silently becomes commentary, and
//     the suite reports OK because it stopped looking. That has happened here.
//
// Everything without the marker is printed for a human to read. Numbers that
// look wrong to a person are the cheapest bug detector this stage has, and
// several of this project's real defects were found by a count being an order
// of magnitude off rather than by anything failing.

// Nodes with no pack tag [must be empty]
// Neo4j Community has exactly one database, so packs share it and are separated
// only by this property. An untagged node belongs to nobody and leaks across
// every boundary the design depends on.
MATCH (n)
WHERE n.pack IS NULL
  AND NOT n:Chunk AND NOT n:`__Node__` AND NOT n:`__Entity__`
  AND NOT n:_Migration AND NOT n:_Pack
RETURN n LIMIT 25;

// Books with no author edge [must be empty]
// Every book in the metadata credits somebody. A book with no WRITTEN_BY edge
// means the explode or the id template dropped rows — and since gold comes from
// exactly this edge, it would quietly shrink the measurement rather than fail.
MATCH (b:Book {pack: '_template'})
WHERE NOT (b)-[:WRITTEN_BY]->(:Author)
RETURN b.id, b.title LIMIT 25;

// Node counts by label
// Read these against what you expect from the source. An order-of-magnitude
// surprise here is worth more than any assertion below it.
MATCH (n {pack: '_template'})
RETURN labels(n)[0] AS label, count(*) AS n
ORDER BY n DESC;

// Edge counts by type
MATCH ({pack: '_template'})-[r]->({pack: '_template'})
RETURN type(r) AS type, count(*) AS n
ORDER BY n DESC;

// Authors credited on the most books
// A name at the top with an implausible count usually means a normaliser
// collapsed two different people into one id.
MATCH (a:Author {pack: '_template'})<-[:WRITTEN_BY]-(b:Book {pack: '_template'})
RETURN a.id, a.name, count(b) AS books
ORDER BY books DESC LIMIT 15;

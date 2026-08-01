// Sanity queries for the bench-wiki backbone.
//
//   graphpack backbone check bench-wiki
//
// This backbone is metadata, so it cannot be wrong the way tr-law's can — no
// pattern reads it out of a sentence. What it can be is disconnected from the
// gold, and that is the only failure that matters here: a benchmark whose
// ground truth points at articles the graph does not hold measures nothing.

// Nodes with no pack tag [must be empty]
MATCH (n)
WHERE n.pack IS NULL AND NOT n:_Migration AND NOT n:_Pack
RETURN labels(n) AS labels, count(*) AS nodes;

// Every article has exactly one outlet [must be empty]
MATCH (a:Article {pack: 'bench-wiki'})
WITH a, size([(a)-[:PUBLISHED_BY]->() | 1]) AS outlets
WHERE outlets <> 1
RETURN a.id AS article, outlets
LIMIT 10;

// Every article has exactly one category [must be empty]
MATCH (a:Article {pack: 'bench-wiki'})
WITH a, size([(a)-[:IN_CATEGORY]->() | 1]) AS cats
WHERE cats <> 1
RETURN a.id AS article, cats
LIMIT 10;

// No Author node stands for a missing byline [must be empty]
//
// 68 of 609 articles carry none. The identity renders incomplete and the row is
// dropped, rather than 68 articles all turning out to be written by somebody
// called "None".
MATCH (w:Author {pack: 'bench-wiki'})
WHERE toLower(w.name) IN ['none', 'null', '', 'unknown']
RETURN w.id AS placeholder_author;

// No article is its own outlet's article twice [must be empty]
MATCH (a:Article {pack: 'bench-wiki'})-[r:PUBLISHED_BY]->(s)
WITH a, s, count(r) AS n WHERE n > 1
RETURN a.id AS article, s.id AS outlet, n
LIMIT 10;

// --- for reading ----------------------------------------------------------

// Shape of the graph
MATCH (n {pack: 'bench-wiki'})
RETURN labels(n)[0] AS label, count(*) AS n
ORDER BY n DESC;

// Outlets by output. The dataset's `source` carries a section suffix, so one
// publisher appears several times — The Independent three ways, FOX News three,
// BBC News two. Any question about "which outlet" inherits that.
MATCH (a:Article {pack: 'bench-wiki'})-[:PUBLISHED_BY]->(s:Source {pack: 'bench-wiki'})
RETURN s.name AS outlet, count(a) AS articles
ORDER BY articles DESC
LIMIT 15;

// Categories, and how many outlets cover each — the join a retriever cannot do
MATCH (c:Category {pack: 'bench-wiki'})<-[:IN_CATEGORY]-(a:Article)-[:PUBLISHED_BY]->(s:Source)
RETURN c.name AS category, count(DISTINCT s) AS outlets, count(a) AS articles
ORDER BY articles DESC;

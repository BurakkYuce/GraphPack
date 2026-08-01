// Sanity queries for the tr-law backbone.
//
//   graphpack backbone check tr-law
//
// A backbone read out of prose can be wrong in ways one loaded from metadata
// cannot: a pattern that matches a page number as an article, a statute number
// caught from a monetary amount. The assertions below are the cheap guards; the
// rest are for reading, because a legal graph that looks wrong to somebody who
// knows the domain usually is.

// Nodes with no pack tag [must be empty]
MATCH (n)
WHERE n.pack IS NULL AND NOT n:_Migration AND NOT n:_Pack
RETURN labels(n) AS labels, count(*) AS nodes;

// Decisions citing themselves [must be empty]
// A decision quoting its own case number would mean the citation pattern is
// matching the header rather than the body.
MATCH (d:Decision {pack: 'tr-law'})-[:REFERS_TO]->(d)
RETURN d.id AS decision;

// Statutes with an implausible number [must be empty]
// Turkish statute numbers run from 1 to roughly 7600. Anything outside that
// came from a monetary amount or a date, not a citation.
MATCH (k:Statute {pack: 'tr-law'})
WHERE toInteger(k.number) < 1 OR toInteger(k.number) > 7700
RETURN k.id AS statute;

// Node counts by label
MATCH (n {pack: 'tr-law'})
RETURN labels(n)[0] AS label, count(*) AS nodes
ORDER BY nodes DESC;

// Relationship counts by type
MATCH ({pack: 'tr-law'})-[r]->({pack: 'tr-law'})
RETURN type(r) AS relationship, count(*) AS edges
ORDER BY edges DESC;

// Most cited statutes
// For a labour chamber, 6100 (civil procedure) should dominate, with 4857
// (labour), 6356 (unions) and 5718 (private international law, for overseas
// contracts) close behind. If this list looks unlike that, the pattern is
// catching something other than citations.
MATCH (d:Decision {pack: 'tr-law'})-[:CITES]->(k:Statute {pack: 'tr-law'})
RETURN k.number AS statute, count(DISTINCT d) AS decisions
ORDER BY decisions DESC
LIMIT 12;

// Most cited articles
MATCH (d:Decision {pack: 'tr-law'})-[:CITES_ARTICLE]->(m:Article {pack: 'tr-law'})
MATCH (k:Statute {pack: 'tr-law'})-[:HAS_ARTICLE]->(m)
RETURN k.number + ' m.' + m.article_number AS article, count(DISTINCT d) AS decisions
ORDER BY decisions DESC
LIMIT 12;

// Articles per statute — the widest ones
// A statute with hundreds of articles cited is either genuinely procedural or
// the pattern is grabbing numbers that are not article references.
MATCH (k:Statute {pack: 'tr-law'})-[:HAS_ARTICLE]->(m:Article)
RETURN k.number AS statute, count(m) AS articles
ORDER BY articles DESC
LIMIT 8;

// Decisions citing nothing
// Expected to be small: 5% of decisions named no statute in the sample this
// pack was built from.
MATCH (d:Decision {pack: 'tr-law'})
WHERE NOT (d)-[:CITES]->(:Statute) AND d.subject IS NOT NULL
RETURN count(d) AS decisions_citing_no_statute;

// Decisions cited but not present
// A decision named in someone else's text that the corpus does not contain.
// Unlike a package outside a top-N slice, this is fully identified by its
// citation, so it is a real node with real edges and no text.
MATCH (d:Decision {pack: 'tr-law'})
WHERE d.subject IS NULL
RETURN count(d) AS cited_from_outside_the_corpus;

// The citation chain — decisions reached in two hops
// The multi-hop question this pack exists to answer.
MATCH (d:Decision {pack: 'tr-law'})-[:REFERS_TO*1..2]->(cited:Decision)
RETURN count(DISTINCT cited) AS decisions_reachable_by_citation;

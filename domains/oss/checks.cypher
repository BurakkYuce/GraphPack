// Sanity queries for the oss backbone.
//
// Run with: graphpack backbone check oss
//
// Each query is separated by a blank line and preceded by a `//` title. The
// first two are assertions — they must return zero rows — and are marked
// `[must be empty]`. The rest are here to be read: numbers that look wrong to a
// human are the cheapest bug detector this stage has.

// Nodes with no pack tag [must be empty]
// Neo4j Community has one database, so an untagged node belongs to nobody and
// leaks across every pack boundary the design depends on.
MATCH (n)
WHERE n.pack IS NULL AND NOT n:_Migration AND NOT n:_Pack
RETURN labels(n) AS labels, count(*) AS nodes;

// Packages that depend on themselves [must be empty]
MATCH (p:Package {pack: 'oss'})-[:DEPENDS_ON]->(p)
RETURN p.id AS package;

// Node counts by label
MATCH (n {pack: 'oss'})
RETURN labels(n)[0] AS label, count(*) AS nodes
ORDER BY nodes DESC;

// Relationship counts by type
MATCH ({pack: 'oss'})-[r]->({pack: 'oss'})
RETURN type(r) AS relationship, count(*) AS edges
ORDER BY edges DESC;

// What breaks if urllib3 breaks — direct dependents
// The reference query for this phase: verified by hand against PyPI.
MATCH (d:Package {pack: 'oss'})-[:DEPENDS_ON]->(:Package {pack: 'oss', id: 'pypi:urllib3'})
RETURN d.name AS dependent
ORDER BY dependent;

// Most depended-upon packages
// Expect the obvious infrastructure at the top. If it is not there, the
// dependency edges are wrong.
MATCH (d:Package {pack: 'oss'})-[:DEPENDS_ON]->(p:Package {pack: 'oss'})
RETURN p.name AS package, count(DISTINCT d) AS dependents
ORDER BY dependents DESC
LIMIT 15;

// Two-hop blast radius of urllib3
// The first multi-hop question the graph exists to answer.
MATCH (p:Package {pack: 'oss', id: 'pypi:urllib3'})
MATCH (d:Package {pack: 'oss'})-[:DEPENDS_ON*1..2]->(p)
RETURN count(DISTINCT d) AS packages_affected_within_two_hops;

// Packages sharing the most dependencies with requests
MATCH (:Package {pack: 'oss', id: 'pypi:requests'})-[:DEPENDS_ON]->(shared:Package)<-[:DEPENDS_ON]-(other:Package {pack: 'oss'})
WHERE other.id <> 'pypi:requests'
RETURN other.name AS package, count(shared) AS shared_dependencies
ORDER BY shared_dependencies DESC, package
LIMIT 10;

// Packages with no repository
// A large number here means the repo_slug normaliser is missing a URL shape,
// not that the packages have no source.
MATCH (p:Package {pack: 'oss'})
WHERE NOT (p)-[:HOSTED_IN]->(:Repository)
RETURN count(p) AS packages_without_repository;

// Isolated packages — no dependencies either way
MATCH (p:Package {pack: 'oss'})
WHERE NOT (p)-[:DEPENDS_ON]-(:Package)
RETURN count(p) AS isolated_packages;

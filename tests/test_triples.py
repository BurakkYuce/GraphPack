"""Triple constraints, applied where nothing else applies them.

An ontology says `DEPENDS_ON` runs from a package to a package. The engine never
passes that to the extractor, so a relation can carry a declared type and still
connect two things the ontology never meant to pair. This is the only check that
notices.
"""

from __future__ import annotations

import pytest

from graphpack.resolve.triples import validate_triples

pytestmark = [pytest.mark.integration, pytest.mark.graph]

PACK = "_graphpack_triples_test"

CONSTRAINTS = [
    ("PACKAGE", "DEPENDS_ON", "PACKAGE"),
    ("PERSON", "AUTHORED", "ISSUE"),
    ("PERSON", "MAINTAINS", "PACKAGE"),
]


@pytest.fixture
def graph(neo4j_session):
    neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=PACK)
    yield neo4j_session
    neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=PACK)


def _link(session, subject_type, subject, relation, object_type, obj):
    session.run(
        f"""
        MERGE (a:`__Entity__`:{subject_type} {{pack: $p, id: $a}})
        MERGE (b:`__Entity__`:{object_type} {{pack: $p, id: $b}})
        MERGE (a)-[:{relation}]->(b)
        """,
        p=PACK,
        a=subject,
        b=obj,
    )


def test_a_correctly_typed_triple_conforms(graph):
    _link(graph, "PACKAGE", "_t_requests", "DEPENDS_ON", "PACKAGE", "_t_urllib3")

    report = validate_triples(graph, PACK, CONSTRAINTS)

    assert (report.conforming, report.violating, report.undeclared) == (1, 0, 0)
    assert report.conformance == 1.0


def test_a_declared_relation_between_the_wrong_types_is_a_violation(graph):
    """The case nothing else catches: `AUTHORED` is a real relation and both
    ends are real entities, but a package does not author anything."""
    _link(graph, "PACKAGE", "_t_requests", "AUTHORED", "PACKAGE", "_t_urllib3")

    report = validate_triples(graph, PACK, CONSTRAINTS)

    assert report.violating == 1
    assert report.conforming == 0
    violation = report.violations[0]
    assert (violation.subject_type, violation.relation) == ("PACKAGE", "AUTHORED")
    assert "PERSON -> ISSUE" in violation.expected


def test_a_relation_the_ontology_never_declared_is_counted_apart(graph):
    """An invented relation and a misapplied one are different failures: the
    first says the ontology is incomplete, the second that extraction was
    careless."""
    _link(graph, "PACKAGE", "_t_requests", "SUPERSEDES", "PACKAGE", "_t_urllib3")

    report = validate_triples(graph, PACK, CONSTRAINTS)

    assert report.undeclared == 1
    assert report.violating == 0
    assert report.undeclared_types["SUPERSEDES"] == 1


def test_an_entity_carrying_several_types_conforms_if_any_pairing_does(graph):
    """Extraction gives an entity more than one label often enough that being
    strict here would report mistakes that are not mistakes."""
    graph.run(
        """
        MERGE (a:`__Entity__`:PERSON:PACKAGE {pack: $p, id: '_t_ambiguous'})
        MERGE (b:`__Entity__`:PACKAGE {pack: $p, id: '_t_urllib3'})
        MERGE (a)-[:MAINTAINS]->(b)
        """,
        p=PACK,
    )

    report = validate_triples(graph, PACK, CONSTRAINTS)

    assert report.conforming == 1


def test_violations_are_ordered_by_how_often_they_happen(graph):
    """A mistake made two hundred times is a rule worth fixing; one made once
    is noise."""
    for i in range(3):
        _link(graph, "PACKAGE", f"pkg{i}", "AUTHORED", "PACKAGE", "_t_urllib3")
    _link(graph, "ISSUE", "issue1", "MAINTAINS", "PACKAGE", "_t_urllib3")

    report = validate_triples(graph, PACK, CONSTRAINTS)

    assert [v.count for v in report.violations] == sorted(
        (v.count for v in report.violations), reverse=True
    )
    assert report.violations[0].relation == "AUTHORED"


def test_an_empty_graph_reports_nothing_rather_than_dividing_by_zero(graph):
    report = validate_triples(graph, PACK, CONSTRAINTS)

    assert report.total == 0
    assert report.conformance == 0.0

"""Forgetting one document, against a real database.

The invariant this file exists for is not "the delete runs". It is that the
delete removes *exactly* one document's worth of graph and leaves everything
another document still needs — which is a different question, and the reason
`reingest.py` does not use the engine's recipe unchanged.
"""

from __future__ import annotations

import pytest

from graphpack.reingest import forget_documents

pytestmark = [pytest.mark.integration, pytest.mark.graph]

PACK = "_graphpack_reingest_test"


class _NoVectorStore:
    """A system with no vector store.

    The graph half is what these tests are about, and a fake Qdrant would test
    the fake. `forget_documents` must cope with the attribute being absent,
    which is also its behaviour against a pack ingested with vectors disabled.
    """

    vector_store = None


@pytest.fixture
def graph(neo4j_session):
    """Two documents. One entity is mentioned by both, one by each.

    `shared` is the whole point: extraction MERGEs entities globally on id, so
    it is a single node that both documents reach. Deleting "document one's
    entities" by any property on the node would take it, and document two would
    silently lose a mention it still makes.
    """
    neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=PACK)
    neo4j_session.run(
        """
        CREATE (c1:Chunk {pack: $pack, ref_doc_id: 'doc:1', text: 'one'})
        CREATE (c2:Chunk {pack: $pack, ref_doc_id: 'doc:2', text: 'two'})
        CREATE (shared:`__Entity__` {pack: $pack, id: $pack + ':shared'})
        CREATE (only1:`__Entity__` {pack: $pack, id: $pack + ':only1'})
        CREATE (only2:`__Entity__` {pack: $pack, id: $pack + ':only2'})
        CREATE (c1)-[:MENTIONS]->(shared)
        CREATE (c2)-[:MENTIONS]->(shared)
        CREATE (c1)-[:MENTIONS]->(only1)
        CREATE (c2)-[:MENTIONS]->(only2)
        """,
        pack=PACK,
    )
    yield neo4j_session
    neo4j_session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=PACK)


def _entities(session) -> set[str]:
    return {
        row["id"]
        for row in session.run("MATCH (e:`__Entity__` {pack: $p}) RETURN e.id AS id", p=PACK)
    }


def test_an_entity_the_other_document_still_mentions_survives(graph):
    """The measurement that put this design in the plan: on tr-law, 965 of 5,130
    entities are mentioned by more than one document and the most-shared by
    1,175. The engine's own recipe deletes a document's entities by matching a
    `doc_id` property, which here would take all of them."""
    forget_documents(graph, _NoVectorStore(), PACK, ["doc:1"])

    assert f"{PACK}:shared" in _entities(graph)


def test_an_entity_nothing_mentions_any_more_is_collected(graph):
    """The other half. Leaving it would grow the graph on every re-ingest and
    quietly inflate every entity count the evaluation reads."""
    report = forget_documents(graph, _NoVectorStore(), PACK, ["doc:1"])

    assert f"{PACK}:only1" not in _entities(graph)
    assert report.entities_collected == 1


def test_the_other_document_is_untouched(graph):
    forget_documents(graph, _NoVectorStore(), PACK, ["doc:1"])

    remaining = graph.run(
        "MATCH (c:Chunk {pack: $p}) RETURN collect(c.ref_doc_id) AS docs", p=PACK
    ).single()["docs"]

    assert remaining == ["doc:2"]
    assert f"{PACK}:only2" in _entities(graph)


def test_forgetting_the_same_document_twice_is_harmless(graph):
    """A re-ingest that fails partway is re-run, so this is the ordinary case
    rather than an edge one."""
    forget_documents(graph, _NoVectorStore(), PACK, ["doc:1"])
    second = forget_documents(graph, _NoVectorStore(), PACK, ["doc:1"])

    assert second.absent == ["doc:1"]
    assert second.chunks_removed == 0


def test_a_document_the_graph_never_held_is_reported_not_raised(graph):
    """Also what a mistyped identifier looks like, which is why it is reported
    rather than passed over in silence."""
    report = forget_documents(graph, _NoVectorStore(), PACK, ["doc:nope"])

    assert report.absent == ["doc:nope"]
    assert _entities(graph) == {f"{PACK}:shared", f"{PACK}:only1", f"{PACK}:only2"}

"""Ground truth derived from the backbone, against a real database.

The graph is built the way the engine builds one, which is the point: entities
MERGEd on `id` so they are shared between documents, and chunks linked to them
with MENTIONS. A fixture that gave each document its own entity nodes would test
a data model that does not exist and would have hidden the reason this
generator scores over the corpus rather than per document.
"""

from __future__ import annotations

import textwrap

import pytest

from graphpack.eval.contract import load_eval_rules
from graphpack.eval.generators import backbone_edges
from graphpack.eval.runner import run_eval

pytestmark = [pytest.mark.integration, pytest.mark.graph]

PACK = "_graphpack_eval_test"

RULES = textwrap.dedent(
    """\
    tasks:
      - name: dependencies
        generator: backbone_edges
        relation: DEPENDS_ON
        endpoint_label: Package
        directed: true
    holdout: 0.0
    """
)

#: Stands for published metadata. Nobody annotated this.
BACKBONE = [("pypi:requests", "pypi:urllib3"), ("pypi:requests", "pypi:certifi")]


@pytest.fixture
def rules(tmp_path):
    (tmp_path / "eval.yaml").write_text(RULES, encoding="utf-8")
    return load_eval_rules(tmp_path / "eval.yaml")


@pytest.fixture
def graph(neo4j_session):
    session = neo4j_session
    session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=PACK)

    for identifier in ("pypi:requests", "pypi:urllib3", "pypi:certifi", "pypi:idna"):
        session.run("CREATE (:Package {pack: $p, id: $id, name: $id})", p=PACK, id=identifier)
    for start, end in BACKBONE:
        session.run(
            "MATCH (a:Package {pack: $p, id: $a}), (b:Package {pack: $p, id: $b}) "
            "CREATE (a)-[:DEPENDS_ON]->(b)",
            p=PACK,
            a=start,
            b=end,
        )

    def mention(document, text, canonical):
        """One chunk per document, entities MERGEd and shared — as the store does."""
        session.run(
            """
            MATCH (c:Package {pack: $p, id: $canonical})
            MERGE (chunk:Chunk {pack: $p, id: $doc})
              ON CREATE SET chunk.ref_doc_id = $doc, chunk.text = 'x'
            MERGE (e:`__Entity__`:PACKAGE {pack: $p, id: $text})
              ON CREATE SET e.name = $text
            MERGE (chunk)-[:MENTIONS]->(e)
            MERGE (e)-[:RESOLVED_AS {method: 'exact', score: 100}]->(c)
            """,
            p=PACK,
            doc=document,
            text=text,
            canonical=canonical,
        )

    def claim(a, b):
        session.run(
            "MATCH (x:`__Entity__` {pack: $p, id: $a}), (y:`__Entity__` {pack: $p, id: $b}) "
            "MERGE (x)-[:DEPENDS_ON]->(y)",
            p=PACK,
            a=a,
            b=b,
        )

    for text, canonical in (
        ("requests", "pypi:requests"),
        ("urllib3", "pypi:urllib3"),
        ("certifi", "pypi:certifi"),
    ):
        mention("doc1", text, canonical)
    claim("requests", "urllib3")  # true positive
    claim("urllib3", "certifi")  # false positive: the backbone says no
    # requests -> certifi is gold and unclaimed: a false negative.

    # doc2 mentions two packages the backbone does not relate. Nothing to find,
    # so nothing to be penalised for. urllib3 is the same node as in doc1.
    mention("doc2", "urllib3", "pypi:urllib3")
    mention("doc2", "idna", "pypi:idna")

    yield session
    session.run("MATCH (n {pack: $p}) DETACH DELETE n", p=PACK)


def test_gold_is_the_backbone_edges_some_document_discusses(graph, rules):
    predicted, gold, _ = backbone_edges(graph, PACK, rules.tasks[0])

    assert gold == {("pypi:requests", "pypi:urllib3"), ("pypi:requests", "pypi:certifi")}
    assert predicted == {("pypi:requests", "pypi:urllib3"), ("pypi:urllib3", "pypi:certifi")}


def test_a_pair_no_document_mentions_together_is_not_gold(graph, rules):
    """Otherwise the corpus is charged with missing relations nothing in it
    could have expressed."""
    graph.run(
        "MATCH (a:Package {pack: $p, id: 'pypi:idna'}), (b:Package {pack: $p, id: 'pypi:certifi'}) "
        "CREATE (a)-[:DEPENDS_ON]->(b)",
        p=PACK,
    )

    _, gold, _ = backbone_edges(graph, PACK, rules.tasks[0])

    assert ("pypi:idna", "pypi:certifi") not in gold


def test_mentions_are_read_through_the_chunk_not_the_entity(graph, rules):
    """The entity is shared between every document that mentions it, so its own
    ref_doc_id names one of them arbitrarily. Reading it instead of the chunk
    would make co-occurrence depend on which write landed last."""
    graph.run("MATCH (e:`__Entity__` {pack: $p}) SET e.ref_doc_id = 'somewhere-else'", p=PACK)

    _, gold, diagnostics = backbone_edges(graph, PACK, rules.tasks[0])

    assert len(gold) == 2
    assert diagnostics["documents_with_resolved_entities"] == 2


def test_the_scores_come_out_as_the_graph_says(graph, rules):
    report = run_eval(graph, PACK, rules)
    scores = report.results[0].scores

    assert (scores.true_positive, scores.false_positive, scores.false_negative) == (1, 1, 1)
    assert scores.precision == pytest.approx(0.5)
    assert scores.recall == pytest.approx(0.5)


def test_a_miss_says_which_stage_lost_it(graph, rules):
    """requests -> certifi: both ends resolved, nothing extracted between them,
    so extraction missed it rather than resolution."""
    report = run_eval(graph, PACK, rules)

    assert report.results[0].misses == {"no relation extracted": 1}


def test_a_relation_of_the_wrong_type_is_reported_as_such(graph, rules):
    """A different failure needing a different fix: the pair was related, just
    not as the ontology's DEPENDS_ON."""
    graph.run(
        "MATCH (x:`__Entity__` {pack: $p, id: 'requests'}), "
        "(y:`__Entity__` {pack: $p, id: 'certifi'}) MERGE (x)-[:USES]->(y)",
        p=PACK,
    )

    report = run_eval(graph, PACK, rules)

    assert "related, but as another type" in report.results[0].misses


def test_undirected_scoring_ignores_which_way_round_it_was(graph, tmp_path):
    """Some statements are symmetric, and judging the direction of a symmetric
    statement measures nothing."""
    (tmp_path / "eval.yaml").write_text(
        RULES.replace("directed: true", "directed: false"), encoding="utf-8"
    )
    undirected = load_eval_rules(tmp_path / "eval.yaml")

    predicted, gold, _ = backbone_edges(graph, PACK, undirected.tasks[0])

    assert all(a <= b for a, b in gold | predicted)


def test_no_gold_is_reported_rather_than_scored_as_failure(graph, rules, tmp_path):
    """An empty gold set means the generator found nothing, which is a different
    thing from the model finding nothing."""
    graph.run("MATCH (:Package {pack: $p})-[r:DEPENDS_ON]->() DELETE r", p=PACK)

    report = run_eval(graph, PACK, rules)

    assert report.results[0].scores.gold == 0

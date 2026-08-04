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

#: Keeps test entities out of the namespace a real ingest writes into.
TEST_ID_PREFIX = "_t_"

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
        """One chunk per document, entities MERGEd and shared — as the store does.

        Ids are prefixed on the way in. The uniqueness constraint the engine
        creates on `__Entity__.id` is not pack-scoped, so a test entity called
        "requests" collides with the one a real ingest wrote — which is how
        these tests started failing the first time the corpus was extracted.
        """
        text = TEST_ID_PREFIX + text
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
            a=TEST_ID_PREFIX + a,
            b=TEST_ID_PREFIX + b,
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
        "MATCH (x:`__Entity__` {pack: $p, id: $a}), "
        "(y:`__Entity__` {pack: $p, id: $b}) MERGE (x)-[:USES]->(y)",
        p=PACK,
        a=TEST_ID_PREFIX + "requests",
        b=TEST_ID_PREFIX + "certifi",
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


def test_every_generator_reports_the_diagnostics_the_report_prints():
    """The eval report is generic over generators and looks these up by key. A
    generator inventing its own names raised KeyError *after* the score had been
    computed — the number was right there and the command died printing it."""
    import inspect

    from graphpack.eval import generators as gen

    required = {"documents_carrying_gold", "documents_with_resolved_entities", "backbone_edges"}
    for name in gen.GENERATORS:
        source = inspect.getsource(getattr(gen, name))
        missing = {key for key in required if f'"{key}"' not in source}
        assert not missing, f"{name} does not report {sorted(missing)}"


@pytest.mark.unit
def test_a_task_reports_how_many_subjects_the_holdout_kept():
    """The diagnostics beside a score describe the whole corpus.

    The generator runs before the split, so `documents_carrying_gold` counts
    every document while precision and recall are computed on the held-out
    share. tr-law printed "710 of 710 documents carried gold" next to numbers
    scored on 213 — two quantities a reader would divide.
    """
    from graphpack.eval.contract import EvalRules, Task
    from graphpack.eval.runner import run_eval

    task = Task(
        name="t",
        generator="fake",
        relation="R",
        backbone_relation="R",
        endpoint_label="L",
    )
    rules = EvalRules(tasks=(task,), holdout=0.5, seed=0)
    gold = {(f"s{i}", "o") for i in range(10)}

    def generator(_session, _pack, _task):
        return set(gold), set(gold), {"documents_carrying_gold": 10}

    from graphpack.eval import runner as runner_module

    original = dict(runner_module.GENERATORS)
    runner_module.GENERATORS["fake"] = generator
    try:
        report = run_eval(None, "p", rules)
    finally:
        runner_module.GENERATORS.clear()
        runner_module.GENERATORS.update(original)

    assert report.results[0].held_out == 5
    # And the whole-corpus diagnostic is still whole-corpus, not silently
    # rewritten to match — the point is that both are visible.
    assert report.results[0].diagnostics["documents_carrying_gold"] == 10


@pytest.mark.unit
def test_require_relation_keeps_the_denominator_of_the_loose_task():
    """The flattering bug this flag nearly shipped with.

    `ingested` is the proxy for "this document went through extraction", and it
    is what restricts gold. Deriving it from the *prediction* drops every
    document extraction failed on out of the gold set, so recall is reported
    over the documents it already succeeded on. The strict task read 59.0%
    that way and 13.1% over the same documents the loose task uses.
    """
    from graphpack.eval.contract import Task
    from graphpack.eval.generators import document_edges

    # Two documents, both extracted (both mention the statute). Only one of them
    # also has the CITES relation.
    session = _FakeSession(
        backbone={"dec:1": ["kanun:1"], "dec:2": ["kanun:1"]},
        mentions={"dec:1": ["kanun:1"], "dec:2": ["kanun:1"]},
        related={"dec:1": ["kanun:1"]},
    )
    task = Task(
        name="t",
        generator="document_edges",
        relation="CITES",
        backbone_relation="CITES",
        endpoint_label="Statute",
        source_label="Decision",
        require_relation=True,
    )

    predicted, gold, _ = document_edges(session, "p", task)

    # Both documents stay in gold — the one extraction missed is the point.
    assert gold == {("dec:1", "kanun:1"), ("dec:2", "kanun:1")}
    assert predicted == {("dec:1", "kanun:1")}


@pytest.mark.unit
def test_without_require_relation_a_mention_is_enough():
    """What this generator has always scored, now said out loud.

    The task declares a relation and, by default, never checks it. That is a
    fair measurement of a real thing and not the thing the name suggests — the
    graph held 170 CITES relations while this reported 97% precision against
    1,242 gold edges.
    """
    from graphpack.eval.contract import Task
    from graphpack.eval.generators import document_edges

    session = _FakeSession(
        backbone={"dec:1": ["kanun:1"]},
        mentions={"dec:1": ["kanun:1"]},
        related={},
    )
    task = Task(
        name="t",
        generator="document_edges",
        relation="CITES",
        backbone_relation="CITES",
        endpoint_label="Statute",
        source_label="Decision",
    )

    predicted, gold, _ = document_edges(session, "p", task)

    assert predicted == gold == {("dec:1", "kanun:1")}


class _FakeSession:
    """Enough of a Neo4j session to tell the generator's three queries apart."""

    def __init__(self, backbone, mentions, related):
        self._backbone = backbone
        self._mentions = mentions
        self._related = related

    def run(self, query, **_kwargs):
        if "RESOLVED_AS" in query and "]->(other" in query:
            rows = [{"document": d, "entities": list(e)} for d, e in self._related.items()]
        elif "RESOLVED_AS" in query:
            rows = [{"document": d, "entities": list(e)} for d, e in self._mentions.items()]
        elif "count(" in query:
            rows = [{"n": 0}]
        else:
            rows = [{"document": d, "targets": list(t)} for d, t in self._backbone.items()]
        return _Result(rows)


class _Result(list):
    """A list that also answers `.single()`, which the diagnostics use."""

    def single(self):
        return self[0] if self else None

"""Reranking: the contract, and the invariant that it stays off.

The measurement gate for this phase lives in `docs/RESULTS.md` — with rerank
off, the hybrid run must reproduce 0.777 / 0.953 exactly. These are the cheap
checks that guard the same property structurally: a reranker that leaks into a
default run would move every published number at once.
"""

from __future__ import annotations

import re
import textwrap

import pytest

from graphpack.agent.contract import RetrievalError, load_retrieval_rules
from graphpack.bench.rerank import rerank_nodes

INTENTS = """\
intents:
  - name: dependencies
    description: What this needs.
    entity: PACKAGE
    match: ["require"]
    cypher: |
      MATCH (p {pack: $pack})-[:DEPENDS_ON]->(d) WHERE p.id = $entity_id
      RETURN d.id AS id
"""


@pytest.fixture
def rules(tmp_path):
    def _make(body: str) -> object:
        (tmp_path / "retrieval.yaml").write_text(body, encoding="utf-8")
        return load_retrieval_rules(tmp_path / "retrieval.yaml")

    return _make


@pytest.mark.unit
def test_a_pack_without_a_rerank_block_declares_no_reranker(rules):
    """The default, and the one that keeps every published number valid."""
    assert rules(INTENTS).rerank is None


@pytest.mark.unit
def test_an_empty_rerank_block_is_also_no_reranker(rules):
    """`rerank:` with nothing under it is a comment, not a configuration."""
    assert rules(INTENTS + "\nrerank:\n").rerank is None


@pytest.mark.unit
def test_a_rerank_block_is_parsed(rules):
    parsed = rules(
        INTENTS
        + textwrap.dedent("""
            rerank:
              model: BAAI/bge-reranker-large
              top_n: 10
              fetch_factor: 4
            """)
    ).rerank
    assert parsed.model == "BAAI/bge-reranker-large"
    assert parsed.top_n == 10
    # The over-fetch is the confounder this phase has to keep visible: scoring
    # 20 reranked results means retrieving 80 first.
    assert parsed.candidates_for(20) == 80


@pytest.mark.unit
def test_fetch_factor_defaults_to_three(rules):
    parsed = rules(INTENTS + "\nrerank:\n  model: BAAI/bge-reranker-large\n").rerank
    assert parsed.candidates_for(20) == 60
    assert parsed.top_n is None  # keeps the run's own depth


@pytest.mark.unit
@pytest.mark.parametrize(
    "block, expected",
    [
        ("rerank:\n  top_n: 10\n", "missing 'model'"),
        ("rerank:\n  model: m\n  top_n: 0\n", "'top_n' must be a positive integer"),
        ("rerank:\n  model: m\n  fetch_factor: -1\n", "'fetch_factor' must be a positive"),
        ("rerank:\n  model: m\n  topn: 10\n", "unknown key(s) ['topn']"),
        ("rerank: BAAI/bge-reranker-large\n", "'rerank' must be a mapping"),
    ],
)
def test_a_malformed_rerank_block_is_rejected_at_load(rules, block, expected):
    """Rejected here rather than at the end of a 2,556-query run."""
    with pytest.raises(RetrievalError, match=re.escape(expected)):
        rules(INTENTS + "\n" + block)


@pytest.mark.unit
def test_reranking_nothing_does_not_load_a_model(monkeypatch):
    """A failed retrieval must not cost 1.3 GB.

    Every query whose retrieval returned empty would otherwise construct a
    cross-encoder to sort zero results.
    """

    def explode(_model):
        raise AssertionError("empty input must not load the model")

    monkeypatch.setattr("graphpack.bench.rerank.load_reranker", explode)
    assert rerank_nodes([], "anything", "BAAI/bge-reranker-large") == []


@pytest.mark.unit
def test_reranking_reorders_by_cross_encoder_score(monkeypatch):
    nodes = [_node("worst"), _node("best"), _node("middling")]
    monkeypatch.setattr(
        "graphpack.bench.rerank.load_reranker",
        lambda _m: _Encoder({"worst": 0.1, "best": 0.9, "middling": 0.5}),
    )

    ranked = rerank_nodes(nodes, "q", "m")

    assert [n.node.get_content() for n in ranked] == ["best", "middling", "worst"]


@pytest.mark.unit
def test_top_n_truncates_after_reordering_not_before(monkeypatch):
    """Truncating first would throw away the result the reranker exists to find.

    The best passage is last in retrieval order here, which is the whole case
    for reranking; cutting to 1 before scoring would return the worst one.
    """
    nodes = [_node("worst"), _node("middling"), _node("best")]
    monkeypatch.setattr(
        "graphpack.bench.rerank.load_reranker",
        lambda _m: _Encoder({"worst": 0.1, "best": 0.9, "middling": 0.5}),
    )

    ranked = rerank_nodes(nodes, "q", "m", top_n=1)

    assert [n.node.get_content() for n in ranked] == ["best"]


@pytest.mark.unit
def test_a_failing_encoder_keeps_retrieval_order_instead_of_ending_the_run(monkeypatch):
    """One bad query is not a bad run — the lesson from score_chunks."""

    class Broken:
        def predict(self, _pairs):
            raise RuntimeError("out of memory")

    nodes = [_node("a"), _node("b")]
    monkeypatch.setattr("graphpack.bench.rerank.load_reranker", lambda _m: Broken())

    ranked = rerank_nodes(nodes, "q", "m")

    assert [n.node.get_content() for n in ranked] == ["a", "b"]


@pytest.mark.unit
def test_retrieve_nodes_without_a_reranker_does_not_widen_the_leg(monkeypatch):
    """The regression that would silently move every published number.

    With no reranker the retrieval depth must be exactly what was asked for.
    Retrieving deeper "just in case" changes the vector leg's own results.
    """
    from graphpack.bench import runner

    seen = {}
    monkeypatch.setattr(runner, "_retriever", lambda _s, depth, _h: _Retriever(seen, depth))
    monkeypatch.setattr("graphpack.loop.run", lambda coro: coro)

    runner.retrieve_nodes(object(), "q", 20)

    assert seen["depth"] == 20


@pytest.mark.unit
def test_retrieve_nodes_with_a_reranker_widens_the_leg_before_cutting(monkeypatch):
    """And with one, the widening is real — otherwise reranking sorts the same
    twenty results and the phase measures nothing."""
    from graphpack.agent.contract import Rerank
    from graphpack.bench import runner

    seen = {}
    monkeypatch.setattr(runner, "_retriever", lambda _s, depth, _h: _Retriever(seen, depth))
    monkeypatch.setattr("graphpack.loop.run", lambda coro: coro)
    monkeypatch.setattr("graphpack.bench.rerank.rerank_nodes", lambda nodes, *a, **k: nodes)

    runner.retrieve_nodes(object(), "q", 20, rerank=Rerank(model="m", fetch_factor=3))

    assert seen["depth"] == 60


class _Retriever:
    def __init__(self, seen, depth):
        seen["depth"] = depth

    def aretrieve(self, _question):
        return []


class _Encoder:
    def __init__(self, scores):
        self.scores = scores

    def predict(self, pairs):
        return [self.scores[text] for _q, text in pairs]


def _node(text: str):
    class _Inner:
        def get_content(self):
            return text

    class _Scored:
        node = _Inner()

    return _Scored()

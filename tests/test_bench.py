"""Retrieval scoring against a published benchmark's ground truth.

The arithmetic is the part worth pinning. A rank-aware metric computed the wrong
way still produces a plausible number, and a plausible wrong number in a
comparison table is worse than no table.
"""

from __future__ import annotations

import json

import pytest

from graphpack.bench.metrics import MRR_DEPTH, QueryResult, score
from graphpack.bench.runner import BenchError, BenchQuery, load_gold, run_query

pytestmark = pytest.mark.unit


def result(ranked, gold, unattributed=0, question="q"):
    return QueryResult(
        question=question,
        ranked=tuple(ranked),
        gold=frozenset(gold),
        unattributed=unattributed,
    )


# ----------------------------------------------------------------------
# Rank is the point
# ----------------------------------------------------------------------


def test_the_rank_of_the_first_gold_article_is_what_mrr_counts():
    assert result(["a", "b", "gold"], ["gold"]).first_hit == 3


def test_a_gold_article_below_the_cut_does_not_count():
    """MRR@10 is a different quantity from MRR over an unbounded list, and the
    published number is the bounded one."""
    ranked = [f"x{i}" for i in range(MRR_DEPTH)] + ["gold"]

    assert result(ranked, ["gold"]).first_hit is None


def test_reciprocal_rank_is_averaged_over_every_query_not_just_the_hits():
    """A query that found nothing contributes zero, not nothing. Dividing by the
    number of successful queries would report the score of a system that
    answered only what it was already good at."""
    scores = score([result(["gold"], ["gold"]), result(["miss"], ["gold"])])

    assert scores.mrr == pytest.approx(0.5)


def test_hit_at_k_looks_only_at_the_first_k():
    hit = result(["a", "b", "c", "gold"], ["gold"])

    assert hit.hit_at(4) is True
    assert hit.hit_at(2) is False


def test_any_one_gold_article_is_a_hit():
    """The benchmark's questions rest on several articles, and this counts a
    query as a hit when retrieval found any one of them.

    An earlier version of this docstring said that was "the quantity the paper
    reports". It is not, and reading the paper is what settled it: MultiHop-RAG
    defines Hit@K as "the fraction of evidence that appears in the top-K
    retrieved set" — recall over the evidence set, which is the stricter
    quantity this test's own last line used to call different. Our number is
    the easier one, and RESULTS.md now says so beside the published table."""
    assert result(["b"], ["a", "b", "c"]).hit_at(1) is True


# ----------------------------------------------------------------------
# Queries with no answer
# ----------------------------------------------------------------------


def test_null_queries_are_scored_apart_from_the_rest():
    """301 of MultiHop-RAG's queries have no answer in the corpus. Averaging
    them in would reward a system that returns nothing for everything."""
    scores = score([result(["gold"], ["gold"]), result([], [])])

    assert scores.queries == 1
    assert scores.null_queries == 1
    assert scores.hit_rate(1) == 1.0


def test_a_null_query_is_right_when_it_retrieves_nothing():
    scores = score([result([], []), result(["something"], [])])

    assert scores.null_queries == 2
    assert scores.null_correct == 1


# ----------------------------------------------------------------------
# The mapping from passages to articles
# ----------------------------------------------------------------------


class FakeSystem:
    """Stands in for the engine's vector index.

    Retrieval goes through the index rather than `system.search`, which returns
    no document identity at all — its `source` field is the retriever's name, so
    every passage once attributed to an article called "Qdrant vector".
    """

    class _Node:
        def __init__(self, ref):
            self.node = self
            self.ref_doc_id = ref

    class _Retriever:
        def __init__(self, docs, top_k):
            self.docs, self.similarity_top_k = docs, top_k

        async def aretrieve(self, question):
            return [FakeSystem._Node(d) for d in self.docs[: self.similarity_top_k]]

    class _Index:
        def __init__(self, docs):
            self.docs = docs

        def as_retriever(self, similarity_top_k=10):
            return FakeSystem._Retriever(self.docs, similarity_top_k)

    def __init__(self, docs, hybrid_docs=None):
        self.vector_index = FakeSystem._Index(docs)
        # A system built without ingesting has no hybrid retriever at all — the
        # engine's BM25 leg is an in-memory docstore owned by the object that
        # ingested. `None` is that state, and it must not be scored as a miss.
        self.hybrid_retriever = (
            FakeSystem._Retriever(hybrid_docs, 10) if hybrid_docs is not None else None
        )


def test_several_chunks_of_one_article_are_one_result():
    """Retrieval returns chunks; the benchmark scores articles. Counting a
    chunk each would let one article fill the whole ranked list and make Hit@10
    a measure of chunk density."""
    got = run_query(FakeSystem(["mhr:a", "mhr:a", "mhr:b"]), BenchQuery("q", frozenset({"mhr:b"})))

    assert got.ranked == ("mhr:a", "mhr:b")


def test_the_rank_an_article_earned_is_its_first_chunk():
    got = run_query(FakeSystem(["mhr:a", "mhr:b", "mhr:a"]), BenchQuery("q", frozenset({"mhr:a"})))

    assert got.first_hit == 1


def test_a_passage_naming_no_article_is_counted_not_dropped():
    """If the corpus stops carrying document ids, every score goes to zero and
    looks like a retrieval failure. This is the number that says otherwise."""
    got = run_query(FakeSystem(["mhr:a", "", "mhr:b"]), BenchQuery("q", frozenset({"mhr:a"})))

    assert got.unattributed == 1
    assert got.ranked == ("mhr:a", "mhr:b")


def test_attribution_is_reported_as_a_share_of_everything_retrieved():
    scores = score([result(["a", "b"], ["a"], unattributed=2)])

    assert scores.retrieved == 4
    assert scores.attribution_rate == pytest.approx(0.5)


# ----------------------------------------------------------------------
# Reading the gold
# ----------------------------------------------------------------------


def test_a_question_collects_every_article_its_evidence_names(tmp_path):
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"question": "q1", "article": "mhr:a", "question_type": "inference_query"},
                {"question": "q1", "article": "mhr:b", "question_type": "inference_query"},
                {"question": "q2", "article": "mhr:c", "question_type": "comparison_query"},
            ]
        ),
        encoding="utf-8",
    )

    queries = {q.question: q for q in load_gold(gold)}

    assert queries["q1"].gold == frozenset({"mhr:a", "mhr:b"})
    assert queries["q1"].kind == "inference_query"


def test_queries_with_no_evidence_are_carried_through_with_empty_gold(tmp_path):
    """They never reach gold.jsonl — a null query's evidence list is empty and
    the derive step requires an article — so they come back from queries.jsonl.
    Losing them would silently drop the only questions that test saying no."""
    (tmp_path / "gold.jsonl").write_text(
        json.dumps({"question": "q1", "article": "mhr:a"}) + "\n", encoding="utf-8"
    )
    (tmp_path / "queries.jsonl").write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"query": "q1", "question_type": "inference_query"},
                {"query": "q-null", "question_type": "null_query"},
            ]
        ),
        encoding="utf-8",
    )

    queries = load_gold(tmp_path / "gold.jsonl", tmp_path / "queries.jsonl")

    null = [q for q in queries if not q.gold]
    assert [q.question for q in null] == ["q-null"]
    assert len(queries) == 2, "a question with evidence must not also arrive as a null"


def test_missing_gold_says_what_to_run(tmp_path):
    with pytest.raises(BenchError, match="backbone fetch"):
        load_gold(tmp_path / "absent.jsonl")


# ----------------------------------------------------------------------
# Refusing to report a number about nothing
# ----------------------------------------------------------------------


class FakeSession:
    def __init__(self, ids):
        self.ids = ids

    def run(self, query, **params):
        return [{"id": i} for i in params["ids"] if i in self.ids]


def test_gold_the_graph_does_not_hold_stops_the_run():
    """Every miss would be the loader's rather than the retriever's, and the
    number would describe nothing."""
    from graphpack.bench.runner import check_gold_is_reachable

    queries = [BenchQuery("q", frozenset({"mhr:a", "mhr:ghost"}))]

    with pytest.raises(BenchError, match="not in the graph"):
        check_gold_is_reachable(FakeSession({"mhr:a"}), "bench-wiki", queries)


def test_gold_that_is_all_present_passes_quietly():
    from graphpack.bench.runner import check_gold_is_reachable

    check_gold_is_reachable(
        FakeSession({"mhr:a", "mhr:b"}), "bench-wiki", [BenchQuery("q", frozenset({"mhr:a"}))]
    )


def test_nothing_retrieved_is_not_reported_as_a_broken_mapping():
    """Two different faults. An un-ingested corpus retrieves nothing; a broken
    document-id mapping retrieves plenty and can attribute none of it. Reporting
    the first as 0% attribution blamed the mapping for search having returned
    nothing, and sent the reader to the wrong place."""
    scores = score([result([], ["gold"])])

    assert scores.attribution_rate is None


def test_retrieved_but_unattributable_is_reported_as_such():
    scores = score([result([], ["gold"], unattributed=5)])

    assert scores.attribution_rate == 0.0


def test_asking_for_hybrid_without_it_raises_rather_than_scoring_zero():
    """The failure mode this guards is silent, not loud. A system that never
    ingested has no BM25 docstore, and a `--hybrid` run against it would
    otherwise fall through to an empty result set and publish a hybrid number
    that is really a vector number, or a zero. Either is a wrong number in a
    comparison table."""
    from graphpack.bench.runner import RetrieverUnavailable

    with pytest.raises(RetrieverUnavailable):
        run_query(FakeSystem(["mhr:a"]), BenchQuery("q", frozenset({"mhr:a"})), hybrid=True)


def test_the_hybrid_leg_is_scored_when_it_is_there():
    """And it is a different retriever, so it can return different documents."""
    system = FakeSystem(["mhr:vector"], hybrid_docs=["mhr:fused", "mhr:vector"])

    assert run_query(system, BenchQuery("q", frozenset({"mhr:fused"}))).ranked == ("mhr:vector",)
    assert run_query(system, BenchQuery("q", frozenset({"mhr:fused"})), hybrid=True).ranked == (
        "mhr:fused",
        "mhr:vector",
    )


def test_both_legs_are_scored_at_the_depth_asked_for():
    """The vector leg is built per call at the requested depth; the fusion
    retriever was built during ingest at whatever depth the engine chose.
    Comparing them without settling that would compare depths."""
    system = FakeSystem(["v1", "v2", "v3"], hybrid_docs=["h1", "h2", "h3"])
    system.hybrid_retriever.similarity_top_k = 99  # as the engine left it

    query = BenchQuery("q", frozenset({"h1"}))

    assert run_query(system, query, top_k=2).ranked == ("v1", "v2")
    assert run_query(system, query, top_k=2, hybrid=True).ranked == ("h1", "h2")


def test_retrieval_that_returns_no_identity_is_counted_as_unattributed():
    """The engine's `system.search` returns {content, file_name, file_type, rank,
    score, source} and no document id — `source` is the retriever's name. Every
    passage attributed to an article called "Qdrant vector", which is not a miss
    and must not be scored as one."""
    got = run_query(FakeSystem(["", "mhr:a", ""]), BenchQuery("q", frozenset({"mhr:a"})))

    assert got.unattributed == 2
    assert got.ranked == ("mhr:a",)


# ----------------------------------------------------------------------
# The paper's metric, beside ours
# ----------------------------------------------------------------------


def test_the_papers_hit_at_k_is_recall_over_the_evidence_set():
    """MultiHop-RAG defines Hit@K as "the fraction of evidence that appears in
    the top-K retrieved set" (arXiv:2401.15391, §2.3). A query resting on four
    articles scores 0.25 there when one is found, and 1.0 here under `hit_at`.

    This repository published the second under the first's name, and the size of
    the resulting lead — 0.759 against a best published 0.586 — was the clue."""
    got = result(["a"], ["a", "b", "c", "d"])

    assert got.hit_at(10) is True
    assert got.evidence_recall_at(10) == pytest.approx(0.25)


def test_evidence_recall_is_one_only_when_everything_is_found():
    assert result(["a", "b"], ["a", "b"]).evidence_recall_at(10) == pytest.approx(1.0)


def test_evidence_recall_respects_the_cut():
    """Same k semantics as hit_at: what is below the line does not count."""
    got = result(["x", "y", "a", "b"], ["a", "b"])

    assert got.evidence_recall_at(2) == pytest.approx(0.0)
    assert got.evidence_recall_at(4) == pytest.approx(1.0)


def test_a_null_query_scores_zero_rather_than_dividing_by_zero():
    assert result([], []).evidence_recall_at(10) == 0.0


def test_both_quantities_are_aggregated_side_by_side():
    """So a report can print them together and the difference can be read off
    rather than argued about."""
    scores = score([result(["a"], ["a", "b"]), result(["c", "d"], ["c", "d"])])

    assert scores.hit_rate(10) == pytest.approx(1.0)
    assert scores.evidence_recall(10) == pytest.approx(0.75)


def test_evidence_matching_survives_the_whitespace_chunking_introduces():
    """The evidence is quoted verbatim from the articles, so it is present — but
    chunking and the metadata prepended to every chunk leave the line breaks
    different. Matching raw strings loses real hits to a newline."""
    from graphpack.bench.runner import _normalise

    assert _normalise("The  quick\nbrown\tfox") == _normalise("the quick brown fox")

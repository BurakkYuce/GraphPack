"""Retrieval metrics, as the benchmark's own paper reports them.

`eval` scores extraction: a set of predicted edges against a set of gold ones,
where order means nothing. Retrieval is a different quantity — the gold articles
are somewhere in a ranked list, and *where* is most of what matters. A system
that puts the right article tenth is not the same as one that puts it first, and
precision/recall cannot tell them apart.

So: Hit@k and MRR@10, which is what MultiHop-RAG reports and therefore the pair
worth comparing against. Both carry an interval, for the same reason the other
metrics do — a score without one is a number pretending to be a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from graphpack.eval.metrics import _wilson

#: Where the ranked list is cut for MRR. The benchmark's number is MRR@10;
#: reporting MRR over an unbounded list would not be the same quantity.
MRR_DEPTH = 10


@dataclass(frozen=True)
class QueryResult:
    """One query's ranked retrieval, already reduced to article ids."""

    question: str
    ranked: tuple[str, ...]
    gold: frozenset[str]
    #: Passages that named no article. Counted rather than dropped: a mapping
    #: that quietly stops working shows up here as a large number, instead of
    #: as a score of zero that looks like a retrieval failure.
    unattributed: int = 0

    @property
    def first_hit(self) -> int | None:
        """1-based rank of the first gold article, or None within MRR_DEPTH."""
        for rank, article in enumerate(self.ranked[:MRR_DEPTH], start=1):
            if article in self.gold:
                return rank
        return None

    def hit_at(self, k: int) -> bool:
        return bool(self.gold & set(self.ranked[:k]))

    def evidence_recall_at(self, k: int) -> float:
        """The paper's Hit@K: *the fraction of evidence* in the top K.

        Not the same quantity as ``hit_at``, and the difference is not small. A
        query resting on four articles scores 1.0 here only when all four are
        retrieved; ``hit_at`` scores it 1.0 for any one of them. On a multi-hop
        benchmark — where the whole premise is that an answer needs several
        pieces — that is the demanding version and the one MultiHop-RAG quotes:

            "Hit@K metric measures the fraction of evidence that appears in the
            top-K retrieved set."  (arXiv:2401.15391, §2.3)

        Reported beside ours rather than instead of it. Ours answers "did
        retrieval find *an* answer", which is a fair question and a different
        one; publishing it under the paper's name was the mistake this fixes.
        """
        if not self.gold:
            return 0.0
        return len(self.gold & set(self.ranked[:k])) / len(self.gold)


@dataclass(frozen=True)
class BenchScores:
    """What a benchmark run produced."""

    queries: int
    hits: dict[int, int]
    #: Summed evidence recall per k — the paper's Hit@K before averaging. Kept
    #: apart from ``hits`` because they are different quantities with the same
    #: name in two different papers; see ``QueryResult.evidence_recall_at``.
    evidence: dict[int, float]
    #: Sum of 1/rank over queries with a hit inside MRR_DEPTH.
    reciprocal: float
    unattributed: int
    retrieved: int
    #: Queries the gold says have no answer. Scored separately: a null query
    #: retrieving nothing is correct, and averaging it in with the rest would
    #: reward a system that returns nothing for everything.
    null_queries: int = 0
    null_correct: int = 0
    diagnostics: dict = field(default_factory=dict)

    @property
    def mrr(self) -> float:
        return self.reciprocal / self.queries if self.queries else 0.0

    def hit_rate(self, k: int) -> float:
        return self.hits.get(k, 0) / self.queries if self.queries else 0.0

    def evidence_recall(self, k: int) -> float:
        """The paper's Hit@K, averaged over answerable queries."""
        return self.evidence.get(k, 0.0) / self.queries if self.queries else 0.0

    def interval(self, k: int) -> tuple[float, float]:
        return _wilson(self.hits.get(k, 0), self.queries)

    @property
    def attribution_rate(self) -> float | None:
        """Share of retrieved passages that reached an article.

        Below 1.0 means the corpus is carrying document ids the benchmark cannot
        map back to the graph — which makes every score below it meaningless
        rather than merely bad.

        ``None`` when nothing was retrieved at all, which is a different fault:
        an un-ingested corpus, not a broken mapping. Reporting it as 0% blamed
        the mapping for search having returned nothing.
        """
        if not self.retrieved:
            return None
        return (self.retrieved - self.unattributed) / self.retrieved


def score(results: list[QueryResult], ks: tuple[int, ...] = (1, 2, 4, 10)) -> BenchScores:
    """Aggregate per-query retrievals into the reported numbers."""
    answerable = [r for r in results if r.gold]
    null = [r for r in results if not r.gold]

    hits = {k: sum(1 for r in answerable if r.hit_at(k)) for k in ks}
    evidence = {k: sum(r.evidence_recall_at(k) for r in answerable) for k in ks}
    reciprocal = sum(1.0 / r.first_hit for r in answerable if r.first_hit)

    return BenchScores(
        queries=len(answerable),
        hits=hits,
        evidence=evidence,
        reciprocal=reciprocal,
        unattributed=sum(r.unattributed for r in results),
        retrieved=sum(len(r.ranked) + r.unattributed for r in results),
        null_queries=len(null),
        # A null query is answered correctly by retrieving nothing. This is the
        # weakest of the numbers here — the benchmark's own null handling is an
        # answer-level judgement, not a retrieval one — so it is reported apart
        # from the rest rather than folded in.
        null_correct=sum(1 for r in null if not r.ranked),
        diagnostics={
            "ks": list(ks),
            "answerable": len(answerable),
            "null": len(null),
        },
    )

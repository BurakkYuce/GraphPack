"""Run a pack's benchmark queries and score what came back.

The corpus half of the pipeline, measured against somebody else's ground truth.
Each query goes through the same hybrid search the agent uses, the passages are
reduced to the articles they came from, and the ranked article list is scored
against the evidence the benchmark ships.

Two things here are deliberately loud rather than convenient. Passages that
cannot be traced to an article are counted, because a document-id mapping that
silently breaks would otherwise look like a retrieval failure. And a query whose
gold names an article the graph does not hold is refused before scoring, because
a benchmark that quietly drops its own ground truth reports a number about
nothing.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from graphpack.bench.metrics import MRR_DEPTH, BenchScores, QueryResult, score

logger = logging.getLogger(__name__)

#: How deep to retrieve. Enough to fill MRR@10 and the largest Hit@k, with room
#: for passages that share an article — several chunks of one article collapse
#: to one entry, so the ranked list is shorter than the passage list.
DEFAULT_TOP_K = 30


class BenchError(Exception):
    """Raised when a benchmark cannot be run as specified."""


@dataclass(frozen=True)
class BenchQuery:
    question: str
    gold: frozenset[str]
    kind: str = ""


def load_gold(gold_path: Path, queries_path: Path | None = None) -> list[BenchQuery]:
    """Read the benchmark's questions and the articles each rests on.

    `gold.jsonl` holds one row per (question, evidence article). Questions with
    no evidence never reach it — the benchmark's null queries have empty
    evidence lists by design — so they are read back from `queries.jsonl` and
    carried through with empty gold, to be scored apart.
    """
    if not gold_path.is_file():
        raise BenchError(f"{gold_path}: no gold. Run `graphpack backbone fetch` first.")

    by_question: dict[str, set[str]] = {}
    kinds: dict[str, str] = {}
    for row in _rows(gold_path):
        question = row.get("question")
        article = row.get("article")
        if not question or not article:
            continue
        by_question.setdefault(question, set()).add(article)
        kinds.setdefault(question, str(row.get("question_type") or ""))

    queries = [
        BenchQuery(question=q, gold=frozenset(a), kind=kinds.get(q, ""))
        for q, a in by_question.items()
    ]

    if queries_path and queries_path.is_file():
        seen = set(by_question)
        for row in _rows(queries_path):
            question = row.get("query")
            if question and question not in seen:
                seen.add(question)
                queries.append(
                    BenchQuery(
                        question=question,
                        gold=frozenset(),
                        kind=str(row.get("question_type") or ""),
                    )
                )

    return queries


def _rows(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def check_gold_is_reachable(session, pack: str, queries: list[BenchQuery]) -> None:
    """Refuse to score against gold the graph does not hold.

    If the evidence names articles that were never loaded, every miss is the
    loader's rather than the retriever's, and the resulting number describes
    nothing. Better to stop here than to publish it.
    """
    wanted = {article for query in queries for article in query.gold}
    if not wanted:
        return
    rows = session.run(
        "MATCH (a:Article {pack: $pack}) WHERE a.id IN $ids RETURN a.id AS id",
        pack=pack,
        ids=sorted(wanted),
    )
    missing = wanted - {row["id"] for row in rows}
    if missing:
        raise BenchError(
            f"{len(missing)} of {len(wanted)} gold articles are not in the graph "
            f"(e.g. {sorted(missing)[:3]}). Load the backbone before benchmarking."
        )


class RetrieverUnavailable(Exception):
    """Raised when the requested retrieval leg does not exist in this process."""


def _retriever(system, top_k: int, hybrid: bool):
    """The retriever to score with.

    Both legs return LlamaIndex nodes, so ``ref_doc_id`` survives either way.
    That is the property the benchmark actually depends on — see ``retrieve``.
    """
    if not hybrid:
        return system.vector_index.as_retriever(similarity_top_k=top_k)

    retriever = getattr(system, "hybrid_retriever", None)
    if retriever is None:
        raise RetrieverUnavailable(
            "This system has no hybrid retriever. The engine's BM25 leg is an "
            "in-memory docstore owned by the process that ingested, so hybrid "
            "retrieval only exists in a process that has ingested — run "
            "`graphpack bench <pack> --ingest --hybrid`."
        )

    # The vector leg is constructed per call at the depth asked for; the fusion
    # retriever was constructed during ingest at whatever depth the engine
    # configured. Comparing the two at different depths would be a comparison of
    # depths, so the depth is set here — and said out loud when it cannot be.
    if hasattr(retriever, "similarity_top_k"):
        retriever.similarity_top_k = top_k
    else:
        logger.warning(
            "The fusion retriever exposes no similarity_top_k, so this run's depth is "
            "the engine's rather than the %d asked for. The hybrid and vector numbers "
            "are then not directly comparable.",
            top_k,
        )
    return retriever


def retrieve(system, question: str, top_k: int, hybrid: bool = False) -> list[str]:
    """Retrieved chunks, as the ids of the documents they came from.

    Not through ``system.search``: that returns
    ``{content, file_name, file_type, rank, score, source}`` and no document
    identity at all — ``source`` is the retriever's name, so every passage
    attributed to an article called "Qdrant vector" and the benchmark scored a
    confident zero. Retrieving through a retriever keeps ``ref_doc_id``, which
    is the id the pack's corpus block assigned.

    ``hybrid`` scores the engine's fusion retriever — vector, BM25 and the
    property graph — instead of the vector leg alone. It is off by default and
    unavailable in a process that has not ingested, because the BM25 docstore
    lives in memory and belongs to the object that built it.
    """
    from graphpack.loop import run

    retriever = _retriever(system, top_k, hybrid)
    nodes = run(retriever.aretrieve(question))
    return [(node.node.ref_doc_id or "").strip() for node in nodes]


def run_query(
    system, query: BenchQuery, top_k: int = DEFAULT_TOP_K, hybrid: bool = False
) -> QueryResult:
    """Retrieve for one question and reduce the chunks to ranked articles."""
    try:
        documents = retrieve(system, query.question, top_k, hybrid=hybrid)
    except RetrieverUnavailable:
        raise  # a missing leg is a setup error, not a query that scored zero
    except Exception as exc:
        logger.warning("Retrieval failed for %r — %s", query.question[:60], exc)
        documents = []

    ranked: list[str] = []
    unattributed = 0
    for article in documents:
        if not article:
            unattributed += 1
            continue
        # Several chunks of one article are one retrieval result. Keeping the
        # first occurrence preserves the rank the article actually earned.
        if article not in ranked:
            ranked.append(article)

    return QueryResult(
        question=query.question,
        ranked=tuple(ranked),
        gold=query.gold,
        unattributed=unattributed,
    )


def run_benchmark(
    system,
    queries: list[BenchQuery],
    top_k: int = DEFAULT_TOP_K,
    ks: tuple[int, ...] = (1, 2, 4, MRR_DEPTH),
    progress=None,
    hybrid: bool = False,
) -> BenchScores:
    """Run every query and score the lot."""
    results = []
    for index, query in enumerate(queries, start=1):
        results.append(run_query(system, query, top_k=top_k, hybrid=hybrid))
        if progress and index % 50 == 0:
            progress(index, len(queries))

    scores = score(results, ks=ks)
    if scores.attribution_rate is None:
        logger.warning("Search returned no passages at all — is the corpus ingested?")
    elif scores.attribution_rate < 1.0:
        logger.warning(
            "%.1f%% of retrieved passages could not be traced to an article — "
            "the scores below are bounded by that, not by retrieval",
            100 * (1 - scores.attribution_rate),
        )
    return scores

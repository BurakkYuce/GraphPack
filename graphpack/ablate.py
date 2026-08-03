"""Does the graph answer what retrieval cannot?

The project has claimed, in prose, that some questions are joins rather than
passages — "which outlets covered this topic" is not written down anywhere, it
is assembled. This measures that instead of asserting it.

The design avoids the obvious unfairness. Scoring the graph against an answer
the graph produced would be circular: it would score 1.0 by construction and
mean nothing. So the graph's answer is treated as the *definition of the
question*, and the only thing measured is how much of it a reader could recover
from the corpus alone:

    recovered = |answer entities named anywhere in the top-k passages|
                --------------------------------------------------
                            |answer entities|

A question whose answer sits in one paragraph scores near 1: retrieval finds the
paragraph and every name is in it. A question whose answer is spread across
forty documents scores near 0 however good the retriever is, because no k
passages contain forty documents' worth of names. That gap is the claim, stated
as a number.

What this does not measure: whether an end-to-end RAG system *answers* the
question, which would need a judge. Name presence is a lower bound on what a
reader could assemble — being in the retrieved text is necessary, not
sufficient.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: How deep to retrieve. Generous on purpose — the claim is not that retrieval
#: is bad at k, it is that no k recovers an answer that is a join.
DEFAULT_TOP_K = 30


@dataclass(frozen=True)
class QuestionResult:
    """One question, the graph's answer, and how much of it the corpus shows."""

    question_id: str
    question: str
    intent: str
    #: Names of the entities the traversal returned.
    answer: tuple[str, ...]
    #: Those found somewhere in the retrieved passages.
    recovered: tuple[str, ...]
    passages: int

    @property
    def recall(self) -> float:
        return len(self.recovered) / len(self.answer) if self.answer else 0.0


@dataclass
class AblationReport:
    results: list[QuestionResult] = field(default_factory=list)
    #: Questions the traversal answered with nothing — no answer to recover, so
    #: they are excluded from the mean rather than counted as a perfect score.
    unanswered: list[str] = field(default_factory=list)

    @property
    def scored(self) -> list[QuestionResult]:
        return [r for r in self.results if r.answer]

    @property
    def mean_recall(self) -> float:
        scored = self.scored
        return sum(r.recall for r in scored) / len(scored) if scored else 0.0

    @property
    def answer_sizes(self) -> list[int]:
        return [len(r.answer) for r in self.scored]


#: Every form of the letter i, collapsed to one. Turkish distinguishes dotted
#: from dotless and the two case-fold in opposite directions — `I` is `ı` under
#: Turkish rules and `i` under everybody else's — while the corpus itself mixes
#: conventions, writing "İş Kanunu" in one decision and "IS KANUNU" in the next.
#: A matcher that respects the distinction reports a name absent because of
#: orthography, which understates retrieval. It is collapsed instead.
_I_FORMS = str.maketrans({"İ": "i", "I": "i", "ı": "i"})


def _fold(text: str) -> str:
    """Normalise for presence-matching: one i, no case, no accents, one space."""
    stripped = unicodedata.normalize("NFKD", text.translate(_I_FORMS).casefold())
    return re.sub(r"\s+", " ", "".join(c for c in stripped if not unicodedata.combining(c)))


#: Below this a "name" matches everything. Two-character outlet codes and
#: single-digit article numbers would each be found in any passage by accident.
MIN_NAME_LENGTH = 4


def recovered_from(answer: list[str], passages: list[str]) -> list[str]:
    """Which of *answer* is named anywhere in *passages*.

    Substring matching, deliberately generous: the question is whether the
    information is present at all, so anything stricter would measure the
    matcher rather than the corpus.
    """
    haystack = _fold(" \n ".join(passages))
    found = []
    for name in answer:
        needle = _fold(name).strip()
        if len(needle) >= MIN_NAME_LENGTH and needle in haystack:
            found.append(name)
    return found


def run_ablation(
    session, system, pack_name: str, questions, rules, top_k=DEFAULT_TOP_K, resolver=None
):
    """Answer each question through the graph, then look for that answer in text."""
    from graphpack.agent import answer_question

    report = AblationReport()
    for question in questions:
        trace = answer_question(
            session, pack_name, question.question, rules, system=None, llm=None, resolver=resolver
        )
        names = _answer_names(session, pack_name, trace)
        if not names:
            report.unanswered.append(question.id)
            logger.info("%s: the traversal returned nothing — excluded", question.id)

        passages = _passages(system, question.question, top_k)
        report.results.append(
            QuestionResult(
                question_id=question.id,
                question=question.question,
                intent=question.intent or "",
                answer=tuple(names),
                recovered=tuple(recovered_from(names, passages)),
                passages=len(passages),
            )
        )
    return report


_NAMES = """
MATCH (n {pack: $pack}) WHERE n.id IN $ids
RETURN toString(coalesce(n.name, n.title, n.case_number, n.number, n.id)) AS name
"""


def _answer_names(session, pack_name: str, trace) -> list[str]:
    """Readable names for what the traversal returned.

    Names rather than identifiers, because an identifier is GraphPack's
    invention — `src:the-verge` appears in no news article, and scoring the
    corpus on whether it contains our slug would measure nothing.
    """
    ids = [i for i in trace.nodes_touched if i]
    if not ids:
        return []
    rows = session.run(_NAMES, pack=pack_name, ids=ids)
    return [row["name"] for row in rows if row["name"]]


def _passages(system, question: str, top_k: int) -> list[str]:
    """The retrieved text, as a reader of it would see it.

    `MetadataMode.LLM` rather than the bare body, because the metadata a pack
    declares is prepended to every chunk before anything reads it — an article's
    title and outlet are in the passage. Scoring against the body alone
    understates retrieval and flatters the graph, which is the one direction
    this measurement must not be wrong in.
    """
    from llama_index.core.schema import MetadataMode

    from graphpack.agent.tools import _run

    retriever = system.vector_index.as_retriever(similarity_top_k=top_k)
    try:
        nodes = _run(retriever.aretrieve(question))
    except Exception as exc:  # a retriever that is not set up should not end the run
        logger.warning("Retrieval failed for %r — %s", question[:50], exc)
        return []
    return [node.node.get_content(metadata_mode=MetadataMode.LLM) for node in nodes]

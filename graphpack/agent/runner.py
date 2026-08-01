"""Run a pack's question set, and compare the loop against plain retrieval.

The claim being tested is narrow and checkable: for questions whose answer is a
path through the graph, hybrid search cannot answer them, because there is no
passage to find. "What breaks if urllib3 breaks" is a two-hop traversal; no
issue thread contains that list.

So the comparison is not "which reads better". It is whether the right entities
appear at all. A question marked ``multi_hop`` is one where the traversal should
find entities that retrieval does not, and the runner reports exactly that,
per question, with the entities named.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from graphpack.agent.contract import RetrievalRules
from graphpack.agent.loop import answer_question
from graphpack.agent.tools import search
from graphpack.agent.trace import Trace

logger = logging.getLogger(__name__)


@dataclass
class Question:
    id: str
    question: str
    intent: str = ""
    #: Canonical id the question is about, or None when it names nothing real.
    entity: str | None = None
    multi_hop: bool = False
    note: str = ""


@dataclass
class Answered:
    question: Question
    trace: Trace
    #: Entities the traversal reached that hybrid retrieval did not surface.
    only_from_graph: list[str] = field(default_factory=list)
    #: Ids the answer cited that the graph could not confirm.
    unverifiable: list[str] = field(default_factory=list)

    @property
    def routed_to(self) -> str:
        for event in self.trace.events:
            if event.step == "route":
                return str(event.detail.get("intent") or "")
        return ""

    @property
    def routed_correctly(self) -> bool:
        return not self.question.intent or self.routed_to == self.question.intent

    @property
    def found_its_entity(self) -> bool:
        """Whether the run reached the entity the question is about.

        A question naming nothing real is answered correctly by finding nothing,
        so the two cases are judged the same way.
        """
        if self.question.entity is None:
            return not self.trace.cited_ids
        return self.question.entity in self.trace.cited_ids


@dataclass
class RunReport:
    pack: str
    answers: list[Answered] = field(default_factory=list)

    @property
    def routed(self) -> int:
        return sum(1 for a in self.answers if a.routed_correctly)

    @property
    def found(self) -> int:
        return sum(1 for a in self.answers if a.found_its_entity)

    @property
    def hallucinated(self) -> int:
        return sum(1 for a in self.answers if a.unverifiable)

    @property
    def multi_hop_gain(self) -> tuple[int, int]:
        """``(questions where the graph found something retrieval did not, total)``
        over the multi-hop questions — the number the whole loop is for."""
        multi = [a for a in self.answers if a.question.multi_hop]
        return sum(1 for a in multi if a.only_from_graph), len(multi)


def load_questions(path: Path) -> list[Question]:
    """Read a pack's ``questions.jsonl``."""
    questions = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: not valid JSON — {exc}") from exc
        questions.append(
            Question(
                id=str(row.get("id") or f"q{number}"),
                question=str(row["question"]),
                intent=str(row.get("intent") or ""),
                entity=row.get("entity"),
                multi_hop=bool(row.get("multi_hop")),
                note=str(row.get("note") or ""),
            )
        )
    return questions


def run_questions(
    session,
    pack: str,
    questions: list[Question],
    rules: RetrievalRules,
    system=None,
    llm=None,
    resolver=None,
) -> RunReport:
    """Answer every question and record what the traversal added."""
    report = RunReport(pack=pack)

    for question in questions:
        trace = answer_question(
            session, pack, question.question, rules, system=system, llm=llm, resolver=resolver
        )
        answered = Answered(question=question, trace=trace)

        # What plain retrieval would have surfaced, for the same question. Only
        # meaningful once a corpus has been ingested; without one the comparison
        # is skipped rather than scored as a win.
        if system is not None:
            retrieved = " ".join(p.text for p in search(system, question.question))
            reached = _reached_by_traversal(trace)
            answered.only_from_graph = [
                node for node in reached if _bare(node) and _bare(node) not in retrieved.lower()
            ]

        for event in trace.events:
            if event.step == "critique":
                answered.unverifiable = list(event.detail.get("unverifiable") or [])

        report.answers.append(answered)
        logger.info(
            "%s: routed to %s, %d entity(ies), %d unverifiable",
            question.id,
            answered.routed_to or "nothing",
            len(trace.cited_ids),
            len(answered.unverifiable),
        )

    return report


def _reached_by_traversal(trace: Trace) -> list[str]:
    """Ids the expand step produced — not the ones the question already named."""
    for event in trace.events:
        if event.step == "expand":
            return list(event.node_ids)
    return []


def _bare(node_id: str) -> str:
    """An identifier without its namespace, which is what prose would contain.

    `pypi:urllib3` is written as `urllib3` in an issue thread and `kanun:4857`
    as `4857` in a judgment; comparing the namespaced form against retrieved
    text would report every entity as graph-only and prove nothing.
    """
    return node_id.split(":", 1)[-1].strip().lower()

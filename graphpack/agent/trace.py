"""What the agent did, recorded as it happens.

This is a contract with two readers who want different things. The visualisation
replays a traversal and needs to know which nodes and edges lit up at each step.
A person reading a wrong answer needs to know which step went wrong and what it
saw. Both are served by the same record, so it is written once and deliberately.

Every step carries its node and edge identifiers even when the step did not
retrieve anything, because "the expansion found nothing" is the interesting
state to be able to see.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

#: Steps the loop can take. Named rather than free-form so a replay can style
#: them and a test can assert on the sequence.
STEPS = (
    "route",  # decide what kind of question this is
    "lookup",  # find the entities the question names
    "expand",  # walk the graph from them
    "retrieve",  # hybrid search over the corpus
    "answer",  # produce a reply from what was gathered
    "critique",  # check the reply against what was gathered
)


@dataclass
class TraceEvent:
    """One step of one run."""

    step: str
    #: Free-form, one line, shown in the replay. What this step concluded.
    summary: str = ""
    #: Graph nodes this step touched, by canonical id.
    node_ids: list[str] = field(default_factory=list)
    #: Edges as ``(start_id, type, end_id)`` — enough to draw them.
    edge_ids: list[tuple[str, str, str]] = field(default_factory=list)
    #: Which tool did the work: a Cypher template name, "hybrid", "llm".
    tool: str = ""
    duration_ms: int = 0
    #: Anything step-specific worth keeping. Not for the replay; for the person
    #: working out why an answer was wrong.
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trace:
    """The whole run."""

    question: str
    pack: str
    events: list[TraceEvent] = field(default_factory=list)
    answer: str = ""
    #: Canonical ids the answer rests on. Phase 6's hallucination check reads
    #: this: every entity named in an answer has to be one the graph holds.
    cited_ids: list[str] = field(default_factory=list)

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(asdict(self), indent=indent, ensure_ascii=False)

    @property
    def steps(self) -> list[str]:
        return [event.step for event in self.events]

    @property
    def total_ms(self) -> int:
        return sum(event.duration_ms for event in self.events)

    @property
    def nodes_touched(self) -> list[str]:
        """Every node the run saw, in the order it first saw them.

        Order matters to the replay: it is the path taken, not a set.
        """
        seen: list[str] = []
        for event in self.events:
            for node in event.node_ids:
                if node not in seen:
                    seen.append(node)
        return seen


class Recorder:
    """Collects events and times them.

    Used as a context manager per step so the duration is measured rather than
    estimated, and so a step that raises still leaves a record of having been
    tried.
    """

    def __init__(self, question: str, pack: str):
        self.trace = Trace(question=question, pack=pack)

    def step(self, name: str, tool: str = "") -> _Step:
        if name not in STEPS:
            raise ValueError(f"unknown step '{name}'; expected one of {', '.join(STEPS)}")
        return _Step(self.trace, name, tool)


class _Step:
    def __init__(self, trace: Trace, name: str, tool: str):
        self.event = TraceEvent(step=name, tool=tool)
        self._trace = trace
        self._started = 0.0

    def __enter__(self) -> TraceEvent:
        self._started = time.perf_counter()
        return self.event

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.event.duration_ms = int((time.perf_counter() - self._started) * 1000)
        if exc is not None:
            self.event.summary = self.event.summary or f"failed: {exc}"
            self.event.detail["error"] = str(exc)
        self._trace.events.append(self.event)
        return False

"""The second pass, and the line it must not cross.

Extraction finds tr-law's statutes at 98.4% precision and draws the `CITES`
edge to them at 13.1% recall. This pass asks one question per already-found
pair and doubled that to 26.2%. The tests that matter are less about the
doubling than about what it is not allowed to do to get there.
"""

from __future__ import annotations

import pytest

from graphpack.eval.contract import Task
from graphpack.verify import find_candidates, verify

TASK = Task(
    name="t",
    generator="document_edges",
    relation="CITES",
    backbone_relation="CITES",
    endpoint_label="Statute",
    source_label="Decision",
    require_relation=True,
)


@pytest.mark.unit
def test_candidates_never_read_the_backbone():
    """The line. Choosing what to ask by reading gold writes the answers in.

    The candidate query may touch `MENTIONS`, `RESOLVED_AS` and the extracted
    relation. It must not touch the backbone edge the evaluator scores against
    — doing so would make the benchmark a tautology, and it would still look
    like an improvement.
    """
    from graphpack.verify import _CANDIDATES

    query = _CANDIDATES.format(
        entity="__Entity__", source_label="Decision", endpoint_label="Statute", relation="CITES"
    )
    # The backbone's own edge between Decision and Statute is also called CITES,
    # so the check is structural: nothing may match a *backbone* node to a
    # backbone node. Every relation traversal here starts from an extracted
    # entity.
    assert query.count("MENTIONS") == 2
    assert "(ca)-[" not in query and "(cb)-[" not in query
    assert "backbone" not in query.lower()


@pytest.mark.unit
def test_a_confirmed_pair_is_written_and_a_rejected_one_is_not():
    session = _FakeSession(
        [
            {
                "start": "e1",
                "end": "e2",
                "source": "d:1",
                "target": "k:1",
                "target_name": "Kanun 1",
                "text": "The court applied Kanun 1.",
            },
            {
                "start": "e3",
                "end": "e4",
                "source": "d:2",
                "target": "k:2",
                "target_name": "Kanun 2",
                "text": "Unrelated passage.",
            },
        ]
    )
    llm = _FakeLLM(["YES", "NO"])

    report = verify(session, llm, "p", TASK)

    assert report.asked == 2
    assert report.confirmed == 1
    assert report.written == 1
    assert session.writes == [("e1", "e2")]


@pytest.mark.unit
def test_a_dry_run_asks_and_writes_nothing():
    """How a pass is checked before it touches a graph that took hours."""
    session = _FakeSession(
        [
            {
                "start": "e1",
                "end": "e2",
                "source": "d:1",
                "target": "k:1",
                "target_name": "K",
                "text": "cites K",
            }
        ]
    )

    report = verify(session, _FakeLLM(["YES"]), "p", TASK, dry_run=True)

    assert report.confirmed == 1
    assert report.written == 0
    assert session.writes == []


@pytest.mark.unit
def test_an_unreadable_answer_is_counted_apart_from_a_rejection():
    """A prompt that stops being followed must show as a number.

    Reading "I think perhaps..." as NO would report the same lower recall as a
    model that correctly rejected everything, and nothing would say which.
    """
    session = _FakeSession(
        [
            {
                "start": "e1",
                "end": "e2",
                "source": "d:1",
                "target": "k:1",
                "target_name": "K",
                "text": "some text",
            }
        ]
    )

    report = verify(session, _FakeLLM(["It depends on the context"]), "p", TASK)

    assert report.confirmed == 0
    assert report.unparsed == 1


@pytest.mark.unit
def test_one_failed_call_does_not_end_the_pass():
    session = _FakeSession(
        [
            {
                "start": "e1",
                "end": "e2",
                "source": "d:1",
                "target": "k:1",
                "target_name": "K",
                "text": "a",
            },
            {
                "start": "e3",
                "end": "e4",
                "source": "d:2",
                "target": "k:2",
                "target_name": "K",
                "text": "b",
            },
        ]
    )

    report = verify(session, _FakeLLM([RuntimeError("timeout"), "YES"]), "p", TASK)

    assert report.failed == 1
    assert report.confirmed == 1


@pytest.mark.unit
def test_a_pair_with_no_text_is_skipped_rather_than_asked_about():
    """Asking a model to read an empty passage produces an answer, not a fact."""
    session = _FakeSession(
        [
            {
                "start": "e1",
                "end": "e2",
                "source": "d:1",
                "target": "k:1",
                "target_name": "K",
                "text": "   ",
            }
        ]
    )

    report = verify(session, _FakeLLM(["YES"]), "p", TASK)

    assert report.candidates == 1
    assert report.asked == 0


@pytest.mark.unit
def test_limit_takes_a_stable_slice():
    rows = [
        {
            "start": f"e{i}",
            "end": "e0",
            "source": f"d:{i}",
            "target": "k:1",
            "target_name": "K",
            "text": "t",
        }
        for i in range(5)
    ]
    session = _FakeSession(rows)

    first = find_candidates(session, "p", TASK, limit=2)
    second = find_candidates(session, "p", TASK, limit=2)

    assert first == second == rows[:2]


class _FakeLLM:
    def __init__(self, answers):
        self._answers = list(answers)

    def complete(self, _prompt):
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class _FakeSession:
    def __init__(self, candidates):
        self._candidates = candidates
        self.writes: list[tuple[str, str]] = []

    def run(self, query, **kwargs):
        if "MERGE" in query:
            self.writes.append((kwargs["start"], kwargs["end"]))
            return _Result([{"written": 1}])
        return _Result(list(self._candidates))


class _Result(list):
    def single(self):
        return self[0] if self else None

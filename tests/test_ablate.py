"""How much of a graph answer the corpus shows.

The arithmetic is small; what needs pinning is the fairness. Every way this
measurement can be wrong flatters the graph, so the tests are mostly about the
directions it must not lean.
"""

from __future__ import annotations

import pytest

from graphpack.ablate import AblationReport, QuestionResult, recovered_from

pytestmark = pytest.mark.unit


def result(answer, recovered, qid="q"):
    return QuestionResult(
        question_id=qid,
        question="?",
        intent="i",
        answer=tuple(answer),
        recovered=tuple(recovered),
        passages=30,
    )


def test_a_name_present_in_any_passage_counts():
    found = recovered_from(["The Verge", "Polygon"], ["... reported by The Verge today ..."])

    assert found == ["The Verge"]


def test_matching_ignores_case_and_turkish_dotted_capitals():
    """A decision writes a statute's name with the corpus's letters and a
    question types whichever the keyboard gave. Matching raw strings would score
    the retriever down for orthography."""
    found = recovered_from(["İş Kanunu"], ["... 4857 sayılı IS KANUNU uyarınca ..."])

    assert found == ["İş Kanunu"]


def test_short_names_are_refused_rather_than_matched_everywhere():
    """A two-character code or a single-digit article number is a substring of
    almost any passage. Counting those would show recovery that is not there."""
    assert recovered_from(["25", "TC"], ["a passage mentioning 25 and TC"]) == []


def test_a_question_the_graph_could_not_answer_is_excluded_not_scored_perfect():
    """An empty answer set divides by zero on one reading and scores 100% on
    another. Both are wrong: there was nothing to recover."""
    report = AblationReport(results=[result([], []), result(["a name here"], ["a name here"])])

    assert [r.question_id for r in report.scored] == ["q"]
    assert report.mean_recall == 1.0


def test_recall_is_the_share_of_the_answer_that_was_found():
    assert result(["aaaa", "bbbb", "cccc", "dddd"], ["aaaa"]).recall == 0.25


def test_the_mean_is_over_questions_not_over_entities():
    """Otherwise one question with a fifty-entity answer decides the figure, and
    the number stops being about questions."""
    report = AblationReport(results=[result(["aaaa"], ["aaaa"]), result(["b" * 4] * 50, [])])

    assert report.mean_recall == 0.5


def test_passages_are_read_the_way_a_reader_sees_them():
    """The metadata a pack declares is prepended to every chunk before anything
    reads it. Scoring the bare body understated retrieval by more than a factor
    of two — 11.4% against 26.8% — in the one direction this must not be wrong.
    """
    import inspect

    from graphpack import ablate

    assert "MetadataMode.LLM" in inspect.getsource(ablate._passages)

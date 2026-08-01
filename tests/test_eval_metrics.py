"""Scoring arithmetic and the interval around it.

Small sets are the whole situation here — a few hundred edges — so the interval
is not decoration. A test that only checks precision and recall would let a
confidently-stated 60% pass for a measurement.
"""

from __future__ import annotations

import pytest

from graphpack.eval.metrics import score

pytestmark = pytest.mark.unit


def test_counts_follow_from_the_two_sets():
    scores = score(predicted={"a", "b", "c"}, gold={"b", "c", "d"})

    assert (scores.true_positive, scores.false_positive, scores.false_negative) == (2, 1, 1)
    assert scores.precision == pytest.approx(2 / 3)
    assert scores.recall == pytest.approx(2 / 3)
    assert scores.f1 == pytest.approx(2 / 3)


def test_a_repeated_claim_is_one_claim():
    """A relation extracted twice from one document is a single assertion.
    Counting it twice would let a repetitive model outscore a careful one."""
    scores = score(predicted=["a", "a", "a"], gold=["a"])

    assert (scores.true_positive, scores.false_positive) == (1, 0)


def test_predicting_nothing_scores_zero_rather_than_dividing_by_zero():
    scores = score(predicted=[], gold={"a", "b"})

    assert (scores.precision, scores.recall, scores.f1) == (0.0, 0.0, 0.0)
    assert scores.false_negative == 2


def test_with_no_gold_everything_predicted_is_wrong():
    """There is no recall to measure, and every claim is unsupported. A run that
    reports this is telling you the gold generator found nothing, not that the
    model did well."""
    scores = score(predicted={"a"}, gold=[])

    assert (scores.true_positive, scores.false_positive) == (0, 1)
    assert scores.precision == 0.0
    assert scores.recall == 0.0


def test_perfect_agreement_still_carries_an_interval():
    """The normal approximation gives a zero-width interval at p=1, which reads
    as certainty from four observations. Wilson does not."""
    scores = score(predicted={"a", "b", "c", "d"}, gold={"a", "b", "c", "d"})

    low, high = scores.precision_interval
    assert scores.precision == 1.0
    assert low < 1.0
    assert high == 1.0


def test_a_small_sample_gets_a_wide_interval():
    """The reason the interval is printed: at n=10 a difference of ten points
    is not a difference."""
    small = score(predicted=set("abcdefghij"), gold=set("abcde") | set("klmno"))
    low, high = small.precision_interval

    assert small.precision == 0.5
    assert high - low > 0.5


def test_a_larger_sample_narrows_it():
    predicted = {f"p{i}" for i in range(1000)}
    gold = {f"p{i}" for i in range(500)} | {f"g{i}" for i in range(500)}

    large = score(predicted=predicted, gold=gold)
    low, high = large.precision_interval

    assert large.precision == 0.5
    assert high - low < 0.07


def test_intervals_stay_inside_zero_and_one():
    """Where the textbook approximation misbehaves: near the ends, at small n,
    it produces bounds outside the range a proportion can take."""
    for tp, total in ((0, 5), (5, 5), (1, 3)):
        scores = score(predicted=set(range(total)), gold=set(range(tp)))
        low, high = scores.precision_interval
        assert 0.0 <= low <= high <= 1.0


def test_examples_are_kept_for_the_report():
    scores = score(predicted={"a", "b"}, gold={"b", "c"})

    assert scores.examples["false_positive"] == ["a"]
    assert scores.examples["false_negative"] == ["c"]
    assert scores.examples["true_positive"] == ["b"]


def test_the_report_line_shows_the_interval_beside_the_score():
    line = score(predicted={"a", "b"}, gold={"a"}).line("micro")

    assert "P " in line and "R " in line and "F1" in line
    assert "[" in line and "]" in line

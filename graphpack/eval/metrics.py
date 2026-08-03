"""Precision, recall, F1 — and how much to trust them.

Micro-averaged: every gold edge counts once, regardless of which document it
came from. Macro-averaging over documents would give a thread mentioning two
packages the same weight as one mentioning twenty, which is not the quantity
anybody wants to know about.

A score without an interval is a number pretending to be a measurement. These
sets are small — a few hundred edges — and at that size a difference of ten
points can be noise. The interval is reported next to the score for that reason.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

#: 1.96 standard errors — the conventional 95% two-sided normal interval.
_Z = 1.96


@dataclass(frozen=True)
class Scores:
    """One evaluation's counts and what follows from them."""

    true_positive: int
    false_positive: int
    false_negative: int
    #: Kept for the report rather than the arithmetic: examples say more about
    #: what to fix than the totals do.
    examples: dict[str, list] = field(default_factory=dict)

    @property
    def predicted(self) -> int:
        return self.true_positive + self.false_positive

    @property
    def gold(self) -> int:
        return self.true_positive + self.false_negative

    @property
    def precision(self) -> float:
        return self.true_positive / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return self.true_positive / self.gold if self.gold else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def precision_ceiling(self) -> float | None:
        """The best precision this gold set allows, when that is below 1.0.

        A gold set narrower than what extraction can claim puts a hard cap on
        precision, and the bare number then reads as an error rate it is not.
        The oss `thread_package` task is the case that prompted this: its gold
        holds one package per thread — the one the thread's own repository
        publishes — while extraction resolves every package the thread
        discusses. 135 gold pairs against 218 claimed caps precision at 61.9%,
        and the 38% excess is mostly correct readings this task cannot credit.

        `None` when the cap does not bind, so a report only mentions it where it
        changes how the number should be read.
        """
        if not self.predicted or self.gold >= self.predicted:
            return None
        return self.gold / self.predicted

    @property
    def precision_interval(self) -> tuple[float, float]:
        return _wilson(self.true_positive, self.predicted)

    @property
    def recall_interval(self) -> tuple[float, float]:
        return _wilson(self.true_positive, self.gold)

    def line(self, label: str) -> str:
        lo, hi = self.precision_interval
        rlo, rhi = self.recall_interval
        return (
            f"{label:<22} P {self.precision:5.1%} [{lo:.1%}-{hi:.1%}]   "
            f"R {self.recall:5.1%} [{rlo:.1%}-{rhi:.1%}]   "
            f"F1 {self.f1:5.1%}   "
            f"(tp {self.true_positive}, fp {self.false_positive}, fn {self.false_negative})"
        )


def score[T](
    predicted: Iterable[T],
    gold: Iterable[T],
    example_limit: int = 15,
) -> Scores:
    """Compare two sets of facts.

    Both sides are sets: a relation extracted twice from one document is one
    claim, not two, and counting it twice would let a repetitive model score
    better than a careful one.
    """
    predicted_set, gold_set = set(predicted), set(gold)
    hits = predicted_set & gold_set
    spurious = predicted_set - gold_set
    missed = gold_set - predicted_set

    return Scores(
        true_positive=len(hits),
        false_positive=len(spurious),
        false_negative=len(missed),
        examples={
            "false_positive": sorted(spurious)[:example_limit],
            "false_negative": sorted(missed)[:example_limit],
            "true_positive": sorted(hits)[:example_limit],
        },
    )


def _wilson(successes: int, trials: int) -> tuple[float, float]:
    """Wilson score interval for a proportion.

    Not the textbook normal approximation, which misbehaves exactly where these
    numbers live: near 0 or 1, and at small n, it produces bounds outside [0, 1]
    and intervals of width zero when a score is perfect. Wilson stays sane at
    both ends.
    """
    if trials == 0:
        return (0.0, 0.0)
    p = successes / trials
    denominator = 1 + _Z**2 / trials
    centre = (p + _Z**2 / (2 * trials)) / denominator
    spread = _Z * math.sqrt(p * (1 - p) / trials + _Z**2 / (4 * trials**2)) / denominator
    return (max(0.0, centre - spread), min(1.0, centre + spread))

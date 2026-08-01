"""Measuring the corpus half against somebody else's ground truth.

`eval` scores what extraction found, using gold a pack derived from its own
structured data. This scores what retrieval returned, using gold a published
benchmark shipped — which is the only way to say whether a number is good.
"""

from graphpack.bench.metrics import BenchScores, QueryResult, score
from graphpack.bench.runner import BenchError, BenchQuery, load_gold, run_benchmark

__all__ = [
    "BenchError",
    "BenchQuery",
    "BenchScores",
    "QueryResult",
    "load_gold",
    "run_benchmark",
    "score",
]

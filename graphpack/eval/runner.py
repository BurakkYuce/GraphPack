"""Run a pack's evaluation and say where the errors come from.

A single F1 says how well the system did. It does not say which of the three
things it is made of was responsible, and those need fixing differently:

* **extraction** did not claim the relation at all;
* **resolution** could not turn one of the mentions into an identifier;
* **the model** claimed something the backbone does not hold.

The third is the interesting one. Some of those are wrong and some are relations
the index simply does not record — a discussion thread saying "we vendored X"
states a real dependency that no metadata field contains. Counting them as
precision errors without saying so would understate the system and overstate the
ground truth.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from graphpack.eval.contract import EvalRules, Task
from graphpack.eval.generators import GENERATORS
from graphpack.eval.metrics import Scores, score
from graphpack.inspect import ENTITY_LABEL

logger = logging.getLogger(__name__)


@dataclass
class TaskResult:
    task: Task
    scores: Scores
    diagnostics: dict = field(default_factory=dict)
    #: Why each missed gold edge was missed, counted by cause.
    misses: dict[str, int] = field(default_factory=dict)
    miss_examples: dict[str, list] = field(default_factory=dict)


@dataclass
class EvalReport:
    pack: str
    results: list[TaskResult] = field(default_factory=list)
    documents_scored: int = 0
    documents_held_out: int = 0


def run_eval(session, pack: str, rules: EvalRules, example_limit: int = 10) -> EvalReport:
    """Score every task the pack declares."""
    report = EvalReport(pack=pack)

    for task in rules.tasks:
        generator = GENERATORS.get(task.generator)
        if generator is None:
            logger.warning("Generator '%s' is not implemented yet — skipping", task.generator)
            continue

        predicted, gold, diagnostics = generator(session, pack, task)
        predicted, gold, held_out = _split(predicted, gold, rules)
        scores = score(predicted, gold, example_limit=example_limit)

        misses, examples = _diagnose_misses(
            session, pack, task, scores.examples.get("false_negative", []), example_limit
        )
        report.results.append(
            TaskResult(
                task=task,
                scores=scores,
                diagnostics=diagnostics,
                misses=misses,
                miss_examples=examples,
            )
        )
        report.documents_scored = diagnostics.get("documents_with_resolved_entities", 0)
        report.documents_held_out = held_out

    return report


def _split(predicted: set, gold: set, rules: EvalRules) -> tuple[set, set, int]:
    """Withhold a share of the gold pairs from scoring.

    Held out by first endpoint rather than by individual pair: rules are written
    about entities — an alias row, a normaliser — so putting `urllib3 -> certifi`
    in the fitted half and `urllib3 -> idna` in the held-out one would leak the
    very thing the split is meant to isolate.

    With ``holdout: 0`` nothing is withheld, which is the honest setting while no
    rule has been tuned against this corpus. The moment the alias table grows
    from reading these errors, it stops being honest.
    """
    if rules.holdout <= 0:
        return predicted, gold, 0

    subjects = sorted({a for a, _ in gold} | {a for a, _ in predicted})
    count = int(len(subjects) * rules.holdout)
    if count == 0:
        return predicted, gold, 0

    chosen = set(random.Random(rules.seed).sample(subjects, count))
    logger.info("Scoring on %d held-out subject(s) of %d", count, len(subjects))
    return (
        {pair for pair in predicted if pair[0] in chosen},
        {pair for pair in gold if pair[0] in chosen},
        count,
    )


_ANY_RELATION_BETWEEN = (
    f"MATCH (a:{ENTITY_LABEL} {{pack: $pack}})-[r]->(b:{ENTITY_LABEL} {{pack: $pack}}) "
    "MATCH (a)-[:RESOLVED_AS]->({pack: $pack, id: $start}) "
    "MATCH (b)-[:RESOLVED_AS]->({pack: $pack, id: $end}) "
    "RETURN collect(DISTINCT type(r)) AS types"
)

_SHARED_DOCUMENTS = (
    f"MATCH ({{pack: $pack, id: $start}})<-[:RESOLVED_AS]-(:{ENTITY_LABEL})<-[:MENTIONS]-(chunk) "
    f"MATCH ({{pack: $pack, id: $end}})<-[:RESOLVED_AS]-(:{ENTITY_LABEL})<-[:MENTIONS]-(chunk) "
    "RETURN collect(DISTINCT chunk.ref_doc_id)[..3] AS documents"
)


def _diagnose_misses(
    session, pack: str, task: Task, missed: list, limit: int
) -> tuple[dict[str, int], dict[str, list]]:
    """Sort missed gold pairs by which stage lost them.

    Every gold pair has both endpoints resolved by construction — that is how it
    became gold — so the question is what extraction did between them: claimed
    nothing, or claimed something of another type. The second is a schema
    problem and the first is a reading problem, and they are worked on
    differently.

    Only the sampled misses are diagnosed. The purpose is to know the shape of
    the problem, not to label every failure.
    """
    causes: dict[str, int] = {}
    examples: dict[str, list] = {}

    for start, end in missed[:limit]:
        types = (
            session.run(_ANY_RELATION_BETWEEN, pack=pack, start=start, end=end).single()["types"]
            or []
        )
        documents = (
            session.run(_SHARED_DOCUMENTS, pack=pack, start=start, end=end).single()["documents"]
            or []
        )
        where = f" (seen together in {', '.join(d for d in documents if d)})" if documents else ""

        if types:
            cause = "related, but as another type"
            detail = f"{start} -> {end}: extracted as {', '.join(types)}{where}"
        else:
            cause = "no relation extracted"
            detail = f"{start} -> {end}: both ends resolved, nothing between them{where}"

        causes[cause] = causes.get(cause, 0) + 1
        examples.setdefault(cause, []).append(detail)

    return causes, examples

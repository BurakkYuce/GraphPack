"""Ask the model about pairs it already found, one at a time.

Extraction reads a chunk once and must emit every entity *and* every relation in
one pass. Measured on tr-law, it does the first well and the second badly: 98.4%
precision finding the statutes a decision names, and 13.1% recall drawing the
`CITES` edge to them. The entities are in the graph; the edges are not.

This is a second pass over exactly that gap. For each pair the extractor already
co-mentioned in one document and did *not* relate, it asks one question about one
pair with the text in front of it — a far easier task than open extraction, and
one a small local model can do.

**The honesty constraint, which shapes everything here.** The candidate pairs
come from `MENTIONS`, never from the backbone. Reading the backbone to decide
what to ask about would be writing the gold into the graph and then scoring
against it, which would turn a benchmark into a tautology. The only inputs are
what extraction found and what the document says.

Two consequences follow and both are deliberate. Pairs the extractor never
co-mentioned stay missing — this pass cannot fix entity recall, only relation
recall. And a confirmation can be wrong, so the edges it writes carry
``verified: true`` and are removable in one query, which is what makes the run
repeatable rather than a one-way door.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from graphpack.inspect import ENTITY_LABEL

logger = logging.getLogger(__name__)

#: Pairs a document co-mentions where the relation is absent. Ordered so a
#: `--limit` takes a stable slice rather than whatever the planner returned.
#:
#: `RESOLVED_AS` on both ends for the same reason the evaluator uses it: a claim
#: about something nobody can identify cannot be checked or scored.
_CANDIDATES = """
MATCH (chunk)-[:MENTIONS]->(a:{entity} {{pack: $pack}})
MATCH (chunk)-[:MENTIONS]->(b:{entity} {{pack: $pack}})
MATCH (a)-[:RESOLVED_AS]->(ca:{source_label} {{pack: $pack}})
MATCH (b)-[:RESOLVED_AS]->(cb:{endpoint_label} {{pack: $pack}})
WHERE a <> b AND NOT (a)-[:{relation}]->(b)
RETURN DISTINCT a.id AS start, b.id AS end, ca.id AS source, cb.id AS target,
       coalesce(cb.name, cb.title, cb.id) AS target_name,
       chunk.text AS text
ORDER BY source, target
"""

_WRITE = """
MATCH (a:{entity} {{pack: $pack}}) WHERE a.id = $start
MATCH (b:{entity} {{pack: $pack}}) WHERE b.id = $end
MERGE (a)-[r:{relation}]->(b)
SET r.verified = true
RETURN count(r) AS written
"""

#: Every edge this pass has ever written, for a pack. The undo.
_FORGET = """
MATCH (:{entity} {{pack: $pack}})-[r {{verified: true}}]->(:{entity} {{pack: $pack}})
DELETE r
RETURN count(r) AS removed
"""

_PROMPT = """Read the passage and answer one question about it.

Passage:
\"\"\"{text}\"\"\"

Question: does this passage state that it {verb} "{target}"?

Answer with one word, YES or NO. Answer YES only if the passage says so
directly. If the passage merely happens to mention "{target}" without stating
that relationship, answer NO."""


@dataclass
class VerifyReport:
    candidates: int = 0
    asked: int = 0
    confirmed: int = 0
    written: int = 0
    unparsed: int = 0
    failed: int = 0
    examples: list[str] = field(default_factory=list)


def forget_verified(session, pack: str) -> int:
    """Remove every edge a previous verification pass wrote."""
    row = session.run(_FORGET.format(entity=ENTITY_LABEL), pack=pack).single()
    return int(row["removed"]) if row else 0


def find_candidates(session, pack: str, task, limit: int | None = None) -> list[dict]:
    """Co-mentioned, resolved, unrelated pairs — the gap this pass works on."""
    query = _CANDIDATES.format(
        entity=ENTITY_LABEL,
        source_label=task.source_label or task.endpoint_label,
        endpoint_label=task.endpoint_label,
        relation=task.relation,
    )
    rows = [dict(row) for row in session.run(query, pack=pack)]
    return rows[:limit] if limit else rows


def _verb(relation: str) -> str:
    """`CITES` -> "cites". Good enough, and the pack can say better itself."""
    return relation.replace("_", " ").lower()


def verify(
    session,
    llm,
    pack: str,
    task,
    limit: int | None = None,
    progress=None,
    dry_run: bool = False,
) -> VerifyReport:
    """Confirm or reject each candidate, and write the confirmed ones.

    ``dry_run`` asks and counts without writing, which is how a run is checked
    on a slice before it touches a graph that took hours to build.

    A model that answers neither YES nor NO is counted apart rather than read as
    a rejection: a prompt that stops being followed should show up as a number,
    not as a quietly lower recall.
    """
    candidates = find_candidates(session, pack, task, limit=limit)
    report = VerifyReport(candidates=len(candidates))
    verb = _verb(task.relation)

    for index, row in enumerate(candidates, start=1):
        if progress and index % 25 == 0:
            progress(index, len(candidates))

        text = (row.get("text") or "").strip()
        if not text:
            continue

        prompt = _PROMPT.format(text=text[:4000], verb=verb, target=row["target_name"])
        try:
            answer = str(llm.complete(prompt)).strip().upper()
        except Exception as exc:  # noqa: BLE001 — one pair is not the whole pass
            logger.warning(
                "verification failed for %s -> %s: %s", row["source"], row["target"], exc
            )
            report.failed += 1
            continue

        report.asked += 1
        if answer.startswith("YES"):
            report.confirmed += 1
            if len(report.examples) < 5:
                report.examples.append(f"{row['source']} -> {row['target']}")
            if not dry_run:
                written = session.run(
                    _WRITE.format(entity=ENTITY_LABEL, relation=task.relation),
                    pack=pack,
                    start=row["start"],
                    end=row["end"],
                ).single()
                report.written += int(written["written"]) if written else 0
        elif not answer.startswith("NO"):
            report.unparsed += 1

    return report


_COUNT = """
MATCH (:{entity} {{pack: $pack}})-[r {{verified: true}}]->()
RETURN count(r) AS n
"""


def count_verified(session, pack: str) -> int:
    """How many edges in this pack came from a verification pass.

    Read on every ``eval`` run. A score that silently includes relations the
    extractor did not produce describes a different pipeline than the one it
    appears to be about, and nothing else in the graph distinguishes them.
    """
    row = session.run(_COUNT.format(entity=ENTITY_LABEL), pack=pack).single()
    return int(row["n"]) if row else 0

"""What the agent can do: look things up, walk the graph, read the corpus.

Three tools, and the split between them is the whole argument for the agent.
Hybrid search finds text that resembles the question. The graph answers
questions about structure that no amount of resembling reaches — "what breaks if
this breaks" is two hops of an edge type, and a passage saying so may not exist
anywhere in the corpus.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from graphpack.agent.contract import render_cypher

# Re-exported: several modules import `_run` from here. The loop itself lives in
# `graphpack.loop`, because it belongs to the process rather than to the agent —
# ingest needs the same one now that a single command can ingest and then query.
from graphpack.loop import run as _run

logger = logging.getLogger(__name__)

#: Fallback for a pack that declares no lookup query. Matches a canonical
#: identifier outright, or a name case-insensitively.
DEFAULT_LOOKUP = """
MATCH (n {pack: $pack})
WHERE n.id = $needle OR toLower(toString(coalesce(n.name, ''))) = toLower($needle)
RETURN n.id AS id, coalesce(n.name, n.id) AS name, labels(n)[0] AS label
LIMIT $limit
"""


@dataclass
class Found:
    """One graph node an agent step reached."""

    id: str
    name: str = ""
    label: str = ""
    #: How it was reached, for the trace: the intent name, or "lookup".
    via: str = ""


@dataclass
class Passage:
    """One retrieved chunk of corpus text."""

    text: str
    score: float = 0.0
    document: str = ""


@dataclass
class Gathered:
    """Everything one run collected, before an answer is written."""

    entities: list[Found] = field(default_factory=list)
    neighbours: list[Found] = field(default_factory=list)
    passages: list[Passage] = field(default_factory=list)
    edges: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def all_ids(self) -> list[str]:
        seen: list[str] = []
        for found in [*self.entities, *self.neighbours]:
            if found.id not in seen:
                seen.append(found.id)
        return seen


def lookup(session, pack: str, needle: str, rules, limit: int = 10) -> list[Found]:
    """Find the graph nodes a question names, by Cypher."""
    cypher = rules.lookup or DEFAULT_LOOKUP
    rows = session.run(cypher, pack=pack, needle=needle, limit=limit)
    return [
        Found(
            id=row["id"],
            name=row.get("name") or row["id"],
            label=row.get("label") or "",
            via="lookup",
        )
        for row in (dict(r) for r in rows)
    ]


def resolve_mention(needle: str, resolver: ResolverIndex | None) -> list[Found]:
    """Find the graph nodes a question names, by the pack's resolution rules.

    A question says "Sendikalar Kanunu" where the graph says `kanun:6356`, and
    turning one into the other is precisely what resolution does — the alias
    table already holds the abbreviations, the normalisers already strip the
    Turkish case suffixes. Asking the lookup query to learn all of that again
    would be a second, worse copy of a thing the pack already declares.
    """
    if resolver is None:
        return []
    found = []
    for rule in resolver.rules.rules:
        match = _first_match(needle, rule, resolver)
        if match is not None:
            found.append(
                Found(id=match.canonical_id, name=needle, label=rule.entity, via=match.method)
            )
    return found


def _first_match(needle: str, rule, resolver: ResolverIndex):
    from graphpack.resolve.methods import METHODS

    for name in rule.methods:
        match = METHODS[name](needle, rule, resolver.rules.pipelines, resolver.index)
        if match is not None:
            return match
    return None


class ResolverIndex:
    """The pack's resolution rules, with the backbone read in.

    Built once per run rather than per question: the backbone is small and the
    lookup step asks about several candidate strings per question.
    """

    def __init__(self, session, pack: str, rules):
        from graphpack.resolve.methods import BackboneIndex

        self.rules = rules
        self.index = BackboneIndex(session, pack, rules.rules, rules.pipelines)
        self.index.aliases = rules.aliases


def traverse(session, pack: str, intent, entity_id: str) -> tuple[list[Found], list[tuple]]:
    """Run an intent's Cypher from one entity.

    ``$pack`` and ``$entity_id`` are bound as parameters; only the hop bound and
    the row limit are interpolated, because Cypher will not take those any other
    way.
    """
    cypher = render_cypher(intent.cypher, hops=intent.hops, limit=intent.limit)
    rows = [dict(r) for r in session.run(cypher, pack=pack, entity_id=entity_id)]

    found = [
        Found(
            id=row["id"],
            name=row.get("name") or row["id"],
            label=row.get("label") or "",
            via=intent.name,
        )
        for row in rows
        if row.get("id")
    ]
    # An intent may describe the edge it walked; when it does, the replay can
    # draw it rather than guessing a straight line to each result.
    edges = [(entity_id, row.get("via") or intent.name, row["id"]) for row in rows if row.get("id")]
    return found, edges


def search(system, question: str, top_k: int = 6) -> list[Passage]:
    """Hybrid search over the corpus, through the engine.

    Runs in whatever process holds the system: the engine's BM25 leg is an
    in-memory docstore, so a separate process would search vectors only and
    quietly return a worse answer.
    """
    try:
        results = _run(system.search(question, top_k=top_k))
    except Exception as exc:  # a retriever that is not set up should not end the run
        logger.warning("Hybrid search failed, continuing without passages — %s", exc)
        return []

    passages = []
    for result in results or []:
        if isinstance(result, dict):
            text = result.get("text") or result.get("content") or ""
            passages.append(
                Passage(
                    text=text,
                    score=float(result.get("score") or 0.0),
                    document=str(result.get("doc_id") or result.get("source") or ""),
                )
            )
    return passages


def verify(session, pack: str, ids: list[str]) -> tuple[list[str], list[str]]:
    """Split ids into those the graph holds and those it does not.

    The hallucination check. An answer naming an entity the graph has never seen
    is wrong in a way no amount of fluency excuses, and it is cheap to detect
    because every id the agent was given came from a query.
    """
    if not ids:
        return [], []
    rows = session.run(
        "MATCH (n {pack: $pack}) WHERE n.id IN $ids RETURN collect(DISTINCT n.id) AS present",
        pack=pack,
        ids=ids,
    ).single()
    present = set(rows["present"] or []) if rows else set()
    return [i for i in ids if i in present], [i for i in ids if i not in present]

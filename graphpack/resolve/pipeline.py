"""The resolution pass.

Reads the entities extraction wrote, resolves each against the backbone, and
records the answer as an edge:

    (:__Entity__)-[:RESOLVED_AS {method, score}]->(:Package)

The raw layer is left exactly as extraction produced it. That separation is what
makes re-resolution cheap: growing an alias table means deleting the edges and
running again — seconds — where re-extracting is hours. It also keeps the record
honest, since the mention and what it was taken to mean stay distinguishable.

The plan called for a separate ``(:Mention)`` node. The entities the engine
already writes carry the mention text, the source document and the pack, so a
second node would duplicate all three and have to be kept in step with it.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from graphpack.inspect import ENTITY_LABEL
from graphpack.resolve.contract import ResolveRules, Rule
from graphpack.resolve.methods import METHODS, BackboneIndex, Match

logger = logging.getLogger(__name__)

BATCH_SIZE = 5_000

#: Marks an identifier resolution could not find. Kept, rather than dropped,
#: when the pack asks for it: a mention nobody can place is still evidence, and
#: the set of them is where the next round of alias entries comes from.
PROVISIONAL_LABEL = "Provisional"

_CLEAR = f"""
MATCH (:{ENTITY_LABEL} {{pack: $pack}})-[r:RESOLVED_AS]->()
CALL (r) {{ DELETE r }} IN TRANSACTIONS OF 10000 ROWS
"""

_DROP_PROVISIONAL = f"""
MATCH (n:{PROVISIONAL_LABEL} {{pack: $pack}})
CALL (n) {{ DETACH DELETE n }} IN TRANSACTIONS OF 10000 ROWS
"""

_MENTIONS = f"""
MATCH (e:{ENTITY_LABEL} {{pack: $pack}})
WHERE any(l IN labels(e) WHERE l IN $entities)
RETURN elementId(e) AS eid,
       coalesce(e.name, e.id) AS text,
       [l IN labels(e) WHERE l IN $entities] AS entities
"""

_LINK = """
UNWIND $rows AS row
MATCH (e) WHERE elementId(e) = row.eid
MATCH (c {pack: $pack, id: row.canonical_id})
MERGE (e)-[r:RESOLVED_AS]->(c)
SET r.method = row.method, r.score = row.score
RETURN count(r) AS linked
"""

_LINK_PROVISIONAL = f"""
UNWIND $rows AS row
MATCH (e) WHERE elementId(e) = row.eid
MERGE (p:{PROVISIONAL_LABEL} {{pack: $pack, id: row.canonical_id}})
  ON CREATE SET p.text = row.text, p.entity = row.entity
MERGE (e)-[r:RESOLVED_AS]->(p)
SET r.method = 'provisional', r.score = 0
RETURN count(r) AS linked
"""


@dataclass
class ResolutionReport:
    """How a pass went, per entity type and per method.

    The method breakdown is the substance. A pack resolving 95% of mentions by
    exact match and one resolving 95% by fuzzy match have the same headline
    number and completely different trustworthiness.
    """

    pack: str
    mentions: Counter = field(default_factory=Counter)
    methods: Counter = field(default_factory=Counter)
    by_entity: dict[str, Counter] = field(default_factory=dict)
    unresolved_samples: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.mentions.values())

    @property
    def resolved(self) -> int:
        return sum(n for method, n in self.methods.items() if method not in ("drop",))

    @property
    def accounted_for(self) -> float:
        """Share of mentions that reached a deliberate outcome.

        A dropped mention counts: deciding a mention has no canonical form is an
        answer. What would not count is a mention no rule ever looked at, and
        those are excluded from the pass rather than silently counted as drops.
        """
        return self.resolved / self.total if self.total else 0.0

    def lines(self) -> list[str]:
        out = []
        for method, count in self.methods.most_common():
            share = count / self.total if self.total else 0
            out.append(f"{count:>8,}  {method:<12} {share:6.1%}")
        return out


def resolve_pack(
    session,
    pack_name: str,
    rules: ResolveRules,
    sample_unresolved: int = 25,
) -> ResolutionReport:
    """Resolve every mention in *pack_name* against its backbone.

    Idempotent: existing ``RESOLVED_AS`` edges are cleared first, so a second run
    over unchanged rules reproduces the same graph and a run after changing them
    reflects only the new rules.
    """
    report = ResolutionReport(pack=pack_name)

    cleared = _clear_previous(session, pack_name)
    if cleared:
        logger.info("Cleared %d edge(s) from a previous pass", cleared)

    index = BackboneIndex(session, pack_name, rules.rules, rules.pipelines)
    index.aliases = rules.aliases
    logger.info("Alias table: %d entries", len(rules.aliases))

    resolved: list[dict] = []
    provisional: list[dict] = []

    for mention in session.run(_MENTIONS, pack=pack_name, entities=rules.entity_labels):
        text = (mention["text"] or "").strip()
        if not text:
            continue

        # An __Entity__ node accumulates every type the model ever gave it: the
        # store MERGEs globally on id, so one node ends up labelled both
        # REPOSITORY and PACKAGE. Taking labels[0] let Neo4j's arbitrary label
        # order decide which rule ran — 24 package mentions resolved to
        # repositories that way. Every applicable rule is tried and the best
        # match wins, which is at least a decision this code makes.
        entity, rule, match = _best_across_types(mention["entities"], text, rules, index)
        if rule is None:
            continue

        report.mentions[entity] += 1
        counts = report.by_entity.setdefault(entity, Counter())

        if match is not None:
            report.methods[match.method] += 1
            counts[match.method] += 1
            resolved.append(
                {
                    "eid": mention["eid"],
                    "canonical_id": match.canonical_id,
                    "method": match.method,
                    "score": match.score,
                }
            )
        elif rule.keeps_unresolved:
            report.methods["provisional"] += 1
            counts["provisional"] += 1
            provisional.append(
                {
                    "eid": mention["eid"],
                    "canonical_id": _provisional_id(pack_name, entity, text, rule, rules),
                    "text": text,
                    "entity": entity,
                }
            )
        else:
            report.methods["drop"] += 1
            counts["drop"] += 1
            if len(report.unresolved_samples) < sample_unresolved:
                report.unresolved_samples.append((entity, text))

        if len(resolved) >= BATCH_SIZE:
            _write(session, pack_name, _LINK, resolved)
            resolved = []
        if len(provisional) >= BATCH_SIZE:
            _write(session, pack_name, _LINK_PROVISIONAL, provisional)
            provisional = []

    _write(session, pack_name, _LINK, resolved)
    _write(session, pack_name, _LINK_PROVISIONAL, provisional)

    logger.info(
        "Resolved %d/%d mentions for '%s' (%s)",
        report.resolved,
        report.total,
        pack_name,
        ", ".join(f"{m}={n}" for m, n in report.methods.most_common()),
    )
    return report


def _resolve_one(text: str, rule: Rule, rules: ResolveRules, index: BackboneIndex) -> Match | None:
    """Try the rule's methods in order and take the first answer.

    Order is the pack's, and it is meaningful: the methods are listed from most
    to least trustworthy, so stopping early is stopping at the best available
    answer rather than merely the first.
    """
    for name in rule.methods:
        method = METHODS[name]
        match = method(text, rule, rules.pipelines, index)
        if match is not None:
            return match
    return None


#: How much a resolution is worth, best first. A mention typed both REPOSITORY
#: and PACKAGE should become whichever the backbone actually knows it as, and an
#: exact hit is stronger evidence of that than a fuzzy one.
_METHOD_RANK = {"exact": 0, "alias": 1, "fuzzy": 2}


def _best_across_types(
    entities: list[str], text: str, rules: ResolveRules, index: BackboneIndex
) -> tuple[str, Rule | None, Match | None]:
    """Resolve a mention under every type it was given and keep the best answer.

    Returns the type whose rule produced it, so the report still counts a
    mention once and under the type it was finally read as.

    Ties — two types resolving equally well — go to the pack's declaration
    order, which is the one ordering a pack author controls. Neo4j's label order
    is not.
    """
    best: tuple[int, str, Rule, Match] | None = None
    fallback: tuple[str, Rule] | None = None

    for entity in sorted(entities, key=rules.declaration_index):
        rule = rules.for_entity(entity)
        if rule is None:
            continue
        if fallback is None:
            fallback = (entity, rule)
        match = _resolve_one(text, rule, rules, index)
        if match is None:
            continue
        rank = _METHOD_RANK.get(match.method, len(_METHOD_RANK))
        if best is None or rank < best[0]:
            best = (rank, entity, rule, match)

    if best is not None:
        return best[1], best[2], best[3]
    if fallback is not None:
        return fallback[0], fallback[1], None
    return "", None, None


def _provisional_id(pack: str, entity: str, text: str, rule: Rule, rules: ResolveRules) -> str:
    """A stable identifier for something the backbone does not contain.

    Namespaced away from real identifiers so a provisional node can never be
    mistaken for a resolved one, and derived from the normalised text so the
    same unplaceable mention in two documents lands on one node.
    """
    from graphpack.resolve.methods import apply

    normalised = apply(rule.match or rule.id, text, rules.pipelines) or text
    return f"prov:{pack}:{entity.lower()}:{normalised}"


def _clear_previous(session, pack_name: str) -> int:
    """Remove a previous pass's conclusions, leaving extraction untouched."""
    before = session.run(
        f"MATCH (:{ENTITY_LABEL} {{pack: $pack}})-[r:RESOLVED_AS]->() RETURN count(r) AS n",
        pack=pack_name,
    ).single()["n"]
    if before:
        session.run(_CLEAR, pack=pack_name).consume()
    session.run(_DROP_PROVISIONAL, pack=pack_name).consume()
    return int(before)


def _write(session, pack_name: str, query: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    record = session.run(query, rows=rows, pack=pack_name).single()
    return int(record["linked"]) if record else 0

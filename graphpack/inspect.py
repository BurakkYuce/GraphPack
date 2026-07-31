"""Report what extraction actually wrote.

Two things have to be true before a resolution pass can be built on top of
extracted entities, and neither is documented by the engine:

1. **What an entity node looks like.** The engine writes entities through
   LlamaIndex's Neo4j property-graph store, which chooses the labels and
   property names. A resolver has to match those exactly.
2. **Whether the pack tag survives.** Documents are tagged with their pack, and
   knowledge-graph extraction is expected to copy source metadata onto extracted
   entities. If it does not, entities from two packs are indistinguishable in a
   single shared database.

Both are answered by looking, not by assuming — which is also why this is a
command rather than a comment.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: The label LlamaIndex's Neo4j store puts on every extracted entity.
ENTITY_LABEL = "__Entity__"


@dataclass
class EntityShape:
    """What the entity nodes in the graph look like."""

    total: int = 0
    labels: Counter = field(default_factory=Counter)
    properties: Counter = field(default_factory=Counter)
    pack_values: Counter = field(default_factory=Counter)
    untagged: int = 0
    samples: list[dict] = field(default_factory=list)

    @property
    def pack_tag_survives(self) -> bool:
        """Whether extracted entities carry the pack tag their documents had."""
        return self.total > 0 and self.untagged == 0


def inspect_entities(session, sample_size: int = 5) -> EntityShape:
    """Describe the extracted entities currently in the graph."""
    shape = EntityShape()

    record = session.run(f"MATCH (e:{ENTITY_LABEL}) RETURN count(e) AS n").single()
    shape.total = int(record["n"]) if record else 0
    if not shape.total:
        return shape

    for row in session.run(
        f"MATCH (e:{ENTITY_LABEL}) UNWIND labels(e) AS label "
        "RETURN label, count(*) AS n ORDER BY n DESC"
    ):
        shape.labels[row["label"]] = row["n"]

    for row in session.run(
        f"MATCH (e:{ENTITY_LABEL}) UNWIND keys(e) AS key RETURN key, count(*) AS n ORDER BY n DESC"
    ):
        shape.properties[row["key"]] = row["n"]

    for row in session.run(
        f"MATCH (e:{ENTITY_LABEL}) RETURN coalesce(e.pack, '<missing>') AS pack, "
        "count(*) AS n ORDER BY n DESC"
    ):
        shape.pack_values[row["pack"]] = row["n"]
    shape.untagged = shape.pack_values.get("<missing>", 0)

    shape.samples = [
        dict(row["e"])
        for row in session.run(f"MATCH (e:{ENTITY_LABEL}) RETURN e LIMIT $n", n=sample_size)
    ]
    return shape


@dataclass
class RelationReport:
    """Extracted relation types, split by whether the ontology declares them."""

    counts: list[tuple[str, int]] = field(default_factory=list)
    declared: set[str] = field(default_factory=set)

    @property
    def in_ontology(self) -> int:
        return sum(n for name, n in self.counts if name in self.declared)

    @property
    def outside_ontology(self) -> int:
        return sum(n for name, n in self.counts if name not in self.declared)

    @property
    def conformance(self) -> float:
        """Share of extracted relations whose type the ontology declares.

        The number that says whether the schema reached the extractor. On the
        dynamic path the ontology is guidance rather than a constraint, so this
        sits below 1.0 by design; near zero would mean the schema never arrived.
        """
        total = self.in_ontology + self.outside_ontology
        return self.in_ontology / total if total else 0.0


def relationship_shape(
    session, declared: set[str] | None = None, limit: int = 40
) -> RelationReport:
    """Relationship types between extracted entities, most common first.

    Pass the pack's declared relations to see how much of what came back the
    ontology actually asked for.
    """
    rows = session.run(
        f"MATCH (:{ENTITY_LABEL})-[r]->(:{ENTITY_LABEL}) "
        "RETURN type(r) AS type, count(*) AS n ORDER BY n DESC LIMIT $limit",
        limit=limit,
    )
    return RelationReport(
        counts=[(row["type"], row["n"]) for row in rows],
        declared=declared or set(),
    )


def chunk_shape(session, limit: int = 5) -> dict:
    """What the engine wrote for the text chunks themselves.

    The fallback route for pack attribution: if extracted entities lose the tag,
    a chunk still carries it, and entities link back to their source chunk.
    """
    record = session.run(
        f"MATCH (c) WHERE NOT c:{ENTITY_LABEL} AND c.text IS NOT NULL RETURN count(c) AS n"
    ).single()
    total = int(record["n"]) if record else 0

    properties: Counter = Counter()
    if total:
        for row in session.run(
            f"MATCH (c) WHERE NOT c:{ENTITY_LABEL} AND c.text IS NOT NULL "
            "UNWIND keys(c) AS key RETURN key, count(*) AS n ORDER BY n DESC"
        ):
            properties[row["key"]] = row["n"]

    return {"total": total, "properties": properties, "limit": limit}

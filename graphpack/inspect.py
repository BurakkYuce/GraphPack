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


def _scoped(inner: str, pack: str | None) -> str:
    """A node pattern, filtered to one pack or left global.

    Given an inner pattern and a pack name, returns
    ``(e:__Entity__ {pack: $pack})``; given no pack name, ``(e:__Entity__)``.

    Every count here used to be database-wide even when a pack was named, and
    with two packs ingested that produced a genuinely wrong number: `graphpack
    inspect oss` compared oss's ontology against *every* pack's relations and
    reported 28% conformance while `validate-triples oss` reported 100% on the
    same graph, in the same minute.

    Global is still right for one question — whether the pack tag survives
    extraction is about attribution across packs, and scoping it would assume
    the answer.
    """
    return f"({inner})" if pack is None else f"({inner} {{pack: $pack}})"


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


def inspect_entities(session, sample_size: int = 5, pack: str | None = None) -> EntityShape:
    """Describe the extracted entities currently in the graph.

    Scoped to *pack* when one is given, except for the pack-tag census — see
    ``_scoped``.
    """
    shape = EntityShape()
    entity = _scoped(f"e:{ENTITY_LABEL}", pack)
    args = {"pack": pack} if pack else {}

    record = session.run(f"MATCH {entity} RETURN count(e) AS n", **args).single()
    shape.total = int(record["n"]) if record else 0
    if not shape.total:
        return shape

    for row in session.run(
        f"MATCH {entity} UNWIND labels(e) AS label RETURN label, count(*) AS n ORDER BY n DESC",
        **args,
    ):
        shape.labels[row["label"]] = row["n"]

    for row in session.run(
        f"MATCH {entity} UNWIND keys(e) AS key RETURN key, count(*) AS n ORDER BY n DESC", **args
    ):
        shape.properties[row["key"]] = row["n"]

    # Deliberately unscoped: "does the pack tag survive extraction" is a question
    # about every entity in a shared database, and filtering on the tag to count
    # the tag would answer it by assumption.
    for row in session.run(
        f"MATCH (e:{ENTITY_LABEL}) RETURN coalesce(e.pack, '<missing>') AS pack, "
        "count(*) AS n ORDER BY n DESC"
    ):
        shape.pack_values[row["pack"]] = row["n"]
    shape.untagged = shape.pack_values.get("<missing>", 0)

    shape.samples = [
        dict(row["e"])
        for row in session.run(f"MATCH {entity} RETURN e LIMIT $n", n=sample_size, **args)
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
    session, declared: set[str] | None = None, limit: int = 40, pack: str | None = None
) -> RelationReport:
    """Relationship types between extracted entities, most common first.

    Pass the pack's declared relations to see how much of what came back the
    ontology actually asked for — and pass the pack too, or the comparison is
    one pack's ontology against every pack's relations.
    """
    start = end = _scoped(f":{ENTITY_LABEL}", pack)
    rows = session.run(
        f"MATCH {start}-[r]->{end} "
        "RETURN type(r) AS type, count(*) AS n ORDER BY n DESC LIMIT $limit",
        limit=limit,
        **({"pack": pack} if pack else {}),
    )
    return RelationReport(
        counts=[(row["type"], row["n"]) for row in rows],
        declared=declared or set(),
    )


def chunk_shape(session, limit: int = 5, pack: str | None = None) -> dict:
    """What the engine wrote for the text chunks themselves.

    The fallback route for pack attribution: if extracted entities lose the tag,
    a chunk still carries it, and entities link back to their source chunk.

    Identified by "carries text and is not an entity" rather than by label,
    because which label the engine writes is one of the things this module
    exists to find out rather than assume. `Provisional` is excluded by name
    because it is ours: resolution writes it, it carries the mention text, and
    counting 69 of them as chunks made oss report 673 where the ingest wrote 604.
    """
    where = f"WHERE NOT c:{ENTITY_LABEL} AND NOT c:Provisional AND c.text IS NOT NULL"
    if pack:
        where += " AND c.pack = $pack"
    args = {"pack": pack} if pack else {}

    record = session.run(f"MATCH (c) {where} RETURN count(c) AS n", **args).single()
    total = int(record["n"]) if record else 0

    properties: Counter = Counter()
    if total:
        for row in session.run(
            f"MATCH (c) {where} UNWIND keys(c) AS key RETURN key, count(*) AS n ORDER BY n DESC",
            **args,
        ):
            properties[row["key"]] = row["n"]

    return {"total": total, "properties": properties, "limit": limit}

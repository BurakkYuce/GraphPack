"""Check extracted relations against the ontology's domain and range.

An ontology says more than which types exist: ``DEPENDS_ON`` runs from a package
to a package, ``AUTHORED`` from a person to an issue. The engine never passes
those constraints to the extractor — ``SchemaManager`` hands it entity and
relation *name lists* and nothing else, and the triples the OWL reader derives
reach only one adapter GraphPack does not use. So an ontology's ``rdfs:domain``
and ``rdfs:range`` constrain nothing during extraction.

This applies them afterwards. It is the only place they are applied at all, and
the count it produces is a quality measure extraction does not otherwise offer:
a relation can carry a declared type and still connect two things that type was
never meant to connect.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from graphpack.inspect import ENTITY_LABEL

logger = logging.getLogger(__name__)

#: Labels the store adds to every entity; they are not ontology types.
_STRUCTURAL_LABELS = frozenset({ENTITY_LABEL, "__Node__", "Chunk", "Provisional"})

_TRIPLES = f"""
MATCH (a:{ENTITY_LABEL} {{pack: $pack}})-[r]->(b:{ENTITY_LABEL} {{pack: $pack}})
RETURN [l IN labels(a) WHERE NOT l IN $structural] AS subject_types,
       type(r) AS relation,
       [l IN labels(b) WHERE NOT l IN $structural] AS object_types,
       count(*) AS n
"""


@dataclass(frozen=True)
class Violation:
    subject_type: str
    relation: str
    object_type: str
    #: The pairings the ontology does allow for this relation, as a readable
    #: string. Empty when the relation itself is undeclared.
    expected: str
    count: int = 1


@dataclass
class TripleReport:
    """How much of what extraction produced the ontology actually permits."""

    conforming: int = 0
    #: Declared relation type, but between types it does not pair.
    violating: int = 0
    #: Relation type the ontology never declared at all.
    undeclared: int = 0
    violations: list[Violation] = field(default_factory=list)
    undeclared_types: Counter = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return self.conforming + self.violating + self.undeclared

    @property
    def conformance(self) -> float:
        return self.conforming / self.total if self.total else 0.0


def validate_triples(session, pack: str, constraints: list[tuple[str, str, str]]) -> TripleReport:
    """Compare every extracted relation against *constraints*.

    ``constraints`` are ``(subject, relation, object)`` triples from the pack's
    ontology, as the OWL reader derives them from domain and range.
    """
    allowed: dict[str, set[tuple[str, str]]] = {}
    for subject, relation, obj in constraints:
        allowed.setdefault(relation, set()).add((subject, obj))

    report = TripleReport()

    for row in session.run(_TRIPLES, pack=pack, structural=sorted(_STRUCTURAL_LABELS)):
        relation = row["relation"]
        count = int(row["n"])
        pairings = allowed.get(relation)

        if pairings is None:
            report.undeclared += count
            report.undeclared_types[relation] += count
            continue

        # An entity can carry more than one ontology label. Conformance is
        # generous about that on purpose: if any pairing of the labels present
        # satisfies the constraint, the relation is not evidence of a mistake.
        subjects = row["subject_types"] or ["<untyped>"]
        objects = row["object_types"] or ["<untyped>"]
        if any((s, o) in pairings for s in subjects for o in objects):
            report.conforming += count
            continue

        report.violating += count
        report.violations.append(
            Violation(
                subject_type="/".join(subjects),
                relation=relation,
                object_type="/".join(objects),
                expected=", ".join(f"{s} -> {o}" for s, o in sorted(pairings)),
                count=count,
            )
        )

    report.violations.sort(key=lambda v: v.count, reverse=True)
    logger.info(
        "Triple check for '%s': %d conforming, %d wrongly typed, %d undeclared",
        pack,
        report.conforming,
        report.violating,
        report.undeclared,
    )
    return report

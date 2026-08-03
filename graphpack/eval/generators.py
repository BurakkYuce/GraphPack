"""Where ground truth comes from when nobody labelled anything.

``backbone_edges``: the backbone already states which entities are related, so
any pair the corpus discusses together is a fact the model had the chance to
find and no one had to annotate. Extraction is scored on whether it found them.

Both sides are sets of canonical pairs over the whole corpus, not per document.
That follows from how the engine stores things rather than from preference.
LlamaIndex's Neo4j store MERGEs entities on ``id``, so an entity named `urllib3`
is one node however many documents mention it, and it MERGEs relations on
``(source, type, target)`` with an empty key, so a dependency ten documents
discuss is one edge — attributed, by way of ``triplet_source_id``, to whichever
chunk happened to state it first. Scoring per document against that data model
would count ten gold instances against one findable prediction and call the
difference recall.

What per-document scoping was for is kept: a pair only becomes gold if some
document mentions both ends. Otherwise the corpus is charged with missing
relations nothing in it could have expressed.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from graphpack.inspect import ENTITY_LABEL

logger = logging.getLogger(__name__)

# Labels and relationship types cannot be parameterised in Cypher, so these are
# templates. Plain strings rather than f-strings: mixing the two means every
# literal Cypher brace has to survive one round of interpolation and then be
# hidden from the next, and `{pack: $pack}` reaching str.format is read as a
# field named `pack`. One mechanism, doubled braces, no ambiguity.

#: Which canonical entities each document mentions.
#:
#: Via the chunk, not via the entity's own ``ref_doc_id``. The entity is shared
#: between every document that mentions it, so its ``ref_doc_id`` names one of
#: them arbitrarily — whichever write landed last. The chunk is per document and
#: the store links the two with MENTIONS.
_MENTIONS_PER_DOCUMENT = """
MATCH (chunk)-[:MENTIONS]->(e:{entity} {{pack: $pack}})
MATCH (e)-[:RESOLVED_AS]->(c:{label} {{pack: $pack}})
WHERE chunk.ref_doc_id IS NOT NULL
RETURN chunk.ref_doc_id AS document, collect(DISTINCT c.id) AS entities
"""

#: Relations extraction claimed, in canonical terms. A relation counts only when
#: both ends resolved: a claim about something nobody can identify is not a
#: claim this evaluation can check.
_PREDICTED = """
MATCH (a:{entity} {{pack: $pack}})-[:{relation}]->(b:{entity} {{pack: $pack}})
MATCH (a)-[:RESOLVED_AS]->(ca:{label} {{pack: $pack}})
MATCH (b)-[:RESOLVED_AS]->(cb:{label} {{pack: $pack}})
WHERE ca.id <> cb.id
RETURN DISTINCT ca.id AS start, cb.id AS end
"""

#: The backbone's own relation — the part nobody had to annotate.
_BACKBONE = """
MATCH (a:{label} {{pack: $pack}})-[:{relation}]->(b:{label} {{pack: $pack}})
RETURN a.id AS start, b.id AS end
"""


def backbone_edges(session, pack: str, task) -> tuple[set, set, dict]:
    """Return ``(predicted, gold, diagnostics)`` as sets of ``(a, b)`` pairs."""
    backbone = {
        (row["start"], row["end"])
        for row in session.run(
            _BACKBONE.format(label=task.endpoint_label, relation=task.backbone_relation),
            pack=pack,
        )
    }
    if not backbone:
        logger.warning(
            "Backbone holds no %s edges between %s nodes — nothing to score against",
            task.backbone_relation,
            task.endpoint_label,
        )

    mentions: dict[str, set[str]] = {}
    for row in session.run(
        _MENTIONS_PER_DOCUMENT.format(entity=ENTITY_LABEL, label=task.endpoint_label), pack=pack
    ):
        mentions[row["document"]] = set(row["entities"])

    # A backbone edge becomes gold once some document mentions both ends. The
    # document is not part of the key — see the module docstring — but the
    # requirement is, because a relation the corpus never discusses is not
    # something extraction failed to find.
    gold: set[tuple[str, str]] = set()
    documents_carrying_gold: set[str] = set()
    for document, entities in mentions.items():
        for a in entities:
            for b in entities:
                if a != b and (a, b) in backbone:
                    gold.add(_orient(a, b, task))
                    documents_carrying_gold.add(document)

    predicted = {
        _orient(row["start"], row["end"], task)
        for row in session.run(
            _PREDICTED.format(
                entity=ENTITY_LABEL, label=task.endpoint_label, relation=task.relation
            ),
            pack=pack,
        )
    }

    diagnostics = {
        "documents_with_resolved_entities": len(mentions),
        "documents_carrying_gold": len(documents_carrying_gold),
        "backbone_edges": len(backbone),
        "resolvable_entities": len({e for entities in mentions.values() for e in entities}),
        "mentions_per_document": _histogram(mentions),
    }
    logger.info(
        "%s: %d gold pair(s), %d predicted, from %d document(s) with resolved entities",
        task.name,
        len(gold),
        len(predicted),
        len(mentions),
    )
    return predicted, gold, diagnostics


def _orient(a: str, b: str, task) -> tuple[str, str]:
    """Order a pair, or normalise it away when direction is not being judged."""
    return (a, b) if task.directed else tuple(sorted((a, b)))


def _histogram(mentions: dict[str, set[str]]) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for entities in mentions.values():
        counts[len(entities)] += 1
    return dict(sorted(counts.items()))


#: What the backbone says a document itself is connected to.
#:
#: A different shape of gold from ``backbone_edges``, and tr-law needs it: there,
#: the document *is* a node. A decision cites statutes, so the fact to be found
#: is `(this decision, that statute)` — not a pair of statutes, which is what
#: asking for two same-labelled endpoints produces. The tr-law backbone holds
#: 2,367 `Decision -[:CITES]-> Statute` edges and exactly zero
#: `Statute -[:CITES]-> Statute`, so the symmetric generator scored nothing.
_DOCUMENT_BACKBONE = """
MATCH (d:{source_label} {{pack: $pack}})-[:{relation}]->(t:{label} {{pack: $pack}})
RETURN d.id AS document, collect(DISTINCT t.id) AS targets
"""


def document_edges(session, pack: str, task) -> tuple[set, set, dict]:
    """Score what a document was linked to, against what the backbone says it is.

    Gold is `(document, target)` for every backbone edge out of a document that
    the corpus actually ingested. Prediction is `(document, target)` for every
    entity mentioned in that document's chunks that resolved to the right kind
    of node.

    Restricted to ingested documents on purpose. The backbone holds every
    decision the fetch produced; charging extraction for the 1,378 that were
    never put through it would be measuring the sample size.
    """
    backbone: dict[str, set[str]] = {}
    for row in session.run(
        _DOCUMENT_BACKBONE.format(
            source_label=task.source_label,
            relation=task.backbone_relation,
            label=task.endpoint_label,
        ),
        pack=pack,
    ):
        backbone[row["document"]] = set(row["targets"])

    mentions: dict[str, set[str]] = {}
    for row in session.run(
        _MENTIONS_PER_DOCUMENT.format(entity=ENTITY_LABEL, label=task.endpoint_label), pack=pack
    ):
        mentions[row["document"]] = set(row["entities"])

    ingested = set(mentions)
    if not ingested:
        logger.warning(
            "No document has a resolved %s mention — nothing to score", task.endpoint_label
        )

    gold = {
        (document, target)
        for document, targets in backbone.items()
        if document in ingested
        for target in targets
    }
    predicted = {(document, target) for document, targets in mentions.items() for target in targets}

    return (
        predicted,
        gold,
        {
            "documents_ingested": len(ingested),
            # Named to match `backbone_edges`, because the report is generic
            # over generators and prints these by key. A generator inventing its
            # own names crashes the command it feeds after the score is already
            # computed — which is exactly what happened.
            "documents_carrying_gold": sum(1 for d in backbone if d in ingested),
            "backbone_edges": sum(len(t) for t in backbone.values()),
            "documents_with_resolved_entities": len(ingested),
            "resolvable_entities": sum(len(e) for e in mentions.values()),
            "mentions_per_document": _histogram(mentions),
            # So an empty result can tell "nothing ran" from "everything ran and
            # nothing of this type resolved". Without it the report told tr-law
            # to re-run an ingest that had just taken fifty-five minutes.
            "chunks_ingested": _chunk_count(session, pack),
            "endpoint_label": task.endpoint_label,
        },
    )


def _chunk_count(session, pack: str) -> int:
    record = session.run("MATCH (c:Chunk {pack: $pack}) RETURN count(c) AS n", pack=pack).single()
    return int(record["n"]) if record else 0


#: Registered last, so every generator above is defined by the time it is read.
GENERATORS = {"backbone_edges": backbone_edges, "document_edges": document_edges}

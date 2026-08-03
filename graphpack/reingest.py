"""Forget one document, so it can be extracted again without redoing the corpus.

Re-extracting a pack is the expensive thing in this project: 55 minutes for
tr-law's 1,578 decisions. Until now every change to an ontology or a normaliser
cost a full run, because `pack reset --extraction-only` is all-or-nothing. Most
changes are worth testing on five documents first.

The recipe is the engine's, from ``incremental_updates/engine.py``, with one
step replaced — and the replacement is the whole reason this module exists
rather than a call into that one.

**Why the entity step is ours.** The engine deletes a document's entities by
matching on a ``doc_id`` property, which is right for its data model: there, a
document owns its entities. Here it does not. LlamaIndex's Neo4j store MERGEs
entities globally on ``id``, so one node is shared by every document that
mentions it. Measured on tr-law before this was written:

    5,130  entities reached from chunks
      965  mentioned by more than one document   (19%)
    1,175  documents sharing the most-shared entity

Deleting "the entities of document X" would therefore take entities that 1,174
other documents also mention. The reverse fails too: an entity's own
``ref_doc_id`` names whichever document wrote it last, so the property does not
even identify the document reliably — ``eval/generators.py`` documents that.

So chunks and vectors are deleted the engine's way, and entities are collected
afterwards by asking the graph what is still mentioned. That question has an
exact answer and the property does not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from graphpack.inspect import ENTITY_LABEL

logger = logging.getLogger(__name__)


@dataclass
class ForgetReport:
    pack: str
    documents: list[str] = field(default_factory=list)
    chunks_removed: int = 0
    entities_collected: int = 0
    #: Documents asked for that the graph did not hold. Not an error — asking to
    #: forget something already absent is the normal case on a first run — but
    #: reported, because it is also what a mistyped id looks like.
    absent: list[str] = field(default_factory=list)


#: Entities nothing mentions any more.
#:
#: Candidates are scoped to the pack so one pack's re-ingest cannot collect
#: another's orphans. The *mention* check is deliberately not scoped: an entity
#: node is MERGEd globally on id, so two packs extracting the same name would
#: share one node, and deleting it because this pack no longer mentions it would
#: silently damage the other. No such collision exists today — 0 of 5,549
#: entities are reached from more than one pack — but the query costs nothing to
#: write safely and the collision is one shared word away.
_COLLECT_ORPHANS = f"""
MATCH (e:{ENTITY_LABEL} {{pack: $pack}})
WHERE NOT (:Chunk)-[:MENTIONS]->(e)
CALL (e) {{ DETACH DELETE e }} IN TRANSACTIONS OF 10000 ROWS
"""

_COUNT_ORPHANS = f"""
MATCH (e:{ENTITY_LABEL} {{pack: $pack}})
WHERE NOT (:Chunk)-[:MENTIONS]->(e)
RETURN count(e) AS n
"""

_COUNT_CHUNKS = """
MATCH (c:Chunk {pack: $pack}) WHERE c.ref_doc_id IN $ids RETURN count(c) AS n
"""

_PRESENT = """
MATCH (c:Chunk {pack: $pack}) WHERE c.ref_doc_id IN $ids
RETURN collect(DISTINCT c.ref_doc_id) AS found
"""


def forget_documents(session, system, pack_name: str, doc_ids: list[str]) -> ForgetReport:
    """Remove *doc_ids* from every index, then collect what nothing mentions.

    ``system`` must be the engine instance the caller will re-ingest with — see
    ``ingest_pack``'s note on why that matters.
    """
    report = ForgetReport(pack=pack_name, documents=list(doc_ids))
    if not doc_ids:
        return report

    found = set(session.run(_PRESENT, pack=pack_name, ids=doc_ids).single()["found"] or [])
    report.absent = [d for d in doc_ids if d not in found]
    if report.absent:
        logger.info(
            "%d of %d document(s) are not in the graph — nothing to forget for those",
            len(report.absent),
            len(doc_ids),
        )
    report.chunks_removed = session.run(_COUNT_CHUNKS, pack=pack_name, ids=doc_ids).single()["n"]

    for doc_id in doc_ids:
        _forget_one(system, doc_id)
    session.run(_DELETE_CHUNKS, pack=pack_name, ids=doc_ids).consume()

    # After the chunks are gone, not before: an entity is an orphan only once
    # every chunk that mentioned it has been removed.
    report.entities_collected = session.run(_COUNT_ORPHANS, pack=pack_name).single()["n"]
    if report.entities_collected:
        session.run(_COLLECT_ORPHANS, pack=pack_name).consume()

    logger.info(
        "Forgot %d document(s) from '%s': %d chunk(s), %d orphaned entity(ies)",
        len(doc_ids) - len(report.absent),
        pack_name,
        report.chunks_removed,
        report.entities_collected,
    )
    return report


#: Chunks belonging to one document.
#:
#: Ours rather than `PropertyGraphIndex.delete_ref_doc`, and that is not a
#: preference. `delete_ref_doc` works through the index's *docstore*, which is
#: built in memory by whichever process ingested. A process that opens an
#: existing graph has an empty one, so the call returns without raising and
#: deletes nothing — the same shape as the BM25 leg not surviving a process
#: boundary, and the same reason: the engine keeps state a database could hold.
#:
#: Verified rather than assumed: called against a document with one chunk, it
#: returned cleanly and the chunk count was unchanged.
_DELETE_CHUNKS = """
MATCH (c:Chunk {pack: $pack}) WHERE c.ref_doc_id IN $ids
CALL (c) { DETACH DELETE c } IN TRANSACTIONS OF 10000 ROWS
"""


def _forget_one(system, doc_id: str) -> None:
    """Delete one document's vectors.

    The graph side is a single batched statement over every id — see
    ``_DELETE_CHUNKS`` — because it is our Cypher and one round trip is enough.
    Vectors are per document because the store's API is.

    Failure is logged rather than raised. A document absent from one index and
    present in another is the normal state after an interrupted run, and
    refusing to continue would make the recovery path need its own recovery
    path.
    """
    vector_store = getattr(system, "vector_store", None)
    if vector_store is not None and hasattr(vector_store, "delete"):
        try:
            vector_store.delete(doc_id)
        except Exception as exc:  # noqa: BLE001 — absent vectors are not a failure
            logger.debug("No vectors for %s — %s", doc_id, exc)

"""Pack-scoped teardown.

The engine ships ``scripts/cleanup.py``, which wipes every store wholesale.
That is unusable here: two packs share one Neo4j database, and the whole point
of the development loop is re-ingesting one of them without disturbing the
other.  Everything below is scoped by pack.
"""

from __future__ import annotations

import logging

from graphpack.inspect import ENTITY_LABEL
from graphpack.migrations import runner as migration_runner
from graphpack.packs import registry
from graphpack.packs.contract import Pack

logger = logging.getLogger(__name__)

# Chunk and entity nodes written by the engine's Neo4j property-graph store do
# not carry our `pack` property until extraction copies it from document
# metadata, so both are matched: the tagged nodes we wrote ourselves, and the
# engine-written nodes whose pack tag arrived via metadata.
_DELETE_TAGGED = """
MATCH (n {pack: $pack})
CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS
"""

_COUNT_TAGGED = "MATCH (n {pack: $pack}) RETURN count(n) AS n"

# What an ingest produced, as opposed to what the backbone loaded: entity nodes
# the extractor invented, and the text chunks they came from. Both carry the
# pack tag, which is how they are told apart from another pack's.
_EXTRACTED = f"MATCH (n {{pack: $pack}}) WHERE n:{ENTITY_LABEL} OR n.text IS NOT NULL"

_DELETE_EXTRACTED = f"""
{_EXTRACTED}
CALL (n) {{ DETACH DELETE n }} IN TRANSACTIONS OF 10000 ROWS
"""

_COUNT_EXTRACTED = f"{_EXTRACTED} RETURN count(n) AS n"


def delete_pack_nodes(session, pack_name: str) -> int:
    """Delete every node tagged with this pack. Returns how many were removed."""
    before = session.run(_COUNT_TAGGED, pack=pack_name).single()["n"]
    if before:
        session.run(_DELETE_TAGGED, pack=pack_name).consume()
    remaining = session.run(_COUNT_TAGGED, pack=pack_name).single()["n"]
    if remaining:
        raise RuntimeError(f"{remaining} nodes for pack '{pack_name}' survived deletion")
    return int(before)


def delete_extracted_nodes(session, pack_name: str) -> int:
    """Delete what extraction produced, leaving the backbone in place.

    Changing an ontology means re-extracting, and re-extracting should not mean
    re-downloading and re-loading a backbone that has not changed. The backbone
    is also the ground truth extraction is measured against, so keeping it fixed
    across runs is what makes two runs comparable.
    """
    before = session.run(_COUNT_EXTRACTED, pack=pack_name).single()["n"]
    if before:
        session.run(_DELETE_EXTRACTED, pack=pack_name).consume()
    remaining = session.run(_COUNT_EXTRACTED, pack=pack_name).single()["n"]
    if remaining:
        raise RuntimeError(f"{remaining} extracted nodes for '{pack_name}' survived deletion")
    return int(before)


def drop_qdrant_collection(collection: str) -> bool:
    """Drop the pack's Qdrant collection. Returns False when it did not exist."""
    from qdrant_client import QdrantClient

    from graphpack.packs.loader import qdrant_config

    config = qdrant_config(collection)
    client = QdrantClient(
        host=config["host"],
        port=config["port"],
        api_key=config["api_key"],
        https=config["https"],
    )
    try:
        if not client.collection_exists(collection):
            return False
        client.delete_collection(collection)
        return True
    finally:
        client.close()


def reset_extraction(session, pack: Pack, drop_vectors: bool = True) -> dict[str, object]:
    """Undo an ingest, keeping the backbone.

    The Qdrant collection goes too: its vectors are chunk embeddings, and
    leaving them behind would let a search return text the graph no longer has.
    """
    deleted = delete_extracted_nodes(session, pack.name)
    dropped = drop_qdrant_collection(pack.qdrant_collection) if drop_vectors else False

    logger.info(
        "reset extraction for pack=%s: %d nodes deleted, qdrant collection %s",
        pack.name,
        deleted,
        "dropped" if dropped else "absent",
    )
    return {"nodes_deleted": deleted, "migrations_forgotten": 0, "qdrant_dropped": dropped}


def reset_pack(session, pack: Pack, drop_vectors: bool = True) -> dict[str, object]:
    """Return the graph to the state it had before this pack was loaded.

    Pack-scoped migrations are forgotten so they re-run on the next ``migrate``;
    their DDL is idempotent, and any data they carry has just been deleted.
    """
    deleted = delete_pack_nodes(session, pack.name)
    registry.forget(session, pack.name)

    pack_migrations = [m.id for m in migration_runner.discover() if m.scope == pack.name]
    forgotten = migration_runner.forget(session, pack_migrations) if pack_migrations else 0

    dropped = drop_qdrant_collection(pack.qdrant_collection) if drop_vectors else False

    logger.info(
        "reset pack=%s: %d nodes deleted, %d migrations forgotten, qdrant collection %s",
        pack.name,
        deleted,
        forgotten,
        "dropped" if dropped else "absent",
    )
    return {
        "nodes_deleted": deleted,
        "migrations_forgotten": forgotten,
        "qdrant_dropped": dropped,
    }

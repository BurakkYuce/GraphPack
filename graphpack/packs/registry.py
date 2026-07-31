"""Record which packs the graph holds, and at which ontology.

Kept apart from :mod:`graphpack.migrations` because this is per-pack state that
changes with every ingest, not a schema version that changes with every release.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from graphpack.packs.contract import Pack

_UPSERT = """
MERGE (p:_Pack {name: $name})
SET   p.version            = $version,
      p.ontology_checksum  = $ontology_checksum,
      p.qdrant_collection  = $qdrant_collection,
      p.registered_at      = coalesce(p.registered_at, $now),
      p.updated_at         = $now
RETURN p.name AS name
"""

_READ = """
MATCH (p:_Pack)
RETURN p.name AS name, p.version AS version,
       p.ontology_checksum AS ontology_checksum,
       p.qdrant_collection AS qdrant_collection,
       p.updated_at AS updated_at
ORDER BY p.name
"""


def register(session, pack: Pack) -> None:
    """Upsert the registry row for *pack*."""
    session.run(
        _UPSERT,
        name=pack.name,
        version=pack.version,
        ontology_checksum=pack.ontology_checksum,
        qdrant_collection=pack.qdrant_collection,
        now=datetime.now(UTC).isoformat(),
    )


def registered(session) -> list[dict[str, Any]]:
    """Every registered pack, ordered by name."""
    return [dict(row) for row in session.run(_READ)]


def ontology_drift(session, pack: Pack) -> str | None:
    """Return the stored checksum when it disagrees with the pack on disk.

    A mismatch means the graph holds entities extracted under a different
    ontology than the one now on disk; that is a migration, not a no-op.
    """
    row = session.run(
        "MATCH (p:_Pack {name: $name}) RETURN p.ontology_checksum AS checksum",
        name=pack.name,
    ).single()
    if row is None or not row["checksum"]:
        return None
    stored = str(row["checksum"])
    return None if stored == pack.ontology_checksum else stored


def forget(session, pack_name: str) -> None:
    """Drop a pack's registry row — part of ``pack reset``."""
    session.run("MATCH (p:_Pack {name: $name}) DETACH DELETE p", name=pack_name)

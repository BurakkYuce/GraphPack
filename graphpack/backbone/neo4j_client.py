"""Neo4j connection handling for migrations and backbone loading.

Separate from the engine's store layer on purpose: migrations run before any
pack has been ingested, and the backbone writes structured data that never goes
through chunking or extraction.

One instance, one database.  The docker image is Neo4j Community, which supports
a single database, so packs coexist in ``neo4j`` and are separated by a ``pack``
property on every node.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DATABASE = "neo4j"


def connection_params() -> dict[str, str]:
    """Bolt connection settings, ``GRAPHPACK_``-prefixed so they cannot be
    confused with the engine's own ``NEO4J_*`` variables."""
    return {
        "uri": os.getenv("GRAPHPACK_NEO4J_URI", "bolt://localhost:7687"),
        "user": os.getenv("GRAPHPACK_NEO4J_USER", "neo4j"),
        "password": os.getenv("GRAPHPACK_NEO4J_PASSWORD", "password"),
    }


def driver_from_env():
    """Create a driver and verify it can actually reach the server.

    Connecting lazily would push a refused connection into the middle of a
    migration, where it reads as a migration failure rather than a missing
    container.
    """
    from neo4j import GraphDatabase
    from neo4j.exceptions import AuthError, ServiceUnavailable

    params = connection_params()
    driver = GraphDatabase.driver(params["uri"], auth=(params["user"], params["password"]))
    try:
        driver.verify_connectivity()
    except ServiceUnavailable as exc:
        driver.close()
        raise ConnectionError(
            f"Neo4j is not reachable at {params['uri']}. "
            "Start it with `docker compose -f infra/compose.yaml up -d`."
        ) from exc
    except AuthError as exc:
        driver.close()
        raise ConnectionError(
            f"Neo4j rejected the credentials for user '{params['user']}'. "
            "Set GRAPHPACK_NEO4J_USER / GRAPHPACK_NEO4J_PASSWORD."
        ) from exc

    logger.debug("Connected to Neo4j at %s (database=%s)", params["uri"], DATABASE)
    return driver


@contextmanager
def session_scope() -> Iterator:
    """Session bound to the shared database, closing the driver on exit."""
    driver = driver_from_env()
    try:
        with driver.session(database=DATABASE) as session:
            yield session
    finally:
        driver.close()

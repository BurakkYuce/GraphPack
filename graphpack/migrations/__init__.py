"""Ordered, idempotent Neo4j migrations.

Neo4j is schemaless but not undisciplined: constraints, indexes and backfills
are versioned the way Alembic versions a relational schema.  Applied migrations
are recorded as ``(:_Migration)`` nodes inside the graph itself, so the database
is the single source of truth about its own state.

There is no ``down()``.  Reversing a graph migration reliably is not something
we can promise, so the rollback story is a ``neo4j-admin dump`` snapshot taken
before each phase.
"""

from graphpack.migrations.runner import (
    Migration,
    MigrationError,
    apply_pending,
    discover,
    status,
)

__all__ = ["Migration", "MigrationError", "apply_pending", "discover", "status"]

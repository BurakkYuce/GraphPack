"""Migration bookkeeping itself.

The runner bootstraps the ``_Migration`` uniqueness constraint before reading
state, so this migration is mostly a marker: it makes the very first row in the
ledger describe the ledger, and it gives ``migrate`` on an empty database
something to report.
"""

CONSTRAINTS = (
    # Idempotent by id — a migration is applied at most once.
    "CREATE CONSTRAINT graphpack_migration_id IF NOT EXISTS "
    "FOR (m:_Migration) REQUIRE m.id IS UNIQUE",
    # Applied order is queried on every run.
    "CREATE INDEX graphpack_migration_number IF NOT EXISTS FOR (m:_Migration) ON (m.number)",
)


def up(session) -> None:
    for statement in CONSTRAINTS:
        session.run(statement)

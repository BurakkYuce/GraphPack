"""Discover, order and apply migrations.

Uses the Neo4j driver directly — no engine involvement — because migrations must
run before any pack has been ingested and must keep working if the engine's
store layer changes.

A migration file is ``versions/NNN_<scope>_<name>.py`` exposing::

    def up(session) -> None: ...

``scope`` is ``core`` or a pack name.  Numbering is global and gapless-by-
convention; ordering is by the numeric prefix.

``up`` receives a *session*, not a transaction, so each statement auto-commits.
That is deliberate: Neo4j refuses to mix schema changes and data writes inside
one transaction, and a migration that creates an index and then backfills
against it is a perfectly reasonable thing to write.  The price is that a
migration can fail halfway, so **every ``up`` must be safe to run twice** —
``IF NOT EXISTS`` for DDL, ``MERGE`` for writes.  The checksum guard protects
against migrations edited after the fact; idempotency protects against partial
application.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VERSIONS_DIR = Path(__file__).resolve().parent / "versions"

_FILENAME = re.compile(r"^(?P<number>\d{3})_(?P<scope>[a-z0-9-]+)_(?P<name>[a-z0-9_]+)\.py$")


class MigrationError(Exception):
    """Raised when migrations are malformed, out of order, or altered after use."""


@dataclass(frozen=True)
class Migration:
    number: int
    scope: str
    name: str
    path: Path
    checksum: str
    #: ``up(session)`` — see the module docstring on why it is not ``up(tx)``.
    up: Callable[[Any], None]

    @property
    def id(self) -> str:
        return f"{self.number:03d}_{self.scope}_{self.name}"

    def __str__(self) -> str:
        return self.id


# ----------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------


def discover(versions_dir: Path | None = None) -> list[Migration]:
    """Load every migration module in numeric order."""
    directory = versions_dir or VERSIONS_DIR
    if not directory.is_dir():
        return []

    migrations: list[Migration] = []
    seen: dict[int, str] = {}

    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        match = _FILENAME.match(path.name)
        if not match:
            raise MigrationError(
                f"{path.name}: expected NNN_<scope>_<name>.py, e.g. 010_oss_constraints.py"
            )

        number = int(match["number"])
        if number in seen:
            raise MigrationError(
                f"duplicate migration number {number:03d}: {seen[number]} and {path.name}"
            )
        seen[number] = path.name

        module = _load_module(path)
        up = getattr(module, "up", None)
        if not callable(up):
            raise MigrationError(f"{path.name}: no callable up(session)")

        migrations.append(
            Migration(
                number=number,
                scope=match["scope"],
                name=match["name"],
                path=path,
                checksum=checksum(path),
                up=up,
            )
        )

    return migrations


def checksum(path: Path) -> str:
    """Fingerprint a migration by its parsed syntax, not its bytes.

    Hashing the raw file would make the drift guard fire on a reformat or a
    reworded comment — changes that alter nothing about what the migration does.
    A guard that cries wolf gets switched off, so this hashes the AST instead:
    invariant to layout and comments, sensitive to every change in behaviour.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise MigrationError(f"{path.name}: syntax error — {exc}") from exc
    return hashlib.sha256(ast.dump(tree).encode("utf-8")).hexdigest()


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"graphpack._migration_{path.stem}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import machinery
        raise MigrationError(f"{path}: could not be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ----------------------------------------------------------------------
# State in the graph
# ----------------------------------------------------------------------

BOOTSTRAP = (
    "CREATE CONSTRAINT graphpack_migration_id IF NOT EXISTS "
    "FOR (m:_Migration) REQUIRE m.id IS UNIQUE"
)

_APPLIED_QUERY = "MATCH (m:_Migration) RETURN m.id AS id, m.checksum AS checksum"

_RECORD_QUERY = """
MERGE (m:_Migration {id: $id})
SET   m.scope       = $scope,
      m.number      = $number,
      m.checksum    = $checksum,
      m.applied_at  = $applied_at
"""


def applied(session) -> dict[str, str]:
    """``{migration_id: checksum}`` for everything already applied."""
    session.run(BOOTSTRAP)
    return {row["id"]: row["checksum"] for row in session.run(_APPLIED_QUERY)}


def status(session, versions_dir: Path | None = None) -> tuple[list[Migration], list[Migration]]:
    """Return ``(already_applied, pending)`` and verify nothing was edited."""
    migrations = discover(versions_dir)
    done = applied(session)

    drifted = [m for m in migrations if m.id in done and done[m.id] not in (None, "", m.checksum)]
    if drifted:
        raise MigrationError(
            "these migrations were modified after being applied: "
            + ", ".join(m.id for m in drifted)
            + ". Add a new migration instead of editing an applied one."
        )

    return (
        [m for m in migrations if m.id in done],
        [m for m in migrations if m.id not in done],
    )


def apply_pending(
    session, versions_dir: Path | None = None, dry_run: bool = False
) -> list[Migration]:
    """Apply every pending migration in order. Returns those applied."""
    _, pending = status(session, versions_dir)
    if not pending:
        logger.info("No pending migrations")
        return []

    if dry_run:
        for migration in pending:
            logger.info("PENDING %s", migration.id)
        return pending

    for migration in pending:
        logger.info("Applying %s", migration.id)
        try:
            migration.up(session)
        except Exception as exc:
            raise MigrationError(f"{migration.id} failed: {exc}") from exc
        session.run(
            _RECORD_QUERY,
            id=migration.id,
            scope=migration.scope,
            number=migration.number,
            checksum=migration.checksum,
            applied_at=datetime.now(UTC).isoformat(),
        )
        logger.info("Applied  %s", migration.id)

    return pending


def forget(session, ids: Iterable[str]) -> int:
    """Delete ``_Migration`` records for *ids*.

    Used by ``pack reset`` so a pack's migrations re-run after its data is
    dropped. Deliberately does not undo the migration itself — the DDL is
    idempotent, and data-bearing migrations are re-run from scratch.
    """
    result = session.run(
        "MATCH (m:_Migration) WHERE m.id IN $ids DETACH DELETE m RETURN count(m) AS n",
        ids=list(ids),
    ).single()
    return int(result["n"]) if result else 0

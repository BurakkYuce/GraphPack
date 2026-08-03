"""Migration discovery, ordering and the checksum guard.

Discovery is unit-testable against temporary files; applying needs a real
server, so those tests carry the ``integration`` marker and skip when Neo4j is
not running.
"""

from __future__ import annotations

import re
import textwrap

import pytest

from graphpack.migrations import runner

UP_NOOP = "def up(session):\n    pass\n"


def _write(directory, filename: str, body: str = UP_NOOP):
    path = directory / filename
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def versions(tmp_path):
    directory = tmp_path / "versions"
    directory.mkdir()
    return directory


# ----------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_migrations_are_ordered_by_number_not_filename(versions):
    _write(versions, "010_oss_constraints.py")
    _write(versions, "002_core_registry.py")
    _write(versions, "100_trlaw_backbone.py")

    found = runner.discover(versions)

    assert [m.number for m in found] == [2, 10, 100]
    assert [m.scope for m in found] == ["core", "oss", "trlaw"]
    assert found[0].id == "002_core_registry"


@pytest.mark.unit
def test_duplicate_numbers_are_rejected(versions):
    """Two migrations sharing a number would apply in filesystem order, which is
    not the order anybody reviewing the list would assume."""
    _write(versions, "010_core_one.py")
    _write(versions, "010_core_two.py")

    with pytest.raises(runner.MigrationError, match="duplicate migration number 010"):
        runner.discover(versions)


@pytest.mark.unit
@pytest.mark.parametrize("filename", ["oops.py", "1_core_short.py", "010-core-dashes.py"])
def test_malformed_filenames_are_rejected(versions, filename):
    _write(versions, filename)

    with pytest.raises(runner.MigrationError, match="expected NNN_<scope>_<name>.py"):
        runner.discover(versions)


@pytest.mark.unit
def test_migration_without_up_is_rejected(versions):
    _write(versions, "001_core_empty.py", "VALUE = 1\n")

    with pytest.raises(runner.MigrationError, match="no callable up"):
        runner.discover(versions)


@pytest.mark.unit
def test_underscore_prefixed_files_are_ignored(versions):
    _write(versions, "__init__.py", "")
    _write(versions, "001_core_real.py")

    assert [m.id for m in runner.discover(versions)] == ["001_core_real"]


@pytest.mark.unit
def test_checksum_ignores_formatting_and_comments(versions):
    """The drift guard must not fire on a reformat.

    `ruff format` runs in CI and will happily rewrite a migration's layout. If
    that counted as drift, the guard would produce false alarms until someone
    turned it off — so it fingerprints the AST, not the bytes.
    """
    path = _write(versions, "001_core_style.py", "def up(session):\n    session.run('RETURN 1')\n")
    original = runner.checksum(path)

    path.write_text(
        '# a new comment\ndef up(session):\n    session.run("RETURN 1")\n',
        encoding="utf-8",
    )

    assert runner.checksum(path) == original


@pytest.mark.unit
def test_checksum_changes_when_behaviour_changes(versions):
    path = _write(versions, "001_core_style.py", "def up(session):\n    session.run('RETURN 1')\n")
    original = runner.checksum(path)

    path.write_text("def up(session):\n    session.run('RETURN 2')\n", encoding="utf-8")

    assert runner.checksum(path) != original


@pytest.mark.unit
def test_shipped_migrations_are_discoverable():
    """The real versions/ directory must satisfy the same rules.

    Asserts the rules rather than the exact list. Pinning every id meant each new
    migration edited this test to say the same thing again, which is churn that
    teaches a reader nothing — and the property worth holding is that discovery
    orders them, numbers them uniquely, and names them the way the runner
    expects.
    """
    found = runner.discover()
    ids = [m.id for m in found]

    assert ids[:2] == ["001_core_runner", "002_core_pack_registry"]
    assert ids == sorted(ids), "migrations must be discovered in file order"
    numbers = [int(i.split("_", 1)[0]) for i in ids]
    assert numbers == sorted(set(numbers)), f"duplicate or out-of-order numbers: {ids}"
    assert all(re.fullmatch(r"\d{3}_[a-z]+_[a-z0-9_]+", i) for i in ids), ids


# ----------------------------------------------------------------------
# Applying
# ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.graph
def test_apply_is_idempotent_and_recorded(neo4j_session, versions):
    marker = "graphpack_test_idempotent"
    _write(
        versions,
        "900_core_testmarker.py",
        textwrap.dedent(
            f"""\
            def up(session):
                session.run("MERGE (n:_GraphPackTest {{name: '{marker}'}})")
            """
        ),
    )
    neo4j_session.run("MATCH (m:_Migration {id: '900_core_testmarker'}) DETACH DELETE m")
    neo4j_session.run("MATCH (n:_GraphPackTest) DETACH DELETE n")

    try:
        first = runner.apply_pending(neo4j_session, versions)
        second = runner.apply_pending(neo4j_session, versions)

        assert [m.id for m in first] == ["900_core_testmarker"]
        assert second == []
        count = neo4j_session.run(
            "MATCH (n:_GraphPackTest {name: $n}) RETURN count(n) AS c", n=marker
        ).single()["c"]
        assert count == 1

        record = neo4j_session.run(
            "MATCH (m:_Migration {id: '900_core_testmarker'}) "
            "RETURN m.scope AS scope, m.number AS number, m.applied_at AS applied_at"
        ).single()
        assert record["scope"] == "core"
        assert record["number"] == 900
        assert record["applied_at"]
    finally:
        neo4j_session.run("MATCH (m:_Migration {id: '900_core_testmarker'}) DETACH DELETE m")
        neo4j_session.run("MATCH (n:_GraphPackTest) DETACH DELETE n")


@pytest.mark.integration
@pytest.mark.graph
def test_editing_an_applied_migration_is_caught(neo4j_session, versions):
    """Silently re-running an edited migration would leave the graph in a state
    no version of the code produces."""
    path = _write(versions, "901_core_drift.py")
    neo4j_session.run("MATCH (m:_Migration {id: '901_core_drift'}) DETACH DELETE m")

    try:
        runner.apply_pending(neo4j_session, versions)
        # A real change in what the migration does — a reformat or a new comment
        # deliberately would not trip the guard.
        path.write_text(
            "def up(session):\n    session.run(\"MERGE (n:_GraphPackTest {name: 'drift'})\")\n",
            encoding="utf-8",
        )

        with pytest.raises(runner.MigrationError, match="modified after being applied"):
            runner.status(neo4j_session, versions)
    finally:
        neo4j_session.run("MATCH (m:_Migration {id: '901_core_drift'}) DETACH DELETE m")


@pytest.mark.integration
@pytest.mark.graph
def test_dry_run_reports_without_applying(neo4j_session, versions):
    _write(versions, "902_core_dryrun.py")
    neo4j_session.run("MATCH (m:_Migration {id: '902_core_dryrun'}) DETACH DELETE m")

    pending = runner.apply_pending(neo4j_session, versions, dry_run=True)

    assert [m.id for m in pending] == ["902_core_dryrun"]
    _, still_pending = runner.status(neo4j_session, versions)
    assert [m.id for m in still_pending] == ["902_core_dryrun"]

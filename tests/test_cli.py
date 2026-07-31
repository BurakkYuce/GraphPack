"""CLI behaviour that other things depend on.

Exit codes matter more than wording here: CI asserts on them, and a message that
reads well is not the same as a command that reports failure correctly.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from graphpack.cli import app

runner = CliRunner()


@pytest.fixture
def domains(monkeypatch, pack_dir):
    monkeypatch.setenv("GRAPHPACK_DOMAINS", str(pack_dir.domains))
    return pack_dir


@pytest.mark.unit
def test_validate_exits_non_zero_on_a_broken_pack(domains):
    domains("widgets")
    domains("broken", ontology="not turtle {{{")

    result = runner.invoke(app, ["packs", "validate"])

    assert result.exit_code == 1
    assert "broken" in result.output


@pytest.mark.unit
def test_validate_exits_zero_when_every_pack_is_sound(domains):
    domains("widgets")

    result = runner.invoke(app, ["packs", "validate"])

    assert result.exit_code == 0


@pytest.mark.unit
def test_schema_json_is_machine_readable(domains):
    domains("widgets")

    result = runner.invoke(app, ["packs", "schema", "widgets", "--json"])

    assert result.exit_code == 0
    assert "WIDGET" in result.output


@pytest.mark.integration
@pytest.mark.graph
def test_migrate_check_reports_pending_through_the_exit_code(neo4j_session):
    """CI depends on this: after a successful migrate, --check must exit 0.

    The first version of the CI step matched output text instead, and the regex
    `[0-9]* pending` matched the word "pending" inside "no pending migrations" —
    a green pipeline reported as red.
    """
    runner.invoke(app, ["migrate"])

    result = runner.invoke(app, ["migrate", "--check"])

    assert result.exit_code == 0, result.output


@pytest.mark.integration
@pytest.mark.graph
def test_migrate_check_applies_nothing(neo4j_session):
    neo4j_session.run("MATCH (m:_Migration) DETACH DELETE m")

    result = runner.invoke(app, ["migrate", "--check"])

    assert result.exit_code == 1
    remaining = neo4j_session.run("MATCH (m:_Migration) RETURN count(m) AS n").single()["n"]
    assert remaining == 0, "--check must not apply anything"

    runner.invoke(app, ["migrate"])  # leave the database as we found it

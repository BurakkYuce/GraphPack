"""Static pack validation."""

from __future__ import annotations

import textwrap

import pytest

from graphpack.packs.validate import validate_pack

pytestmark = pytest.mark.unit


@pytest.fixture
def domains(monkeypatch, pack_dir):
    """Point pack discovery at the temporary domains root."""
    monkeypatch.setenv("GRAPHPACK_DOMAINS", str(pack_dir.domains))
    return pack_dir


def test_valid_pack_passes_with_notes_for_later_phases(domains):
    domains("widgets")

    result = validate_pack("widgets")

    assert result.ok, result.errors
    assert "2 entity types" in result.summary
    # Files belonging to phases that have not landed are notes, not failures.
    assert any("sources.yaml" in w for w in result.warnings)


def test_relation_without_range_is_an_error(domains):
    ontology = textwrap.dedent(
        """\
        @prefix ex:   <http://example.org/test/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix owl:  <http://www.w3.org/2002/07/owl#> .

        ex:Widget a owl:Class .
        ex:loose a owl:ObjectProperty ;
            rdfs:domain ex:Widget .
        """
    )
    domains("loose", ontology=ontology)

    result = validate_pack("loose")

    assert not result.ok
    assert any("LOOSE is missing rdfs:range" in e for e in result.errors)


def test_entity_and_relation_name_collision_is_an_error(domains):
    """Both lists reach the extractor as label vocabularies; a shared name makes
    the extracted type ambiguous."""
    ontology = textwrap.dedent(
        """\
        @prefix ex:   <http://example.org/test/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix owl:  <http://www.w3.org/2002/07/owl#> .

        ex:Widget a owl:Class .
        ex:Build a owl:Class .
        ex:build a owl:ObjectProperty ;
            rdfs:domain ex:Widget ;
            rdfs:range  ex:Build .
        """
    )
    domains("collide", ontology=ontology)

    result = validate_pack("collide")

    assert not result.ok
    assert any("collide" in e and "BUILD" in e for e in result.errors)


def test_unparseable_ontology_is_reported_not_raised(domains):
    domains("broken", ontology="this is not turtle {{{")

    result = validate_pack("broken")

    assert not result.ok
    assert any("could not parse ontology" in e for e in result.errors)


def test_missing_pack_is_reported_not_raised(domains):
    domains("widgets")

    result = validate_pack("nope")

    assert not result.ok
    assert any("not found" in e for e in result.errors)


def test_invalid_yaml_in_optional_file_is_an_error(domains):
    root = domains("widgets")
    (root / "sources.yaml").write_text("sources: [unclosed\n", encoding="utf-8")

    result = validate_pack("widgets")

    assert not result.ok
    assert any("sources.yaml" in e and "invalid YAML" in e for e in result.errors)

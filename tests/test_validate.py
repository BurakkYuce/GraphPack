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
    assert "backbone: 1 fetch, 2 load" in result.summary
    assert "resolve: 1 rule" in result.summary
    # Files belonging to phases that have not landed are notes, not failures.
    assert not result.warnings or all("no resolve rule" in w for w in result.warnings)


def test_an_entity_type_with_no_resolve_rule_is_a_note(domains):
    """Not every extracted type has a canonical form, so this is not an error —
    but silence would let an omission pass for coverage."""
    domains("widgets")

    result = validate_pack("widgets")

    assert result.ok
    assert any("no resolve rule for FACTORY" in w for w in result.warnings)


def test_a_rule_for_an_entity_the_ontology_does_not_declare_is_an_error(domains):
    """It would match no mention and report nothing — a rule that quietly does
    no work at all."""
    domains(
        "widgets",
        resolve='resolve:\n  - {entity: GHOST, target: Widget, id: "w:{name}"}\n',
    )

    result = validate_pack("widgets")

    assert not result.ok
    assert any("ontology does not declare" in e for e in result.errors)


def test_a_rule_targeting_a_label_no_load_step_writes_is_an_error(domains):
    """Resolution would find no candidates and drop every mention, which looks
    exactly like a corpus that mentions nothing."""
    domains(
        "widgets",
        resolve='resolve:\n  - {entity: WIDGET, target: Sprocket, id: "w:{name}"}\n',
    )

    result = validate_pack("widgets")

    assert not result.ok
    assert any("no load step writes" in e for e in result.errors)


def test_a_file_required_by_an_implemented_phase_is_an_error(domains):
    """The distinction the warnings above rest on: once a phase ships, its
    artifacts stop being optional and a half-built pack fails loudly."""
    root = domains("widgets")
    (root / "sources.yaml").unlink()

    result = validate_pack("widgets")

    assert not result.ok
    assert any("sources.yaml missing" in e for e in result.errors)


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


def test_a_corpus_text_template_with_no_placeholder_is_rejected(domains):
    """`text: body` names a field to a reader and renders the literal string
    "body" to the code — 609 documents of four characters, embedded without
    complaint. The benchmark scoring zero is a long way from the cause."""
    domains(
        "widgets",
        sources=textwrap.dedent(
            """\
            fetch:
              - id: a
                url: https://example.invalid/a.json
                out: a.jsonl
            load:
              - source: a.jsonl
                node: {label: Widget, id: "w:{n}"}
            corpus:
              - source: a.jsonl
                id: "d:{n}"
                text: body
            """
        ),
    )

    result = validate_pack("widgets")

    assert not result.ok
    assert any("no placeholder" in e and "{body}" in e for e in result.errors)


def test_a_corpus_template_with_a_placeholder_passes(domains):
    domains(
        "widgets",
        sources=textwrap.dedent(
            """\
            fetch:
              - id: a
                url: https://example.invalid/a.json
                out: a.jsonl
            load:
              - source: a.jsonl
                node: {label: Widget, id: "w:{n}"}
            corpus:
              - source: a.jsonl
                id: "d:{n}"
                text: "{body}"
            """
        ),
    )

    result = validate_pack("widgets")

    assert not [e for e in result.errors if "placeholder" in e]


def test_an_eval_task_asking_for_a_shape_the_backbone_cannot_build_is_rejected(domains):
    """backbone_edges scores pairs of same-labelled nodes. tr-law's decisions
    cite statutes, so the backbone holds Decision->Statute and no
    Statute->Statute at all — and the mistake is invisible until an extraction
    run finishes and the gold set comes back empty. That run is fifteen hours."""
    domains(
        "widgets",
        sources=textwrap.dedent(
            """\
            fetch:
              - id: a
                url: https://example.invalid/a.json
                out: a.jsonl
            load:
              - source: a.jsonl
                node: {label: Doc, id: "d:{n}"}
              - source: a.jsonl
                node: {label: Widget, id: "w:{n}"}
              - source: a.jsonl
                edge: {type: CITES, from: "d:{n}", to: "w:{n}"}
            """
        ),
        evaluation=textwrap.dedent(
            """\
            tasks:
              - name: t
                generator: backbone_edges
                relation: CITES
                backbone_relation: CITES
                endpoint_label: Widget
            holdout: 0.0
            seed: 0
            """
        ),
    )

    result = validate_pack("widgets")

    assert not result.ok
    assert any("document_edges" in e and "Doc->Widget" in e for e in result.errors)


def test_the_document_shaped_generator_passes_the_same_check(domains):
    domains(
        "widgets",
        sources=textwrap.dedent(
            """\
            fetch:
              - id: a
                url: https://example.invalid/a.json
                out: a.jsonl
            load:
              - source: a.jsonl
                node: {label: Doc, id: "d:{n}"}
              - source: a.jsonl
                node: {label: Widget, id: "w:{n}"}
              - source: a.jsonl
                edge: {type: CITES, from: "d:{n}", to: "w:{n}"}
            """
        ),
        evaluation=textwrap.dedent(
            """\
            tasks:
              - name: t
                generator: document_edges
                relation: CITES
                backbone_relation: CITES
                source_label: Doc
                endpoint_label: Widget
            holdout: 0.0
            seed: 0
            """
        ),
    )

    result = validate_pack("widgets")

    assert not [e for e in result.errors if "eval task" in e]


def test_an_eval_task_scoring_a_relation_nothing_builds_is_a_note(domains):
    """Not an error — a pack may score a relation extraction invents and the
    backbone never states — but silence would let a typo pass for coverage."""
    domains(
        "widgets",
        evaluation=textwrap.dedent(
            """\
            tasks:
              - name: t
                generator: document_edges
                relation: NOPE
                backbone_relation: NOPE
                source_label: Doc
                endpoint_label: Widget
            holdout: 0.0
            seed: 0
            """
        ),
    )

    result = validate_pack("widgets")

    assert any("no load step builds" in w for w in result.warnings)

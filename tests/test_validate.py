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


def test_corpus_templates_do_not_short_circuit_the_backbone_checks(domains):
    """A regression, and the reason this test is specific rather than general.

    The corpus template check was inserted between `if not sources.load:` and its
    `return`, which put the `return` inside the new `for spec in sources.corpus:`
    loop. Every pack declaring a corpus then skipped everything below it — the
    backbone summary and the edge-prefix warning — after one iteration. Nothing
    failed; the output was simply shorter, and stayed that way for a day.

    So this asserts on what disappeared: the second corpus spec is still checked,
    and the checks that live below the loop still run.
    """
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
              - source: a.jsonl
                edge: {type: BUILT_IN, from: "w:{n}", to: "typo:{m}"}
            corpus:
              - source: a.jsonl
                id: "d:{n}"
                text: "{body}"
              - source: a.jsonl
                id: "e:{n}"
                text: body
            """
        ),
    )

    result = validate_pack("widgets")

    # The second corpus spec is reached at all.
    assert any("no placeholder" in e and "{body}" in e for e in result.errors)
    # And the checks below the loop still run.
    assert "backbone: 1 fetch, 2 load" in result.summary
    assert any("but no node step produces that prefix" in w for w in result.warnings)


def test_a_pack_with_a_corpus_and_no_load_steps_still_has_its_templates_checked(domains):
    """The no-load-steps return is legitimate — there is no backbone to describe.
    It must not take the corpus check down with it: documents and nodes are
    independent, and a pack may declare only the former."""
    domains(
        "widgets",
        sources=textwrap.dedent(
            """\
            fetch:
              - id: a
                url: https://example.invalid/a.json
                out: a.jsonl
            corpus:
              - source: a.jsonl
                id: "d:{n}"
                text: body
            """
        ),
    )

    result = validate_pack("widgets")

    assert any("no placeholder" in e for e in result.errors)
    assert any("no load steps" in w for w in result.warnings)


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


def _document_edges_pack(domains, node_id, corpus_id):
    domains(
        "widgets",
        sources=textwrap.dedent(
            f"""\
            normalize:
              slug: [lower]
            fetch:
              - id: a
                url: https://example.invalid/a.json
                out: a.jsonl
            load:
              - source: a.jsonl
                node: {{label: Doc, id: "{node_id}"}}
              - source: a.jsonl
                node: {{label: Widget, id: "w:{{n}}"}}
              - source: a.jsonl
                edge: {{type: CITES, from: "{node_id}", to: "w:{{n}}"}}
            corpus:
              - source: a.jsonl
                id: "{corpus_id}"
                text: "{{body}}"
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
            """
        ),
    )
    return validate_pack("widgets")


def test_document_nodes_in_another_namespace_than_the_corpus_is_an_error(domains):
    """The join is `document.id = chunk.ref_doc_id`, string equality. Different
    namespaces can never satisfy it, so the gold set comes back empty — which is
    what a corpus with genuinely no gold also reports, after however long
    extraction took."""
    result = _document_edges_pack(domains, "issue:{n}", "d:{n}")

    assert not result.ok
    assert any("different namespaces" in e for e in result.errors)


def test_the_same_namespace_written_differently_is_a_note_not_a_failure(domains):
    """`{n}` and `{n|slug}` may well render alike, and blocking a correct pack is
    not what this check is for. It exists to prevent a silent empty gold set, not
    to insist on one spelling."""
    result = _document_edges_pack(domains, "d:{n|slug}", "d:{n}")

    assert result.ok, result.errors
    assert any("not identical" in w for w in result.warnings)


def test_identical_templates_say_nothing_at_all(domains):
    result = _document_edges_pack(domains, "d:{n}", "d:{n}")

    assert result.ok, result.errors
    assert not [w for w in result.warnings if "ingested documents" in w]


def test_a_malformed_eval_file_is_an_error_not_silence(domains):
    """These used to be swallowed. `_check_eval_shape` caught EvalError and
    returned, with a comment saying the check that owns eval.yaml would report it
    — and no check owned eval.yaml. An unknown generator passed validation and
    failed with a KeyError after scoring."""
    domains(
        "widgets",
        evaluation=textwrap.dedent(
            """\
            tasks:
              - name: t
                generator: invented_edges
                relation: BUILT_IN
                endpoint_label: Widget
            holdout: 0.0
            """
        ),
    )

    result = validate_pack("widgets")

    assert not result.ok
    assert any("unknown generator 'invented_edges'" in e for e in result.errors)


def test_a_document_edges_task_without_a_source_label_is_rejected(domains):
    """It used to default to the label "Document", which no pack writes: gold came
    back empty and the run said "0 gold edges" — the same output a corpus with
    genuinely no gold produces, after however long extraction took."""
    domains(
        "widgets",
        evaluation=textwrap.dedent(
            """\
            tasks:
              - name: t
                generator: document_edges
                relation: BUILT_IN
                endpoint_label: Widget
            holdout: 0.0
            """
        ),
    )

    result = validate_pack("widgets")

    assert not result.ok
    assert any("needs 'source_label'" in e for e in result.errors)


def test_a_pack_declaring_no_eval_tasks_is_valid(domains):
    """`tasks: []` is a statement, not an omission: bench-wiki runs no extraction
    and its ground truth is the benchmark's own labelling, scored elsewhere. The
    contract used to treat an empty list and a missing key as the same thing, and
    surfacing that cost a red CI run the first time eval.yaml errors stopped
    being swallowed."""
    domains("widgets", evaluation="tasks: []\nholdout: 0.0\n")

    result = validate_pack("widgets")

    assert result.ok, result.errors


def test_an_eval_file_with_no_tasks_key_at_all_is_an_error(domains):
    """The other half of the distinction — a truncated file, not a claim."""
    domains("widgets", evaluation="holdout: 0.0\n")

    result = validate_pack("widgets")

    assert not result.ok
    assert any("'tasks' is missing" in e for e in result.errors)


def test_an_out_of_range_holdout_is_an_error(domains):
    domains(
        "widgets",
        evaluation=textwrap.dedent(
            """\
            tasks:
              - name: t
                generator: backbone_edges
                relation: BUILT_IN
                endpoint_label: Widget
            holdout: 1.5
            """
        ),
    )

    result = validate_pack("widgets")

    assert not result.ok
    assert any("holdout must be in [0, 1)" in e for e in result.errors)


def test_a_retrieval_intent_missing_its_cypher_is_an_error(domains):
    """retrieval.yaml was parsed by nothing until a question was asked, so every
    check in agent/contract.py existed and none of them ran at validation time."""
    domains(
        "widgets",
        retrieval=textwrap.dedent(
            """\
            intents:
              - name: built_in
                description: Which factory built this widget.
                entity: WIDGET
                match: ["built"]
            """
        ),
    )

    result = validate_pack("widgets")

    assert not result.ok
    assert any("missing 'cypher'" in e for e in result.errors)


def test_a_traversal_that_does_not_filter_on_pack_is_an_error(domains):
    """The quiet one. Neo4j Community has a single database, so packs share it and
    are separated by a `pack` property. A traversal that omits it answers out of
    whichever pack happens to hold a matching id — a plausible answer from the
    wrong graph."""
    domains(
        "widgets",
        retrieval=textwrap.dedent(
            """\
            intents:
              - name: built_in
                description: Which factory built this widget.
                entity: WIDGET
                cypher: |
                  MATCH (w:Widget)-[:BUILT_IN]->(f) WHERE w.id = $entity_id
                  RETURN f.id AS id LIMIT {limit}
            """
        ),
    )

    result = validate_pack("widgets")

    assert not result.ok
    assert any("does not filter on $pack" in e for e in result.errors)


def test_a_lookup_that_does_not_filter_on_pack_is_an_error(domains):
    """The lookup runs before any intent does, on every question, and was the one
    query in retrieval.yaml that nothing checked."""
    domains(
        "widgets",
        retrieval=textwrap.dedent(
            """\
            lookup: |
              MATCH (n) WHERE n.id = $needle RETURN n.id AS id LIMIT $limit

            intents:
              - name: built_in
                description: Which factory built this widget.
                entity: WIDGET
                cypher: |
                  MATCH (w:Widget {pack: $pack})-[:BUILT_IN]->(f {pack: $pack})
                  WHERE w.id = $entity_id
                  RETURN f.id AS id LIMIT {limit}
            """
        ),
    )

    result = validate_pack("widgets")

    assert not result.ok
    assert any("lookup" in e and "does not filter on $pack" in e for e in result.errors)


def test_an_intent_about_a_type_the_ontology_does_not_declare_is_an_error(domains):
    domains(
        "widgets",
        retrieval=textwrap.dedent(
            """\
            intents:
              - name: built_in
                description: Which factory built this widget.
                entity: GHOST
                cypher: |
                  MATCH (w:Widget {pack: $pack})-[:BUILT_IN]->(f {pack: $pack})
                  WHERE w.id = $entity_id
                  RETURN f.id AS id LIMIT {limit}
            """
        ),
    )

    result = validate_pack("widgets")

    assert not result.ok
    assert any("intent 'built_in' is about 'GHOST'" in e for e in result.errors)


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


def test_a_context_relation_the_ontology_does_not_declare_is_an_error(domains):
    """A context block follows an *extracted* edge. Naming a relation the
    ontology never declares means following one extraction cannot produce — it
    resolves nothing and looks exactly like a domain where the trick does not
    apply."""
    domains(
        "widgets",
        resolve=textwrap.dedent(
            """\
            resolve:
              - entity: WIDGET
                target: Widget
                id: "w:{name}"
                context: {via: TELEPATHY, from: FACTORY, id: "w:{source}/{name}"}
            """
        ),
    )

    result = validate_pack("widgets")

    assert not result.ok
    assert any("resolves through 'TELEPATHY'" in e for e in result.errors)


def test_a_context_source_the_ontology_does_not_declare_is_an_error(domains):
    domains(
        "widgets",
        resolve=textwrap.dedent(
            """\
            resolve:
              - entity: WIDGET
                target: Widget
                id: "w:{name}"
                context: {via: BUILT_IN, from: GHOST, id: "w:{source}/{name}"}
            """
        ),
    )

    result = validate_pack("widgets")

    assert not result.ok
    assert any("takes context from 'GHOST'" in e for e in result.errors)


def test_a_well_formed_context_block_passes(domains):
    domains(
        "widgets",
        resolve=textwrap.dedent(
            """\
            resolve:
              - entity: WIDGET
                target: Widget
                id: "w:{name}"
                context: {via: BUILT_IN, from: FACTORY, id: "w:{source}/{name}"}
            """
        ),
    )

    result = validate_pack("widgets")

    assert not [e for e in result.errors if "context" in e], result.errors

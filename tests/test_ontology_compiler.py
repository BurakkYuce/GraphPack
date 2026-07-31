"""The TTL -> extraction schema compiler.

These tests pin down behaviour GraphPack inherits from the engine's OWL reader
and would otherwise only discover mid-ingest: names arrive upper-cased, only
explicitly typed OWL constructs are seen, and triple constraints stay out of the
dict handed to the extractor.
"""

from __future__ import annotations

import textwrap

import pytest

from graphpack.packs.ontology import OntologyError, compile_ontology

pytestmark = pytest.mark.unit


def test_compiles_entities_relations_and_properties(pack_dir):
    root = pack_dir("widgets")

    schema = compile_ontology(root / "ontology.ttl")

    # Local names reach the extractor upper-cased (OntologyManager._uri_to_name).
    assert schema.entities == ["WIDGET", "FACTORY"]
    assert schema.relations == ["BUILT_IN"]
    assert schema.properties == {"WIDGET": {"SERIAL": "string"}}


def test_triple_constraints_are_derived_but_kept_out_of_the_engine_schema(pack_dir):
    root = pack_dir("widgets")

    schema = compile_ontology(root / "ontology.ttl")

    assert schema.triple_constraints == [("WIDGET", "BUILT_IN", "FACTORY")]
    # The engine never forwards triple constraints to SchemaLLMPathExtractor, so
    # shipping them in this dict would imply an enforcement that does not happen.
    assert "validation_schema" not in schema.as_engine_schema()


def test_engine_schema_has_exactly_the_keys_the_engine_reads(pack_dir):
    root = pack_dir("widgets")

    engine_schema = compile_ontology(root / "ontology.ttl").as_engine_schema()

    assert set(engine_schema) == {
        "entities",
        "relations",
        "properties",
        "relation_properties",
    }


def test_relation_without_range_fans_out_across_every_entity(pack_dir):
    """An untyped range is why the validator insists on rdfs:range.

    The engine pairs such a relation with every entity type, so the constraint
    set stops being a constraint. Capturing it here means the validator's rule
    has a documented reason rather than being folklore.
    """
    ontology = textwrap.dedent(
        """\
        @prefix ex:   <http://example.org/test/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix owl:  <http://www.w3.org/2002/07/owl#> .

        ex:Widget a owl:Class .
        ex:Factory a owl:Class .
        ex:Crate a owl:Class .

        ex:loose a owl:ObjectProperty ;
            rdfs:domain ex:Widget .
        """
    )
    root = pack_dir("loose", ontology=ontology)

    schema = compile_ontology(root / "ontology.ttl")

    assert len(schema.triple_constraints) == len(schema.entities)


def test_rdfs_class_alone_is_invisible_to_the_engine(pack_dir):
    """The reader queries for `a owl:Class`; rdfs:Class alone yields nothing."""
    ontology = textwrap.dedent(
        """\
        @prefix ex:   <http://example.org/test/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

        ex:Widget a rdfs:Class .
        """
    )
    root = pack_dir("rdfs_only", ontology=ontology)

    with pytest.raises(OntologyError, match="no entity types"):
        compile_ontology(root / "ontology.ttl")


def test_relative_paths_are_rejected(pack_dir):
    """The engine resolves relative ontology paths against os.getcwd(), so a
    relative path here would silently depend on where the command was run."""
    pack_dir("widgets")

    with pytest.raises(OntologyError, match="must be absolute"):
        compile_ontology("domains/widgets/ontology.ttl")

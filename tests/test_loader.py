"""The pack -> engine bridge.

This is the load-bearing claim of the whole project: a pack directory becomes a
configured engine with no change to engine source. These tests exercise the real
``config.Settings``, so a change upstream that breaks the bridge fails here.
"""

from __future__ import annotations

import textwrap

import pytest

from graphpack.packs.contract import Pack
from graphpack.packs.loader import build_settings, clear_engine_env

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_engine_env(monkeypatch):
    """Keep the developer's own shell out of these assertions."""
    for name in (
        "VECTOR_DB_CONFIG",
        "GRAPH_DB_CONFIG",
        "QDRANT_VECTOR_DB_CONFIG",
        "NEO4J_GRAPH_DB_CONFIG",
        "SCHEMAS",
        "SCHEMA_NAME",
    ):
        monkeypatch.delenv(name, raising=False)


def test_compiled_ontology_reaches_the_engine_as_the_active_schema(pack_dir):
    """The end-to-end claim: TTL on disk -> Settings.get_active_schema()."""
    pack = Pack.from_dir(pack_dir("widgets"))

    settings = build_settings(pack)

    active = settings.get_active_schema()
    assert settings.schema_name == "widgets"
    assert active["entities"] == ["WIDGET", "FACTORY"]
    assert active["relations"] == ["BUILT_IN"]
    assert active["properties"] == {"WIDGET": {"SERIAL": "string"}}


def test_store_targets_are_fixed_and_scoped_to_the_pack(pack_dir):
    pack = Pack.from_dir(pack_dir("widgets"))

    settings = build_settings(pack)

    assert str(settings.pg_graph_db) == "neo4j"
    assert str(settings.vector_db) == "qdrant"
    assert settings.vector_db_config["collection_name"] == "widgets_chunks"
    # Neo4j Community has one database; packs share it and separate by property.
    assert settings.graph_db_config["database"] == "neo4j"


def test_ontology_singleton_path_stays_off(pack_dir):
    """USE_ONTOLOGY routes through a process-global OntologyManager, which cannot
    hold two packs at once. The compiled schema goes through `schemas` instead."""
    pack = Pack.from_dir(pack_dir("widgets"))

    assert build_settings(pack).use_ontology is False


def test_pack_knobs_reach_the_engine(pack_dir):
    pack = Pack.from_dir(
        pack_dir(
            "widgets",
            pack_yaml=(
                "name: widgets\nversion: 1.0.0\n"
                "extraction:\n"
                "  strict_schema: false\n"
                "  max_triplets_per_chunk: 7\n"
                "  chunk_size: 512\n"
                "  chunk_overlap: 64\n"
            ),
        )
    )

    settings = build_settings(pack)

    assert settings.strict_schema_validation is False
    assert settings.max_triplets_per_chunk == 7
    assert (settings.chunk_size, settings.chunk_overlap) == (512, 64)


def test_named_store_env_var_cannot_override_the_pack(pack_dir, monkeypatch):
    """`{TYPE}_VECTOR_DB_CONFIG` is applied inside Settings.__init__ *after*
    super().__init__, unconditionally — a leaked .env would otherwise redirect
    an ingest into the wrong collection without a word."""
    monkeypatch.setenv(
        "QDRANT_VECTOR_DB_CONFIG", '{"collection_name": "hijacked", "host": "elsewhere"}'
    )
    pack = Pack.from_dir(pack_dir("widgets"))

    settings = build_settings(pack)

    assert settings.vector_db_config["collection_name"] == "widgets_chunks"


def test_clear_engine_env_reports_what_it_removed(monkeypatch):
    monkeypatch.setenv("QDRANT_VECTOR_DB_CONFIG", "{}")
    monkeypatch.setenv("GRAPH_DB_CONFIG", "{}")

    removed = clear_engine_env()

    assert set(removed) == {"QDRANT_VECTOR_DB_CONFIG", "GRAPH_DB_CONFIG"}
    assert clear_engine_env() == []


def test_extraction_is_on_unless_a_pack_turns_it_off(pack_dir):
    pack = Pack.from_dir(pack_dir("widgets"))

    assert build_settings(pack).enable_knowledge_graph is True


def test_a_pack_whose_graph_is_metadata_can_skip_extraction(pack_dir):
    """`extract: false` has to reach the engine, not just the Pack object.

    A pack whose every edge comes from a structured field has nothing for a
    model to find, and running one over its corpus costs days of GPU to change
    no number. The corpus is still chunked and embedded — only extraction is
    skipped — so this asserts the one setting and not a whole disabled pipeline.
    """
    root = pack_dir(
        "metadata-only",
        pack_yaml=textwrap.dedent(
            """\
            name: metadata-only
            version: 1.0.0
            lang: en
            id_prefix: ex
            extraction:
              extract: false
              strict_schema: true
              max_triplets_per_chunk: 5
              chunk_size: 256
              chunk_overlap: 32
            stores:
              qdrant_collection: metadata_only_chunks
            """
        ),
    )
    pack = Pack.from_dir(root)

    settings = build_settings(pack)

    assert pack.extract is False
    assert settings.enable_knowledge_graph is False
    # Still indexed for search: skipping extraction is not skipping the corpus.
    assert str(settings.vector_db) == "qdrant"


def test_the_extractor_is_given_the_pack_s_own_triple_constraints(pack_dir):
    """Without this the extractor validates against LlamaIndex's
    DEFAULT_VALIDATION_SCHEMA — a PRODUCT / MARKET / TECHNOLOGY example — and
    `strict=True` discards every triple a real pack produces. Turkish case law
    was being filtered against a schema about consumer products, and the run
    reported success while writing nothing."""
    from graphpack.packs.loader import enforce_triple_constraints
    from graphpack.packs.ontology import compile_ontology

    root = pack_dir("widgets")
    schema = compile_ontology(root / "ontology.ttl")

    class FakeExtractor:
        kg_validation_schema = {"relationships": [("PRODUCT", "USED_BY", "PRODUCT")]}

    class FakeManager:
        def create_extractor(self, *args, **kwargs):
            return FakeExtractor()

    class FakeSystem:
        schema_manager = FakeManager()

    system = FakeSystem()
    enforce_triple_constraints(system, schema)

    extractor = system.schema_manager.create_extractor()
    assert extractor.kg_validation_schema == {"relationships": [("WIDGET", "BUILT_IN", "FACTORY")]}


def test_an_extractor_without_a_validation_schema_is_left_alone(pack_dir):
    """The dynamic extractor has no such field and wants none."""
    from graphpack.packs.loader import enforce_triple_constraints
    from graphpack.packs.ontology import compile_ontology

    schema = compile_ontology(pack_dir("widgets") / "ontology.ttl")

    class Dynamic:
        pass

    class FakeManager:
        def create_extractor(self, *a, **k):
            return Dynamic()

    class FakeSystem:
        schema_manager = FakeManager()

    system = FakeSystem()
    enforce_triple_constraints(system, schema)

    assert not hasattr(system.schema_manager.create_extractor(), "kg_validation_schema")

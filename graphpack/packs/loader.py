"""Turn a :class:`~graphpack.packs.contract.Pack` into a live engine instance.

This is the whole pack -> engine bridge.  It touches no engine source: the
engine's ``Settings`` is a ``pydantic_settings.BaseSettings`` whose constructor
kwargs outrank the environment, and ``HybridSearchSystem.from_settings`` accepts
an instance, so one pack becomes one ``Settings`` becomes one system.

Two engine behaviours are actively worked around:

* ``{TYPE}_VECTOR_DB_CONFIG`` / ``{TYPE}_GRAPH_DB_CONFIG`` are read inside
  ``Settings.__init__`` *after* ``super().__init__``, unconditionally — they
  overwrite whatever we passed programmatically.  :func:`clear_engine_env`
  removes them so the pack, not a stray shell variable, decides where data goes.
* ``Settings`` loads ``.env`` relative to the working directory.  GraphPack
  commands must run from the GraphPack repo root; running them from the engine
  checkout would silently import the engine's own configuration.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from graphpack.models import extractor_type_for, properties_supported, tune_llm
from graphpack.packs.contract import Pack
from graphpack.packs.ontology import CompiledSchema, compile_ontology

logger = logging.getLogger(__name__)

#: Fixed store targets. GraphPack's generality claim is about domains, not
#: databases, so the engine's 15 graph / 10 vector backends are deliberately
#: narrowed to one of each.
PG_GRAPH_DB = "neo4j"
VECTOR_DB = "qdrant"
SEARCH_DB = "bm25"

#: Environment variables that override programmatic store config inside
#: ``Settings.__init__``.  ``{TYPE}_`` prefixes are added for the stores we use.
_OVERRIDING_ENV = (
    "VECTOR_DB_CONFIG",
    "GRAPH_DB_CONFIG",
    f"{VECTOR_DB.upper()}_VECTOR_DB_CONFIG",
    f"{PG_GRAPH_DB.upper()}_GRAPH_DB_CONFIG",
)


def clear_engine_env() -> list[str]:
    """Drop engine env vars that would outrank the pack's own configuration.

    Returns the names that were actually removed so callers can log the fact —
    a silently ignored pack setting is the kind of bug that costs an afternoon.
    """
    removed = [name for name in _OVERRIDING_ENV if os.environ.pop(name, None) is not None]
    if removed:
        logger.warning(
            "Ignoring engine environment overrides %s — pack configuration wins",
            ", ".join(removed),
        )
    return removed


def neo4j_config() -> dict[str, Any]:
    """Neo4j connection for the single shared instance.

    Neo4j Community supports exactly one database, so packs share it and are
    separated by a ``pack`` property on every node rather than by database.
    """
    return {
        "url": os.getenv("GRAPHPACK_NEO4J_URI", "bolt://localhost:7687"),
        "username": os.getenv("GRAPHPACK_NEO4J_USER", "neo4j"),
        "password": os.getenv("GRAPHPACK_NEO4J_PASSWORD", "password"),
        "database": "neo4j",
    }


def qdrant_config(collection: str) -> dict[str, Any]:
    """Qdrant connection with a per-pack collection."""
    return {
        "host": os.getenv("GRAPHPACK_QDRANT_HOST", "localhost"),
        "port": int(os.getenv("GRAPHPACK_QDRANT_PORT", "6333")),
        "api_key": os.getenv("GRAPHPACK_QDRANT_API_KEY"),
        "collection_name": collection,
        "https": False,
    }


def compile_pack_schema(pack: Pack) -> CompiledSchema:
    """Compile the pack's ontology into the engine's extraction schema."""
    return compile_ontology(pack.ontology_path)


def build_settings(pack: Pack, schema: CompiledSchema | None = None):
    """Build the engine ``Settings`` for *pack*.

    ``use_ontology`` stays False on purpose: that engine path reads a
    process-global ``OntologyManager`` and cannot hold two packs at once.  The
    ontology is compiled into a named ``schemas`` entry instead, which lives on
    this ``Settings`` object alone.
    """
    from config import Settings  # engine module, flat top-level name

    clear_engine_env()
    schema = schema or compile_pack_schema(pack)

    # Which extractor works, and whether it can carry properties, depends on the
    # provider rather than on the pack. See graphpack/models.py for what was
    # measured and why.
    provider = pack.llm_provider or os.getenv("LLM_PROVIDER") or ""
    kwargs: dict[str, Any] = {
        # Extraction schema, compiled from the pack ontology.
        "schema_name": pack.name,
        "schemas": [{"name": pack.name, "schema": schema.as_engine_schema()}],
        "use_ontology": False,
        "kg_extractor_type": extractor_type_for(provider),
        "disable_properties": not properties_supported(provider),
        "strict_schema_validation": pack.strict_schema,
        "max_triplets_per_chunk": pack.max_triplets_per_chunk,
        "enable_knowledge_graph": True,
        # Stores.
        "pg_graph_db": PG_GRAPH_DB,
        "vector_db": VECTOR_DB,
        "search_db": SEARCH_DB,
        "rdf_graph_db": "none",
        "graph_db_config": neo4j_config(),
        "vector_db_config": qdrant_config(pack.qdrant_collection),
        # Chunking.
        "chunk_size": pack.chunk_size,
        "chunk_overlap": pack.chunk_overlap,
    }

    # Model selection is optional: when a pack says nothing, the engine derives
    # its LLM and embedding configuration from the environment as usual.
    if pack.llm_provider:
        kwargs["llm_provider"] = pack.llm_provider
    if pack.llm_config:
        kwargs["llm_config"] = dict(pack.llm_config)
    if pack.embedding_kind:
        kwargs["embedding_kind"] = pack.embedding_kind
    if pack.embedding_model:
        kwargs["embedding_model"] = pack.embedding_model
    if pack.embedding_dimension:
        kwargs["embedding_dimension"] = pack.embedding_dimension

    settings = Settings(**kwargs)
    _log_effective_config(pack, settings)
    return settings


def build_system(pack: Pack, schema: CompiledSchema | None = None):
    """Instantiate the engine for *pack*.

    One system per process: ``HybridSearchSystem.__init__`` assigns the global
    ``llama_index.core.Settings`` (llm, embed_model, chunk_size), so two packs
    cannot be live in the same interpreter at the same time.
    """
    from hybrid_system import HybridSearchSystem  # engine module

    settings = build_settings(pack, schema)
    system = HybridSearchSystem.from_settings(settings)
    # Adjust the LLM the engine just built. The engine assigns the same object
    # to LlamaIndex's global Settings, so this reaches every consumer of it.
    tune_llm(system.llm, settings.llm_provider)
    return system


def _log_effective_config(pack: Pack, settings) -> None:
    """Report where this run will actually write.

    The engine has several config precedence layers; printing the resolved
    values makes a leaked ``.env`` visible immediately instead of after an
    ingest lands in the wrong collection.
    """
    logger.info(
        "pack=%s v%s -> neo4j=%s db=%s | qdrant=%s | schema=%s strict=%s",
        pack.name,
        pack.version,
        settings.graph_db_config.get("url"),
        settings.graph_db_config.get("database"),
        settings.vector_db_config.get("collection_name"),
        settings.schema_name,
        settings.strict_schema_validation,
    )

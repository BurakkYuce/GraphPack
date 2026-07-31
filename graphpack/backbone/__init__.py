"""LLM-free path: structured sources straight into Neo4j.

The engine has no route for this — its ingest pipeline always goes through
chunking and extraction — so the backbone talks to the Neo4j driver directly.
"""

from graphpack.backbone.fetch import FetchError, fetch_all, write_manifest
from graphpack.backbone.load import LoadError, LoadReport, ensure_constraints, load_backbone
from graphpack.backbone.neo4j_client import driver_from_env, session_scope
from graphpack.backbone.sources import Sources, SourcesError, load_sources

__all__ = [
    "FetchError",
    "LoadError",
    "LoadReport",
    "Sources",
    "SourcesError",
    "driver_from_env",
    "ensure_constraints",
    "fetch_all",
    "load_backbone",
    "load_sources",
    "session_scope",
    "write_manifest",
]

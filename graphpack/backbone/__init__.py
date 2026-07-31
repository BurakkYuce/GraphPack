"""LLM-free path: structured sources straight into Neo4j.

The engine has no route for this — its ingest pipeline always goes through
chunking and extraction — so the backbone talks to the Neo4j driver directly.
"""

from graphpack.backbone.neo4j_client import driver_from_env, session_scope

__all__ = ["driver_from_env", "session_scope"]

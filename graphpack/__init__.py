"""GraphPack — declarative domain packs on top of the flexible-graphrag engine.

Layering rule (enforced in CI):
    graphpack/  never imports domains/  and never hard-codes a pack name.
    domains/    contains no Python — only TTL, YAML, CSV and JSONL.

The engine is installed as an editable sibling checkout and exposes *flat*
top-level modules (``config``, ``main``, ``backend``, ``hybrid_system``,
``ingest``, ``process``, ``sources``, ``rdf`` …).  Everything we write therefore
lives under this single ``graphpack`` package so nothing shadows it.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]

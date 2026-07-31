"""Filesystem anchors for the GraphPack repository.

Kept in one place because two engine behaviours make path handling load-bearing:

* ``rdf.ontology_manager.ontology_path_anchor()`` resolves *relative* ontology
  paths against ``os.getcwd()``.  We therefore always hand the engine absolute
  paths.
* ``config.Settings`` reads ``.env`` relative to the current working directory,
  so GraphPack commands must run from the GraphPack repo root, never from the
  engine checkout.
"""

from __future__ import annotations

import os
from pathlib import Path

# graphpack/paths.py -> graphpack/ -> <repo root>
REPO_ROOT: Path = Path(__file__).resolve().parents[1]


def domains_root() -> Path:
    """Directory holding the declarative packs.

    Override with ``GRAPHPACK_DOMAINS`` when running from an installed wheel or
    when pointing at an alternative pack collection (benchmarks, experiments).
    """
    override = os.getenv("GRAPHPACK_DOMAINS")
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / "domains"


def snapshots_root() -> Path:
    """Where ``neo4j-admin dump`` snapshots are kept (our only rollback path)."""
    return REPO_ROOT / "snapshots"

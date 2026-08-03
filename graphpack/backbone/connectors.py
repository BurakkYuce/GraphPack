"""Named data sources, borrowed from the engine.

The engine ships readers for fourteen systems and selects between them with one
configuration value. GraphPack's ``fetch`` block could only make HTTP requests,
which meant a pack over SharePoint or S3 was not expressible as configuration —
the exact thing this project claims should never happen.

**They integrate at ``fetch``, not at ``corpus``.** A connector hands back
LlamaIndex ``Document`` objects, and it is tempting to pass those straight to the
engine. That would throw away the contract: ``id`` templates, ``where`` filters,
``hide_from_model`` and ``metadata`` all operate on *rows*, and the ``id``
template in particular is load-bearing — it is the key ``document_edges`` joins
the graph to the corpus on.

So a connector's documents become rows, ``{"text": ..., **metadata}``, written to
``out`` as JSONL like every other fetch step. Every later block works unchanged,
``data/MANIFEST.txt`` still records what a run produced, and ``--force`` still
means the same thing.

Nothing new is installed. These readers are core dependencies of the engine, not
optional extras, so a pack can name one on a machine that already runs GraphPack.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


class ConnectorError(Exception):
    """Raised when a pack names a connector wrongly."""


#: Connector name -> (engine class, configuration keys it cannot work without).
#:
#: The required keys are what makes a misconfigured pack fail at
#: ``packs validate`` rather than at the end of a fetch. They are deliberately
#: minimal: a reader's optional knobs are its own business and pass straight
#: through, but a SharePoint step with no site is not a step.
CONNECTORS: dict[str, tuple[str, tuple[str, ...]]] = {
    "alfresco": ("AlfrescoSource", ()),
    "azure_blob": ("AzureBlobSource", ("container_name",)),
    "box": ("BoxSource", ()),
    "cmis": ("CmisSource", ()),
    "filesystem": ("FileSystemSource", ("paths",)),
    "gcs": ("GCSSource", ("bucket",)),
    "google_drive": ("GoogleDriveSource", ()),
    "onedrive": ("OneDriveSource", ()),
    "s3": ("S3Source", ("bucket",)),
    "sharepoint": ("SharePointSource", ()),
    "web": ("WebSource", ()),
    "wikipedia": ("WikipediaSource", ("query",)),
    "youtube": ("YouTubeSource", ()),
}

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def check_connector(name: str, config: dict[str, Any], where: str) -> None:
    """Reject a connector a pack cannot actually run. Raises ``ConnectorError``.

    Static: no network, no credentials, no import of the reader itself — this
    runs inside ``packs validate``, which is the command a pack author uses
    before anything is reachable.
    """
    if name not in CONNECTORS:
        raise ConnectorError(
            f"{where}: unknown source '{name}'. Available: {', '.join(sorted(CONNECTORS))}"
        )
    missing = [key for key in CONNECTORS[name][1] if not config.get(key)]
    if missing:
        raise ConnectorError(f"{where}: source '{name}' needs config key(s) {missing}")


def expand(config: dict[str, Any]) -> dict[str, Any]:
    """Substitute ``${VAR}`` from the environment, one level deep.

    The same bargain as ``fetch`` headers: a pack names the variable, the
    machine holds the value, and nothing secret is committed. A variable that is
    not set expands to empty rather than raising, so the reader reports its own
    "no credentials" error — which is more use than ours would be.
    """

    def _one(value: Any) -> Any:
        if isinstance(value, str):
            return _ENV.sub(lambda m: os.environ.get(m.group(1), ""), value)
        if isinstance(value, list):
            return [_one(v) for v in value]
        return value

    return {key: _one(value) for key, value in config.items()}


def fetch_rows(name: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Run a connector and return its documents as rows.

    One row per document: the text under ``text``, and the document's own
    metadata spread alongside it, so a pack's templates address them the same
    way they address the fields of a JSON API response.

    Metadata keys are used as-is rather than renamed. What a reader calls its
    fields is part of that reader's contract, and inventing a translation layer
    would mean a pack author reading ours instead of theirs.
    """
    if name not in CONNECTORS:
        raise ConnectorError(f"unknown source '{name}'")
    class_name = CONNECTORS[name][0]

    try:
        import sources as engine_sources  # engine module
    except ImportError as exc:  # pragma: no cover — the engine is a hard dependency
        raise ConnectorError(f"the engine's data sources are not importable — {exc}") from exc

    # `get_documents_with_progress`, not `get_documents`, and this is not about
    # progress. On the filesystem source the plain method returns the right
    # number of documents with **empty text** — it finds the files and never
    # runs the parser. The async one does the extraction. Measured on three
    # files: 3 documents, 0 characters each, against 3 documents and 61, 42 and
    # 60 characters. Nothing raises either way, which is the whole problem.
    from graphpack.loop import run

    try:
        source = getattr(engine_sources, class_name)(expand(config))
        _, documents = run(source.get_documents_with_progress())
    except ConnectorError:
        raise
    except Exception as exc:
        raise ConnectorError(f"source '{name}' failed — {type(exc).__name__}: {exc}") from exc

    rows = [{"text": document.text, **(document.metadata or {})} for document in documents or []]
    logger.info("source '%s': %d document(s) -> %d row(s)", name, len(documents or []), len(rows))
    return rows

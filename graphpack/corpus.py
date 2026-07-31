"""Turn a pack's fetched records into documents for the engine's ingest pipeline.

This is the entire corpus bridge. The engine already accepts a list of
pre-loaded documents — ``ingest.ingest_from_source.ingest_source_documents`` runs
the full chunk, embed, index and extract pipeline over them — so a pack's
unstructured half needs no engine change either.

Every document carries ``metadata["pack"]``. Knowledge-graph extraction copies a
source node's metadata onto the entities it extracts, which is what lets a later
pass tell which pack an ``__Entity__`` node came from. Neo4j Community offers a
single database, so that tag is the only separation there is.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from graphpack.backbone.fetch import read_jsonl
from graphpack.backbone.normalize import render, render_parts
from graphpack.backbone.rows import passes
from graphpack.backbone.sources import CorpusSpec, Sources

logger = logging.getLogger(__name__)

#: Metadata key carrying the pack name. Also the property the resolution pass
#: looks for on extracted entities.
PACK_KEY = "pack"


class CorpusError(Exception):
    """Raised when a pack's corpus block cannot be turned into documents."""


def build_documents(
    pack_name: str,
    sources: Sources,
    data_dir: Path,
    limit: int | None = None,
) -> list:
    """Build the pack's documents, in declaration order.

    Returns LlamaIndex ``Document`` objects, ready for
    ``HybridSearchSystem._ingest_source_documents``.
    """
    from llama_index.core import Document  # engine dependency

    documents = []
    for spec in sources.corpus:
        source_path = data_dir / spec.source
        if not source_path.exists():
            raise CorpusError(
                f"{spec.describes}: '{spec.source}' is missing — run `graphpack backbone fetch "
                f"{pack_name}` first"
            )
        for record in _records(pack_name, spec, sources, source_path):
            documents.append(
                Document(
                    text=record["text"],
                    doc_id=record["id"],
                    metadata=record["metadata"],
                )
            )
            if limit is not None and len(documents) >= limit:
                logger.info("Stopping at the requested limit of %d documents", limit)
                return documents

    logger.info("Built %d document(s) for pack '%s'", len(documents), pack_name)
    return documents


def _records(
    pack_name: str,
    spec: CorpusSpec,
    sources: Sources,
    source_path: Path,
) -> Iterator[dict[str, Any]]:
    """Render one JSONL file into document records."""
    produced = skipped = 0

    for row in read_jsonl(source_path):
        if not passes(row, spec.where):
            skipped += 1
            continue

        doc_id, id_complete = render_parts(spec.id, row, sources.pipelines)
        doc_id = doc_id.strip()
        text = render(spec.text, row, sources.pipelines).strip()
        # An id built from a row missing one of its fields is worse than no id:
        # "gh:psf/requests#" is non-empty and collides with every other
        # numberless row from that repository. And text that renders to nothing
        # costs an embedding to say nothing.
        if not doc_id or not id_complete or not text:
            skipped += 1
            continue

        metadata = {
            name: value
            for name, template in spec.metadata.items()
            if (value := render(template, row, sources.pipelines).strip())
        }
        metadata[PACK_KEY] = pack_name

        yield {"id": doc_id, "text": text, "metadata": metadata}
        produced += 1
        if spec.limit is not None and produced >= spec.limit:
            break

    logger.info("%s: %d document(s), %d row(s) skipped", spec.describes, produced, skipped)

"""Turn a pack's fetched records into documents for the engine's ingest pipeline.

This is the entire corpus bridge. The engine already accepts a list of
pre-loaded documents — ``ingest.ingest_from_source.ingest_source_documents`` runs
the full chunk, embed, index and extract pipeline over them — so a pack's
unstructured half needs no engine change either.

Every document carries ``metadata["pack"]``. Knowledge-graph extraction copies a
source node's metadata onto the entities it extracts, which is what lets a later
pass tell which pack an ``__Entity__`` node came from. Neo4j Community offers a
single database, so that tag is the only separation there is.

That tag is bookkeeping, and the model must not see it. LlamaIndex prepends
metadata to a node's text as ``key: value`` lines before sending it anywhere, so
without the exclusions below the extractor reads ``pack: oss`` as part of the
document — and duly extracted an entity called "pack: oss", typed PACKAGE, from
a thread about botocore. Confirmed rather than guessed: no chunk's ``text``
contains the string, and ``MetadataMode.LLM`` renders it.
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
    sample: int | None = None,
    seed: int = 0,
) -> list:
    """Build the pack's documents.

    Returns LlamaIndex ``Document`` objects, ready for
    ``HybridSearchSystem._ingest_source_documents``.

    ``limit`` takes the first N in file order. That order is not arbitrary and
    it is not random: records arrive grouped by whatever the fetch iterated
    over, so the first 200 documents of this corpus come from eight
    repositories out of a hundred and fourteen. Useful for a smoke test,
    useless as a measurement.

    ``sample`` takes N at random, seeded so the same call selects the same
    documents. Extraction is expensive enough that a run is rarely repeated;
    what is measured on one subset should be reproducible on the same subset.
    """
    from llama_index.core import Document  # engine dependency

    records: list[dict[str, Any]] = []
    for spec in sources.corpus:
        source_path = data_dir / spec.source
        if not source_path.exists():
            raise CorpusError(
                f"{spec.describes}: '{spec.source}' is missing — run `graphpack backbone fetch "
                f"{pack_name}` first"
            )
        for record in _records(pack_name, spec, sources, source_path):
            records.append(record)
            if sample is None and limit is not None and len(records) >= limit:
                logger.info("Stopping at the requested limit of %d documents", limit)
                break

    if sample is not None and sample < len(records):
        import random

        chosen = random.Random(seed).sample(range(len(records)), sample)
        # Keep file order among the chosen so a run reads the same way twice.
        records = [records[i] for i in sorted(chosen)]
        logger.info("Sampled %d document(s) with seed %d", len(records), seed)

    documents = [
        Document(
            text=r["text"],
            doc_id=r["id"],
            metadata=r["metadata"],
            # Hidden from the model and from the embedding, kept on the node for
            # the graph store. A pack may hide more of its own metadata; the
            # pack tag is hidden always, because it is GraphPack's bookkeeping
            # rather than anything the corpus said.
            excluded_llm_metadata_keys=r["hidden"],
            excluded_embed_metadata_keys=r["hidden"],
        )
        for r in records
    ]
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

        # The pack tag always, plus whatever the pack calls bookkeeping. Only
        # keys actually present are listed, so a spec naming a field that never
        # rendered does not carry a phantom exclusion.
        hidden = [PACK_KEY] + [k for k in spec.hide_from_model if k in metadata]

        yield {"id": doc_id, "text": text, "metadata": metadata, "hidden": hidden}
        produced += 1
        if spec.limit is not None and produced >= spec.limit:
            break

    logger.info("%s: %d document(s), %d row(s) skipped", spec.describes, produced, skipped)

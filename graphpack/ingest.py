"""Run a pack's documents through the engine.

The engine does the work: chunking, embedding, vector and full-text indexing,
and ontology-guided extraction with the schema compiled from the pack. GraphPack
supplies the documents and the configuration, and touches no engine source.

Extraction is the slow part — minutes per hundred documents on a local model —
so this reports progress and can be limited to a slice while a pack's ontology
is still being tuned.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from graphpack.corpus import build_documents
from graphpack.packs.contract import Pack

logger = logging.getLogger(__name__)


class IngestError(Exception):
    """Raised when an ingest cannot start or does not finish."""


@dataclass
class IngestReport:
    pack: str
    documents: int
    seconds: float
    entities_before: int
    entities_after: int

    @property
    def entities_added(self) -> int:
        return self.entities_after - self.entities_before


def ingest_pack(
    pack: Pack,
    limit: int | None = None,
    sample: int | None = None,
    seed: int = 0,
    skip_graph: bool = False,
) -> IngestReport:
    """Ingest *pack*'s corpus.

    ``skip_graph`` runs everything except extraction, which is the cheap way to
    check that documents, chunking and embeddings are right before committing
    hours to the LLM.
    """
    from graphpack.backbone import load_sources, session_scope
    from graphpack.packs.loader import build_system

    sources = load_sources(pack.path("sources.yaml"))
    if not sources.corpus:
        raise IngestError(f"{pack.name} declares no corpus steps")

    documents = build_documents(
        pack.name, sources, pack.data_dir, limit=limit, sample=sample, seed=seed
    )
    if not documents:
        raise IngestError(
            f"{pack.name}: no documents — run `graphpack backbone fetch {pack.name}` first"
        )

    with session_scope() as session:
        before = _entity_count(session)

    logger.info(
        "Ingesting %d document(s) for '%s'%s",
        len(documents),
        pack.name,
        " without extraction" if skip_graph else "",
    )
    system = build_system(pack)

    start = time.time()
    try:
        asyncio.run(system._ingest_source_documents(documents, skip_graph=skip_graph))
    except Exception as exc:
        raise IngestError(f"{pack.name}: ingest failed — {exc}") from exc
    duration = time.time() - start

    with session_scope() as session:
        after = _entity_count(session)

    report = IngestReport(
        pack=pack.name,
        documents=len(documents),
        seconds=duration,
        entities_before=before,
        entities_after=after,
    )
    logger.info(
        "Ingested %d document(s) in %.1fs — %d extracted entities added",
        report.documents,
        report.seconds,
        report.entities_added,
    )
    return report


def _entity_count(session) -> int:
    """Entities the engine has written. Counted before and after so the report
    says what this run produced rather than what the database happens to hold."""
    record = session.run("MATCH (e:__Entity__) RETURN count(e) AS n").single()
    return int(record["n"]) if record else 0

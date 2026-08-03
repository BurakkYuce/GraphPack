"""How much of the benchmark's evidence survives chunking at all.

A chunk-level score counts a chunk as relevant when it *contains* one of the
query's evidence sentences. If chunking split that sentence across a boundary,
no retriever can ever score it — the miss belongs to the splitter, and reading
it as a retrieval result is wrong in the direction that flatters nothing and
misleads everything.

This was written twice as a throwaway script and refuted a hypothesis both
times. At 1,024 tokens every one of 981 evidence sentences survives whole, which
killed the idea that chunk boundaries explained the gap to the published table.
At 256 tokens — the paper's own size — 86 of them do not, which was part of why
matching the paper made the score worse rather than better. A measurement with
that record should not keep being retyped.

It reads the vector store rather than the graph, because that is where the
chunks are: a pack with ``extract: false`` writes no ``:Chunk`` nodes at all.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Payload keys a chunk's text might live under, in the order worth trying.
#: LlamaIndex writes ``_node_content`` as a JSON blob; other writers use plain
#: text. Guessing here rather than pinning one keeps this working if the engine
#: changes its store adapter, and an empty result is loud (see ``measure``).
_TEXT_KEYS = ("text", "_node_content", "content")


@dataclass(frozen=True)
class Ceiling:
    """What fraction of the evidence a perfect retriever could still find."""

    chunks: int
    facts: int
    present: int

    @property
    def ratio(self) -> float:
        return self.present / self.facts if self.facts else 0.0

    def line(self) -> str:
        return (
            f"{self.present:,} of {self.facts:,} evidence sentence(s) survive chunking whole "
            f"in {self.chunks:,} chunk(s) — ceiling on evidence recall {self.ratio:.1%}"
        )


def normalise(text: str) -> str:
    """Collapse whitespace and case, for containment matching.

    The same normalisation the chunk-level scorer uses, and for the same reason:
    evidence is quoted verbatim from the articles, so it is present — but
    chunking and any prepended metadata leave the whitespace different, and
    matching raw strings loses real hits to a newline.
    """
    return re.sub(r"\s+", " ", text).strip().lower()


def _chunk_texts(collection: str) -> list[str]:
    from graphpack.reset import _qdrant_client

    client = _qdrant_client()
    texts: list[str] = []
    try:
        if not client.collection_exists(collection):
            return []
        offset = None
        while True:
            points, offset = client.scroll(
                collection, limit=2000, offset=offset, with_payload=True, with_vectors=False
            )
            for point in points:
                payload = point.payload or {}
                for key in _TEXT_KEYS:
                    value = payload.get(key)
                    if not isinstance(value, str) or len(value) < 20:
                        continue
                    if key == "_node_content":
                        try:
                            value = json.loads(value).get("text", "")
                        except (ValueError, AttributeError):
                            pass
                    if value:
                        texts.append(value)
                    break
            if offset is None:
                break
    finally:
        client.close()
    return texts


def measure(collection: str, facts: set[str]) -> Ceiling:
    """Containment ceiling over a pack's chunked corpus.

    Returns zeros when the collection is empty rather than raising, so a caller
    can report "nothing ingested" instead of a traceback — but a corpus that is
    present and yields no text is logged, because that means the payload key
    changed and every ceiling after it would silently read 0%.
    """
    texts = _chunk_texts(collection)
    if not texts:
        logger.warning(
            "No chunk text read from '%s'. Either nothing is ingested, or the store's "
            "payload keys are not %s — the second would make this report 0%% for a "
            "corpus that is fine.",
            collection,
            list(_TEXT_KEYS),
        )
    haystack = [normalise(t) for t in texts]
    wanted = {normalise(f) for f in facts if f.strip()}
    present = sum(1 for f in wanted if any(f in h for h in haystack))
    return Ceiling(chunks=len(haystack), facts=len(wanted), present=present)

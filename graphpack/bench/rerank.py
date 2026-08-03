"""Cross-encoder re-ordering of retrieved passages.

The engine has none — `rerank`, `cross_encoder` and `colbert` match nothing in
its source — so this is GraphPack's, and it is the one place in the project that
changes what retrieval returns rather than measuring it.

The bi-encoder that fills the index scores a question and a passage separately
and compares two vectors, which is what makes an index possible: passages are
embedded once, in advance. A cross-encoder scores the pair together, so it reads
the question *while* reading the passage, and it cannot be indexed — every
(question, passage) pair is a forward pass. That is why it runs over twenty
results rather than nine thousand, and why it is worth the cost only after
something cheaper has narrowed the field.

Two properties this module keeps deliberately:

**It re-orders, it does not retrieve.** A passage the retriever never returned
cannot be promoted, so the ceiling belongs to the leg underneath. `fetch_factor`
widens that leg, and the widening is reported rather than hidden — a reranked
run is "60 retrieved, cut to 20", not "20 retrieved, better".

**It is inert until asked twice.** A pack declaring `rerank:` changes nothing;
only `--rerank` applies it. Every number in `docs/RESULTS.md` was measured
without one, and a default that quietly re-ordered results would invalidate all
of them without a single test going red.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Loaded models, by name. A cross-encoder is ~1.3 GB and several seconds to
#: construct; a benchmark asks for the same one 2,556 times.
_MODELS: dict[str, Any] = {}


class RerankError(Exception):
    """Raised when reranking was asked for and cannot be done."""


def load_reranker(model: str):
    """Construct the cross-encoder, or say plainly what is missing.

    ``sentence-transformers`` is an optional dependency: it pulls a large model
    stack that nothing else here needs, and every published number was measured
    without it. A missing install is a setup message, not a traceback.
    """
    if model in _MODELS:
        return _MODELS[model]

    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RerankError(
            "reranking needs sentence-transformers, which is an optional extra "
            "because nothing else in GraphPack uses it — install it with "
            "`uv pip install -e '.[rerank]'`"
        ) from exc

    device = _device()
    logger.info("loading reranker %s on %s (first call downloads ~1.3 GB)", model, device)
    try:
        _MODELS[model] = CrossEncoder(model, device=device)
    except Exception as exc:  # noqa: BLE001 — a bad model name is a setup error
        raise RerankError(f"could not load reranker '{model}' — {exc}") from exc
    return _MODELS[model]


def _device() -> str:
    """Where to run the cross-encoder.

    Not cosmetic. Measured on this machine, 60 chunks of ~1,000 tokens: 87.4s on
    CPU against 22.8s on Apple's MPS backend. Over a 2,255-query benchmark that
    is the difference between 55 hours and 14 — the gap between a measurement
    that gets made and one that gets described as "too slow to run".

    ``GRAPHPACK_RERANK_DEVICE`` overrides, because a machine where MPS is
    present and broken should not need a code change to be usable.
    """
    import os

    if forced := os.getenv("GRAPHPACK_RERANK_DEVICE"):
        return forced
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001 — no torch, or a backend that will not probe
        pass
    return "cpu"


def rerank_nodes(nodes: list, question: str, model: str, top_n: int | None = None) -> list:
    """Re-order LlamaIndex nodes by cross-encoder relevance, best first.

    Empty input returns empty rather than loading a 1.3 GB model to sort nothing
    — a benchmark hits that on every query whose retrieval failed.

    The nodes are returned as they came in, only re-ordered. Their scores are
    left alone: a node's ``score`` is the retriever's, on the retriever's scale,
    and overwriting it with a cross-encoder logit would make two runs' score
    columns silently incomparable.
    """
    if not nodes:
        return []

    encoder = load_reranker(model)
    pairs = [(question, node.node.get_content() or "") for node in nodes]
    try:
        scores = encoder.predict(pairs)
    except Exception as exc:  # noqa: BLE001 — one query is not the whole run
        logger.warning("rerank failed for %r — %s; keeping retrieval order", question[:60], exc)
        return nodes[:top_n] if top_n else nodes

    order = sorted(range(len(nodes)), key=lambda i: float(scores[i]), reverse=True)
    ranked = [nodes[i] for i in order]
    return ranked[:top_n] if top_n else ranked

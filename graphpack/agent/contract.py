"""The ``retrieval.yaml`` contract: what a pack knows how to be asked.

An intent pairs a way of asking with a way of answering. The question "what
breaks if urllib3 breaks" and "hangi kararlar 4857'ye atıf yapıyor" are the same
shape of request — find an entity, walk outward from it — and differ only in
which edges to walk. That difference is a Cypher template, which belongs to the
pack.

Routing is deterministic when it can be. A pack lists the words its questions
actually use, and matching them costs nothing and is testable. Only when no
intent matches does the model get asked to choose, because a routing step that
calls an LLM on every question is both the slowest part of the loop and the one
that fails least predictably.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: Placeholders a template may use. Only these two, and only because Cypher
#: refuses a variable-length bound or a LIMIT as a parameter. Everything else —
#: the pack, the entity — is a real ``$parameter`` that the driver escapes.
#:
#: Interpolation is done by exact replacement of ``{hops}`` and ``{limit}``
#: rather than ``str.format``. Cypher map literals look like format fields:
#: ``{pack: $pack}`` is read as a field named ``pack`` with the format spec
#: ``" $pack"``, and the query dies with a message about format specifiers. That
#: mistake has now been made twice in this codebase, which is why the
#: interpolation lives in one function.
TEMPLATE_PLACEHOLDERS = ("hops", "limit")


def render_cypher(cypher: str, hops: int, limit: int) -> str:
    """Fill a template's two placeholders. See TEMPLATE_PLACEHOLDERS."""
    return cypher.replace("{hops}", str(hops)).replace("{limit}", str(limit))


class RetrievalError(Exception):
    """Raised when ``retrieval.yaml`` is malformed."""


@dataclass(frozen=True)
class Intent:
    """One way of asking, and the traversal that answers it."""

    name: str
    #: What this answers, in a sentence. Shown to the model when keyword
    #: matching does not settle it, so it is prose rather than a label.
    description: str
    #: Extraction label of the entity the question is about.
    entity: str
    #: Cypher returning ``id`` and optionally ``name``. ``$pack`` and
    #: ``$entity_id`` are bound; ``{hops}`` and ``{limit}`` are interpolated.
    cypher: str
    #: Words and phrases that mean this intent. Lower-cased, matched on word
    #: boundaries where the alphabet allows.
    match: tuple[str, ...] = ()
    hops: int = 2
    limit: int = 25

    def score(self, question: str) -> int:
        """How many of this intent's phrases the question uses."""
        lowered = question.lower()
        return sum(1 for phrase in self.match if phrase.lower() in lowered)


@dataclass(frozen=True)
class Rerank:
    """A cross-encoder re-ordering of what the retriever returned.

    Off unless a pack asks for it, and off even then unless a run asks for it —
    every number this project has published was measured without one, and a
    default that silently changed retrieval would invalidate all of them at
    once. ``graphpack bench --rerank`` is the only thing that turns it on.

    The over-fetch is the part worth stating: a reranker cannot promote a
    passage the retriever never returned, so scoring *k* reranked results means
    retrieving ``k * fetch_factor`` first.

    That widening is not a second variable, and the reason is worth being exact
    about. The retriever returns its 60 in score order, so the first 20 of them
    *are* the plain top-20 — over-fetching cannot improve the result by itself.
    All it does is give the cross-encoder candidates it may promote past that
    cut, which is the effect being measured. (The one caveat is that Qdrant's
    search is approximate, so a k=60 query and a k=20 query need not agree on
    the top 20 down to the last item.)
    """

    model: str = "BAAI/bge-reranker-large"
    #: How many results survive. ``None`` keeps the run's own depth.
    top_n: int | None = None
    #: Retrieve this multiple of the scored depth before re-ordering.
    fetch_factor: int = 3

    def candidates_for(self, top_k: int) -> int:
        return max(top_k, top_k * self.fetch_factor)


@dataclass(frozen=True)
class RetrievalRules:
    intents: tuple[Intent, ...]
    #: Cypher for turning a name into candidate entities, when the agent has to
    #: find what the question is about before it can traverse.
    lookup: str = ""
    #: Present when the pack declares a `rerank:` block. Never applied unless a
    #: run asks for it.
    rerank: Rerank | None = None

    def by_name(self, name: str) -> Intent | None:
        for intent in self.intents:
            if intent.name == name:
                return intent
        return None

    def route(self, question: str) -> tuple[Intent | None, int]:
        """Best keyword match and its score. Zero means nothing matched."""
        if not self.intents:
            return None, 0
        best = max(self.intents, key=lambda i: i.score(question))
        return (best, best.score(question)) if best.score(question) else (None, 0)


def load_retrieval_rules(path: Path) -> RetrievalRules:
    """Parse a pack's ``retrieval.yaml``."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RetrievalError(f"{path}: invalid YAML — {exc}") from exc
    if not isinstance(raw, dict):
        raise RetrievalError(f"{path}: top level must be a mapping")

    entries = raw.get("intents") or []
    if not isinstance(entries, list) or not entries:
        raise RetrievalError(f"{path}: 'intents' must be a non-empty list")

    intents = tuple(_parse_intent(item, path, index) for index, item in enumerate(entries))
    names = [i.name for i in intents]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise RetrievalError(f"{path}: duplicate intent name(s) {sorted(duplicates)}")

    # The lookup runs on every question before any intent does, and it was the
    # one query in this file nothing checked. A lookup missing `$pack` reads
    # whichever pack happens to share the identifier — the same hazard the intent
    # check exists for, on the query that runs most often.
    lookup = str(raw.get("lookup") or "").strip()
    if lookup:
        _check_cypher(lookup, f"{path}: lookup")

    return RetrievalRules(
        intents=intents, lookup=lookup, rerank=_parse_rerank(raw.get("rerank"), path)
    )


def _parse_rerank(item: Any, path: Path) -> Rerank | None:
    """Parse an optional ``rerank:`` block.

    A missing block and an empty one both mean "this pack declares no
    reranker", which is different from a block with bad values — those are
    rejected here rather than at the end of a benchmark run.
    """
    if item is None:
        return None
    if not isinstance(item, dict):
        raise RetrievalError(f"{path}: 'rerank' must be a mapping")
    if not item:
        return None

    where = f"{path}: rerank"
    unknown = set(item) - {"model", "top_n", "fetch_factor"}
    if unknown:
        raise RetrievalError(f"{where}: unknown key(s) {sorted(unknown)}")

    model = str(item.get("model") or "").strip()
    if not model:
        raise RetrievalError(f"{where} is missing 'model'")

    def _positive(key: str, default: int | None) -> int | None:
        value = item.get(key, default)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise RetrievalError(f"{where}: '{key}' must be a positive integer")
        return value

    return Rerank(
        model=model,
        top_n=_positive("top_n", None),
        fetch_factor=_positive("fetch_factor", 3) or 3,
    )


def _parse_intent(item: Any, path: Path, index: int) -> Intent:
    where = f"{path}: intents[{index}]"
    if not isinstance(item, dict):
        raise RetrievalError(f"{where} must be a mapping")
    for required in ("name", "description", "entity", "cypher"):
        if not item.get(required):
            raise RetrievalError(f"{where} is missing '{required}'")

    cypher = str(item["cypher"])
    _check_cypher(cypher, where)

    match = item.get("match") or []
    if not isinstance(match, list):
        raise RetrievalError(f"{where}: 'match' must be a list of phrases")

    return Intent(
        name=str(item["name"]),
        description=str(item["description"]),
        entity=str(item["entity"]),
        cypher=cypher,
        match=tuple(str(m) for m in match),
        hops=int(item.get("hops", 2)),
        limit=int(item.get("limit", 25)),
    )


def _check_cypher(cypher: str, where: str) -> None:
    """Reject a template that cannot work, before it is run once per question.

    Interpolation is limited to the three things Cypher refuses as parameters.
    Anything else with braces is either a mistake or an attempt to build a query
    out of user input, and neither should reach the database.
    """
    # Only a closing brace immediately after the name is a placeholder. A map
    # literal writes `{pack: $pack}`, which this deliberately does not match.
    unknown = {name for name in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", cypher)} - set(
        TEMPLATE_PLACEHOLDERS
    )
    if unknown:
        raise RetrievalError(
            f"{where}: cypher uses {sorted(unknown)} as interpolated placeholder(s). "
            f"Only {', '.join(TEMPLATE_PLACEHOLDERS)} are interpolated; everything else must be "
            "a $parameter so the driver escapes it."
        )
    if "$pack" not in cypher:
        raise RetrievalError(
            f"{where}: cypher does not filter on $pack, so it would read another pack's graph"
        )
    if " id " not in f" {cypher.lower()} " and "as id" not in cypher.lower():
        raise RetrievalError(
            f"{where}: cypher must RETURN an 'id' column — the answer cites canonical "
            "identifiers, and a row without one cannot be checked against the graph"
        )

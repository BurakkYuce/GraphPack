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
class RetrievalRules:
    intents: tuple[Intent, ...]
    #: Cypher for turning a name into candidate entities, when the agent has to
    #: find what the question is about before it can traverse.
    lookup: str = ""

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

    return RetrievalRules(intents=intents, lookup=str(raw.get("lookup") or "").strip())


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

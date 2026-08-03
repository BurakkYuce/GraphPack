"""The ``resolve.yaml`` contract: how a pack turns mentions into identifiers.

One rule per extracted entity type. Each names the backbone label it resolves
against, an identifier template, and the methods to try in order. Methods are
ordered by precision — an exact match is worth more than a fuzzy one — and the
first that answers wins, so a rule reads as a fallback chain.

Everything is data. The code knows what "fuzzy" means; only the pack knows that
a Python package name is lowercase with dashes.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from graphpack.backbone.normalize import (
    NormalizeError,
    Pipeline,
    build_pipelines,
    referenced_pipelines,
)

#: Methods a rule may name, in the order of precision they are usually chained.
METHODS = ("exact", "alias", "fuzzy")

#: What to do with a mention no method resolved.
UNRESOLVED_POLICIES = ("drop", "provisional")


class ResolveError(Exception):
    """Raised when ``resolve.yaml`` is malformed or names something unknown."""


@dataclass(frozen=True)
class Context:
    """Resolve a mention through a relation extraction itself claimed.

    Some mentions identify nothing alone. "371. maddesinde" is an article
    number, and 371 of *which statute* is the whole question — the backbone
    writes ``madde:6100/371`` and no amount of matching gets there from the
    number by itself.

    The naive fix is to attach it to whichever statute was mentioned nearby, and
    the tr-law pack refuses that in writing: it would manufacture citations, and
    manufactured citations are exactly what the evaluation exists to catch.

    This is the other thing. The ontology declares ``STATUTE HAS_ARTICLE
    ARTICLE``, extraction produces those edges, and each one is the *model's own
    claim* about which statute an article belongs to. Following it is using
    evidence rather than guessing from proximity, and a wrong edge is a wrong
    extraction — which the evaluation already measures.

    Measured on tr-law before this existed: 546 extracted ``HAS_ARTICLE`` edges,
    371 of them from a statute that resolved to an article that did not, and 282
    of those build an identifier the backbone actually holds. The other 89 build
    one it does not and are dropped rather than mislinked.
    """

    #: Extracted relation to follow, pointing *at* the unresolved mention.
    via: str
    #: Extraction label the other end must carry.
    source: str
    #: Identifier template. ``{source}`` is the canonical id the other end
    #: resolved to; the mention's own text is ``{name}`` and ``{text}``.
    id: str


@dataclass(frozen=True)
class Rule:
    """How one extracted entity type resolves against the backbone."""

    #: Extraction label, as the ontology spells it — upper case.
    entity: str
    #: Backbone label to resolve against.
    target: str
    #: Identifier template applied to the mention, e.g. ``"pypi:{name|pypi_name}"``.
    #: The mention's text is available as ``{name}`` and as ``{text}``.
    id: str
    methods: tuple[str, ...] = ("exact",)
    #: What fuzzy matching compares. Applied to the mention *and* to each
    #: backbone name, so both sides are shaped the same way. Without it the
    #: comparison runs between an identifier and a bare name, and a shared
    #: prefix like ``pypi:`` inflates every score — Jaro-Winkler rewards common
    #: prefixes specifically.
    match: str = ""
    #: Minimum similarity, 0-100. Below this a fuzzy candidate is refused.
    fuzzy_threshold: int = 90
    #: Backbone property holding the human-readable name.
    fuzzy_field: str = "name"
    on_unresolved: str = "drop"
    #: A second pass for what the methods above could not identify alone.
    context: Context | None = None

    @property
    def keeps_unresolved(self) -> bool:
        return self.on_unresolved == "provisional"


@dataclass(frozen=True)
class ResolveRules:
    """A parsed ``resolve.yaml``, with its alias table loaded."""

    rules: tuple[Rule, ...]
    pipelines: dict[str, Pipeline]
    #: Surface form (already normalised) -> canonical identifier.
    aliases: dict[str, str] = field(default_factory=dict)

    def for_entity(self, label: str) -> Rule | None:
        for rule in self.rules:
            if rule.entity == label:
                return rule
        return None

    @property
    def entity_labels(self) -> list[str]:
        return [rule.entity for rule in self.rules]

    def declaration_index(self, label: str) -> int:
        """Where a type sits in the pack's own ordering.

        The tie-break when one mention carries several types. Declaration order
        is the only ordering a pack author controls — Neo4j's label order is
        arbitrary, and resolution used to follow it.
        """
        for index, rule in enumerate(self.rules):
            if rule.entity == label:
                return index
        return len(self.rules)


def load_rules(path: Path, aliases_path: Path | None = None) -> ResolveRules:
    """Parse a pack's ``resolve.yaml`` and its alias table."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ResolveError(f"{path}: invalid YAML — {exc}") from exc
    if not isinstance(raw, dict):
        raise ResolveError(f"{path}: top level must be a mapping")

    try:
        pipelines = build_pipelines(raw.get("normalize"))
    except NormalizeError as exc:
        raise ResolveError(f"{path}: {exc}") from exc

    entries = raw.get("resolve") or []
    if not isinstance(entries, list):
        raise ResolveError(f"{path}: 'resolve' must be a list")

    rules = tuple(_parse_rule(item, path, index) for index, item in enumerate(entries))
    _check_consistency(rules, pipelines, path)

    aliases = _load_aliases(aliases_path, pipelines, rules) if aliases_path else {}
    return ResolveRules(rules=rules, pipelines=pipelines, aliases=aliases)


def _parse_rule(item: Any, path: Path, index: int) -> Rule:
    where = f"{path}: resolve[{index}]"
    if not isinstance(item, dict):
        raise ResolveError(f"{where} must be a mapping")
    for required in ("entity", "target", "id"):
        if not item.get(required):
            raise ResolveError(f"{where} is missing '{required}'")

    methods = tuple(str(m) for m in (item.get("methods") or ["exact"]))
    unknown = [m for m in methods if m not in METHODS]
    if unknown:
        raise ResolveError(f"{where}: unknown method(s) {unknown}; available: {', '.join(METHODS)}")
    if not methods:
        raise ResolveError(f"{where}: 'methods' cannot be empty")

    policy = str(item.get("on_unresolved", "drop"))
    if policy not in UNRESOLVED_POLICIES:
        raise ResolveError(
            f"{where}: on_unresolved must be one of {', '.join(UNRESOLVED_POLICIES)}, "
            f"got '{policy}'"
        )

    threshold = int(item.get("fuzzy_threshold", 90))
    if not 0 <= threshold <= 100:
        raise ResolveError(f"{where}: fuzzy_threshold must be between 0 and 100")

    match = str(item.get("match") or "")
    if "fuzzy" in methods:
        if not match:
            raise ResolveError(
                f"{where}: 'fuzzy' needs a 'match' template. Comparing the identifier template "
                "against bare names measures the wrong thing, and a shared prefix inflates "
                "every score."
            )
        if threshold < 70:
            # Below about 70, similarity on short identifiers stops meaning
            # anything: `idna` and `ida` already score in the 80s.
            raise ResolveError(
                f"{where}: fuzzy_threshold {threshold} is too low to be meaningful on short names"
            )

    return Rule(
        entity=str(item["entity"]),
        target=str(item["target"]),
        id=str(item["id"]),
        methods=methods,
        match=match,
        fuzzy_threshold=threshold,
        fuzzy_field=str(item.get("fuzzy_field", "name")),
        on_unresolved=policy,
        context=_parse_context(item.get("context"), where),
    )


def _parse_context(raw: Any, where: str) -> Context | None:
    """Parse a rule's optional ``context:`` block. See ``Context``."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ResolveError(f"{where}: 'context' must be a mapping")

    missing = [key for key in ("via", "from", "id") if not raw.get(key)]
    if missing:
        raise ResolveError(f"{where}: context is missing {missing}")
    unknown = sorted(set(raw) - {"via", "from", "id"})
    if unknown:
        raise ResolveError(f"{where}: context has unknown key(s) {unknown}")

    template = str(raw["id"])
    if not re.search(r"\{source(\|[^}]*)?\}", template):
        raise ResolveError(
            f"{where}: the context id template must use '{{source}}' — the canonical id the "
            "other end resolved to is the whole reason to follow the relation. Without it "
            "this is the same identifier the methods above already failed on."
        )
    return Context(via=str(raw["via"]), source=str(raw["from"]), id=template)


def _check_consistency(rules: tuple[Rule, ...], pipelines: dict[str, Pipeline], path: Path) -> None:
    known = set(pipelines)
    for rule in rules:
        referenced = referenced_pipelines(rule.id) | referenced_pipelines(rule.match)
        if rule.context:
            referenced |= referenced_pipelines(rule.context.id)
        missing = referenced - known
        if missing:
            raise ResolveError(
                f"{path}: rule for {rule.entity} references undefined "
                f"normalize pipeline(s) {sorted(missing)}"
            )

    entities = [rule.entity for rule in rules]
    duplicates = {e for e in entities if entities.count(e) > 1}
    if duplicates:
        raise ResolveError(
            f"{path}: more than one rule for {sorted(duplicates)} — only the first would apply"
        )


def _load_aliases(
    path: Path, pipelines: dict[str, Pipeline], rules: tuple[Rule, ...]
) -> dict[str, str]:
    """Read ``aliases.csv``: ``surface,canonical_id`` with a header row.

    Surface forms are normalised on the way in, using the pipeline of the rule
    for their entity type, so a lookup at resolution time is a plain dictionary
    hit rather than a second round of normalising.
    """
    if not path.is_file():
        return {}

    by_entity = {rule.entity: rule for rule in rules}
    aliases: dict[str, str] = {}

    # The table is maintained by hand and the reasons an entry exists matter as
    # much as the entry, so `#` lines are comments. csv.DictReader has no notion
    # of those and would read them as rows.
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        return {}

    reader = csv.DictReader(lines)
    missing = {"entity", "surface", "id"} - set(reader.fieldnames or [])
    if missing:
        raise ResolveError(
            f"{path}: missing column(s) {sorted(missing)}; expected entity,surface,id"
        )

    for number, row in enumerate(reader, start=2):
        entity = (row.get("entity") or "").strip()
        surface = (row.get("surface") or "").strip()
        canonical = (row.get("id") or "").strip()
        if not surface or not canonical:
            continue
        rule = by_entity.get(entity)
        if rule is None:
            raise ResolveError(
                f"{path} row {number}: no resolve rule for entity '{entity}' "
                f"(rules exist for {', '.join(sorted(by_entity)) or 'nothing'})"
            )
        aliases[_alias_key(entity, surface, rule, pipelines)] = canonical

    return aliases


def _alias_key(entity: str, surface: str, rule: Rule, pipelines: dict[str, Pipeline]) -> str:
    """Normalise an alias the same way a mention will be, so the two can meet."""
    from graphpack.resolve.methods import alias_key, apply

    return alias_key(entity, apply(rule.match or rule.id, surface, pipelines))

"""The ``sources.yaml`` contract: how a pack turns structured data into a graph.

Two blocks, deliberately separate:

``fetch``  where the raw records come from — one HTTP shape, no knowledge of any
           particular API. Runs once; the output is gitignored and fingerprinted
           in ``MANIFEST.txt``.
``load``   how those records become nodes and edges. Ids are templates, so the
           identifier scheme is a pack decision rather than a convention buried
           in code.

The whole file is data. Nothing here knows what a package, a court decision or
a citation is.
"""

from __future__ import annotations

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


class SourcesError(Exception):
    """Raised when ``sources.yaml`` is malformed or self-inconsistent."""


@dataclass(frozen=True)
class FetchSpec:
    """One HTTP acquisition step.

    Either a single request (``url`` with no placeholders) or one request per
    row of an earlier step's output (``for_each``), with the row's fields
    substituted into the URL.
    """

    id: str
    url: str
    out: str
    select: str | None = None
    limit: int | None = None
    for_each: str | None = None
    keep: dict[str, str] = field(default_factory=dict)
    #: Request headers. Values may contain ``${VAR}``, expanded from the
    #: environment at request time so credentials stay out of the pack.
    headers: dict[str, str] = field(default_factory=dict)
    #: Walk an offset/limit API: ``{parameter, step, pages}``. The URL carries a
    #: placeholder named after ``parameter``, which is substituted with 0, step,
    #: 2*step … until ``pages`` requests have been made or a page comes back
    #: empty. Almost every bulk API is shaped this way and none of them will
    #: hand over a thousand rows in one response.
    paginate: dict[str, Any] = field(default_factory=dict)
    concurrency: int = 8
    on_error: str = "fail"

    @property
    def skips_errors(self) -> bool:
        return self.on_error == "skip"

    @property
    def page_parameter(self) -> str:
        return str(self.paginate.get("parameter", "offset"))

    @property
    def page_step(self) -> int:
        return int(self.paginate.get("step", 100))

    @property
    def page_limit(self) -> int:
        return int(self.paginate.get("pages", 0))


@dataclass(frozen=True)
class DeriveSpec:
    """A JSONL-to-JSONL transformation. No network.

    Fetching is not the only way to get a record set: the interesting one is
    often already implied by an earlier response. Deriving the repository list
    from the package records, rather than re-requesting it, keeps the two in
    step and costs nothing.
    """

    id: str
    source: str
    out: str
    fields: dict[str, str]
    #: Field path, or ``{field, pattern}`` to explode a text field by regex.
    explode: str | dict[str, str] | None = None
    where: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Output fields that must render non-empty; a row missing any is dropped.
    require: tuple[str, ...] = ()
    #: Field whose value identifies a row. Later duplicates are discarded.
    unique: str | None = None
    limit: int | None = None


@dataclass(frozen=True)
class NodeSpec:
    label: str
    id: str
    properties: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EdgeSpec:
    type: str
    start: str
    end: str
    properties: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadSpec:
    """One pass over one JSONL file, producing nodes or edges.

    ``explode`` turns a list-valued field into one row per element, exposed to
    templates as ``{value}``; that is how a package's dependency list becomes a
    set of edges. ``where`` drops rows before rendering.
    """

    source: str
    node: NodeSpec | None = None
    edge: EdgeSpec | None = None
    #: Field path, or ``{field, pattern}`` to explode a text field by regex.
    explode: str | dict[str, str] | None = None
    where: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Take the most common value for each property instead of the last one.
    #: A property read out of prose is read many times and not always well: the
    #: correct title of statute 6356 is in the corpus, appears first, and was
    #: then overwritten by a sentence fragment from a later decision. Ordinary
    #: MERGE is last-write-wins, which lets the worst reading win. Where a value
    #: is recovered from repeated mentions rather than stated once, the reading
    #: most decisions agree on is the one to keep.
    vote: bool = False

    @property
    def describes(self) -> str:
        return f"node:{self.node.label}" if self.node else f"edge:{self.edge.type}"


@dataclass(frozen=True)
class CorpusSpec:
    """One JSONL file becoming documents for the engine's ingest pipeline.

    The unstructured half of a pack. Where ``load`` produces the graph directly,
    this produces text that the engine chunks, embeds and extracts from — and
    whose extracted entities are later resolved against what ``load`` built.
    """

    source: str
    id: str
    text: str
    metadata: dict[str, str] = field(default_factory=dict)
    #: Metadata the model should not read. LlamaIndex prepends every metadata
    #: key to the text as a ``key: value`` line, so a field kept for the graph —
    #: a URL, a state flag — is also a line the extractor tries to find entities
    #: in. The pack tag is hidden without being listed; these are the pack's own.
    hide_from_model: tuple[str, ...] = ()
    where: dict[str, dict[str, str]] = field(default_factory=dict)
    limit: int | None = None

    @property
    def describes(self) -> str:
        return f"corpus:{self.source}"


@dataclass(frozen=True)
class Sources:
    """A parsed ``sources.yaml``."""

    fetch: tuple[FetchSpec, ...]
    derive: tuple[DeriveSpec, ...]
    load: tuple[LoadSpec, ...]
    corpus: tuple[CorpusSpec, ...]
    pipelines: dict[str, Pipeline]

    @property
    def produced_files(self) -> set[str]:
        """Every JSONL a run of this pack writes."""
        return {spec.out for spec in self.fetch} | {spec.out for spec in self.derive}

    @property
    def node_labels(self) -> list[str]:
        """Labels this pack writes.

        The loader derives the uniqueness constraints from these, which is why
        no pack needs a migration file of its own.
        """
        seen: list[str] = []
        for spec in self.load:
            if spec.node and spec.node.label not in seen:
                seen.append(spec.node.label)
        return seen


# ----------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------


def load_sources(path: Path) -> Sources:
    """Parse and validate a pack's ``sources.yaml``."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SourcesError(f"{path}: invalid YAML — {exc}") from exc
    if not isinstance(raw, dict):
        raise SourcesError(f"{path}: top level must be a mapping")

    try:
        pipelines = build_pipelines(raw.get("normalize"))
    except NormalizeError as exc:
        raise SourcesError(f"{path}: {exc}") from exc

    sources = Sources(
        fetch=tuple(_parse_fetch(item, path, i) for i, item in enumerate(_seq(raw, "fetch", path))),
        derive=tuple(
            _parse_derive(item, path, i) for i, item in enumerate(_seq(raw, "derive", path))
        ),
        load=tuple(_parse_load(item, path, i) for i, item in enumerate(_seq(raw, "load", path))),
        corpus=tuple(
            _parse_corpus(item, path, i) for i, item in enumerate(_seq(raw, "corpus", path))
        ),
        pipelines=pipelines,
    )
    _check_consistency(sources, path)
    return sources


def _seq(raw: dict[str, Any], key: str, path: Path) -> list[Any]:
    value = raw.get(key) or []
    if not isinstance(value, list):
        raise SourcesError(f"{path}: '{key}' must be a list")
    return value


def _parse_fetch(item: Any, path: Path, index: int) -> FetchSpec:
    where = f"{path}: fetch[{index}]"
    if not isinstance(item, dict):
        raise SourcesError(f"{where} must be a mapping")
    for required in ("id", "url", "out"):
        if not item.get(required):
            raise SourcesError(f"{where} is missing '{required}'")

    on_error = str(item.get("on_error", "fail"))
    if on_error not in ("fail", "skip"):
        raise SourcesError(f"{where}: on_error must be 'fail' or 'skip', got '{on_error}'")

    keep = item.get("keep") or {}
    if not isinstance(keep, dict):
        raise SourcesError(f"{where}: 'keep' must be a mapping of output field to source path")

    paginate = item.get("paginate") or {}
    if paginate:
        if not isinstance(paginate, dict):
            raise SourcesError(f"{where}: 'paginate' must be a mapping")
        unknown = set(paginate) - {"parameter", "step", "pages"}
        if unknown:
            raise SourcesError(
                f"{where}: paginate has unknown key(s) {sorted(unknown)}; "
                "supported: parameter, step, pages"
            )
        if int(paginate.get("pages", 0)) < 1:
            raise SourcesError(f"{where}: paginate needs 'pages' of at least 1")
        parameter = str(paginate.get("parameter", "offset"))
        if "{" + parameter + "}" not in str(item["url"]):
            raise SourcesError(
                f"{where}: the url has no '{{{parameter}}}' placeholder for paginate to fill, "
                "so every page would request the same rows"
            )
        if item.get("for_each"):
            raise SourcesError(
                f"{where}: 'paginate' and 'for_each' both decide what to request; use one"
            )

    return FetchSpec(
        id=str(item["id"]),
        url=str(item["url"]),
        out=str(item["out"]),
        select=_opt_str(item.get("select")),
        limit=_opt_int(item.get("limit")),
        for_each=_opt_str(item.get("for_each")),
        keep={str(k): str(v) for k, v in keep.items()},
        headers=_str_map(item.get("headers"), f"{where}: headers"),
        paginate=dict(paginate),
        concurrency=int(item.get("concurrency", 8)),
        on_error=on_error,
    )


def _parse_derive(item: Any, path: Path, index: int) -> DeriveSpec:
    where = f"{path}: derive[{index}]"
    if not isinstance(item, dict):
        raise SourcesError(f"{where} must be a mapping")
    for required in ("id", "source", "out", "fields"):
        if not item.get(required):
            raise SourcesError(f"{where} is missing '{required}'")

    fields = _str_map(item.get("fields"), f"{where}: fields")
    require = tuple(str(name) for name in (item.get("require") or []))
    unknown = set(require) - set(fields)
    if unknown:
        raise SourcesError(
            f"{where}: 'require' names field(s) {sorted(unknown)} that this step does not produce"
        )
    unique = _opt_str(item.get("unique"))
    if unique and unique not in fields:
        raise SourcesError(
            f"{where}: 'unique' names field '{unique}' that this step does not produce"
        )

    return DeriveSpec(
        id=str(item["id"]),
        source=str(item["source"]),
        out=str(item["out"]),
        fields=fields,
        explode=_explode(item.get("explode"), where),
        where=_conditions(item.get("where"), where),
        require=require,
        unique=unique,
        limit=_opt_int(item.get("limit")),
    )


def _parse_corpus(item: Any, path: Path, index: int) -> CorpusSpec:
    where = f"{path}: corpus[{index}]"
    if not isinstance(item, dict):
        raise SourcesError(f"{where} must be a mapping")
    for required in ("source", "id", "text"):
        if not item.get(required):
            raise SourcesError(f"{where} is missing '{required}'")

    return CorpusSpec(
        source=str(item["source"]),
        id=str(item["id"]),
        text=str(item["text"]),
        metadata=_str_map(item.get("metadata"), f"{where}: metadata"),
        hide_from_model=tuple(str(k) for k in (item.get("hide_from_model") or ())),
        where=_conditions(item.get("where"), where),
        limit=_opt_int(item.get("limit")),
    )


def _parse_load(item: Any, path: Path, index: int) -> LoadSpec:
    where = f"{path}: load[{index}]"
    if not isinstance(item, dict):
        raise SourcesError(f"{where} must be a mapping")
    if not item.get("source"):
        raise SourcesError(f"{where} is missing 'source'")

    has_node, has_edge = "node" in item, "edge" in item
    if has_node == has_edge:
        raise SourcesError(f"{where} must declare exactly one of 'node' or 'edge'")

    node = edge = None
    if has_node:
        spec = item["node"]
        if not isinstance(spec, dict) or not spec.get("label") or not spec.get("id"):
            raise SourcesError(f"{where}: node needs 'label' and 'id'")
        node = NodeSpec(
            label=str(spec["label"]),
            id=str(spec["id"]),
            properties=_str_map(spec.get("properties"), f"{where}: node.properties"),
        )
    else:
        spec = item["edge"]
        if not isinstance(spec, dict):
            raise SourcesError(f"{where}: edge must be a mapping")
        for required in ("type", "from", "to"):
            if not spec.get(required):
                raise SourcesError(f"{where}: edge needs '{required}'")
        edge = EdgeSpec(
            # `from` is a Python keyword, so the dataclass calls these start/end.
            type=str(spec["type"]),
            start=str(spec["from"]),
            end=str(spec["to"]),
            properties=_str_map(spec.get("properties"), f"{where}: edge.properties"),
        )

    return LoadSpec(
        source=str(item["source"]),
        node=node,
        edge=edge,
        explode=_explode(item.get("explode"), where),
        where=_conditions(item.get("where"), where),
        vote=bool(item.get("vote", False)),
    )


def _explode(raw: Any, where: str) -> str | dict[str, str] | None:
    """Parse an ``explode:`` value — a field path, or a pattern over one.

    The pattern form is checked here rather than at run time: an unparseable
    regex would otherwise surface as a step that quietly produced no rows.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw.strip() or None
    if not isinstance(raw, dict):
        raise SourcesError(f"{where}: 'explode' must be a field name or a mapping")

    missing = {"field", "pattern"} - set(raw)
    if missing:
        raise SourcesError(f"{where}: explode by pattern needs {sorted(missing)}")
    unknown = set(raw) - {"field", "pattern"}
    if unknown:
        raise SourcesError(f"{where}: explode has unknown key(s) {sorted(unknown)}")

    import re as _re

    try:
        compiled = _re.compile(str(raw["pattern"]))
    except _re.error as exc:
        raise SourcesError(f"{where}: explode pattern is not a valid regex — {exc}") from exc
    if not compiled.groupindex:
        raise SourcesError(
            f"{where}: explode pattern has no named groups, so a match produces nothing "
            "for a template to refer to. Use (?P<name>...)."
        )
    return {"field": str(raw["field"]), "pattern": str(raw["pattern"])}


def _conditions(raw: Any, where: str) -> dict[str, dict[str, str]]:
    """Parse a ``where:`` block — row filters shared by derive, load and corpus."""
    conditions = raw or {}
    if not isinstance(conditions, dict):
        raise SourcesError(f"{where}: 'where' must be a mapping of field to condition")
    for field_name, condition in conditions.items():
        if not isinstance(condition, dict) or not condition:
            raise SourcesError(f"{where}: where.{field_name} must be a mapping")
        unknown = set(condition) - {"matches", "not_matches"}
        if unknown:
            raise SourcesError(
                f"{where}: where.{field_name} has unknown condition(s) {sorted(unknown)}; "
                "supported: matches, not_matches"
            )
    return {str(k): {str(ck): str(cv) for ck, cv in v.items()} for k, v in conditions.items()}


def _check_consistency(sources: Sources, path: Path) -> None:
    """Catch mistakes that would otherwise appear as an empty graph.

    A template naming a pipeline that does not exist, or a load step reading a
    file no fetch step writes, both produce a run that looks successful and
    loads nothing.
    """
    known_pipelines = set(sources.pipelines)
    for describes, templates in _all_templates(sources):
        for template in templates:
            missing = referenced_pipelines(template) - known_pipelines
            if missing:
                raise SourcesError(
                    f"{path}: step '{describes}' references undefined "
                    f"normalize pipeline(s) {sorted(missing)}"
                )

    # Walk the steps in the order they actually execute — each fetch, then any
    # derive that reads its output — so a pack whose issue fetch is declared
    # before the derive that builds its input list fails here rather than
    # halfway through a download.
    # A pack that declares no acquisition reads files placed by hand, and there
    # is nothing to check against. Once it declares any, every source must be
    # accounted for — including the first one, which an "is anything known yet"
    # guard would wave through.
    tracked = bool(sources.fetch or sources.derive)
    available: set[str] = set()
    for spec in sources.fetch:
        if spec.for_each:
            _require_source(spec.for_each, available, f"fetch step '{spec.id}'", path, tracked)
        available.add(spec.out)
        for derived in sources.derive:
            if derived.source == spec.out:
                available.add(derived.out)

    orphans = [d.id for d in sources.derive if d.out not in available]
    if orphans:
        raise SourcesError(f"{path}: derive step(s) {orphans} read a file no fetch step produces")

    for spec in sources.load:
        _require_source(spec.source, available, f"load step '{spec.describes}'", path, tracked)
    for spec in sources.corpus:
        _require_source(spec.source, available, f"corpus step '{spec.describes}'", path, tracked)

    ids = [spec.id for spec in sources.fetch] + [spec.id for spec in sources.derive]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise SourcesError(f"{path}: duplicate fetch/derive id(s) {sorted(duplicates)}")


def _all_templates(sources: Sources):
    for spec in sources.derive:
        yield f"derive:{spec.id}", list(spec.fields.values())
    for spec in sources.load:
        if spec.node:
            yield spec.describes, [spec.node.id, *spec.node.properties.values()]
        elif spec.edge:
            yield spec.describes, [spec.edge.start, spec.edge.end, *spec.edge.properties.values()]
    for spec in sources.corpus:
        yield spec.describes, [spec.id, spec.text, *spec.metadata.values()]


def _require_source(
    source: str, available: set[str], describes: str, path: Path, tracked: bool
) -> None:
    if tracked and source not in available:
        raise SourcesError(
            f"{path}: {describes} reads '{source}', which nothing before it produces "
            f"(available: {sorted(available)})"
        )


def _str_map(value: Any, where: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SourcesError(f"{where} must be a mapping")
    return {str(k): str(v) for k, v in value.items()}


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)

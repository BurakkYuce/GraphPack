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
    concurrency: int = 8
    on_error: str = "fail"

    @property
    def skips_errors(self) -> bool:
        return self.on_error == "skip"


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
    explode: str | None = None
    where: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def describes(self) -> str:
        return f"node:{self.node.label}" if self.node else f"edge:{self.edge.type}"


@dataclass(frozen=True)
class Sources:
    """A parsed ``sources.yaml``."""

    fetch: tuple[FetchSpec, ...]
    load: tuple[LoadSpec, ...]
    pipelines: dict[str, Pipeline]

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

    fetch = tuple(
        _parse_fetch(item, path, index) for index, item in enumerate(_seq(raw, "fetch", path))
    )
    load = tuple(
        _parse_load(item, path, index) for index, item in enumerate(_seq(raw, "load", path))
    )

    sources = Sources(fetch=fetch, load=load, pipelines=pipelines)
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

    return FetchSpec(
        id=str(item["id"]),
        url=str(item["url"]),
        out=str(item["out"]),
        select=_opt_str(item.get("select")),
        limit=_opt_int(item.get("limit")),
        for_each=_opt_str(item.get("for_each")),
        keep={str(k): str(v) for k, v in keep.items()},
        concurrency=int(item.get("concurrency", 8)),
        on_error=on_error,
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

    conditions = item.get("where") or {}
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

    return LoadSpec(
        source=str(item["source"]),
        node=node,
        edge=edge,
        explode=_opt_str(item.get("explode")),
        where={str(k): {str(ck): str(cv) for ck, cv in v.items()} for k, v in conditions.items()},
    )


def _check_consistency(sources: Sources, path: Path) -> None:
    """Catch mistakes that would otherwise appear as an empty graph.

    A template naming a pipeline that does not exist, or a load step reading a
    file no fetch step writes, both produce a run that looks successful and
    loads nothing.
    """
    known_pipelines = set(sources.pipelines)
    for spec in sources.load:
        templates = []
        if spec.node:
            templates = [spec.node.id, *spec.node.properties.values()]
        elif spec.edge:
            templates = [spec.edge.start, spec.edge.end, *spec.edge.properties.values()]
        for template in templates:
            missing = referenced_pipelines(template) - known_pipelines
            if missing:
                raise SourcesError(
                    f"{path}: load step '{spec.describes}' references undefined "
                    f"normalize pipeline(s) {sorted(missing)}"
                )

    produced = {spec.out for spec in sources.fetch}
    for spec in sources.load:
        if produced and spec.source not in produced:
            raise SourcesError(
                f"{path}: load step '{spec.describes}' reads '{spec.source}', "
                f"which no fetch step produces (produced: {sorted(produced)})"
            )

    fetch_ids = [spec.id for spec in sources.fetch]
    duplicates = {i for i in fetch_ids if fetch_ids.count(i) > 1}
    if duplicates:
        raise SourcesError(f"{path}: duplicate fetch id(s) {sorted(duplicates)}")


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

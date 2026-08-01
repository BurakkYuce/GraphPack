"""Row handling shared by derive, load and corpus steps.

All three read JSONL, optionally explode a collection field, filter, and render
templates. Keeping that in one place means a pack's ``where:`` clause behaves
identically wherever it appears.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, Protocol

from graphpack.backbone.normalize import field


class RowSpec(Protocol):
    """The part of a step's declaration that governs row selection."""

    explode: str | dict[str, str] | None
    where: dict[str, dict[str, str]]


def expand(rows: Iterator[dict[str, Any]], spec: RowSpec) -> Iterator[dict[str, Any]]:
    """Explode and filter *rows* according to *spec*.

    ``explode`` turns one record into several. Three shapes, because records
    hide their multiplicity in three places:

    * a **list** field yields one row per element as ``value`` — a package's
      dependency list becomes individual edges;
    * a **mapping** field yields one row per entry, with the entry's name as
      ``key`` — which is how a step searches every URL a record carries without
      the pack enumerating the key names publishers happen to use;
    * a **pattern** over a text field yields one row per match, with the whole
      match as ``value`` and each named group as a field of its own. That is the
      only way to get at a fact stated in prose: a decision does not carry a
      list of the statutes it relies on, it names them mid-sentence.
    """
    for row in rows:
        for candidate in _explode(row, spec.explode):
            if passes(candidate, spec.where):
                yield candidate


def _explode(row: dict[str, Any], explode: str | dict[str, str] | None) -> list[dict[str, Any]]:
    if not explode:
        return [row]
    if isinstance(explode, dict):
        return _explode_by_pattern(row, explode)

    values = field(row, explode)
    if isinstance(values, dict):
        return [{**row, "key": k, "value": v} for k, v in values.items()]
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    return [{**row, "value": value} for value in values]


def _explode_by_pattern(row: dict[str, Any], explode: dict[str, str]) -> list[dict[str, Any]]:
    """One row per regex match in a text field.

    Named groups become fields, so a pack writes what it is looking for once and
    then refers to the parts by name — ``(?P<statute>\\d{3,4})\\s*sayılı`` gives
    templates a ``{statute}`` to use. Overlapping matches are not sought: a
    citation appearing twice in a sentence is one citation.
    """
    text = field(row, explode["field"])
    if not text:
        return []

    pattern = _compiled(explode["pattern"])
    out = []
    for match in pattern.finditer(str(text)):
        groups = {name: value for name, value in (match.groupdict() or {}).items() if value}
        out.append({**row, **groups, "value": match.group(0)})
    return out


_CACHE: dict[str, re.Pattern[str]] = {}


def _compiled(pattern: str) -> re.Pattern[str]:
    """Compile once. A pattern runs against every record of a corpus."""
    if pattern not in _CACHE:
        _CACHE[pattern] = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    return _CACHE[pattern]


def passes(row: dict[str, Any], conditions: dict[str, dict[str, str]]) -> bool:
    """Apply a ``where:`` block. A missing field is tested as the empty string."""
    for name, condition in conditions.items():
        value = field(row, name)
        text = "" if value is None else str(value)
        if "matches" in condition and not re.search(condition["matches"], text):
            return False
        if "not_matches" in condition and re.search(condition["not_matches"], text):
            return False
    return True

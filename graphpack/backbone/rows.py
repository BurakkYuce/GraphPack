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

    explode: str | None
    where: dict[str, dict[str, str]]


def expand(rows: Iterator[dict[str, Any]], spec: RowSpec) -> Iterator[dict[str, Any]]:
    """Explode and filter *rows* according to *spec*.

    ``explode`` turns one record with a collection field into several:

    * a list yields one row per element as ``value`` — a package's dependency
      list becomes individual edges;
    * a mapping yields one row per entry, with the entry's name as ``key`` —
      which is how a step searches every URL a record carries without the pack
      enumerating the key names publishers actually use.
    """
    for row in rows:
        for candidate in _explode(row, spec.explode):
            if passes(candidate, spec.where):
                yield candidate


def _explode(row: dict[str, Any], path: str | None) -> list[dict[str, Any]]:
    if not path:
        return [row]
    values = field(row, path)
    if isinstance(values, dict):
        return [{**row, "key": k, "value": v} for k, v in values.items()]
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    return [{**row, "value": value} for value in values]


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

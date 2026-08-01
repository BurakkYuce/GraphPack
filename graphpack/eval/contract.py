"""The ``eval.yaml`` contract: what counts as ground truth for this pack.

A pack names a generator and the relation it applies to. The generators are
generic — one derives gold from edges the backbone already holds, and the
others will follow as the second pack needs them. What a package is, or a
citation, stays in the pack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Ways a pack can obtain ground truth without anyone annotating anything.
#:
#: ``backbone_edges``   the structured graph already states the relation, so any
#:                      document mentioning both endpoints has a gold edge.
#: ``regex_canonical``  the text states the relation in a form a pattern can
#:                      recognise — a citation, an identifier. Phase 5.
#: ``structured_field`` a field of the source record states it outright. Phase 5.
GENERATORS = ("backbone_edges", "regex_canonical", "structured_field")


class EvalError(Exception):
    """Raised when ``eval.yaml`` is malformed or names something unknown."""


@dataclass(frozen=True)
class Task:
    """One thing to measure."""

    name: str
    generator: str
    #: Extracted relation type being scored.
    relation: str
    #: Backbone relation the gold comes from. Usually the same name; they differ
    #: when a pack's ontology and its structured source disagree on wording.
    backbone_relation: str
    #: Backbone labels the endpoints must carry for a pair to count.
    endpoint_label: str
    #: Treat the relation as undirected when scoring. "A and B are related" is
    #: sometimes all a sentence says, and penalising a model for guessing the
    #: direction of a symmetric statement measures nothing.
    directed: bool = True

    @property
    def describes(self) -> str:
        return f"{self.name} ({self.generator}: {self.relation})"


@dataclass(frozen=True)
class EvalRules:
    tasks: tuple[Task, ...]
    #: Fraction of documents held out. Zero means score everything, which is
    #: what a run does when the rules themselves were never fitted to the data.
    holdout: float = 0.0
    seed: int = 0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def load_eval_rules(path: Path) -> EvalRules:
    """Parse a pack's ``eval.yaml``."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise EvalError(f"{path}: invalid YAML — {exc}") from exc
    if not isinstance(raw, dict):
        raise EvalError(f"{path}: top level must be a mapping")

    entries = raw.get("tasks") or []
    if not isinstance(entries, list) or not entries:
        raise EvalError(f"{path}: 'tasks' must be a non-empty list")

    tasks = tuple(_parse_task(item, path, index) for index, item in enumerate(entries))
    names = [t.name for t in tasks]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise EvalError(f"{path}: duplicate task name(s) {sorted(duplicates)}")

    holdout = float(raw.get("holdout", 0.0))
    if not 0.0 <= holdout < 1.0:
        raise EvalError(f"{path}: holdout must be in [0, 1), got {holdout}")

    return EvalRules(tasks=tasks, holdout=holdout, seed=int(raw.get("seed", 0)), raw=raw)


def _parse_task(item: Any, path: Path, index: int) -> Task:
    where = f"{path}: tasks[{index}]"
    if not isinstance(item, dict):
        raise EvalError(f"{where} must be a mapping")
    for required in ("name", "generator", "relation", "endpoint_label"):
        if not item.get(required):
            raise EvalError(f"{where} is missing '{required}'")

    generator = str(item["generator"])
    if generator not in GENERATORS:
        raise EvalError(
            f"{where}: unknown generator '{generator}'; available: {', '.join(GENERATORS)}"
        )

    relation = str(item["relation"])
    return Task(
        name=str(item["name"]),
        generator=generator,
        relation=relation,
        backbone_relation=str(item.get("backbone_relation") or relation),
        endpoint_label=str(item["endpoint_label"]),
        directed=bool(item.get("directed", True)),
    )

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
#: ``backbone_edges``  the structured graph already relates two entities, so any
#:                     document mentioning both has a gold edge.
#: ``document_edges``  the document is itself a node, and the backbone states
#:                     what it points at — a decision and the statutes it cites.
#:
#: Read from the implementations rather than listed here. This tuple used to be
#: written by hand and named two generators that were never built: a pack could
#: declare one, pass validation, and fail at run time with a KeyError.
def _known_generators() -> tuple[str, ...]:
    from graphpack.eval.generators import GENERATORS as implemented

    return tuple(sorted(implemented))


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
    #: For ``document_edges``: the label the corpus documents themselves carry.
    #: A decision is both a document and a node, and the gold is the edge
    #: between it and what it cites.
    source_label: str = ""
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
    known = _known_generators()
    if generator not in known:
        raise EvalError(f"{where}: unknown generator '{generator}'; available: {', '.join(known)}")

    relation = str(item["relation"])
    return Task(
        name=str(item["name"]),
        generator=generator,
        relation=relation,
        backbone_relation=str(item.get("backbone_relation") or relation),
        endpoint_label=str(item["endpoint_label"]),
        source_label=str(item.get("source_label") or ""),
        directed=bool(item.get("directed", True)),
    )

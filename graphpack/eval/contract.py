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
    #: For ``document_edges``: require the declared ``relation`` to have been
    #: extracted, rather than counting a mention that resolved.
    #:
    #: The default is False, which is what this generator has always done — and
    #: which was invisible, because the task declares a ``relation`` that was
    #: then never read. tr-law's `statute_citations` reported 97% precision and
    #: 69% recall against 1,242 gold edges while the graph held 170 `CITES`
    #: relations in total: the score was never about relations. It is a fair
    #: measurement of a real thing — did the document name a statute the
    #: reader could identify — but it is not the thing the name suggests, and
    #: both belong in the report.
    require_relation: bool = False

    @property
    def describes(self) -> str:
        kind = self.relation if self.require_relation else f"mentions of {self.endpoint_label}"
        return f"{self.name} ({self.generator}: {kind})"


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

    # An empty list and a missing key are different statements, and collapsing
    # them cost a red CI run. `tasks: []` is bench-wiki saying it has no
    # extraction metrics — it runs no extraction, and its ground truth is the
    # benchmark's own labelling, scored by `graphpack bench`. A missing or
    # null `tasks` is a file somebody truncated. Only the second is an error.
    if "tasks" not in raw:
        raise EvalError(f"{path}: 'tasks' is missing — use 'tasks: []' for a pack with none")
    entries = raw["tasks"]
    if not isinstance(entries, list):
        raise EvalError(f"{path}: 'tasks' must be a list, got {type(entries).__name__}")

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

    # `document_edges` matches documents by label. Without one it used to fall
    # back to "Document", which no pack writes: the gold set came back empty and
    # the run reported "0 gold edges" rather than an error — indistinguishable
    # from a corpus that genuinely has none, after however long extraction took.
    if generator == "document_edges" and not item.get("source_label"):
        raise EvalError(
            f"{where}: document_edges needs 'source_label' — the label its documents "
            "carry in the backbone. Without it there is nothing to match and the gold "
            "set comes back empty rather than wrong."
        )

    relation = str(item["relation"])
    return Task(
        name=str(item["name"]),
        generator=generator,
        relation=relation,
        backbone_relation=str(item.get("backbone_relation") or relation),
        endpoint_label=str(item["endpoint_label"]),
        source_label=str(item.get("source_label") or ""),
        directed=bool(item.get("directed", True)),
        require_relation=bool(item.get("require_relation", False)),
    )

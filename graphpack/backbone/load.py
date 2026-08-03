"""Write a pack's structured records into Neo4j.

Deterministic and idempotent: every write is a MERGE keyed on ``(pack, id)``, so
running the loader twice leaves the same graph. That property is what makes the
backbone usable as evaluation ground truth — a number that changes when you
reload is not ground truth.

Constraints are *derived* from the labels a pack declares rather than written as
a per-pack migration. The plan called for one migration per pack, but that would
put pack-specific labels into shared code and mean every new vertical ships a
Python file. Deriving them keeps the promise that a pack is configuration only.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

from graphpack.backbone.fetch import read_jsonl
from graphpack.backbone.normalize import render, render_parts
from graphpack.backbone.rows import expand
from graphpack.backbone.sources import EdgeSpec, LoadSpec, NodeSpec, Sources

logger = logging.getLogger(__name__)

#: Rows per Cypher statement. Large enough that the round trip is amortised,
#: small enough that a transaction stays well inside the default heap.
BATCH_SIZE = 5_000

#: Neo4j labels and relationship types cannot be parameterised, so they are
#: interpolated into the query text. Anything not matching this is rejected
#: rather than escaped — a label with punctuation in it is a mistake in the
#: pack, not a case to support.
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class LoadError(Exception):
    """Raised when a pack's load block cannot be executed."""


@dataclass
class LoadReport:
    """What one load produced, per step.

    ``created`` is the idempotency signal: a second load of unchanged data must
    create nothing while writing the same number of rows.
    """

    pack: str
    #: Rows the database matched and merged.
    written: Counter = dataclass_field(default_factory=Counter)
    #: Nodes or relationships that did not exist before.
    created: Counter = dataclass_field(default_factory=Counter)
    #: Rows whose id template rendered incomplete — a missing source field.
    skipped: Counter = dataclass_field(default_factory=Counter)
    #: Edges naming an endpoint outside the loaded set. Expected, and worth
    #: reporting: for a top-N slice these are dependencies on the long tail.
    outside: Counter = dataclass_field(default_factory=Counter)

    @property
    def total_written(self) -> int:
        return sum(self.written.values())

    @property
    def total_created(self) -> int:
        return sum(self.created.values())

    def lines(self) -> list[str]:
        out = []
        for step, count in self.written.most_common():
            notes = []
            if self.created.get(step):
                notes.append(f"{self.created[step]:,} new")
            if self.skipped.get(step):
                notes.append(f"{self.skipped[step]:,} incomplete")
            if self.outside.get(step):
                notes.append(f"{self.outside[step]:,} outside the set")
            suffix = f"  ({', '.join(notes)})" if notes else ""
            out.append(f"{count:>8,}  {step}{suffix}")
        return out


# ----------------------------------------------------------------------
# Constraints
# ----------------------------------------------------------------------


def ensure_constraints(session, sources: Sources) -> list[str]:
    """Create the uniqueness constraints and indexes a pack's labels imply.

    ``(pack, id)`` is unique per label: Neo4j Community offers a single
    database, so the ``pack`` property is the only thing keeping two verticals
    from colliding on an identifier.
    """
    statements = []
    for label in sources.node_labels:
        _check_identifier(label, "node label")
        safe = label.lower()
        statements.append(
            f"CREATE CONSTRAINT graphpack_{safe}_identity IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE (n.pack, n.id) IS UNIQUE"
        )
        statements.append(
            f"CREATE INDEX graphpack_{safe}_pack IF NOT EXISTS FOR (n:{label}) ON (n.pack)"
        )

    # The one every label shares, and the reason edge loading is not quadratic.
    # See SHARED_LABEL.
    #
    # An index rather than a constraint, deliberately. Uniqueness is already
    # enforced per label above, where it means something. Imposing it across
    # labels as well would invent a new way for a load to fail — two labels in
    # one pack sharing an identifier is unusual but not incoherent, and it is
    # not this index's business to forbid it.
    statements.append(
        f"CREATE INDEX graphpack_backbone_identity IF NOT EXISTS "
        f"FOR (n:`{SHARED_LABEL}`) ON (n.pack, n.id)"
    )
    for statement in statements:
        session.run(statement)
    logger.info("Constraints ensured for labels: %s", ", ".join(sources.node_labels) or "none")
    return statements


def _check_identifier(value: str, kind: str) -> None:
    if not _IDENTIFIER.match(value):
        raise LoadError(
            f"{kind} '{value}' is not a plain identifier. Labels and relationship types "
            "are interpolated into Cypher and must match [A-Za-z][A-Za-z0-9_]*."
        )


# ----------------------------------------------------------------------
# Rows -> graph
# ----------------------------------------------------------------------


def load_backbone(session, pack_name: str, sources: Sources, data_dir: Path) -> LoadReport:
    """Run every load step against the graph."""
    ensure_constraints(session, sources)
    report = LoadReport(pack=pack_name)

    for spec in sources.load:
        source_path = data_dir / spec.source
        if not source_path.exists():
            raise LoadError(
                f"{spec.describes}: '{spec.source}' is missing — run `graphpack backbone fetch "
                f"{pack_name}` first"
            )
        _run_step(session, pack_name, spec, sources, source_path, report)

    logger.info("Loaded %d rows for pack '%s'", report.total_written, pack_name)
    return report


def _run_step(
    session,
    pack_name: str,
    spec: LoadSpec,
    sources: Sources,
    source_path: Path,
    report: LoadReport,
) -> None:
    label = spec.describes
    batch: list[dict[str, Any]] = []
    written = created = skipped = sent = 0
    ballot: dict[str, list[dict[str, Any]]] = {}

    def _flush(rows: list[dict[str, Any]]) -> tuple[int, int]:
        return _write(session, pack_name, spec, rows)

    for row in _rows(source_path, spec):
        rendered = (
            _render_node(spec.node, row, sources)
            if spec.node
            else _render_edge(spec.edge, row, sources)
        )
        if rendered is None:
            skipped += 1
            continue
        if spec.vote:
            # Held back rather than written: the winner is not known until every
            # mention has been read.
            ballot.setdefault(rendered["id"], []).append(rendered["props"])
            continue
        batch.append(rendered)
        if len(batch) >= BATCH_SIZE:
            sent += len(batch)
            matched, new = _flush(batch)
            written += matched
            created += new
            batch = []

    if spec.vote:
        batch = _count_the_vote(ballot)

    if batch:
        sent += len(batch)
        matched, new = _flush(batch)
        written += matched
        created += new

    report.written[label] += written
    report.created[label] += created
    report.skipped[label] += skipped
    report.outside[label] += sent - written
    logger.info(
        "%s: %d written (%d new), %d incomplete, %d outside the set",
        label,
        written,
        created,
        skipped,
        sent - written,
    )


def _count_the_vote(ballot: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """One row per id, each property set to the value most mentions agreed on.

    Counted per property rather than per row, because two mentions of the same
    statute can each supply a different field. Ties go to the value seen first,
    which is the earliest mention — statutes are named in full the first time a
    decision cites them and by number afterwards, so the earliest reading is the
    likeliest to be the whole name.
    """
    rows = []
    for identity, seen in ballot.items():
        winners: dict[str, Any] = {}
        for key in dict.fromkeys(k for props in seen for k in props):
            counts = Counter(props[key] for props in seen if key in props)
            winners[key] = counts.most_common(1)[0][0]
        rows.append({"id": identity, "props": winners})
    return rows


def _rows(source_path: Path, spec: LoadSpec) -> Iterator[dict[str, Any]]:
    """Yield the rows a step sees, after exploding and filtering.

    Delegates to the shared implementation so a ``where`` clause and an
    ``explode`` behave identically wherever a pack writes them. This had its own
    copy once, which then quietly failed to learn about exploding a text field
    by pattern — the second pack's entire backbone.
    """
    return expand(read_jsonl(source_path), spec)


def _render_node(spec: NodeSpec, row: dict[str, Any], sources: Sources) -> dict[str, Any] | None:
    identity = _identity(spec.id, row, sources)
    if identity is None:
        return None
    return {"id": identity, "props": _properties(spec.properties, row, sources)}


def _render_edge(spec: EdgeSpec, row: dict[str, Any], sources: Sources) -> dict[str, Any] | None:
    start = _identity(spec.start, row, sources)
    end = _identity(spec.end, row, sources)
    if start is None or end is None or start == end:
        # A self-edge here always means a rendering artefact rather than a real
        # relationship — nothing in a dependency graph depends on itself.
        return None
    return {"start": start, "end": end, "props": _properties(spec.properties, row, sources)}


def _properties(templates: dict[str, str], row: dict[str, Any], sources: Sources) -> dict[str, str]:
    """Render a step's properties, dropping the ones that come out empty.

    Unlike identifiers, a missing property is simply absent: writing an empty
    string would claim the record says something it does not.
    """
    return {
        name: value
        for name, template in templates.items()
        if (value := render(template, row, sources.pipelines).strip())
    }


def _identity(template: str, row: dict[str, Any], sources: Sources) -> str | None:
    """Render an identifier, or ``None`` when the row cannot supply one.

    Completeness is judged by whether every placeholder found a value, not by
    inspecting the result. ``"pypi:{name}@{version}"`` on a row with no version
    renders ``"pypi:requests@"`` — a plausible-looking string that no amount of
    trimming distinguishes from a real id, and that every other versionless row
    for that package would share.
    """
    identity, complete = render_parts(template, row, sources.pipelines)
    identity = identity.strip()
    return identity if identity and complete else None


# ----------------------------------------------------------------------
# Cypher
# ----------------------------------------------------------------------

#: Every loaded node carries its own label *and* `Thing`, and the second one is
#: what makes edge loading finish.
#:
#: An edge names its endpoints by id, and a pack's ids span every label it
#: declares — `pypi:requests` is a Package, `gh:psf/requests` a Repository — so
#: the endpoint match cannot name a label. Neo4j's RANGE indexes are all
#: label-scoped, so a label-less match uses none of them and scans every node in
#: the database once per batch. Measured on oss: batches climbing from 15
#: seconds to nearly two minutes, and half an hour to load 25,385 edges.
#:
#: One shared label fixes it. Named with dunders on purpose: `_check_identifier`
#: rejects a pack label that does not match `[A-Za-z][A-Za-z0-9_]*`, so no pack
#: can declare this one and collide with it.
#:
#: It was `Thing` for one commit, which collided immediately — a test pack
#: declares a label by that name, and the per-label uniqueness constraint could
#: not be created because this index already existed on it. That is also where
#: the `Thing` index found in a live database had come from: test residue, not a
#: designed-but-unwired index, which is what an earlier version of this comment
#: claimed. See migration 004.
SHARED_LABEL = "__Backbone__"

_MERGE_NODE = f"""
UNWIND $rows AS row
MERGE (n:{{label}}:`{SHARED_LABEL}` {{{{pack: $pack, id: row.id}}}})
SET n += row.props
RETURN count(n) AS matched
"""

# Endpoints are matched, never created: an edge naming a package outside the
# loaded set is dropped rather than conjured as a bare node. Keeping the graph
# closed over what was actually fetched is what lets a missing edge be read as a
# genuine absence when extraction is evaluated against it.
#
# `:Thing` here is not a filter — every loaded node has it — it is the label
# that lets the index be used at all. See SHARED_LABEL.
_MERGE_EDGE = f"""
UNWIND $rows AS row
MATCH (a:`{SHARED_LABEL}` {{{{pack: $pack, id: row.start}}}})
MATCH (b:`{SHARED_LABEL}` {{{{pack: $pack, id: row.end}}}})
MERGE (a)-[r:{{type}}]->(b)
SET r += row.props
RETURN count(r) AS matched
"""


def _write(session, pack_name: str, spec: LoadSpec, batch: list[dict[str, Any]]) -> tuple[int, int]:
    """Write one batch. Returns ``(matched, created)``.

    ``matched`` is counted by the query rather than assumed from the batch size,
    because an edge whose endpoints are not both loaded matches nothing. Guessing
    it from the update counters would make the numbers plausible and wrong.
    """
    if spec.node:
        _check_identifier(spec.node.label, "node label")
        query = _MERGE_NODE.format(label=spec.node.label)
    else:
        _check_identifier(spec.edge.type, "relationship type")
        query = _MERGE_EDGE.format(type=spec.edge.type)

    result = session.run(query, rows=batch, pack=pack_name)
    record = result.single()
    matched = int(record["matched"]) if record else 0
    counters = result.consume().counters
    created = counters.nodes_created if spec.node else counters.relationships_created
    return matched, created

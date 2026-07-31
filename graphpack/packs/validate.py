"""Static checks over a pack directory — no databases, no LLM, no engine state.

Catches the mistakes that would otherwise surface deep inside an ingest run:
an ontology the extractor cannot read, a relation with no range, a pack whose
name disagrees with its directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from graphpack.packs.contract import (
    IMPLEMENTED_PHASE,
    REQUIRED_BY_PHASE,
    Pack,
    PackError,
    load_pack,
)
from graphpack.packs.ontology import OntologyError, compile_ontology, relation_domain_range

#: A relation whose domain has no range fans out across every entity type when
#: the engine builds its validation schema. We reject that outright rather than
#: police the size of the result.
MAX_TRIPLE_CONSTRAINTS = 2000


@dataclass
class ValidationResult:
    pack_name: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_pack(name: str) -> ValidationResult:
    """Run every static check for one pack."""
    result = ValidationResult(pack_name=name)

    try:
        pack = load_pack(name)
    except PackError as exc:
        result.errors.append(str(exc))
        return result

    _check_required_files(pack, result)
    _check_yaml_files(pack, result)
    _check_ontology(pack, result)
    _check_sources(pack, result)
    return result


def validate_all() -> list[ValidationResult]:
    from graphpack.packs.contract import list_packs

    return [validate_pack(name) for name in list_packs()]


# ----------------------------------------------------------------------
# Individual checks
# ----------------------------------------------------------------------


def _check_required_files(pack: Pack, result: ValidationResult) -> None:
    for filename, phase in sorted(REQUIRED_BY_PHASE.items(), key=lambda kv: kv[1]):
        exists = pack.path(filename).is_file()
        if exists:
            continue
        if phase <= IMPLEMENTED_PHASE:
            result.errors.append(f"{filename} missing (required from phase {phase})")
        else:
            result.warnings.append(f"{filename} not present yet (needed in phase {phase})")


def _check_yaml_files(pack: Pack, result: ValidationResult) -> None:
    for filename in REQUIRED_BY_PHASE:
        if not filename.endswith(".yaml"):
            continue
        path = pack.path(filename)
        if not path.is_file():
            continue
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            result.errors.append(f"{filename}: invalid YAML — {exc}")
            continue
        if loaded is not None and not isinstance(loaded, dict):
            result.errors.append(f"{filename}: top level must be a mapping")


def _check_ontology(pack: Pack, result: ValidationResult) -> None:
    if not pack.ontology_path.is_file():
        return  # already reported by _check_required_files

    try:
        schema = compile_ontology(pack.ontology_path)
    except OntologyError as exc:
        result.errors.append(str(exc))
        return

    result.summary = schema.summary()

    overlap = set(schema.entities) & set(schema.relations)
    if overlap:
        result.errors.append("entity and relation names collide: " + ", ".join(sorted(overlap)))

    # Every object property needs both ends typed. Without a range the engine
    # pairs the relation with every entity type, and our own triple validation
    # has nothing to check against.
    try:
        pairs = relation_domain_range(pack.ontology_path)
    except Exception as exc:  # parse already succeeded above; be defensive anyway
        result.warnings.append(f"could not inspect domain/range — {exc}")
        return

    for relation, (domain, range_) in sorted(pairs.items()):
        missing = [
            label for label, value in (("rdfs:domain", domain), ("rdfs:range", range_)) if not value
        ]
        if missing:
            result.errors.append(f"relation {relation} is missing {' and '.join(missing)}")

    if len(schema.triple_constraints) > MAX_TRIPLE_CONSTRAINTS:
        result.errors.append(
            f"{len(schema.triple_constraints)} triple constraints exceed the "
            f"{MAX_TRIPLE_CONSTRAINTS} limit — usually a relation missing rdfs:range"
        )


def _check_sources(pack: Pack, result: ValidationResult) -> None:
    """Parse sources.yaml so an undefined normaliser or an unreadable input is
    reported here rather than as a load that silently writes nothing."""
    from graphpack.backbone.sources import SourcesError, load_sources

    path = pack.path("sources.yaml")
    if not path.is_file():
        return  # reported by _check_required_files when the phase demands it

    try:
        sources = load_sources(path)
    except SourcesError as exc:
        result.errors.append(str(exc))
        return

    if not sources.load:
        result.warnings.append("sources.yaml declares no load steps")
        return

    labels = sources.node_labels
    result.summary = (
        f"{result.summary}; backbone: {len(sources.fetch)} fetch, "
        f"{len(sources.load)} load, labels {', '.join(labels)}"
    ).lstrip("; ")

    # Every edge endpoint should be an id shape some node step also produces.
    # A mismatched prefix loads zero edges and looks exactly like missing data.
    node_prefixes = {_prefix(spec.node.id) for spec in sources.load if spec.node}
    for spec in sources.load:
        if not spec.edge:
            continue
        for role, template in (("from", spec.edge.start), ("to", spec.edge.end)):
            prefix = _prefix(template)
            if prefix and prefix not in node_prefixes:
                result.warnings.append(
                    f"edge {spec.edge.type}.{role} builds ids as '{prefix}…' but no node step "
                    f"produces that prefix ({', '.join(sorted(p for p in node_prefixes if p))})"
                )


def _prefix(template: str) -> str:
    """Literal text before the first placeholder — the id's namespace."""
    head, _, _ = template.partition("{")
    return head

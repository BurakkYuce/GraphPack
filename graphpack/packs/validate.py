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
    _check_eval_shape(pack, result)
    _check_resolve(pack, result)
    _check_retrieval(pack, result)
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


def _check_eval_shape(pack: Pack, result: ValidationResult) -> None:
    """Warn when an eval task asks for a shape the pack's backbone cannot have.

    `backbone_edges` scores pairs of same-labelled nodes; `document_edges` scores
    an edge out of a document. Choosing the wrong one is not visible until an
    extraction run finishes and the gold set comes back empty — which for tr-law
    would have been fifteen hours.
    """
    from graphpack.backbone.sources import SourcesError, load_sources
    from graphpack.eval.contract import EvalError, load_eval_rules

    eval_path = pack.path("eval.yaml")
    sources_path = pack.path("sources.yaml")
    if not eval_path.is_file() or not sources_path.is_file():
        return
    # These two are caught differently on purpose. `_check_sources` owns
    # sources.yaml and will report a SourcesError itself, so repeating it here
    # would double every message. Nothing owns eval.yaml — an earlier version of
    # this function caught both and returned, with a comment claiming otherwise,
    # so an unknown generator, an empty task list, a duplicate task name and an
    # out-of-range holdout all passed validation in silence.
    try:
        rules = load_eval_rules(eval_path)
    except EvalError as exc:
        result.errors.append(str(exc))
        return
    try:
        sources = load_sources(sources_path)
    except SourcesError:
        return  # reported by _check_sources, which owns that file

    # Which (from-label, type, to-label) triples the backbone can actually build.
    edges = {
        (_label_of(spec.edge.start, sources), spec.edge.type, _label_of(spec.edge.end, sources))
        for spec in sources.load
        if spec.edge
    }
    for task in rules.tasks:
        wanted = task.backbone_relation
        pairs = {(f, t) for f, rel, t in edges if rel == wanted}
        if not pairs:
            result.warnings.append(
                f"eval task '{task.name}' scores {wanted}, which no load step builds"
            )
            continue
        if task.generator == "backbone_edges" and not any(
            f == task.endpoint_label == t for f, t in pairs
        ):
            shapes = ", ".join(sorted(f"{f}->{t}" for f, t in pairs))
            result.errors.append(
                f"eval task '{task.name}' uses backbone_edges with "
                f"endpoint_label {task.endpoint_label}, which needs "
                f"{task.endpoint_label}->{task.endpoint_label} edges; the backbone "
                f"builds {shapes}. Use document_edges with a source_label for that shape."
            )


def _label_of(id_template: str, sources) -> str:
    """The node label an edge endpoint template points at, by id prefix."""
    prefix = _prefix(id_template)
    for spec in sources.load:
        if spec.node and _prefix(spec.node.id) == prefix:
            return spec.node.label
    return "?"


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

    # A template with no placeholder renders itself. `text: body` looks like it
    # names a field and produces the four-character string "body" for every
    # document — an ingest embeds that without complaint, and the corpus is
    # gone. Caught here because the next place it shows up is a benchmark
    # scoring zero.
    #
    # Ahead of the no-load-steps return below, because a corpus is independent of
    # the backbone: a pack can declare documents and no nodes. An earlier version
    # of this function put the loop after that `return` and captured it into the
    # loop body, which silenced every check below for every pack that has a
    # corpus — see test_corpus_templates_do_not_short_circuit_the_backbone_checks.
    for spec in sources.corpus:
        for field_name, template in (("text", spec.text), ("id", spec.id)):
            if template and "{" not in template:
                result.errors.append(
                    f"{spec.describes}: {field_name} is '{template}', which has no "
                    f"placeholder and renders as that literal string. Did you mean "
                    f"'{{{template}}}'?"
                )

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


def _check_resolve(pack: Pack, result: ValidationResult) -> None:
    """Parse resolve.yaml and check it lines up with the ontology and backbone.

    A rule naming an entity type the ontology does not declare, or a backbone
    label no load step writes, resolves nothing and reports no error — the pass
    simply finds no mentions to work on.
    """
    from graphpack.resolve.contract import ResolveError, load_rules

    path = pack.path("resolve.yaml")
    if not path.is_file():
        return  # reported by _check_required_files when the phase demands it

    try:
        rules = load_rules(path, pack.path("aliases.csv"))
    except ResolveError as exc:
        result.errors.append(str(exc))
        return

    try:
        declared = set(compile_ontology(pack.ontology_path).entities)
    except OntologyError:
        return  # already reported

    for rule in rules.rules:
        if rule.entity not in declared:
            result.errors.append(
                f"resolve.yaml: rule for '{rule.entity}', which the ontology does not declare "
                f"(it has {', '.join(sorted(declared))})"
            )

    labels = _backbone_labels(pack)
    if labels:
        for rule in rules.rules:
            if rule.target not in labels:
                result.errors.append(
                    f"resolve.yaml: rule for {rule.entity} resolves against '{rule.target}', "
                    f"which no load step writes (backbone has {', '.join(sorted(labels))})"
                )

    unresolved_entities = sorted(declared - {r.entity for r in rules.rules})
    if unresolved_entities:
        result.warnings.append(
            "no resolve rule for " + ", ".join(unresolved_entities) + " — mentions of those "
            "types stay unlinked"
        )

    result.summary = (
        f"{result.summary}; resolve: {len(rules.rules)} rule(s), {len(rules.aliases)} alias(es)"
    )


def _check_retrieval(pack: Pack, result: ValidationResult) -> None:
    """Parse retrieval.yaml, which until now only had to be valid YAML.

    Every check below already existed in `graphpack/agent/contract.py`; nothing
    called them until a question was asked. So a pack could pass validation with
    an intent missing its Cypher, a duplicate intent name, or — worst, because it
    is silent rather than loud — a traversal that does not filter on `$pack` and
    answers out of a different pack's graph. `graphpack packs validate` is where
    a pack author looks; this puts the answers there.
    """
    from graphpack.agent.contract import RetrievalError, load_retrieval_rules

    path = pack.path("retrieval.yaml")
    if not path.is_file():
        return  # reported by _check_required_files when the phase demands it

    try:
        rules = load_retrieval_rules(path)
    except RetrievalError as exc:
        result.errors.append(str(exc))
        return

    if not rules.lookup:
        result.warnings.append(
            "retrieval.yaml declares no lookup — the agent can only reach entities "
            "resolution already links, so a question naming an id outright finds nothing"
        )

    # An intent's `entity` is the extraction label its questions are about. The
    # agent does not read it today (lookup goes through the pack's Cypher and
    # resolve rules), but the parser requires it of every pack, so it is at least
    # held to naming something real rather than being required and unchecked.
    try:
        declared = set(compile_ontology(pack.ontology_path).entities)
    except OntologyError:
        declared = set()  # already reported
    for intent in rules.intents:
        if declared and intent.entity not in declared:
            result.errors.append(
                f"retrieval.yaml: intent '{intent.name}' is about '{intent.entity}', which the "
                f"ontology does not declare (it has {', '.join(sorted(declared))})"
            )

    result.summary = f"{result.summary}; retrieval: {len(rules.intents)} intent(s)"


def _backbone_labels(pack: Pack) -> set[str]:
    from graphpack.backbone.sources import SourcesError, load_sources

    path = pack.path("sources.yaml")
    if not path.is_file():
        return set()
    try:
        return set(load_sources(path).node_labels)
    except SourcesError:
        return set()  # already reported

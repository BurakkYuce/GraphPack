"""Compile a pack's ``ontology.ttl`` into the engine's extraction schema.

The engine already parses OWL into entity/relation/property structures
(``rdf.ontology_manager.OntologyManager``), so we reuse it as a library rather
than writing a second TTL compiler.  Two deliberate departures from the engine's
own ontology path:

1. We instantiate our **own** ``OntologyManager``.  The engine's
   ``USE_ONTOLOGY=true`` route reads a process-global singleton
   (``rdf.api_rdf_enhancements.ontology_manager``), which cannot represent more
   than one pack at a time.  Compiling to a ``schemas`` entry keeps everything
   on the per-``Settings`` path instead.

2. We keep ``validation_schema`` out of the schema handed to the engine.
   ``SchemaManager`` never forwards triple constraints to
   ``SchemaLLMPathExtractor`` — only entity/relation/property lists reach the
   extractor — so shipping it there would imply an enforcement that does not
   happen.  The constraints are returned separately and enforced by GraphPack's
   own validation pass.

Note that ``OntologyManager._uri_to_name`` upper-cases local names, so the
compiled entity and relation names are UPPERCASE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class OntologyError(Exception):
    """Raised when an ontology cannot be parsed or is unusable as a schema."""


@dataclass(frozen=True)
class CompiledSchema:
    """Result of compiling one ``ontology.ttl``."""

    #: Entity type names, UPPERCASE, in ontology order.
    entities: list[str]
    #: Relation type names, UPPERCASE.
    relations: list[str]
    #: ``{ENTITY: {prop: type}}`` from ``owl:DatatypeProperty`` with a class domain.
    properties: dict[str, dict[str, str]]
    #: ``{RELATION: {prop: type}}`` from ``owl:DatatypeProperty`` with a relation domain.
    relation_properties: dict[str, dict[str, str]]
    #: ``(subject, predicate, object)`` triples from ``rdfs:domain`` / ``rdfs:range``.
    #: Not enforced by the engine — GraphPack applies these itself.
    triple_constraints: list[tuple[str, str, str]] = field(default_factory=list)

    def as_engine_schema(self) -> dict[str, Any]:
        """The dict the engine expects as ``schemas[i]["schema"]``.

        Consumed by ``config.Settings.get_active_schema`` and from there by
        ``SchemaManager.create_extractor``.
        """
        return {
            "entities": list(self.entities),
            "relations": list(self.relations),
            "properties": {k: dict(v) for k, v in self.properties.items()},
            "relation_properties": {k: dict(v) for k, v in self.relation_properties.items()},
        }

    def summary(self) -> str:
        return (
            f"{len(self.entities)} entity types, {len(self.relations)} relation types, "
            f"{sum(len(v) for v in self.properties.values())} entity props, "
            f"{sum(len(v) for v in self.relation_properties.values())} relation props, "
            f"{len(self.triple_constraints)} triple constraints"
        )


def compile_ontology(ttl_path: Path) -> CompiledSchema:
    """Parse *ttl_path* and return the compiled extraction schema.

    ``ttl_path`` must be absolute: the engine resolves relative ontology paths
    against ``os.getcwd()`` (``rdf.ontology_manager.ontology_path_anchor``).
    """
    ttl_path = Path(ttl_path)
    if not ttl_path.is_absolute():
        raise OntologyError(
            f"{ttl_path}: ontology path must be absolute — the engine resolves "
            "relative paths against the process working directory"
        )
    if not ttl_path.is_file():
        raise OntologyError(f"{ttl_path}: ontology file not found")

    manager = _new_manager()
    try:
        manager.load_ontology(str(ttl_path))
    except Exception as exc:  # rdflib raises a wide variety of parse errors
        raise OntologyError(f"{ttl_path}: could not parse ontology — {exc}") from exc

    entities = list(manager.entities.keys())
    relations = list(manager.relations.keys())
    if not entities:
        raise OntologyError(
            f"{ttl_path}: no entity types found. The engine's extractor looks for "
            "explicit 'a owl:Class' declarations — plain rdfs:Class is not matched."
        )
    if not relations:
        raise OntologyError(
            f"{ttl_path}: no relation types found. Declare each relation with "
            "'a owl:ObjectProperty' plus rdfs:domain and rdfs:range."
        )

    return CompiledSchema(
        entities=entities,
        relations=relations,
        properties=manager.get_entity_properties(),
        relation_properties=manager.get_relation_properties(),
        triple_constraints=[tuple(t) for t in manager.get_validation_schema()],
    )


def relation_domain_range(ttl_path: Path) -> dict[str, tuple[str | None, str | None]]:
    """``{RELATION: (domain, range)}`` — used by the validator to insist that
    every object property is fully typed."""
    manager = _new_manager()
    manager.load_ontology(str(Path(ttl_path).resolve()))
    return {
        name: (
            rel.domain.name if rel.domain else None,
            rel.range.name if rel.range else None,
        )
        for name, rel in manager.relations.items()
    }


def _new_manager():
    """Fresh ``OntologyManager``, never the engine's global singleton."""
    try:
        from rdf.ontology_manager import OntologyManager
    except ImportError as exc:  # pragma: no cover - environment problem, not logic
        raise OntologyError(
            "flexible-graphrag engine is not importable. Install it with "
            "`uv pip install -e ../flexible-graphrag/flexible-graphrag`."
        ) from exc
    return OntologyManager()

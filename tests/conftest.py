"""Shared fixtures.

Unit tests build throwaway packs on disk rather than leaning on ``domains/oss``,
so a change to the real pack cannot quietly turn a contract test green or red.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

MINIMAL_ONTOLOGY = textwrap.dedent(
    """\
    @prefix ex:   <http://example.org/test/> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix owl:  <http://www.w3.org/2002/07/owl#> .
    @prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

    ex:Widget a owl:Class ;
        rdfs:label "Widget" .

    ex:Factory a owl:Class ;
        rdfs:label "Factory" .

    ex:built_in a owl:ObjectProperty ;
        rdfs:domain ex:Widget ;
        rdfs:range  ex:Factory .

    ex:serial a owl:DatatypeProperty ;
        rdfs:domain ex:Widget ;
        rdfs:range  xsd:string .
    """
)

MINIMAL_PACK_YAML = textwrap.dedent(
    """\
    name: {name}
    version: 1.0.0
    lang: en
    id_prefix: ex
    extraction:
      strict_schema: true
      max_triplets_per_chunk: 5
      chunk_size: 256
      chunk_overlap: 32
    stores:
      qdrant_collection: {name}_chunks
    """
)

MINIMAL_SOURCES = textwrap.dedent(
    """\
    normalize:
      slug:
        - lower
        - {regex_replace: {pattern: "[-_.]+", replace: "-"}}

    fetch:
      - id: widgets
        url: https://example.invalid/widgets.json
        out: widgets.jsonl

    load:
      - source: widgets.jsonl
        node:
          label: Widget
          id: "w:{name|slug}"
          properties:
            name: "{name}"
    """
)

MINIMAL_RESOLVE = textwrap.dedent(
    """\
    normalize:
      slug:
        - lower
        - {regex_replace: {pattern: "[-_.]+", replace: "-"}}

    resolve:
      - entity: WIDGET
        target: Widget
        id: "w:{name|slug}"
        methods: [exact]
        on_unresolved: drop
    """
)

MINIMAL_EVAL = textwrap.dedent(
    """\
    tasks:
      - name: built_in
        generator: backbone_edges
        relation: BUILT_IN
        endpoint_label: Widget
    holdout: 0.0
    """
)


@pytest.fixture
def pack_dir(tmp_path: Path):
    """Factory building a valid pack directory under a temporary domains root."""

    domains = tmp_path / "domains"
    domains.mkdir()

    def _make(
        name: str = "widgets",
        *,
        pack_yaml: str | None = None,
        ontology: str | None = None,
        sources: str | None = None,
        resolve: str | None = None,
        evaluation: str | None = None,
    ):
        root = domains / name
        root.mkdir(parents=True, exist_ok=True)
        (root / "pack.yaml").write_text(
            pack_yaml if pack_yaml is not None else MINIMAL_PACK_YAML.format(name=name),
            encoding="utf-8",
        )
        (root / "ontology.ttl").write_text(
            MINIMAL_ONTOLOGY if ontology is None else ontology, encoding="utf-8"
        )
        (root / "sources.yaml").write_text(
            MINIMAL_SOURCES if sources is None else sources, encoding="utf-8"
        )
        (root / "resolve.yaml").write_text(
            MINIMAL_RESOLVE if resolve is None else resolve, encoding="utf-8"
        )
        (root / "eval.yaml").write_text(
            MINIMAL_EVAL if evaluation is None else evaluation, encoding="utf-8"
        )
        return root

    _make.domains = domains  # type: ignore[attr-defined]
    return _make


@pytest.fixture
def neo4j_session():
    """Live Neo4j session, skipping the test when no server is reachable."""
    from graphpack.backbone import driver_from_env
    from graphpack.backbone.neo4j_client import DATABASE

    try:
        driver = driver_from_env()
    except ConnectionError as exc:
        pytest.skip(f"Neo4j unavailable: {exc}")

    try:
        with driver.session(database=DATABASE) as session:
            yield session
    finally:
        driver.close()

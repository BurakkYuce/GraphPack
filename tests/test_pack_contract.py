"""Pack contract loading and static validation."""

from __future__ import annotations

import textwrap

import pytest

from graphpack.packs.contract import Pack, PackError, list_packs, load_pack

pytestmark = pytest.mark.unit


def test_loads_declared_fields(pack_dir):
    root = pack_dir("widgets")

    pack = Pack.from_dir(root)

    assert (pack.name, pack.version, pack.lang, pack.id_prefix) == ("widgets", "1.0.0", "en", "ex")
    assert pack.strict_schema is True
    assert (pack.chunk_size, pack.chunk_overlap) == (256, 32)
    assert pack.qdrant_collection == "widgets_chunks"


def test_qdrant_collection_defaults_from_the_pack_name(pack_dir):
    yaml_without_stores = textwrap.dedent(
        """\
        name: tr-law
        version: 0.1.0
        """
    )
    root = pack_dir("tr-law", pack_yaml=yaml_without_stores)

    # A hyphen is legal in a pack name but not in a store identifier.
    assert Pack.from_dir(root).qdrant_collection == "tr_law_chunks"


def test_name_must_match_the_directory(pack_dir):
    """Otherwise `load_pack(x)` and the graph's `pack` property drift apart."""
    root = pack_dir("widgets", pack_yaml="name: other\nversion: 1.0.0\n")

    with pytest.raises(PackError, match="does not match directory"):
        Pack.from_dir(root)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("version: 1.0.0\n", "'name' is required"),
        ("name: widgets\n", "'version' is required"),
        ("name: widgets\nversion: 1.0.0\nllm: not-a-mapping\n", "'llm' must be a mapping"),
    ],
)
def test_malformed_manifest_is_rejected(pack_dir, body, message):
    root = pack_dir("widgets", pack_yaml=body)

    with pytest.raises(PackError, match=message):
        Pack.from_dir(root)


@pytest.mark.parametrize("reserved", ["default", "none", "sample", "Sample"])
def test_engine_reserved_names_are_rejected(pack_dir, reserved):
    """`Settings.get_active_schema` intercepts these before consulting our schema
    list — "sample" hands back the engine's built-in SAMPLE_SCHEMA and the others
    fall through to LlamaIndex's internal one. The pack would be ingested under a
    schema unrelated to its ontology, and nothing would report an error."""
    root = pack_dir(reserved)

    with pytest.raises(PackError, match="reserved by the engine"):
        Pack.from_dir(root)


def test_ontology_checksum_tracks_file_contents(pack_dir):
    root = pack_dir("widgets")
    pack = Pack.from_dir(root)
    before = pack.ontology_checksum

    (root / "ontology.ttl").write_text(
        (root / "ontology.ttl").read_text(encoding="utf-8") + "\n# edited\n",
        encoding="utf-8",
    )

    assert Pack.from_dir(root).ontology_checksum != before


def test_discovery_finds_packs_and_reports_unknown_names(pack_dir):
    pack_dir("alpha")
    pack_dir("beta")
    domains = pack_dir.domains

    assert list_packs(domains) == ["alpha", "beta"]
    with pytest.raises(PackError, match="known packs: alpha, beta"):
        load_pack("gamma", domains)

"""Records becoming documents.

The pack tag is the load-bearing part: Neo4j Community has one database, so a
document that reaches the engine untagged produces entities nobody can attribute
to a pack.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from graphpack.backbone.sources import load_sources
from graphpack.corpus import PACK_KEY, CorpusError, build_documents

pytestmark = pytest.mark.unit

SOURCES = textwrap.dedent(
    """\
    normalize:
      slug:
        - lower
        - {regex_replace: {pattern: "[-_.]+", replace: "-"}}

    fetch:
      - {id: threads, url: https://example.invalid/threads, out: threads.jsonl}

    corpus:
      - source: threads.jsonl
        id: "th:{repo|slug}#{number}"
        text: "{title}\\n\\n{body}"
        metadata:
          repo: "{repo}"
          title: "{title}"
    """
)

ROWS = [
    {"repo": "Psf/Requests", "number": 1, "title": "Timeout", "body": "urllib3 raises here."},
    {"repo": "psf/requests", "number": 2, "title": "Empty body", "body": "   "},
    {"repo": "psf/requests", "number": 3, "title": "No number", "body": "text"},
]


@pytest.fixture
def corpus(tmp_path):
    def _make(sources_yaml: str = SOURCES, rows: list[dict] = None):
        (tmp_path / "sources.yaml").write_text(sources_yaml, encoding="utf-8")
        data = tmp_path / "data"
        data.mkdir(exist_ok=True)
        (data / "threads.jsonl").write_text(
            "\n".join(json.dumps(r) for r in (ROWS if rows is None else rows)) + "\n",
            encoding="utf-8",
        )
        return load_sources(tmp_path / "sources.yaml"), data

    return _make


def test_every_document_carries_its_pack(corpus):
    """Extraction copies source metadata onto the entities it produces, so this
    tag is what later makes an extracted entity attributable to a pack."""
    sources, data = corpus()

    documents = build_documents("oss", sources, data)

    assert documents
    assert all(d.metadata[PACK_KEY] == "oss" for d in documents)


def test_ids_and_text_come_from_templates(corpus):
    sources, data = corpus()

    first = build_documents("oss", sources, data)[0]

    assert first.doc_id == "th:psf/requests#1"
    assert first.text == "Timeout\n\nurllib3 raises here."
    assert first.metadata["repo"] == "Psf/Requests"


def test_text_is_judged_after_rendering_the_whole_template(corpus):
    """The template joins title and body, so an issue with an empty body still
    has text. Dropping it is the pack's decision, expressed as a `where` clause,
    not something the builder should decide on its behalf."""
    sources, data = corpus()

    titles = [d.metadata["title"] for d in build_documents("oss", sources, data)]

    assert "Empty body" in titles


def test_rows_whose_template_renders_nothing_are_dropped(corpus):
    """An embedding of whitespace costs the same as one of content."""
    sources, data = corpus(rows=[{"repo": "psf/requests", "number": 9, "title": "", "body": ""}])

    assert build_documents("oss", sources, data) == []


def test_an_id_missing_one_of_its_fields_is_dropped(corpus):
    """A row with no number renders "th:psf/requests#" — non-empty, and shared
    with every other numberless row from that repository. Documents would
    overwrite each other under one id."""
    sources, data = corpus(rows=[{"repo": "psf/requests", "title": "t", "body": "b"}])

    assert build_documents("oss", sources, data) == []


def test_where_filters_before_rendering(corpus):
    sources_yaml = textwrap.dedent(
        """\
        fetch:
          - {id: threads, url: https://example.invalid/threads, out: threads.jsonl}

        corpus:
          - source: threads.jsonl
            id: "th:{number}"
            text: "{title}"
            metadata:
              title: "{title}"
            where:
              body: {matches: "urllib3"}
        """
    )
    sources, data = corpus(sources_yaml)

    documents = build_documents("oss", sources, data)

    assert [d.metadata["title"] for d in documents] == ["Timeout"]


def test_limit_stops_early(corpus):
    sources, data = corpus()

    assert len(build_documents("oss", sources, data, limit=1)) == 1


def test_limit_takes_the_first_documents_not_a_sample(corpus):
    """Recorded because it is the trap: file order groups documents by whatever
    the fetch iterated over, so a --limit slice of the oss corpus came from 8
    repositories out of 114."""
    rows = [{"repo": f"org/repo{i}", "number": 1, "title": f"t{i}", "body": "b"} for i in range(10)]
    sources, data = corpus(rows=rows)

    taken = build_documents("oss", sources, data, limit=3)

    assert [d.metadata["repo"] for d in taken] == ["org/repo0", "org/repo1", "org/repo2"]


def test_sample_spreads_across_the_corpus(corpus):
    rows = [{"repo": f"org/repo{i}", "number": 1, "title": f"t{i}", "body": "b"} for i in range(20)]
    sources, data = corpus(rows=rows)

    sampled = build_documents("oss", sources, data, sample=5, seed=0)

    assert len(sampled) == 5
    # A sample of 5 from 20 that happened to be the first 5 would be a
    # one-in-fifteen-thousand coincidence, not a sample.
    assert [d.metadata["repo"] for d in sampled] != [f"org/repo{i}" for i in range(5)]


def test_the_same_seed_selects_the_same_documents(corpus):
    """Extraction is expensive enough that runs are rarely repeated. What was
    measured on one subset has to be reproducible on that subset."""
    rows = [{"repo": f"org/repo{i}", "number": 1, "title": f"t{i}", "body": "b"} for i in range(20)]
    sources, data = corpus(rows=rows)

    first = [d.doc_id for d in build_documents("oss", sources, data, sample=5, seed=7)]
    again = [d.doc_id for d in build_documents("oss", sources, data, sample=5, seed=7)]
    other = [d.doc_id for d in build_documents("oss", sources, data, sample=5, seed=8)]

    assert first == again
    assert first != other


def test_sampling_more_than_the_corpus_holds_returns_everything(corpus):
    sources, data = corpus()

    assert len(build_documents("oss", sources, data, sample=1000)) == len(ROWS)


def test_a_missing_data_file_says_what_to_run(corpus, tmp_path):
    sources, _ = corpus()

    with pytest.raises(CorpusError, match="backbone fetch"):
        build_documents("oss", sources, tmp_path / "absent")


def test_the_real_pack_declares_a_corpus():
    from graphpack.packs import load_pack

    sources = load_sources(load_pack("oss").path("sources.yaml"))

    assert sources.corpus, "the oss pack should declare corpus steps"


def test_the_pack_tag_is_hidden_from_the_model(tmp_path):
    """LlamaIndex prepends metadata to the text it sends. Left visible, the pack
    tag becomes document content — extraction produced an entity named
    "pack: oss", typed PACKAGE, from a thread about botocore."""
    from llama_index.core.schema import MetadataMode

    documents = _documents(tmp_path)

    seen = documents[0].get_content(metadata_mode=MetadataMode.LLM)
    assert "pack:" not in seen
    assert documents[0].metadata["pack"] == "testpack"


def test_the_pack_tag_is_hidden_from_the_embedding_too(tmp_path):
    """It is not text about the document any more than it is text for the model,
    and an embedding that encodes it moves every document in a pack together."""
    from llama_index.core.schema import MetadataMode

    seen = _documents(tmp_path)[0].get_content(metadata_mode=MetadataMode.EMBED)

    assert "pack:" not in seen


def test_a_pack_can_hide_more_of_its_own_metadata(tmp_path):
    """A URL and a state flag are kept for the graph and are noise in a prompt —
    much of the URL and DATE entity noise in the oss run came from exactly
    these lines."""
    from llama_index.core.schema import MetadataMode

    documents = _documents(tmp_path, hide=["url"])

    seen = documents[0].get_content(metadata_mode=MetadataMode.LLM)
    assert "url:" not in seen
    assert "title:" in seen, "hiding one field must not hide the rest"
    assert documents[0].metadata["url"] == "https://example.invalid/1"


def test_hiding_a_field_the_row_never_had_is_not_carried(tmp_path):
    """A phantom exclusion is harmless but misleading when read back."""
    documents = _documents(tmp_path, hide=["nosuchfield"])

    assert documents[0].excluded_llm_metadata_keys == ["pack"]


def _documents(tmp_path, hide=None):
    """One document from a minimal corpus block."""
    import json
    import textwrap

    from graphpack.backbone.sources import load_sources
    from graphpack.corpus import build_documents

    hide_line = f"    hide_from_model: {json.dumps(hide)}\n" if hide else ""
    (tmp_path / "sources.yaml").write_text(
        textwrap.dedent(
            """\
            fetch:
              - id: threads
                url: https://example.invalid/t.json
                out: threads.jsonl

            corpus:
              - source: threads.jsonl
                id: "t:{n}"
                text: body
                metadata:
                  title: "{title}"
                  url: "{url}"
            """
        )
        + hide_line,
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "threads.jsonl").write_text(
        json.dumps(
            {
                "n": "1",
                "title": "A thread",
                "url": "https://example.invalid/1",
                "body": "some prose about botocore",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return build_documents("testpack", load_sources(tmp_path / "sources.yaml"), data)


# ----------------------------------------------------------------------
# Selecting documents by identifier
# ----------------------------------------------------------------------


def test_only_selects_documents_by_id(corpus):
    """What a re-ingest of one changed document needs. The identifier is the
    corpus `id` template's output, which is also what a chunk carries as
    `ref_doc_id` — the same string the graph is joined on."""
    sources, data = corpus()

    documents = build_documents("p", sources, data, only=["th:psf/requests#1"])

    assert [d.doc_id for d in documents] == ["th:psf/requests#1"]


def test_only_ignores_ids_the_corpus_does_not_have(corpus):
    """Reported by the caller rather than raised here: `ingest_pack` turns an
    empty selection into an error naming where identifiers come from, which is
    more use than a KeyError on a typo."""
    sources, data = corpus()

    assert build_documents("p", sources, data, only=["th:nope#9"]) == []


def test_only_is_applied_before_sampling(corpus):
    """Naming a document and then sampling away from it would be nonsense."""
    sources, data = corpus()

    documents = build_documents("p", sources, data, only=["th:psf/requests#1"], sample=1, seed=0)

    assert [d.doc_id for d in documents] == ["th:psf/requests#1"]

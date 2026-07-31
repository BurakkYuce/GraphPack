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


def test_a_missing_data_file_says_what_to_run(corpus, tmp_path):
    sources, _ = corpus()

    with pytest.raises(CorpusError, match="backbone fetch"):
        build_documents("oss", sources, tmp_path / "absent")


def test_the_real_pack_declares_a_corpus():
    from graphpack.packs import load_pack

    sources = load_sources(load_pack("oss").path("sources.yaml"))

    assert sources.corpus, "the oss pack should declare corpus steps"

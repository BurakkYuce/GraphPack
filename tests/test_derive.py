"""Deriving one record set from another.

The repository list is implied by the package records already fetched. Asking
the index for it again would cost requests and let the two drift apart, so a
derive step builds it locally.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from graphpack.backbone.fetch import FetchError, read_jsonl, run_derive
from graphpack.backbone.sources import SourcesError, load_sources

pytestmark = pytest.mark.unit

SOURCES = textwrap.dedent(
    """\
    normalize:
      slug:
        - lower
        - {regex_extract: {pattern: "github\\\\.com[:/]+([^/]+/[^/#?]+)"}}

    fetch:
      - {id: packages, url: https://example.invalid/p, out: packages.jsonl}

    derive:
      - id: repos
        source: packages.jsonl
        out: repos.jsonl
        explode: urls
        fields:
          slug: "{value|slug}"
          package: "{name}"
        require: [slug]
        unique: slug
    """
)

ROWS = [
    {
        "name": "requests",
        "urls": {
            "Source": "https://github.com/psf/requests",
            "Issues": "https://github.com/psf/requests/issues",
            "Docs": "https://requests.readthedocs.io",
        },
    },
    {"name": "urllib3", "urls": {"Homepage": "https://github.com/urllib3/urllib3"}},
    {"name": "pyyaml", "urls": {"Homepage": "https://pyyaml.org"}},
]


@pytest.fixture
def derived(tmp_path):
    def _run(sources_yaml: str = SOURCES, rows: list[dict] = None):
        (tmp_path / "sources.yaml").write_text(sources_yaml, encoding="utf-8")
        data = tmp_path / "data"
        data.mkdir(exist_ok=True)
        (data / "packages.jsonl").write_text(
            "\n".join(json.dumps(r) for r in (ROWS if rows is None else rows)) + "\n",
            encoding="utf-8",
        )
        sources = load_sources(tmp_path / "sources.yaml")
        result = run_derive(sources.derive[0], data, sources)
        return result, list(read_jsonl(data / "repos.jsonl"))

    return _run


def test_several_urls_for_one_repository_collapse_to_one_row(derived):
    """A package lists its source, its issues and its changelog; all three
    normalise to the same owner/repo and only one request should be made for it."""
    _, rows = derived()

    assert [r["slug"] for r in rows] == ["psf/requests", "urllib3/urllib3"]


def test_rows_missing_a_required_field_are_dropped(derived):
    """pyyaml's only URL is not a repository, so it yields no slug and no row."""
    _, rows = derived()

    assert "pyyaml" not in {r["package"] for r in rows}


def test_the_result_is_reported(derived):
    result, rows = derived()

    assert result.rows == len(rows) == 2


def test_limit_caps_the_output(derived):
    sources_yaml = SOURCES + "    limit: 1\n"
    _, rows = derived(sources_yaml)

    assert len(rows) == 1


def test_require_naming_an_unproduced_field_is_rejected(tmp_path):
    """Otherwise the requirement silently never holds and every row is dropped."""
    (tmp_path / "sources.yaml").write_text(
        textwrap.dedent(
            """\
            derive:
              - id: d
                source: a.jsonl
                out: b.jsonl
                fields: {x: "{x}"}
                require: [typo]
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(SourcesError, match="'require' names field"):
        load_sources(tmp_path / "sources.yaml")


def test_unique_naming_an_unproduced_field_is_rejected(tmp_path):
    (tmp_path / "sources.yaml").write_text(
        textwrap.dedent(
            """\
            derive:
              - id: d
                source: a.jsonl
                out: b.jsonl
                fields: {x: "{x}"}
                unique: typo
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(SourcesError, match="'unique' names field"):
        load_sources(tmp_path / "sources.yaml")


def test_a_fetch_step_reading_a_file_derived_later_is_rejected(tmp_path):
    """Execution order is fetch, then the derives that read it, then the next
    fetch. A step declared before its input exists would fail mid-download."""
    (tmp_path / "sources.yaml").write_text(
        textwrap.dedent(
            """\
            fetch:
              - {id: uses, url: "https://example.invalid/{slug}", for_each: repos.jsonl, out: u.jsonl}
              - {id: packages, url: https://example.invalid/p, out: packages.jsonl}
            derive:
              - id: repos
                source: packages.jsonl
                out: repos.jsonl
                fields: {slug: "{slug}"}
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(SourcesError, match="nothing before it produces"):
        load_sources(tmp_path / "sources.yaml")


def test_deriving_from_a_missing_file_says_so(tmp_path):
    (tmp_path / "sources.yaml").write_text(SOURCES, encoding="utf-8")
    sources = load_sources(tmp_path / "sources.yaml")

    with pytest.raises(FetchError, match="nothing to derive from"):
        run_derive(sources.derive[0], tmp_path / "data", sources)
